"""Tests de seguridad del control del bot desde el dashboard (Fase 0)."""


def test_control_allowed_by_default(monkeypatch):
    monkeypatch.delenv("PHOENIX_DASHBOARD_ALLOW_BOT_CONTROL", raising=False)
    from bot.dashboard.bot_controller import BotController

    ctrl = BotController()
    assert ctrl.control_allowed() is True
    assert ctrl.can_start() is True


def test_start_is_blocked_when_control_disabled(monkeypatch):
    monkeypatch.setenv("PHOENIX_DASHBOARD_ALLOW_BOT_CONTROL", "false")
    from bot.dashboard.bot_controller import BotController

    ctrl = BotController()
    assert ctrl.control_allowed() is False
    assert ctrl.can_start() is False
    assert ctrl.start() is False
    assert ctrl._thread is None
