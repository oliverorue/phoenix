"""
Escáner global de mercado para Binance USDⓈ-M Futures (true async).

Estrategia de bajo consumo de RAM para cientos de pares:

- **Streams combinados**: un único socket multiplexa ``symbols_per_shard``
  pares (``/stream?streams=a@aggTrade/b@aggTrade/...``), nunca un socket por
  símbolo. Además usa dos streams globales de mercado completo en un socket:
  ``!miniTicker@arr`` (precio/volumen de todo el mercado) y ``!forceOrder@arr``
  (liquidaciones forzadas reales).
- **Universo filtrado**: descubre todos los perpetuos USDT-M vía
  ``exchangeInfo`` y descarta "basura" por volumen 24h (quote volume).
- **Estado acotado**: el motor de anomalías guarda ventanas ``deque(maxlen)``;
  no hay historial ilimitado ni DataFrames por evento.
- **Backpressure**: cola de señales con límite; si se llena, se descarta la más
  vieja y se cuenta en ``dropped``.
- **Reconexión**: backoff exponencial con tope y cancelación limpia (sin leaks
  de corutinas ni sockets).
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from collections import deque
from typing import Any, Awaitable, Callable, Optional

import websockets
from loguru import logger

from bot.core.exchange_client import BinanceFuturesClient
from bot.scanner.anomaly_engine import AnomalyEngine
from bot.scanner.config import ScannerConfig
from bot.scanner.models import PairMeta, ScannerStats, Signal

_WSS_MAIN = "wss://fstream.binance.com"
_WSS_TESTNET = "wss://stream.binancefuture.com"

# Binance migró (2026) los WS de USDⓈ-M futures a rutas por categoría.
# Legacy `/stream` solo sirve la categoría `public`; `market` se descarta en
# silencio. Mapeo: public = depth/bookTicker; market = aggTrade/markPrice/
# kline/miniTicker/forceOrder.
_PUBLIC = "/public"
_MARKET = "/market"

# Un stream "muerto" no lanza error (el handshake da 101 y luego 0 frames).
# Todos nuestros streams emiten al menos 1 msg/s, así que 60s sin datos = falla.
_STALE_SECONDS = 60

StreamHandler = Callable[[str, Any], Awaitable[None]]


class MarketScanner:
    """Descubre, filtra y escanea el mercado; emite señales de anomalía."""

    def __init__(
        self,
        config: Optional[ScannerConfig] = None,
        engine: Optional[AnomalyEngine] = None,
        on_signal: Optional[Callable[[Signal], None]] = None,
    ) -> None:
        self._cfg = config or ScannerConfig.from_settings()
        self._engine = engine or AnomalyEngine(self._cfg)
        self.on_signal = on_signal
        self.on_price: Optional[Callable[[str, float], None]] = None

        self._testnet = not self._cfg.use_mainnet
        self._client = BinanceFuturesClient("", "", testnet=self._testnet)

        self._stop = threading.Event()
        self._started_at = 0.0

        # Universo rastreado (inmutable tras el discovery, salvo reconexión).
        self._universe: list[PairMeta] = []
        self._tracked: set[str] = set()
        self._last_price: dict[str, float] = {}

        # Señales (bounded) + stats, protegidas para lectura desde otro thread.
        self._signals: deque[Signal] = deque(maxlen=self._cfg.signal_queue_maxlen)
        self._lock = threading.Lock()
        self._stats = {
            "universe": 0, "tracked": 0, "connections": 0,
            "events_processed": 0, "signals_emitted": 0, "dropped": 0,
            "reconnects": 0, "last_error": None,
        }
        self._eval_throttle: dict[str, float] = {}
        self._last_emit: dict[str, tuple[float, str]] = {}
        self._stream_counts: dict[str, int] = {}

    # ── Discovery ────────────────────────────────────────

    async def discover_universe(self) -> list[PairMeta]:
        """Lista los perpetuos USDT-M elegibles, filtrados por volumen 24h."""
        rest = self._client._rest  # Client sincrónico de python-binance

        exchange_info = await asyncio.to_thread(rest.futures_exchange_info)
        perps = {
            s["symbol"]: s
            for s in exchange_info.get("symbols", [])
            if s.get("contractType") == "PERPETUAL"
            and s.get("status") == "TRADING"
            and s.get("quoteAsset") == "USDT"
        }

        tickers = await asyncio.to_thread(rest.futures_ticker)
        pairs: list[PairMeta] = []
        watch = {w.upper() for w in self._cfg.watchlist}
        for t in tickers:
            sym = t.get("symbol", "")
            if sym not in perps:
                continue
            quote_vol = float(t.get("quoteVolume") or 0.0)
            if quote_vol < self._cfg.min_quote_volume_24h and sym not in watch:
                continue
            pairs.append(PairMeta(
                symbol=sym,
                base_asset=perps[sym].get("baseAsset", ""),
                last_price=float(t.get("lastPrice") or 0.0),
                price_change_pct=float(t.get("priceChangePercent") or 0.0),
                quote_volume_24h=quote_vol,
            ))

        pairs.sort(key=lambda p: p.quote_volume_24h, reverse=True)
        # Watchlist primero (si existe), luego por volumen, con techo.
        head = [p for p in pairs if p.symbol in watch]
        tail = [p for p in pairs if p.symbol not in watch]
        return (head + tail)[: self._cfg.max_symbols]

    # ── Construcción de streams ──────────────────────────

    def _base_url(self) -> str:
        return _WSS_TESTNET if self._testnet else _WSS_MAIN

    def _global_stream_url(self) -> str:
        return f"{self._base_url()}{_MARKET}/stream?streams=!miniTicker@arr/!forceOrder@arr"

    def _shard_urls(self) -> list[str]:
        """Devuelve sockets combinados separados: aggTrade (por shard) y depth (top-N).

        Se separan para no mezclar streams de alta frecuencia (aggTrade) con el
        book parcial en el mismo socket, y reducir la carga por conexión.
        """
        symbols = sorted(self._tracked)
        base = self._base_url()
        chunk = self._cfg.symbols_per_shard
        urls: list[str] = []

        for i in range(0, len(symbols), chunk):
            group = symbols[i:i + chunk]
            agg = "/".join(f"{s.lower()}@aggTrade" for s in group)
            urls.append(f"{base}{_MARKET}/stream?streams={agg}")

        depth_symbols = symbols[:self._cfg.depth_symbols_limit]
        for i in range(0, len(depth_symbols), chunk):
            group = depth_symbols[i:i + chunk]
            depth = "/".join(
                f"{s.lower()}@depth{self._cfg.depth_levels}" for s in group
            )
            urls.append(f"{base}{_PUBLIC}/stream?streams={depth}")

        return urls

    # ── Ciclo de vida ────────────────────────────────────

    async def run(self) -> None:
        self._started_at = time.time()
        self._universe = await self.discover_universe()
        self._tracked = {p.symbol for p in self._universe}
        self._last_price = {p.symbol: p.last_price for p in self._universe}
        self._stats["universe"] = len(self._universe)
        self._stats["tracked"] = len(self._tracked)
        logger.info(f"[scanner] universo: {len(self._tracked)} pares")

        urls = [self._global_stream_url()] + self._shard_urls()
        self._stats["connections"] = len(urls)

        tasks: list[asyncio.Task[None]] = []
        for url in urls:
            handler: StreamHandler = (
                self._handle_global if url == self._global_stream_url()
                else self._handle_symbol
            )
            tasks.append(asyncio.create_task(
                self._stream_loop(url, handler), name=f"ws:{url[-40:]}",
            ))
        tasks.append(asyncio.create_task(self._funding_loop(), name="funding"))

        try:
            while not self._stop.is_set():
                await asyncio.sleep(0.25)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            logger.info("[scanner] detenido limpiamente")

    def stop(self) -> None:
        self._stop.set()

    # ── Stream loop con reconexión ───────────────────────

    async def _stream_loop(self, url: str, handler: StreamHandler) -> None:
        backoff = self._cfg.reconnect_base_seconds
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    url, max_size=self._cfg.message_max_size, open_timeout=15,
                ) as ws:
                    backoff = self._cfg.reconnect_base_seconds
                    logger.info(f"[scanner] conectado a {url[:60]}...")
                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(
                                ws.recv(), timeout=_STALE_SECONDS,
                            )
                        except asyncio.TimeoutError:
                            logger.warning(
                                f"[scanner] {url[:60]}... sin datos en "
                                f"{_STALE_SECONDS}s; reconectando"
                            )
                            break
                        try:
                            stream, data = self._parse(raw)
                            suffix = (
                                stream.split("@", 1)[1]
                                if "@" in stream else stream
                            )
                            self._stream_counts[suffix] = (
                                self._stream_counts.get(suffix, 0) + 1
                            )
                            await handler(stream, data)
                        except Exception as exc:  # noqa: BLE001
                            self._record_error(exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self._stats["reconnects"] += 1
                logger.warning(
                    f"[scanner] {url[:60]}... caído ({exc}); "
                    f"reintento en {backoff:.1f}s",
                )
                await self._sleep_interruptible(backoff)
                backoff = min(backoff * 2, self._cfg.reconnect_max_seconds)

    async def _funding_loop(self) -> None:
        while not self._stop.is_set():
            await self._refresh_funding()
            await self._sleep_interruptible(self._cfg.funding_poll_seconds)

    async def _refresh_funding(self) -> None:
        try:
            premium = await asyncio.to_thread(self._client._rest.futures_mark_price)
            for row in premium:
                sym = row.get("symbol", "")
                if sym in self._tracked:
                    self._engine.on_funding(sym, float(row.get("lastFundingRate") or 0.0))
        except Exception as exc:  # noqa: BLE001
            self._record_error(exc)

    # ── Parseo y handlers ────────────────────────────────

    @staticmethod
    def _parse(raw: str | bytes) -> tuple[str, Any]:
        msg = json.loads(raw)
        return msg.get("stream", ""), msg.get("data", msg)

    async def _handle_global(self, stream: str, data: Any) -> None:
        if isinstance(data, list):
            if stream.endswith("!miniTicker@arr"):
                for t in data:
                    sym = t.get("s", "")
                    if sym in self._tracked:
                        price = float(t.get("c") or 0.0)
                        self._last_price[sym] = price
                        self._engine.on_price(sym, price)
                        if self.on_price is not None:
                            self.on_price(sym, price)
            elif stream.endswith("!forceOrder@arr"):
                for o in data:
                    sym = o.get("s", "")
                    if sym not in self._tracked:
                        continue
                    qty = float(o.get("q") or o.get("z") or 0.0)
                    price = float(o.get("ap") or o.get("p") or 0.0)
                    self._engine.on_force_order(
                        sym, time.time(), str(o.get("S", "")), qty * price,
                    )
                    await self._maybe_evaluate(sym)
        self._stats["events_processed"] += 1

    async def _handle_symbol(self, stream: str, data: Any) -> None:
        sym = stream.split("@", 1)[0].upper()
        if sym not in self._tracked:
            return
        try:
            if "@aggTrade" in stream:
                self._engine.on_agg_trade(
                    sym,
                    float(data.get("T", time.time() * 1000)) / 1000.0,
                    float(data.get("p") or 0.0),
                    float(data.get("q") or 0.0),
                    bool(data.get("m", False)),
                )
            elif "@depth" in stream:
                bids = [(float(p), float(q)) for p, q in (data.get("b") or [])]
                asks = [(float(p), float(q)) for p, q in (data.get("a") or [])]
                self._engine.on_depth(sym, bids, asks)
                if bids and asks:
                    self._engine.on_price(sym, (bids[0][0] + asks[0][0]) / 2.0)
                    if self.on_price is not None:
                        self.on_price(sym, (bids[0][0] + asks[0][0]) / 2.0)
        finally:
            self._stats["events_processed"] += 1
        await self._maybe_evaluate(sym)

    async def _maybe_evaluate(self, symbol: str) -> None:
        now = time.time()
        if now - self._eval_throttle.get(symbol, 0.0) < 1.0:
            return
        self._eval_throttle[symbol] = now
        signal = self._engine.evaluate(symbol, now)
        if signal is None:
            return

        # Cooldown por símbolo+dirección: evita re-emitir la misma anomalía
        # cada segundo mientras persista.
        last = self._last_emit.get(symbol)
        if last is not None:
            last_ts, last_dir = last
            if (
                last_dir == signal.direction
                and now - last_ts < self._cfg.signal_cooldown_seconds
            ):
                return
        self._last_emit[symbol] = (now, signal.direction)
        self._emit(signal)

    # ── Emisión / snapshot ───────────────────────────────

    def _emit(self, signal: Signal) -> None:
        with self._lock:
            if len(self._signals) >= self._signals.maxlen:
                self._stats["dropped"] += 1
            self._signals.append(signal)
            self._stats["signals_emitted"] += 1
        if self.on_signal is not None:
            try:
                self.on_signal(signal)
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[scanner] on_signal falló: {exc}")

    def _record_error(self, exc: Exception) -> None:
        with self._lock:
            self._stats["last_error"] = str(exc)[:200]

    def signals_snapshot(self) -> list[Signal]:
        with self._lock:
            return list(self._signals)

    def tracked_symbols(self) -> list[str]:
        return sorted(self._tracked)

    def volume_map(self) -> dict[str, float]:
        """Volumen 24h por símbolo (para el ranking por liquidez)."""
        return {p.symbol: p.quote_volume_24h for p in self._universe}

    def stats(self) -> ScannerStats:
        with self._lock:
            return ScannerStats(
                universe=self._stats["universe"],
                tracked=self._stats["tracked"],
                connections=self._stats["connections"],
                events_processed=self._stats["events_processed"],
                signals_emitted=self._stats["signals_emitted"],
                dropped=self._stats["dropped"],
                reconnects=self._stats["reconnects"],
                uptime_seconds=round(time.time() - self._started_at, 1),
                last_error=self._stats["last_error"],
                diagnostics=self._engine.diagnostics(),
                stream_counts=dict(self._stream_counts),
            )

    # ── Helpers ──────────────────────────────────────────

    async def _sleep_interruptible(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while time.monotonic() < end and not self._stop.is_set():
            await asyncio.sleep(min(0.25, end - time.monotonic()))


if __name__ == "__main__":
    try:
        asyncio.run(MarketScanner().run())
    except KeyboardInterrupt:
        logger.info("[scanner] interrumpido por el usuario")
