"""
Tests para ConfigWatcher — Recarga en caliente de settings.yaml.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Generator
from unittest.mock import patch

import pytest

from bot.core.config_watcher import ConfigWatcher, _sync_module_vars


# ── Fixtures ────────────────────────────────────────────────────

@pytest.fixture
def temp_yaml(tmp_path: Path) -> Generator[Path, None, None]:
    """Crea un settings.yaml temporal para las pruebas."""
    yaml_path = tmp_path / "settings.yaml"
    yaml_path.write_text(
        "symbol: XRPUSDT\n"
        "execution:\n"
        "  loop_sleep_position: 30\n"
        "  loop_sleep_no_position: 90\n"
        "phases:\n"
        "  0:\n"
        "    descripcion: Fase 0\n"
        "    balance_min: 0\n"
        "    balance_max: 100\n"
        "    leverage: 1\n"
        "paper_trading:\n"
        "  enabled: true\n"
        "  initial_balance: 5000\n"
    )
    yield yaml_path


@pytest.fixture
def watcher(temp_yaml: Path) -> Generator[ConfigWatcher, None, None]:
    """ConfigWatcher apuntando al YAML temporal."""
    with patch("bot.core.config_watcher._CONFIG_PATH", temp_yaml):
        w = ConfigWatcher(check_interval=0.1)
        yield w


@pytest.fixture(autouse=True)
def _no_pollute_config_loader() -> Generator:
    """Evita que _sync_module_vars corrompa config/loader.py.

    Los tests de config_watcher usan un YAML temporal mínimo. Si
    _sync_module_vars se ejecuta sobre el módulo real, reemplaza las
    variables de config/loader.py (FASES_ESCALAMIENTO, EXCHANGE, etc.)
    con valores incompletos, rompiendo todos los tests posteriores que
    importan config.loader.

    Esta fixture para _sync_module_vars para que no toque el módulo real.
    TestSyncModuleVars prueba _sync_module_vars directamente y ya usa
    su propio mock de config.loader.
    """
    with patch("bot.core.config_watcher._sync_module_vars"):
        yield


# ── Tests de carga inicial ──────────────────────────────────────

class TestInit:
    """Verifica la carga inicial del YAML."""

    def test_loads_yaml(self, watcher: ConfigWatcher) -> None:
        assert watcher._settings.get("symbol") == "XRPUSDT"
        assert watcher._settings.get("execution", {}).get("loop_sleep_position") == 30

    def test_mtime_set(self, watcher: ConfigWatcher, temp_yaml: Path) -> None:
        assert watcher._mtime == os.path.getmtime(temp_yaml)

    def test_empty_yaml(self, tmp_path: Path) -> None:
        empty = tmp_path / "settings.yaml"
        empty.write_text("")
        with patch("bot.core.config_watcher._CONFIG_PATH", empty):
            w = ConfigWatcher()
            assert w._settings == {}

    def test_missing_yaml(self, tmp_path: Path) -> None:
        missing = tmp_path / "nonexistent.yaml"
        with patch("bot.core.config_watcher._CONFIG_PATH", missing):
            w = ConfigWatcher()
            assert w._settings == {}


# ── Tests de detección de cambios ───────────────────────────────

class TestChangeDetection:
    """Verifica que detecta cambios en el archivo."""

    def test_no_change_initially(self, watcher: ConfigWatcher) -> None:
        assert not watcher._has_changed()

    def test_detects_change(self, watcher: ConfigWatcher, temp_yaml: Path) -> None:
        time.sleep(0.01)  # asegurar mtime diferente
        temp_yaml.write_text("symbol: BTCUSDT\n")
        assert watcher._has_changed()

    def test_reload_updates_settings(self, watcher: ConfigWatcher, temp_yaml: Path) -> None:
        time.sleep(0.01)
        temp_yaml.write_text("symbol: BTCUSDT\n")
        assert watcher.reload()
        assert watcher._settings["symbol"] == "BTCUSDT"

    def test_reload_false_when_no_change(self, watcher: ConfigWatcher) -> None:
        assert not watcher.reload()


# ── Tests de acceso a configuración ─────────────────────────────

class TestGet:
    """Verifica el método get()."""

    def test_get_top_level(self, watcher: ConfigWatcher) -> None:
        assert watcher.get("symbol") == "XRPUSDT"

    def test_get_nested(self, watcher: ConfigWatcher) -> None:
        assert watcher.get("execution", "loop_sleep_position") == 30

    def test_get_default_when_missing(self, watcher: ConfigWatcher) -> None:
        assert watcher.get("nonexistent", default=42) == 42

    def test_get_default_when_nested_missing(self, watcher: ConfigWatcher) -> None:
        assert watcher.get("execution", "missing_key", default=99) == 99

    def test_get_all_settings(self, watcher: ConfigWatcher) -> None:
        all_ = watcher.get()
        assert isinstance(all_, dict)
        assert "symbol" in all_


# ── Tests de callbacks ──────────────────────────────────────────

class TestCallbacks:
    """Verifica que los callbacks se disparen al recargar."""

    def test_callback_called_on_change(self, watcher: ConfigWatcher, temp_yaml: Path) -> None:
        calls: list[str] = []

        def on_phases(new_val: Any, old_val: Any) -> None:
            calls.append("phases_changed")

        watcher.register_callback("phases", on_phases)
        time.sleep(0.01)
        temp_yaml.write_text(
            "symbol: XRPUSDT\n"
            "phases:\n"
            "  0:\n"
            "    descripcion: Fase 0 Modificada\n"
            "    balance_min: 0\n"
            "    balance_max: 200\n"
            "    leverage: 2\n"
        )
        watcher.reload()
        assert len(calls) == 1

    def test_callback_not_called_on_no_change(self, watcher: ConfigWatcher) -> None:
        calls: list[str] = []

        def on_exe(new_val: Any, old_val: Any) -> None:
            calls.append("execution_changed")

        watcher.register_callback("execution", on_exe)
        watcher.reload()  # sin cambios
        assert len(calls) == 0

    def test_decorator_on_change(self, watcher: ConfigWatcher, temp_yaml: Path) -> None:
        calls: list[str] = []

        @watcher.on_change("paper_trading")
        def cb(new_val: Any, old_val: Any) -> None:
            calls.append("pt_changed")

        time.sleep(0.01)
        temp_yaml.write_text(
            "paper_trading:\n"
            "  enabled: false\n"
            "  initial_balance: 9999\n"
        )
        watcher.reload()
        assert len(calls) == 1
        assert calls[0] == "pt_changed"


# ── Tests de watch loop ─────────────────────────────────────────

@pytest.mark.asyncio
class TestWatchLoop:
    """Verifica el loop asíncrono de vigilancia."""

    async def test_watch_detects_change(self, watcher: ConfigWatcher, temp_yaml: Path) -> None:
        """El watch loop debe detectar y recargar cambios."""
        time.sleep(0.02)
        temp_yaml.write_text("symbol: ETHUSDT\n")

        # Ejecutar watch por un ciclo
        import asyncio
        task = asyncio.create_task(watcher.watch())
        await asyncio.sleep(0.15)  # más que check_interval=0.1
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        assert watcher._settings["symbol"] == "ETHUSDT"

    async def test_watch_loop_continues_after_error(
        self, watcher: ConfigWatcher, temp_yaml: Path
    ) -> None:
        """El watch loop no debe morir si hay un error temporal."""
        # Corromper el archivo temporalmente (YAML inválido: tab como indentación)
        temp_yaml.write_text("invalid_yaml:\n\tbroken_indent: 1\n")

        import asyncio
        task = asyncio.create_task(watcher.watch())
        await asyncio.sleep(0.15)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        # Settings no deberían haber cambiado
        assert watcher._settings.get("symbol") == "XRPUSDT"


# ── Tests de sync_module_vars ───────────────────────────────────

class TestSyncModuleVars:
    """Verifica que _sync_module_vars actualice config.loader correctamente."""

    def test_sync_basic(self) -> None:
        settings = {
            "symbol": "BTCUSDT",
            "execution": {"loop_sleep_position": 99},
        }
        with patch("config.loader") as mock_loader:
            _sync_module_vars(settings)
            assert mock_loader.SYMBOL == "BTCUSDT"
            assert mock_loader.LOOP_SLEEP_POSITION == 99

    def test_sync_paper_trading(self) -> None:
        settings = {
            "paper_trading": {
                "enabled": True,
                "initial_balance": 7777,
                "slippage_pct": 0.005,
                "commission_pct": 0.001,
            }
        }
        with patch("config.loader") as mock_loader:
            _sync_module_vars(settings)
            assert mock_loader.PAPER_TRADING_ENABLED is True
            assert mock_loader.PAPER_INITIAL_BALANCE == 7777.0
            assert mock_loader.PAPER_SLIPPAGE_PCT == 0.005
            assert mock_loader.PAPER_COMMISSION_PCT == 0.001

    def test_sync_phases(self) -> None:
        settings = {
            "phases": {
                0: {"descripcion": "Test", "balance_min": 0, "balance_max": 50, "leverage": 1},
            }
        }
        with patch("config.loader") as mock_loader:
            _sync_module_vars(settings)
            assert mock_loader.FASES_ESCALAMIENTO == settings["phases"]

    def test_sync_metrics(self) -> None:
        settings = {
            "metrics": {"enabled": False, "port": 9999, "json_path": "/tmp/test.json"},
        }
        with patch("config.loader") as mock_loader:
            _sync_module_vars(settings)
            assert mock_loader.METRICS_ENABLED is False
            assert mock_loader.METRICS_PORT == 9999
            assert mock_loader.METRICS_JSON_PATH == "/tmp/test.json"


# ── Test de integración: hot-reload del bot ─────────────────────

class TestIntegration:
    """Verifica que una recarga no rompa los componentes del bot."""

    def test_reload_preserves_structure(self, watcher: ConfigWatcher) -> None:
        """Recargar el mismo contenido no debe perder keys."""
        before = set(watcher._settings.keys())
        watcher.reload()
        after = set(watcher._settings.keys())
        assert before == after

    def test_reload_empty_section_does_not_crash(
        self, watcher: ConfigWatcher, temp_yaml: Path
    ) -> None:
        """Secciones vacías no deben causar errores."""
        time.sleep(0.01)
        temp_yaml.write_text("symbol: XRPUSDT\n")
        # Esto no debería lanzar excepción aunque falten secciones
        watcher.reload()
        assert watcher._settings.get("symbol") == "XRPUSDT"
