"""Tests para DataFeed (FASE 5.2)."""

import pytest
import pandas as pd
from unittest.mock import MagicMock, AsyncMock, patch


class TestDataFeedInit:
    """Verifica la inicialización del DataFeed."""

    def test_init_creates_http_session(self):
        with patch("bot.core.data_feed.BinanceFuturesClient") as mock_client, \
             patch("bot.core.data_feed.API_KEY", ""), \
             patch("bot.core.data_feed.API_SECRET", ""):
            from bot.core.data_feed import DataFeed
            feed = DataFeed(symbol="XRPUSDT", testnet=True)
            mock_client.assert_called_once_with(
                api_key="", api_secret="", testnet=True
            )
            assert feed.symbol == "XRPUSDT"
            assert feed.testnet is True

    def test_init_creates_circuit_breaker(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            feed = DataFeed()
            assert feed.circuit_breaker is not None
            assert feed._latency_samples == {}


class TestDataFeedLatency:
    """Verifica el registro y cálculo de latencia."""

    def test_record_latency_new_endpoint(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            feed = DataFeed()
            feed._record_latency("test_api", 0.15)
            assert "test_api" in feed._latency_samples
            assert feed._latency_samples["test_api"] == [0.15]

    def test_record_latency_appends(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            feed = DataFeed()
            feed._record_latency("test", 0.1)
            feed._record_latency("test", 0.2)
            assert feed._latency_samples["test"] == [0.1, 0.2]

    def test_record_latency_max_samples(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            feed = DataFeed()
            feed._max_latency_samples = 3
            for i in range(5):
                feed._record_latency("test", float(i))
            assert len(feed._latency_samples["test"]) == 3

    def test_calc_stats_empty(self):
        from bot.core.data_feed import DataFeed
        stats = DataFeed._calc_stats("test", [])
        assert stats["count"] == 0
        assert stats["min"] == 0.0

    def test_calc_stats_with_samples(self):
        from bot.core.data_feed import DataFeed
        stats = DataFeed._calc_stats("test", [0.1, 0.2, 0.3, 0.4, 0.5])
        assert stats["count"] == 5
        assert stats["min"] == 0.1
        assert stats["max"] == 0.5
        assert stats["avg"] == 0.3

    def test_get_latency_stats_single_endpoint(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            feed = DataFeed()
            feed._record_latency("ep1", 0.1)
            stats = feed.get_latency_stats("ep1")
            assert stats["endpoint"] == "ep1"
            assert stats["count"] == 1

    def test_get_latency_stats_all_endpoints(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            feed = DataFeed()
            feed._record_latency("ep1", 0.1)
            feed._record_latency("ep2", 0.2)
            stats = feed.get_latency_stats()
            assert "ep1" in stats
            assert "ep2" in stats

    def test_get_all_latency_stats(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            feed = DataFeed()
            feed._record_latency("ep1", 0.1)
            stats = feed.get_all_latency_stats()
            assert "ep1" in stats


class TestDataFeedRunSync:
    """Verifica asyncio.to_thread (se usa internamente para operaciones sync)."""

    @pytest.mark.asyncio
    async def test_to_thread_executes_function(self):
        import asyncio
        def sync_func(a, b):
            return a + b
        result = await asyncio.to_thread(sync_func, 1, 2)
        assert result == 3


class TestDataFeedGetKlines:
    """Verifica get_klines."""

    @pytest.fixture
    def feed_with_client(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            feed = DataFeed()
            feed._client = MagicMock()
            feed._client.get_klines = AsyncMock()
            yield feed

    @pytest.mark.asyncio
    async def test_get_klines_returns_dataframe(self, feed_with_client):
        feed = feed_with_client
        feed._client.get_klines.return_value = [
            {"open_time": 1714320000000, "open": "0.6185", "high": "0.6190", "low": "0.6180", "close": "0.6188", "volume": "1000"},
            {"open_time": 1714320900000, "open": "0.6188", "high": "0.6192", "low": "0.6183", "close": "0.6190", "volume": "1200"},
        ]
        df = await feed.get_klines("15", limit=2)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert "open" in df.columns
        assert "close" in df.columns
        assert df["close"].iloc[-1] == 0.6190

    @pytest.mark.asyncio
    async def test_get_klines_empty_returns_empty_df(self, feed_with_client):
        feed = feed_with_client
        feed._client.get_klines.return_value = []
        df = await feed.get_klines("15")
        assert isinstance(df, pd.DataFrame)
        assert df.empty

    @pytest.mark.asyncio
    async def test_get_klines_raises_datafeed_exception(self, feed_with_client):
        from bot.core.exceptions import DataFeedException
        from bot.core.exchange_client import MarketDataException
        feed = feed_with_client
        feed._client.get_klines.side_effect = MarketDataException("API error")
        with pytest.raises(DataFeedException):
            await feed.get_klines("15")

    @pytest.mark.asyncio
    async def test_current_1h_delegates_to_get_klines(self, feed_with_client):
        feed = feed_with_client
        mock_df = pd.DataFrame({"close": [0.5]})
        with patch.object(feed, "get_klines", new=AsyncMock(return_value=mock_df)) as mock_get:
            result = await feed.current_1h()
            mock_get.assert_called_once()
            assert not result.empty

    @pytest.mark.asyncio
    async def test_current_15m_delegates_to_get_klines(self, feed_with_client):
        feed = feed_with_client
        mock_df = pd.DataFrame({"close": [0.5]})
        with patch.object(feed, "get_klines", new=AsyncMock(return_value=mock_df)) as mock_get:
            result = await feed.current_15m()
            mock_get.assert_called_once()
            assert not result.empty



class TestDataFeedGetBalance:
    """Verifica get_balance."""

    @pytest.mark.asyncio
    async def test_get_balance_returns_float(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            feed = DataFeed()
            # get_balance() llama asyncio.to_thread(self._client._rest.futures_account)
            feed._client._rest.futures_account.return_value = {  # type: ignore[reportAttributeAccessIssue]
                "assets": [{"asset": "USDT", "walletBalance": "150.50"}]
            }
            balance = await feed.get_balance()
            assert balance == 150.50

    @pytest.mark.asyncio
    async def test_get_balance_raises_datafeed_exception(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            from bot.core.exceptions import DataFeedException
            feed = DataFeed()
            feed._client._rest.futures_account.side_effect = Exception("API error")  # type: ignore[reportAttributeAccessIssue]
            with pytest.raises(DataFeedException):
                await feed.get_balance()


class TestDataFeedGetInstrumentInfo:
    """Verifica get_instrument_info."""

    @pytest.mark.asyncio
    async def test_get_instrument_info_cached(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            feed = DataFeed()
            feed._instrument_cache = {"qtyStep": 0.001}
            result = await feed.get_instrument_info()
            assert result["qtyStep"] == 0.001

    @pytest.mark.asyncio
    async def test_get_instrument_info_fetches(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            feed = DataFeed()
            # get_instrument_info() llama await self._client.get_instrument_info(self.symbol)
            feed._client.get_instrument_info = AsyncMock(return_value={
                "step_size": 0.001,
                "min_qty": 0.001,
                "max_qty": 1000.0,
                "min_notional": 1.0,
            })
            result = await feed.get_instrument_info()
            assert result["qtyStep"] == 0.001
            assert result["minOrderQty"] == 0.001

    @pytest.mark.asyncio
    async def test_get_instrument_info_fallback(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            from bot.core.exchange_client import MarketDataException
            feed = DataFeed()
            feed.symbol = "DOGEUSDT"
            feed._client.get_instrument_info = AsyncMock(side_effect=MarketDataException("API error"))
            result = await feed.get_instrument_info()
            assert result["qtyStep"] == 1.0  # fallback DOGEUSDT

    @pytest.mark.asyncio
    async def test_get_instrument_info_no_fallback_raises(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            from bot.core.exceptions import DataFeedException
            from bot.core.exchange_client import MarketDataException
            feed = DataFeed()
            feed.symbol = "UNKNOWNSYM"
            feed._client.get_instrument_info = AsyncMock(side_effect=MarketDataException("API error"))
            with pytest.raises(DataFeedException):
                await feed.get_instrument_info()


class TestDataFeedWaitForNextCandle:
    """Verifica wait_for_next_candle."""

    @pytest.mark.asyncio
    async def test_wait_for_next_candle_sleeps(self):
        from bot.core.data_feed import DataFeed
        with patch("bot.core.data_feed.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            await DataFeed.wait_for_next_candle(interval_minutes=1)
            mock_sleep.assert_awaited_once()


class TestDataFeedGetSpread:
    """Verifica get_spread_percent."""

    @pytest.mark.asyncio
    async def test_get_spread_percent_returns_value(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            feed = DataFeed()
            # get_spread_percent() llama asyncio.to_thread(self._client._rest.futures_order_book, ...)
            feed._client._rest.futures_order_book.return_value = {  # type: ignore[reportAttributeAccessIssue]
                "bids": [["0.6185", "1000"]],
                "asks": [["0.6190", "800"]],
            }
            spread = await feed.get_spread_percent()
            assert spread > 0

    @pytest.mark.asyncio
    async def test_get_spread_percent_default_on_error(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            feed = DataFeed()
            feed._client._rest.futures_order_book.side_effect = Exception("API error")  # type: ignore[reportAttributeAccessIssue]
            spread = await feed.get_spread_percent()
            assert spread == 0.1  # default

    @pytest.mark.asyncio
    async def test_get_spread_percent_zero_bid_returns_default(self):
        with patch("bot.core.data_feed.BinanceFuturesClient"):
            from bot.core.data_feed import DataFeed
            feed = DataFeed()
            feed._client._rest.futures_order_book.return_value = {  # type: ignore[reportAttributeAccessIssue]
                "bids": [],
                "asks": [],
            }
            spread = await feed.get_spread_percent()
            assert spread == 0.1


class TestDecimalPlaces:
    """Verifica decimal_places_for_qty_step."""

    def test_zero_step_returns_8(self):
        from bot.core.data_feed import decimal_places_for_qty_step
        assert decimal_places_for_qty_step(0) == 8

    def test_integer_step_returns_0(self):
        from bot.core.data_feed import decimal_places_for_qty_step
        assert decimal_places_for_qty_step(1.0) == 0
        assert decimal_places_for_qty_step(100.0) == 0

    def test_fractional_step(self):
        from bot.core.data_feed import decimal_places_for_qty_step
        assert decimal_places_for_qty_step(0.001) == 3
        assert decimal_places_for_qty_step(0.1) == 1
