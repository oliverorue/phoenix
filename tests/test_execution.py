"""Tests de la capa de ejecución (state machine + TradeManager dry-run)."""

from __future__ import annotations

import pytest

from bot.execution.manager import TradeManager
from bot.execution.models import Direction, Trade, TradeStatus
from bot.execution.state_machine import advance, direction_sign


def make_long() -> Trade:
    return Trade(
        id="T1", symbol="XRPUSDT", direction=Direction.LONG,
        entry_price=100.0, sl_price=99.0, tp1_price=101.0, tp2_price=102.0,
        qty=10.0, tp1_ratio=0.6, opened_at=1000.0,
    )


def make_short() -> Trade:
    return Trade(
        id="T2", symbol="XRPUSDT", direction=Direction.SHORT,
        entry_price=100.0, sl_price=101.0, tp1_price=99.0, tp2_price=98.0,
        qty=10.0, tp1_ratio=0.6, opened_at=1000.0,
    )


class TestDirectionSign:
    def test_long_positive_short_negative(self):
        assert direction_sign(Direction.LONG) == 1.0
        assert direction_sign(Direction.SHORT) == -1.0


class TestLongCycle:
    def test_sl_hit_closes_full_loss(self):
        trade = make_long()
        actions = advance(trade, 98.5, ts=1001.0)
        assert trade.status == TradeStatus.CLOSED
        assert trade.exit_reason == "SL"
        assert trade.realized_pnl == pytest.approx(-10.0)
        assert actions[0].kind == "FULL_CLOSE"

    def test_tp1_partial_then_tp2_win(self):
        trade = make_long()
        actions = advance(trade, 101.5, ts=1001.0)
        assert trade.status == TradeStatus.PARTIAL
        assert trade.sl_price == pytest.approx(100.0)  # breakeven
        assert trade.remaining_qty == pytest.approx(4.0)
        assert trade.realized_pnl == pytest.approx(6.0)
        assert any(a.kind == "MOVE_SL" for a in actions)

        actions = advance(trade, 102.5, ts=1002.0)
        assert trade.status == TradeStatus.CLOSED
        assert trade.exit_reason == "TP2"
        assert trade.realized_pnl == pytest.approx(14.0)

    def test_trailing_sl_ratchets_up_and_closes(self):
        trade = make_long()
        trade.trailing_dist = 0.5
        advance(trade, 101.5, ts=1001.0)  # TP1 -> breakeven en 100
        actions = advance(trade, 101.8, ts=1002.0)  # trail a 101.3
        assert any(a.kind == "TRAIL" for a in actions)
        assert trade.sl_price == pytest.approx(101.3)

        advance(trade, 101.2, ts=1003.0)  # cae -> trailing SL
        assert trade.status == TradeStatus.CLOSED
        assert trade.exit_reason == "TRAILING_SL"
        # 6 en TP1 (+6) + 4 en 101.3 (+5.2) = +11.2
        assert trade.realized_pnl == pytest.approx(11.2)


class TestShortCycle:
    def test_tp1_then_tp2_win(self):
        trade = make_short()
        advance(trade, 99.0, ts=1001.0)
        assert trade.status == TradeStatus.PARTIAL
        assert trade.sl_price == pytest.approx(100.0)
        advance(trade, 98.0, ts=1002.0)
        assert trade.status == TradeStatus.CLOSED
        assert trade.realized_pnl == pytest.approx(14.0)

    def test_sl_hit_loss(self):
        trade = make_short()
        advance(trade, 101.5, ts=1001.0)
        assert trade.status == TradeStatus.CLOSED
        assert trade.realized_pnl == pytest.approx(-10.0)


class TestIdempotent:
    def test_closed_trade_returns_no_actions(self):
        trade = make_long()
        advance(trade, 98.0, ts=1001.0)
        assert advance(trade, 97.0, ts=1002.0) == []


class TestManager:
    def test_full_cycle_and_stats(self):
        mgr = TradeManager()
        trade = mgr.open_trade(
            "XRPUSDT", "LONG", 100.0, 99.0, 101.0, 102.0, 10.0,
            tp1_ratio=0.6, ts=1000.0,
        )
        fills = mgr.on_price("XRPUSDT", 101.5, ts=1001.0)
        assert trade.status == TradeStatus.PARTIAL
        assert len(fills) == 1
        assert fills[0].side == "TP1"

        fills = mgr.on_price("XRPUSDT", 102.5, ts=1002.0)
        assert trade.status == TradeStatus.CLOSED
        assert mgr.open_trades() == []
        assert mgr.closed_trades() == [trade]

        stats = mgr.stats()
        assert stats["total"] == 1
        assert stats["wins"] == 1
        assert stats["losses"] == 0
        assert stats["win_rate"] == pytest.approx(1.0)
        assert stats["pnl"] == pytest.approx(14.0)

    def test_loss_counted_in_stats(self):
        mgr = TradeManager()
        mgr.open_trade("XRPUSDT", "LONG", 100.0, 99.0, 101.0, 102.0, 10.0, ts=0.0)
        mgr.on_price("XRPUSDT", 98.0, ts=1.0)
        stats = mgr.stats()
        assert stats["wins"] == 0
        assert stats["losses"] == 1
        assert stats["win_rate"] == pytest.approx(0.0)

    def test_close_manual(self):
        mgr = TradeManager()
        trade = mgr.open_trade("XRPUSDT", "LONG", 100.0, 99.0, 101.0, 102.0, 10.0, ts=0.0)
        mgr.close_manual(trade.id, 100.5, ts=2000.0)
        assert trade.status == TradeStatus.CLOSED
        assert trade.exit_reason == "MANUAL"
        assert trade.realized_pnl == pytest.approx(5.0)
        assert mgr.open_trades() == []

    def test_close_manual_unknown_id_returns_none(self):
        mgr = TradeManager()
        assert mgr.close_manual("noexiste", 1.0) is None


