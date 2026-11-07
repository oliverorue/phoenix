"""Tests del broker live (mapeo de dirección y cierres reduceOnly)."""

from __future__ import annotations

import pytest

from bot.core.exchange_client import BinanceFuturesClient
from bot.execution.broker import LiveBroker


class RecordingRest:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def futures_exchange_info(self):
        return {
            "symbols": [{
                "symbol": "XRPUSDT", "pricePrecision": 4, "quantityPrecision": 1,
                "filters": [
                    {"filterType": "LOT_SIZE", "stepSize": "0.1",
                     "minQty": "0.1", "maxQty": "1000000"},
                    {"filterType": "NOTIONAL", "minNotional": "5",
                     "maxNotional": "10000"},
                    {"filterType": "PRICE_FILTER", "tickSize": "0.0001",
                     "minPrice": "0", "maxPrice": "99999"},
                ],
            }]
        }

    def futures_create_order(self, **kwargs):
        self.calls.append(("order", kwargs))
        return {"orderId": 1, "status": "NEW", **kwargs}

    def futures_change_leverage(self, symbol, leverage):
        self.calls.append(("leverage", (symbol, leverage)))
        return {}

    def futures_change_margin_type(self, symbol, marginType):
        self.calls.append(("margin", (symbol, marginType)))
        return {}

    def futures_position_information(self, symbol=None):
        return []

    def futures_get_order(self, symbol, orderId):
        return {}

    def futures_account_balance(self):
        return []

    def futures_cancel_all_open_orders(self, symbol):
        self.calls.append(("cancel", symbol))
        return None


def make_client(rest: RecordingRest) -> BinanceFuturesClient:
    client = BinanceFuturesClient.__new__(BinanceFuturesClient)
    client._rest = rest
    client._instrument_cache = {}
    client._ws_conn = None
    client._ws_task = None
    client._ws_callback = None
    client._ws_running = False
    client.testnet = True
    return client


def _last_order(rest: RecordingRest) -> dict:
    return [c for c in rest.calls if c[0] == "order"][-1][1]


class TestLiveBroker:
    @pytest.mark.asyncio
    async def test_open_long_buys_no_reduce_only(self):
        rest = RecordingRest()
        broker = LiveBroker(make_client(rest))
        await broker.open_market("XRPUSDT", "LONG", 5.0)

        call = _last_order(rest)
        assert call["side"] == "BUY"
        assert "reduceOnly" not in call

    @pytest.mark.asyncio
    async def test_open_short_sells(self):
        rest = RecordingRest()
        broker = LiveBroker(make_client(rest))
        await broker.open_limit("XRPUSDT", "SHORT", 5.0, 1.2)

        call = _last_order(rest)
        assert call["side"] == "SELL"
        assert call["type"] == "LIMIT"

    @pytest.mark.asyncio
    async def test_close_market_reduce_only_opposite_side(self):
        rest = RecordingRest()
        broker = LiveBroker(make_client(rest))
        await broker.close_market("XRPUSDT", "LONG", 5.0)

        call = _last_order(rest)
        assert call["side"] == "SELL"
        assert call["reduceOnly"] == "true"

    @pytest.mark.asyncio
    async def test_set_sl_uses_close_position(self):
        rest = RecordingRest()
        broker = LiveBroker(make_client(rest))
        await broker.set_sl("XRPUSDT", "LONG", 1.0)

        call = _last_order(rest)
        assert call["type"] == "STOP_MARKET"
        assert call["side"] == "SELL"
        assert call["closePosition"] == "true"

    @pytest.mark.asyncio
    async def test_set_tp_uses_close_position(self):
        rest = RecordingRest()
        broker = LiveBroker(make_client(rest))
        await broker.set_tp("XRPUSDT", "LONG", 1.1)

        call = _last_order(rest)
        assert call["type"] == "TAKE_PROFIT_MARKET"
        assert call["closePosition"] == "true"

    @pytest.mark.asyncio
    async def test_set_margin_isolated_and_leverage(self):
        rest = RecordingRest()
        broker = LiveBroker(make_client(rest))
        await broker.set_margin("XRPUSDT", 2)

        margins = [c for c in rest.calls if c[0] == "margin"]
        leverages = [c for c in rest.calls if c[0] == "leverage"]
        assert margins[0][1] == ("XRPUSDT", "ISOLATED")
        assert leverages[0][1] == ("XRPUSDT", 2)
