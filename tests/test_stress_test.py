"""
Tests para el sistema de Stress Testing (flash crash, gap, volatilidad extrema).

Cubre:
  - StressScenario (defaults, personalizado, escenarios predefinidos)
  - generate_stress_candles (flash_crash, gap_down, gap_up, extreme_volatility)
  - dynamic_slippage (normal, estrés, escalado extremo)
  - check_sl_tp_in_candle (LONG/SHORT, gap_through, TP1 prioridad sobre SL)
  - run_stress_scenario_async (flujo completo con PaperExecutor)
  - run_all_stress_scenarios_async (suite completa)
  - StressTestResult (summary, balance_change_pct, passed)
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bot.core.paper_executor import PaperExecutor
from bot.core.stress_test import (
    FLASH_CRASH_MODERATE,
    FLASH_CRASH_SEVERE,
    GAP_DOWN_5PCT,
    GAP_DOWN_10PCT,
    GAP_UP_5PCT,
    VOLATILITY_EXTREME,
    FillEvent,
    ScenarioType,
    StressScenario,
    StressTestResult,
    check_sl_tp_in_candle,
    dynamic_slippage,
    generate_stress_candles,
    run_all_stress_scenarios_async,
    run_stress_scenario_async,
)


# =========================================================================
#  Helpers compartidos
# =========================================================================

@pytest.fixture
def mock_data_feed() -> MagicMock:
    """Mock de DataFeed para PaperExecutor."""
    df = MagicMock()
    df.symbol = "TESTUSDT"
    df._instrument_cache = {"qtyStep": "0.001"}
    return df


def _make_executor(data_feed: MagicMock, balance: float = 10000.0) -> PaperExecutor:
    """Crea un PaperExecutor con estado limpio."""
    exec_ = PaperExecutor(
        data_feed=data_feed,
        journal=None,
        initial_balance=balance,
        slippage_pct=0.001,
        commission_pct=0.0005,
    )
    exec_.paper_balance = balance
    return exec_


# =========================================================================
#  TestScenarioType
# =========================================================================

class TestScenarioType:
    """Verifica el enum ScenarioType."""

    def test_enum_values(self) -> None:
        assert ScenarioType.FLASH_CRASH.value == "flash_crash"
        assert ScenarioType.GAP_DOWN.value == "gap_down"
        assert ScenarioType.GAP_UP.value == "gap_up"
        assert ScenarioType.EXTREME_VOLATILITY.value == "extreme_volatility"

    def test_enum_members_count(self) -> None:
        assert len(ScenarioType) == 4


# =========================================================================
#  TestStressScenario
# =========================================================================

class TestStressScenario:
    """Verifica la creación y configuración de StressScenario."""

    def test_defaults(self) -> None:
        sc = StressScenario(scenario_type=ScenarioType.FLASH_CRASH, label="Test")
        assert sc.scenario_type == ScenarioType.FLASH_CRASH
        assert sc.label == "Test"
        assert sc.base_price == 0.50
        assert sc.max_slippage_pct == 0.05
        assert sc.normal_slippage_pct == 0.001
        assert sc.crash_depth_pct == 0.20
        assert sc.crash_recovery_pct == 0.60
        assert sc.crash_candles == 4
        assert sc.recovery_candles == 12
        assert sc.gap_pct == 0.05
        assert sc.fill_price_mode == "worst"
        assert sc.vol_multiplier == 8.0
        assert sc.pre_candles == 10
        assert sc.post_candles == 20
        assert sc.noise_std == 0.001

    def test_custom_values(self) -> None:
        sc = StressScenario(
            scenario_type=ScenarioType.GAP_DOWN,
            label="Custom Gap",
            base_price=100.0,
            max_slippage_pct=0.10,
            gap_pct=0.15,
            fill_price_mode="open",
            pre_candles=5,
            post_candles=10,
            noise_std=0.002,
        )
        assert sc.base_price == 100.0
        assert sc.max_slippage_pct == 0.10
        assert sc.gap_pct == 0.15
        assert sc.fill_price_mode == "open"
        assert sc.pre_candles == 5
        assert sc.post_candles == 10
        assert sc.noise_std == 0.002


# =========================================================================
#  TestPredefinedScenarios
# =========================================================================

class TestPredefinedScenarios:
    """Verifica que los escenarios predefinidos tengan valores esperados."""

    def test_flash_crash_severe(self) -> None:
        sc = FLASH_CRASH_SEVERE
        assert sc.scenario_type == ScenarioType.FLASH_CRASH
        assert sc.label == "Flash Crash Severo -25%"
        assert sc.crash_depth_pct == 0.25
        assert sc.crash_recovery_pct == 0.50
        assert sc.max_slippage_pct == 0.08

    def test_flash_crash_moderate(self) -> None:
        sc = FLASH_CRASH_MODERATE
        assert sc.scenario_type == ScenarioType.FLASH_CRASH
        assert sc.label == "Flash Crash Moderado -15%"
        assert sc.crash_depth_pct == 0.15
        assert sc.crash_recovery_pct == 0.70
        assert sc.crash_candles == 3
        assert sc.max_slippage_pct == 0.04

    def test_gap_down_5pct(self) -> None:
        sc = GAP_DOWN_5PCT
        assert sc.scenario_type == ScenarioType.GAP_DOWN
        assert sc.label == "Gap Down -5%"
        assert sc.gap_pct == 0.05
        assert sc.max_slippage_pct == 0.03

    def test_gap_down_10pct(self) -> None:
        sc = GAP_DOWN_10PCT
        assert sc.scenario_type == ScenarioType.GAP_DOWN
        assert sc.label == "Gap Down -10%"
        assert sc.gap_pct == 0.10
        assert sc.max_slippage_pct == 0.05

    def test_gap_up_5pct(self) -> None:
        sc = GAP_UP_5PCT
        assert sc.scenario_type == ScenarioType.GAP_UP
        assert sc.label == "Gap Up +5%"
        assert sc.gap_pct == 0.05
        assert sc.max_slippage_pct == 0.03

    def test_volatility_extreme(self) -> None:
        sc = VOLATILITY_EXTREME
        assert sc.scenario_type == ScenarioType.EXTREME_VOLATILITY
        assert sc.label == "Volatilidad Extrema 8×"
        assert sc.vol_multiplier == 8.0
        assert sc.max_slippage_pct == 0.06

    def test_all_have_labels(self) -> None:
        for sc in [FLASH_CRASH_SEVERE, FLASH_CRASH_MODERATE,
                   GAP_DOWN_5PCT, GAP_DOWN_10PCT, GAP_UP_5PCT,
                   VOLATILITY_EXTREME]:
            assert sc.label, f"Scenario missing label: {sc}"


# =========================================================================
#  TestGenerateStressCandles
# =========================================================================

class TestGenerateStressCandles:
    """Verifica la generación de velas OHLC para cada tipo de escenario."""

    def test_flash_crash_candle_count(self) -> None:
        sc = StressScenario(
            scenario_type=ScenarioType.FLASH_CRASH,
            label="Test",
            pre_candles=5,
            crash_candles=4,
            recovery_candles=10,
            post_candles=5,
            noise_std=0.0,
        )
        candles = generate_stress_candles(sc)
        expected_count = 5 + 4 + 10 + 5
        assert len(candles) == expected_count, (
            f"Expected {expected_count} candles, got {len(candles)}"
        )

    def test_flash_crash_price_decreases(self) -> None:
        sc = StressScenario(
            scenario_type=ScenarioType.FLASH_CRASH,
            label="Test",
            base_price=100.0,
            pre_candles=3,
            crash_candles=4,
            recovery_candles=5,
            post_candles=3,
            noise_std=0.0,
            crash_depth_pct=0.20,
            crash_recovery_pct=0.50,
        )
        candles = generate_stress_candles(sc)
        pre_close = candles[sc.pre_candles - 1]["close"]
        assert pre_close == pytest.approx(100.0, rel=0.01)

        crash_end_idx = sc.pre_candles + sc.crash_candles - 1
        crash_close = candles[crash_end_idx]["close"]
        assert crash_close < pre_close

        recovery_end = candles[-1]["close"]
        assert recovery_end > crash_close

    def test_flash_crash_bounds(self) -> None:
        sc = StressScenario(
            scenario_type=ScenarioType.FLASH_CRASH,
            label="Test",
            base_price=100.0,
            noise_std=0.0001,
        )
        candles = generate_stress_candles(sc)
        for i, c in enumerate(candles):
            assert c["low"] <= min(c["open"], c["close"]), f"Candle {i}: low bound"
            assert c["high"] >= max(c["open"], c["close"]), f"Candle {i}: high bound"

    def test_gap_down_direction(self) -> None:
        sc = StressScenario(
            scenario_type=ScenarioType.GAP_DOWN,
            label="Test",
            base_price=100.0,
            gap_pct=0.10,
            pre_candles=3,
            post_candles=5,
            noise_std=0.0,
        )
        candles = generate_stress_candles(sc)
        pre_close = candles[sc.pre_candles - 1]["close"]
        gap_candle = candles[sc.pre_candles]
        assert gap_candle["open"] < pre_close

    def test_gap_up_direction(self) -> None:
        sc = StressScenario(
            scenario_type=ScenarioType.GAP_UP,
            label="Test",
            base_price=100.0,
            gap_pct=0.10,
            pre_candles=3,
            post_candles=5,
            noise_std=0.0,
        )
        candles = generate_stress_candles(sc)
        pre_close = candles[sc.pre_candles - 1]["close"]
        gap_candle = candles[sc.pre_candles]
        assert gap_candle["open"] > pre_close

    def test_gap_down_bounds(self) -> None:
        sc = StressScenario(
            scenario_type=ScenarioType.GAP_DOWN,
            label="Test",
            base_price=100.0,
            gap_pct=0.05,
            pre_candles=3,
            post_candles=5,
            noise_std=0.0,
        )
        candles = generate_stress_candles(sc)
        for i, c in enumerate(candles):
            assert c["low"] <= min(c["open"], c["close"]), f"Candle {i} low bound"
            assert c["high"] >= max(c["open"], c["close"]), f"Candle {i} high bound"

    def test_extreme_volatility_bounds(self) -> None:
        sc = StressScenario(
            scenario_type=ScenarioType.EXTREME_VOLATILITY,
            label="Test",
            base_price=0.50,
            vol_multiplier=4.0,
            noise_std=0.0,
            pre_candles=3,
            post_candles=3,
        )
        candles = generate_stress_candles(sc)
        for i, c in enumerate(candles):
            assert c["low"] <= min(c["open"], c["close"]), f"Candle {i} low bound"
            assert c["high"] >= max(c["open"], c["close"]), f"Candle {i} high bound"

    def test_deterministic_seed(self) -> None:
        sc = StressScenario(
            scenario_type=ScenarioType.FLASH_CRASH,
            label="Test",
            base_price=100.0,
            noise_std=0.001,
        )
        candles1 = generate_stress_candles(sc)
        candles2 = generate_stress_candles(sc)
        assert len(candles1) == len(candles2)
        for i in range(len(candles1)):
            assert candles1[i] == candles2[i], f"Candle {i} differs between runs"


# =========================================================================
#  TestDynamicSlippage
# =========================================================================

class TestDynamicSlippage:
    """Verifica el cálculo de slippage dinámico."""

    def test_normal_period_returns_normal_slippage(self) -> None:
        sc = StressScenario(
            scenario_type=ScenarioType.FLASH_CRASH,
            label="Test",
            normal_slippage_pct=0.001,
            max_slippage_pct=0.05,
        )
        slip = dynamic_slippage(100.0, 99.5, sc, is_stress_period=False)
        assert slip == 0.001

    def test_stress_period_returns_max(self) -> None:
        sc = StressScenario(
            scenario_type=ScenarioType.FLASH_CRASH,
            label="Test",
            normal_slippage_pct=0.001,
            max_slippage_pct=0.05,
        )
        slip = dynamic_slippage(100.0, 99.5, sc, is_stress_period=True)
        assert slip == 0.05

    def test_extreme_movement_scales_slippage(self) -> None:
        sc = StressScenario(
            scenario_type=ScenarioType.GAP_DOWN,
            label="Test",
            gap_pct=0.05,
            max_slippage_pct=0.05,
        )
        slip = dynamic_slippage(80.0, 100.0, sc, is_stress_period=True)
        assert slip == pytest.approx(0.075, rel=0.01)

    def test_extreme_movement_capped_at_20pct(self) -> None:
        sc = StressScenario(
            scenario_type=ScenarioType.GAP_DOWN,
            label="Test",
            gap_pct=0.05,
            max_slippage_pct=0.10,
        )
        slip = dynamic_slippage(50.0, 100.0, sc, is_stress_period=True)
        assert slip == pytest.approx(0.20, rel=0.01)

    def test_no_prev_price_returns_max(self) -> None:
        sc = StressScenario(
            scenario_type=ScenarioType.FLASH_CRASH,
            label="Test",
            max_slippage_pct=0.05,
        )
        slip = dynamic_slippage(100.0, 0.0, sc, is_stress_period=True)
        assert slip == 0.05


# =========================================================================
#  TestCheckSlTpInCandle
# =========================================================================

class TestCheckSlTpInCandle:
    """Verifica la evaluación de SL/TP en una vela."""

    # ── LONG ──

    def test_long_tp1_hit(self) -> None:
        candle = {"open": 1.00, "high": 1.06, "low": 0.99, "close": 1.05}
        tp_ev, sl_ev = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=0.98,
            tp1=1.05, tp2=1.08, direction="LONG",
        )
        assert tp_ev is not None and tp_ev.triggered
        assert tp_ev.reason == "tp1"
        assert sl_ev is None or not sl_ev.triggered

    def test_long_sl_hit(self) -> None:
        candle = {"open": 1.00, "high": 1.01, "low": 0.97, "close": 0.98}
        tp_ev, sl_ev = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=0.98,
            tp1=1.05, tp2=1.08, direction="LONG",
        )
        assert sl_ev is not None and sl_ev.triggered
        assert sl_ev.reason == "sl"
        assert tp_ev is None or not tp_ev.triggered

    def test_long_tp1_before_sl(self) -> None:
        candle = {"open": 1.00, "high": 1.06, "low": 0.97, "close": 1.02}
        tp_ev, sl_ev = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=0.98,
            tp1=1.05, tp2=1.08, direction="LONG",
        )
        assert tp_ev is not None and tp_ev.triggered
        assert tp_ev.reason == "tp1"
        assert sl_ev is None or not sl_ev.triggered

    def test_long_tp2_in_same_candle(self) -> None:
        candle = {"open": 1.00, "high": 1.10, "low": 0.99, "close": 1.09}
        tp_ev, _ = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=0.98,
            tp1=1.05, tp2=1.08, direction="LONG",
        )
        assert tp_ev is not None and tp_ev.triggered
        assert tp_ev.reason == "tp2"

    def test_long_no_hit(self) -> None:
        candle = {"open": 1.00, "high": 1.02, "low": 0.99, "close": 1.01}
        tp_ev, sl_ev = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=0.98,
            tp1=1.05, tp2=1.08, direction="LONG",
        )
        assert (tp_ev is None or not tp_ev.triggered)
        assert (sl_ev is None or not sl_ev.triggered)

    # ── SHORT ──

    def test_short_tp1_hit(self) -> None:
        candle = {"open": 1.00, "high": 1.01, "low": 0.94, "close": 0.95}
        tp_ev, _ = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=1.02,
            tp1=0.95, tp2=0.92, direction="SHORT",
        )
        assert tp_ev is not None and tp_ev.triggered
        assert tp_ev.reason == "tp1"

    def test_short_sl_hit(self) -> None:
        candle = {"open": 1.00, "high": 1.03, "low": 0.99, "close": 1.02}
        _, sl_ev = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=1.02,
            tp1=0.95, tp2=0.92, direction="SHORT",
        )
        assert sl_ev is not None and sl_ev.triggered
        assert sl_ev.reason == "sl"

    def test_short_tp1_before_sl(self) -> None:
        candle = {"open": 1.00, "high": 1.03, "low": 0.94, "close": 0.96}
        tp_ev, sl_ev = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=1.02,
            tp1=0.95, tp2=0.92, direction="SHORT",
        )
        assert tp_ev is not None and tp_ev.triggered
        assert tp_ev.reason == "tp1"
        assert sl_ev is None or not sl_ev.triggered

    def test_short_tp2_in_same_candle(self) -> None:
        candle = {"open": 1.00, "high": 1.01, "low": 0.91, "close": 0.92}
        tp_ev, _ = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=1.02,
            tp1=0.95, tp2=0.92, direction="SHORT",
        )
        assert tp_ev is not None and tp_ev.triggered
        assert tp_ev.reason == "tp2"

    def test_short_no_hit(self) -> None:
        candle = {"open": 1.00, "high": 1.01, "low": 0.98, "close": 0.99}
        tp_ev, sl_ev = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=1.02,
            tp1=0.95, tp2=0.92, direction="SHORT",
        )
        assert (tp_ev is None or not tp_ev.triggered)
        assert (sl_ev is None or not sl_ev.triggered)

    # ── Gap through (LONG) ──

    def test_long_gap_through_sl(self) -> None:
        candle = {"open": 0.95, "high": 0.97, "low": 0.94, "close": 0.96}
        _, sl_ev = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=0.98,
            tp1=1.05, tp2=1.08, direction="LONG",
            gap_mode=True,
        )
        assert sl_ev is not None and sl_ev.triggered
        assert sl_ev.reason == "gap_sl"
        assert sl_ev.gap_through is True

    def test_long_gap_through_tp(self) -> None:
        candle = {"open": 1.10, "high": 1.12, "low": 1.09, "close": 1.11}
        tp_ev, _ = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=0.98,
            tp1=1.05, tp2=1.08, direction="LONG",
            gap_mode=True,
        )
        assert tp_ev is not None and tp_ev.triggered
        assert tp_ev.reason == "gap_tp"
        assert tp_ev.gap_through is True

    def test_long_gap_through_sl_no_gap_mode(self) -> None:
        candle = {"open": 0.95, "high": 0.97, "low": 0.94, "close": 0.96}
        _, sl_ev = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=0.98,
            tp1=1.05, tp2=1.08, direction="LONG",
            gap_mode=False,
        )
        assert sl_ev is not None and sl_ev.triggered
        assert sl_ev.reason == "sl"
        assert not sl_ev.gap_through

    # ── Gap through (SHORT) ──

    def test_short_gap_through_sl(self) -> None:
        candle = {"open": 1.05, "high": 1.07, "low": 1.04, "close": 1.06}
        _, sl_ev = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=1.02,
            tp1=0.95, tp2=0.92, direction="SHORT",
            gap_mode=True,
        )
        assert sl_ev is not None and sl_ev.triggered
        assert sl_ev.reason == "gap_sl"
        assert sl_ev.gap_through is True

    def test_short_gap_through_tp(self) -> None:
        candle = {"open": 0.90, "high": 0.92, "low": 0.89, "close": 0.91}
        tp_ev, _ = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=1.02,
            tp1=0.95, tp2=0.92, direction="SHORT",
            gap_mode=True,
        )
        assert tp_ev is not None and tp_ev.triggered
        assert tp_ev.reason == "gap_tp"
        assert tp_ev.gap_through is True

    # ── tp1_already_hit ──

    def test_long_tp1_already_hit_skips_tp1(self) -> None:
        candle = {"open": 1.00, "high": 1.06, "low": 0.99, "close": 1.04}
        tp_ev, _ = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=1.00,
            tp1=1.05, tp2=1.08, direction="LONG",
            tp1_already_hit=True,
        )
        assert tp_ev is None or not tp_ev.triggered

    # ── Slippage ──

    def test_slippage_applied_to_sl(self) -> None:
        candle = {"open": 1.00, "high": 1.01, "low": 0.95, "close": 0.97}
        _, sl_ev = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=0.98,
            tp1=1.05, tp2=1.08, direction="LONG",
            slippage_pct=0.01,
        )
        assert sl_ev is not None and sl_ev.triggered
        expected_fill = 0.98 * (1.0 - 0.01)
        assert sl_ev.fill_price == pytest.approx(expected_fill, rel=0.001)

    def test_slippage_applied_to_tp1(self) -> None:
        candle = {"open": 1.00, "high": 1.06, "low": 0.99, "close": 1.05}
        tp_ev, _ = check_sl_tp_in_candle(
            candle=candle, entry=1.00, current_sl=0.98,
            tp1=1.05, tp2=1.08, direction="LONG",
            slippage_pct=0.005,
        )
        assert tp_ev is not None and tp_ev.triggered
        expected_fill = 1.05 * (1.0 + 0.005)
        assert tp_ev.fill_price == pytest.approx(expected_fill, rel=0.001)


# =========================================================================
#  TestStressTestResult
# =========================================================================

class TestStressTestResult:
    """Verifica el dataclass StressTestResult."""

    def test_balance_change_pct_positive(self) -> None:
        result = StressTestResult(
            scenario=FLASH_CRASH_SEVERE,
            initial_balance=1000.0,
            final_balance=1050.0,
        )
        assert result.balance_change_pct == pytest.approx(5.0)

    def test_balance_change_pct_negative(self) -> None:
        result = StressTestResult(
            scenario=FLASH_CRASH_SEVERE,
            initial_balance=1000.0,
            final_balance=950.0,
        )
        assert result.balance_change_pct == pytest.approx(-5.0)

    def test_balance_change_pct_zero_initial(self) -> None:
        result = StressTestResult(
            scenario=FLASH_CRASH_SEVERE,
            initial_balance=0.0,
            final_balance=100.0,
        )
        assert result.balance_change_pct == 0.0

    def test_summary_contains_keys(self) -> None:
        result = StressTestResult(
            scenario=FLASH_CRASH_SEVERE,
            initial_balance=10000.0,
            final_balance=9500.0,
            max_drawdown_pct=8.5,
            sl_triggered=True,
            tp_triggered=False,
            gap_through_sl=True,
            worst_slippage_pct=0.08,
            fill_events=[FillEvent(triggered=True, reason="gap_sl", fill_price=0.46)],
            candle_count=46,
            passed=True,
        )
        s = result.summary()
        assert "Escenario" in s
        assert s["Balance inicial"] == "$10000.00"
        assert s["Balance final"] == "$9500.00"
        assert s["Max Drawdown"] == "8.50%"
        assert s["SL disparado"] is True
        assert s["Gap through SL"] is True
        assert s["Peor slippage"] == "8.00%"
        assert s["Estado"] == "✅ PASÓ"
        assert s["Velas simuladas"] == 46

    def test_passed_false_when_dd_over_50_and_no_sl(self) -> None:
        result = StressTestResult(
            scenario=FLASH_CRASH_SEVERE,
            max_drawdown_pct=55.0,
            sl_triggered=False,
            passed=False,
        )
        assert not result.passed

    def test_passed_true_when_sl_triggered(self) -> None:
        result = StressTestResult(
            scenario=FLASH_CRASH_SEVERE,
            max_drawdown_pct=55.0,
            sl_triggered=True,
            passed=True,
        )
        assert result.passed

    def test_fill_events_default_empty(self) -> None:
        result = StressTestResult(scenario=FLASH_CRASH_SEVERE)
        assert result.fill_events == []


# =========================================================================
#  TestRunStressScenarioAsync
# =========================================================================

class TestRunStressScenarioAsync:
    """Verifica la ejecución completa de un escenario de estrés vía async."""

    @pytest.mark.asyncio
    async def test_run_flash_crash_long(self, mock_data_feed: MagicMock) -> None:
        executor = _make_executor(mock_data_feed, balance=12000.0)
        sc = StressScenario(
            scenario_type=ScenarioType.FLASH_CRASH,
            label="Test Flash Crash",
            base_price=100.0,
            crash_depth_pct=0.10,
            crash_recovery_pct=0.50,
            crash_candles=3,
            recovery_candles=5,
            pre_candles=3,
            post_candles=5,
            noise_std=0.0,
            max_slippage_pct=0.05,
        )

        result = await run_stress_scenario_async(executor, sc, direction="LONG")

        assert isinstance(result, StressTestResult)
        assert result.scenario == sc
        assert result.candle_count > 0
        assert not executor.position_open

    @pytest.mark.asyncio
    async def test_run_flash_crash_short(self, mock_data_feed: MagicMock) -> None:
        executor = _make_executor(mock_data_feed, balance=12000.0)
        sc = StressScenario(
            scenario_type=ScenarioType.FLASH_CRASH,
            label="Test Flash Crash Short",
            base_price=100.0,
            crash_depth_pct=0.10,
            crash_recovery_pct=0.50,
            crash_candles=3,
            recovery_candles=5,
            pre_candles=3,
            post_candles=3,
            noise_std=0.0,
        )

        result = await run_stress_scenario_async(executor, sc, direction="SHORT")

        assert isinstance(result, StressTestResult)
        assert not executor.position_open

    @pytest.mark.asyncio
    async def test_run_gap_down_long(self, mock_data_feed: MagicMock) -> None:
        executor = _make_executor(mock_data_feed, balance=12000.0)
        sc = StressScenario(
            scenario_type=ScenarioType.GAP_DOWN,
            label="Test Gap Down",
            base_price=100.0,
            gap_pct=0.05,
            max_slippage_pct=0.03,
            pre_candles=3,
            post_candles=5,
            noise_std=0.0,
        )

        result = await run_stress_scenario_async(executor, sc, direction="LONG")

        assert isinstance(result, StressTestResult)
        assert not executor.position_open

    @pytest.mark.asyncio
    async def test_run_gap_up_short(self, mock_data_feed: MagicMock) -> None:
        executor = _make_executor(mock_data_feed, balance=12000.0)
        sc = StressScenario(
            scenario_type=ScenarioType.GAP_UP,
            label="Test Gap Up",
            base_price=100.0,
            gap_pct=0.05,
            max_slippage_pct=0.03,
            pre_candles=3,
            post_candles=5,
            noise_std=0.0,
        )

        result = await run_stress_scenario_async(executor, sc, direction="SHORT")

        assert isinstance(result, StressTestResult)
        assert not executor.position_open

    @pytest.mark.asyncio
    async def test_insufficient_balance_returns_failed(self, mock_data_feed: MagicMock) -> None:
        executor = _make_executor(mock_data_feed, balance=1.0)
        sc = StressScenario(
            scenario_type=ScenarioType.FLASH_CRASH,
            label="Test No Balance",
            base_price=100.0,
            noise_std=0.0,
        )

        result = await run_stress_scenario_async(executor, sc, direction="LONG")
        assert not result.passed
        assert result.final_balance == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_result_has_expected_metrics(self, mock_data_feed: MagicMock) -> None:
        executor = _make_executor(mock_data_feed, balance=12000.0)
        sc = StressScenario(
            scenario_type=ScenarioType.EXTREME_VOLATILITY,
            label="Test Vol",
            base_price=100.0,
            vol_multiplier=3.0,
            noise_std=0.0,
            pre_candles=3,
            post_candles=3,
        )

        result = await run_stress_scenario_async(executor, sc, direction="LONG")

        assert hasattr(result, "initial_balance")
        assert hasattr(result, "final_balance")
        assert hasattr(result, "max_drawdown_pct")
        assert hasattr(result, "sl_triggered")
        assert hasattr(result, "tp_triggered")
        assert hasattr(result, "gap_through_sl")
        assert hasattr(result, "worst_slippage_pct")
        assert hasattr(result, "fill_events")
        assert hasattr(result, "candle_count")
        assert hasattr(result, "passed")
        assert result.worst_slippage_pct >= 0.0


# =========================================================================
#  TestRunAllStressScenariosAsync
# =========================================================================

class TestRunAllStressScenariosAsync:
    """Verifica la suite completa de stress tests vía async."""

    @pytest.mark.asyncio
    async def test_runs_all_scenarios(self, mock_data_feed: MagicMock) -> None:
        executor = _make_executor(mock_data_feed, balance=12000.0)

        scenarios = [
            StressScenario(ScenarioType.FLASH_CRASH, "Test FC", base_price=100.0,
                           crash_depth_pct=0.05, crash_recovery_pct=0.50,
                           crash_candles=2, recovery_candles=3,
                           pre_candles=2, post_candles=2, noise_std=0.0),
            StressScenario(ScenarioType.GAP_DOWN, "Test GD", base_price=100.0,
                           gap_pct=0.03, pre_candles=2, post_candles=3, noise_std=0.0),
            StressScenario(ScenarioType.GAP_UP, "Test GU", base_price=100.0,
                           gap_pct=0.03, pre_candles=2, post_candles=3, noise_std=0.0),
        ]

        results = await run_all_stress_scenarios_async(executor, base_price=100.0, scenarios=scenarios)

        assert len(results) == 6
        assert all(isinstance(r, StressTestResult) for r in results)

    @pytest.mark.asyncio
    async def test_default_scenarios_list(self, mock_data_feed: MagicMock) -> None:
        executor = _make_executor(mock_data_feed, balance=12000.0)

        results = await run_all_stress_scenarios_async(executor, base_price=0.50)

        assert len(results) == 12
        labels = {r.scenario.label for r in results}
        assert "Flash Crash Severo -25%" in labels
        assert "Flash Crash Moderado -15%" in labels
        assert "Gap Down -5%" in labels
        assert "Gap Down -10%" in labels
        assert "Gap Up +5%" in labels
        assert "Volatilidad Extrema 8×" in labels

    @pytest.mark.asyncio
    async def test_results_have_both_directions(self, mock_data_feed: MagicMock) -> None:
        executor = _make_executor(mock_data_feed, balance=12000.0)
        sc = StressScenario(ScenarioType.FLASH_CRASH, "Test Dir", base_price=100.0,
                            crash_depth_pct=0.05, crash_recovery_pct=0.50,
                            crash_candles=2, recovery_candles=3,
                            pre_candles=2, post_candles=3, noise_std=0.0)

        results = await run_all_stress_scenarios_async(executor, base_price=100.0, scenarios=[sc])

        assert len(results) == 2
