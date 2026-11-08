"""Tests del ejecutor live (órdenes + reconciliación) y seguridad."""

from __future__ import annotations

import pytest

import bot.execution.security as sec
from bot.execution.live import LiveTradeExecutor
from bot.execution.manager import TradeManager
from bot.execution.models import Direction, Trade, TradeStatus


def make_trade() -> Trade:
    return Trade(
        id="T1", symbol="XRPUSDT", direction=Direction.LONG,
        entry_price=1.0, sl_price=0.985, tp1_price=1.015, tp2_price=1.03,
        qty=10.0, tp1_ratio=0.6,
    )


class FakeBroker:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.order_status: dict[int, str] = {}
        self.position_size = 10.0
        self._fills: list[dict] = []
        self._oid = 0

    def _next(self) -> int:
        self._oid += 1
        return self._oid

    async def set_margin(self, symbol, leverage=1):
        self.calls.append(("set_margin", symbol, leverage))

    async def open_market(self, symbol, direction, qty):
        self.calls.append(("open_market", symbol, direction, qty))
        return {"orderId": self._next()}

    async def set_sl(self, symbol, direction, stop_price, qty=None):
        oid = self._next()
        self.calls.append(("set_sl", symbol, direction, stop_price, qty))
        self.order_status[oid] = "NEW"
        return {"orderId": oid}

    async def set_tp(self, symbol, direction, stop_price, qty=None):
        oid = self._next()
        self.calls.append(("set_tp", symbol, direction, stop_price, qty))
        self.order_status[oid] = "NEW"
        return {"orderId": oid}

    async def move_sl(self, symbol, direction, stop_price):
        self.calls.append(("move_sl", symbol, direction, stop_price))
        return {"orderId": self._next()}

    async def order(self, symbol, order_id):
        return {"status": self.order_status.get(int(order_id), "NEW")}

    async def position(self, symbol):
        if self.position_size == 0:
            return None
        return {"symbol": symbol, "size": self.position_size, "side": "LONG",
                "avgPrice": 1.0, "unrealizedPnL": 0.0}

    async def fills(self, symbol, order_id=None):
        return self._fills


class TestLiveOpen:
    @pytest.mark.asyncio
    async def test_places_entry_sl_and_both_tps(self):
        broker = FakeBroker()
        executor = LiveTradeExecutor(broker)
        state = await executor.open(make_trade())

        kinds = [c[0] for c in broker.calls]
        assert kinds == ["set_margin", "open_market", "set_sl", "set_tp", "set_tp"]
        assert state.sl_order_id is not None
        assert state.tp1_qty == pytest.approx(6.0)
        assert state.tp2_qty == pytest.approx(4.0)


class TestLiveReconcile:
    @pytest.mark.asyncio
    async def test_moves_sl_to_breakeven_on_tp1_fill(self):
        broker = FakeBroker()
        executor = LiveTradeExecutor(broker)
        state = await executor.open(make_trade())
        broker.order_status[state.tp1_order_id] = "FILLED"
        broker.position_size = 4.0

        closed = await executor.reconcile(state)

        assert not closed
        assert state.breakeven_moved
        assert any(
            c[0] == "move_sl" and c[3] == pytest.approx(1.0)
            for c in broker.calls
        )

    @pytest.mark.asyncio
    async def test_closes_when_position_gone(self):
        broker = FakeBroker()
        executor = LiveTradeExecutor(broker)
        state = await executor.open(make_trade())
        state.opened_ms = 0
        broker.position_size = 0.0
        broker._fills = [
            {"time": 500, "price": 1.03, "realized_pnl": 1.5,
             "order_id": 1, "qty": 4.0, "commission": 0.0, "side": "SELL"},
        ]

        closed = await executor.reconcile(state)

        assert closed
        assert state.closed
        assert state.realized_pnl == pytest.approx(1.5)
        assert state.exit_price == pytest.approx(1.03)


class TestCloseWithPnl:
    def test_sets_pnl_and_reason(self):
        mgr = TradeManager()
        trade = mgr.open_trade("XRPUSDT", "LONG", 1.0, 0.985, 1.015, 1.03, 85.0)
        mgr.close_with_pnl(trade.id, 1.5, 1.03, "LIVE_CLOSE")

        assert trade.status == TradeStatus.CLOSED
        assert trade.realized_pnl == pytest.approx(1.5)
        assert trade.exit_reason == "LIVE_CLOSE"
        assert mgr.open_trades() == []


class TestSecurity:
    def test_raises_without_keys(self, monkeypatch):
        monkeypatch.setattr(sec, "API_KEY", "")
        monkeypatch.setattr(sec, "API_SECRET", "")
        with pytest.raises(sec.LiveConfigError):
            sec.enforce_live_config()

    def test_ok_with_keys(self, monkeypatch):
        monkeypatch.setattr(sec, "API_KEY", "key")
        monkeypatch.setattr(sec, "API_SECRET", "secret")
        sec.enforce_live_config()  # no lanza
