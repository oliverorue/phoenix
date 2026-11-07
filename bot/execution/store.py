"""Persistencia de trades paper (dry-run/live) en SQLite.

Guarda el estado completo de cada ``Trade`` (niveles, fills, PnL) para que el
ciclo sobreviva reinicios y para alimentar el Kelly con el historial real.
Thread-safe (lock interno).
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from bot.execution.models import Direction, Fill, Trade, TradeStatus

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "paper_trades.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id TEXT PRIMARY KEY,
    symbol TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_price REAL NOT NULL,
    sl_price REAL NOT NULL,
    tp1_price REAL NOT NULL,
    tp2_price REAL NOT NULL,
    qty REAL NOT NULL,
    tp1_ratio REAL NOT NULL,
    trailing_dist REAL NOT NULL,
    opened_at REAL NOT NULL,
    status TEXT NOT NULL,
    remaining_qty REAL NOT NULL,
    realized_pnl REAL NOT NULL,
    exit_price REAL NOT NULL,
    closed_at REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    fills TEXT NOT NULL
)
"""


class TradeStore:
    """Almacén thread-safe de trades paper y sus resultados."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or _DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def upsert(self, trade: Trade) -> None:
        """Inserta o reemplaza el trade (mismo ``id``)."""
        fills_json = json.dumps([f.__dict__ for f in trade.fills])
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO paper_trades "
                "(id, symbol, direction, entry_price, sl_price, tp1_price, tp2_price, "
                " qty, tp1_ratio, trailing_dist, opened_at, status, remaining_qty, "
                " realized_pnl, exit_price, closed_at, exit_reason, fills) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    trade.id, trade.symbol, trade.direction.value, trade.entry_price,
                    trade.sl_price, trade.tp1_price, trade.tp2_price, trade.qty,
                    trade.tp1_ratio, trade.trailing_dist, trade.opened_at,
                    trade.status.value, trade.remaining_qty, trade.realized_pnl,
                    trade.exit_price, trade.closed_at, trade.exit_reason, fills_json,
                ),
            )
            self._conn.commit()

    def load_open(self) -> list[Trade]:
        """Trades que no están cerrados (para recuperar tras reinicio)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM paper_trades WHERE status != 'CLOSED' "
                "ORDER BY opened_at ASC",
            ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def load_closed(self, limit: int = 1000) -> list[Trade]:
        """Trades cerrados, más recientes primero."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM paper_trades WHERE status = 'CLOSED' "
                "ORDER BY closed_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_trade(r) for r in rows]

    def load_pnl(self) -> list[float]:
        """PnL realizado de trades cerrados, en orden cronológico (para Kelly)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT realized_pnl FROM paper_trades WHERE status = 'CLOSED' "
                "ORDER BY closed_at ASC",
            ).fetchall()
        return [float(r[0]) for r in rows]

    @staticmethod
    def _row_to_trade(row: tuple) -> Trade:
        (
            id_, symbol, direction, entry, sl, tp1, tp2, qty, ratio, trail,
            opened, status, remaining, pnl, exit_price, closed_at, reason,
            fills_json,
        ) = row
        fills = [Fill(**f) for f in json.loads(fills_json or "[]")]
        trade = Trade(
            id=id_, symbol=symbol, direction=Direction(direction),
            entry_price=entry, sl_price=sl, tp1_price=tp1, tp2_price=tp2,
            qty=qty, tp1_ratio=ratio, trailing_dist=trail, opened_at=opened,
            status=TradeStatus(status), realized_pnl=pnl, exit_price=exit_price,
            closed_at=closed_at, exit_reason=reason, fills=fills,
        )
        trade.remaining_qty = remaining
        return trade

    def close(self) -> None:
        with self._lock:
            self._conn.close()
