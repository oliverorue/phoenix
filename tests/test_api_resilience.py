"""
Tests para módulo de resiliencia: CircuitBreaker, RateLimiter, decoradores.
"""

import pytest
import time
from unittest.mock import MagicMock, patch, AsyncMock
from bot.core.api_resilience import (
    CircuitBreaker, api_retry, api_retry_async, get_rate_limiter
)
from bot.core.rate_limiter import RateLimiter


# ─── CircuitBreaker ───────────────────────────────────────────────

class TestCircuitBreaker:
    def test_initial_state(self):
        cb = CircuitBreaker(max_failures=3, cooldown_minutes=5)
        assert cb.can_execute() is True
        assert cb.get_remaining_cooldown() == 0.0

    def test_record_failure_opens_after_threshold(self):
        cb = CircuitBreaker(max_failures=3, cooldown_minutes=60)
        cb.record_failure()
        cb.record_failure()
        assert cb.can_execute() is True  # Aún no llega al límite
        cb.record_failure()
        assert cb.can_execute() is False  # Circuito abierto

    def test_record_success_resets(self):
        cb = CircuitBreaker(max_failures=3, cooldown_minutes=60)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # Resetea
        assert cb.can_execute() is True
        cb.record_failure()
        cb.record_failure()
        cb.record_failure()
        assert cb.can_execute() is False

    def test_cooldown_after_open(self):
        cb = CircuitBreaker(max_failures=1, cooldown_minutes=0.001)  # ~60ms
        cb.record_failure()
        assert cb.can_execute() is False
        time.sleep(0.1)
        assert cb.can_execute() is True  # Cooldown expiró

    def test_remaining_cooldown(self):
        cb = CircuitBreaker(max_failures=1, cooldown_minutes=60)
        cb.record_failure()
        remaining = cb.get_remaining_cooldown()
        assert remaining > 0

    def test_default_values(self):
        cb = CircuitBreaker(max_failures=5, cooldown_minutes=15)
        assert cb.max_failures == 5
        assert cb.cooldown_minutes == 15


# ─── RateLimiter ──────────────────────────────────────────────────

class TestRateLimiter:
    def test_init(self):
        rl = RateLimiter(tokens_per_second=10.0, max_tokens=20.0)
        assert rl._max_tokens == 20.0
        assert rl.available_tokens == pytest.approx(20.0, abs=0.5)

    def test_is_limited_when_empty(self):
        rl = RateLimiter(tokens_per_second=1.0, max_tokens=1.0)
        rl.acquire(block=False)  # Consume el único token
        assert rl.is_limited is True

    def test_is_not_limited_with_tokens(self):
        rl = RateLimiter(tokens_per_second=10.0, max_tokens=10.0)
        assert rl.is_limited is False

    def test_acquire_block_false_returns_false_when_empty(self):
        rl = RateLimiter(tokens_per_second=1.0, max_tokens=1.0)
        rl.acquire(block=False)  # Consume
        result = rl.acquire(block=False)
        assert result is False

    def test_acquire_block_true_waits(self):
        rl = RateLimiter(tokens_per_second=100.0, max_tokens=1.0)
        rl.acquire(block=False)  # Consume (1 token)
        start = time.time()
        result = rl.acquire(block=True)  # Debe esperar ~0.01s
        elapsed = time.time() - start
        assert result is True
        assert elapsed >= 0.005  # Algo de espera

    def test_reset_restores_tokens(self):
        rl = RateLimiter(tokens_per_second=1.0, max_tokens=1.0)
        rl.acquire(block=False)
        assert rl.is_limited
        rl.reset()
        assert rl.available_tokens == pytest.approx(1.0, abs=0.1)
        assert rl.is_limited is False

    def test_context_manager(self):
        rl = RateLimiter(tokens_per_second=100.0, max_tokens=10.0)
        with rl:
            pass  # No debe lanzar excepción
        assert rl.available_tokens < 10.0  # Se consumió al menos 1

    @pytest.mark.asyncio
    async def test_acquire_async(self):
        rl = RateLimiter(tokens_per_second=100.0, max_tokens=1.0)
        rl.acquire(block=False)  # Consume
        result = await rl.acquire_async()
        assert result is True

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        rl = RateLimiter(tokens_per_second=100.0, max_tokens=10.0)
        async with rl:
            pass
        assert rl.available_tokens < 10.0


