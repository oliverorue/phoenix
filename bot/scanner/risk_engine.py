"""
Gestión de riesgo integrada (Kelly) para el monitor.

Reutiliza ``bot.core.kelly_sizing.KellySizing`` para ajustar el riesgo por el
historial del trader y lo combina con la confianza de cada señal. El resultado
es el monto en USDT a arriesgar por operación, no el tamaño del contrato.
"""

from __future__ import annotations

import threading
from typing import Optional

from bot.core.kelly_sizing import KellySizing
from bot.scanner.models import Signal, Sizing


class RiskEngine:
    """Convierte (capital, confianza, historial) en USDT a arriesgar."""

    def __init__(
        self,
        base_risk_pct: float = 0.01,
        kelly: Optional[KellySizing] = None,
        min_confidence_scale: float = 0.5,
    ) -> None:
        if base_risk_pct <= 0 or base_risk_pct > 0.25:
            raise ValueError("base_risk_pct debe estar en (0, 0.25]")
        self.base_risk_pct = base_risk_pct
        self.kelly = kelly or KellySizing()
        self.min_confidence_scale = min_confidence_scale
        self._lock = threading.RLock()

    def suggest(self, capital: float, signal: Signal,
                base_risk_pct: Optional[float] = None,
                max_risk_pct: Optional[float] = None) -> Sizing:
        """Devuelve cuántos USDT arriesgar en esta señal.

        ``risk_pct = base_risk_pct × confianza_escalada × kelly_mult`` con
        ``confianza_escalada = min_confidence_scale + (1 - min_confidence_scale)
        × confidence``. El multiplicador de Kelly arranca en 1.0 y se ajusta
        según el historial registrado con :meth:`record_trade`.

        ``base_risk_pct`` permite sobreescribir el riesgo base desde la UI sin
        recrear el motor (preservando el historial de Kelly). ``max_risk_pct``
        aplica un techo duro (ej. 10%) por encima de lo que diga Kelly.
        """
        if capital <= 0:
            return Sizing(symbol=signal.symbol, note="Capital inválido (0 o negativo)")

        if base_risk_pct is not None:
            if base_risk_pct <= 0 or base_risk_pct > 0.25:
                raise ValueError("base_risk_pct debe estar en (0, 0.25]")
            eff_base = base_risk_pct
        else:
            eff_base = self.base_risk_pct

        with self._lock:
            kelly_mult = self.kelly.get_multiplier()
        conf_scale = self.min_confidence_scale + (
            1.0 - self.min_confidence_scale
        ) * signal.confidence
        risk_pct = eff_base * conf_scale * kelly_mult
        if max_risk_pct is not None:
            risk_pct = min(risk_pct, max_risk_pct)
        risk_usdt = capital * risk_pct

        return Sizing(
            symbol=signal.symbol,
            risk_usdt=round(risk_usdt, 2),
            risk_pct=round(risk_pct * 100, 4),
            kelly_multiplier=round(kelly_mult, 3),
            note="Arriesgá este monto en USDT (distancia al SL).",
        )

    def record_trade(self, pnl_usdt: float) -> None:
        with self._lock:
            self.kelly.record_trade(pnl_usdt)

    def multiplier(self) -> float:
        """Multiplicador actual de Kelly (thread-safe)."""
        with self._lock:
            return self.kelly.get_multiplier()

    def load_history(self, pnl_usdts: list[float]) -> None:
        """Reconstruye el estado de Kelly a partir de resultados históricos."""
        with self._lock:
            for pnl in pnl_usdts:
                self.kelly.record_trade(pnl)
