"""Tests del cálculo puro de niveles y cantidad (sizing)."""

from __future__ import annotations

import pytest

from bot.execution.models import Direction
from bot.execution.sizing import plan_trade


class TestPlanTrade:
    def test_long_levels_and_qty(self):
        plan = plan_trade("XRPUSDT", Direction.LONG, price=1.0, risk_usdt=0.85)
        assert plan.entry_price == 1.0
        assert plan.sl_price == pytest.approx(0.985)
        assert plan.tp1_price == pytest.approx(1.015)
        assert plan.tp2_price == pytest.approx(1.03)
        assert plan.qty == pytest.approx(0.85 / 0.015)

    def test_short_levels_mirrored(self):
        plan = plan_trade("XRPUSDT", Direction.SHORT, price=1.0, risk_usdt=0.85)
        assert plan.sl_price == pytest.approx(1.015)
        assert plan.tp1_price == pytest.approx(0.985)
        assert plan.tp2_price == pytest.approx(0.97)

    def test_trailing_distance(self):
        plan = plan_trade(
            "XRPUSDT", Direction.LONG, price=1.0, risk_usdt=0.85,
            trailing_dist_pct=0.5,
        )
        assert plan.trailing_dist == pytest.approx(0.5)

    def test_invalid_inputs_raise(self):
        with pytest.raises(ValueError):
            plan_trade("X", Direction.LONG, price=0.0, risk_usdt=1.0)
        with pytest.raises(ValueError):
            plan_trade("X", Direction.LONG, price=1.0, risk_usdt=0.0)
        with pytest.raises(ValueError):
            plan_trade("X", Direction.LONG, price=1.0, risk_usdt=1.0, sl_distance_pct=0.0)
