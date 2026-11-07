"""TradeManager: orquesta el ciclo de trades sobre la state machine.

En F3 corre en **dry-run**: los fills se simulan al precio del tick y el PnL se
acumula en memoria. Es thread-safe (lock interno) porque lo muta el hilo del
scanner y lo lee el hilo de Streamlit. En F4 se conectará un broker live.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Optional, Union

from bot.execution.models import Direction, Fill, Trade, TradeStatus
from bot.execution.state_machine import advance, direction_sign


class TradeManager:
    """Dueño de los trades abiertos/cerrados y del PnL del ciclo."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._open: dict[str, Trade] = {}
        self._by_symbol: dict[str, list[str]] = {}
        self._closed: deque[Trade] = deque(maxlen=1000)
        self._counter = 0

    def open_trade(
        self,
        symbol: str,
        direction: Union[str, Direction],
        entry_price: float,
        sl_price: float,
        tp1_price: float,
        tp2_price: float,
        qty: float,
        tp1_ratio: float = 0.6,
        trailing_dist: float = 0.0,
        ts: Optional[float] = None,
    ) -> Trade:
        """Abre un trade (dry-run: fill de entrada instantáneo a ``entry_price``)."""
        direction_ = (
            direction if isinstance(direction, Direction) else Direction(direction)
        )
        with self._lock:
            self._counter += 1
            trade = Trade(
                id=f"T{self._counter}",
                symbol=symbol.upper(),
                direction=direction_,
                entry_price=entry_price,
                sl_price=sl_price,
                tp1_price=tp1_price,
                tp2_price=tp2_price,
                qty=qty,
                tp1_ratio=tp1_ratio,
                trailing_dist=trailing_dist,
                opened_at=ts if ts is not None else time.time(),
            )
            trade.fills.append(
                Fill(trade.id, "ENTRY", qty, entry_price, trade.opened_at)
            )
            self._open[trade.id] = trade
            self._by_symbol.setdefault(trade.symbol, []).append(trade.id)
            return trade

    def on_price(
        self, symbol: str, price: float, ts: Optional[float] = None,
    ) -> list[Fill]:
        """Avanza todos los trades abiertos del símbolo con el nuevo precio.

        Devuelve los fills generados (PARTIAL_CLOSE / FULL_CLOSE) en este tick.
        """
        now = ts if ts is not None else time.time()
        sym = symbol.upper()
        fills: list[Fill] = []
        with self._lock:
            for tid in list(self._by_symbol.get(sym, [])):
                trade = self._open.get(tid)
                if trade is None:
                    continue
                for action in advance(trade, price, now):
                    if action.kind in ("PARTIAL_CLOSE", "FULL_CLOSE"):
                        fill = Fill(tid, action.reason, action.qty, action.price, now)
                        trade.fills.append(fill)
                        fills.append(fill)
                if trade.status == TradeStatus.CLOSED:
                    self._finalize(trade)
        return fills

    def close_manual(
        self, trade_id: str, price: float, ts: Optional[float] = None,
    ) -> Optional[Trade]:
        """Cierra manualmente un trade (kill switch o decisión del operador)."""
        now = ts if ts is not None else time.time()
        with self._lock:
            trade = self._open.pop(trade_id, None)
            if trade is None:
                return None
            sign = direction_sign(trade.direction)
            trade.realized_pnl += (
                (price - trade.entry_price) * trade.remaining_qty * sign
            )
            trade.fills.append(
                Fill(trade.id, "MANUAL", trade.remaining_qty, price, now)
            )
            trade.exit_price = price
            trade.exit_reason = "MANUAL"
            trade.closed_at = now
            trade.remaining_qty = 0.0
            trade.status = TradeStatus.CLOSED
            self._finalize(trade)
            return trade

    def _finalize(self, trade: Trade) -> None:
        self._open.pop(trade.id, None)
        lst = self._by_symbol.get(trade.symbol)
        if lst and trade.id in lst:
            lst.remove(trade.id)
        self._closed.append(trade)

    def open_trades(self) -> list[Trade]:
        with self._lock:
            return list(self._open.values())

    def closed_trades(self) -> list[Trade]:
        with self._lock:
            return list(self._closed)

    def get_trade(self, trade_id: str) -> Optional[Trade]:
        with self._lock:
            return self._open.get(trade_id)

    def stats(self) -> dict:
        """Métricas agregadas sobre los trades cerrados."""
        with self._lock:
            closed = list(self._closed)
            wins = sum(1 for t in closed if t.realized_pnl > 0)
            losses = sum(1 for t in closed if t.realized_pnl < 0)
            decided = wins + losses
            return {
                "total": len(closed),
                "wins": wins,
                "losses": losses,
                "win_rate": (wins / decided) if decided else 0.0,
                "pnl": sum(t.realized_pnl for t in closed),
            }
