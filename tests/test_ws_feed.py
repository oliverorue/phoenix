"""Tests para WebSocketFeed (API Binance USDⓈ-M Futures)."""

import pytest
import pandas as pd
import time
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.fixture
def ws_feed():
    with patch("bot.core.ws_feed.BinanceFuturesClient"), \
         patch("bot.core.ws_feed.DataFeed"):
        from bot.core.ws_feed import WebSocketFeed
        feed = WebSocketFeed(symbol="XRPUSDT", testnet=True)
        yield feed


# ── Inicialización ────────────────────────────────────────


class TestWebSocketFeedInit:
    """Verifica la inicialización del WebSocketFeed."""

    def test_init_defaults(self, ws_feed):
        assert ws_feed.symbol == "XRPUSDT"
        assert ws_feed.testnet is True
        assert ws_feed.connected is False
        assert ws_feed._running is False
        assert ws_feed._df_15m.empty
        assert ws_feed._df_1h.empty
        assert ws_feed._client is None  # se crea lazy en start()

    def test_init_with_rest_fallback(self):
        with patch("bot.core.ws_feed.BinanceFuturesClient"), \
             patch("bot.core.ws_feed.DataFeed") as _mock_df_cls:
            from bot.core.ws_feed import WebSocketFeed
            rest_feed = MagicMock()
            feed = WebSocketFeed(symbol="XRPUSDT", rest_fallback=rest_feed)
            assert feed._rest is rest_feed


# ── Start / Stop ──────────────────────────────────────────


