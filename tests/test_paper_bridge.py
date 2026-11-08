"""Tests del bridge de paper trading (open_paper_trade + hook de precio)."""

from __future__ import annotations

import pytest

from bot.dashboard.scanner_bridge import ScannerBridge
from bot.execution.manager import TradeManager
from bot.execution.models import Direction
from bot.scanner.config import ScannerConfig
from bot.scanner.market_scanner import MarketScanner
from bot.scanner.models import Signal
from bot.scanner.risk_engine import RiskEngine


def make_bridge() -> ScannerBridge:
    """Bridge sin I/O: solo los atributos que necesita open_paper_trade."""
    bridge = ScannerBridge.__new__(ScannerBridge)
    bridge._config = ScannerConfig()
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
