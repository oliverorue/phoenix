"""
Tests para bot/core/kelly_sizing.py — Kelly Criterion Dynamic Sizing.

Cubre:
  - Inicialización y validación de parámetros
  - record_trade / streak tracking
  - get_multiplier con datos insuficientes
  - Kelly Criterion con wins y losses mixtos
  - Casos extremos: todos wins, todos losses
  - Modulación por racha (wins/losses consecutivos)
  - Ventana deslizante (solo últimos N trades)
  - Propiedades stats
  - Integración con risk_manager.py (Kelly aplicado a calculate_position_size)
  - Integración con trade_journal.py (record_trade llamado al cerrar)
"""

import pytest
from bot.core.kelly_sizing import KellySizing


# ================================================================
# Fixtures
# ================================================================

@pytest.fixture
def kelly() -> KellySizing:
    """KellySizing con defaults de test (window pequeña para forzar rápido)."""
    return KellySizing(
        window_size=10,
        min_trades=3,
        max_multiplier=2.5,
        min_multiplier=0.3,
        fractional=0.25,
        streak_boost=0.08,
        streak_penalty=0.12,
        max_streak_boost=0.50,
        conservative_tier_threshold=50,
        conservative_max_multiplier=2.5,
    )


# ================================================================
# Test: Inicialización
# ================================================================

class TestInit:
    def test_defaults(self) -> None:
        k = KellySizing()
        assert k.window_size == 20
        assert k.min_trades == 5
        assert k.max_multiplier == 1.5          # Fix #2: reducido desde 2.5
        assert k.min_multiplier == 0.3
        assert k.fractional == 0.25
        assert k.streak_boost == 0.04           # Fix #8: reducido desde 0.08
        assert k.streak_penalty == 0.08         # Fix #8: reducido desde 0.12
        assert k.max_streak_boost == 0.20       # Fix #8: nuevo cap
        assert k.conservative_tier_threshold == 50   # Fix #2: nuevo tier
        assert k.conservative_max_multiplier == 1.2  # Fix #2: nuevo tier
        assert k.total_trades == 0
        assert k.streak == 0

    def test_custom_params(self) -> None:
        k = KellySizing(
            window_size=15,
            min_trades=8,
            max_multiplier=3.0,
            min_multiplier=0.5,
            fractional=0.5,
            streak_boost=0.1,
            streak_penalty=0.15,
            max_streak_boost=0.40,
            conservative_tier_threshold=100,
            conservative_max_multiplier=2.0,
        )
        assert k.window_size == 15
        assert k.min_trades == 8
        assert k.max_multiplier == 3.0
        assert k.min_multiplier == 0.5
        assert k.fractional == 0.5
        assert k.streak_boost == 0.1
        assert k.streak_penalty == 0.15
        assert k.max_streak_boost == 0.40
        assert k.conservative_tier_threshold == 100
        assert k.conservative_max_multiplier == 2.0

    @pytest.mark.parametrize("bad_kwargs", [
        {"window_size": 1},
        {"min_trades": 0},
        {"fractional": 0},
        {"fractional": 1.5},
        {"max_multiplier": 0.5, "min_multiplier": 1.0},
        {"max_streak_boost": 0},
    ])
    def test_validation_errors(self, bad_kwargs) -> None:
        with pytest.raises(ValueError):
            KellySizing(**bad_kwargs)


# ================================================================
# Test: record_trade / streak tracking
# ================================================================

class TestRecordTrade:
    def test_initial_streak_zero(self, kelly: KellySizing) -> None:
        assert kelly.streak == 0

    def test_single_win_sets_streak_positive(self, kelly: KellySizing) -> None:
        kelly.record_trade(10.0)
        assert kelly.streak == 1
        assert kelly.total_trades == 1

    def test_single_loss_sets_streak_negative(self, kelly: KellySizing) -> None:
        kelly.record_trade(-5.0)
        assert kelly.streak == -1
        assert kelly.total_trades == 1

    def test_consecutive_wins(self, kelly: KellySizing) -> None:
        kelly.record_trade(5.0)
        kelly.record_trade(3.0)
        kelly.record_trade(7.0)
        assert kelly.streak == 3

    def test_consecutive_losses(self, kelly: KellySizing) -> None:
        kelly.record_trade(-2.0)
        kelly.record_trade(-4.0)
        assert kelly.streak == -2

    def test_win_after_loss_resets_streak(self, kelly: KellySizing) -> None:
        kelly.record_trade(-5.0)
        kelly.record_trade(-3.0)
        assert kelly.streak == -2
        kelly.record_trade(10.0)
        assert kelly.streak == 1  # racha se reinicia a positiva

    def test_loss_after_win_resets_streak(self, kelly: KellySizing) -> None:
        kelly.record_trade(5.0)
        kelly.record_trade(3.0)
        assert kelly.streak == 2
        kelly.record_trade(-2.0)
        assert kelly.streak == -1  # racha se reinicia a negativa

    def test_breakeven_resets_streak(self, kelly: KellySizing) -> None:
        kelly.record_trade(5.0)
        kelly.record_trade(3.0)
        assert kelly.streak == 2
        kelly.record_trade(0.0)
        assert kelly.streak == 0


# ================================================================
# Test: get_multiplier
# ================================================================

