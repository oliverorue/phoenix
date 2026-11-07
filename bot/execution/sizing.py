"""Cálculo puro de niveles de entrada/SL/TP y cantidad para un trade.

Desacopla la matemática del sizing de la UI y del broker para poder testearla
sin red. La estructura sigue el playbook: SL a ``sl_distance_pct``, TP1 a 1R
(cierra ``tp1_ratio``), TP2 a 2R, y trailing opcional a ``trailing_dist_pct``.
"""

from __future__ import annotations

from dataclasses import dataclass

from bot.execution.models import Direction


@dataclass
class TradePlan:
    symbol: str
    direction: Direction
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    qty: float
    risk_usdt: float
    tp1_ratio: float
    trailing_dist: float


def plan_trade(
    symbol: str,
    direction: Direction,
    price: float,
    risk_usdt: float,
    sl_distance_pct: float = 0.015,
    tp1_ratio: float = 0.6,
    trailing_dist_pct: float = 0.0,
) -> TradePlan:
    """Devuelve el plan (niveles + cantidad) a partir del riesgo en USDT.

    ``qty = risk_usdt / R`` con ``R = price * sl_distance_pct`` (distancia al
    SL). Así, tocar el SL pierde ``risk_usdt``, el TP1 (1R) gana
    ``risk_usdt * tp1_ratio`` y el TP2 (2R) gana ``2 * risk_usdt`` sobre el
    remanente.
    """
    if price <= 0:
        raise ValueError("price debe ser positivo")
    if risk_usdt <= 0:
        raise ValueError("risk_usdt debe ser positivo")
    if sl_distance_pct <= 0:
        raise ValueError("sl_distance_pct debe ser positivo")

    r = price * sl_distance_pct
    is_long = direction == Direction.LONG

    sl_price = price - r if is_long else price + r
    tp1_price = price + r if is_long else price - r
    tp2_price = price + 2 * r if is_long else price - 2 * r
    qty = risk_usdt / r
    trailing_dist = price * trailing_dist_pct

    return TradePlan(
        symbol=symbol,
        direction=direction,
        entry_price=price,
        sl_price=sl_price,
        tp1_price=tp1_price,
        tp2_price=tp2_price,
        qty=qty,
        risk_usdt=risk_usdt,
        tp1_ratio=tp1_ratio,
        trailing_dist=trailing_dist,
    )
