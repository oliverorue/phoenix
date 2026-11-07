"""
Data Feed para Binance USDⓈ-M Futures – Async REST (vía BinanceFuturesClient).
"""

import asyncio
from loguru import logger
from datetime import datetime, timezone
from typing import Any, Optional
from bot.core.exceptions import DataFeedException

import pandas as pd

from config.loader import (
    API_KEY, API_SECRET, TESTNET, SYMBOL,
    TIMEFRAME_PRIMARY, TIMEFRAME_ENTRY,
    VELAS_LIMIT_PRIMARY, VELAS_LIMIT_ENTRY,
)
from bot.core.exchange_client import BinanceFuturesClient, MarketDataException
from bot.core.api_resilience import CircuitBreaker, api_retry_async
from bot.core.timeframes import normalize_interval
from bot.core.tracing import trace


def decimal_places_for_qty_step(qty_step: float) -> int:
    """
    Decimales para formatear/redondear cantidades según stepSize de Binance.
    Evita tratar 1.0 como «un decimal» (p. ej. "757.0" en contratos enteros).
    """
    if qty_step <= 0:
        return 8
    if abs(qty_step - round(qty_step)) < 1e-12:
        return 0
    s = f"{qty_step:.12f}".rstrip("0").rstrip(".")
    if "." not in s:
        return 0
    return len(s.split(".")[1])


# Fallback si falla get_instrument_info (evita step_size=0.001 en enteros tipo DOGE)
_INSTRUMENT_LOT_FALLBACK = {
    "DOGEUSDT": {
        "qtyStep": 1.0,
        "minOrderQty": 1.0,
        "maxOrderQty": 43678900.0,
        "minNotionalValue": 5.0,
        "maxNotionalValue": 1000000.0,
    },
}


