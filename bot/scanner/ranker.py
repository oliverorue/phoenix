"""
Ranking de señales — de "muchas tarjetas" a "las mejores N".

Combina en un score [0, 1] la confianza del motor, la cantidad de motivos
alineados (confluencia), la liquidez del símbolo (percentil de volumen 24h
dentro del universo rastreado) y la frescura de la señal (decae exponencial).
Los pesos son configurables en ``settings.yaml → scanner``.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional

from bot.scanner.config import ScannerConfig
from bot.scanner.models import Signal


@dataclass
class RankedSignal:
    """Señal con su score compuesto y el desglose por componente."""

    signal: Signal
    score: float
    breakdown: dict[str, float]


class SignalRanker:
    """Ordena señales por score compuesto (mejor primero)."""

    def __init__(self, config: ScannerConfig) -> None:
        self._cfg = config

    def rank(
        self,
        signals: list[Signal],
        now: Optional[float] = None,
        volumes: Optional[dict[str, float]] = None,
    ) -> list[RankedSignal]:
        now = now if now is not None else time.time()
        volumes = volumes or {}
        percentiles = self._percentiles(volumes)

        ranked: list[RankedSignal] = []
        for s in signals:
            reasons = min(1.0, len(s.reasons) / 4.0)
            liquidity = percentiles.get(s.symbol, 0.0)
            age = max(0.0, now - s.ts)
            freshness = math.exp(-age / self._cfg.freshness_halflife_seconds)
            score = (
                self._cfg.w_confidence * s.confidence
                + self._cfg.w_reasons * reasons
                + self._cfg.w_liquidity * liquidity
                + self._cfg.w_freshness * freshness
            )
            ranked.append(RankedSignal(
                signal=s,
                score=round(score, 4),
                breakdown={
                    "confianza": round(s.confidence, 3),
                    "motivos": round(reasons, 3),
                    "liquidez": round(liquidity, 3),
                    "frescura": round(freshness, 3),
                },
            ))

        ranked.sort(key=lambda r: r.score, reverse=True)
        return ranked

    @staticmethod
    def _percentiles(volumes: dict[str, float]) -> dict[str, float]:
        """Percentil de volumen 24h de cada símbolo dentro del universo."""
        if not volumes:
            return {}
        vals = sorted(volumes.values())
        n = len(vals)
        result: dict[str, float] = {}
        for symbol, vol in volumes.items():
            below = sum(1 for x in vals if x <= vol)
            result[symbol] = below / n
        return result
