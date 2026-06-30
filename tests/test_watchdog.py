"""Tests para watchdog.py — helpers sin subprocess (FASE 5.2)."""

import pytest
from unittest.mock import MagicMock, patch, AsyncMock


class TestWatchdogGetNotifier:
    """Verifica _get_notifier()."""

    def test_get_notifier_returns_none_on_import_error(self):
        import bot.watchdog as wd
        wd._notifier = None

        from bot.core import notifier as cn
        with patch.object(cn, "Notifier", new=None):
            result = wd._get_notifier()
            assert result is None

    def test_get_notifier_returns_instance(self):
        import bot.watchdog as wd
        wd._notifier = None

        from bot.core import notifier as cn
        mock_instance = MagicMock()
        with patch.object(cn, "Notifier", return_value=mock_instance):
            result = wd._get_notifier()
            assert result is mock_instance
            assert wd._notifier is mock_instance


class TestWatchdogCheckSystem:
    """Verifica check_system()."""

    def test_check_system_ok(self):
        with patch("bot.watchdog.psutil.Process") as mock_proc:
            proc = MagicMock()
            proc.cpu_percent.return_value = 30.0
            proc.memory_info.return_value.rss = 100 * 1024 * 1024
            mock_proc.return_value = proc

            from bot.watchdog import check_system
            result = check_system()
            assert result is True

    def test_check_system_high_cpu_still_returns_true(self):
        """El watchdog no reinicia por CPU alta, solo loggea."""
        with patch("bot.watchdog.psutil.Process") as mock_proc:
            proc = MagicMock()
            proc.cpu_percent.return_value = 95.0
            proc.memory_info.return_value.rss = 100 * 1024 * 1024
            mock_proc.return_value = proc

            from bot.watchdog import check_system
            result = check_system()
            assert result is True

    def test_check_system_exception_returns_true(self):
        with patch("bot.watchdog.psutil.Process", side_effect=Exception("fail")):
            from bot.watchdog import check_system
            result = check_system()
            assert result is True


class TestWatchdogCheckAPI:
    """Verifica check_api() wrapper sync."""

    def test_check_api_success(self):
        """check_api() ejecuta _check_api_async() con asyncio.run()."""
        with patch("bot.watchdog._check_api_async", new_callable=AsyncMock, return_value=True) as mock_async:
            from bot.watchdog import check_api
            result = check_api()
            assert result is True
            mock_async.assert_called_once()

    def test_check_api_failure(self):
        with patch("bot.watchdog._check_api_async", side_effect=Exception("fail")):
            from bot.watchdog import check_api
            result = check_api()
            assert result is False


class TestWatchdogCheckData:
    """Verifica check_data() wrapper sync."""

    def test_check_data_success(self):
        with patch("bot.watchdog._check_data_async", new_callable=AsyncMock, return_value=True) as mock_async:
            from bot.watchdog import check_data
            result = check_data()
            assert result is True
            mock_async.assert_called_once()

    def test_check_data_failure(self):
        with patch("bot.watchdog._check_data_async", side_effect=Exception("fail")):
            from bot.watchdog import check_data
            result = check_data()
            assert result is False


class TestWatchdogAsyncChecks:
    """Verifica las funciones async internas con mocks."""

    @pytest.mark.asyncio
    async def test_check_api_async_calls_get_balance(self):
        with patch("bot.core.data_feed.DataFeed") as mock_feed_cls:
            feed = MagicMock()
            feed.get_balance = AsyncMock(return_value=150.0)
            mock_feed_cls.return_value = feed

            from bot.watchdog import _check_api_async
            result = await _check_api_async()
            assert result is True

    @pytest.mark.asyncio
    async def test_check_api_async_zero_balance(self):
        with patch("bot.core.data_feed.DataFeed") as mock_feed_cls:
            feed = MagicMock()
            feed.get_balance = AsyncMock(return_value=0.0)
            mock_feed_cls.return_value = feed

            from bot.watchdog import _check_api_async
            result = await _check_api_async()
            assert result is True

    @pytest.mark.asyncio
    async def test_check_data_async_with_recent_data(self):
        import pandas as pd
        from datetime import timezone

        df = pd.DataFrame({
            "timestamp": [pd.Timestamp.now(tz=timezone.utc) - pd.Timedelta(minutes=5)],
            "close": [0.50],
        })

        with patch("bot.core.data_feed.DataFeed") as mock_feed_cls:
            feed = MagicMock()
            feed.current_15m = AsyncMock(return_value=df)
            mock_feed_cls.return_value = feed

            from bot.watchdog import _check_data_async
            result = await _check_data_async()
            assert result is True

    @pytest.mark.asyncio
    async def test_check_data_async_stale_data_returns_false(self):
        import pandas as pd
        from datetime import timezone

        df = pd.DataFrame({
            "timestamp": [pd.Timestamp.now(tz=timezone.utc) - pd.Timedelta(minutes=30)],
            "close": [0.50],
        })

        with patch("bot.core.data_feed.DataFeed") as mock_feed_cls:
            feed = MagicMock()
            feed.current_15m = AsyncMock(return_value=df)
            mock_feed_cls.return_value = feed

            from bot.watchdog import _check_data_async
            result = await _check_data_async()
            assert result is False

    @pytest.mark.asyncio
    async def test_check_data_async_empty_df_returns_false(self):
        import pandas as pd

        with patch("bot.core.data_feed.DataFeed") as mock_feed_cls:
            feed = MagicMock()
            feed.current_15m = AsyncMock(return_value=pd.DataFrame())
            mock_feed_cls.return_value = feed

            from bot.watchdog import _check_data_async
            result = await _check_data_async()
            assert result is False


class TestWatchdogConstants:
    """Verifica las constantes del módulo."""

    def test_constants(self):
        from bot.watchdog import MAX_CRASHES, CRASH_WINDOW, RESTART_DELAY, HEALTH_INTERVAL
        assert MAX_CRASHES == 5
        assert CRASH_WINDOW == 600
        assert RESTART_DELAY == 15
        assert HEALTH_INTERVAL == 300
