"""
Sistema de resiliencia para llamadas API:
- Circuit Breaker
- Retry con backoff exponencial
- Rate Limiter proactivo (token bucket)
"""

import time
import random
import asyncio
import functools
from datetime import datetime, timezone
from typing import Callable, Any, Optional
from loguru import logger
from bot.core.exceptions import CircuitBreakerOpen

# Valores por defecto (se pueden sobrescribir al instanciar)
CIRCUIT_BREAKER_MAX_FAILURES = 5
CIRCUIT_BREAKER_COOLDOWN_MIN = 15
API_RETRY_MAX = 3
API_RETRY_BASE_DELAY = 1.0
API_RETRY_MAX_DELAY = 30.0

# Rate limiter global (singleton) — se inicializa lazy
_GLOBAL_RATE_LIMITER: Any = None

def get_rate_limiter(tokens_per_second: Optional[float] = None,
                     max_tokens: Optional[float] = None) -> Any:
    """
    Retorna el rate limiter global (singleton).
    Usa valores de config/loader.py como defaults si no se especifican.
    """
    global _GLOBAL_RATE_LIMITER
    if _GLOBAL_RATE_LIMITER is None:
        from bot.core.rate_limiter import RateLimiter
        try:
            from config.loader import RL_ENABLED, RL_TOKENS_PER_SECOND, RL_MAX_TOKENS
            if not RL_ENABLED:
                # Rate limiter deshabilitado en config — devolver dummy
                class _NoopRateLimiter:
                    def acquire(self, block: bool = True) -> bool: return True
                    async def acquire_async(self) -> bool: return True
                    @property
                    def available_tokens(self) -> float: return 999.0
                    @property
                    def is_limited(self) -> bool: return False
                    def reset(self) -> None: pass
                _GLOBAL_RATE_LIMITER = _NoopRateLimiter()
                return _GLOBAL_RATE_LIMITER
            _tps = tokens_per_second or RL_TOKENS_PER_SECOND
            _max = max_tokens or RL_MAX_TOKENS
        except ImportError:
            _tps = tokens_per_second or 5.0
            _max = max_tokens or 10.0
        _GLOBAL_RATE_LIMITER = RateLimiter(
            tokens_per_second=_tps,
            max_tokens=_max,
        )
    return _GLOBAL_RATE_LIMITER


class CircuitBreaker:
    """Protege contra fallos en cascada."""

    def __init__(self, max_failures: Optional[int] = None, cooldown_minutes: Optional[float] = None) -> None:
        self.max_failures = max_failures or CIRCUIT_BREAKER_MAX_FAILURES
        self.cooldown_minutes = cooldown_minutes or CIRCUIT_BREAKER_COOLDOWN_MIN
        self.failure_count = 0
        self.last_failure_time: Optional[datetime] = None
        self.is_open: bool = False

    def record_failure(self) -> None:
        self.failure_count += 1
        self.last_failure_time = datetime.now(timezone.utc)

        if self.failure_count >= self.max_failures:
            self.is_open = True
            logger.critical(
                f"🔴 CIRCUIT BREAKER ABIERTO | "
                f"{self.failure_count} fallos | Pausa {self.cooldown_minutes} min"
            )

    def record_success(self) -> None:
        if self.failure_count > 0:
            logger.info(f"🟢 Circuit breaker reseteado. Fallos previos: {self.failure_count}")
        self.failure_count = 0
        self.is_open = False

    def can_execute(self) -> bool:
        if not self.is_open:
            return True

        if self.last_failure_time:
            elapsed = (datetime.now(timezone.utc) - self.last_failure_time).total_seconds()
            if elapsed >= (self.cooldown_minutes * 60):
                logger.info(f"🟡 Circuit breaker semi-abierto: probando tras {elapsed/60:.1f} min")
                self.is_open = False
                self.failure_count = 0
                return True

        return False

    def get_remaining_cooldown(self) -> float:
        if not self.is_open or not self.last_failure_time:
            return 0
        elapsed = (datetime.now(timezone.utc) - self.last_failure_time).total_seconds()
        return max(0, (self.cooldown_minutes * 60) - elapsed)

