"""
Tests para CorrelationGuard — Correlación entre símbolos.
"""

import pytest
from unittest.mock import MagicMock
from bot.core.correlation import CorrelationGuard


@pytest.fixture
def sample_groups():
    return [
        {
            "name": "majors",
            "symbols": ["XRPUSDT", "ETHUSDT"],
            "max_concurrent": 1,
        },
        {
            "name": "high_risk",
            "symbols": ["SOLUSDT", "DOGEUSDT"],
            "max_concurrent": 1,
        },
    ]


@pytest.fixture
def guard(sample_groups):
    return CorrelationGuard(groups=sample_groups)


@pytest.fixture
def mock_runners():
    """Crea runners mock con position_open controlable."""
    runners = []
    for sym in ["XRPUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT"]:
        r = MagicMock()
        r.symbol = sym
        r.executor = MagicMock()
        r.executor.position_open = False
        runners.append(r)
    return runners


class TestCorrelationGuardInit:
    """Verifica inicialización."""

    def test_init_empty_groups(self):
        guard = CorrelationGuard()
        assert guard._groups == []
        assert guard._symbol_map == {}

    def test_init_with_groups(self, guard, sample_groups):
        assert len(guard._groups) == 2
        assert guard._symbol_map["XRPUSDT"]["name"] == "majors"
        assert guard._symbol_map["ETHUSDT"]["name"] == "majors"
        assert guard._symbol_map["SOLUSDT"]["name"] == "high_risk"
        assert "BTCUSDT" not in guard._symbol_map

    def test_unknown_symbol_no_restriction(self, guard):
        """Símbolo sin grupo definido = sin restricción."""
        assert guard.can_open("BTCUSDT") is True


class TestCorrelationGuardCanOpen:
    """Verifica la lógica de can_open."""

    def test_allow_when_no_position_in_group(self, guard, mock_runners):
        """Ningún símbolo del grupo tiene posición → permitido."""
        guard.bind_runners(mock_runners)
        assert guard.can_open("XRPUSDT") is True
        assert guard.can_open("ETHUSDT") is True

    def test_block_when_correlated_has_position(self, guard, mock_runners):
        """Si ETHUSDT tiene posición, XRPUSDT debe estar bloqueado."""
        # ETH tiene posición abierta
        mock_runners[1].executor.position_open = True
        guard.bind_runners(mock_runners)

        assert guard.can_open("XRPUSDT") is False
        # ETH ya tiene posición, debe poder abrir otra? No, max_concurrent=1
        assert guard.can_open("ETHUSDT") is False

    def test_allow_different_groups(self, guard, mock_runners):
        """XRP (majors) y SOL (high_risk) son grupos diferentes → ambos permitidos."""
        mock_runners[2].executor.position_open = True  # SOL abierto
        guard.bind_runners(mock_runners)

        # XRP está en grupo diferente a SOL
        assert guard.can_open("XRPUSDT") is True
        # SOL ya abierto, DOGE bloqueado en high_risk
        assert guard.can_open("DOGEUSDT") is False

    def test_release_when_position_closes(self, guard, mock_runners):
        """Cuando ETH cierra posición, XRP debe poder abrir."""
        mock_runners[1].executor.position_open = True  # ETH abierto
        guard.bind_runners(mock_runners)

        assert guard.can_open("XRPUSDT") is False

        # ETH cierra
        mock_runners[1].executor.position_open = False
        assert guard.can_open("XRPUSDT") is True

    def test_multiple_positions_allowed_when_max_concurrent_gt_1(self):
        """Grupo con max_concurrent=2 permite hasta 2 posiciones."""
        groups = [{
            "name": "test",
            "symbols": ["A", "B", "C"],
            "max_concurrent": 2,
        }]
        guard = CorrelationGuard(groups=groups)
        runners = []
        for sym in ["A", "B", "C"]:
            r = MagicMock()
            r.symbol = sym
            r.executor = MagicMock()
            r.executor.position_open = False
            runners.append(r)

        runners[0].executor.position_open = True  # A abierto
        guard.bind_runners(runners)

        # 1 de 2 permitidas, B y C pueden abrir
        assert guard.can_open("B") is True
        assert guard.can_open("C") is True

        runners[1].executor.position_open = True  # B también abierto
        # 2 de 2, C bloqueado
        assert guard.can_open("C") is False

    def test_can_open_no_restriction_without_getter(self, guard):
        """Sin getter vinculado, can_open retorna True para símbolos conocidos."""
        # bind_symbol no se llamó → no hay getters
        assert guard.can_open("XRPUSDT") is True


