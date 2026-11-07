"""Smoke tests del scanner global: motor de anomalías y riesgo (sin red)."""

import pytest

from bot.scanner.anomaly_engine import AnomalyEngine
from bot.scanner.config import ScannerConfig
from bot.scanner.models import Signal
from bot.scanner.risk_engine import RiskEngine

T = 1_700_000_000.0


def _seed_price(engine: AnomalyEngine, symbol: str, price: float = 1.0) -> None:
    engine.on_agg_trade(symbol, T, price, 0.0, False)


def test_obi_extreme_signals_long():
    cfg = ScannerConfig(obi_extreme=0.6, volume_zscore=999.0)
    engine = AnomalyEngine(cfg)
    _seed_price(engine, "XRPUSDT")

    engine.on_depth("XRPUSDT", [(1.0, 900.0)], [(1.0, 100.0)])  # OBI = +0.8

    sig = engine.evaluate("XRPUSDT", T)
    assert sig is not None
    assert sig.direction == "LONG"
    assert any("OBI" in r for r in sig.reasons)
    assert sig.confidence > 0.0


def test_funding_extreme_signals_short():
    cfg = ScannerConfig(funding_extreme=0.0005, obi_extreme=1.5, volume_zscore=999.0)
    engine = AnomalyEngine(cfg)
    _seed_price(engine, "XRPUSDT")

    engine.on_funding("XRPUSDT", 0.001)  # sobreapalancado LONG → bias SHORT

    sig = engine.evaluate("XRPUSDT", T)
    assert sig is not None
    assert sig.direction == "SHORT"


def test_liquidation_cascade_signals_short():
    cfg = ScannerConfig(
        liq_cascade_min_events=3,
        liq_cascade_min_notional=1000.0,
        obi_extreme=1.5,
        funding_extreme=1.0,
        volume_zscore=999.0,
    )
    engine = AnomalyEngine(cfg)
    _seed_price(engine, "XRPUSDT")

    for _ in range(3):
        engine.on_force_order("XRPUSDT", T, "SELL", 500.0)

    sig = engine.evaluate("XRPUSDT", T)
    assert sig is not None
    assert sig.direction == "SHORT"
    assert any("Liquidaciones" in r for r in sig.reasons)


def test_no_signal_without_data():
    engine = AnomalyEngine(ScannerConfig())
    assert engine.evaluate("XRPUSDT", T) is None


def test_diagnostics_counters_increment():
    engine = AnomalyEngine(ScannerConfig())
    engine.on_agg_trade("XRPUSDT", T, 1.0, 1.0, False)
    engine.on_depth("XRPUSDT", [(1.0, 1.0)], [(1.0, 1.0)])
    engine.on_funding("XRPUSDT", 0.0)
    engine.on_force_order("XRPUSDT", T, "SELL", 1.0)

    d = engine.diagnostics()
    assert d["agg"] == 1
    assert d["depth"] == 1
    assert d["funding"] == 1
    assert d["force"] == 1


def test_risk_engine_scales_with_confidence():
    risk = RiskEngine(base_risk_pct=0.01)
    high = Signal(symbol="XRPUSDT", direction="LONG", confidence=1.0, price=1.0)
    low = Signal(symbol="XRPUSDT", direction="LONG", confidence=0.0, price=1.0)

    assert risk.suggest(1000.0, high).risk_usdt == pytest.approx(10.0)
    assert risk.suggest(1000.0, low).risk_usdt == pytest.approx(5.0)


def test_scanner_config_from_settings():
    cfg = ScannerConfig.from_settings()
    assert cfg.min_quote_volume_24h > 0
    assert cfg.max_symbols > 0
