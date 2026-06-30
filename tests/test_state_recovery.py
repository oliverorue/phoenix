"""Tests para StateRecovery (FASE 5.2)."""

import pytest
import json
from unittest.mock import patch


@pytest.fixture
def mock_state_file(tmp_path, monkeypatch):
    """Crea un STATE_FILE temporal usando monkeypatch (thread-safe)."""
    from bot.core.state_recovery import StateRecovery
    state_path = tmp_path / "bot_state_persistent.json"
    monkeypatch.setattr(StateRecovery, "STATE_FILE", state_path)
    return state_path


class TestStateRecoveryLoadAll:
    """Verifica _load_all()."""

    def test_load_all_file_not_found_returns_empty(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        result = StateRecovery._load_all()
        assert result == {}

    def test_load_all_returns_parsed_json(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        data = {"symbols": {"XRPUSDT": {"position": "open"}}}
        mock_state_file.write_text(json.dumps(data))
        result = StateRecovery._load_all()
        assert result == data

    def test_load_all_corrupt_json_returns_empty(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        mock_state_file.write_text("not json")
        result = StateRecovery._load_all()
        assert result == {}


class TestStateRecoveryWriteAll:
    """Verifica _write_all()."""

    def test_write_all_creates_file(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        payload = {"symbols": {}}
        result = StateRecovery._write_all(payload)
        assert result is True
        assert mock_state_file.exists()
        loaded = json.loads(mock_state_file.read_text())
        assert loaded == payload

    def test_write_all_failure_returns_false(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        with patch("builtins.open", side_effect=PermissionError("denied")):
            result = StateRecovery._write_all({"test": True})
            assert result is False


class TestStateRecoverySaveSymbolState:
    """Verifica save_symbol_state()."""

    def test_save_and_load_symbol_state(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        state = {"position": "open", "entry_price": 0.6185}
        saved = StateRecovery.save_symbol_state("XRPUSDT", state)
        assert saved is True

        loaded = StateRecovery.load_symbol_state("XRPUSDT")
        assert loaded["position"] == "open"
        assert loaded["entry_price"] == 0.6185

    def test_save_multiple_symbols(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        StateRecovery.save_symbol_state("XRPUSDT", {"pos": "open"})
        StateRecovery.save_symbol_state("ETHUSDT", {"pos": "closed"})

        all_syms = StateRecovery.load_all_symbols()
        assert "XRPUSDT" in all_syms
        assert "ETHUSDT" in all_syms
        assert all_syms["XRPUSDT"]["pos"] == "open"
        assert all_syms["ETHUSDT"]["pos"] == "closed"

    def test_save_preserves_other_symbols(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        StateRecovery.save_symbol_state("XRPUSDT", {"pos": "open"})
        StateRecovery.save_symbol_state("DOGEUSDT", {"pos": "open"})
        StateRecovery.save_symbol_state("XRPUSDT", {"pos": "closed"})

        loaded = StateRecovery.load_symbol_state("XRPUSDT")
        assert loaded["pos"] == "closed"
        loaded_doge = StateRecovery.load_symbol_state("DOGEUSDT")
        assert loaded_doge["pos"] == "open"


class TestStateRecoveryLoadSymbolState:
    """Verifica load_symbol_state()."""

    def test_load_nonexistent_symbol_returns_empty(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        result = StateRecovery.load_symbol_state("NONEXIST")
        assert result == {}

    def test_load_existing_symbol(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        StateRecovery.save_symbol_state("XRPUSDT", {"pos": "open"})
        result = StateRecovery.load_symbol_state("XRPUSDT")
        assert result["pos"] == "open"


class TestStateRecoveryLegacy:
    """Verifica métodos legacy save_state/load_state."""

    def test_save_state_writes_whole_file(self, mock_state_file):
        """save_state() escribe el payload completo (no bajo un symbol key)."""
        from bot.core.state_recovery import StateRecovery
        payload = {"bot_state": "running", "last_cycle": 42}
        result = StateRecovery.save_state(payload)
        assert result is True
        # load_state() retorna load_all_symbols(), que es el dict completo
        loaded = StateRecovery.load_state()
        assert loaded == payload

    def test_load_state_empty(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        result = StateRecovery.load_state()
        assert result == {}


class TestStateRecoveryClearSymbol:
    """Verifica clear_symbol()."""

    def test_clear_existing_symbol(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        StateRecovery.save_symbol_state("XRPUSDT", {"pos": "open"})
        StateRecovery.save_symbol_state("ETHUSDT", {"pos": "open"})

        cleared = StateRecovery.clear_symbol("XRPUSDT")
        assert cleared is True

        remaining = StateRecovery.load_all_symbols()
        assert "XRPUSDT" not in remaining
        assert "ETHUSDT" in remaining

    def test_clear_nonexistent_symbol_returns_true(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        result = StateRecovery.clear_symbol("NONEXIST")
        assert result is True

    def test_clear_all_symbols(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        StateRecovery.save_symbol_state("XRPUSDT", {"pos": "open"})
        StateRecovery.clear_symbol("XRPUSDT")
        loaded = StateRecovery.load_all_symbols()
        assert loaded == {}


class TestStateRecoveryLoadAllSymbols:
    """Verifica load_all_symbols()."""

    def test_load_all_symbols_empty_when_no_file(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        result = StateRecovery.load_all_symbols()
        assert result == {}

    def test_load_all_symbols_returns_all(self, mock_state_file):
        from bot.core.state_recovery import StateRecovery
        StateRecovery.save_symbol_state("A", {"v": 1})
        StateRecovery.save_symbol_state("B", {"v": 2})
        result = StateRecovery.load_all_symbols()
        assert len(result) == 2
        assert result["A"]["v"] == 1
        assert result["B"]["v"] == 2
