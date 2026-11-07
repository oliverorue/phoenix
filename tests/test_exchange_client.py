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


XRP_INFO = {
    "symbol": "XRPUSDT",
    "step_size": 0.1,
    "quantity_precision": 1,
    "tick_size": 0.0001,
    "price_precision": 4,
    "min_qty": 0.1,
    "min_notional": 5.0,
}


class RecordingRest:
    """REST falso que registra kwargs de órdenes y devuelve datos fijos."""

    def __init__(self) -> None:
        self.order_calls: list[dict] = []
        self.balance_data: list[dict] = [
            {"asset": "USDT", "balance": "100.0", "availableBalance": "90.0"},
        ]
        self.order_data: dict = {
            "orderId": 7, "symbol": "XRPUSDT", "status": "FILLED",
            "side": "BUY", "type": "LIMIT", "origQty": "10",
            "executedQty": "10", "avgPrice": "1.2", "cumQuote": "12.0",
            "reduceOnly": False, "price": "1.2", "stopPrice": "0",
        }
        self.trades_data: list[dict] = [
            {"orderId": 1, "price": "1.1", "qty": "2", "commission": "0.001",
             "realizedPnl": "0", "side": "BUY", "time": 1},
            {"orderId": 2, "price": "1.2", "qty": "3", "commission": "0.002",
             "realizedPnl": "0.5", "side": "SELL", "time": 2},
        ]
        self.positions_data: list[dict] = [
            {"symbol": "XRPUSDT", "positionAmt": "100", "entryPrice": "1.1",
             "unRealizedProfit": "0.5"},
            {"symbol": "BTCUSDT", "positionAmt": "0", "entryPrice": "0",
             "unRealizedProfit": "0"},
            {"symbol": "ETHUSDT", "positionAmt": "-2", "entryPrice": "2200",
             "unRealizedProfit": "-1.2"},
        ]

    def futures_create_order(self, **kwargs):
        self.order_calls.append(kwargs)
        return {"orderId": 1, "status": "NEW", **kwargs}

    def futures_account_balance(self):
        return self.balance_data

    def futures_get_order(self, symbol, orderId):
        return self.order_data

    def futures_account_trades(self, symbol, limit):
        return self.trades_data

    def futures_position_information(self, symbol=None):
        if symbol is None:
            return self.positions_data
        return [p for p in self.positions_data if p["symbol"] == symbol]


def make_order_client(rest: RecordingRest) -> BinanceFuturesClient:
    client = make_client(rest)
    client._instrument_cache = {"XRPUSDT": dict(XRP_INFO)}
    return client


class TestPriceFormatting:
    def test_truncates_to_tick_size_per_symbol(self):
        client = make_client(FakeRest())
        client._instrument_cache = {
            "XRPUSDT": {"tick_size": 0.0001, "price_precision": 4},
            "BTCUSDT": {"tick_size": 0.1, "price_precision": 2},
        }
        assert client._format_price("xrpusdt", 1.15965) == "1.1596"
        assert client._format_price("BTCUSDT", 75746.95) == "75746.90"


class TestTakeProfit:
    @pytest.mark.asyncio
    async def test_full_close_uses_close_position(self):
        rest = RecordingRest()
        client = make_order_client(rest)
        await client.place_take_profit_market_order("XRPUSDT", "LONG", 1.30)

        call = rest.order_calls[-1]
        assert call["type"] == "TAKE_PROFIT_MARKET"
        assert call["side"] == "SELL"
        assert call["stopPrice"] == "1.3000"
        assert call["closePosition"] == "true"
        assert "quantity" not in call
        assert "reduceOnly" not in call

    @pytest.mark.asyncio
    async def test_partial_close_uses_reduce_only_and_qty(self):
        rest = RecordingRest()
        client = make_order_client(rest)
        await client.place_take_profit_market_order(
            "XRPUSDT", "SHORT", 1.10, qty=5.0,
        )

        call = rest.order_calls[-1]
        assert call["side"] == "BUY"
        assert call["reduceOnly"] == "true"
        assert call["quantity"] == "5.0"
        assert "closePosition" not in call


class TestReduceOnlyAudit:
    @pytest.mark.asyncio
    async def test_stop_loss_full_uses_close_position(self):
        rest = RecordingRest()
        client = make_order_client(rest)
        await client.set_stop_loss("XRPUSDT", "LONG", 1.15)

        call = rest.order_calls[-1]
        assert call["type"] == "STOP_MARKET"
        assert call["side"] == "SELL"
        assert call["closePosition"] == "true"

    @pytest.mark.asyncio
    async def test_stop_loss_partial_uses_reduce_only(self):
        rest = RecordingRest()
        client = make_order_client(rest)
        await client.set_stop_loss("XRPUSDT", "LONG", 1.15, qty=3.0)

        call = rest.order_calls[-1]
        assert call["reduceOnly"] == "true"
        assert call["quantity"] == "3.0"
        assert "closePosition" not in call

    @pytest.mark.asyncio
    async def test_limit_close_uses_reduce_only(self):
        rest = RecordingRest()
        client = make_order_client(rest)
        await client.place_limit_order("XRPUSDT", "SELL", 5.0, 1.2, reduce_only=True)

        call = rest.order_calls[-1]
        assert call["reduceOnly"] == "true"
        assert call["price"] == "1.2000"


class TestAccountBalance:
    @pytest.mark.asyncio
    async def test_parses_usdt(self):
        client = make_order_client(RecordingRest())
        b = await client.get_account_balance("USDT")
        assert b["balance"] == pytest.approx(100.0)
        assert b["available"] == pytest.approx(90.0)

    @pytest.mark.asyncio
    async def test_missing_asset_returns_zero(self):
        rest = RecordingRest()
        rest.balance_data = [
            {"asset": "BTC", "balance": "1", "availableBalance": "1"},
        ]
        client = make_order_client(rest)
        b = await client.get_account_balance("USDT")
        assert b["balance"] == 0.0
        assert b["available"] == 0.0


class TestOrderStatus:
    @pytest.mark.asyncio
    async def test_standardizes_fields(self):
        client = make_order_client(RecordingRest())
        o = await client.get_order_status("XRPUSDT", 7)
        assert o["status"] == "FILLED"
        assert o["executed_qty"] == pytest.approx(10.0)
        assert o["avg_price"] == pytest.approx(1.2)
        assert o["reduce_only"] is False


class TestFills:
    @pytest.mark.asyncio
    async def test_filters_by_order_id(self):
        client = make_order_client(RecordingRest())
        fills = await client.get_fills("XRPUSDT", order_id=2)
        assert len(fills) == 1
        assert fills[0]["order_id"] == 2
        assert fills[0]["realized_pnl"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_returns_all_when_no_order_id(self):
        client = make_order_client(RecordingRest())
        fills = await client.get_fills("XRPUSDT")
        assert len(fills) == 2


class TestAllPositions:
    @pytest.mark.asyncio
    async def test_omits_zero_and_standardizes(self):
        client = make_order_client(RecordingRest())
        positions = await client.get_all_positions()
        assert len(positions) == 2
        assert positions[0]["symbol"] == "XRPUSDT"
        assert positions[0]["side"] == "LONG"
        assert positions[1]["symbol"] == "ETHUSDT"
        assert positions[1]["side"] == "SHORT"
