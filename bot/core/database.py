"""
Gestión de base de datos SQLite para historial de trades, eventos y balance.
"""

import sqlite3
import json
from pathlib import Path
from typing import Any
from loguru import logger
from bot.core.tracing import trace

DB_DIR = Path("data")
DB_DIR.mkdir(exist_ok=True)
DB_PATH = DB_DIR / "bot_history.db"


class DatabaseManager:
    """Maneja la persistencia en SQLite."""

    def __init__(self, db_path: str | None = None) -> None:
        self.db_path = db_path or str(DB_PATH)
        self.conn = sqlite3.connect(self.db_path)
        self._create_tables()

    def _create_tables(self) -> None:
        cursor = self.conn.cursor()
        # Trades
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT UNIQUE,
                symbol TEXT,
                side TEXT,
                entry_price REAL,
                entry_time TEXT,
                exit_price REAL,
                exit_time TEXT,
                quantity REAL,
                pnl_usdt REAL,
                pnl_pct REAL,
                sl_initial REAL,
                sl_final REAL,
                tp1 REAL,
                tp2 REAL,
                close_reason TEXT,
                duration_seconds INTEGER,
                rsi_entry REAL,
                adx_entry REAL,
                atr_entry REAL,
                created_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        # Eventos
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                severity TEXT,
                message TEXT,
                metadata TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        ''')
        # Balance history
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS balance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                balance REAL,
                symbol TEXT,
                timestamp TEXT DEFAULT (datetime('now'))
            )
        ''')
        self.conn.commit()
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN ma_spread_entry REAL")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN tp1_hit INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN tp2_hit INTEGER")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN commission_usdt REAL")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN max_runup_pct REAL")
        except sqlite3.OperationalError:
            pass
        try:
            cursor.execute("ALTER TABLE trades ADD COLUMN max_drawdown_pct REAL")
        except sqlite3.OperationalError:
            pass

    @trace(attributes={"component": "DatabaseManager"})
    def save_trade(self, trade_data: dict[str, Any]) -> bool:
        """Inserta una operación completada."""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO trades (
                    trade_id, symbol, side, entry_price, entry_time,
                    exit_price, exit_time, quantity, pnl_usdt, pnl_pct,
                    sl_initial, sl_final, tp1, tp2, close_reason, duration_seconds,
                    rsi_entry, adx_entry, atr_entry,
                    max_runup_pct, max_drawdown_pct, ma_spread_entry, 
                    tp1_hit, tp2_hit, commission_usdt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?)
            ''', (
                trade_data.get("trade_id", ""),
                trade_data.get("symbol", ""),
                trade_data.get("side", ""),
                trade_data.get("entry_price", 0),
                trade_data.get("entry_time", ""),
                trade_data.get("exit_price", 0),
                trade_data.get("exit_time", ""),
                trade_data.get("qty", 0),
                trade_data.get("pnl_usdt", 0),
                trade_data.get("pnl_pct", 0),
                trade_data.get("sl_initial", 0),
                trade_data.get("sl_final", 0),
                trade_data.get("tp1", 0),
                trade_data.get("tp2", 0),
                trade_data.get("close_reason", ""),
                trade_data.get("duration_seconds", 0),
                trade_data.get("rsi_entry", 0),
                trade_data.get("adx_entry", 0),
                trade_data.get("atr_entry", 0),
                trade_data.get("max_runup_pct", 0),
                trade_data.get("max_drawdown_pct", 0),
                trade_data.get("ma_spread_entry", 0),
                trade_data.get("tp1_hit", 0),
                trade_data.get("tp2_hit", 0),
                trade_data.get("commission_usdt", 0)
            ))
            self.conn.commit()
            return True
        except Exception as e:
            logger.error(f"Error guardando trade en DB: {e}")
            return False

    @trace(attributes={"component": "DatabaseManager"})
    def save_event(self, event_type: str, severity: str, message: str, metadata: dict[str, Any] | None = None) -> None:
        """Registra un evento."""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO events (event_type, severity, message, metadata)
                VALUES (?, ?, ?, ?)
            ''', (event_type, severity, message, json.dumps(metadata or {})))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error guardando evento en DB: {e}")

    def log_balance(self, balance: float, symbol: str = "") -> None:
        """Guarda balance en el historial."""
        try:
            cursor = self.conn.cursor()
            cursor.execute('''
                INSERT INTO balance_history (balance, symbol)
                VALUES (?, ?)
            ''', (balance, symbol))
            self.conn.commit()
        except Exception as e:
            logger.error(f"Error guardando balance en DB: {e}")

    def get_trades(self, limit: int = 100, symbol: str | None = None) -> list[dict[str, Any]]:
        """Recupera últimos trades."""
        cursor = self.conn.cursor()
        query = "SELECT * FROM trades WHERE 1=1"
        params: list[Any] = []
        if symbol:
            query += " AND symbol = ?"
            params.append(symbol)
        query += " ORDER BY entry_time DESC LIMIT ?"
        params.append(limit)
        cursor.execute(query, params)
        rows = cursor.fetchall()
        cols = [desc[0] for desc in cursor.description]
        return [dict(zip(cols, row)) for row in rows]

    @trace(attributes={"component": "DatabaseManager"})
    def get_stats(self, days: int = 7) -> dict[str, Any]:
        """Métricas de los últimos N días."""
        cursor = self.conn.cursor()
        cursor.execute(f'''
            SELECT COUNT(*) as total_trades,
                   SUM(CASE WHEN pnl_usdt > 0 THEN 1 ELSE 0 END) as wins,
                   SUM(pnl_usdt) as total_pnl,
                   AVG(pnl_usdt) as avg_pnl,
                   MAX(pnl_usdt) as best_trade,
                   MIN(pnl_usdt) as worst_trade
            FROM trades
            WHERE entry_time >= datetime('now', '-{days} days')
        ''')
        row = cursor.fetchone()
        return {
            "total_trades": row[0] or 0,
            "wins": row[1] or 0,
            "total_pnl": row[2] or 0.0,
            "avg_pnl": row[3] or 0.0,
            "best_trade": row[4] or 0.0,
            "worst_trade": row[5] or 0.0,
        }

    def close(self) -> None:
        if self.conn:
            self.conn.close()