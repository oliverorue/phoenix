import pytest
import tempfile
import os
import csv
from unittest.mock import MagicMock
from bot.core.trade_journal import TradeJournal


class TestTradeJournal:
    """Tests básicos: init, open, update, close, CSV persistence."""

    @pytest.fixture
    def journal(self):
        """TradeJournal con archivo temporal real."""
        tmp_dir = tempfile.mkdtemp()
        journal_path = os.path.join(tmp_dir, 'trade_journal.csv')
        journal = TradeJournal(journal_file=journal_path)
        yield journal
        # Limpiar
        if os.path.exists(journal_path):
            os.unlink(journal_path)
        os.rmdir(tmp_dir)

    def test_init_creates_file(self, journal):
        assert os.path.exists(journal.filepath)

    def test_open(self, journal):
        journal.open(symbol='XRPUSDT', side='LONG', entry_price=0.50,
                     qty=100.0, sl=0.495, tp1=0.51, tp2=0.52)
        assert journal._open_trade['side'] == 'LONG'
        assert journal._open_trade['entry_price'] == 0.50

    def test_update_runup_long(self, journal):
        journal.open(symbol='XRPUSDT', side='LONG', entry_price=0.50,
                     qty=100.0, sl=0.49, tp1=0.52, tp2=0.53)
        journal.update_runup_drawdown(0.51)  # +2%
        assert journal._open_trade['max_runup_pct'] == pytest.approx(2.0, abs=0.01)

    def test_update_drawdown_short(self, journal):
        journal.open(symbol='XRPUSDT', side='SHORT', entry_price=0.50,
                     qty=80.0, sl=0.51, tp1=0.48, tp2=0.47)
        journal.update_runup_drawdown(0.49)  # +2% runup para SHORT
        assert journal._open_trade['max_runup_pct'] == pytest.approx(2.0, abs=0.01)

    def test_update_max_drawdown_short(self, journal):
        """SHORT: precio sube → drawdown aumenta."""
        journal.open(symbol='XRPUSDT', side='SHORT', entry_price=0.50,
                     qty=80.0, sl=0.51, tp1=0.48, tp2=0.47)
        journal.update_runup_drawdown(0.505)  # +1% drawdown
        assert journal._open_trade['max_drawdown_pct'] == pytest.approx(1.0, abs=0.01)

    def test_close_writes_csv(self, journal):
        journal.open(symbol='XRPUSDT', side='LONG', entry_price=0.50,
                     qty=100.0, sl=0.49, tp1=0.51, tp2=0.53)
        journal.close(exit_price=0.51, close_reason='tp1', sl_final=0.49)
        assert not journal._open_trade
        with open(journal.filepath, 'r') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 1
        assert rows[0]['side'] == 'LONG'

    def test_close_without_open(self, journal):
        journal.close(exit_price=0.50, close_reason='manual')
        # No debe fallar

    def test_has_active_trade(self, journal):
        assert not journal._open_trade
        journal.open(symbol='XRPUSDT', side='LONG', entry_price=0.50,
                     qty=100.0, sl=0.49, tp1=0.51, tp2=0.53)
        assert journal._open_trade != {}


class TestTradeJournalMultipleTrades:
    """Verifica que múltiples trades se acumulen correctamente en CSV."""

    @pytest.fixture
    def journal(self):
        tmp_dir = tempfile.mkdtemp()
        journal_path = os.path.join(tmp_dir, 'trade_journal.csv')
        j = TradeJournal(journal_file=journal_path)
        yield j
        if os.path.exists(journal_path):
            os.unlink(journal_path)
        os.rmdir(tmp_dir)

    def test_two_trades_appended(self, journal):
        journal.open('XRPUSDT', 'LONG', 0.50, 100, 0.49, 0.51, 0.53)
        journal.close(0.51, 'tp1', 0.49)

        journal.open('ETHUSDT', 'SHORT', 2000, 0.5, 2050, 1950, 1900)
        journal.close(1980, 'tp1', 2050)

        with open(journal.filepath, 'r') as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 2
        assert rows[0]['symbol'] == 'XRPUSDT'
        assert rows[1]['symbol'] == 'ETHUSDT'

    def test_csv_columns_present(self, journal):
        journal.open('XRPUSDT', 'LONG', 0.50, 100, 0.49, 0.51, 0.53)
        journal.close(0.51, 'tp1', 0.49)

        with open(journal.filepath, 'r') as f:
            reader = csv.DictReader(f)
            row = next(reader)
        assert 'pnl_usdt' in row
        assert 'pnl_pct' in row
        assert 'close_reason' in row
        assert row['close_reason'] == 'tp1'

    def test_close_with_sl_tracks_pnl(self, journal):
        """Cierre por SL con PnL negativo debe registrar pérdida."""
        journal.open('XRPUSDT', 'LONG', 0.50, 100, 0.48, 0.52, 0.54)
        journal.close(0.48, 'sl', 0.48)

        with open(journal.filepath, 'r') as f:
            row = next(csv.DictReader(f))
        assert float(row['pnl_usdt']) < 0
        assert row['close_reason'] == 'sl'


