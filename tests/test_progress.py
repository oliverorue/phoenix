"""Tests del progreso de etapa (gates de edge probado)."""

from __future__ import annotations

from bot.execution.progress import format_progress, stage_progress


class TestStageProgress:
    def test_not_passed_early(self):
        progress = stage_progress(
            {"total": 5, "win_rate": 0.5, "profit_factor": 2.0, "pnl": 3.0}
        )
        assert not progress["passed"]
        assert progress["gates"]["trades"] is False
        assert progress["trades"] == 5
        assert any("35 trades" in m for m in progress["missing"])

    def test_passed_all_gates(self):
        progress = stage_progress(
            {"total": 40, "win_rate": 0.4, "profit_factor": 1.5, "pnl": 10.0}
        )
        assert progress["passed"]
        assert all(progress["gates"].values())
        assert "ETAPA CUMPLIDA" in format_progress(progress)

    def test_fails_negative_pnl(self):
        progress = stage_progress(
            {"total": 45, "win_rate": 0.4, "profit_factor": 1.5, "pnl": -1.0}
        )
        assert not progress["passed"]
        assert progress["gates"]["pnl"] is False

    def test_format_shows_inf_profit_factor(self):
        progress = stage_progress(
            {"total": 10, "win_rate": 0.6, "profit_factor": float("inf"), "pnl": 5.0}
        )
        assert "∞" in format_progress(progress)

    def test_empty_stats(self):
        progress = stage_progress({})
        assert progress["trades"] == 0
        assert not progress["passed"]
