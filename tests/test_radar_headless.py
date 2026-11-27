"""Tests del motor headless: snapshot, comandos y lock compartido."""

from __future__ import annotations

import time

import pytest

from bot import radar_state
from bot.dashboard.scanner_bridge import ScannerBridge
from bot.execution.manager import TradeManager
from bot.execution.models import Direction
from bot.execution.store import TradeStore
from bot.radar_runner import handle_command
from bot.scanner.config import ScannerConfig
from bot.scanner.models import Signal
from bot.scanner.risk_engine import RiskEngine


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(radar_state, "DATA_DIR", tmp_path)
    monkeypatch.setattr(radar_state, "STATE_PATH", tmp_path / "radar_state.json")
    monkeypatch.setattr(radar_state, "LOCK_PATH", tmp_path / "radar.lock")
    monkeypatch.setattr(radar_state, "CMD_DIR", tmp_path / "radar_cmds")


def make_view_bridge(tmp_path) -> ScannerBridge:
    bridge = ScannerBridge.__new__(ScannerBridge)
    bridge._config = ScannerConfig()
    bridge._dry_run = True
    bridge._view_mode = True
    bridge._base_risk_pct = 0.01
    bridge._max_dd_daily = 0.03
    bridge._max_stops_day = 2
    bridge._day_key = ""
    bridge._day_start_equity = bridge._config.paper_initial_capital
    bridge._day_halted = False
    bridge._day_halt_reason = ""
    bridge.trade_store = TradeStore(tmp_path / "trades.db")
    bridge.trades = TradeManager(store=bridge.trade_store)
    bridge.risk = RiskEngine(base_risk_pct=0.01)
    return bridge


def ranked_state(symbol: str = "XRPUSDT", score: float = 0.9) -> dict:
    signal = Signal(
        symbol=symbol, direction="LONG", confidence=0.7, price=1.0,
        ts=1000.0, reasons=["a"],
    )
    return {
        "ts": time.time(),
        "running": True,
        "dry_run": True,
        "auto_trade": True,
        "halted": False,
        "halt_reason": "",
        "equity": 15.0,
        "stats": {},
        "scanner_stats": {"universe": 20, "tracked": 20},
        "tracked": [symbol],
        "last_prices": {symbol: 1.01},
        "signals": [
            {"signal": signal.model_dump(), "score": score, "breakdown": {"motivos": 1.0}},
        ],
    }


class TestRadarState:
    def test_state_roundtrip_and_heartbeat(self):
        radar_state.write_state({"ts": time.time(), "auto_trade": True})

        assert radar_state.read_state()["auto_trade"] is True
        assert radar_state.engine_alive() is True

        radar_state.write_state({"ts": time.time() - 60})
        assert radar_state.engine_alive() is False

    def test_lock_is_exclusive(self):
        first = radar_state.try_acquire_lock()
        assert first is not None
        assert radar_state.try_acquire_lock() is None

        radar_state.release_lock(first)
        second = radar_state.try_acquire_lock()
        assert second is not None
        radar_state.release_lock(second)


class TestViewBridge:
    def test_snapshot_reads_engine_state(self, tmp_path):
        radar_state.write_state(ranked_state())
        bridge = make_view_bridge(tmp_path)

        signals, stats, tracked = bridge.snapshot()
        assert [s.symbol for s in signals] == ["XRPUSDT"]
        assert stats.universe == 20
        assert tracked == ["XRPUSDT"]

    def test_top_signals_reads_ranked_state(self, tmp_path):
        radar_state.write_state(ranked_state())
        bridge = make_view_bridge(tmp_path)

        ranked = bridge.top_signals(limit=5)
        assert len(ranked) == 1
        assert ranked[0].signal.symbol == "XRPUSDT"
        assert ranked[0].score == pytest.approx(0.9)
        assert ranked[0].breakdown == {"motivos": 1.0}

    def test_open_positions_use_state_prices(self, tmp_path):
        bridge = make_view_bridge(tmp_path)
        bridge.trades.open_trade(
            symbol="XRPUSDT", direction=Direction.LONG, entry_price=1.0,
            sl_price=0.985, tp1_price=1.015, tp2_price=1.03, qty=10.0,
        )
        radar_state.write_state({"last_prices": {"XRPUSDT": 1.01}})

        positions = bridge.paper_open_positions()
        assert len(positions) == 1
        _trade, upnl, price = positions[0]
        assert price == pytest.approx(1.01)
        assert upnl == pytest.approx(0.1)

    def test_execute_enqueues_open_command(self, tmp_path):
        bridge = make_view_bridge(tmp_path)
        signal = Signal(symbol="XRPUSDT", direction="LONG", confidence=0.7, price=1.0)

        assert bridge.execute(signal) is None
        commands = radar_state.drain_commands()
        assert commands[0][1]["cmd"] == "open"
        assert commands[0][1]["signal"]["symbol"] == "XRPUSDT"

    def test_kill_switch_enqueues_command(self, tmp_path):
        bridge = make_view_bridge(tmp_path)

        assert bridge.kill_switch() == -1
        assert radar_state.drain_commands()[0][1]["cmd"] == "kill"

    def test_auto_toggle_enqueues_command(self, tmp_path):
        bridge = make_view_bridge(tmp_path)
        bridge.set_auto_trade(False)

        assert radar_state.drain_commands()[0][1]["cmd"] == "auto_off"

    def test_is_running_uses_heartbeat(self, tmp_path):
        bridge = make_view_bridge(tmp_path)
        assert bridge.is_running() is False

        radar_state.write_state({"ts": time.time()})
        assert bridge.is_running() is True


class FakeBridge:
    def __init__(self):
        self.calls: list[tuple] = []

    def kill_switch(self):
        self.calls.append(("kill",))
        return 1

    def set_auto_trade(self, enabled):
        self.calls.append(("auto", enabled))

    def notify_status(self):
        self.calls.append(("notify",))

    def execute(self, signal):
        self.calls.append(("open", signal.symbol))
        return None


class TestCommandHandling:
    def test_dispatches_all_commands(self):
        bridge = FakeBridge()
        handle_command(bridge, {"cmd": "kill"})
        handle_command(bridge, {"cmd": "auto_off"})
        handle_command(bridge, {"cmd": "notify"})
        handle_command(bridge, {
            "cmd": "open",
            "signal": {"symbol": "XRPUSDT", "direction": "LONG", "confidence": 0.5},
        })

        assert bridge.calls == [
            ("kill",),
            ("auto", False),
            ("notify",),
            ("open", "XRPUSDT"),
        ]
