"""
Recuperación de estado desde el exchange (Binance) tras un reinicio.

Separa la lógica de sincronización con el exchange del orquestador
principal (TradingBot), permitiendo mejor testabilidad y mantenimiento.
"""

from typing import Any
from loguru import logger

from bot.core.state_recovery import StateRecovery


class ExchangeStateRecovery:
    """
    Maneja la recuperación de posiciones abiertas, órdenes de protección y
    estado persistente desde el exchange y almacenamiento local.
    """

    def __init__(self, database: Any = None) -> None:
        self.database = database

    async def recover_positions(self, runners: list[Any]) -> None:
        """Sincroniza las posiciones reales del exchange con los runners."""
        for runner in runners:
            await self._recover_single_position(runner)

    async def _recover_single_position(self, runner: Any) -> None:
        """Recupera la posición de un único símbolo desde el exchange."""
        sym = runner.symbol
        try:
            _ = runner.executor._ensure_client()
            assert runner.executor.session is not None
            client = runner.executor.session
            positions = await client.get_positions(sym)

            if not positions:
                runner.executor.position_open = False
                logger.info(f"✅ {sym}: sin posición abierta en exchange")
                return

            p = positions[0]
            size = float(p.get("size", 0))
            if size <= 0:
                runner.executor.position_open = False
                return

            runner.executor.position_open = True
            runner.executor.position_type = p.get("side", "LONG")
            runner.executor.entry_price = float(p.get("avgPrice", 0) or 0)
            runner.executor.quantity = size
            logger.warning(
                f"⚠️ {sym}: posición abierta detectada "
                f"({runner.executor.position_type} x{size})"
            )
        except Exception as e:
            if self.database:
                self.database.save_event(
                    "ERROR", "HIGH", str(e)[:200], {"symbol": sym},
                )
            logger.error(f"No se pudo recuperar estado de {sym}: {e}")

    def recover_persistent_state(self, runners: list[Any]) -> None:
        """Completa el estado del exchange con datos que Binance no provee.

        Solo aplica el JSON si el exchange confirmó una posición abierta y el
        lado coincide. Nunca resucita una posición fantasma desde un JSON viejo.
        """
        recovered_symbols = StateRecovery.load_all_symbols()
        for runner in runners:
            sym = runner.symbol
            ex = runner.executor
            recovered = recovered_symbols.get(sym, {})

            if not ex.position_open:
                if recovered.get("position_open"):
                    logger.warning(
                        f"🧹 {sym}: JSON viejo decía posición abierta, "
                        "pero el exchange no tiene posición. Se ignora."
                    )
                continue

            if not recovered or not recovered.get("position_open"):
                continue

            saved_side = recovered.get("position_type")
            if saved_side and saved_side != ex.position_type:
                logger.error(
                    f"⚠️ {sym}: lado en JSON ({saved_side}) != exchange "
                    f"({ex.position_type}). Se ignora el JSON."
                )
                continue

            saved_qty = float(recovered.get("quantity") or 0)
            if saved_qty > 0 and abs(saved_qty - ex.quantity) / ex.quantity > 0.05:
                logger.warning(
                    f"⚠️ {sym}: cantidad en JSON ({saved_qty}) != exchange "
                    f"({ex.quantity}). Se conserva la del exchange."
                )

            ex.current_sl = float(recovered.get("current_sl") or 0)
            ex.tp1_price = float(recovered.get("tp1_price") or 0)
            ex.tp2_price = float(recovered.get("tp2_price") or 0)
            ex.tp1_hit = bool(recovered.get("tp1_hit", False))
            ex.trailing_activated = bool(recovered.get("trailing_activated", False))
            logger.info(f"✅ Estado persistente recuperado para {sym}")

    async def recover_tp_orders(self, runners: list[Any]) -> None:
        """Asegura SL y TPs para posiciones abiertas, sin cancelar protección.

        Si no hay SL conocido, cierra la posición por seguridad en lugar de
        dejarla sin stop loss.
        """
        for runner in runners:
            if not runner.executor.position_open:
                continue
            await self._recover_protection(runner)

    async def _recover_protection(self, runner: Any) -> None:
        sym = runner.symbol
        ex = runner.executor
        try:
            assert ex.session is not None
            client = ex.session
            open_orders = await client.get_open_orders(sym)
            opposite = "SELL" if ex.position_type == "LONG" else "BUY"

            has_sl = any(
                o.get("type") == "STOP_MARKET" and o.get("side") == opposite
                for o in open_orders
            )
            tps = [
                o for o in open_orders
                if o.get("type") == "LIMIT"
                and o.get("reduceOnly", False)
            ]

            if not has_sl:
                if ex.current_sl and ex.current_sl > 0:
                    await client.set_stop_loss(
                        symbol=sym,
                        side=ex.position_type or "LONG",
                        stop_price=ex.current_sl,
                    )
                    logger.info(f"🔁 SL recolocado para {sym} @ {ex.current_sl}")
                else:
                    logger.critical(
                        f"🚨 {sym}: posición sin SL y sin precio conocido. "
                        "Cerrando por seguridad."
                    )
                    close_async = getattr(ex, "close_position_async", None)
                    if callable(close_async):
                        await close_async(reason="recovery_no_sl")
                    else:
                        ex.close_position()
                    return

            existing_tp_prices = {
                round(float(o.get("price", 0)), 8)
                for o in tps
                if o.get("price")
            }

            tp1_pct = 0.65
            try:
                _, cfg = await runner.risk_mgr.get_phase_config()
                tp1_pct = float(cfg.get("tp1_porcentaje", tp1_pct))
            except Exception:
                pass

            remaining = ex.quantity
            if ex.tp1_hit:
                qty_tp1 = 0.0
                qty_tp2 = remaining
            else:
                qty_tp1 = (
                    ex._truncate_qty(remaining * tp1_pct)
                    if hasattr(ex, "_truncate_qty")
                    else remaining * tp1_pct
                )
                qty_tp2 = (
                    ex._truncate_qty(remaining - qty_tp1)
                    if hasattr(ex, "_truncate_qty")
                    else remaining - qty_tp1
                )

            if (not ex.tp1_hit and ex.tp1_price and ex.tp1_price > 0
                    and round(float(ex.tp1_price), 8) not in existing_tp_prices):
                await client.place_limit_order(
                    symbol=sym,
                    side=opposite,
                    qty=qty_tp1,
                    price=ex.tp1_price,
                    reduce_only=True,
                )
                logger.info(f"🔁 TP1 recolocado para {sym} @ {ex.tp1_price}")

            if (ex.tp2_price and ex.tp2_price > 0
                    and round(float(ex.tp2_price), 8) not in existing_tp_prices):
                await client.place_limit_order(
                    symbol=sym,
                    side=opposite,
                    qty=qty_tp2,
                    price=ex.tp2_price,
                    reduce_only=True,
                )
                logger.info(f"🔁 TP2 recolocado para {sym} @ {ex.tp2_price}")
        except Exception as e:
            logger.warning(f"No se pudieron recolocar protecciones para {sym}: {e}")
