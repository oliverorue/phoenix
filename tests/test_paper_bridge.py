"""Tests del bridge de paper trading (open_paper_trade + hook de precio)."""

from __future__ import annotations

import pytest

from bot.dashboard.scanner_bridge import ScannerBridge
from bot.execution.manager import TradeManager
from bot.execution.models import Direction
from bot.scanner.config import ScannerConfig
from bot.scanner.market_scanner import MarketScanner
from bot.scanner.models import Signal
from bot.scanner.ranker import SignalRanker
from bot.scanner.risk_engine import RiskEngine


def make_bridge() -> ScannerBridge:
    """Bridge sin I/O: solo los atributos que necesita el flujo paper."""
    bridge = ScannerBridge.__new__(ScannerBridge)
    bridge._config = ScannerConfig()
    bridge._dry_run = True
    bridge._live_equity = bridge._config.paper_initial_capital
    bridge._executor = None
    bridge._loop = None
    bridge._live_states = {}
    bridge.risk = RiskEngine(base_risk_pct=0.01)
    bridge.trades = TradeManager()
    return bridge


class TestOpenPaperTrade:
    def test_long_trade_uses_auto_capital_and_kelly(self):
        bridge = make_bridge()
        signal = Signal(symbol="XRPUSDT", direction="LONG", confidence=0.7, price=1.0)
        trade = bridge.open_paper_trade(signal)

        assert trade is not None
        assert trade.symbol == "XRPUSDT"
        assert trade.direction == Direction.LONG
        assert trade.entry_price == pytest.approx(1.0)
        assert trade.sl_price == pytest.approx(0.985)
        assert trade.tp1_price == pytest.approx(1.015)
        assert trade.tp2_price == pytest.approx(1.03)
        # equity 15 · riesgo = 15 × 0.085 ≈ 1.28 (redondeado) → qty = 1.28 / 0.015
        assert trade.qty == pytest.approx(1.28 / 0.015)

    def test_equity_starts_at_15(self):
        assert make_bridge().equity() == pytest.approx(15.0)

    def test_equity_tracks_realized_pnl(self):
        bridge = make_bridge()
        signal = Signal(symbol="XRPUSDT", direction="LONG", confidence=0.7, price=1.0)
        trade = bridge.open_paper_trade(signal)
        bridge.trades.close_manual(trade.id, 1.01)
        assert bridge.equity() > 15.0

    def test_neutral_signal_returns_none(self):
        bridge = make_bridge()
        signal = Signal(symbol="XRPUSDT", direction="NEUTRAL", confidence=0.7, price=1.0)
        assert bridge.open_paper_trade(signal) is None


class TestKillSwitch:
    def test_closes_all_open_trades(self):
        bridge = make_bridge()
        s1 = Signal(symbol="XRPUSDT", direction="LONG", confidence=0.7, price=1.0)
        s2 = Signal(symbol="SOLUSDT", direction="SHORT", confidence=0.7, price=100.0)
        bridge.open_paper_trade(s1)
        bridge.open_paper_trade(s2)
        bridge.trades.on_price("XRPUSDT", 1.01, ts=1.0)
        bridge.trades.on_price("SOLUSDT", 99.0, ts=1.0)

        assert len(bridge.paper_open_trades()) == 2
        n = bridge.kill_switch()

        assert n == 2
        assert bridge.paper_open_trades() == []
        assert len(bridge.paper_closed_trades()) == 2


class TestTopSignalsDedup:
    def test_one_card_per_symbol(self):
        bridge = ScannerBridge.__new__(ScannerBridge)
        bridge._config = ScannerConfig()
        bridge.ranker = SignalRanker(bridge._config)

        class FakeScanner:
            def signals_snapshot(self):
                return [
                    Signal(symbol="XRPUSDT", direction="LONG", confidence=0.9,
                           price=1.0, ts=1000.0, reasons=["a", "b"]),
                    Signal(symbol="XRPUSDT", direction="LONG", confidence=0.8,
                           price=1.0, ts=999.0, reasons=["a", "b"]),
                    Signal(symbol="SOLUSDT", direction="LONG", confidence=0.7,
                           price=100.0, ts=1000.0, reasons=["a", "b"]),
                ]

            def volume_map(self):
                return {"XRPUSDT": 1e8, "SOLUSDT": 5e7}

        bridge._scanner = FakeScanner()
        ranked = bridge.top_signals(now=1000.0, limit=10)
        assert [r.signal.symbol for r in ranked] == ["XRPUSDT", "SOLUSDT"]


class TestScannerPriceHook:
    @pytest.mark.asyncio
    async def test_on_price_callback_invoked_on_depth(self):
        scanner = MarketScanner(ScannerConfig())
        calls: list[tuple[str, float]] = []
        scanner.on_price = lambda sym, price: calls.append((sym, price))
        scanner._tracked = {"XRPUSDT"}

        await scanner._handle_symbol(
            "xrpusdt@depth20",
            {"b": [["1.0", "100"]], "a": [["1.01", "100"]]},
        )

        assert calls
        assert calls[-1][0] == "XRPUSDT"
        assert calls[-1][1] == pytest.approx(1.005)
