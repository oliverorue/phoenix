import inspect
import time
from typing import Any
from loguru import logger
from bot.core.data_feed import DataFeed
from bot.core.strategy_engine import ScalpingStrategy
from bot.core.risk_manager import RiskManager
from bot.core.execution import OrderExecutor
from bot.core.position_manager import PositionManager
from bot.core.indicators import Indicators
from bot.core.exceptions import RiskException
from config.loader import FUNDING_RATE_ENABLED
from bot.core.tracing import trace, use_span


class SymbolRunner:
    def __init__(self, symbol: str, allocation_pct: float, leverage: int,
                 risk_per_trade: float, journal: Any, notifier: Any, total_balance: float,
                 data_feed: Any = None, strategy: Any = None, risk_mgr: Any = None,
                 executor: Any = None, position_mgr: Any = None, market_data: Any = None,
                 spread_max: float = 0.15,
                 correlation_guard: Any = None,
                 portfolio_risk: Any = None) -> None:
        self.symbol = symbol
        self.allocation_pct = allocation_pct
        self.leverage = leverage
        self.risk_per_trade = risk_per_trade
        self.spread_max = spread_max  # spread máximo configurable por símbolo
        # ── Dependency Injection: permite inyectar dependencias para testing ──
        self.data_feed = data_feed or DataFeed(symbol=symbol)
        self.strategy = strategy or ScalpingStrategy()
        self.risk_mgr = risk_mgr or RiskManager(self.data_feed)
        self.executor = executor or OrderExecutor(self.data_feed, journal)
        self.position_mgr = position_mgr or PositionManager(
            self.executor, self.data_feed,
            balance_getter=lambda: self.balance,
        )

        # Market Data Provider (on-chain / funding rate) — inyectable
        if market_data is not None:
            self.market_data = market_data
        else:
            from bot.core.market_data import MarketDataProvider
            self.market_data = MarketDataProvider()

        self.correlation_guard = correlation_guard
        self.portfolio_risk = portfolio_risk
        self.notifier = notifier
        self.journal = journal
        self.balance = total_balance * allocation_pct
        self.risk_mgr.set_notifier(self.notifier)
        if hasattr(self.risk_mgr, "set_symbol_overrides"):
            self.risk_mgr.set_symbol_overrides(
                risk_per_trade=self.risk_per_trade,
                leverage=self.leverage,
            )
        if hasattr(self.risk_mgr, "set_initial_balance"):
            self.risk_mgr.set_initial_balance(self.balance)
        else:
            # Compatibilidad con implementaciones legacy.
            self.risk_mgr.set_balance(self.balance)
        self.cooldown = 0  # velas de bloqueo propias del runner
        self._last_candle_ts: Any = None
        self.fase_num = 0
        self.fase_cfg: dict[str, Any] = {}
        self.database = journal.database if journal else None

    async def _current_balance(self) -> float:
        """Balance live/paper del executor, ajustado por allocation."""
        if hasattr(self.executor, "get_balance"):
            raw = await self.executor.get_balance()
        else:
            raw = await self.data_feed.get_balance()
        scope = getattr(self.executor, "balance_scope", "allocated")
        if scope == "account":
            return float(raw) * self.allocation_pct
        return float(raw)

    async def _report_close_result(self) -> None:
        """Contabiliza el resultado de un cierre pendiente."""
        consumer = getattr(self.executor, "consume_close_result", None)
        if not callable(consumer):
            return
        result = consumer()
        if not result:
            return

        pnl = result.get("pnl")
        reason = str(result.get("reason") or "")
        direction = result.get("direction")
        if pnl is None:
            # Sin PnL no se puede clasificar el trade: no tocar rachas.
            self.cooldown = max(self.cooldown, 1)
            return
        was_loss = float(pnl) < 0
        is_stop = reason in (
            "stop_loss", "sl", "trailing_stop", "breakeven_stop",
        )

        try:
            await self.risk_mgr.register_trade_result(
                was_loss=was_loss, is_stop=is_stop,
            )
        except RiskException as rfe:
            logger.warning(f"{self.symbol}: {rfe}")
            self.cooldown = max(self.cooldown, 1)
            if self.notifier:
                self.notifier.send(
                    "⚠️ Límite de riesgo",
                    f"{self.symbol}: {rfe}",
                    severity="WARNING",
                )
        finally:
            if direction:
                self.strategy.register_trade_outcome(direction, was_loss)
            self.cooldown = max(self.cooldown, 1)

    @trace(attributes={"component": "SymbolRunner"})
    async def run_cycle(self) -> None:
        """Un ciclo completo para este símbolo."""
        try:
            df_1h = await self.data_feed.current_1h()
            df_15m = await self.data_feed.current_15m()
            if df_1h.empty or df_15m.empty:
                return

            # Cooldown y estrategia avanzan por vela cerrada, no por tick.
            candle_ts = df_15m["timestamp"].iloc[-1]
            if candle_ts != self._last_candle_ts:
                self._last_candle_ts = candle_ts
                self.strategy.tick_cooldown()
                if self.cooldown > 0:
                    self.cooldown -= 1

            # Contabilizar cierres pendientes antes de decidir el próximo trade.
            await self._report_close_result()

            # Balance real (live) o virtual (paper) del ciclo actual.
            self.balance = await self._current_balance()
            self.risk_mgr.update_live_balance(self.balance)

            day_changed = await self.risk_mgr.reset_daily_if_needed()
            if day_changed:
                self.strategy.reset_cooldown()
                self.cooldown = 0

            # La fase se refresca cada ciclo según el balance actual.
            fase_num, fase_cfg = await self.risk_mgr.get_phase_config()
            if fase_num != self.fase_num:
                self.strategy.reset_cooldown()
                self.cooldown = 0
            self.fase_num = fase_num
            self.fase_cfg = fase_cfg

            ind_15m = Indicators.calc_15m_indicators(df_15m, self.fase_cfg)
            ind_1h = Indicators.calc_1h_indicators(df_1h, self.fase_cfg)

            current_price = df_15m["close"].iloc[-1]
            # Cachear último precio en executor para close_position() (sync safe)
            self.executor.last_price = current_price
            # Paper: registrar equity mark-to-market.
            equity_update = getattr(self.executor, "update_equity", None)
            if callable(equity_update):
                result = equity_update(current_price)
                if inspect.isawaitable(result):
                    await result
            # Actualizar balance para tracking de drawdown
            await self.risk_mgr.update_balance_for_drawdown(self.balance)
            if self.database:
                self.database.log_balance(self.balance, self.symbol)
            logger.debug(f"{self.symbol} | Precio: {current_price:.4f} | Balance: ${self.balance:.2f}")

            if self.executor.position_open:
                with use_span("position_update", attributes={"symbol": self.symbol}):
                    self.journal.update_runup_drawdown(current_price)
                    accion = await self.position_mgr.update(current_price, df_15m, df_1h)
                    if accion:
                        logger.info(f"{self.symbol}: {accion}")
            else:
                if self.cooldown > 0:
                    logger.debug(f"{self.symbol}: cooldown {self.cooldown} vela(s)")
                    return
                if not await self.risk_mgr.is_trading_session():
                    return
                # Fix #3: Verificar si el circuit breaker por pérdidas consecutivas está activo
                if await self.risk_mgr.is_loss_paused():
                    logger.warning(f"{self.symbol}: en pausa por pérdidas consecutivas")
                    return
                # Validar drawdown diario antes de operar
                try:
                    await self.risk_mgr.validate_drawdown()
                except RiskException as rfe:
                    logger.warning(f"{self.symbol}: {rfe}")
                    self.cooldown = 1
                    if self.notifier:
                        self.notifier.send("🛑 Drawdown máximo",
                                           f"{self.symbol}: {rfe}",
                                           severity="WARNING")
                    return
                # Filtro de spread
                spread = await self.data_feed.get_spread_percent()
                if spread > self.spread_max:
                    logger.debug(f"{self.symbol}: Spread alto ({spread:.3f}%) > máx ({self.spread_max:.2f}%), esperando...")
                    return

                # Obtener señal de mercado (funding rate, OI) si está habilitado
                market_signal = None
                if FUNDING_RATE_ENABLED:
                    with use_span("market_signal_check", attributes={"symbol": self.symbol}):
                        try:
                            fr_data = await self.market_data.get_funding_rate(self.symbol)
                            if fr_data:
                                market_signal = self.market_data.get_market_signal(self.symbol)
                                if market_signal and market_signal.get("score", 0) != 0:
                                    logger.info(
                                        f"{self.symbol} | Señal de mercado: {market_signal['signal']} "
                                        f"(score {market_signal['score']:+d}) | {market_signal.get('reason', '')}"
                                    )
                        except Exception:
                            logger.debug(f"{self.symbol}: mercado no disponible, ignorando señal")
                
                with use_span("strategy_evaluate", attributes={"symbol": self.symbol}):
                    signal = self.strategy.evaluate(
                        df_1h, df_15m, current_price, self.balance,
                        market_signal=market_signal,
                    )
                    if signal.has_signal:
                        # Fix #8: rechazar señal si expiró (más de 30s desde evaluación)
                        if time.time() - signal.timestamp > ScalpingStrategy.SIGNAL_EXPIRY_SECONDS:
                            logger.warning(f"{self.symbol}: señal expirada ({time.time() - signal.timestamp:.0f}s)")
                            return
                        logger.info(f"✅ {self.symbol} señal: {signal}")
                        if not await self.risk_mgr.validate_sl_distance(signal.entry, signal.sl):
                            logger.warning(f"{self.symbol}: SL demasiado amplio")
                            return
                        if not await self.risk_mgr.validate_rr(signal.entry, signal.tp1, signal.sl):
                            logger.warning(f"{self.symbol}: RR insuficiente")
                            return

                        # ── Verificar correlación con otros símbolos ──
                        if self.correlation_guard and not self.correlation_guard.can_open(self.symbol):
                            logger.info(
                                f"🔒 {self.symbol}: señal ignorada — símbolo correlacionado "
                                f"ya tiene posición abierta"
                            )
                            if self.database:
                                self.database.save_event(
                                    "CORRELATION", "INFO",
                                    f"{self.symbol}: señal bloqueada por correlación",
                                    {"symbol": self.symbol}
                                )
                            return

                        with use_span("open_position", attributes={"symbol": self.symbol, "direction": signal.direction}):
                            # Fix #9: Pasar score de la señal para sizing condicional
                            signal_score = getattr(signal, "score", 0)
                            qty, _risk_usd, msg = await self.risk_mgr.calculate_position_size(
                                signal.entry, signal.sl, score=signal_score
                            )
                            if qty <= 0:
                                logger.warning(f"{self.symbol}: tamaño inválido: {msg}")
                                return

                            # ── Verificar exposición total con el nocional nuevo ──
                            notional_nuevo = qty * signal.entry
                            if (self.portfolio_risk is not None
                                    and not self.portfolio_risk.can_open(notional_nuevo)):
                                logger.info(
                                    f"🔒 {self.symbol}: señal ignorada — exposición máxima "
                                    f"del portfolio alcanzada"
                                )
                                if self.database:
                                    self.database.save_event(
                                        "PORTFOLIO_RISK", "INFO",
                                        f"{self.symbol}: bloqueado por exposición total",
                                        {"symbol": self.symbol}
                                    )
                                return

                            await self.executor.set_leverage(self.leverage)
                            qty_tp1, qty_tp2 = await self.risk_mgr.distribute_tp(qty)
                            success = await self.executor.open_position(
                                direction=signal.direction,  # type: ignore[arg-type]
                                entry=signal.entry,
                                sl=signal.sl,
                                tp1=signal.tp1,
                                tp2=signal.tp2,
                                qty=qty,
                                qty_tp1=qty_tp1,
                                qty_tp2=qty_tp2,
                            )
                            if success:
                                # El journal.open() ya se llama dentro de executor.open_position()
                                # Solo actualizamos indicadores en el trade abierto
                                if self.journal and self.journal._open_trade:
                                    self.journal._open_trade["rsi_entry"] = ind_15m.get("RSI", 0)
                                    self.journal._open_trade["adx_entry"] = ind_1h.get("ADX", 0)
                                    self.journal._open_trade["atr_entry"] = ind_15m.get("ATR", 0)
                                    self.journal._open_trade["ma_spread_entry"] = ind_1h.get("MA_spread", 0)
                                self.notifier.trade_open(
                                    symbol=self.symbol,
                                    side=signal.direction,
                                    entry=signal.entry,
                                    sl=signal.sl,
                                    tp1=signal.tp1,
                                    tp2=signal.tp2,
                                    score=signal.score
                                )
        except RiskException as rfe:
            # RiskException no capturada por el bloque específico (e.g., en validate_*)
            logger.warning(f"{self.symbol}: riesgo denegado: {rfe}")
            self.cooldown = 1
            if self.database:
                self.database.save_event("RISK", "WARNING", str(rfe)[:200], {"symbol": self.symbol})
        except Exception as e:
            if self.database:
                self.database.save_event("ERROR", "HIGH", str(e)[:200], {"symbol": self.symbol})
            logger.error(f"{self.symbol}: error en ciclo: {e}")