"""
Reconciliación continua de órdenes entre el bot y el exchange.

Previene pérdidas por órdenes huérfanas:
- TPs limit que quedan en el exchange tras cerrar una posición
- Posiciones no detectadas por el bot (crash → restart)
- Flags en memoria desincronizados con el estado real

Uso:
    reconciler = OrderReconciler(database=db, notifier=notifier)
    await reconciler.reconcile_all(runners)
"""

from typing import Any, Optional
from loguru import logger
from bot.core.exchange_recovery import ExchangeStateRecovery


class OrderReconciler:
    """
    Reconciliación periódica entre el estado del bot y el exchange.

    Se ejecuta cada N ciclos (configurable via interval_cycles) para:
    1. Cancelar órdenes limit reduce‑only (TP) que quedaron huérfanas
    2. Detectar posiciones en el exchange que el bot no conoce
    3. Detectar flags phantom (bot cree que hay posición pero no existe)
    4. Alertar sobre cualquier inconsistencia detectada
    """

    def __init__(self, database: Any = None, notifier: Any = None,
                 interval_cycles: int = 12) -> None:
        """
        Args:
            database: DatabaseManager opcional para log de eventos.
            notifier: Notifier opcional para alertas.
            interval_cycles: Cada cuántos ciclos de run_cycle ejecutar
                             reconciliación. Default 12 (~1 min con WS,
                             ~3 min sin WS).
        """
        self.database = database
        self.notifier = notifier
        self.interval_cycles = interval_cycles
        self._cycle_counter: int = 0
        self._total_reconciliations: int = 0
        self._total_orphans_cancelled: int = 0
        self._total_desyncs_fixed: int = 0

    @property
    def stats(self) -> dict[str, Any]:
        """Estadísticas acumuladas de reconciliación."""
        return {
            "total_reconciliations": self._total_reconciliations,
            "orphans_cancelled": self._total_orphans_cancelled,
            "desyncs_fixed": self._total_desyncs_fixed,
        }

    async def reconcile_all(self, runners: list[Any]) -> None:
        """
        Ejecuta reconciliación completa para todos los símbolos.
        Solo ejecuta cada N ciclos (interval_cycles).
        """
        self._cycle_counter += 1
        if self._cycle_counter < self.interval_cycles:
            return  # aún no toca

        self._cycle_counter = 0
        self._total_reconciliations += 1

        for runner in runners:
            try:
                await self._reconcile_symbol(runner)
            except Exception as e:
                logger.warning(
                    f"Reconciliación fallida para {runner.symbol}: {e}"
                )

    async def _reconcile_symbol(self, runner: Any) -> None:
        """Reconcilia un único símbolo contra el exchange."""
        sym = runner.symbol
        client = runner.executor._ensure_client()  # BinanceFuturesClient

        # ── 1. Obtener órdenes abiertas del exchange ────────
        open_orders = await self._fetch_open_orders(client, sym)
        limit_orders = [
            o for o in open_orders
            if o.get("type") == "LIMIT" and o.get("reduceOnly", False)
        ]

        # ── 2. Obtener posición real del exchange ───────────
        real_position = await self._fetch_real_position(client, sym)

        # ── 3. Determinar estado y actuar ───────────────────
        bot_thinks_open = runner.executor.position_open
        exchange_has_position = real_position is not None

        if not exchange_has_position and not bot_thinks_open:
            # ✅ Sin posición en ningún lado — cancelar TPs huérfanos
            if limit_orders:
                await self._cancel_orders(client, sym, limit_orders)
                self._total_orphans_cancelled += len(limit_orders)
                logger.warning(
                    f"🧹 {sym}: {len(limit_orders)} TP(s) huérfano(s) "
                    f"cancelados (sin posición activa)"
                )
                self._log_event("RECONCILIATION", "WARNING",
                                f"{len(limit_orders)} TPs huérfanos "
                                f"cancelados en {sym}")

        elif not exchange_has_position and bot_thinks_open:
            # ⚠️ Bot cree que hay posición pero no existe — phantom
            logger.warning(
                f"⚠️ {sym}: Flag phantom detectado — bot indica posición "
                f"pero exchange no tiene"
            )
            runner.executor.position_open = False
            runner.executor.position_type = None
            runner.executor.tp1_hit = False
            runner.executor.trailing_activated = False
            self._total_desyncs_fixed += 1
            self._log_event("RECONCILIATION", "WARNING",
                            f"Flag phantom corregido en {sym}")
            if self.notifier:
                self.notifier.send(
                    "🔧 Desync corregido",
                    f"{sym}: flag phantom → posición limpiada",
                    severity="WARNING",
                )
            # Cancelar TPs que pudieran quedar
            if limit_orders:
                await self._cancel_orders(client, sym, limit_orders)
                self._total_orphans_cancelled += len(limit_orders)

        elif exchange_has_position and not bot_thinks_open:
            # ⚠️ Exchange tiene posición pero bot no la registró — recovery
            # exchange_has_position ya garantiza real_position is not None
            assert real_position is not None
            logger.warning(
                f"⚠️ {sym}: Posición no detectada en exchange "
                f"({real_position['type']} x{real_position['size']})"
            )
            self._sync_position_from_exchange(runner, real_position)
            self._total_desyncs_fixed += 1
            self._log_event("RECONCILIATION", "WARNING",
                            f"Posición recuperada en {sym}: "
                            f"{real_position['type']} x{real_position['size']}")
            if self.notifier:
                self.notifier.send(
                    "🔧 Posición recuperada",
                    f"{sym}: {real_position['type']} x{real_position['size']} "
                    f"@ {real_position['entry']:.4f}",
                    severity="WARNING",
                )

        else:
            # ✅ Posición consistente — verificar TPs
            if not limit_orders:
                # Posición abierta pero sin TPs — intentar recolocar
                if (runner.executor.tp1_price or runner.executor.tp2_price):
                    logger.warning(
                        f"⚠️ {sym}: Posición abierta sin TPs en exchange — "
                        f"recolocando..."
                    )
                    # Delegar a exchange_recovery para recolocar
                    recovery = ExchangeStateRecovery(database=self.database)
                    await recovery.recover_tp_orders([runner])
            elif len(limit_orders) > 2:
                # Más TPs de los esperados — cancelar extras
                logger.warning(
                    f"⚠️ {sym}: {len(limit_orders)} TPs encontrados "
                    f"(esperados ≤2), limpiando..."
                )
                await self._cancel_orders(client, sym, limit_orders)
                self._total_orphans_cancelled += len(limit_orders)
                # Recolocar desde cero
                recovery = ExchangeStateRecovery(database=self.database)
                await recovery.recover_tp_orders([runner])

    async def _fetch_open_orders(self, client: Any, symbol: str) -> list[dict[str, Any]]:
        """Obtiene todas las órdenes abiertas de un símbolo (async)."""
        try:
            orders = await client.get_open_orders(symbol)
            return list(orders) if orders else []
        except Exception as e:
            logger.debug(f"No se pudieron obtener órdenes de {symbol}: {e}")
            return []

    async def _fetch_real_position(self, client: Any, symbol: str) -> Optional[dict[str, Any]]:
        """
        Obtiene la posición real desde el exchange (async).
        Retorna dict con keys 'type', 'size', 'entry', 'sl' o None.
        """
        try:
            positions = await client.get_positions(symbol)
            if not positions:
                return None
            p = positions[0]
            size = float(p.get("size", 0))
            if size <= 0:
                return None
            return {
                "type": p.get("side", "LONG"),   # Binance usa "LONG"/"SHORT"
                "size": size,
                "entry": float(p.get("avgPrice", 0)),
                "sl": float(p.get("stopLoss", 0)) if p.get("stopLoss") else None,
            }
        except Exception as e:
            logger.debug(f"No se pudo obtener posición de {symbol}: {e}")
            return None

    def _sync_position_from_exchange(self, runner: Any,
                                      position: dict[str, Any]) -> None:
        """Sincroniza el estado del bot con una posición real del exchange."""
        runner.executor.position_open = True
        runner.executor.position_type = position["type"]
        runner.executor.entry_price = position["entry"]
        runner.executor.quantity = position["size"]
        if position["sl"]:
            runner.executor.current_sl = position["sl"]

    async def _cancel_orders(self, client: Any, symbol: str,
                              orders: list[dict[str, Any]]) -> None:
        """Cancela una lista de órdenes en el exchange (async)."""
        for order in orders:
            try:
                await client.cancel_order(symbol, str(order["orderId"]))
            except Exception as e:
                logger.debug(
                    f"No se pudo cancelar orden {str(order.get('orderId','?'))[:8]}: {e}"
                )

    def _log_event(self, event_type: str, severity: str,
                    message: str) -> None:
        """Guarda evento en la base de datos si está disponible."""
        if self.database:
            try:
                self.database.save_event(event_type, severity, message[:200])
            except Exception:
                pass
