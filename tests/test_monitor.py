"""Tests del monitor de señales (solo lectura, sin ejecución)."""

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from bot.monitor import (
    Card,
    Sizing,
    _ceil_step,
    _floor_step,
    _session_state,
    format_card,
    size_position,
)
from bot.core.strategy_engine import Signal


def test_floor_step_never_rounds_up():
    assert _floor_step(1.239, 0.01) == pytest.approx(1.23)
    assert _floor_step(0.0009, 0.001) == 0.0
    assert _floor_step(5.0, 0.0) == 5.0


def test_ceil_step_rounds_up():
    assert _ceil_step(2.631, 0.1) == pytest.approx(2.7)
    assert _ceil_step(2.0, 0.1) == pytest.approx(2.0)
    assert _ceil_step(0.0009, 0.001) == pytest.approx(0.001)


def test_size_position_uses_risk_when_above_min_notional():
    sizing = size_position(
        entry=2.0,
        sl=1.98,
        capital_usdt=100.0,
        risk_pct=0.01,
        qty_step=0.1,
        min_qty=0.1,
        min_notional=5.0,
        fee_pct=0.0005,
        max_risk_pct_hard=0.02,
    )

    assert sizing.qty == pytest.approx(50.0)
    assert sizing.notional_usdt == pytest.approx(100.0)
    assert sizing.risk_actual_usdt == pytest.approx(1.0)
    assert sizing.risk_actual_pct == pytest.approx(0.01)
    assert sizing.fees_usdt == pytest.approx(0.1)
    assert sizing.executable is True
    assert sizing.risk_ok is True
    assert "dentro del límite" in sizing.note


def test_size_position_flags_min_notional_over_hard_risk():
    sizing = size_position(
        entry=2.0,
        sl=1.9,
        capital_usdt=10.0,
        risk_pct=0.006,
        qty_step=0.1,
        min_qty=0.1,
        min_notional=5.0,
        max_risk_pct_hard=0.02,
    )

    assert sizing.qty == pytest.approx(2.5)
    assert sizing.notional_usdt == pytest.approx(5.0)
    assert sizing.risk_actual_pct == pytest.approx(0.025)
    assert sizing.executable is True
    assert sizing.risk_ok is False
    assert "NO OPERAR" in sizing.note


def test_size_position_rounds_min_notional_up_to_step():
    sizing = size_position(
        entry=1.9,
        sl=1.5,
        capital_usdt=13.7,
        risk_pct=0.006,
        qty_step=0.1,
        min_qty=0.1,
        min_notional=5.0,
    )

    assert sizing.qty == pytest.approx(2.7)
    assert sizing.notional_usdt == pytest.approx(5.13)
    assert sizing.executable is True


def test_size_position_respects_max_notional_cap():
    sizing = size_position(
        entry=2.0,
        sl=1.99,
        capital_usdt=13.7,
        risk_pct=0.006,
        qty_step=0.1,
        min_qty=0.1,
        min_notional=5.0,
        max_notional_usdt=10.96,
    )

    assert sizing.notional_usdt <= 10.96 + 1e-9
    assert sizing.executable is True
    assert "limitado por capital/leverage" in sizing.note


def test_size_position_applies_score_factor():
    base = size_position(
        entry=2.0,
        sl=1.98,
        capital_usdt=100.0,
        risk_pct=0.01,
        qty_step=0.1,
        min_qty=0.1,
        min_notional=5.0,
        score=0.0,
    )
    low = size_position(
        entry=2.0,
        sl=1.98,
        capital_usdt=100.0,
        risk_pct=0.01,
        qty_step=0.1,
        min_qty=0.1,
        min_notional=5.0,
        score=40.0,
    )

    assert low.qty == pytest.approx(base.qty * 0.25)


@pytest.mark.parametrize(
    "entry, sl, capital, risk",
    [
        (0.0, 1.0, 100.0, 0.01),
        (2.0, 0.0, 100.0, 0.01),
        (2.0, 1.9, 0.0, 0.01),
        (2.0, 1.9, 100.0, 0.0),
        (2.0, 2.0, 100.0, 0.01),
    ],
)
def test_size_position_rejects_invalid_inputs(entry, sl, capital, risk):
    with pytest.raises(ValueError):
        size_position(entry, sl, capital, risk, 0.1, 0.1, 5.0)


def test_session_state_respects_excluded_day():
    fixed = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
    excluded = fixed.strftime("%A")
    session_open, day_allowed = _session_state(
        {"dias_excluir": [excluded], "sesion_inicio": 0, "sesion_fin": 24},
        tz_name="UTC",
        now=fixed,
    )

    assert session_open is True
    assert day_allowed is False


