"""
Proveedor de datos de mercado avanzados (funding rate / open interest).
Proporciona señales complementarias a la estrategia técnica (RSI, ADX, etc.).
Usa BinanceFuturesClient como backend.
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional
from loguru import logger

from config.loader import (
    API_KEY, API_SECRET, TESTNET,
    FUNDING_RATE_ENABLED, FUNDING_RATE_EXTREME_HIGH, FUNDING_RATE_EXTREME_LOW,
    OPEN_INTEREST_ENABLED,
)
from bot.core.exchange_client import BinanceFuturesClient, MarketDataException as ClientMarketDataException
from bot.core.api_resilience import CircuitBreaker, api_retry_async


class MarketDataProvider:
    """
    Obtiene datos de mercado complementarios (funding rate, open interest)
    desde la API de Binance y genera señales de trading.

    Soporta Dependency Injection: se puede mockear inyectando http_session.
    """

    def __init__(self, http_session: Any = None, circuit_breaker: Any = None) -> None:
        """
        Args:
            http_session: instancia de BinanceFuturesClient (opcional).
            circuit_breaker: instancia de CircuitBreaker (opcional).
        """
        if http_session is not None:
            self._client = http_session
        else:
            self._client = BinanceFuturesClient(
                api_key=API_KEY or "",
                api_secret=API_SECRET or "",
                testnet=TESTNET,
            )
        self.circuit_breaker = circuit_breaker or CircuitBreaker(
            max_failures=3, cooldown_minutes=5
        )

        # Cache de funding rate (evita llamadas repetitivas en el mismo ciclo)
        self._funding_cache: dict[str, dict[str, Any]] = {}
        self._oi_cache: dict[str, dict[str, Any]] = {}

    # ── Funding Rate ────────────────────────────────────────

    @api_retry_async(max_retries=2, base_delay=1.0, critical=False, use_rate_limiter=True)
    async def get_funding_rate(self, symbol: str) -> Optional[dict[str, Any]]:
        """
        Obtiene el funding rate actual del símbolo desde Binance.

        Returns:
            dict con keys: symbol, funding_rate, funding_time, o None si falla.
        """
        cache_key = f"{symbol}_funding"
        if cache_key in self._funding_cache:
            return self._funding_cache[cache_key]

        try:
            fr_data = await self._client.get_funding_rate(symbol)
            if fr_data:
                result = {
                    "symbol": symbol,
                    "funding_rate": fr_data["funding_rate"],
                    "funding_time": datetime.fromtimestamp(
                        fr_data["funding_time"] / 1000, tz=timezone.utc
                    ) if fr_data.get("funding_time") else None,
                }
                # Cachear por 60s (el funding rate cambia cada 8h)
                self._funding_cache[cache_key] = result
                asyncio.get_event_loop().call_later(60, lambda: self._funding_cache.pop(cache_key, None))
                return result
            logger.debug(f"MarketData: sin funding rate para {symbol}")
            return None
        except ClientMarketDataException as e:
            logger.warning(f"MarketData: error obteniendo funding rate {symbol}: {e}")
            return None

    # ── Open Interest ───────────────────────────────────────

    @api_retry_async(max_retries=2, base_delay=1.0, critical=False, use_rate_limiter=True)
    async def get_open_interest(self, symbol: str) -> Optional[dict[str, Any]]:
        """
        Obtiene el open interest actual del símbolo desde Binance.

        Returns:
            dict con keys: symbol, open_interest, o None si falla.
        """
        cache_key = f"{symbol}_oi"
        if cache_key in self._oi_cache:
            return self._oi_cache[cache_key]

        try:
            oi_data = await self._client.get_open_interest(symbol)
            if oi_data:
                result = {
                    "symbol": symbol,
                    "open_interest": oi_data["open_interest"],
                    "timestamp": datetime.fromtimestamp(
                        oi_data["time"] / 1000, tz=timezone.utc
                    ) if oi_data.get("time") else None,
                }
                # Cachear por 60s
                self._oi_cache[cache_key] = result
                asyncio.get_event_loop().call_later(60, lambda: self._oi_cache.pop(cache_key, None))
                return result
            logger.debug(f"MarketData: sin open interest para {symbol}")
            return None
        except ClientMarketDataException as e:
            logger.warning(f"MarketData: error obteniendo open interest {symbol}: {e}")
            return None

    # ── Señal de mercado ───────────────────────────────────

    def get_market_signal(self, symbol: str) -> dict[str, Any]:
        """
        Genera una señal de mercado combinada (funding rate + OI).

        Fix #7: la señal se usa como filtro binario (no ajuste de score).
        Si funding rate es extremo, 'signal' = "bullish" | "bearish" y
        la estrategia bloquea entradas en dirección contraria.

        Returns:
            dict con:
                - signal: "bullish" | "bearish" | "neutral"
                - reason: str explicación
                - funding_rate: float or None
                - oi_change_pct: float or None
        """
        result: dict[str, Any] = {
            "signal": "neutral",
            "reason": "",
            "funding_rate": None,
            "oi_change_pct": None,
        }

        # Funding rate signal
        fr_data = self._funding_cache.get(f"{symbol}_funding")
        if fr_data and FUNDING_RATE_ENABLED:
            fr = fr_data["funding_rate"]
            result["funding_rate"] = fr
            if fr >= FUNDING_RATE_EXTREME_HIGH:
                # Funding extremo positivo → mercado sobreapalancado LONG
                result["signal"] = "bearish"
                result["reason"] += f"Funding extremo {fr:.4f}% (bajista); "
            elif fr <= FUNDING_RATE_EXTREME_LOW:
                # Funding extremo negativo → mercado sobreapalancado SHORT
                result["signal"] = "bullish"
                result["reason"] += f"Funding negativo {fr:.4f}% (alcista); "
            # Si no es extremo → señal neutral, no bloquea nada

        # Open interest signal
        oi_data = self._oi_cache.get(f"{symbol}_oi")
        if oi_data and OPEN_INTEREST_ENABLED:
            result["oi_change_pct"] = 0.0  # placeholder, requeriría historial

        if result["reason"].endswith("; "):
            result["reason"] = result["reason"][:-2]

        return result

    def clear_cache(self) -> None:
        """Limpia toda la caché interna."""
        self._funding_cache.clear()
        self._oi_cache.clear()