class TestUnrealized:
    def test_long_unrealized_pnl_and_last_price(self):
        mgr = TradeManager()
        trade = mgr.open_trade("XRPUSDT", "LONG", 100.0, 99.0, 101.0, 102.0, 10.0, ts=0.0)
        mgr.on_price("XRPUSDT", 100.5, ts=1.0)
        assert mgr.last_prices()["XRPUSDT"] == pytest.approx(100.5)
        assert mgr.unrealized_pnl(trade) == pytest.approx(5.0)

    def test_short_unrealized_pnl(self):
        mgr = TradeManager()
        trade = mgr.open_trade("XRPUSDT", "SHORT", 100.0, 101.0, 99.0, 98.0, 10.0, ts=0.0)
        mgr.on_price("XRPUSDT", 99.5, ts=1.0)
        assert mgr.unrealized_pnl(trade) == pytest.approx(5.0)


class TestProfitFactor:
    def test_profit_factor_with_losses(self):
        mgr = TradeManager()
        win = mgr.open_trade("A", "LONG", 100.0, 99.0, 101.0, 102.0, 10.0, ts=0.0)
        mgr.close_manual(win.id, 101.0)  # +10
        loss = mgr.open_trade("B", "LONG", 100.0, 99.0, 101.0, 102.0, 10.0, ts=0.0)
        mgr.close_manual(loss.id, 99.0)  # -10
        assert mgr.stats()["profit_factor"] == pytest.approx(1.0)

    def test_profit_factor_no_losses_is_inf(self):
        mgr = TradeManager()
        win = mgr.open_trade("A", "LONG", 100.0, 99.0, 101.0, 102.0, 10.0, ts=0.0)
        mgr.close_manual(win.id, 101.0)
        assert mgr.stats()["profit_factor"] == float("inf")


class TestTimeout:
    def test_timeout_closes_at_market(self):
        trade = make_long()  # entry 100, opened_at 1000
        actions = advance(trade, 100.2, ts=1000.0 + 7200, max_hold_seconds=7200)
        assert trade.status == TradeStatus.CLOSED
        assert trade.exit_reason == "TIMEOUT"
        assert actions[-1].kind == "FULL_CLOSE"
        assert actions[-1].reason == "TIMEOUT"
        assert trade.realized_pnl == pytest.approx(2.0)  # (100.2-100)*10

    def test_sl_takes_priority_over_timeout(self):
        trade = make_long()
        advance(trade, 98.0, ts=1000.0 + 8000, max_hold_seconds=7200)
        assert trade.exit_reason == "SL"

    def test_no_timeout_if_disabled(self):
        trade = make_long()
        advance(trade, 100.2, ts=1000.0 + 99999, max_hold_seconds=0)
        assert trade.status == TradeStatus.OPEN

    def test_manager_timeout_closes_trade(self):
        mgr = TradeManager(max_hold_seconds=7200)
        trade = mgr.open_trade("XRPUSDT", "LONG", 100.0, 99.0, 101.0, 102.0, 10.0, ts=1000.0)
        mgr.on_price("XRPUSDT", 100.2, ts=1000.0 + 7200)
        assert trade.status == TradeStatus.CLOSED
        assert trade.exit_reason == "TIMEOUT"
        assert mgr.open_trades() == []


class TestFees:
    def test_entry_and_exit_fees_applied(self):
        mgr = TradeManager(fee_pct=0.0005)
        trade = mgr.open_trade("X", "LONG", 100.0, 99.0, 101.0, 102.0, 10.0, ts=0.0)
        # comisión de entrada = 10*100*0.0005 = 0.5
        assert trade.realized_pnl == pytest.approx(-0.5)
        mgr.close_manual(trade.id, 101.0, ts=1.0)
        # bruto = (101-100)*10 = 10; salida = 10*101*0.0005 = 0.505
        assert trade.realized_pnl == pytest.approx(-0.5 + 10.0 - 0.505)

    def test_sl_applies_fees(self):
        mgr = TradeManager(fee_pct=0.0005)
        trade = mgr.open_trade("X", "LONG", 100.0, 99.0, 101.0, 102.0, 10.0, ts=0.0)
        mgr.on_price("X", 98.0, ts=1.0)
        # entrada -0.5; SL bruto -10; salida 10*99*0.0005 = 0.495
        assert trade.realized_pnl == pytest.approx(-0.5 - 10.0 - 0.495)

    def test_no_fees_when_zero(self):
        mgr = TradeManager(fee_pct=0.0)
        trade = mgr.open_trade("X", "LONG", 100.0, 99.0, 101.0, 102.0, 10.0, ts=0.0)
        assert trade.realized_pnl == 0.0