class TestCorrelationGuardBind:
    """Verifica bind_symbol."""

    def test_bind_symbol(self, guard):
        flag = False

        def getter():
            return flag

        guard.bind_symbol("XRPUSDT", getter)
        guard.bind_symbol("ETHUSDT", getter)

        # Ambos en majors, flag=False → sin posición
        assert guard.can_open("XRPUSDT") is True
        assert guard.can_open("ETHUSDT") is True

        # Activar flag → ETH "tiene posición"
        flag = True
        assert guard.can_open("XRPUSDT") is False


class TestCorrelationGuardBlocked:
    """Verifica blocked_symbols."""

    def test_no_blocked_when_none_active(self, guard, mock_runners):
        guard.bind_runners(mock_runners)
        assert guard.blocked_symbols == []

    def test_blocks_correlated(self, guard, mock_runners):
        mock_runners[0].executor.position_open = True  # XRP abierto
        guard.bind_runners(mock_runners)
        blocked = guard.blocked_symbols
        assert "ETHUSDT" in blocked
        assert "XRPUSDT" not in blocked

    def test_multiple_groups(self, guard, mock_runners):
        mock_runners[0].executor.position_open = True  # XRP abierto (majors)
        mock_runners[2].executor.position_open = True  # SOL abierto (high_risk)
        guard.bind_runners(mock_runners)
        blocked = guard.blocked_symbols
        assert "ETHUSDT" in blocked
        assert "DOGEUSDT" in blocked
        assert "XRPUSDT" not in blocked
        assert "SOLUSDT" not in blocked


class TestCorrelationGuardActivePositions:
    """Verifica get_active_positions."""

    def test_empty_when_none_active(self, guard, mock_runners):
        guard.bind_runners(mock_runners)
        assert guard.get_active_positions() == []

    def test_returns_active_groups(self, guard, mock_runners):
        mock_runners[0].executor.position_open = True  # XRP en majors
        guard.bind_runners(mock_runners)
        active = guard.get_active_positions()
        assert len(active) == 1
        assert active[0]["group"] == "majors"
        assert "XRPUSDT" in active[0]["symbols"]

    def test_both_groups_active(self, guard, mock_runners):
        mock_runners[0].executor.position_open = True  # XRP
        mock_runners[2].executor.position_open = True  # SOL
        guard.bind_runners(mock_runners)
        active = guard.get_active_positions()
        assert len(active) == 2


class TestCorrelationGuardStats:
    """Verifica stats."""

    def test_stats_empty(self):
        guard = CorrelationGuard()
        stats = guard.stats
        assert stats["total_groups"] == 0
        assert stats["groups"] == []

    def test_stats_with_groups(self, guard, mock_runners):
        mock_runners[0].executor.position_open = True
        guard.bind_runners(mock_runners)
        stats = guard.stats
        assert stats["total_groups"] == 2
        # Majors: 1 active of 1 max → blocked
        majors = [g for g in stats["groups"] if g["name"] == "majors"][0]
        assert majors["active"] == 1
        assert majors["blocked"] is True
        # High_risk: 0 active → not blocked
        hr = [g for g in stats["groups"] if g["name"] == "high_risk"][0]
        assert hr["active"] == 0
        assert hr["blocked"] is False
