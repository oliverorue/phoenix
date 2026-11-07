"""Tests del build monitor-only: la ejecución de órdenes está deshabilitada."""

from unittest.mock import AsyncMock, patch

import pytest


def test_monitor_only_flag_is_true():
    from config.loader import MONITOR_ONLY

    assert MONITOR_ONLY is True


def test_dashboard_control_disabled_in_monitor_only():
    import bot.dashboard.bot_controller as controller_module

    with patch.object(controller_module, "MONITOR_ONLY", True):
        assert controller_module.BotController.control_allowed() is False


@pytest.mark.asyncio
async def test_trading_bot_run_refuses_without_allow_trading():
    import bot.main as main_module

    bot = main_module.TradingBot.__new__(main_module.TradingBot)
    with patch.object(main_module, "MONITOR_ONLY", True):
        with pytest.raises(RuntimeError, match="monitor-only"):
            await bot.run()


@pytest.mark.asyncio
async def test_main_delegates_to_monitor_in_monitor_only():
    import bot.main as main_module

    with patch.object(main_module, "MONITOR_ONLY", True), \
            patch("bot.monitor.run_from_config", new=AsyncMock()) as mock_run:
        await main_module.main()

    mock_run.assert_awaited_once()