class TestWebSocketFeedStartStop:
    """Verifica start() y stop()."""

    def test_start_connects_and_subscribes(self):
        with patch("bot.core.ws_feed.BinanceFuturesClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value = mock_client
            mock_client.subscribe_ws = AsyncMock()
            from bot.core.ws_feed import WebSocketFeed
            feed = WebSocketFeed(symbol="XRPUSDT", testnet=True)
            result = feed.start()
            assert result is True
            assert feed._running is True
            assert feed._client is not None
            mock_client.subscribe_ws.assert_awaited_once()

    def test_start_already_running(self, ws_feed):
        ws_feed._running = True
        result = ws_feed.start()
        assert result is True  # ya corriendo

    def test_start_failure_returns_false(self):
        with patch("bot.core.ws_feed.BinanceFuturesClient",
                   side_effect=Exception("conn error")):
            from bot.core.ws_feed import WebSocketFeed
            feed = WebSocketFeed(symbol="XRPUSDT", testnet=True)
            result = feed.start()
            assert result is False
            assert feed.connected is False
            assert feed._running is False

    def test_stop_disconnects(self, ws_feed):
        ws_feed._running = True
        ws_feed.connected = True
        mock_client = AsyncMock()
        mock_client.close_ws = AsyncMock()
        ws_feed._client = mock_client
        ws_feed.stop()
        assert ws_feed._running is False
        assert ws_feed.connected is False
        assert ws_feed._ob_ready is False
        mock_client.close_ws.assert_called_once()

    def test_stop_no_client(self, ws_feed):
        ws_feed._running = True
        ws_feed.connected = True
        ws_feed._client = None
        ws_feed.stop()  # no debe lanzar error
        assert ws_feed._running is False


# ── Handle Kline ──────────────────────────────────────────


class TestWebSocketFeedHandleKline:
    """Verifica _handle_kline y _on_ws_message (router)."""

    def _make_kline_msg(self, interval: str = "15m",
                        close: str = "0.6190",
                        ts_ms: int = 1714320000000) -> dict:
        return {
            "e": "kline",
            "E": ts_ms + 899000,
            "s": "xrpusdt",
            "k": {
                "t": ts_ms,
                "T": ts_ms + 899000,
                "s": "XRPUSDT",
                "i": interval,
                "o": "0.6185",
                "c": close,
                "h": "0.6192",
                "l": "0.6183",
                "v": "123456",
                "x": True,
            },
        }

    def test_handle_kline_15m_updates_buffer(self, ws_feed):
        with patch("bot.core.ws_feed.TIMEFRAME_ENTRY", "15"):
            msg = self._make_kline_msg(interval="15m")
            ws_feed._handle_kline(msg)
        assert not ws_feed._df_15m.empty
        assert ws_feed._last_update_15m > 0

    def test_handle_kline_1h_updates_buffer(self, ws_feed):
        with patch("bot.core.ws_feed.TIMEFRAME_PRIMARY", "60"):
            msg = self._make_kline_msg(interval="60m")
            ws_feed._handle_kline(msg)
        assert not ws_feed._df_1h.empty
        assert ws_feed._last_update_1h > 0

    def test_handle_kline_calls_15m_callback(self, ws_feed):
        cb = MagicMock()
        ws_feed.on_kline_15m = cb
        with patch("bot.core.ws_feed.TIMEFRAME_ENTRY", "15"):
            msg = self._make_kline_msg(interval="15m")
            ws_feed._handle_kline(msg)
        cb.assert_called_once()

    def test_handle_kline_calls_1h_callback(self, ws_feed):
        cb = MagicMock()
        ws_feed.on_kline_1h = cb
        with patch("bot.core.ws_feed.TIMEFRAME_PRIMARY", "60"):
            msg = self._make_kline_msg(interval="60m")
            ws_feed._handle_kline(msg)
        cb.assert_called_once()

    def test_handle_kline_unknown_interval_ignored(self, ws_feed):
        msg = self._make_kline_msg(interval="5m")
        ws_feed._handle_kline(msg)
        assert ws_feed._df_15m.empty
        assert ws_feed._df_1h.empty

    def test_handle_kline_missing_k_object(self, ws_feed):
        ws_feed._handle_kline({"e": "kline"})  # no k key
        assert ws_feed._df_15m.empty

    def test_handle_kline_callback_exception_does_not_crash(self, ws_feed):
        cb = MagicMock(side_effect=ValueError("callback error"))
        ws_feed.on_kline_15m = cb
        with patch("bot.core.ws_feed.TIMEFRAME_ENTRY", "15"):
            msg = self._make_kline_msg(interval="15m")
            ws_feed._handle_kline(msg)  # no debe lanzar

    def test_on_ws_message_routes_kline(self, ws_feed):
        with patch("bot.core.ws_feed.TIMEFRAME_ENTRY", "15"):
            msg = self._make_kline_msg(interval="15m")
            import asyncio
            asyncio.run(ws_feed._on_ws_message(msg))
        assert not ws_feed._df_15m.empty

    def test_on_ws_message_routes_depth(self, ws_feed):
        msg = {
            "e": "depthUpdate",
            "E": 1714320899000,
            "b": [["0.6180", "1000"]],
            "a": [["0.6190", "1500"]],
        }
        import asyncio
        asyncio.run(ws_feed._on_ws_message(msg))
        assert ws_feed._ob_ready is True
        assert len(ws_feed._ob_bids) == 1

    def test_on_ws_message_unknown_event(self, ws_feed):
        msg = {"e": "unknown"}
        import asyncio
        asyncio.run(ws_feed._on_ws_message(msg))  # no debe lanzar


# ── Append Kline ──────────────────────────────────────────


class TestWebSocketFeedAppendKline:
    """Verifica _append_kline (timestamps en ms para Binance)."""

    def test_append_to_empty_df(self):
        from bot.core.ws_feed import WebSocketFeed
        df = pd.DataFrame()
        data = {"start": 1714320000000, "open": 0.6185, "high": 0.6192,
                "low": 0.6183, "close": 0.6190, "volume": 123456}
        result = WebSocketFeed._append_kline(df, data, interval_min=15)
        assert len(result) == 1
        assert result["close"].iloc[0] == 0.6190

    def test_append_replaces_existing(self):
        from bot.core.ws_feed import WebSocketFeed
        # Timestamp en ms para "2024-04-28 12:00:00 UTC"
        ts_ms = int(pd.Timestamp("2024-04-28 12:00:00", tz="UTC").timestamp() * 1000)  # type: ignore[attr-defined]
        df = pd.DataFrame([{
            "timestamp": pd.to_datetime("2024-04-28 12:00:00", utc=True),
            "open": 0.6180, "high": 0.6190, "low": 0.6175,
            "close": 0.6185, "volume": 1000,
        }])
        data = {"start": ts_ms, "open": 0.6185, "high": 0.6192,
                "low": 0.6183, "close": 0.6190, "volume": 123456}
        result = WebSocketFeed._append_kline(df, data, interval_min=15)
        assert len(result) == 1
        assert result["close"].iloc[0] == 0.6190

    def test_append_adds_new_row(self):
        from bot.core.ws_feed import WebSocketFeed
        ts = pd.to_datetime("2024-04-28 12:00:00", utc=True)
        df = pd.DataFrame([{
            "timestamp": ts, "open": 0.6180, "high": 0.6190,
            "low": 0.6175, "close": 0.6185, "volume": 1000,
        }])
        data = {"start": 1714323600000, "open": 0.6190, "high": 0.6200,
                "low": 0.6185, "close": 0.6195, "volume": 2000}
        result = WebSocketFeed._append_kline(df, data, interval_min=15)
        assert len(result) == 2

    def test_append_trims_max_rows(self):
        from bot.core.ws_feed import WebSocketFeed
        import numpy as np
        rows = 150
        df = pd.DataFrame({
            "timestamp": pd.date_range("2024-01-01", periods=rows,
                                       freq="15min", tz="UTC"),
            "open": np.random.rand(rows),
            "high": np.random.rand(rows),
            "low": np.random.rand(rows),
            "close": np.random.rand(rows),
            "volume": np.random.rand(rows) * 1000,
        })
        data = {"start": 1714323600000, "open": 0.6190, "high": 0.6200,
                "low": 0.6185, "close": 0.6195, "volume": 2000}
        result = WebSocketFeed._append_kline(df, data, interval_min=15)
        assert len(result) <= 100  # max_rows = max(100, 30) = 100


# ── Current Data ──────────────────────────────────────────


class TestWebSocketFeedCurrentData:
    """Verifica current_15m y current_1h."""

    def test_current_15m_from_buffer(self, ws_feed):
        df = pd.DataFrame({"close": [0.6190]})
        ws_feed._df_15m = df
        result = ws_feed.current_15m()
        assert not result.empty

    def test_current_15m_no_fallback_after_fase5(self):
        """
        FASE 5: current_15m() ya no hace REST fallback.
        Devuelve DataFrame vacío si el buffer WS está vacío,
        incluso si rest_fallback está configurado.
        """
        with patch("bot.core.ws_feed.BinanceFuturesClient"):
            from bot.core.ws_feed import WebSocketFeed
            mock_rest = MagicMock()
            mock_df = pd.DataFrame({"close": [0.6185]})
            mock_rest.current_15m = AsyncMock(return_value=mock_df)
            feed = WebSocketFeed(symbol="XRPUSDT", rest_fallback=mock_rest)
            feed._df_15m = pd.DataFrame()  # empty buffer
            result = feed.current_15m()
            # REST fallback eliminado en FASE 5 — devuelve vacío
            assert result.empty

    def test_current_15m_empty_when_no_buffer_and_no_rest(self, ws_feed):
        ws_feed._df_15m = pd.DataFrame()
        ws_feed._rest = None
        result = ws_feed.current_15m()
        assert result.empty

    def test_current_1h_from_buffer(self, ws_feed):
        df = pd.DataFrame({"close": [0.6200]})
        ws_feed._df_1h = df
        result = ws_feed.current_1h()
        assert not result.empty


# ── Health ────────────────────────────────────────────────


class TestWebSocketFeedHealth:
    """Verifica health_status y is_ws_connected."""

    def test_is_ws_connected_true(self, ws_feed):
        ws_feed.connected = True
        ws_feed._running = True
        assert ws_feed.is_ws_connected is True

    def test_is_ws_connected_false(self, ws_feed):
        ws_feed.connected = False
        assert ws_feed.is_ws_connected is False

    def test_health_status_disconnected(self, ws_feed):
        status = ws_feed.health_status()
        assert status["symbol"] == "XRPUSDT"
        assert status["connected"] is False
        assert status["has_data_15m"] is False

    def test_health_status_connected_with_data(self, ws_feed):
        ws_feed.connected = True
        ws_feed._running = True
        ws_feed._df_15m = pd.DataFrame({"close": [0.5]})
        ws_feed._df_1h = pd.DataFrame({"close": [0.6]})
        ws_feed._last_update_15m = time.time()
        ws_feed._last_update_1h = time.time()
        status = ws_feed.health_status()
        assert status["connected"] is True
        assert status["has_data_15m"] is True
        assert status["has_data_1h"] is True
        assert status["data_15m_age_s"] >= 0


# ── Spread ────────────────────────────────────────────────


class TestWebSocketFeedSpread:
    """Verifica get_spread_percent."""

    def test_get_spread_percent_no_rest(self, ws_feed):
        ws_feed._rest = None
        spread = ws_feed.get_spread_percent()
        assert spread == 0.0

    def test_get_spread_percent_no_fallback_after_fase5(self, ws_feed):
        """
        FASE 5: get_spread_percent() ya no usa REST fallback.
        Sin orderbook listo, devuelve 0.0.
        """
        ws_feed._rest = None
        ws_feed._ob_ready = False
        spread = ws_feed.get_spread_percent()
        assert spread == 0.0


# ── Orderbook Init ────────────────────────────────────────


class TestWebSocketFeedOrderbookInit:
    """Verifica init con parámetros de orderbook."""

    def test_init_orderbook_defaults(self, ws_feed):
        assert ws_feed._ob_enabled is True
        assert ws_feed._ob_depth == 50
        assert ws_feed._ob_ready is False
        assert ws_feed._ob_bids == []
        assert ws_feed._ob_asks == []

    def test_init_orderbook_custom_params(self):
        with patch("bot.core.ws_feed.BinanceFuturesClient"), \
             patch("bot.core.ws_feed.DataFeed"):
            from bot.core.ws_feed import WebSocketFeed
            feed = WebSocketFeed(symbol="XRPUSDT", testnet=True,
                                 orderbook_enabled=False, orderbook_depth=200)
            assert feed._ob_enabled is False
            assert feed._ob_depth == 200


# ── Handle Depth ──────────────────────────────────────────


class TestWebSocketFeedHandleDepth:
    """Verifica _handle_depth (depthUpdate de Binance)."""

    def _make_depth_msg(self, bids=None, asks=None) -> dict:
        return {
            "e": "depthUpdate",
            "E": 1714320899000,
            "b": [["0.6180", "1000"]] if bids is None else bids,
            "a": [["0.6190", "1500"]] if asks is None else asks,
        }

    def test_depth_stores_bids_asks(self, ws_feed):
        msg = self._make_depth_msg(
            bids=[["0.6180", "1000"], ["0.6175", "2000"]],
            asks=[["0.6190", "1500"], ["0.6195", "3000"]],
        )
        ws_feed._handle_depth(msg)
        assert ws_feed._ob_ready is True
        assert len(ws_feed._ob_bids) == 2
        assert len(ws_feed._ob_asks) == 2
        assert ws_feed._ob_bids[0] == [0.6180, 1000.0]
        assert ws_feed._ob_asks[0] == [0.6190, 1500.0]

    def test_depth_replaces_levels(self, ws_feed):
        """Cada depthUpdate sobrescribe los niveles anteriores."""
        msg1 = self._make_depth_msg(
            bids=[["0.6180", "1000"]],
            asks=[["0.6190", "1500"]],
        )
        ws_feed._handle_depth(msg1)
        assert len(ws_feed._ob_bids) == 1

        # Segundo depthUpdate con datos completamente diferentes
        msg2 = self._make_depth_msg(
            bids=[["0.6175", "500"], ["0.6170", "300"]],
            asks=[["0.6195", "800"]],
        )
        ws_feed._handle_depth(msg2)
        assert len(ws_feed._ob_bids) == 2
        assert ws_feed._ob_bids[0] == [0.6175, 500.0]  # nuevo mejor bid

    def test_depth_updates_timestamp(self, ws_feed):
        msg = self._make_depth_msg()
        ws_feed._handle_depth(msg)
        assert ws_feed._last_update_ob > 0

    def test_depth_calls_external_callback(self, ws_feed):
        cb = MagicMock()
        ws_feed.on_orderbook = cb
        msg = self._make_depth_msg()
        ws_feed._handle_depth(msg)
        cb.assert_called_once()
        call_data = cb.call_args[0][0]
        assert call_data["bid"] == 0.6180
        assert call_data["ask"] == 0.6190

    def test_depth_no_callback_when_empty(self, ws_feed):
        """Sin bids ni asks en el mensaje, callback no debe dispararse."""
        cb = MagicMock()
        ws_feed.on_orderbook = cb
        # Aseguramos estado limpio
        ws_feed._ob_bids = []
        ws_feed._ob_asks = []
        ws_feed._ob_ready = False
        msg = self._make_depth_msg(bids=[], asks=[])
        ws_feed._handle_depth(msg)
        # _ob_best_locked requiere bids y asks no vacíos
        cb.assert_not_called()

    def test_depth_callback_exception_does_not_crash(self, ws_feed):
        cb = MagicMock(side_effect=ValueError("callback error"))
        ws_feed.on_orderbook = cb
        msg = self._make_depth_msg()
        ws_feed._handle_depth(msg)  # no debe lanzar

    def test_depth_sorts_bids_descending(self, ws_feed):
        msg = self._make_depth_msg(
            bids=[["0.6175", "500"], ["0.6180", "1000"], ["0.6170", "300"]],
        )
        ws_feed._handle_depth(msg)
        prices = [b[0] for b in ws_feed._ob_bids]
        assert prices == [0.6180, 0.6175, 0.6170]  # descendente

    def test_depth_sorts_asks_ascending(self, ws_feed):
        msg = self._make_depth_msg(
            asks=[["0.6200", "500"], ["0.6190", "1000"], ["0.6195", "300"]],
        )
        ws_feed._handle_depth(msg)
        prices = [a[0] for a in ws_feed._ob_asks]
        assert prices == [0.6190, 0.6195, 0.6200]  # ascendente

    def test_depth_respects_max_depth(self, ws_feed):
        ws_feed._ob_depth = 2
        msg = self._make_depth_msg(
            bids=[["0.6180", "1000"], ["0.6175", "500"],
                  ["0.6170", "300"], ["0.6165", "200"]],
        )
        ws_feed._handle_depth(msg)
        assert len(ws_feed._ob_bids) == 2


# ── Orderbook API ─────────────────────────────────────────


class TestWebSocketFeedOrderbookAPI:
    """Verifica la API pública del orderbook."""

    def test_get_orderbook_best_returns_none_when_not_ready(self, ws_feed):
        assert ws_feed.get_orderbook_best() is None

    def test_get_orderbook_best_returns_data(self, ws_feed):
        ws_feed._handle_depth({
            "e": "depthUpdate",
            "E": 1714320899000,
            "b": [["0.6180", "1000"]],
            "a": [["0.6190", "1500"]],
        })
        best = ws_feed.get_orderbook_best()
        assert best is not None
        assert best["bid"] == 0.6180
        assert best["ask"] == 0.6190
        assert best["bid_size"] == 1000.0
        assert best["ask_size"] == 1500.0
        assert best["spread_pct"] > 0

    def test_get_orderbook_returns_levels(self, ws_feed):
        ws_feed._handle_depth({
            "e": "depthUpdate",
            "E": 1714320899000,
            "b": [["0.6180", "1000"], ["0.6175", "500"], ["0.6170", "200"]],
            "a": [["0.6190", "1500"], ["0.6195", "800"]],
        })
        ob = ws_feed.get_orderbook(levels=2)
        assert ob is not None
        assert len(ob["bids"]) == 2
        assert len(ob["asks"]) == 2
        assert ob["bid"] == 0.6180
        assert ob["ask"] == 0.6190

    def test_get_orderbook_returns_none_when_not_ready(self, ws_feed):
        assert ws_feed.get_orderbook(levels=5) is None

    def test_get_spread_percent_uses_orderbook_first(self, ws_feed):
        """Con orderbook listo, debe usarlo antes que REST."""
        ws_feed._handle_depth({
            "e": "depthUpdate",
            "E": 1714320899000,
            "b": [["0.6180", "1000"]],
            "a": [["0.6190", "1500"]],
        })
        spread = ws_feed.get_spread_percent()
        expected = ((0.6190 - 0.6180) / 0.6180) * 100
        assert spread == pytest.approx(expected, rel=1e-3)

    def test_get_spread_percent_fallback_rest_removed_in_fase5(self, ws_feed):
        """
        FASE 5: Sin orderbook listo, get_spread_percent() devuelve 0.0
        (REST fallback fue eliminado).
        """
        mock_rest = MagicMock()
        mock_rest.get_spread_percent = AsyncMock(return_value=0.05)
        ws_feed._rest = mock_rest
        ws_feed._ob_ready = False
        spread = ws_feed.get_spread_percent()
        assert spread == 0.0

    def test_get_spread_percent_zero_when_nothing_available(self, ws_feed):
        ws_feed._ob_ready = False
        ws_feed._rest = None
        spread = ws_feed.get_spread_percent()
        assert spread == 0.0


# ── Health with Orderbook ─────────────────────────────────


class TestWebSocketFeedHealthWithOrderbook:
    """Verifica health_status con orderbook."""

    def test_health_status_includes_orderbook_when_enabled(self, ws_feed):
        ws_feed._ob_enabled = True
        status = ws_feed.health_status()
        assert "orderbook" in status

    def test_health_status_orderbook_not_ready(self, ws_feed):
        ws_feed._ob_enabled = True
        ws_feed._ob_ready = False
        status = ws_feed.health_status()
        assert status["orderbook"] == {"ready": False}

    def test_health_status_orderbook_ready(self, ws_feed):
        ws_feed._ob_enabled = True
        ws_feed._handle_depth({
            "e": "depthUpdate",
            "E": 1714320899000,
            "b": [["0.6180", "1000"]],
            "a": [["0.6190", "1500"]],
        })
        ws_feed._running = True
        status = ws_feed.health_status()
        ob = status.get("orderbook", {})
        assert ob.get("bid") == 0.6180
        assert ob.get("ask") == 0.6190
        assert ob.get("spread_pct", 0) > 0
        assert ob.get("age_s", -1) >= 0
