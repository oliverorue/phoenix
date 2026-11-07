"""
Persistencia de señales y resultados del radar (SQLite).

Guarda cada señal emitida y el resultado que el operador registra luego
(WIN / LOSS / SKIP + PnL). El historial de PnL alimenta el Kelly del
``RiskEngine`` para que el tamaño recomendado aprenda de los resultados
reales, y la UI muestra el win-rate / PnL acumulado.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Optional

from bot.scanner.models import Signal

_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "scanner_signals.db"


class SignalStore:
    """Almacén thread-safe de señales y sus resultados."""

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = db_path or _DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._create_table()

    def _create_table(self) -> None:
        with self._lock:
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    symbol TEXT NOT NULL,
                    direction TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    price REAL NOT NULL,
                    reasons TEXT NOT NULL,
                    outcome TEXT,
                    pnl_usdt REAL
                )
                """
            )
            self._conn.commit()

    # ── Escritura ───────────────────────────────────────

    def save(self, signal: Signal) -> int:
        """Persiste una señal. Se usa como callback ``on_signal`` del scanner."""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO signals (ts, symbol, direction, confidence, price, reasons) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    signal.ts, signal.symbol, signal.direction,
                    signal.confidence, signal.price, json.dumps(signal.reasons),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def set_outcome(self, signal_id: int, outcome: str, pnl_usdt: float) -> None:
        """Registra el resultado de una señal ya emitida."""
        with self._lock:
            self._conn.execute(
                "UPDATE signals SET outcome = ?, pnl_usdt = ? WHERE id = ?",
                (outcome, pnl_usdt, signal_id),
            )
            self._conn.commit()

    # ── Lectura ─────────────────────────────────────────

    def recent(self, limit: int = 30) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, ts, symbol, direction, confidence, price, reasons, outcome, pnl_usdt "
                "FROM signals ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row(r) for r in rows]

    def load_pnl(self) -> list[float]:
        """PnL de resultados registrados (WIN/LOSS), para reconstruir el Kelly."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT pnl_usdt FROM signals "
                "WHERE outcome IN ('WIN', 'LOSS') AND pnl_usdt IS NOT NULL "
                "ORDER BY id ASC",
            ).fetchall()
        return [float(r[0]) for r in rows]

    def stats(self) -> dict:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN outcome = 'WIN' THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN outcome = 'LOSS' THEN 1 ELSE 0 END), "
                "COALESCE(SUM(pnl_usdt), 0) "
                "FROM signals WHERE outcome IN ('WIN', 'LOSS')",
            ).fetchone()
        total, wins, losses, pnl = row
        return {
            "total": total or 0,
            "wins": wins or 0,
            "losses": losses or 0,
            "win_rate": ((wins or 0) / total) if total else 0.0,
            "pnl": pnl or 0.0,
        }

    @staticmethod
    def _row(r: tuple) -> dict:
        return {
            "id": r[0], "ts": r[1], "symbol": r[2], "direction": r[3],
            "confidence": r[4], "price": r[5],
            "reasons": json.loads(r[6] or "[]"),
            "outcome": r[7], "pnl_usdt": r[8],
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
