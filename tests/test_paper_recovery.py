"""Tests de restauración de paper trading tras reinicio."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from bot.main import TradingBot


def make_bot():
    ex = MagicMock()
    ex.paper_balance = 50.0
    ex.total_trades = 0
    ex.wins = 0
    ex.losses = 0
    ex.total_pnl = 0.0
    ex.total_commission = 0.0
    ex._entry_qty = 0.0
    ex._tp1_qty = 0.0
    ex._tp2_qty = 0.0
    ex._realized_net = 0.0
    ex._realized_gross = 0.0
    ex.position_open = False
    ex.position_type = None
    ex.entry_price = 0.0
    ex.quantity = 0.0
    ex.current_sl = 0.0
    ex.tp1_price = 0.0
    ex.tp2_price = 0.0
    ex.tp1_hit = False
    ex.trailing_activated = False
    runner = SimpleNamespace(symbol="XRPUSDT", executor=ex, balance=50.0)
    bot = TradingBot.__new__(TradingBot)
    bot.runners = [runner]
    bot.balance = 50.0
    bot.journal = MagicMock()
    bot.journal._open_trade = {}
    return bot, runner, ex


def test_restores_paper_balance_and_position():
    bot, runner, ex = make_bot()
    saved = {
        "XRPUSDT": {
            "position_open": True,
            "position_type": "LONG",
            "entry_price": 0.5,
            "quantity": 35.0,
            "current_sl": 0.51,
            "tp1_price": 0.52,
            "tp2_price": 0.54,
            "tp1_hit": True,
            "trailing_activated": True,
            "paper_balance": 57.25,
            "entry_qty": 100.0,
            "tp1_qty": 65.0,
            "tp2_qty": 35.0,
            "realized_net": 3.0,
            "realized_gross": 3.2,
            "total_trades": 3,
            "wins": 2,
            "losses": 1,
            "total_pnl": 7.25,
            "total_commission": 0.2,
        },
        "_open_trade": {
            "trade_id": "LONG_1",
            "symbol": "XRPUSDT",
            "side": "LONG",
        },
    }
    with patch(
        "bot.core.state_recovery.StateRecovery.load_all_symbols",
        return_value=saved,
    ):
        bot._restore_paper_state()

    assert ex.paper_balance == 57.25
    assert runner.balance == 57.25
    assert ex.position_open is True
    assert ex.quantity == 35.0
    assert ex.tp1_hit is True
    assert ex.total_trades == 3
    assert ex._entry_qty == 100.0
    assert bot.balance == 57.25
    assert bot.journal._open_trade["trade_id"] == "LONG_1"


def test_no_saved_state_keeps_defaults():
    bot, runner, ex = make_bot()
    with patch(
        "bot.core.state_recovery.StateRecovery.load_all_symbols",
        return_value={},
    ):
        bot._restore_paper_state()

    assert ex.paper_balance == 50.0
    assert ex.position_open is False
    assert runner.balance == 50.0