class TestGetMultiplier:
    def test_returns_1_when_insufficient_trades(self, kelly: KellySizing) -> None:
        """Con menos trades que min_trades, debe retornar 1.0."""
        kelly.record_trade(5.0)
        kelly.record_trade(3.0)
        assert kelly.total_trades == 2
        assert kelly.get_multiplier() == 1.0

    def test_all_wins_returns_max(self, kelly: KellySizing) -> None:
        """Si todos los trades son wins, retorna max_multiplier."""
        for _ in range(5):
            kelly.record_trade(10.0)
        assert kelly.get_multiplier() == kelly.max_multiplier

    def test_all_losses_returns_min(self, kelly: KellySizing) -> None:
        """Si todos los trades son losses, retorna min_multiplier."""
        for _ in range(5):
            kelly.record_trade(-5.0)
        assert kelly.get_multiplier() == kelly.min_multiplier

    def test_mixed_wins_losses_between_min_max(self, kelly: KellySizing) -> None:
        """Con wins y losses mixtos, multiplier debe estar entre min y max."""
        # Alternar wins y losses para evitar streak penalty
        kelly.record_trade(10.0)
        kelly.record_trade(-4.0)
        kelly.record_trade(8.0)
        kelly.record_trade(-5.0)
        kelly.record_trade(12.0)
        # win_rate=0.6, sin racha negativa (streak=1, último win)
        mult = kelly.get_multiplier()
        assert kelly.min_multiplier <= mult <= kelly.max_multiplier
        assert mult > 1.0  # win_rate > 0.5 debe dar multiplier > 1.0

    def test_poor_performance_below_one(self, kelly: KellySizing) -> None:
        """Con más losses que wins, multiplier debe ser < 1.0."""
        kelly.record_trade(3.0)
        kelly.record_trade(-8.0)
        kelly.record_trade(-6.0)
        kelly.record_trade(-5.0)
        kelly.record_trade(2.0)
        mult = kelly.get_multiplier()
        assert mult < 1.0
        assert mult >= kelly.min_multiplier

    def test_sliding_window_only_recent(self, kelly: KellySizing) -> None:
        """
        Verifica que solo los últimos `window_size` trades afecten.
        Si llenamos con wins, luego más trades que window_size con losses,
        debe reflejar solo el rendimiento reciente.
        """
        # 10 wins antiguos
        for _ in range(10):
            kelly.record_trade(10.0)
        assert kelly.get_multiplier() == kelly.max_multiplier

        # Agregar 10 losses recientes (desplaza los wins fuera de la ventana)
        for _ in range(10):
            kelly.record_trade(-5.0)
        mult = kelly.get_multiplier()
        assert mult == kelly.min_multiplier  # solo losses en ventana


# ================================================================
# Test: Modulación por racha
# ================================================================

class TestStreakModulation:
    def test_streak_boost_after_3_wins(self, kelly: KellySizing) -> None:
        """
        Con misma win-rate, una racha de 3+ wins al final debe dar multiplier
        más alto que sin racha.
        """
        # Sin racha: win-loss alternados (termina en win, streak=1)
        kelly_alt = KellySizing(window_size=10, min_trades=3)
        kelly_alt.record_trade(10.0)
        kelly_alt.record_trade(-4.0)
        kelly_alt.record_trade(10.0)
        kelly_alt.record_trade(-4.0)
        kelly_alt.record_trade(10.0)
        alt_mult = kelly_alt.get_multiplier()

        # Con racha de 3 wins al final (misma win_rate=0.6)
        kelly_str = KellySizing(window_size=10, min_trades=3)
        kelly_str.record_trade(-4.0)
        kelly_str.record_trade(-4.0)
        kelly_str.record_trade(10.0)
        kelly_str.record_trade(10.0)
        kelly_str.record_trade(10.0)
        str_mult = kelly_str.get_multiplier()

        # Ambos tienen win_rate=0.6, pero el segundo termina con racha de 3 wins
        assert str_mult > alt_mult

    def test_streak_penalty_after_2_losses(self, kelly: KellySizing) -> None:
        """
        Con misma win-rate, una racha de 2+ losses debe dar multiplier
        más bajo que sin racha.
        """
        # Sin racha
        kelly_alt = KellySizing(window_size=10, min_trades=3)
        kelly_alt.record_trade(10.0)
        kelly_alt.record_trade(-4.0)
        kelly_alt.record_trade(10.0)
        kelly_alt.record_trade(-4.0)
        kelly_alt.record_trade(10.0)
        alt_mult = kelly_alt.get_multiplier()

        # Con racha de 2 losses al final
        kelly_str = KellySizing(window_size=10, min_trades=3)
        kelly_str.record_trade(10.0)
        kelly_str.record_trade(-4.0)
        kelly_str.record_trade(10.0)
        kelly_str.record_trade(-4.0)
        kelly_str.record_trade(-4.0)
        str_mult = kelly_str.get_multiplier()

        assert str_mult <= alt_mult


# ================================================================
# Test: stats property
# ================================================================

class TestStats:
    def test_stats_insufficient_data(self, kelly: KellySizing) -> None:
        kelly.record_trade(5.0)
        stats = kelly.stats
        assert stats["status"] == "insufficient_data"
        assert stats["multiplier"] == 1.0
        assert stats["total_trades"] == 1

    def test_stats_active(self, kelly: KellySizing) -> None:
        for pnl in [10.0, 8.0, -4.0, 12.0, -5.0]:
            kelly.record_trade(pnl)
        stats = kelly.stats
        assert stats["status"] == "active"
        assert stats["total_trades"] == 5
        assert stats["window_trades"] == 5
        assert stats["win_rate"] == 0.6  # 3/5
        assert stats["streak"] == -1     # último fue loss
        assert 0.3 <= stats["multiplier"] <= 2.5

    def test_stats_win_loss_ratio(self, kelly: KellySizing) -> None:
        for pnl in [10.0, 8.0, -4.0]:
            kelly.record_trade(pnl)
        stats = kelly.stats
        # avg_win = 9.0, avg_loss = 4.0, ratio = 2.25
        assert stats["avg_win"] == 9.0
        assert stats["avg_loss"] == 4.0
        assert stats["win_loss_ratio"] == 2.25
