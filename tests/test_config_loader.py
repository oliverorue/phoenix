"""Pruebas para el cargador de configuración (edge cases incluidos)."""
import sys
from pathlib import Path

# Asegurar que el directorio raíz está en el path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.loader import (
    FASES_ESCALAMIENTO,
    SYMBOL,
    TESTNET,
    get_phase_config,
)


class TestLoaderBasic:
    """Verifica los valores básicos de configuración."""

    def test_fases_cargadas(self):
        assert len(FASES_ESCALAMIENTO) == 5, "Debe haber 5 fases"
        assert 0 in FASES_ESCALAMIENTO
        assert FASES_ESCALAMIENTO[0]["leverage"] == 1

    def test_symbol_definido(self):
        assert isinstance(SYMBOL, str) and SYMBOL

    def test_testnet_booleano(self):
        assert isinstance(TESTNET, bool)

    def test_get_phase_config(self):
        fase, cfg = get_phase_config(50)
        assert fase == 0
        assert cfg["leverage"] == 1
        assert cfg["risk_per_trade"] > 0


class TestGetPhaseConfigEdgeCases:
    """Casos borde de get_phase_config."""

    def test_balance_exact_boundary_returns_correct_phase(self):
        fase, _ = get_phase_config(0)
        assert fase == 0

    def test_balance_high_triggers_last_phase(self):
        last_phase = max(FASES_ESCALAMIENTO.keys())
        last_cfg = FASES_ESCALAMIENTO[last_phase]
        high_balance = last_cfg["balance_min"] + 1
        fase, _ = get_phase_config(high_balance)
        assert fase == last_phase

    def test_balance_negative_returns_phase_0(self):
        fase, _ = get_phase_config(-1)
        assert fase == 0


class TestFasesEscalamientoValidation:
    """Validación estructural de FASES_ESCALAMIENTO."""

    def test_all_phases_have_required_keys(self):
        required = {
            "balance_min", "balance_max", "leverage", "risk_per_trade",
            "tp1_rr", "tp2_rr", "rsi_min_long", "rsi_max_long",
        }
        for fase_num, cfg in FASES_ESCALAMIENTO.items():
            missing = required - set(cfg.keys())
            assert not missing, f"Fase {fase_num} faltan keys: {missing}"

    def test_phase_balance_ranges_are_contiguous(self):
        sorted_phases = sorted(FASES_ESCALAMIENTO.items())
        for i in range(len(sorted_phases) - 1):
            _, cfg_a = sorted_phases[i]
            _, cfg_b = sorted_phases[i + 1]
            assert cfg_a["balance_max"] >= cfg_b["balance_min"], (
                f"Fase {sorted_phases[i][0]} max={cfg_a['balance_max']} "
                f"no alcanza Fase {sorted_phases[i + 1][0]} min={cfg_b['balance_min']}"
            )

    def test_phase_ids_start_at_zero(self):
        sorted_phases = sorted(FASES_ESCALAMIENTO.keys())
        assert sorted_phases[0] == 0
        for i in range(len(sorted_phases) - 1):
            assert sorted_phases[i + 1] == sorted_phases[i] + 1, (
                f"Fases no consecutivas entre {sorted_phases[i]} y {sorted_phases[i + 1]}"
            )