def _card(signal: Signal, sizing: Sizing | None = None) -> Card:
    return Card(
        symbol="XRPUSDT",
        now_local=datetime.now(timezone.utc),
        price=2.0,
        signal=signal,
        sizing=sizing,
        fase_num=0,
        session_open=True,
        day_allowed=True,
        cooldown=0,
        adx=25.0,
        di_pos=30.0,
        di_neg=12.0,
        ema50=1.95,
        ema20=1.99,
        rsi=30.0,
        atr_pct=0.008,
        bb_width=0.03,
    )


def test_format_card_without_signal():
    text = format_card(_card(Signal()))

    assert "SIN SEÑAL" in text
    assert "XRPUSDT" in text


def test_format_card_with_signal_and_sizing():
    signal = Signal(
        direction="LONG",
        entry=2.0,
        stop_loss=1.98,
        take_profit1=2.04,
        take_profit2=2.06,
        score=80,
        reason="test",
        atr=0.008,
    )
    sizing = Sizing(
        capital_usdt=100.0,
        risk_usdt=1.0,
        risk_pct=0.01,
        qty=50.0,
        notional_usdt=100.0,
        risk_actual_usdt=1.0,
        risk_actual_pct=0.01,
        fees_usdt=0.1,
        executable=True,
        risk_ok=True,
        note="Riesgo dentro del límite configurado.",
    )

    text = format_card(_card(signal, sizing))

    assert "SEÑAL LONG" in text
    assert "TP1" in text
    assert "Estado: OK" in text


def _ohlcv(periods: int, freq: str, closed_close: float) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=periods, freq=freq, tz="UTC")
    closes = [1.0] * periods
    closes[-2] = closed_close
    closes[-1] = 9.9999
    return pd.DataFrame({
        "timestamp": idx,
        "open": closes,
        "high": [c + 0.001 for c in closes],
        "low": [c - 0.001 for c in closes],
        "close": closes,
        "volume": [1000.0] * periods,
    })


class _FakeFeed:
    def __init__(self, df_1h: pd.DataFrame, df_15m: pd.DataFrame) -> None:
        self._df = {"60": df_1h, "15": df_15m}
        self._client = object()

    async def get_klines(self, interval: str, limit: int) -> pd.DataFrame:
        return self._df[interval]

    async def get_instrument_info(self) -> dict:
        return {
            "qtyStep": 0.1,
            "minOrderQty": 0.1,
            "minNotionalValue": 5.0,
        }


class _FakeStrategy:
    def __init__(self) -> None:
        self.seen: dict | None = None
        self._cooldown_candles_remaining = 0

    def evaluate(self, df_1h, df_15m, price, balance, market_signal=None):
        self.seen = {
            "n_1h": len(df_1h),
            "n_15m": len(df_15m),
            "price": price,
        }
        return Signal()

    def tick_cooldown(self) -> None:
        pass


@pytest.mark.asyncio
async def test_evaluate_uses_closed_candles_only():
    from bot.monitor import SignalMonitor

    monitor = SignalMonitor(
        symbols=["XRPUSDT"],
        capital_pyg=100000.0,
        fx_usd_pyg=7300.0,
        notify=False,
    )
    monitor._feeds["XRPUSDT"] = _FakeFeed(
        _ohlcv(20, "60min", closed_close=1.0),
        _ohlcv(30, "15min", closed_close=1.2345),
    )
    fake_strategy = _FakeStrategy()
    monitor._strategies["XRPUSDT"] = fake_strategy
    monitor._market_data = {}

    card = await monitor.evaluate("XRPUSDT")

    assert fake_strategy.seen == {
        "n_1h": 19,
        "n_15m": 29,
        "price": pytest.approx(1.2345),
    }
    assert card.price == pytest.approx(1.2345)


def test_main_honors_disabled_flag(monkeypatch, capsys):
    import sys

    import bot.monitor as monitor_module

    monkeypatch.setattr(monitor_module, "MONITOR_CFG", {"enabled": False})
    monkeypatch.setattr(sys, "argv", ["monitor.py"])

    monitor_module.main()

    assert "deshabilitado" in capsys.readouterr().out


def test_monitor_source_has_no_execution_paths():
    source = (
        Path(__file__).resolve().parent.parent / "bot" / "monitor.py"
    ).read_text(encoding="utf-8")

    for forbidden in (
        "PaperExecutor",
        "ExecutionEngine",
        "SymbolRunner",
        "OrderReconciler",
        "place_order",
    ):
        assert forbidden not in source
