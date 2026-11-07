"""
Motor de anomalías en tiempo real — la "ventaja matemática".

Detecta señales que no son simples cruces de medias:

- **Order Book Imbalance (OBI)** a partir del depth parcial (`@depth20`):
  (suma de bids − suma de asks) / (suma total). Extremo positivo → presión
  compradora (bias LONG); extremo negativo → presión vendedora (bias SHORT).
- **Volume Delta** desde `@aggTrade`: diferencia entre volumen taker-buy y
  taker-sell en una ventana móvil, más un z-score de volumen total frente a
  su propia historia reciente (picos anómalos).
- **Funding Rate extremo** (REST `futures_mark_price`): funding muy positivo →
  mercado sobreapalancado LONG (bias de reversión SHORT) y viceversa.
- **Liquidaciones en cascada** (`!forceOrder@arr`): ráfaga de órdenes de
  liquidación por encima de un nocional mínimo en una ventana corta.

Todo el estado por símbolo está acotado con ``deque(maxlen=...)`` y expone
contadores de diagnóstico para saber qué condición se está disparando (o no).
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from bot.scanner.config import ScannerConfig
from bot.scanner.models import Direction, Signal


@dataclass
class _SymbolState:
    """Estado móvil acotado de un único símbolo."""

    trades: deque = field(default_factory=lambda: deque(maxlen=2000))  # (ts, qty, is_buy)
    window_totals: deque = field(default_factory=lambda: deque(maxlen=120))  # volumen por ventana
    obi: float = 0.0
    funding: float = 0.0
    liquidations: deque = field(default_factory=lambda: deque(maxlen=200))  # (ts, side, notional)
    last_price: float = 0.0


class AnomalyEngine:
    """Agrega ticks de mercado en señales direccionales, con estado acotado."""

    def __init__(self, config: ScannerConfig) -> None:
        self._cfg = config
        self._states: dict[str, _SymbolState] = {}
        self._diag = {
            "evals": 0, "agg": 0, "depth": 0, "funding": 0, "force": 0,
            "obi_hits": 0, "delta_hits": 0, "spike_hits": 0,
            "funding_hits": 0, "cascade_hits": 0,
        }

    # ── Estado acotado ──────────────────────────────────

    def _state(self, symbol: str) -> _SymbolState:
        state = self._states.get(symbol)
        if state is None:
            state = _SymbolState()
            self._states[symbol] = state
        return state

    # ── Ingresos de datos ───────────────────────────────

    def on_agg_trade(self, symbol: str, ts: float, price: float,
                     qty: float, is_buyer_maker: bool) -> None:
        state = self._state(symbol)
        state.trades.append((ts, qty, not is_buyer_maker))  # is_buy = taker compra
        state.last_price = price
        self._diag["agg"] += 1

    def on_depth(self, symbol: str, bids: list[tuple[float, float]],
                 asks: list[tuple[float, float]]) -> None:
        state = self._state(symbol)
        bid_sum = sum(q for _p, q in bids) if bids else 0.0
        ask_sum = sum(q for _p, q in asks) if asks else 0.0
        total = bid_sum + ask_sum
        state.obi = (bid_sum - ask_sum) / total if total > 0 else 0.0
        self._diag["depth"] += 1

    def on_price(self, symbol: str, price: float) -> None:
        """Setea el último precio desde miniTicker o el mid del book."""
        if price > 0:
            self._state(symbol).last_price = price

    def on_funding(self, symbol: str, rate: float) -> None:
        self._state(symbol).funding = rate
        self._diag["funding"] += 1

    def on_force_order(self, symbol: str, ts: float, side: str,
                       notional: float) -> None:
        self._state(symbol).liquidations.append((ts, side, notional))
        self._diag["force"] += 1

    # ── Detección ───────────────────────────────────────

    def _volume_delta(self, state: _SymbolState, now: float) -> tuple[float, float]:
        """(delta normalizado, volumen total) en la ventana móvil (time-based)."""
        window = self._cfg.volume_delta_window_seconds
        while state.trades and now - state.trades[0][0] > window:
            state.trades.popleft()
        buy = sum(q for _ts, q, is_buy in state.trades if is_buy)
        sell = sum(q for _ts, q, is_buy in state.trades if not is_buy)
        total = buy + sell
        delta = (buy - sell) / total if total > 0 else 0.0
        return delta, total

    def _volume_zscore(self, state: _SymbolState, total: float) -> float:
        state.window_totals.append(total)
        if len(state.window_totals) < 10:
            return 0.0
        vals = list(state.window_totals)
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        std = math.sqrt(var)
        return (total - mean) / std if std > 1e-9 else 0.0

    def _liquidation_cascade(self, state: _SymbolState, now: float) -> Optional[Direction]:
        """Devuelve la dirección del impulso si hay cascada de liquidaciones."""
        window = self._cfg.liq_cascade_window_seconds
        recent = [l for l in state.liquidations if now - l[0] <= window]
        if len(recent) < self._cfg.liq_cascade_min_events:
            return None
        notional = sum(n for _ts, _side, n in recent)
        if notional < self._cfg.liq_cascade_min_notional:
            return None
        long_liq = sum(n for _ts, side, n in recent if side == "SELL")
        short_liq = notional - long_liq
        # Liquidaciones LONG (SELL) empujan el precio abajo → SHORT; y viceversa.
        if long_liq >= short_liq * 2:
            return "SHORT"
        if short_liq >= long_liq * 2:
            return "LONG"
        return None

    def evaluate(self, symbol: str, now: Optional[float] = None) -> Optional[Signal]:
        """Combina las señales disponibles en una tarjeta direccional."""
        now = now if now is not None else time.time()
        state = self._state(symbol)
        self._diag["evals"] += 1
        if state.last_price <= 0:
            return None

        reasons: list[str] = []
        score = 0.0
        weight = 0.0

        # 1) Volume delta (direccional) + spike (magnitud)
        delta, total = self._volume_delta(state, now)
        zscore = self._volume_zscore(state, total)
        directional = abs(delta) >= self._cfg.delta_extreme
        spike = zscore >= self._cfg.volume_zscore

        if directional:
            self._diag["delta_hits"] += 1
            w = 0.25
            weight += w
            score += w if delta > 0 else -w
            reasons.append(
                f"{'Compra' if delta > 0 else 'Venta'} agresiva (delta {delta:+.2f})"
            )
        if spike:
            self._diag["spike_hits"] += 1
            w = 0.15
            weight += w
            # El spike solo sesga la dirección si el delta también es extremo.
            if directional:
                score += w if delta > 0 else -w
            reasons.append(f"Pico de volumen (z={zscore:.1f})")

        # 2) Order book imbalance
        obi = state.obi
        if abs(obi) >= self._cfg.obi_extreme:
            self._diag["obi_hits"] += 1
            w = 0.30
            weight += w
            score += w if obi > 0 else -w
            reasons.append(f"OBI {'alcista' if obi > 0 else 'bajista'} ({obi:+.2f})")

        # 3) Funding extremo (reversión)
        if abs(state.funding) >= self._cfg.funding_extreme:
            self._diag["funding_hits"] += 1
            w = 0.15
            weight += w
            if state.funding > 0:
                score += -w
                reasons.append(f"Funding extremo {state.funding * 100:+.3f}% (bias SHORT)")
            else:
                score += w
                reasons.append(f"Funding extremo {state.funding * 100:+.3f}% (bias LONG)")

        # 4) Cascada de liquidaciones
        cascade = self._liquidation_cascade(state, now)
        if cascade is not None:
            self._diag["cascade_hits"] += 1
            w = 0.30
            weight += w
            if cascade == "LONG":
                score += w
                reasons.append("Liquidaciones SHORT en cascada")
            else:
                score += -w
                reasons.append("Liquidaciones LONG en cascada")

        if weight <= 0 or not reasons:
            return None

        norm = score / weight  # en [-1, 1]
        confidence = min(0.95, abs(norm))

        # Piso de calidad: confianza mínima y motivos alineados.
        if confidence < self._cfg.min_confidence or len(reasons) < self._cfg.min_reasons:
            return None

        direction: Direction = "LONG" if norm > 0 else "SHORT"

        return Signal(
            symbol=symbol,
            direction=direction,
            reasons=reasons,
            confidence=round(confidence, 3),
            price=state.last_price,
            ts=now,
            metrics={
                "obi": round(obi, 4),
                "volume_delta": round(delta, 4),
                "volume_zscore": round(zscore, 2),
                "funding": round(state.funding, 6),
                "norm_score": round(norm, 3),
            },
        )

    def diagnostics(self) -> dict[str, int]:
        """Contadores para ver qué condición se dispara (o no)."""
        return dict(self._diag)

    def reset_symbol(self, symbol: str) -> None:
        self._states.pop(symbol, None)
