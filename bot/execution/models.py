"""Modelos de dominio de la capa de ejecución."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class TradeStatus(str, Enum):
    OPEN = "OPEN"
    PARTIAL = "PARTIAL"
    CLOSED = "CLOSED"


@dataclass
class Fill:
    """Un fill (parcial o total) registrado durante el ciclo del trade."""

    trade_id: str
    side: str  # "ENTRY" | "TP1" | "SL" | "TP2" | "TRAILING_SL" | "MANUAL"
    qty: float
    price: float
    ts: float
    reason: str = ""


@dataclass
class Action:
    """Acción devuelta por la state machine al avanzar con un precio."""

    kind: str  # PARTIAL_CLOSE | FULL_CLOSE | MOVE_SL | TRAIL
    price: float
    qty: float
    reason: str


@dataclass
class Trade:
    """Un trade en curso o cerrado, con su estado de ciclo completo."""

    id: str
    symbol: str
    direction: Direction
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    qty: float
    tp1_ratio: float = 0.6
    trailing_dist: float = 0.0
    opened_at: float = 0.0
    status: TradeStatus = TradeStatus.OPEN
    remaining_qty: float = field(init=False)
    realized_pnl: float = 0.0
    exit_price: float = 0.0
    closed_at: float = 0.0
    exit_reason: str = ""
    fills: list[Fill] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.remaining_qty = self.qty