class DataFeed:
    """Maneja la conexión con Binance para obtener datos OHLCV (REST asíncrono)."""

    def __init__(self, symbol: str = SYMBOL, testnet: bool = TESTNET) -> None:
        self.symbol = symbol
        self.testnet = testnet

        self._client = BinanceFuturesClient(
            api_key=API_KEY or "",
            api_secret=API_SECRET or "",
            testnet=self.testnet,
        )
        self.circuit_breaker = CircuitBreaker()

        # ── Latency tracking ────────────────────────────────
        self._latency_samples: dict[str, list[float]] = {}
        self._max_latency_samples = 100
        # Cache de instrument info (se rellena con get_instrument_info)
        self._instrument_cache: Optional[dict[str, Any]] = None

    @property
    def _rest(self) -> Any:
        """Backward-compat: expone el binance.Client subyacente para el backtester."""
        return self._client._rest if self._client else None

    # ── Latency tracking ────────────────────────────────────

    def _record_latency(self, endpoint: str, elapsed: float) -> None:
        """Registra una muestra de latencia para un endpoint."""
        if endpoint not in self._latency_samples:
            self._latency_samples[endpoint] = []
        samples = self._latency_samples[endpoint]
        samples.append(elapsed)
        if len(samples) > self._max_latency_samples:
            samples.pop(0)

    def get_latency_stats(self, endpoint: Optional[str] = None) -> dict[str, Any]:
        """
        Estadísticas de latencia para un endpoint.

        Args:
            endpoint: Nombre del endpoint. Si es None, retorna todas.

        Returns:
            dict con min, max, avg, p50, p95, p99, count.
            Si no hay muestras, retorna dict con valores 0.
        """
        if endpoint:
            samples = self._latency_samples.get(endpoint, [])
            return self._calc_stats(endpoint, samples)
        stats = {}
        for ep, samples in self._latency_samples.items():
            stats[ep] = self._calc_stats(ep, samples)
        return stats

    def get_all_latency_stats(self) -> dict[str, Any]:
        """Retorna estadísticas de latencia de todos los endpoints."""
        return self.get_latency_stats()

    @staticmethod
    def _calc_stats(endpoint: str, samples: list[float]) -> dict[str, Any]:
        """Calcula estadísticas básicas de una lista de latencias."""
        if not samples:
            return {"endpoint": endpoint, "count": 0, "min": 0.0, "max": 0.0,
                    "avg": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0}
        s = sorted(samples)
        n = len(s)
        return {
            "endpoint": endpoint,
            "count": n,
            "min": s[0],
            "max": s[-1],
            "avg": sum(s) / n,
            "p50": s[int(n * 0.50)],
            "p95": s[int(n * 0.95)],
            "p99": s[int(n * 0.99)],
        }

    # ── REST: Velas históricas ──────────────────────

    @trace(attributes={"component": "DataFeed"})
    @api_retry_async(max_retries=3, base_delay=2.0)
    async def get_klines(self, interval: str, limit: int = 100) -> pd.DataFrame:
        """Obtiene velas OHLCV desde Binance (async)."""
        binance_interval = normalize_interval(interval)
        try:
            klines = await self._client.get_klines(
                self.symbol, binance_interval, limit,
            )
            if not klines:
                logger.warning(f"Sin velas para {self.symbol} {binance_interval}")
                return pd.DataFrame()

            df = pd.DataFrame(klines)
            # Las claves que retorna BinanceFuturesClient.get_klines
            df = df.rename(columns={
                "open_time": "timestamp",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            })
            df = df.astype({col: float for col in ["open", "high", "low", "close", "volume"]})
            df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms", utc=True)
            df.sort_values("timestamp", inplace=True)
            df.reset_index(drop=True, inplace=True)
            return df
        except MarketDataException as e:
            raise DataFeedException(
                f"Error obteniendo velas {binance_interval}: {e}"
            ) from e

    async def current_1h(self) -> pd.DataFrame:
        """Obtiene las velas del timeframe principal (1h)."""
        return await self.get_klines(TIMEFRAME_PRIMARY, VELAS_LIMIT_PRIMARY)

    async def current_15m(self) -> pd.DataFrame:
        """Obtiene las velas del timeframe de entrada (15m)."""
        return await self.get_klines(TIMEFRAME_ENTRY, VELAS_LIMIT_ENTRY)

    # ── Balance ────────────────────────────────────

    @trace(attributes={"component": "DataFeed"})
    @api_retry_async(max_retries=3, base_delay=2.0)
    async def get_balance(self) -> float:
        """Consulta el balance USDT disponible desde futures_account (async)."""
        try:
            resp = await asyncio.to_thread(
                self._client._rest.futures_account
            )
            for asset in resp.get("assets", []):
                if asset.get("asset") == "USDT":
                    return float(asset.get("walletBalance", 0))
            raise DataFeedException("USDT balance no encontrado en futures_account")
        except Exception as e:
            raise DataFeedException(f"Error obteniendo balance: {e}") from e

    @api_retry_async(max_retries=3, base_delay=2.0)
    async def get_instrument_info(self) -> dict[str, Any]:
        """Obtiene y cachea los filtros del instrumento (async)."""
        if self._instrument_cache is not None:
            return self._instrument_cache
        try:
            info = await self._client.get_instrument_info(self.symbol)
            max_notional = float(info.get("max_notional", 0.0))
            self._instrument_cache = {
                "qtyStep": info["step_size"],
                "minOrderQty": info["min_qty"],
                "maxOrderQty": info["max_qty"],
                "minNotionalValue": info.get("min_notional", 1.0),
                "maxNotionalValue": max_notional or 1_000_000.0,
            }
        except MarketDataException as e:
            fb = _INSTRUMENT_LOT_FALLBACK.get(self.symbol)
            if fb:
                self._instrument_cache = dict(fb)
                logger.warning(
                    f"Instrumento {self.symbol}: API falló ({e}); usando fallback"
                )
            else:
                raise DataFeedException(f"Error obteniendo info del instrumento: {e}") from e
        logger.info(
            f"Instrumento {self.symbol}: {self._instrument_cache} "
            f"(decimales qty: {decimal_places_for_qty_step(self._instrument_cache['qtyStep'])})"
        )
        return self._instrument_cache

    # ── Espera sincronizada con velas (sin WebSocket) ─

    @staticmethod
    @staticmethod
    def seconds_until_next_candle(interval_minutes: int = 15) -> float:
        """Segundos hasta el inicio de la siguiente vela (+2 s de margen)."""
        now = datetime.now(timezone.utc)
        seconds_per_candle = interval_minutes * 60
        elapsed = now.timestamp() % seconds_per_candle
        return float(seconds_per_candle - elapsed + 2)

    @staticmethod
    async def wait_for_next_candle(interval_minutes: int = 15) -> None:
        """
        Espera hasta el inicio de la siguiente vela.
        Ejemplo: si interval=15 y son las 12:03, espera hasta 12:15:02.
        """
        await asyncio.sleep(
            DataFeed.seconds_until_next_candle(interval_minutes)
        )

    async def get_spread_percent(self) -> float:
        """Obtiene el spread actual (bid-ask) en porcentaje via order book (async)."""
        try:
            book = await asyncio.to_thread(
                self._client._rest.futures_order_book,
                symbol=self.symbol,
                limit=5,
            )
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            if bids and asks:
                bid = float(bids[0][0])
                ask = float(asks[0][0])
                if bid > 0 and ask > 0:
                    spread = ((ask - bid) / bid) * 100
                    logger.debug(f"Spread {self.symbol}: {spread:.4f}%")
                    return spread
        except Exception as e:
            logger.debug(f"No se pudo obtener spread para {self.symbol}: {e}")
        # Sin datos de orderbook no se debe habilitar una entrada.
        return 100.0
