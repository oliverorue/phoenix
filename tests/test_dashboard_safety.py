"""Tests de seguridad del control del bot desde el dashboard (monitor-only)."""

from unittest.mock import patch


def test_control_disabled_in_monitor_only(monkeypatch):
    monkeypatch.delenv("PHOENIX_DASHBOARD_ALLOW_BOT_CONTROL", raising=False)
    import bot.dashboard.bot_controller as controller_module

    with patch.object(controller_module, "MONITOR_ONLY", True):
        ctrl = controller_module.BotController()
        assert ctrl.control_allowed() is False
        assert ctrl.can_start() is False
        assert ctrl.start() is False
        assert ctrl._thread is None


def test_control_allowed_when_not_monitor_only(monkeypatch):
    monkeypatch.delenv("PHOENIX_DASHBOARD_ALLOW_BOT_CONTROL", raising=False)
    import bot.dashboard.bot_controller as controller_module

    with patch.object(controller_module, "MONITOR_ONLY", False):
        ctrl = controller_module.BotController()
        assert ctrl.control_allowed() is True
        assert ctrl.can_start() is True


def test_start_is_blocked_when_control_disabled(monkeypatch):
    monkeypatch.setenv("PHOENIX_DASHBOARD_ALLOW_BOT_CONTROL", "false")
    import bot.dashboard.bot_controller as controller_module

    with patch.object(controller_module, "MONITOR_ONLY", False):
        ctrl = controller_module.BotController()
        assert ctrl.control_allowed() is False
        assert ctrl.can_start() is False
        assert ctrl.start() is False
        assert ctrl._thread is None
