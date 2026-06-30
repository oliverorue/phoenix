"""Tests para el monitoreo de latencia (FASE 5.1) en DataFeed y HealthMonitor."""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import math

from bot.core.data_feed import DataFeed
from bot.core.health import HealthMonitor


# ── Helpers ────────────────────────────────────────────────────

def make_datafeed_with_latency():
    """Crea un DataFeed con constructor mockeado para probar solo métodos de latencia."""
    with patch("bot.core.data_feed.BinanceFuturesClient"), \
         patch("bot.core.data_feed.CircuitBreaker"):
        feed = DataFeed.__new__(DataFeed)
        feed._latency_samples = {}
        feed._max_latency_samples = 100
        return feed


# ── DataFeed: _record_latency ──────────────────────────────────

class TestRecordLatency:
    """Prueba _record_latency en DataFeed."""

    def test_record_new_endpoint(self):
        feed = make_datafeed_with_latency()
        feed._record_latency("get_balance", 0.15)
        assert "get_balance" in feed._latency_samples
        assert feed._latency_samples["get_balance"] == [0.15]

    def test_record_multiple_samples(self):
        feed = make_datafeed_with_latency()
        for _ in range(5):
            feed._record_latency("get_kline_15", 0.12)
        assert len(feed._latency_samples["get_kline_15"]) == 5
        assert all(v == 0.12 for v in feed._latency_samples["get_kline_15"])

    def test_record_multiple_endpoints(self):
        feed = make_datafeed_with_latency()
        feed._record_latency("ep1", 0.1)
        feed._record_latency("ep2", 0.2)
        assert list(feed._latency_samples.keys()) == ["ep1", "ep2"]
        assert feed._latency_samples["ep1"] == [0.1]
        assert feed._latency_samples["ep2"] == [0.2]

    def test_max_samples_enforced(self):
        """Mantiene máximo de 100 muestras por endpoint (FIFO)."""
        feed = make_datafeed_with_latency()
        for i in range(110):
            feed._record_latency("ep", float(i) / 100)
        assert len(feed._latency_samples["ep"]) == 100
        # Las primeras 10 se descartaron
        assert feed._latency_samples["ep"][0] == 0.10  # el sample 10
        assert feed._latency_samples["ep"][-1] == 1.09  # el sample 109


# ── DataFeed: _calc_stats ──────────────────────────────────────

class TestCalcStats:
    """Prueba _calc_stats en DataFeed."""

    def test_empty_samples(self):
        stats = DataFeed._calc_stats("ep", [])
        assert stats["endpoint"] == "ep"
        assert stats["count"] == 0
        assert stats["min"] == 0.0
        assert stats["max"] == 0.0

    def test_single_sample(self):
        """Con 1 muestra, todos los percentiles son ese valor."""
        stats = DataFeed._calc_stats("ep", [0.5])
        assert stats["count"] == 1
        assert stats["min"] == 0.5
        assert stats["max"] == 0.5
        assert stats["avg"] == 0.5
        assert stats["p50"] == 0.5
        assert stats["p95"] == 0.5
        assert stats["p99"] == 0.5

    def test_percentiles_known_values(self):
        """Verifica cálculos de percentiles con valores conocidos."""
        samples = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
        stats = DataFeed._calc_stats("ep", samples)
        assert stats["count"] == 10
        assert stats["min"] == 0.1
        assert stats["max"] == 1.0
        assert stats["avg"] == 0.55
        assert stats["p50"] == 0.6   # s[int(10*0.50)] = s[5] = 0.6
        assert stats["p95"] == 1.0   # s[int(10*0.95)] = s[9] = 1.0
        assert stats["p99"] == 1.0   # s[int(10*0.99)] = s[9] = 1.0

    def test_percentiles_100_samples(self):
        """Con 100 muestras, verifica p95 y p99 más precisos.
        s[int(n * 0.95)] con n=100 → s[95] (0-indexed) = 0.96"""
        samples = [i / 100 for i in range(1, 101)]  # 0.01 a 1.00
        stats = DataFeed._calc_stats("ep", samples)
        assert stats["count"] == 100
        assert math.isclose(stats["p95"], 0.96, abs_tol=0.01)
        assert math.isclose(stats["p99"], 1.0, abs_tol=0.01)

    def test_avg_calculation(self):
        """Promedio correcto."""
        samples = [1.0, 2.0, 3.0, 4.0]
        stats = DataFeed._calc_stats("ep", samples)
        assert stats["avg"] == 2.5


# ── DataFeed: get_latency_stats ────────────────────────────────

class TestGetLatencyStats:
    """Prueba get_latency_stats en DataFeed."""

    def test_get_stats_single_endpoint(self):
        feed = make_datafeed_with_latency()
        feed._record_latency("ep1", 0.1)
        feed._record_latency("ep1", 0.2)
        stats = feed.get_latency_stats("ep1")
        assert stats["endpoint"] == "ep1"
        assert stats["count"] == 2
        assert stats["min"] == 0.1
        assert stats["max"] == 0.2

    def test_get_stats_unknown_endpoint(self):
        """Endpoint sin muestras → stats vacíos."""
        feed = make_datafeed_with_latency()
        stats = feed.get_latency_stats("no_existe")
        assert stats["count"] == 0
        assert stats["min"] == 0.0

    def test_get_stats_all_endpoints(self):
        feed = make_datafeed_with_latency()
        feed._record_latency("ep1", 0.1)
        feed._record_latency("ep2", 0.2)
        stats = feed.get_latency_stats()
        assert isinstance(stats, dict)
        assert "ep1" in stats
        assert "ep2" in stats
        assert stats["ep1"]["count"] == 1
        assert stats["ep2"]["count"] == 1

    def test_get_stats_empty(self):
        """Sin ningún endpoint → dict vacío."""
        feed = make_datafeed_with_latency()
        stats = feed.get_latency_stats()
        assert stats == {}

    def test_get_all_latency_stats_alias(self):
        """get_all_latency_stats() = get_latency_stats()."""
        feed = make_datafeed_with_latency()
        feed._record_latency("ep", 0.1)
        assert feed.get_all_latency_stats() == feed.get_latency_stats()