# ─── Decorador api_retry ──────────────────────────────────────────

class TestApiRetryDecorator:
    def test_retry_on_failure(self):
        """Verifica que reintenta N veces antes de fallar."""
        mock_method = MagicMock()
        mock_method.side_effect = [ValueError("fail1"), ValueError("fail2"), "success"]

        @api_retry(max_retries=2, base_delay=0.01, use_rate_limiter=False)
        def decorated(self, *args, **kwargs):
            return mock_method(*args, **kwargs)

        result = decorated("self")
        assert result == "success"
        # max_retries=2 → intento inicial + 2 reintentos = 3 llamadas
        assert mock_method.call_count == 3

    def test_retry_exhausted_raises(self):
        """Si se agotan los reintentos, relanza la última excepción."""
        mock_method = MagicMock()
        mock_method.side_effect = ValueError("always fail")

        @api_retry(max_retries=1, base_delay=0.01, use_rate_limiter=False)
        def decorated(self, *args, **kwargs):
            return mock_method(*args, **kwargs)

        with pytest.raises(ValueError, match="always fail"):
            decorated("self")
        # max_retries=1 → intento inicial + 1 reintento = 2 llamadas
        assert mock_method.call_count == 2

    def test_retry_success_first_try(self):
        """Si la primera llamada funciona, no reintenta."""
        mock_method = MagicMock(return_value="ok")

        @api_retry(max_retries=3, base_delay=0.01, use_rate_limiter=False)
        def decorated(self, *args, **kwargs):
            return mock_method(*args, **kwargs)

        result = decorated("self")
        assert result == "ok"
        assert mock_method.call_count == 1

    @patch("bot.core.api_resilience.get_rate_limiter")
    def test_rate_limiter_called(self, mock_get_rl):
        """Con use_rate_limiter=True, llama al rate limiter."""
        mock_rl = MagicMock()
        mock_rl.acquire.return_value = True
        mock_get_rl.return_value = mock_rl

        mock_method = MagicMock(return_value="ok")

        @api_retry(max_retries=1, base_delay=0.01, use_rate_limiter=True)
        def decorated(self, *args, **kwargs):
            return mock_method(*args, **kwargs)

        decorated("self")
        mock_rl.acquire.assert_called_once()


# ─── Decorador api_retry_async ────────────────────────────────────

class TestApiRetryAsyncDecorator:
    @pytest.mark.asyncio
    async def test_retry_async_on_failure(self):
        mock_method = AsyncMock()
        mock_method.side_effect = [ValueError("fail1"), ValueError("fail2"), "success"]

        @api_retry_async(max_retries=2, base_delay=0.01, use_rate_limiter=False)
        async def decorated(self, *args, **kwargs):
            return await mock_method(*args, **kwargs)

        result = await decorated("self")
        assert result == "success"
        assert mock_method.call_count == 3

    @pytest.mark.asyncio
    async def test_retry_async_exhausted_raises(self):
        mock_method = AsyncMock()
        mock_method.side_effect = ValueError("always fail")

        @api_retry_async(max_retries=1, base_delay=0.01, use_rate_limiter=False)
        async def decorated(self, *args, **kwargs):
            return await mock_method(*args, **kwargs)

        with pytest.raises(ValueError, match="always fail"):
            await decorated("self")
        assert mock_method.call_count == 2


# ─── get_rate_limiter singleton ───────────────────────────────────

class TestGetRateLimiter:
    def test_returns_singleton(self):
        rl1 = get_rate_limiter()
        rl2 = get_rate_limiter()
        assert rl1 is rl2
