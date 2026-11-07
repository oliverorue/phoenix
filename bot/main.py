#!/usr/bin/env python3
"""
Orquestador principal de Phoenix ‑ Multi‑símbolo.
"""
import asyncio
from datetime import datetime, timezone
from typing import Any, Optional
import yaml
from pathlib import Path

from config.loader import SYMBOL, TESTNET, WS_ENABLED
from config.loader import (
    PAPER_TRADING_ENABLED, PAPER_INITIAL_BALANCE, PAPER_SLIPPAGE_PCT,
    PAPER_COMMISSION_PCT, PAPER_MAKER_COMMISSION_PCT,
)
from bot.core.data_feed import DataFeed
from bot.core.trade_journal import TradeJournal
from bot.core.notifier import Notifier
from bot.core.state_recovery import StateRecovery
from bot.core.symbol_runner import SymbolRunner
from bot.core.paper_executor import PaperExecutor
from bot.core.database import DatabaseManager
from bot.core.blockchain_audit import BlockchainAudit
from bot.core.scheduler import BotScheduler
from bot.core.health import HealthMonitor
from bot.core.ws_feed import WebSocketFeed
from bot.core.exchange_recovery import ExchangeStateRecovery
from bot.core.reconciliation import OrderReconciler
from bot.core.correlation import CorrelationGuard
from bot.core.kelly_sizing import KellySizing
from bot.core.portfolio_risk import PortfolioRisk
from bot.core.metrics_exporter import MetricsExporter
from bot.core.config_watcher import config_watcher
from bot.core.shutdown import create_shutdown, GracefulShutdown
from config.loader import (
    CORRELATION_GROUPS, KELLY_SIZING_CFG, PORTFOLIO_RISK_CFG,
    METRICS_ENABLED, METRICS_PORT, METRICS_JSON_PATH,
    TRACING_ENABLED, TRACING_EXPORTER, TRACING_OTLP_ENDPOINT, TRACING_SAMPLING_RATE,
    SHUTDOWN_TIMEOUT, SHUTDOWN_CLOSE_POSITIONS, SHUTDOWN_NOTIFY,
    assert_runtime_safety,
)
from loguru import logger

from bot.core.tracing import (
    trace, init_tracing, trace_id_filter,
)

logger.add("logs/bot.log", rotation="10 MB", retention="7 days", level="INFO")
# Parchar loguru para inyectar trace_id en cada registro
logger.configure(patcher=trace_id_filter)  # type: ignore[arg-type]


