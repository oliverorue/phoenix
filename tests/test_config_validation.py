"""Pruebas de validación Pydantic para configuraciones inválidas.

Cubre edge cases: valores límite, valores nulos, tipos incorrectos.
"""

import pytest
from pydantic import ValidationError
from bot.core.config_model import PhaseConfig


def base_config() -> dict:
    return {
        "balance_min": 0, "balance_max": 100, "leverage": 1,
        "risk_per_trade": 0.02, "max_dd_daily": 0.1, "max_stops_day": 4,
        "max_sl_pct": 0.05, "sl_buffer": 0.002, "tp1_rr": 1.2, "tp2_rr": 2.0,
        "tp1_porcentaje": 0.8, "breakeven_trigger": 0.4,
        "rsi_min_long": 30, "rsi_max_long": 45,
        "rsi_min_short": 45, "rsi_max_short": 80,
        "sesion_inicio": 14, "sesion_fin": 22,
        "trailing_dist": 0.001, "max_volatility_pct": 5.0,
        "lateral_adx_min": 18, "lateral_ma_distance": 0.008,
    }


# ── Invalid values ───────────────────────────────────────────────────────────

class TestLeverageValidation:
    def test_leverage_10_rejected(self):
        cfg = base_config()
        cfg["leverage"] = 10
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)

    def test_leverage_0_rejected(self):
        cfg = base_config()
        cfg["leverage"] = 0
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)

    def test_leverage_negative_rejected(self):
        cfg = base_config()
        cfg["leverage"] = -1
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)

    def test_leverage_3_accepted(self):
        cfg = base_config()
        cfg["leverage"] = 3
        fase = PhaseConfig(**cfg)
        assert fase.leverage == 3


class TestBalanceValidation:
    def test_balance_min_mayor_que_max_rejected(self):
        cfg = base_config()
        cfg["balance_min"] = 200
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)

    def test_balance_min_equal_max_rejected(self):
        cfg = base_config()
        cfg["balance_min"] = 100
        cfg["balance_max"] = 100
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)

    def test_balance_negative_rejected(self):
        cfg = base_config()
        cfg["balance_min"] = -100
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)


class TestRiskPerTradeValidation:
    def test_risk_0_5_rejected(self):
        cfg = base_config()
        cfg["risk_per_trade"] = 0.5
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)

    def test_risk_0_rejected(self):
        cfg = base_config()
        cfg["risk_per_trade"] = 0.0
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)

    def test_risk_negative_rejected(self):
        cfg = base_config()
        cfg["risk_per_trade"] = -0.01
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)

    def test_risk_0_05_accepted(self):
        cfg = base_config()
        cfg["risk_per_trade"] = 0.05
        fase = PhaseConfig(**cfg)
        assert fase.risk_per_trade == 0.05


class TestRSIValidation:
    def test_rsi_outside_0_100_rejected(self):
        cfg = base_config()
        cfg["rsi_min_long"] = -1
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)

    def test_rsi_min_equal_max_accepted(self):
        cfg = base_config()
        cfg["rsi_min_long"] = 45
        cfg["rsi_max_long"] = 45
        fase = PhaseConfig(**cfg)
        assert fase.rsi_min_long == 45

    def test_rsi_min_above_max_passes_validation(self):
        """El modelo no tiene cross-validation de RSI min vs max, así que esto pasa."""
        cfg = base_config()
        cfg["rsi_min_long"] = 50
        cfg["rsi_max_long"] = 40
        fase = PhaseConfig(**cfg)
        assert fase.rsi_min_long == 50
        assert fase.rsi_max_long == 40


class TestSessionValidation:
    def test_sesion_inicio_greater_than_fin_rejected(self):
        cfg = base_config()
        cfg["sesion_inicio"] = 22
        cfg["sesion_fin"] = 14
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)

    def test_sesion_equal_times_rejected(self):
        cfg = base_config()
        cfg["sesion_inicio"] = 14
        cfg["sesion_fin"] = 14
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)


class TestTPValidation:
    def test_tp_porcentaje_zero_rejected(self):
        cfg = base_config()
        cfg["tp1_porcentaje"] = 0.0
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)

    def test_tp_porcentaje_above_1_rejected(self):
        cfg = base_config()
        cfg["tp1_porcentaje"] = 1.5
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)

    def test_tp1_rr_unordered_accepted(self):
        """El modelo no tiene cross-validation de tp1_rr vs tp2_rr, así que esto pasa."""
        cfg = base_config()
        cfg["tp1_rr"] = 3.0
        cfg["tp2_rr"] = 1.0
        fase = PhaseConfig(**cfg)
        assert fase.tp1_rr == 3.0
        assert fase.tp2_rr == 1.0


class TestTP2RRValidation:
    def test_tp2_rr_equal_to_tp1_rr_accepted(self):
        """tp2_rr puede ser igual a tp1_rr (sin TP2)."""
        cfg = base_config()
        cfg["tp1_rr"] = 1.5
        cfg["tp2_rr"] = 1.5
        fase = PhaseConfig(**cfg)
        assert fase.tp2_rr == 1.5

    def test_tp2_rr_less_than_tp1_rr_accepted(self):
        """El modelo no tiene cross-validation de tp1_rr vs tp2_rr, así que esto pasa."""
        cfg = base_config()
        cfg["tp1_rr"] = 2.0
        cfg["tp2_rr"] = 1.5
        fase = PhaseConfig(**cfg)
        assert fase.tp1_rr == 2.0
        assert fase.tp2_rr == 1.5


# ── Valid config ─────────────────────────────────────────────────────────────

def test_config_valida():
    cfg = base_config()
    fase = PhaseConfig(**cfg)
    assert fase.leverage == 1
    assert fase.risk_per_trade == 0.02
    assert fase.tp1_rr == 1.2


class TestTypeEnforcement:
    """Verifica que tipos incorrectos sean rechazados."""

    def test_string_instead_of_int(self):
        cfg = base_config()
        cfg["leverage"] = "tres"
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)

    def test_none_value(self):
        cfg = base_config()
        cfg["leverage"] = None
        with pytest.raises(ValidationError):
            PhaseConfig(**cfg)

    def test_extra_fields_ignored(self):
        """Pydantic V2 ignora campos extra por defecto."""
        cfg = base_config()
        cfg["campo_extra"] = "valor"
        fase = PhaseConfig(**cfg)
        assert not hasattr(fase, "campo_extra")
