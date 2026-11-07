"""Tests de recuperación de posiciones y protecciones (Fase 3)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.core.exchange_recovery import ExchangeStateRecovery


@pytest.fixture
def client():
    c = MagicMock()
    c.get_positions = AsyncMock(return_value=[])
    c.get_open_orders = AsyncMock(return_value=[])
    c.cancel_order = AsyncMock(return_value={})
    c.set_stop_loss = AsyncMock(return_value={"orderId": "sl"})
    c.place_limit_order = AsyncMock(return_value={"orderId": "tp"})
    return c


@pytest.fixture
def runner(client):
    r = MagicMock()
    r.symbol = "XRPUSDT"
    r.executor.session = client
    r.executor._ensure_client = MagicMock(return_value=client)
    r.executor.position_open = False
    r.executor.position_type = None
    r.executor.entry_price = 0.0
    r.executor.quantity = 0.0
    r.executor.current_sl = 0.0
    r.executor.tp1_price = 0.0
    r.executor.tp2_price = 0.0
    r.executor.tp1_hit = False
    r.executor.trailing_activated = False
    r.executor._truncate_qty = lambda q: round(q, 1)
    r.executor.close_position_async = AsyncMock(return_value=True)
    r.risk_mgr.get_phase_config = AsyncMock(
        return_value=(0, {"tp1_porcentaje": 0.65})
    )
    return r


class TestRecoverPositions:
    @pytest.mark.asyncio
    async def test_recovers_exchange_position_without_cancelling_orders(
        self, runner, client
    ):
        client.get_positions.return_value = [
            {"size": 100.0, "side": "LONG", "avgPrice": 0.5}
        ]

        await ExchangeStateRecovery().recover_positions([runner])

        assert runner.executor.position_open is True
        assert runner.executor.quantity == 100.0
        assert runner.executor.entry_price == 0.5
        client.cancel_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_position_sets_closed(self, runner, client):
        client.get_positions.return_value = []
        runner.executor.position_open = True

        await ExchangeStateRecovery().recover_positions([runner])

        assert runner.executor.position_open is False


class TestPersistentState:
    def test_ignores_stale_json_when_exchange_has_no_position(self, runner):
        runner.executor.position_open = False
        saved = {
            "XRPUSDT": {
                "position_open": True,
                "position_type": "LONG",
                "quantity": 100.0,
            }
        }
        with patch(
            "bot.core.exchange_recovery.StateRecovery.load_all_symbols",
            return_value=saved,
        ):
            ExchangeStateRecovery().recover_persistent_state([runner])

        assert runner.executor.position_open is False

    def test_applies_json_when_exchange_position_matches(self, runner):
        runner.executor.position_open = True
        runner.executor.position_type = "LONG"
        runner.executor.quantity = 35.0
        saved = {
            "XRPUSDT": {
                "position_open": True,
                "position_type": "LONG",
                "quantity": 35.0,
                "current_sl": 0.5,
                "tp1_price": 0.51,
                "tp2_price": 0.52,
                "tp1_hit": True,
            }
        }
        with patch(
            "bot.core.exchange_recovery.StateRecovery.load_all_symbols",
            return_value=saved,
        ):
            ExchangeStateRecovery().recover_persistent_state([runner])

        assert runner.executor.current_sl == 0.5
        assert runner.executor.tp1_price == 0.51
        assert runner.executor.tp1_hit is True

    def test_ignores_json_with_mismatched_side(self, runner):
        runner.executor.position_open = True
        runner.executor.position_type = "LONG"
        saved = {
            "XRPUSDT": {
                "position_open": True,
                "position_type": "SHORT",
                "current_sl": 0.5,
            }
        }
        with patch(
            "bot.core.exchange_recovery.StateRecovery.load_all_symbols",
            return_value=saved,
        ):
            ExchangeStateRecovery().recover_persistent_state([runner])

        assert runner.executor.current_sl == 0.0


class TestRecoverProtection:
    @pytest.mark.asyncio
    async def test_replaces_missing_sl_and_tps(self, runner, client):
        runner.executor.position_open = True
        runner.executor.position_type = "LONG"
        runner.executor.quantity = 100.0
        runner.executor.current_sl = 0.495
        runner.executor.tp1_price = 0.51
        runner.executor.tp2_price = 0.52
        runner.executor.tp1_hit = False

        await ExchangeStateRecovery().recover_tp_orders([runner])

        client.set_stop_loss.assert_awaited_once()
        assert client.place_limit_order.await_count == 2

    @pytest.mark.asyncio
    async def test_does_not_duplicate_existing_sl(self, runner, client):
        runner.executor.position_open = True
        runner.executor.position_type = "LONG"
        runner.executor.current_sl = 0.495
        client.get_open_orders.return_value = [
            {"type": "STOP_MARKET", "side": "SELL", "orderId": "sl"}
        ]

        await ExchangeStateRecovery().recover_tp_orders([runner])

        client.set_stop_loss.assert_not_called()

    @pytest.mark.asyncio
    async def test_closes_position_if_no_sl_price_known(self, runner, client):
        runner.executor.position_open = True
        runner.executor.position_type = "LONG"
        runner.executor.current_sl = 0.0

        await ExchangeStateRecovery().recover_tp_orders([runner])

        runner.executor.close_position_async.assert_awaited_once_with(
            reason="recovery_no_sl"
        )

    @pytest.mark.asyncio
    async def test_does_not_duplicate_existing_tps(self, runner, client):
        runner.executor.position_open = True
        runner.executor.position_type = "LONG"
        runner.executor.current_sl = 0.495
        runner.executor.tp1_price = 0.51
        runner.executor.tp2_price = 0.52
        client.get_open_orders.return_value = [
            {"type": "STOP_MARKET", "side": "SELL", "orderId": "sl"},
            {"type": "LIMIT", "side": "SELL", "reduceOnly": True,
             "orderId": "t1", "price": "0.51"},
            {"type": "LIMIT", "side": "SELL", "reduceOnly": True,
             "orderId": "t2", "price": "0.52"},
        ]

        await ExchangeStateRecovery().recover_tp_orders([runner])

        client.place_limit_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_after_tp1_tp2_covers_remaining(self, runner, client):
        runner.executor.position_open = True
        runner.executor.position_type = "LONG"
        runner.executor.quantity = 35.0
        runner.executor.current_sl = 0.5
        runner.executor.tp1_price = 0.51
        runner.executor.tp2_price = 0.52
        runner.executor.tp1_hit = True
        client.get_open_orders.return_value = [
            {"type": "STOP_MARKET", "side": "SELL", "orderId": "sl"}
        ]

        await ExchangeStateRecovery().recover_tp_orders([runner])

        client.place_limit_order.assert_awaited_once()
        kwargs = client.place_limit_order.call_args.kwargs
        assert kwargs["price"] == 0.52
        assert kwargs["qty"] == 35.0
