"""Tests para HealthMonitor (FASE 5.2)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
from datetime import timezone


@pytest.fixture
def mock_data_feed():
    feed = MagicMock()
    feed.get_balance = AsyncMock(return_value=150.0)
    feed.get_all_latency_stats = MagicMock(return_value={})
    return feed


@pytest.fixture
def health_monitor(mock_data_feed):
    from bot.core.health import HealthMonitor
    return HealthMonitor(data_feed=mock_data_feed, notifier=None, interval=300)


class TestHealthMonitorInit:
    """Verifica la inicialización del HealthMonitor."""

    def test_init_with_notifier(self, mock_data_feed):
        from bot.core.health import HealthMonitor
        notifier = MagicMock()
        hm = HealthMonitor(data_feed=mock_data_feed, notifier=notifier, interval=60)
        assert hm.data_feed is mock_data_feed
        assert hm.notifier is notifier
        assert hm.interval == 60
        assert hm._consecutive_api_failures == 0
        assert hm._consecutive_data_failures == 0

    def test_init_without_notifier(self, mock_data_feed):
        from bot.core.health import HealthMonitor
        hm = HealthMonitor(data_feed=mock_data_feed, notifier=None, interval=300)
        assert hm.notifier is None
        assert hm.interval == 300

    def test_default_latency_thresholds(self, mock_data_feed):
        from bot.core.health import HealthMonitor
        hm = HealthMonitor(data_feed=mock_data_feed)
        assert hm.latency_warning_p95 == 2.0
        assert hm.latency_critical_p95 == 5.0


class TestHealthMonitorCheckSystem:
    """Verifica el chequeo de sistema (CPU/memoria)."""

    @pytest.mark.asyncio
    async def test_system_ok(self, health_monitor):
        with patch("bot.core.health.psutil.Process") as mock_proc:
            proc = MagicMock()
            proc.cpu_percent.return_value = 30.0
            proc.memory_info.return_value.rss = 100 * 1024 * 1024  # 100 MB
            mock_proc.return_value = proc

            result = await health_monitor._check_system()
            assert result is True

    @pytest.mark.asyncio
    async def test_system_high_cpu_returns_false(self, health_monitor):
        with patch("bot.core.health.psutil.Process") as mock_proc:
            proc = MagicMock()
            proc.cpu_percent.return_value = 90.0
            proc.memory_info.return_value.rss = 100 * 1024 * 1024
            mock_proc.return_value = proc

            result = await health_monitor._check_system()
            assert result is False

    @pytest.mark.asyncio
    async def test_system_high_memory_returns_false(self, health_monitor):
        with patch("bot.core.health.psutil.Process") as mock_proc:
            proc = MagicMock()
            proc.cpu_percent.return_value = 30.0
            proc.memory_info.return_value.rss = 600 * 1024 * 1024  # 600 MB
            mock_proc.return_value = proc

            result = await health_monitor._check_system()
            assert result is False

    @pytest.mark.asyncio
    async def test_system_exception_returns_true(self, health_monitor):
        with patch("bot.core.health.psutil.Process", side_effect=Exception("fail")):
            result = await health_monitor._check_system()
            assert result is True  # No marcar fallo por error de monitoreo


class TestHealthMonitorCheckAPI:
    """Verifica el chequeo de API."""

    @pytest.mark.asyncio
    async def test_api_ok(self, health_monitor):
        result = await health_monitor._check_api()
        assert result is True

    @pytest.mark.asyncio
    async def test_api_failure_returns_false(self, health_monitor):
        health_monitor.data_feed.get_balance.side_effect = Exception("API error")
        result = await health_monitor._check_api()
        assert result is False


class TestHealthMonitorCheckData:
    """Verifica el chequeo de datos."""

    @pytest.mark.asyncio
    async def test_data_ok(self, health_monitor):
        df = pd.DataFrame({
            "timestamp": [pd.Timestamp.now(tz=timezone.utc) - pd.Timedelta(minutes=5)],
            "close": [0.50],
        })
        health_monitor.data_feed.current_15m = AsyncMock(return_value=df)

        result = await health_monitor._check_data()
        assert result is True

    @pytest.mark.asyncio
    async def test_data_empty_returns_false(self, health_monitor):
        health_monitor.data_feed.current_15m = AsyncMock(return_value=pd.DataFrame())
        result = await health_monitor._check_data()
        assert result is False

    @pytest.mark.asyncio
    async def test_data_stale_returns_false(self, health_monitor):
        df = pd.DataFrame({
            "timestamp": [pd.Timestamp.now(tz=timezone.utc) - pd.Timedelta(minutes=30)],
            "close": [0.50],
        })
        health_monitor.data_feed.current_15m = AsyncMock(return_value=df)
        result = await health_monitor._check_data()
        assert result is False

    @pytest.mark.asyncio
    async def test_data_exception_returns_false(self, health_monitor):
        health_monitor.data_feed.current_15m = AsyncMock(side_effect=Exception("fail"))
        result = await health_monitor._check_data()
        assert result is False


class TestHealthMonitorCheckLatency:
    """Verifica el chequeo de latencia."""

    @pytest.mark.asyncio
    async def test_latency_no_data_returns_true(self, health_monitor):
        health_monitor.data_feed.get_all_latency_stats.return_value = {}
        result = await health_monitor._check_latency()
        assert result is True

    @pytest.mark.asyncio
    async def test_latency_ok(self, health_monitor):
        health_monitor.data_feed.get_all_latency_stats.return_value = {
            "get_klines": {"p95": 0.5, "max": 0.8, "count": 10},
        }
        result = await health_monitor._check_latency()
        assert result is True

    @pytest.mark.asyncio
    async def test_latency_warning_returns_false(self, health_monitor):
        health_monitor.data_feed.get_all_latency_stats.return_value = {
            "get_klines": {"p95": 3.0, "max": 4.0, "count": 10},
        }
        result = await health_monitor._check_latency()
        assert result is False

    @pytest.mark.asyncio
    async def test_latency_critical_returns_false(self, health_monitor):
        health_monitor.data_feed.get_all_latency_stats.return_value = {
            "get_klines": {"p95": 6.0, "max": 8.0, "count": 10},
        }
        result = await health_monitor._check_latency()
        assert result is False

    @pytest.mark.asyncio
    async def test_latency_few_samples_skipped(self, health_monitor):
        """Con menos de 3 muestras, no debe marcar fallo."""
        health_monitor.data_feed.get_all_latency_stats.return_value = {
            "get_klines": {"p95": 10.0, "max": 12.0, "count": 2},
        }
        result = await health_monitor._check_latency()
        assert result is True


class TestHealthMonitorNotify:
    """Verifica que las notificaciones se envían ante fallos repetidos."""

    @pytest.mark.asyncio
    async def test_notify_after_3_api_failures(self, mock_data_feed):
        from bot.core.health import HealthMonitor
        notifier = MagicMock()
        hm = HealthMonitor(data_feed=mock_data_feed, notifier=notifier, interval=300)

        # Simular 3 fallos de API
        hm._consecutive_api_failures = 2
        mock_data_feed.get_balance.side_effect = Exception("fail")

        await hm._run_checks()

        assert hm._consecutive_api_failures == 3
        notifier.send.assert_called_once()
        assert "API" in notifier.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_notify_after_2_data_failures(self, mock_data_feed):
        from bot.core.health import HealthMonitor
        notifier = MagicMock()
        hm = HealthMonitor(data_feed=mock_data_feed, notifier=notifier, interval=300)

        hm._consecutive_data_failures = 1
        mock_data_feed.current_15m = AsyncMock(side_effect=Exception("fail"))

        await hm._run_checks()

        assert hm._consecutive_data_failures == 2
        notifier.send.assert_called_once()
        assert "Datos" in notifier.send.call_args[0][0]

    @pytest.mark.asyncio
    async def test_no_notification_when_no_notifier(self, health_monitor):
        """Si no hay notifier, no debe lanzar excepción."""
        health_monitor.data_feed.get_balance.side_effect = Exception("fail")
        health_monitor._consecutive_api_failures = 2

        # No debe lanzar
        await health_monitor._run_checks()
        assert health_monitor._consecutive_api_failures == 3


class TestHealthMonitorRun:
    """Verifica el loop principal run()."""

    @pytest.mark.asyncio
    async def test_run_stops_on_event(self, health_monitor):
        stop_event = asyncio.Event()
        health_monitor.interval = 0.1

        with patch.object(health_monitor, "_run_checks", new=AsyncMock()) as mock_checks:
            # Detener después de un ciclo
            async def _run_with_stop():
                stop_event.set()

            mock_checks.side_effect = _run_with_stop
            await health_monitor.run(stop_event)
            assert mock_checks.called

    @pytest.mark.asyncio
    async def test_run_continues_on_exception(self, health_monitor):
        stop_event = asyncio.Event()

        # Usar intervalo pequeño para no esperar 300s reales
        health_monitor.interval = 0.1

        call_count = 0

        async def _fail_once():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("check error")
            stop_event.set()

        with patch.object(health_monitor, "_run_checks", new=AsyncMock(side_effect=_fail_once)):
            await health_monitor.run(stop_event)
            assert call_count == 2  # falla, luego se detiene


import asyncio
