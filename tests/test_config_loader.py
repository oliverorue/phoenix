"""Pruebas para el cargador de configuración (edge cases incluidos)."""
import sys
from pathlib import Path

# Asegurar que el directorio raíz está en el path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.loader import (
    FASES_ESCALAMIENTO,
    SUPPORTED_COINS,
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

    def test_monedas_cargadas(self):
        assert "XRPUSDT" in SUPPORTED_COINS
        assert "ETHUSDT" in SUPPORTED_COINS

    def test_symbol_valido(self):
        assert SYMBOL in SUPPORTED_COINS

    def test_testnet_booleano(self):
        assert isinstance(TESTNET, bool)

    def test_get_phase_config(self):
        fase, cfg = get_phase_config(50)
        assert fase == 0
        assert cfg["leverage"] == 1
        # El valor real de risk_per_trade está en settings.yaml (puede cambiar)
        assert cfg["risk_per_trade"] > 0


class TestGetPhaseConfigEdgeCases:
    """Casos borde de get_phase_config."""

    def test_balance_exact_boundary_returns_correct_phase(self):
        """Balance exactamente en el límite inferior de una fase."""
        fase, _ = get_phase_config(0)
        assert fase == 0

    def test_balance_high_triggers_last_phase(self):
        """Balance muy alto debe caer en la última fase."""
        last_phase = max(FASES_ESCALAMIENTO.keys())
        last_cfg = FASES_ESCALAMIENTO[last_phase]
        # Usamos un balance dentro del rango de la última fase
        high_balance = last_cfg["balance_min"] + 1
        fase, _ = get_phase_config(high_balance)
        assert fase == last_phase

    def test_balance_negative_returns_phase_0(self):
        """Balance negativo debe caer en fase 0."""
        fase, _ = get_phase_config(-1)
        assert fase == 0


class TestSupportedCoinsEdgeCases:
    """Casos borde para SUPPORTED_COINS."""

    def test_supported_coins_are_uppercase(self):
        """Todos los símbolos deben estar en mayúsculas."""
        for coin in SUPPORTED_COINS:
            assert coin == coin.upper(), f"{coin} no está en mayúsculas"

    def test_supported_coins_end_with_usdt(self):
        """Todos los símbolos deben terminar en USDT."""
        for coin in SUPPORTED_COINS:
            assert coin.endswith("USDT"), f"{coin} no termina en USDT"

    def test_supported_coins_no_duplicates(self):
        """No debe haber símbolos duplicados."""
        assert len(SUPPORTED_COINS) == len(set(SUPPORTED_COINS))


class TestFasesEscalamientoValidation:
    """Validación estructural de FASES_ESCALAMIENTO."""

    def test_all_phases_have_required_keys(self):
        """Cada fase debe tener las keys mínimas requeridas."""
        required = {
            "balance_min", "balance_max", "leverage", "risk_per_trade",
            "tp1_rr", "tp2_rr", "rsi_min_long", "rsi_max_long",
        }
        for fase_num, cfg in FASES_ESCALAMIENTO.items():
            missing = required - set(cfg.keys())
            assert not missing, f"Fase {fase_num} faltan keys: {missing}"

    def test_phase_balance_ranges_are_contiguous(self):
        """El balance_max de fase N debe coincidir con balance_min de fase N+1."""
        sorted_phases = sorted(FASES_ESCALAMIENTO.items())
        for i in range(len(sorted_phases) - 1):
            _, cfg_a = sorted_phases[i]
            _, cfg_b = sorted_phases[i + 1]
            assert cfg_a["balance_max"] >= cfg_b["balance_min"], (
                f"Fase {sorted_phases[i][0]} max={cfg_a['balance_max']} "
                f"no alcanza Fase {sorted_phases[i + 1][0]} min={cfg_b['balance_min']}"
            )

    def test_phase_ids_start_at_zero(self):
        """Las fases deben comenzar en 0 y ser consecutivas."""
        sorted_phases = sorted(FASES_ESCALAMIENTO.keys())
        assert sorted_phases[0] == 0
        for i in range(len(sorted_phases) - 1):
            assert sorted_phases[i + 1] == sorted_phases[i] + 1, (
                f"Fases no consecutivas entre {sorted_phases[i]} y {sorted_phases[i + 1]}"
            )
