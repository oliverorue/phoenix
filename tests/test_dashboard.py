"""Tests del controlador del dashboard (monitor-only)."""

from unittest.mock import patch

import pytest


class TestBotController:
    """Verifica BotController (mecánica legacy con control habilitado)."""

    @pytest.fixture(autouse=True)
    def _legacy_control(self):
        import bot.dashboard.bot_controller as controller_module

        with patch.object(controller_module, "MONITOR_ONLY", False):
            yield

    def test_controller_init(self):
        from bot.dashboard.bot_controller import BotController

        ctrl = BotController()
        assert ctrl._thread is None
        assert ctrl.is_running() is False

    def test_start_creates_thread(self):
        from bot.dashboard.bot_controller import BotController

        ctrl = BotController()
        result = ctrl.start()
        assert result is True
        assert ctrl._thread is not None
        assert ctrl._thread.is_alive()
        ctrl.stop()

    def test_start_when_already_running(self):
        from bot.dashboard.bot_controller import BotController

        ctrl = BotController()
        ctrl.start()
        result = ctrl.start()  # segundo start
        assert result is False
        ctrl.stop()

    def test_stop_sets_event(self):
        from bot.dashboard.bot_controller import BotController

        ctrl = BotController()
        ctrl.start()
        result = ctrl.stop()
        assert result is True
        assert ctrl._stop_event.is_set()
        assert ctrl.is_running() is False

    def test_stop_when_not_running(self):
        from bot.dashboard.bot_controller import BotController

        ctrl = BotController()
        result = ctrl.stop()
        assert result is True

    def test_restart_stops_and_starts(self):
        from bot.dashboard.bot_controller import BotController

        ctrl = BotController()
        result = ctrl.restart()
        assert result is True
        ctrl.stop()

    def test_restart_with_config(self, tmp_path):
        from bot.dashboard.bot_controller import BotController

        with patch("bot.dashboard.bot_controller.ROOT_DIR", tmp_path):
            config_dir = tmp_path / "config"
            config_dir.mkdir()
            settings_file = config_dir / "settings.yaml"
            settings_file.write_text("portfolio:\n  symbols: []\n")
            ctrl = BotController()
            result = ctrl.restart_with_config([
                {"symbol": "XRPUSDT", "allocation_pct": 0.5, "active": True}
            ])
            assert result is True
            # Verificar que se guardó en settings.yaml
            import yaml
            with open(settings_file) as f:
                data = yaml.safe_load(f)
            assert data["portfolio"]["symbols"][0]["symbol"] == "XRPUSDT"
            ctrl.stop()

    def test_is_running_after_stop(self):
        from bot.dashboard.bot_controller import BotController

        ctrl = BotController()
        ctrl.start()
        ctrl.stop()
        assert ctrl.is_running() is False

    def test_is_running_thread_dead(self):
        from bot.dashboard.bot_controller import BotController

        ctrl = BotController()
        assert ctrl.is_running() is False