def api_retry(max_retries: Optional[int] = None, base_delay: Optional[float] = None,
              max_delay: Optional[float] = None, critical: bool = False,
              use_rate_limiter: bool = True) -> Callable[..., Any]:
    """
    Decorador para funciones que llaman a la API de Bybit.

    Args:
        use_rate_limiter: Si True (default), adquiere token del RateLimiter global
                          antes de cada intento para prevenir 429.
    """
    _max_retries = max_retries or API_RETRY_MAX
    _base_delay = base_delay or API_RETRY_BASE_DELAY
    _max_delay = max_delay or API_RETRY_MAX_DELAY

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            cb = getattr(self, 'circuit_breaker', None)
            rl = get_rate_limiter() if use_rate_limiter else None

            if critical and cb and not cb.can_execute():
                remaining = cb.get_remaining_cooldown()
                logger.error(f"⛔ Circuit breaker bloqueó {func.__name__}. {remaining/60:.1f}min restantes")
                raise CircuitBreakerOpen(f"Circuit breaker activo. Reintentar en {remaining/60:.1f}min")

            last_exception = None
            delay = _base_delay

            for attempt in range(_max_retries + 1):
                # Adquirir token del rate limiter antes de cada intento
                if rl is not None:
                    rl.acquire(block=True)

                try:
                    result = func(self, *args, **kwargs)

                    if attempt > 0:
                        logger.info(f"✅ {func.__name__} exitoso tras {attempt} reintentos")

                    if cb:
                        cb.record_success()
                    return result

                except Exception as e:
                    last_exception = e
                    error_str = str(e).lower()

                    is_retryable = any(code in error_str for code in [
                        "500", "502", "503", "504", "timeout", "timed out",
                        "connection", "429", "rate limit", "too many requests"
                    ])

                    logger.warning(
                        f"⚠️ {func.__name__} - Intento {attempt+1}/{_max_retries+1} fallido | "
                        f"Error: {str(e)[:150]}"
                    )

                    if not is_retryable and attempt >= _max_retries:
                        if cb:
                            cb.record_failure()
                        raise

                    if attempt >= _max_retries:
                        break

                    jitter = random.uniform(0, delay * 0.1)
                    sleep_time = min(delay + jitter, _max_delay)

                    if "rate limit" in error_str or "429" in error_str:
                        sleep_time = max(sleep_time, 10.0)

                    time.sleep(sleep_time)
                    delay = min(delay * 2, _max_delay)

            if cb:
                cb.record_failure()
            logger.error(f"❌ {func.__name__} falló tras {_max_retries+1} intentos")
            assert last_exception is not None
            raise last_exception

        return wrapper
    return decorator


def api_retry_async(max_retries: Optional[int] = None, base_delay: Optional[float] = None,
                    max_delay: Optional[float] = None, critical: bool = False,
                    use_rate_limiter: bool = True) -> Callable[..., Any]:
    """
    Decorador asíncrono para funciones que llaman a la API de Bybit.

    Args:
        use_rate_limiter: Si True (default), adquiere token del RateLimiter global
                          antes de cada intento para prevenir 429.
    """
    _max_retries = max_retries or API_RETRY_MAX
    _base_delay = base_delay or API_RETRY_BASE_DELAY
    _max_delay = max_delay or API_RETRY_MAX_DELAY

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            cb = getattr(self, 'circuit_breaker', None)
            rl = get_rate_limiter() if use_rate_limiter else None

            if critical and cb and not cb.can_execute():
                remaining = cb.get_remaining_cooldown()
                logger.error(f"⛔ Circuit breaker bloqueó {func.__name__}. {remaining/60:.1f}min restantes")
                raise CircuitBreakerOpen(f"Circuit breaker activo. Reintentar en {remaining/60:.1f}min")

            last_exception = None
            delay = _base_delay

            for attempt in range(_max_retries + 1):
                # Adquirir token del rate limiter antes de cada intento
                if rl is not None:
                    await rl.acquire_async()

                try:
                    result = await func(self, *args, **kwargs)

                    if attempt > 0:
                        logger.info(f"✅ {func.__name__} exitoso tras {attempt} reintentos")

                    if cb:
                        cb.record_success()
                    return result

                except Exception as e:
                    last_exception = e
                    error_str = str(e).lower()

                    is_retryable = any(code in error_str for code in [
                        "500", "502", "503", "504", "timeout", "timed out",
                        "connection", "429", "rate limit", "too many requests"
                    ])

                    logger.warning(
                        f"⚠️ {func.__name__} - Intento {attempt+1}/{_max_retries+1} fallido | "
                        f"Error: {str(e)[:150]}"
                    )

                    if not is_retryable and attempt >= _max_retries:
                        if cb:
                            cb.record_failure()
                        raise

                    if attempt >= _max_retries:
                        break

                    jitter = random.uniform(0, delay * 0.1)
                    sleep_time = min(delay + jitter, _max_delay)

                    if "rate limit" in error_str or "429" in error_str:
                        sleep_time = max(sleep_time, 10.0)

                    await asyncio.sleep(sleep_time)
                    delay = min(delay * 2, _max_delay)

            if cb:
                cb.record_failure()
            logger.error(f"❌ {func.__name__} falló tras {_max_retries+1} intentos")
            assert last_exception is not None
            raise last_exception

        return wrapper
    return decorator
