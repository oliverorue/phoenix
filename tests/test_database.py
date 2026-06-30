import pytest
import tempfile
import os
from bot.core.database import DatabaseManager


class TestDatabaseManager:
    @pytest.fixture
    def db(self):
        tmp_fd, tmp_path = tempfile.mkstemp(suffix='.db')
        os.close(tmp_fd)
        db = DatabaseManager(db_path=tmp_path)
        yield db
        db.conn.close()
        os.unlink(tmp_path)

    def test_init_creates_tables(self, db):
        cursor = db.conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        assert 'trades' in tables
        assert 'events' in tables
        assert 'balance_history' in tables

    def test_get_trades_empty(self, db):
        trades = db.get_trades()
        assert trades == []

    def test_get_trades_with_data(self, db):
        cursor = db.conn.cursor()
        cursor.execute("""
            INSERT INTO trades (trade_id, symbol, side, entry_price, entry_time,
                exit_price, exit_time, quantity, pnl_usdt, pnl_pct)
            VALUES (?,?,?,?,?,?,?,?,?,?)
        """, ('TEST', 'XRPUSDT', 'LONG', 0.5, '2026-01-01T12:00:00',
              0.51, '2026-01-01T13:00:00', 100.0, 1.0, 2.0))
        db.conn.commit()
        trades = db.get_trades(limit=10)
        assert len(trades) == 1
        assert trades[0]['trade_id'] == 'TEST'

    def test_get_trades_with_symbol_filter(self, db):
        cursor = db.conn.cursor()
        cursor.execute("""
            INSERT INTO trades (trade_id, symbol, side, entry_price, entry_time,
                exit_price, exit_time, quantity, pnl_usdt, pnl_pct)
            VALUES ('BTC_TEST','BTCUSDT','LONG',50000,'2026-01-01',51000,'2026-01-01',0.1,100,2.0)
        """)
        db.conn.commit()
        trades = db.get_trades(symbol='XRPUSDT')
        assert len(trades) == 0
        trades = db.get_trades(symbol='BTCUSDT')
        assert len(trades) == 1

    def test_get_stats(self, db):
        cursor = db.conn.cursor()
        for pnl in [2.0, -1.0, 3.0]:
            cursor.execute("""
                INSERT INTO trades (trade_id, symbol, side, entry_price, entry_time,
                    exit_price, exit_time, quantity, pnl_usdt, pnl_pct)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (f'STAT_{pnl}', 'XRPUSDT', 'LONG', 0.5, '2026-01-01T12:00:00',
                  0.51, '2026-01-01T13:00:00', 100.0, pnl, 2.0))
        db.conn.commit()
        stats = db.get_stats(days=365)
        assert stats['total_trades'] == 3
        assert stats['wins'] == 2
        assert stats['total_pnl'] == pytest.approx(4.0, abs=0.01)

    def test_save_trade(self, db):
        """Guardar un trade completo y recuperarlo."""
        trade_data = {
            "trade_id": "SAVE_TEST_001",
            "symbol": "XRPUSDT",
            "side": "LONG",
            "entry_price": 0.6185,
            "entry_time": "2026-04-01T12:00:00",
            "exit_price": 0.6250,
            "exit_time": "2026-04-01T13:00:00",
            "qty": 100.0,
            "pnl_usdt": 0.65,
            "pnl_pct": 1.05,
            "sl_initial": 0.6150,
            "sl_final": 0.6200,
            "tp1": 0.6220,
            "tp2": 0.6300,
            "close_reason": "TP1",
            "duration_seconds": 3600,
            "rsi_entry": 45.0,
            "adx_entry": 25.0,
            "atr_entry": 0.005,
        }
        result = db.save_trade(trade_data)
        assert result is True
        trades = db.get_trades(limit=10)
        assert len(trades) == 1
        assert trades[0]["trade_id"] == "SAVE_TEST_001"

    def test_save_trade_ignores_duplicate(self, db):
        """INSERT OR IGNORE no debe lanzar error con trade_id duplicado."""
        trade_data = {
            "trade_id": "DUP_TEST",
            "symbol": "XRPUSDT",
            "side": "LONG",
            "entry_price": 0.5,
            "entry_time": "2026-01-01",
            "exit_price": 0.51,
            "exit_time": "2026-01-01",
            "qty": 100,
            "pnl_usdt": 1.0,
            "pnl_pct": 2.0,
        }
        db.save_trade(trade_data)
        result = db.save_trade(trade_data)  # mismo trade_id
        assert result is True  # INSERT OR IGNORE
        trades = db.get_trades()
        assert len(trades) == 1

    def test_save_trade_failure_returns_false(self, db):
        """Si la DB falla, save_trade retorna False."""
        db.conn.close()
        result = db.save_trade({"trade_id": "FAIL", "symbol": "XRPUSDT"})
        assert result is False

    def test_save_event(self, db):
        """Guardar y verificar evento."""
        db.save_event("test_event", "INFO", "Esto es una prueba", {"key": "val"})
        cursor = db.conn.cursor()
        cursor.execute("SELECT * FROM events")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][1] == "test_event"
        assert rows[0][2] == "INFO"

    def test_save_event_without_metadata(self, db):
        """Guardar evento sin metadata no debe fallar."""
        db.save_event("simple_event", "WARNING", "Sin metadata")
        cursor = db.conn.cursor()
        cursor.execute("SELECT * FROM events")
        rows = cursor.fetchall()
        assert len(rows) == 1

    def test_save_event_failure_does_not_raise(self, db):
        """Si la DB falla, save_event no debe lanzar excepción."""
        db.conn.close()
        db.save_event("fail", "ERROR", "should not crash")  # no debe lanzar

    def test_log_balance(self, db):
        """Guardar balance en historial."""
        db.log_balance(100.50, "XRPUSDT")
        cursor = db.conn.cursor()
        cursor.execute("SELECT * FROM balance_history")
        rows = cursor.fetchall()
        assert len(rows) == 1
        assert rows[0][1] == 100.50

    def test_log_balance_without_symbol(self, db):
        """Guardar balance sin símbolo."""
        db.log_balance(200.0)
        cursor = db.conn.cursor()
        cursor.execute("SELECT * FROM balance_history")
        rows = cursor.fetchall()
        assert len(rows) == 1

    def test_log_balance_failure_does_not_raise(self, db):
        """Si la DB falla, log_balance no debe lanzar excepción."""
        db.conn.close()
        db.log_balance(999.0)  # no debe lanzar

    def test_get_trades_respects_limit(self, db):
        """get_trades debe respetar el límite."""
        for i in range(5):
            db.save_trade({
                "trade_id": f"LIMIT_{i}",
                "symbol": "XRPUSDT",
                "side": "LONG",
                "entry_price": 0.5,
                "entry_time": "2026-01-01",
                "exit_price": 0.51,
                "exit_time": "2026-01-01",
                "qty": 100,
                "pnl_usdt": 1.0,
                "pnl_pct": 2.0,
            })
        trades = db.get_trades(limit=3)
        assert len(trades) <= 3

    def test_get_stats_empty(self, db):
        """get_stats sin datos debe retornar ceros."""
        stats = db.get_stats(days=365)
        assert stats["total_trades"] == 0
        assert stats["wins"] == 0
        assert stats["total_pnl"] == 0.0

    def test_get_stats_with_multiple_trades(self, db):
        """get_stats con datos variados."""
        for i, pnl in enumerate([5.0, -2.0, 3.0, -1.0, 4.0]):
            db.save_trade({
                "trade_id": f"STATS_{i}",
                "symbol": "XRPUSDT",
                "side": "LONG",
                "entry_price": 0.5,
                "entry_time": "2026-01-01",
                "exit_price": 0.51,
                "exit_time": "2026-01-01",
                "qty": 100,
                "pnl_usdt": pnl,
                "pnl_pct": 2.0,
            })
        stats = db.get_stats(days=365)
        assert stats["total_trades"] == 5
        assert stats["wins"] == 3
        assert stats["total_pnl"] == pytest.approx(9.0, abs=0.01)
        assert stats["best_trade"] == 5.0
        assert stats["worst_trade"] == -2.0

    def test_close(self, db):
        """close() no debe lanzar excepción."""
        db.close()
        # llamar de nuevo no debe explotar
        db.close()