class TestTradeJournalWithDatabase:
    """Verifica que TradeJournal guarde en database cuando se pasa database=."""

    @pytest.fixture
    def journal_with_db(self):
        tmp_dir = tempfile.mkdtemp()
        journal_path = os.path.join(tmp_dir, 'trade_journal.csv')
        mock_db = MagicMock()
        mock_db.save_trade = MagicMock(return_value=True)
        j = TradeJournal(journal_file=journal_path, database=mock_db)
        yield j, mock_db
        if os.path.exists(journal_path):
            os.unlink(journal_path)
        os.rmdir(tmp_dir)

    def test_close_calls_database_save_trade(self, journal_with_db):
        j, mock_db = journal_with_db
        j.open('XRPUSDT', 'LONG', 0.50, 100, 0.49, 0.51, 0.53)
        j.close(0.51, 'tp1', 0.49)
        mock_db.save_trade.assert_called_once()

    def test_close_without_db_does_not_crash(self, journal_with_db):
        j, _ = journal_with_db
        j._database = None
        j.open('XRPUSDT', 'LONG', 0.50, 100, 0.49, 0.51, 0.53)
        j.close(0.51, 'tp1', 0.49)  # no debe lanzar


class TestTradeJournalCSVWrites:
    """Verifica que close() escribe correctamente en CSV."""

    def test_close_writes_one_row(self, tmp_path):
        """close debe escribir una fila adicional en el CSV."""
        subdir = tmp_path / "csv_writes"
        subdir.mkdir()
        journal_path = subdir / "trade_journal.csv"
        j = TradeJournal(journal_file=str(journal_path))
        j.open('XRPUSDT', 'LONG', 0.50, 100, 0.49, 0.51, 0.53)
        j.close(0.51, 'tp1', 0.49)
        with open(journal_path, 'r') as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]['close_reason'] == 'tp1'

    def test_init_creates_file_in_subdirectory(self, tmp_path):
        """init crea el archivo CSV en un subdirectorio existente."""
        subdir = tmp_path / "subdir"
        subdir.mkdir()
        journal_path = subdir / "trade_journal.csv"
        j = TradeJournal(journal_file=str(journal_path))
        assert os.path.exists(journal_path)
        j.open('XRPUSDT', 'LONG', 0.50, 100, 0.49, 0.51, 0.53)
        assert j._open_trade['symbol'] == 'XRPUSDT'

class TestTradeJournalPartialPnL:
    """El journal debe sumar parciales y descontar comisiones."""

    def test_partial_tp1_tp2_pnl_net(self, tmp_path):
        journal_path = tmp_path / "partial.csv"
        j = TradeJournal(journal_file=str(journal_path))
        j.open(
            symbol="XRPUSDT", side="LONG", entry_price=1.0,
            qty=100.0, sl=0.95, tp1=1.05, tp2=1.10,
        )

        # Simular TP1 parcial (65) y comisiones acumuladas.
        j._open_trade["qty"] = 35.0
        j._open_trade["realized_pnl_gross"] = (1.05 - 1.0) * 65
        j._open_trade["commission_usdt"] = (
            100 * 1.0 * 0.0005 + 65 * 1.05 * 0.0001 + 35 * 1.10 * 0.0001
        )
        j._open_trade["tp1_hit"] = True
        j._open_trade["tp2_hit"] = True

        j.close(exit_price=1.10, close_reason="tp2", sl_final=1.04)

        with open(journal_path, "r") as f:
            row = next(csv.DictReader(f))

        expected = (
            (1.05 - 1.0) * 65
            + (1.10 - 1.0) * 35
            - (100 * 1.0 * 0.0005 + 65 * 1.05 * 0.0001 + 35 * 1.10 * 0.0001)
        )
        assert float(row["qty"]) == pytest.approx(100.0)
        assert float(row["pnl_usdt"]) == pytest.approx(expected, abs=1e-3)
        assert row["tp1_hit"] == "YES"
        assert row["tp2_hit"] == "YES"
