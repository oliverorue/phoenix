"""
Gestión activa de posiciones abiertas (breakeven, trailing stop, cierre por reversión).
"""

from typing import Any, Optional
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


class PositionManager:
    """Monitorea una posición abierta y decide si mover SL, cerrar, etc."""

    def __init__(self, executor: Any, data_feed: Any) -> None:
        self.executor = executor
        self.data = data_feed
        self.indicators = Indicators()

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

    async def update(self, current_price: float, df_15m: pd.DataFrame, df_1h: pd.DataFrame) -> Optional[str]:
        """
        Evalúa la posición actual y retorna una acción (async):
        - "breakeven": movió SL por encima/debajo de entrada (cubre comisiones)
        - "trailing": actualizó SL con trailing stop
        - "close_reversal": cerró por señal de reversión
        - "close_timeout": (opcional) si se quiere cerrar tras X velas
        - None: no se realizó ningún cambio
        """
        if not self.executor.position_open:
            return None

        # Obtener indicadores para detectar patrones
        ind_15m = self.indicators.calc_15m_indicators(df_15m)
        ind_1h = self.indicators.calc_1h_indicators(df_1h)

        # 1. Verificar si TP1 fue alcanzado para mover SL a breakeven
        if not self.executor.tp1_hit:
            if self._tp1_reached(current_price):
                be_price = self._breakeven_price()
                logger.info(f"🎯 TP1 alcanzado, moviendo SL a breakeven real ({be_price:.6f})")
                self.executor.update_stop_loss(be_price)
                self.executor.tp1_hit = True
                # Registrar en journal si está disponible
                if self.executor.journal and self.executor.journal._open_trade:
                    self.executor.journal._open_trade["tp1_hit"] = True

            elif await self._should_breakeven_early(current_price):
                be_price = self._breakeven_price()
                logger.info(f"🛡️ Breakeven temprano a {be_price:.6f} (progreso hacia TP1)")
                self.executor.update_stop_loss(be_price)
                self.executor.tp1_hit = True
                if self.executor.journal and self.executor.journal._open_trade:
                    self.executor.journal._open_trade["tp1_hit"] = True

        # 2. Trailing stop nativo (solo después de TP1, una sola vez)
        if self.executor.tp1_hit and not self.executor.trailing_activated:
            trail_dist_pct = await self._get_trail_dist_pct()
            trail_distance = current_price * trail_dist_pct
            activation = current_price * (1 + trail_dist_pct) if self.executor.position_type == "LONG" else current_price * (1 - trail_dist_pct)
            self.executor.activate_trailing_stop(trail_distance, activation)
            self.executor.trailing_activated = True

        # 3. Señales de reversión (solo si no estamos ya en breakeven — opcional)
        if self._detect_reversal(ind_15m, ind_1h, current_price):
            logger.info("⚠️ Señal de reversión detectada, cerrando posición")
            self.executor.close_position()
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
        if entry is None or tp1 is None or entry == 0:
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
            # Precio cae bajo media móvil de 1h (ej. MA20)
            if ma20 > 0 and current_price < ma20:
                return True
        elif self.executor.position_type == "SHORT":
            # Reversión alcista: RSI bajo + patrón alcista
            if ind_15m.get("RSI", 50) < 35 and Indicators.has_bullish_rejection(ind_15m):
                return True
            if ma20 > 0 and current_price > ma20:
                return True
        return False

    async def risk_phase_config(self) -> tuple[Any, dict[str, Any]]:
        """Helper para obtener fase y configuración actual (async)."""
        balance = await self.data.get_balance()
        return get_phase_config(balance)

    async def _get_trail_dist_pct(self) -> float:
        _, fase_cfg = await self.risk_phase_config()
        return float(fase_cfg.get("trailing_dist", 0.001))
