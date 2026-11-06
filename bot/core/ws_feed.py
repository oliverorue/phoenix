"""
WebSocket para datos de mercado en tiempo real via Binance USDⓈ-M Futures.
Usa BinanceFuturesClient.subscribe_ws() con reconexión automática.

Mantiene buffers internos de DataFrames para los timeframes
configurados (1h y 15m). Cuando el WebSocket está activo,
los datos se actualizan en ~1-60s (frecuencia de push de Binance).

Uso:
    ws = WebSocketFeed(symbol="XRPUSDT", testnet=True)
    ws.start()

    # Obtener datos (desde buffer WebSocket o REST fallback)
    df_15m = ws.current_15m()
    df_1h = ws.current_1h()

    ws.stop()
"""

import time
from typing import Any, Callable, Optional
from threading import Lock

import pandas as pd
from loguru import logger

from config.loader import (
    API_KEY, API_SECRET, TESTNET,
    TIMEFRAME_PRIMARY, TIMEFRAME_ENTRY,
    ORDERBOOK_ENABLED, ORDERBOOK_DEPTH,
)
from bot.core.data_feed import DataFeed
from bot.core.exchange_client import BinanceFuturesClient
from bot.core.timeframes import interval_minutes, normalize_interval


class WebSocketFeed:
    """
    Cliente WebSocket para klines en tiempo real via BinanceFuturesClient.

    La reconexión automática es gestionada internamente por
    BinanceFuturesClient.subscribe_ws(), que implementa exponential
    backoff + jitter.

    Attributes:
        symbol: Símbolo de trading (ej. "XRPUSDT").
        connected: Flag de conexión activa.
        on_kline_15m: Callback opcional para nueva vela 15m.
        on_kline_1h: Callback opcional para nueva vela 1h.
        on_orderbook: Callback opcional para actualización de orderbook.
        on_reconnect: Callback opcional de reconexión (recibe symbol).
    """

    def __init__(self, symbol: str, testnet: bool = TESTNET,
                 rest_fallback: Optional[DataFeed] = None,
                 orderbook_enabled: bool = ORDERBOOK_ENABLED,
                 orderbook_depth: int = ORDERBOOK_DEPTH):
        self.symbol = symbol
        self.testnet = testnet
        self._rest: Optional[DataFeed] = rest_fallback
        self._client: Optional[BinanceFuturesClient] = None

        self._ob_enabled = orderbook_enabled
        self._ob_depth = orderbook_depth

        # Buffers thread-safe para los DataFrames más recientes
        self._lock = Lock()
        self._df_15m: pd.DataFrame = pd.DataFrame()
        self._df_1h: pd.DataFrame = pd.DataFrame()
        self._last_update_15m: float = 0.0
        self._last_update_1h: float = 0.0

        # Orderbook (bid/ask en tiempo real)
        self._ob_bids: list[list[float]] = []    # [[price, size], ...]
        self._ob_asks: list[list[float]] = []    # [[price, size], ...]
        self._last_update_ob: float = 0.0
        self._ob_ready: bool = False

        # Flag de conexión
        self.connected: bool = False
        self._running: bool = False

        # Callbacks opcionales
        self.on_kline_15m: Any = None
        self.on_kline_1h: Any = None
        self.on_orderbook: Optional[Callable[[dict[str, Any]], None]] = None
        self.on_reconnect: Optional[Callable[[str], None]] = None  # recibe "symbol"

    # ── Inicio / Parada ────────────────────────────────────

    def start(self) -> bool:
        """Conecta el WebSocket y se suscribe a klines + orderbook."""
        if self._running:
            logger.warning(f"WebSocketFeed ya está corriendo para {self.symbol}")
            return True
        self._running = True

        # Instanciar cliente (se reutiliza para WS)
        try:
            self._client = BinanceFuturesClient(
                api_key=API_KEY or "",
                api_secret=API_SECRET or "",
                testnet=self.testnet,
            )
        except Exception as exc:
            logger.error(f"Error al crear BinanceFuturesClient para {self.symbol}: {exc}")
            self._running = False
            return False

        # Iniciar WS en background
        import asyncio
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._start_ws())
        except RuntimeError:
            asyncio.run(self._start_ws())
        return True

    async def _start_ws(self) -> None:
        """Se suscribe a los streams de Binance via exchange_client."""
        if not self._client:
            return

        sym_lower = self.symbol.lower()
        entry_interval = normalize_interval(TIMEFRAME_ENTRY)
        primary_interval = normalize_interval(TIMEFRAME_PRIMARY)
        streams = [
            f"{sym_lower}@kline_{entry_interval}",
            f"{sym_lower}@kline_{primary_interval}",
        ]
        if self._ob_enabled:
            streams.append(f"{sym_lower}@depth{self._ob_depth}@100ms")

        self.connected = False
        await self._client.subscribe_ws(streams, self._on_ws_message)
        logger.info(f"🔌 WebSocket suscripto para {self.symbol} (esperando datos)")

    def stop(self) -> None:
        """Desconecta el WebSocket."""
        self._running = False
        self.connected = False
        self._ob_ready = False

        if self._client:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._client.close_ws())
            except RuntimeError:
                # No hay event loop corriendo: crear uno temporal
                try:
                    asyncio.run(self._client.close_ws())
                except Exception:
                    pass
            self._client = None

        logger.info(f"WebSocketFeed desconectado: {self.symbol}")

    # ── Callback principal ─────────────────────────────────

    async def _on_ws_message(self, message: dict[str, Any]) -> None:
        """Router de mensajes WebSocket de Binance."""
        event_type = message.get("e", "")
        if event_type in ("kline", "depthUpdate"):
            self.connected = True

        if event_type == "kline":
            self._handle_kline(message)
        elif event_type == "depthUpdate":
            self._handle_depth(message)

    # ── Kline handler ──────────────────────────────────────

    def _handle_kline(self, message: dict[str, Any]) -> None:
        """Procesa un mensaje de kline de Binance."""
        kline = message.get("k", {})
        interval = kline.get("i", "")
        data = {
            "start": int(kline.get("t", 0)),
            "open": float(kline.get("o", 0)),
            "high": float(kline.get("h", 0)),
            "low": float(kline.get("l", 0)),
            "close": float(kline.get("c", 0)),
            "volume": float(kline.get("v", 0)),
            "confirm": kline.get("x", False),
        }

        entry_interval = normalize_interval(TIMEFRAME_ENTRY)
        primary_interval = normalize_interval(TIMEFRAME_PRIMARY)

        if interval == entry_interval:
            with self._lock:
                self._df_15m = self._append_kline(
                    self._df_15m, data, interval_minutes(TIMEFRAME_ENTRY),
                )
                self._last_update_15m = time.time()
            if self.on_kline_15m:
                try:
                    self.on_kline_15m(data)
                except Exception:
                    pass
        elif interval == primary_interval:
            with self._lock:
                self._df_1h = self._append_kline(
                    self._df_1h, data, interval_minutes(TIMEFRAME_PRIMARY),
                )
                self._last_update_1h = time.time()
            if self.on_kline_1h:
                try:
                    self.on_kline_1h(data)
                except Exception:
                    pass

    # ── Depth handler ──────────────────────────────────────

    def _handle_depth(self, message: dict[str, Any]) -> None:
        """Procesa un mensaje depthUpdate (partial book) de Binance."""
        with self._lock:
            bids = [[float(p), float(q)] for p, q in message.get("b", [])]
            asks = [[float(p), float(q)] for p, q in message.get("a", [])]

            if bids:
                self._ob_bids = sorted(
                    bids, key=lambda x: x[0], reverse=True,
                )[:self._ob_depth]
            if asks:
                self._ob_asks = sorted(
                    asks, key=lambda x: x[0],
                )[:self._ob_depth]
            self._ob_ready = True
            self._last_update_ob = time.time()

        if self.on_orderbook and self._ob_ready:
            best = self._ob_best_locked()
            if best:
                try:
                    self.on_orderbook(best)
                except Exception:
                    pass

    # ── Métodos de utilidad (compatibles con versión anterior) ──

    @staticmethod
    def _append_kline(df: pd.DataFrame, data: dict[str, Any],
                      interval_min: int) -> pd.DataFrame:
        """
        Agrega o reemplaza una vela en el DataFrame buffer.

        Binance usa timestamps en milisegundos (campo ``t`` del objeto k).
        """
        ts = pd.to_datetime(data["start"], unit="ms", utc=True)
        row = pd.DataFrame([{
            "timestamp": ts,
            "open": data["open"],
            "high": data["high"],
            "low": data["low"],
            "close": data["close"],
            "volume": data["volume"],
        }])

        if df.empty:
            return row

        # Si la vela ya existe (mismo timestamp), reemplazar la última
        last_ts = df["timestamp"].iloc[-1]
        if ts == last_ts:
            df.iloc[-1] = row.iloc[0]
            return df

        # Si es nueva, agregar
        df = pd.concat([df, row], ignore_index=True)

        # Mantener solo ~100 velas para no acumular memoria
        max_rows = max(100, interval_min * 2)  # al menos 2x el timeframe en horas
        if len(df) > max_rows:
            df = df.iloc[-max_rows:]

        return df

    def _ob_best_locked(self) -> Optional[dict[str, Any]]:
        """
        Retorna best bid/ask desde el orderbook.
        DEBE llamarse bajo self._lock (aunque aquí se accede en el mismo
        contexto que ya tiene el lock).
        """
        if not self._ob_ready or not self._ob_bids or not self._ob_asks:
            return None
        return {
            "bid": self._ob_bids[0][0],
            "bid_size": self._ob_bids[0][1],
            "ask": self._ob_asks[0][0],
            "ask_size": self._ob_asks[0][1],
            "spread_pct": ((self._ob_asks[0][0] - self._ob_bids[0][0])
                           / self._ob_bids[0][0]) * 100,
            "timestamp": self._last_update_ob,
        }

    # ── API pública (similar a DataFeed) ───────────────────

    def current_15m(self) -> pd.DataFrame:
        """
        Retorna el DataFrame de 15 minutos desde WebSocket o REST fallback.
        """
        with self._lock:
            if not self._df_15m.empty:
                return self._df_15m.tail(30)

        # Fallback a REST — DataFeed ya no se provee como _rest
        return pd.DataFrame()

    def current_1h(self) -> pd.DataFrame:
        """
        Retorna el DataFrame de 1 hora desde WebSocket o REST fallback.
        """
        with self._lock:
            if not self._df_1h.empty:
                return self._df_1h.tail(30)

        # Fallback a REST — DataFeed ya no se provee como _rest
        return pd.DataFrame()

    # ── API pública: Orderbook ──────────────────────────────

    def get_orderbook_best(self) -> Optional[dict[str, Any]]:
        """
        Retorna best bid/ask en vivo desde el orderbook WebSocket.

        Returns:
            dict con keys: bid, bid_size, ask, ask_size, spread_pct, timestamp
            o None si el orderbook aún no está listo.
        """
        with self._lock:
            return self._ob_best_locked()

    def get_orderbook(self, levels: int = 10) -> Optional[dict[str, Any]]:
        """
        Retorna los N mejores niveles del orderbook.

        Args:
            levels: Número de niveles a retornar (default 10).

        Returns:
            dict con keys: bids, asks, bid, ask, spread_pct
            o None si no hay datos.
        """
        with self._lock:
            if not self._ob_ready or not self._ob_bids or not self._ob_asks:
                return None
            return {
                "bids": [[p, s] for p, s in self._ob_bids[:levels]],
                "asks": [[p, s] for p, s in self._ob_asks[:levels]],
                "bid": self._ob_bids[0][0],
                "bid_size": self._ob_bids[0][1],
                "ask": self._ob_asks[0][0],
                "ask_size": self._ob_asks[0][1],
                "spread_pct": ((self._ob_asks[0][0] - self._ob_bids[0][0])
                               / self._ob_bids[0][0]) * 100,
            }

    def get_spread_percent(self) -> float:
        """
        Obtiene spread bid-ask en porcentaje.
        Prioridad:
          1. Orderbook WebSocket en vivo (si está listo)
          2. Valor conservador que bloquea la entrada si no hay datos.
        """
        best = self.get_orderbook_best()
        if best is not None:
            return float(best["spread_pct"])

        return 100.0

    @property
    def is_ws_connected(self) -> bool:
        """True si WebSocket está conectado y recibiendo datos."""
        return self.connected and self._running

    def health_status(self) -> dict[str, Any]:
        """Estado de salud del feed."""
        now = time.time()
        status = {
            "symbol": self.symbol,
            "connected": self.connected,
            "data_15m_age_s": now - self._last_update_15m if self._last_update_15m else -1,
            "data_1h_age_s": now - self._last_update_1h if self._last_update_1h else -1,
            "has_data_15m": not self._df_15m.empty,
            "has_data_1h": not self._df_1h.empty,
        }
        if self._ob_enabled:
            ob_best = self.get_orderbook_best()
            if ob_best is not None:
                status["orderbook"] = {
                    "bid": ob_best["bid"],
                    "ask": ob_best["ask"],
                    "spread_pct": ob_best["spread_pct"],
                    "age_s": now - self._last_update_ob if self._last_update_ob else -1,
                }
            else:
                status["orderbook"] = {"ready": False}
        return status
