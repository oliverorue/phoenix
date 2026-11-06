"""Tests de correctitud de BinanceFuturesClient (Fase 1)."""

from __future__ import annotations

import asyncio
import threading

import pytest

import bot.core.exchange_client as ec
from bot.core.exchange_client import (
    BinanceFuturesClient,
    MarketDataException,
)


class FakeRest:
    """REST falso con varios símbolos y registro de intervalos."""

    def __init__(self) -> None:
        self.last_interval: str | None = None
        self.thread_id: int | None = None

    def futures_exchange_info(self) -> dict:
        return {
            "symbols": [
                {
                    "symbol": "BTCUSDT",
                    "pricePrecision": 2,
                    "quantityPrecision": 3,
                    "filters": [
                        {
                            "filterType": "LOT_SIZE",
                            "stepSize": "0.001",
                            "minQty": "0.001",
                            "maxQty": "1000",
                        },
                        {
                            "filterType": "MIN_NOTIONAL",
                            "notional": "100",
                        },
                        {
                            "filterType": "PRICE_FILTER",
                            "tickSize": "0.1",
                            "minPrice": "0",
                            "maxPrice": "999999",
                        },
                    ],
                },
                {
                    "symbol": "XRPUSDT",
                    "pricePrecision": 4,
                    "quantityPrecision": 1,
                    "filters": [
                        {
                            "filterType": "LOT_SIZE",
                            "stepSize": "0.1",
                            "minQty": "0.1",
                            "maxQty": "1000000",
                        },
                        {
                            "filterType": "NOTIONAL",
                            "minNotional": "5",
                            "maxNotional": "10000",
                        },
                        {
                            "filterType": "PRICE_FILTER",
                            "tickSize": "0.0001",
                            "minPrice": "0",
                            "maxPrice": "99999",
                        },
                    ],
                },
            ]
        }

    def futures_klines(self, symbol, interval, limit, endTime=None):
        self.last_interval = interval
        ts = 1714320000000
        return [
            [ts, "1", "2", "0.5", "1.5", "100", ts + 899999,
             "150", 10, "50", "75", "0"],
        ]

    def futures_symbol_ticker(self, symbol):
        self.thread_id = threading.get_ident()
        return {"symbol": symbol, "price": "0.5"}


def make_client(rest: FakeRest) -> BinanceFuturesClient:
    client = BinanceFuturesClient.__new__(BinanceFuturesClient)
    client._rest = rest
    client._instrument_cache = {}
    client._ws_conn = None
    client._ws_task = None
    client._ws_callback = None
    client._ws_running = False
    client.testnet = True
    return client


class TestInstrumentInfo:
    @pytest.mark.asyncio
    async def test_returns_requested_symbol_not_first(self):
        client = make_client(FakeRest())
        info = await client.get_instrument_info("XRPUSDT")

        assert info["symbol"] == "XRPUSDT"
        assert info["step_size"] == pytest.approx(0.1)
        assert info["quantity_precision"] == 1
        assert info["min_qty"] == pytest.approx(0.1)
        assert info["min_notional"] == pytest.approx(5.0)
        assert info["max_notional"] == pytest.approx(10000.0)
        assert info["tick_size"] == pytest.approx(0.0001)

    @pytest.mark.asyncio
    async def test_raises_when_symbol_does_not_exist(self):
        client = make_client(FakeRest())
        with pytest.raises(MarketDataException, match="no encontrado"):
            await client.get_instrument_info("ETHUSDT")

    @pytest.mark.asyncio
    async def test_caches_per_symbol(self):
        client = make_client(FakeRest())
        first = await client.get_instrument_info("XRPUSDT")
        second = await client.get_instrument_info("XRPUSDT")
        assert first is second


class TestKlines:
    @pytest.mark.asyncio
    async def test_normalizes_numeric_interval_to_binance(self):
        rest = FakeRest()
        client = make_client(rest)
        rows = await client.get_klines("XRPUSDT", "60", 1)

        assert rest.last_interval == "1h"
        assert rows[0]["open_time"] == 1714320000000
        assert rows[0]["close"] == "1.5"

    @pytest.mark.asyncio
    async def test_normalizes_15_to_15m(self):
        rest = FakeRest()
        client = make_client(rest)
        await client.get_klines("XRPUSDT", "15", 1)
        assert rest.last_interval == "15m"


class TestQuantityFormatting:
    def test_uses_symbol_specific_step_and_precision(self):
        client = make_client(FakeRest())
        client._instrument_cache = {
            "XRPUSDT": {"step_size": 0.1, "quantity_precision": 1},
            "BTCUSDT": {"step_size": 0.001, "quantity_precision": 3},
        }

        assert client._format_quantity("xrpusdt", 12.34) == "12.3"
        assert client._format_quantity("BTCUSDT", 12.3456) == "12.345"


class TestNonBlockingRest:
    @pytest.mark.asyncio
    async def test_ticker_runs_in_worker_thread(self):
        rest = FakeRest()
        client = make_client(rest)
        main_thread = threading.get_ident()

        price = await client.get_ticker_price("XRPUSDT")

        assert price == 0.5
        assert rest.thread_id is not None
        assert rest.thread_id != main_thread


class TestWebSocketUrl:
    @pytest.mark.asyncio
    async def test_combined_stream_url(self, monkeypatch):
        captured: list[str] = []

        class FakeConn:
            def __aiter__(self):
                return self

            async def __anext__(self):
                await asyncio.sleep(3600)
                raise StopAsyncIteration

        class FakeContext:
            async def __aenter__(self):
                return FakeConn()

            async def __aexit__(self, *args):
                return False

        def fake_connect(url):
            captured.append(url)
            return FakeContext()

        monkeypatch.setattr(ec.ws, "connect", fake_connect)
        client = make_client(FakeRest())

        await client.subscribe_ws(
            ["xrpusdt@kline_15m", "xrpusdt@kline_1h"],
            lambda msg: None,
        )
        await asyncio.sleep(0)
        await client.close_ws()

        assert captured
        assert captured[0] == (
            "wss://stream.binancefuture.com/stream?"
            "streams=xrpusdt@kline_15m/xrpusdt@kline_1h"
        )
