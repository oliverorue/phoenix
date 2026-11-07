"""
Gestión activa de posiciones abiertas (breakeven, trailing stop, cierre por reversión).
"""

from typing import Any, Callable, Optional
import inspect
import pandas as pd
from config.loader import get_phase_config
from bot.core.indicators import Indicators  # reutilizamos patrones de velas
from loguru import logger


# ── Breakeven offset para cubrir comisiones ──
# El precio de entrada no es el precio real debido a comisiones taker (0.04% Binance)
# + slippage. Mover SL exactamente a entry_price causa micro-pérdidas cuando el
# precio roza entry y rebota. Este offset (0.08%) asegura que incluso tras
# comisiones de entrada+salida el trade cierra en ~breakeven real.
BREAKEVEN_OFFSET_PCT = 0.0008  # 0.08%

# Buffer para no cerrar por una micro-cruz de la MA20.
REVERSAL_MA_BUFFER_PCT = 0.001


class PositionManager:
    """Monitorea una posición abierta y decide si mover SL, cerrar, etc."""

    def __init__(
        self,
        executor: Any,
        data_feed: Any,
        balance_getter: Optional[Callable[[], float]] = None,
    ) -> None:
        self.executor = executor
        self.data = data_feed
        self.indicators = Indicators()
        self._balance_getter = balance_getter
        self._breakeven_done = False
        self._trail_extreme: Optional[float] = None

    def _breakeven_price(self) -> float:
        """
        Retorna el precio de breakeven ajustado por comisiones.
        Para LONG: entry * (1 + offset) → cubre comisión taker entrada + salida
        Para SHORT: entry * (1 - offset)
        """
        entry = self.executor.entry_price
        if self.executor.position_type == "LONG":
            return entry * (1 + BREAKEVEN_OFFSET_PCT)
        else:
            return entry * (1 - BREAKEVEN_OFFSET_PCT)

    def _reset_position_state(self) -> None:
        self._breakeven_done = False
        self._trail_extreme = None

    async def update(self, current_price: float, df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> Optional[str]:
        """
        Evalúa la posición actual y retorna una acción (async):
        - "breakeven": movió SL por encima/debajo de entrada (cubre comisiones)
        - "trailing": actualizó SL con trailing stop
        - "close_reversal": cerró por señal de reversión
        - "close_external": el exchange cerró la posición (SL/TP/manual)
        - None: no se realizó ningún cambio
        """
        if not self.executor.position_open:
            self._reset_position_state()
            return None

        # Sincronizar fills reales (TP1 parcial, SL/TP ejecutados en exchange).
        sync = getattr(self.executor, "sync_position_from_exchange", None)
        if callable(sync):
            result = sync()
            if inspect.isawaitable(result):
                result = await result
            if result and result.get("closed"):
                self._reset_position_state()
                return "close_external"
            if not self.executor.position_open:
                self._reset_position_state()
                return None

        # Obtener indicadores para detectar patrones
        ind_15m = self.indicators.calc_15m_indicators(df_15m)
        ind_1h = self.indicators.calc_1h_indicators(df_1h)

        # 1. Breakeven: solo se mueve una vez por posición.
        if not self._breakeven_done:
            if self.executor.tp1_hit:
                be_price = self._breakeven_price()
                if self.executor.position_type == "LONG":
                    needs_move = self.executor.current_sl < be_price
                else:
                    needs_move = self.executor.current_sl > be_price
                if needs_move:
                    logger.info(
                        f"🎯 TP1 confirmado, moviendo SL a breakeven real "
                        f"({be_price:.6f})"
                    )
                    self.executor.update_stop_loss(be_price)
                self._breakeven_done = True
            elif await self._should_breakeven_early(current_price):
                be_price = self._breakeven_price()
                logger.info(f"🛡️ Breakeven temprano a {be_price:.6f} (progreso hacia TP1)")
                self.executor.update_stop_loss(be_price)
                self._breakeven_done = True

        # 2. Trailing stop real: solo después de un fill de TP1.
        if self.executor.tp1_hit:
            trail_dist_pct = await self._get_trail_dist_pct()
            if not self.executor.trailing_activated:
                self._trail_extreme = current_price
                self.executor.trailing_activated = True
                logger.info(
                    f"🎯 Trailing stop activado desde {current_price:.6f} "
                    f"(dist {trail_dist_pct*100:.2f}%)"
                )

            if self._trail_extreme is None:
                self._trail_extreme = current_price

            if self.executor.position_type == "LONG":
                self._trail_extreme = max(self._trail_extreme, current_price)
                new_sl = self._trail_extreme * (1 - trail_dist_pct)
                improves = new_sl > self.executor.current_sl
            else:
                self._trail_extreme = min(self._trail_extreme, current_price)
                new_sl = self._trail_extreme * (1 + trail_dist_pct)
                improves = new_sl < self.executor.current_sl

            if improves:
                self.executor.update_stop_loss(new_sl)
                logger.info(f"📈 Trailing SL movido a {new_sl:.6f}")

        # 3. Señales de reversión.
        if self._detect_reversal(ind_15m, ind_1h, current_price):
            logger.info("⚠️ Señal de reversión detectada, cerrando posición")
            if hasattr(self.executor, "mark_close_reason"):
                self.executor.mark_close_reason("close_reversal")
            self.executor.close_position()
            self._reset_position_state()
            return "close_reversal"

        return None

    def _tp1_reached(self, current_price: float) -> bool:
        tp1 = self.executor.tp1_price
        if tp1 is None:
            return False
        if self.executor.position_type == "LONG":
            return bool(current_price >= tp1)
        else:
            return bool(current_price <= tp1)

    async def _should_breakeven_early(self, current_price: float) -> bool:
        """Activa breakeven si el precio recorrió al menos el trigger% hacia TP1 (async)."""
        _, fase_cfg = await self.risk_phase_config()
        trigger = fase_cfg.get("breakeven_trigger", 0.4)
        entry = self.executor.entry_price
        tp1 = self.executor.tp1_price
        if entry is None or tp1 is None or entry == 0 or tp1 == entry:
            return False
        if self.executor.position_type == "LONG":
            progress = (current_price - entry) / (tp1 - entry)
        else:
            progress = (entry - current_price) / (entry - tp1)
        return bool(progress >= trigger)

    def _detect_reversal(self, ind_15m: dict[str, Any], ind_1h: dict[str, Any], current_price: float) -> bool:
        """Detecta patrones de reversión contrarios a la posición."""
        ma20 = ind_1h.get("MA20", 0)

        if self.executor.position_type == "LONG":
            # Reversión bajista: RSI alto + patrón de vela bajista
            if ind_15m.get("RSI", 50) > 65 and Indicators.has_bearish_rejection(ind_15m):
                return True
            # Precio cae claramente bajo la media móvil de 1h.
            if ma20 > 0 and current_price < ma20 * (1 - REVERSAL_MA_BUFFER_PCT):
                return True
        elif self.executor.position_type == "SHORT":
            # Reversión alcista: RSI bajo + patrón alcista
            if ind_15m.get("RSI", 50) < 35 and Indicators.has_bullish_rejection(ind_15m):
                return True
            if ma20 > 0 and current_price > ma20 * (1 + REVERSAL_MA_BUFFER_PCT):
                return True
        return False

    async def risk_phase_config(self) -> tuple[Any, dict[str, Any]]:
        """Helper para obtener fase y configuración actual (async)."""
        if self._balance_getter is not None:
            balance = float(self._balance_getter())
        else:
            getter = getattr(self.executor, "get_balance", None)
            balance = None
            if callable(getter):
                value = getter()
                if inspect.isawaitable(value):
                    value = await value
                if isinstance(value, (int, float)):
                    balance = float(value)
            if balance is None:
                value = self.data.get_balance()
                if inspect.isawaitable(value):
                    value = await value
                balance = float(value)
        assert balance is not None
        return get_phase_config(balance)

    async def _get_trail_dist_pct(self) -> float:
        _, fase_cfg = await self.risk_phase_config()
        return float(fase_cfg.get("trailing_dist", 0.001))