# ── HealthMonitor: _check_latency ──────────────────────────────

class TestHealthCheckLatency:
    """Prueba _check_latency en HealthMonitor."""

    @pytest.fixture
    def mock_data_feed(self):
        feed = MagicMock()
        feed.get_all_latency_stats.return_value = {}
        feed.current_15m = AsyncMock()
        return feed

    @pytest.fixture
    def health(self, mock_data_feed):
        h = HealthMonitor(mock_data_feed)
        h._consecutive_latency_warnings = 0
        return h

    @pytest.mark.asyncio
    async def test_no_stats_returns_true(self, health):
        """Sin muestras de latencia → OK."""
        assert await health._check_latency() is True

    @pytest.mark.asyncio
    async def test_all_low_latency_returns_true(self, health, mock_data_feed):
        mock_data_feed.get_all_latency_stats.return_value = {
            "get_kline_15": {
                "endpoint": "get_kline_15", "count": 10,
                "min": 0.1, "max": 0.3, "avg": 0.2,
                "p50": 0.2, "p95": 0.3, "p99": 0.3,
            }
        }
        assert await health._check_latency() is True

    @pytest.mark.asyncio
    async def test_high_p95_triggers_warning(self, health, mock_data_feed):
        """p95 > 2s (latency_warning_p95) → False.
        El contador se incrementa en _run_checks(), no aquí."""
        mock_data_feed.get_all_latency_stats.return_value = {
            "get_kline_15": {
                "endpoint": "get_kline_15", "count": 10,
                "min": 0.1, "max": 3.0, "avg": 1.5,
                "p50": 1.0, "p95": 2.5, "p99": 2.9,
            }
        }
        assert await health._check_latency() is False

    @pytest.mark.asyncio
    async def test_critical_p95_triggers_critical(self, health, mock_data_feed):
        """p95 > 5s (latency_critical_p95) → False."""
        mock_data_feed.get_all_latency_stats.return_value = {
            "get_balance": {
                "endpoint": "get_balance", "count": 10,
                "min": 1.0, "max": 8.0, "avg": 4.0,
                "p50": 3.0, "p95": 6.0, "p99": 7.5,
            }
        }
        assert await health._check_latency() is False

    @pytest.mark.asyncio
    async def test_few_samples_ignored(self, health, mock_data_feed):
        """Menos de 3 samples → no concluyente, se ignora."""
        mock_data_feed.get_all_latency_stats.return_value = {
            "get_kline_15": {
                "endpoint": "get_kline_15", "count": 2,
                "min": 0.5, "max": 8.0, "avg": 4.25,
                "p50": 0.5, "p95": 8.0, "p99": 8.0,
            }
        }
        assert await health._check_latency() is True

    @pytest.mark.asyncio
    async def test_exception_in_check_returns_true(self, health, mock_data_feed):
        """Excepción en _check_latency → retorna True (no falla)."""
        mock_data_feed.get_all_latency_stats.side_effect = Exception("boom")
        assert await health._check_latency() is True

    @pytest.mark.asyncio
    async def test_run_checks_increments_warnings(self, health, mock_data_feed):
        """_run_checks() incrementa el contador si _check_latency() falla."""
        bad_stats = {
            "get_kline_15": {
                "endpoint": "get_kline_15", "count": 10,
                "min": 0.1, "max": 3.0, "avg": 1.5,
                "p50": 1.0, "p95": 3.0, "p99": 2.9,
            }
        }
        mock_data_feed.get_all_latency_stats.return_value = bad_stats

        assert health._consecutive_latency_warnings == 0
        await health._run_checks()
        assert health._consecutive_latency_warnings == 1
        await health._run_checks()
        assert health._consecutive_latency_warnings == 2

    @pytest.mark.asyncio
    async def test_run_checks_resets_warnings(self, health, mock_data_feed):
        """_run_checks() resetea contador cuando latencia vuelve a la normalidad."""
        bad_stats = {
            "get_kline_15": {
                "endpoint": "get_kline_15", "count": 10,
                "min": 0.1, "max": 3.0, "avg": 1.5,
                "p50": 1.0, "p95": 3.0, "p99": 2.9,
            }
        }
        good_stats = {
            "get_kline_15": {
                "endpoint": "get_kline_15", "count": 10,
                "min": 0.1, "max": 0.3, "avg": 0.2,
                "p50": 0.2, "p95": 0.3, "p99": 0.3,
            }
        }

        mock_data_feed.get_all_latency_stats.return_value = bad_stats
        await health._run_checks()
        assert health._consecutive_latency_warnings == 1

        mock_data_feed.get_all_latency_stats.return_value = good_stats
        await health._run_checks()
        assert health._consecutive_latency_warnings == 0
