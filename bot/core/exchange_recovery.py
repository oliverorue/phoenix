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
    Maneja la recuperación de posiciones abiertas, órdenes TP y
    estado persistente desde el exchange y almacenamiento local.
    """

    def __init__(self, database: Any = None) -> None:
        self.database = database

    async def recover_positions(self, runners: list[Any]) -> None:
        """
        Recupera posiciones abiertas desde el exchange para todos los runners.
        Sincroniza flags en memoria con el estado real del exchange.
        """
        for runner in runners:
            await self._recover_single_position(runner)

    async def _recover_single_position(self, runner: Any) -> None:
        """Recupera la posición de un único símbolo desde el exchange."""
        sym = runner.symbol
        try:
            # Forzar inicialización del cliente (ya no hay session_http)
            _ = runner.executor._ensure_client()
            assert runner.executor.session is not None
            client = runner.executor.session
            positions = await client.get_positions(sym)
            if positions:
                p = positions[0]
                size = p.get("size", 0)
                if size > 0:
                    runner.executor.position_open = True
                    runner.executor.position_type = p.get("side", "LONG")
                    runner.executor.entry_price = float(p.get("avgPrice", 0))
                    # SL remoto no se puede leer directamente de get_positions en Binance
                    runner.executor.quantity = size
                    logger.warning(f"⚠️ {sym}: Posición abierta detectada ({runner.executor.position_type} x{size})")
                    # Cancelar órdenes limit pendientes (TPs viejos)
                    await self._cancel_orphan_orders(runner, sym)
                else:
                    logger.info(f"✅ {sym}: Sin posición abierta")
            else:
                logger.info(f"✅ {sym}: Sin posición abierta")
        except Exception as e:
            if self.database:
                self.database.save_event("ERROR", "HIGH", str(e)[:200], {"symbol": runner.symbol})
            logger.error(f"No se pudo recuperar estado de {sym}: {e}")

    async def _cancel_orphan_orders(self, runner: Any, sym: str) -> None:
        """Cancela órdenes limit huérfanas (TPs viejos) para un símbolo."""
        try:
            assert runner.executor.session is not None
            client = runner.executor.session
            open_orders = await client.get_open_orders(sym)
            for order in open_orders:
                await client.cancel_order(sym, order["orderId"])
            logger.info(f"🧹 Órdenes huérfanas canceladas para {sym}")
        except Exception as e:
            logger.warning(f"No se pudieron cancelar órdenes huérfanas de {sym}: {e}")

    def recover_persistent_state(self, runners: list[Any]) -> None:
        """
        Recupera estado persistente (JSON) para todos los símbolos.
        Esto complementa la recuperación desde el exchange con datos
        que el exchange no provee (TP1/TP2 prices, flags, etc.).
        """
        recovered_symbols = StateRecovery.load_all_symbols()
        for runner in runners:
            sym = runner.symbol
            recovered = recovered_symbols.get(sym, {})
            if recovered and recovered.get("position_open"):
                runner.executor.position_open = recovered.get("position_open")
                runner.executor.position_type = recovered.get("position_type")
                runner.executor.entry_price = float(recovered.get("entry_price") or 0)
                runner.executor.current_sl = float(recovered.get("current_sl") or 0)
                runner.executor.tp1_price = float(recovered.get("tp1_price") or 0)
                runner.executor.tp2_price = float(recovered.get("tp2_price") or 0)
                runner.executor.quantity = float(recovered.get("quantity") or 0)
                runner.executor.tp1_hit = bool(recovered.get("tp1_hit", False))
                logger.info(f"✅ Estado persistente recuperado para {sym}")

    async def recover_tp_orders(self, runners: list[Any]) -> None:
        """
        Recoloca órdenes de TP si hay posición abierta sin TPs configurados.
        Esto maneja el caso donde el bot se reinició y perdió las órdenes
        limit de TP1/TP2 que estaban en el libro de órdenes.
        """
        for runner in runners:
            sym = runner.symbol
            if not runner.executor.position_open:
                continue
            await self._recover_single_tp(runner, sym)

    async def _recover_single_tp(self, runner: Any, sym: str) -> None:
        """Recoloca TPs para un único símbolo."""
        try:
            assert runner.executor.session is not None
            client = runner.executor.session
            open_orders = await client.get_open_orders(sym)
            existing_tps = [
                o for o in open_orders
                if o.get("type") == "LIMIT" and o.get("reduceOnly", False)
            ]
            if existing_tps:
                logger.info(f"✅ {sym}: {len(existing_tps)} TP(s) ya existentes")
                return

            # No hay TPs, recolocar
            tp_side = "SELL" if runner.executor.position_type == "LONG" else "BUY"
            if runner.executor.tp1_price and not runner.executor.tp1_hit:
                assert runner.executor.session is not None
                await client.place_limit_order(
                    symbol=sym,
                    side=tp_side,
                    qty=runner.executor.quantity * 0.65,
                    price=runner.executor.tp1_price,
                    reduce_only=True,
                )
                logger.info(f"🔁 TP1 recolocado para {sym} @ {runner.executor.tp1_price}")
            if runner.executor.tp2_price:
                assert runner.executor.session is not None
                await client.place_limit_order(
                    symbol=sym,
                    side=tp_side,
                    qty=runner.executor.quantity * 0.35,
                    price=runner.executor.tp2_price,
                    reduce_only=True,
                )
                logger.info(f"🔁 TP2 recolocado para {sym} @ {runner.executor.tp2_price}")
        except Exception as e:
            logger.warning(f"No se pudieron recolocar TPs para {sym}: {e}")
