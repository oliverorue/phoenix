"""
Ejecución de órdenes en Binance USDⓈ-M Futures (Market, SL, TP).
"""
from typing import Optional
import asyncio
import math
from config.loader import API_KEY, API_SECRET, TESTNET
from bot.core.data_feed import DataFeed, decimal_places_for_qty_step
from bot.core.trade_journal import TradeJournal
from bot.core.api_resilience import CircuitBreaker, api_retry, api_retry_async
from bot.core.tracing import trace
from bot.core.exchange_client import BinanceFuturesClient
from bot.core.exceptions import ExecutionException, APIException
from loguru import logger


class OrderExecutor:
    """Maneja la colocación de órdenes de trading en Binance."""

    def __init__(self, data_feed: DataFeed, journal: Optional[TradeJournal] = None) -> None:
        self.data = data_feed
        self.circuit_breaker: CircuitBreaker = CircuitBreaker()
        self.journal = journal
        self.client: Optional[BinanceFuturesClient] = None
        self.position_open = False
        self.position_type: Optional[str] = None   # LONG / SHORT
        self.entry_price: float = 0.0
        self.quantity: float = 0.0
        self.current_sl: float = 0.0
        self.tp1_price: float = 0.0
        self.tp2_price: float = 0.0
        self.tp1_hit: bool = False
        self.trailing_activated = False
        self.last_price: float = 0.0
        self._qty_step: float = 0.001
        self._init_qty_step()

    def _init_qty_step(self) -> None:
        try:
            if hasattr(self.data, '_instrument_cache') and self.data._instrument_cache:
                self._qty_step = float(self.data._instrument_cache['qtyStep'])
            else:
                self._qty_step = 0.001
        except Exception:
            self._qty_step = 0.001

    @property
    def session(self) -> Optional[BinanceFuturesClient]:
        """Compatibilidad con código legacy que accede a self.executor.session."""
        return self.client

    def _ensure_client(self) -> BinanceFuturesClient:
        if not self.client:
            self.client = BinanceFuturesClient(
                api_key=API_KEY or "",
                api_secret=API_SECRET or "",
                testnet=TESTNET,
            )
        return self.client

    @api_retry_async(max_retries=2, base_delay=0.5, critical=True)
    async def set_leverage(self, leverage: int) -> None:
        client = self._ensure_client()
        try:
            await client.set_leverage(self.data.symbol, leverage)
            logger.info(f"Apalancamiento configurado: {leverage}x")
        except APIException:
            # Leverage unchanged is handled inside client, other errors re-raise
            logger.info(f"Leverage ya está en {leverage}x, no se requiere cambio")

    async def _has_real_position(self) -> bool:
        client = self._ensure_client()
        try:
            positions = await client.get_positions(self.data.symbol)
            if positions:
                return positions[0]["size"] > 0
            return False
        except Exception as e:
            logger.warning(f"No se pudo verificar posición real: {e}")
            return False

    def _verify_position_qty(self) -> float:
        """SINCRONO — usa asyncio.run porque es llamado desde open_position (async)."""
        import asyncio as _asyncio
        _client = self._ensure_client()
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                # Ya estamos en event loop, no podemos usar run()
                return self.quantity  # fallback: asumir qty solicitado
            return _asyncio.run(self._verify_position_qty_async())
        except Exception:
            return self.quantity

    async def _verify_position_qty_async(self) -> float:
        client = self._ensure_client()
        try:
            positions = await client.get_positions(self.data.symbol)
            if positions:
                return positions[0]["size"]
            return 0.0
        except Exception as e:
            logger.debug(f"No se pudo verificar cantidad real: {e}")
            return 0.0

    @trace(attributes={"component": "OrderExecutor"})
    async def open_position(
        self, direction: str, entry: float, sl: float,
        tp1: float, tp2: float, qty: float,
        qty_tp1: Optional[float] = None, qty_tp2: Optional[float] = None
    ) -> bool:
        """Abre posición LONG/SHORT con SL y TPs en Binance (2-pasos: market + SL)."""
        client = self._ensure_client()
        sym = self.data.symbol

        # Verificar posición real
        real_pos = await self._has_real_position()
        if real_pos or self.position_open:
            logger.warning("Ya hay posición abierta, cerrando antes...")
            if self.position_open:
                self.close_position()
            else:
                # Desincronización: cerrar desde exchange
                positions = await client.get_positions(sym)
                if positions:
                    opposite = "SELL" if direction == "LONG" else "BUY"
                    size = positions[0]["size"]
                    if size > 0:
                        await client.place_market_order(sym, opposite, size)
                        logger.info(f"Posición real cerrada a mercado")
                self.position_open = False
                self.position_type = None
                await asyncio.sleep(1)

        if qty_tp1 is None:
            qty_tp1 = qty
        if qty_tp2 is None:
            qty_tp2 = qty

        qty = self._truncate_qty(qty)
        qty_tp1 = self._truncate_qty(qty_tp1)
        qty_tp2 = self._truncate_qty(qty_tp2)

        side = "BUY" if direction == "LONG" else "SELL"
        tp_side = "SELL" if direction == "LONG" else "BUY"

        if qty <= 0 or entry <= 0 or sl <= 0:
            logger.error(f"Parámetros inválidos: qty={qty}, entry={entry}, sl={sl}")
            return False

        # ── 1. Orden Market (SIN SL — diferencia clave vs Bybit) ──
        cb = self.circuit_breaker
        if cb and not cb.can_execute():
            remaining = cb.get_remaining_cooldown()
            logger.error(f"⛔ Circuit breaker bloqueó. {remaining/60:.1f}min restantes")
            return False

        order_ok = await self._place_market_order(side, qty)
        if not order_ok:
            return False

        # ── 2. Verificar que la posición existe ──
        await asyncio.sleep(2)
        actual_qty = await self._verify_position_qty_async()
        if actual_qty <= 0:
            logger.error(f"❌ No se detectó posición tras colocar orden en {sym}")
            return False

        if abs(actual_qty - qty) / qty > 0.005:
            logger.warning(f"⚠️ Partial fill en {sym}: solicitado {qty}, recibido {actual_qty}")
            self.quantity = actual_qty
            ratio = actual_qty / qty
            qty_tp1 = self._truncate_qty(qty_tp1 * ratio)
            qty_tp2 = self._truncate_qty(qty_tp2 * ratio)
        else:
            self.quantity = qty

        # ── 3. Colocar Stop Loss (orden separada — Binance no soporta SL inline) ──
        try:
            await client.set_stop_loss(sym, direction, sl)
            logger.info(f"🛑 SL colocado: {sl:.4f}")
        except Exception as e:
            logger.error(f"❌ No se pudo colocar SL: {e}")
            # Cerrar la posición si no se pudo colocar SL
            close_side = "SELL" if direction == "LONG" else "BUY"
            try:
                await client.place_market_order(sym, close_side, self.quantity)
            except Exception:
                pass
            return False

        # ── 4. Actualizar estado en memoria ──
        self.position_open = True
        self.position_type = direction
        self.entry_price = entry
        self.current_sl = sl
        self.tp1_price = tp1
        self.tp2_price = tp2
        self.tp1_hit = False
        self.trailing_activated = False

        logger.info(f"✅ {direction} abierto | Entry: {entry:.4f} | SL: {sl:.4f} | Qty: {self.quantity}")

        # Journal
        if self.journal:
            self.journal.open(
                symbol=sym, side=direction, entry_price=entry,
                qty=self.quantity, sl=sl, tp1=tp1, tp2=tp2
            )
            if self.journal._open_trade:
                self.journal._open_trade["commission_usdt"] = (
                    self.quantity * entry * 0.0005 + self.quantity * tp1 * 0.0005
                )

        # ── 5. Colocar TP1 y TP2 (órdenes limit reduce-only) ──
        try:
            await client.place_limit_order(sym, tp_side, qty_tp1, tp1, reduce_only=True)
            logger.info(f"TP1 colocado: {tp1:.4f} x {qty_tp1}")
        except Exception as e:
            logger.warning(f"No se pudo colocar TP1: {e}")

        try:
            await client.place_limit_order(sym, tp_side, qty_tp2, tp2, reduce_only=True)
            logger.info(f"TP2 colocado: {tp2:.4f} x {qty_tp2}")
        except Exception as e:
            logger.warning(f"No se pudo colocar TP2: {e}")

        if cb:
            cb.record_success()
        return True

    async def _place_market_order(self, side: str, qty: float) -> bool:
        """Coloca orden market (SIN SL). Reintentos solo para errores transitorios."""
        client = self._ensure_client()
        sym = self.data.symbol
        cb = self.circuit_breaker

        max_retries = 2
        delay = 0.5

        for attempt in range(max_retries + 1):
            try:
                await client.place_market_order(sym, side, qty)
                return True
            except ExecutionException as e:
                error_str = str(e).lower()
                # No recuperables
                if any(code in error_str for code in ["-2010", "-2011", "-1013", "-1121"]):
                    logger.error(f"❌ place_order: error no recuperable: {e}")
                    if cb:
                        cb.record_failure()
                    return False
                # Transitorios
                is_retryable = any(code in error_str for code in [
                    "500", "502", "503", "504", "timeout", "timed out",
                    "connection", "429", "rate limit"
                ])
                if not is_retryable:
                    if cb:
                        cb.record_failure()
                    return False
                logger.warning(f"⚠️ place_order - Intento {attempt+1}/{max_retries+1} fallido: {e}")
                if attempt >= max_retries:
                    if cb:
                        cb.record_failure()
                    return False
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
            except APIException as e:
                if cb:
                    cb.record_failure()
                logger.error(f"❌ place_order API error: {e}")
                return False

        return False

    def _truncate_qty(self, qty: float) -> float:
        step = self._qty_step
        if step > 0:
            truncated = math.floor(qty / step) * step
            decimals = decimal_places_for_qty_step(step)
            return round(truncated, decimals)
        return qty

    async def _cancel_limit_orders(self) -> None:
        """Cancela TODAS las órdenes limit abiertas (TPs)."""
        client = self._ensure_client()
        try:
            orders = await client.get_open_orders(self.data.symbol)
            for order in orders:
                if order.get("type") == "LIMIT" and order.get("reduceOnly", False):
                    await client.cancel_order(self.data.symbol, order["orderId"])
                    logger.debug(f"🧹 TP cancelado: {order.get('orderId', '?')}")
        except Exception as e:
            logger.warning(f"No se pudieron cancelar TPs: {e}")

    @trace(attributes={"component": "OrderExecutor"})
    @api_retry(max_retries=2, base_delay=0.5, critical=True)
    def close_position(self) -> None:
        """Cierra posición a mercado (SINCRONO — legacy compat)."""
        if not self.position_open:
            return
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                _asyncio.ensure_future(self._close_position_async())
            else:
                _asyncio.run(self._close_position_async())
        except Exception as e:
            logger.error(f"Error cerrando posición: {e}")

    async def _close_position_async(self) -> None:
        if not self.position_open:
            return
        client = self._ensure_client()
        sym = self.data.symbol
        side = "SELL" if self.position_type == "LONG" else "BUY"
        exit_price = self.last_price if self.last_price > 0 else self.entry_price

        await self._cancel_limit_orders()
        await client.place_market_order(sym, side, self.quantity)

        logger.info(f"Posición {self.position_type} cerrada a mercado ~{exit_price:.4f}")
        if self.journal:
            self.journal.close(
                exit_price=exit_price,
                close_reason="manual_close",
                sl_final=self.current_sl
            )
        self.position_open = False
        self.position_type = None

    @trace(attributes={"component": "OrderExecutor"})
    @api_retry(max_retries=2, base_delay=0.5, critical=True)
    def update_stop_loss(self, new_sl: float) -> None:
        """Actualiza Stop Loss (SINCRONO — legacy compat)."""
        import asyncio as _asyncio
        self.current_sl = new_sl
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                _asyncio.ensure_future(self._update_stop_loss_async(new_sl))
            else:
                _asyncio.run(self._update_stop_loss_async(new_sl))
        except Exception as e:
            logger.error(f"Error actualizando SL: {e}")

    async def _update_stop_loss_async(self, new_sl: float) -> None:
        client = self._ensure_client()
        await client.update_stop_loss(
            self.data.symbol,
            self.position_type or "LONG",
            new_sl,
        )
        self.current_sl = new_sl
        logger.info(f"SL actualizado a {new_sl:.4f}")

    @trace(attributes={"component": "OrderExecutor"})
    @api_retry(max_retries=2, base_delay=0.5, critical=True)
    def activate_trailing_stop(self, distance: float, activation_price: float) -> bool:
        """Activa trailing stop client-side (NO nativo en Binance)."""
        import asyncio as _asyncio
        try:
            loop = _asyncio.get_event_loop()
            if loop.is_running():
                _asyncio.ensure_future(
                    self._activate_trailing_async(distance, activation_price)
                )
            else:
                _asyncio.run(
                    self._activate_trailing_async(distance, activation_price)
                )
            return True
        except Exception as e:
            logger.error(f"Error activando trailing: {e}")
            return False

    async def _activate_trailing_async(self, distance: float, activation_price: float) -> None:
        client = self._ensure_client()
        await client.set_trailing_stop(
            self.data.symbol,
            self.position_type or "LONG",
            distance,
            activation_price,
        )
        logger.info(
            f"🎯 Trailing stop client-side activado | "
            f"Dist: {distance:.4f} | Activación: {activation_price:.4f}"
        )

    def _format_qty(self, qty: float) -> str:
        qty = self._truncate_qty(qty)
        decimals = decimal_places_for_qty_step(self._qty_step)
        return f"{qty:.{decimals}f}"
