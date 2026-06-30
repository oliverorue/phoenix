"""
Rate limiter proactivo con algoritmo Token Bucket.
Previene errores 429 (rate limit) antes de que ocurran.
"""

import time
import asyncio
import threading
from typing import Any, Callable, Optional
from loguru import logger

# Valores por defecto
DEFAULT_TOKENS_PER_SECOND = 5.0   # 5 calls/segundo
DEFAULT_MAX_TOKENS = 10.0         # burst máximo
DEFAULT_TIMEOUT = 30.0            # segundos máximo esperando un token


class RateLimiter:
    """
    Token Bucket thread-safe y async-safe.

    Modos de uso:
        limiter = RateLimiter(tokens_per_second=5, max_tokens=10)

        # Sync (thread-safe)
        limiter.acquire()         # Bloquea hasta tener token
        limiter.acquire(block=False)  # No bloquea, retorna True/False

        # Async (asyncio)
        await limiter.acquire_async()

        # Context manager
        with limiter:
            api_call()

        # Decorator
        @limiter
        def api_call(): ...
    """

    def __init__(self, tokens_per_second: Optional[float] = None,
                 max_tokens: Optional[float] = None,
                 timeout: Optional[float] = None):
        self._tps = tokens_per_second or DEFAULT_TOKENS_PER_SECOND
        self._max_tokens = max_tokens or DEFAULT_MAX_TOKENS
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._tokens = self._max_tokens
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    # ── Internals ──────────────────────────────────────────

    def _refill(self) -> None:
        """Recarga tokens según el tiempo transcurrido."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._max_tokens, self._tokens + elapsed * self._tps)
        self._last_refill = now

    def _consume(self) -> bool:
        """Intenta consumir 1 token. Retorna True si había disponible."""
        self._refill()
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False

    # ── API pública sync ───────────────────────────────────

    def acquire(self, block: bool = True) -> bool:
        """
        Adquiere un token.

        Args:
            block: Si True, bloquea hasta obtener token o timeout.
                   Si False, retorna inmediatamente.

        Returns:
            True si se obtuvo el token, False si no.
        """
        if not block:
            with self._lock:
                return self._consume()

        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._consume():
                    return True
            # Esperar antes de reintentar
            sleep_time = min(0.05, deadline - time.monotonic())
            if sleep_time > 0:
                time.sleep(sleep_time)

        logger.warning(f"⏳ RateLimiter: timeout tras {self._timeout}s esperando token")
        return False

    # ── API pública async ──────────────────────────────────

    async def acquire_async(self) -> bool:
        """
        Versión async de acquire() — no bloquea el event loop.
        """
        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._consume():
                    return True
            await asyncio.sleep(0.05)

        logger.warning(f"⏳ RateLimiter: timeout async tras {self._timeout}s esperando token")
        return False

    # ── Context manager ────────────────────────────────────

    def __enter__(self) -> 'RateLimiter':
        self.acquire(block=True)
        return self

    def __exit__(self, exc_type: Optional[type[BaseException]], exc_val: Optional[BaseException], exc_tb: object) -> None:
        pass

    async def __aenter__(self) -> 'RateLimiter':
        await self.acquire_async()
        return self

    async def __aexit__(self, exc_type: Optional[type[BaseException]], exc_val: Optional[BaseException], exc_tb: object) -> None:
        pass

    # ── Decorator ──────────────────────────────────────────

    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """Usa el rate limiter como decorador sync."""
        import functools

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            self.acquire(block=True)
            return func(*args, **kwargs)
        return wrapper

    # ── Estadísticas ───────────────────────────────────────

    @property
    def available_tokens(self) -> float:
        """Tokens disponibles actualmente (lectura aproximada)."""
        with self._lock:
            self._refill()
            return self._tokens

    @property
    def is_limited(self) -> bool:
        """True si no hay tokens disponibles."""
        return self.available_tokens < 1.0

    def reset(self) -> None:
        """Reinicia el bucket a su capacidad máxima."""
        with self._lock:
            self._tokens = self._max_tokens
            self._last_refill = time.monotonic()

    def __repr__(self) -> str:
        return (f"RateLimiter(tps={self._tps}, max={self._max_tokens}, "
                f"available={self.available_tokens:.1f})")