class TradingBot:
    def __init__(self, state_manager: Any = None, portfolio_config: Any = None,
                 metrics_registry: Any = None) -> None:
        # ── Inicializar Tracing (OpenTelemetry) ──
        if TRACING_ENABLED:
            try:
                init_tracing(
                    exporter=TRACING_EXPORTER,
                    otlp_endpoint=TRACING_OTLP_ENDPOINT or None,
                    sampling_rate=TRACING_SAMPLING_RATE,
                )
                logger.info("🔍 OpenTelemetry tracing inicializado")
            except Exception as exc:
                logger.warning(f"⚠️ Error iniciando tracing: {exc}")
        else:
            from bot.core.tracing import set_enabled
            set_enabled(False)

        self.state = state_manager
        self.notifier = Notifier()
        self.database = DatabaseManager()
        self.audit = BlockchainAudit()
        # ── Portfolio Risk (VaR / Exposición Total) ──
        _pr_cfg = PORTFOLIO_RISK_CFG
        if _pr_cfg.get("enabled", True):
            self.portfolio_risk = PortfolioRisk(
                database=self.database,
                notifier=self.notifier,
                var_confidence=_pr_cfg.get("var_confidence", 0.95),
                var_lookback_days=_pr_cfg.get("var_lookback_days", 30),
                max_exposure_usdt=_pr_cfg.get("max_exposure_usdt", 0.0),
                max_exposure_pct=_pr_cfg.get("max_exposure_pct", 0.0),
            )
        else:
            self.portfolio_risk = None
        # ── Kelly Criterion Dynamic Sizing ──
        self.kelly_sizing = KellySizing(
            window_size=KELLY_SIZING_CFG.get("window_size", 20),
            min_trades=KELLY_SIZING_CFG.get("min_trades", 5),
            max_multiplier=KELLY_SIZING_CFG.get("max_multiplier", 2.5),
            min_multiplier=KELLY_SIZING_CFG.get("min_multiplier", 0.3),
            fractional=KELLY_SIZING_CFG.get("fractional", 0.25),
            streak_boost=KELLY_SIZING_CFG.get("streak_boost", 0.08),
            streak_penalty=KELLY_SIZING_CFG.get("streak_penalty", 0.12),
        )
        self.journal = TradeJournal(
            notifier=self.notifier,
            database=self.database,
            audit=self.audit,
            kelly_sizing=self.kelly_sizing,
        )
        self.scheduler = BotScheduler(self.database, self.journal)
        self.exchange_recovery = ExchangeStateRecovery(database=self.database)
        # Order reconciler (limpieza continua de órdenes huérfanas)
        self.reconciler = OrderReconciler(
            database=self.database,
            notifier=self.notifier,
            interval_cycles=12,  # ~1 min con WS, ~3 min sin WS
        )
        # Correlation guard (evita doble riesgo en símbolos correlacionados)
        self.correlation_guard = CorrelationGuard(groups=CORRELATION_GROUPS)
        # ── Metrics Exporter (Prometheus + JSON) ──
        if METRICS_ENABLED:
            self.metrics_exporter = MetricsExporter(
                port=METRICS_PORT,
                metrics_path=METRICS_JSON_PATH,
                registry=metrics_registry,
            )
        else:
            self.metrics_exporter = None
        # WebSocket feed (opcional)
        self.ws_feeds: dict[str, WebSocketFeed] = {}
        self._init_event_loop()

    def _init_event_loop(self) -> None:
        """Inicializa el event loop si no existe."""
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)

    @trace(attributes={"component": "TradingBot"})
    async def _async_init(self, portfolio_config: Any = None) -> None:
        """Inicialización asíncrona del bot (balance, runners, estado)."""
        # ── Determinar modo de ejecución y validar seguridad ────────
        paper_mode = PAPER_TRADING_ENABLED
        assert_runtime_safety(paper_mode=paper_mode)
        if paper_mode:
            logger.info(
                f"📄 PAPER TRADING activado | Balance virtual: ${PAPER_INITIAL_BALANCE:.2f} | "
                f"Slippage: {PAPER_SLIPPAGE_PCT*100:.2f}% | Comisión: {PAPER_COMMISSION_PCT*100:.2f}%"
            )

        # ── Cargar configuración de portfolio ──────────────────────
        if portfolio_config:
            portfolio = portfolio_config
        else:
            settings_path = Path(__file__).parent.parent / "config" / "settings.yaml"
            with open(settings_path) as f:
                settings = yaml.safe_load(f)
            portfolio = settings.get("portfolio", {}).get("symbols", [])

            if not portfolio:
                portfolio = [{
                    "symbol": SYMBOL,
                    "allocation_pct": 1.0,
                    "leverage": 1,
                    "risk_per_trade": 0.02,
                    "active": True
                }]

        # Filtrar solo los activos activos
        portfolio = [p for p in portfolio if p.get("active", True)]

        if not portfolio:
            raise ValueError("No hay símbolos activos en el portfolio")

        # ── Obtener balance para distribuir ─────────────────────────
        if paper_mode:
            total_balance = PAPER_INITIAL_BALANCE
            if total_balance <= 0:
                raise RuntimeError(
                    f"paper_trading.initial_balance inválido: ${total_balance:.2f}. "
                    "Debe ser mayor que 0."
                )
            logger.info(f"[PAPER] Balance virtual: ${total_balance:.2f}")
        else:
            # Sin balance real no se puede dimensionar riesgo: abortar.
            try:
                temp_feed = DataFeed(symbol=portfolio[0]["symbol"], testnet=TESTNET)
                total_balance = await temp_feed.get_balance()
                # Fail-fast: verificar que el instrumento se pueda cargar.
                await temp_feed.get_instrument_info()
            except Exception as e:
                raise RuntimeError(
                    f"No se pudo validar la cuenta/instrumento en modo real: {e}. "
                    "El bot no arranca sin balance e instrumento verificados."
                ) from e

            if total_balance <= 0:
                raise RuntimeError(
                    f"Balance USDT inválido (${total_balance:.2f}). "
                    "El bot no arranca en modo real sin fondos."
                )

        # ── Crear runners para cada símbolo ────────────────────────
        self.runners = []
        for cfg in portfolio:
            # En paper mode, inyectar PaperExecutor en vez de OrderExecutor
            if paper_mode:
                paper_exec = PaperExecutor(
                    data_feed=DataFeed(symbol=cfg["symbol"], testnet=TESTNET),
                    journal=self.journal,
                    initial_balance=total_balance * cfg["allocation_pct"],
                    slippage_pct=PAPER_SLIPPAGE_PCT,
                    commission_pct=PAPER_COMMISSION_PCT,
                    maker_commission_pct=PAPER_MAKER_COMMISSION_PCT,
                )
            else:
                paper_exec = None

            runner = SymbolRunner(
                symbol=cfg["symbol"],
                allocation_pct=cfg["allocation_pct"],
                leverage=cfg.get("leverage", 1),
                risk_per_trade=cfg.get("risk_per_trade", 0.02),
                spread_max=cfg.get("spread_max", 0.15),
                journal=self.journal,
                notifier=self.notifier,
                total_balance=total_balance,
                correlation_guard=self.correlation_guard,
                portfolio_risk=self.portfolio_risk,
                executor=paper_exec,
            )
            # Vincular KellySizing al RiskManager de cada runner
            runner.risk_mgr._kelly_sizing = self.kelly_sizing
            self.runners.append(runner)
            logger.info(f"➕ Símbolo agregado: {cfg['symbol']} (alloc {cfg['allocation_pct']*100:.0f}%)")

        self.balance = total_balance
        self.start_time = datetime.now(timezone.utc)

        # ── Iniciar WebSocket si está habilitado ─────────────────
        if WS_ENABLED:
            await self._start_websocket_feeds()

        # ── Vincular correlation guard con runners ─────────────────
        self.correlation_guard.bind_runners(self.runners)

        # ── Vincular portfolio risk con runners ────────────────────
        if self.portfolio_risk is not None:
            self.portfolio_risk.bind_runners(self.runners)

        # ── Iniciar Metrics Exporter ───────────────────────────────
        if self.metrics_exporter is not None:
            self.metrics_exporter.set_bot_ref(self)
            self.metrics_exporter.start()

        # ── Recuperar estado (solo en modo real) ──────────────────
        if not paper_mode:
            await self.exchange_recovery.recover_positions(self.runners)
            self.exchange_recovery.recover_persistent_state(self.runners)
            await self.exchange_recovery.recover_tp_orders(self.runners)
        else:
            logger.info("[PAPER] Saltando recuperación de exchange (no hay posiciones reales)")

        if self.state:
            self.state.update({"active_symbols": len(self.runners)})

    async def _start_websocket_feeds(self) -> None:
        """Inicia WebSocket feeds para todos los símbolos."""
        for runner in self.runners:
            sym = runner.symbol
            try:
                ws = WebSocketFeed(
                    symbol=sym,
                    testnet=TESTNET,
                    rest_fallback=runner.data_feed,
                )
                # Callback para notificar al runner cuando hay nueva vela 15m
                ws.on_kline_15m = lambda data, r=runner: asyncio.ensure_future(
                    self._on_ws_kline_15m(r, data)
                )
                if ws.start():
                    self.ws_feeds[sym] = ws
                    logger.info(f"✅ WebSocket iniciado para {sym}")
                else:
                    logger.warning(f"⚠️ WebSocket no disponible para {sym}, usando REST")
            except Exception as e:
                logger.warning(f"⚠️ Error iniciando WebSocket para {sym}: {e}")

    async def _on_ws_kline_15m(self, runner: Any, data: dict[str, Any]) -> None:
        """Callback cuando llega una nueva vela 15m por WebSocket."""
        pass  # placeholder para futuras optimizaciones


    def _save_cycle_state(self) -> None:
        """Guarda estado persistente de TODOS los runners."""
        now_iso = datetime.now(timezone.utc).isoformat()
        for runner in self.runners:
            StateRecovery.save_symbol_state(runner.symbol, {
                "position_open": runner.executor.position_open,
                "position_type": runner.executor.position_type,
                "entry_price": runner.executor.entry_price,
                "current_sl": runner.executor.current_sl,
                "tp1_price": runner.executor.tp1_price,
                "tp2_price": runner.executor.tp2_price,
                "balance": self.balance,
                "quantity": runner.executor.quantity,
                "tp1_hit": runner.executor.tp1_hit,
                "timestamp": now_iso,
            })

    @trace(attributes={"component": "TradingBot"})
    async def _update_dashboard_state(self) -> None:
        """Actualiza el estado compartido con el dashboard iterando TODOS los runners."""
        if not self.state:
            return
        try:
            primary = self.runners[0]
            current_price = None
            df = await primary.data_feed.current_15m()
            if not df.empty:
                current_price = df["close"].iloc[-1]

            symbols_state = {}
            total_unrealized_pnl_usdt = 0.0
            for runner in self.runners:
                sym = runner.symbol
                sym_data = {
                    "symbol": sym,
                    "fase_actual": runner.fase_num,
                    "position_open": runner.executor.position_open,
                }
                unrealized_pnl = 0.0
                if runner.executor.position_open:
                    entry = runner.executor.entry_price or 0.0
                    qty = runner.executor.quantity or 0.0
                    last = runner.executor.last_price or current_price or entry
                    side = runner.executor.position_type
                    if entry > 0 and qty > 0:
                        if side == "LONG":
                            unrealized_pnl = (last - entry) * qty
                        else:  # SHORT
                            unrealized_pnl = (entry - last) * qty
                    total_unrealized_pnl_usdt += unrealized_pnl
                    sym_data["position"] = {
                        "active": True,
                        "type": side,
                        "entry_price": entry,
                        "current_sl": runner.executor.current_sl,
                        "tp1": runner.executor.tp1_price,
                        "tp2": runner.executor.tp2_price,
                        "unrealized_pnl": round(unrealized_pnl, 2),
                        "qty": qty,
                        "last_price": last,
                    }
                else:
                    sym_data["position"] = {"active": False, "unrealized_pnl": 0.0}
                symbols_state[sym] = sym_data

            # Obtener drawdown del primer runner (todos comparten la misma lógica de riesgo)
            dd_pct = 0.0
            try:
                dd_pct = await primary.risk_mgr.get_current_drawdown_pct()
            except Exception:
                pass

            portfolio_risk_stats = {}
            if self.portfolio_risk is not None:
                try:
                    portfolio_risk_stats = self.portfolio_risk.stats
                except Exception:
                    portfolio_risk_stats = {"error": "Error obteniendo stats"}

            # Calcular equity = balance + unrealized PnL
            equity = (self.balance or 0.0) + total_unrealized_pnl_usdt
            unrealized_pnl_pct = 0.0
            if self.balance and self.balance > 0:
                unrealized_pnl_pct = (total_unrealized_pnl_usdt / self.balance) * 100

            # PnL diario desde DB
            daily_pnl = 0.0
            try:
                today_stats = self.database.get_stats(days=1)
                daily_pnl = today_stats.get("total_pnl", 0) or 0.0
            except Exception:
                pass

            # Equity history (mantener máx 200 puntos ~ 10 min con intervalos de 3s)
            state_copy = self.state.get_state() if self.state else {}
            equity_history = list(state_copy.get("equity_history", []))
            equity_history.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "equity": round(equity, 2),
                "balance": round(self.balance or 0.0, 2),
                "unrealized_pnl": round(total_unrealized_pnl_usdt, 2),
            })
            # Limitar a 200 puntos
            if len(equity_history) > 200:
                equity_history = equity_history[-200:]

            self.state.update({
                "symbol": primary.symbol,
                "symbols": symbols_state,
                "testnet": TESTNET,
                "fase_actual": primary.fase_num,
                "balance": self.balance,
                "current_price": current_price,
                "unrealised_pnl_pct": round(unrealized_pnl_pct, 2),
                "unrealised_pnl_usdt": round(total_unrealized_pnl_usdt, 2),
                "equity": round(equity, 2),
                "daily_pnl": round(daily_pnl, 2),
                "risk": {
                    "stops_dia": primary.risk_mgr.stops_dia,
                    "max_stops_dia": primary.fase_cfg.get("max_stops_day", 4),
                    "session_active": await primary.risk_mgr.is_trading_session() if primary.fase_cfg else True,
                    "drawdown_pct": round(dd_pct, 2),
                },
                "portfolio_risk": portfolio_risk_stats,
                "uptime_seconds": (datetime.now(timezone.utc) - self.start_time).total_seconds(),
                "equity_history": equity_history,
            })

            active_pos = None
            for runner in self.runners:
                if runner.executor.position_open:
                    active_pos = runner
                    break
            target = active_pos or primary
            if target.executor.position_open:
                self.state.set_position({
                    "active": True,
                    "type": target.executor.position_type,
                    "entry_price": target.executor.entry_price,
                    "current_sl": target.executor.current_sl,
                    "tp1": target.executor.tp1_price,
                    "tp2": target.executor.tp2_price,
                })
            else:
                self.state.set_position({"active": False})
            self.state.flush()
        except Exception as e:
            logger.exception(f"Error actualizando dashboard: {e}")

    @trace(attributes={"component": "TradingBot"})
    async def run(self, stop_event: Optional[asyncio.Event] = None,
                  shutdown_ctl: Optional[GracefulShutdown] = None) -> None:
        logger.info(f"🔥 Phoenix iniciado con {len(self.runners)} par(es)")
        await self.scheduler.start()
        health_monitor = HealthMonitor(
            data_feed=self.runners[0].data_feed,
            notifier=self.notifier,
            interval=300,
            paper_mode=PAPER_TRADING_ENABLED,
            balance_provider=lambda: (
                self.runners[0].balance if self.runners else self.balance
            ),
        )
        # Crear stop_event si no se proveyó (ANTES de crear health_task)
        if stop_event is None:
            stop_event = asyncio.Event()

        # ── GracefulShutdown control ────────────────────────────────
        if shutdown_ctl is None:
            shutdown_ctl = create_shutdown(self, stop_event=stop_event)

        # Crear y trackear health monitor task
        health_task = asyncio.create_task(health_monitor.run(stop_event))
        shutdown_ctl.track_task("health", health_task)

        # ── Config watcher: recarga en caliente de settings.yaml ──
        cfg_watch_task = asyncio.create_task(config_watcher.watch())
        shutdown_ctl.track_task("config_watcher", cfg_watch_task)

        while not stop_event.is_set():
            tasks = [runner.run_cycle() for runner in self.runners]
            await asyncio.gather(*tasks, return_exceptions=True)

            # Mantener el balance agregado (live: allocated sum ≈ total;
            # paper: suma de balances virtuales).
            if self.runners:
                self.balance = sum(r.balance for r in self.runners)

            # Reconciliación periódica de órdenes (cada N ciclos)
            await self.reconciler.reconcile_all(self.runners)

            self._save_cycle_state()
            await self._update_dashboard_state()

            # Actualizar métricas exportables (Prometheus + JSON)
            if self.metrics_exporter is not None:
                self.metrics_exporter.update()

            # Si WebSocket está activo, esperar menos tiempo
            if WS_ENABLED and any(f.connected for f in self.ws_feeds.values()):
                await asyncio.sleep(5)  # ciclo rápido con WS
            else:
                await DataFeed.wait_for_next_candle(15)

        # ── Shutdown graceful gestionado por GracefulShutdown ──
        await shutdown_ctl.shutdown()


async def main() -> None:
    bot = TradingBot()
    await bot._async_init()
    stop_event = asyncio.Event()

    # Crear GracefulShutdown
    shutdown_ctl = create_shutdown(
        bot,
        stop_event=stop_event,
        timeout=SHUTDOWN_TIMEOUT,
        close_positions=SHUTDOWN_CLOSE_POSITIONS,
        notify=SHUTDOWN_NOTIFY,
    )

    # Registrar señales (SIGTERM, SIGINT, SIGHUP)
    shutdown_ctl.register_signals()

    try:
        await bot.run(stop_event, shutdown_ctl=shutdown_ctl)
    except Exception as exc:
        logger.error(f"❌ Error fatal en el bot: {exc}")
        # Asegurar shutdown incluso tras error
        await shutdown_ctl.shutdown()
        raise

if __name__ == "__main__":
    asyncio.run(main())
