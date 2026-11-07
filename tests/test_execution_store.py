"""Tests de persistencia de trades paper y alimentación del Kelly."""

from __future__ import annotations

import pytest

from bot.execution.manager import TradeManager
from bot.execution.models import Direction, Fill, Trade, TradeStatus
from bot.execution.store import TradeStore
from bot.scanner.models import Signal
from bot.scanner.risk_engine import RiskEngine


class TestTradeStore:
    def test_roundtrip_open_then_closed(self, tmp_path):
        store = TradeStore(db_path=tmp_path / "paper.db")
        trade = Trade(
            id="T1", symbol="XRPUSDT", direction=Direction.LONG,
            entry_price=1.0, sl_price=0.985, tp1_price=1.015, tp2_price=1.03,
            qty=56.0, opened_at=1000.0,
        )
        trade.fills.append(Fill("T1", "ENTRY", 56.0, 1.0, 1000.0))
        store.upsert(trade)

        opened = store.load_open()
        assert len(opened) == 1
        assert opened[0].id == "T1"
        assert opened[0].symbol == "XRPUSDT"
        assert opened[0].fills[0].side == "ENTRY"

        trade.status = TradeStatus.CLOSED
        trade.exit_price = 1.03
        trade.exit_reason = "TP2"
        trade.closed_at = 2000.0
        trade.remaining_qty = 0.0
        trade.realized_pnl = 12.0
        trade.fills.append(Fill("T1", "TP2", 40.0, 1.03, 2000.0))
        store.upsert(trade)

        assert store.load_open() == []
        closed = store.load_closed()
        assert len(closed) == 1
        assert closed[0].realized_pnl == pytest.approx(12.0)
        assert store.load_pnl() == [pytest.approx(12.0)]


class TestManagerPersistence:
    def test_open_and_close_persist_and_feed_callback(self, tmp_path):
        store = TradeStore(db_path=tmp_path / "paper.db")
        closed_pnls: list[float] = []
        mgr = TradeManager(store=store, on_close=lambda t: closed_pnls.append(t.realized_pnl))

        trade = mgr.open_trade(
            "XRPUSDT", "LONG", 1.0, 0.985, 1.015, 1.03, 56.0, ts=1000.0,
        )
        assert len(store.load_open()) == 1

        mgr.on_price("XRPUSDT", 1.015, ts=1500.0)  # TP1 parcial
        assert trade.status == TradeStatus.PARTIAL

        mgr.on_price("XRPUSDT", 1.03, ts=2000.0)  # TP2
        assert trade.status == TradeStatus.CLOSED

        # (1.015-1)*33.6 + (1.03-1)*22.4 = 0.504 + 0.672 = 1.176
        assert trade.realized_pnl == pytest.approx(1.176)
        assert closed_pnls == [pytest.approx(1.176)]
        assert store.load_open() == []
        assert store.load_closed()[0].realized_pnl == pytest.approx(1.176)

    def test_manager_recovers_state_and_counter(self, tmp_path):
        store = TradeStore(db_path=tmp_path / "paper.db")
        mgr1 = TradeManager(store=store)
        t_open = mgr1.open_trade("XRPUSDT", "LONG", 1.0, 0.985, 1.015, 1.03, 56.0, ts=1000.0)
        t_closed = mgr1.open_trade("BTCUSDT", "SHORT", 70000.0, 71000.0, 69000.0, 68000.0, 0.001, ts=1001.0)
        mgr1.close_manual(t_closed.id, 70000.0, ts=1002.0)

        mgr2 = TradeManager(store=store)
        mgr2.load(store)

        assert [t.id for t in mgr2.open_trades()] == [t_open.id]
        assert [t.id for t in mgr2.closed_trades()] == [t_closed.id]

        t3 = mgr2.open_trade("ETHUSDT", "LONG", 1.0, 0.9, 1.1, 1.2, 1.0, ts=2000.0)
        assert t3.id not in (t_open.id, t_closed.id)


class TestRiskEngineThreadsafe:
    def test_record_and_suggest_still_work(self):
        risk = RiskEngine(base_risk_pct=0.01)
        risk.record_trade(10.0)
        risk.record_trade(-5.0)
        risk.load_history([2.0, 3.0, -1.0])

        sig = Signal(symbol="XRPUSDT", direction="LONG", confidence=0.7, price=1.0)
        sizing = risk.suggest(100.0, sig)
        assert sizing.risk_usdt > 0
