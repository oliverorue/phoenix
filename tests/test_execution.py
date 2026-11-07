import pytest
from unittest.mock import MagicMock, AsyncMock
from bot.core.execution import OrderExecutor


class TestOrderExecutor:
    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.place_market_order = AsyncMock(return_value={"orderId": "order_123"})
        client.place_limit_order = AsyncMock(return_value={"orderId": "tp_order"})
        client.get_positions = AsyncMock(
            return_value=[{"size": 100.0, "side": "LONG", "avgPrice": 0.50}]
        )
        client.get_open_orders = AsyncMock(return_value=[])
        client.set_stop_loss = AsyncMock(return_value={"orderId": "sl_123"})
        client.update_stop_loss = AsyncMock(return_value={"orderId": "sl_updated"})
        client.set_trailing_stop = AsyncMock(return_value={})
        client.cancel_order = AsyncMock(return_value={})
        return client

    @pytest.fixture
    def mock_data_feed(self, mock_client):
        feed = MagicMock()
        feed.get_balance.return_value = 200.0
        feed.get_instrument_info.return_value = {
            "qtyStep": 1.0,
            "minOrderQty": 1.0,
            "maxOrderQty": 1000000.0,
            "minNotionalValue": 5.0,
            "maxNotionalValue": 1000000.0,
        }
        feed.symbol = "XRPUSDT"
        feed.circuit_breaker = MagicMock()
        feed.circuit_breaker.can_execute.return_value = True
        return feed

    @pytest.fixture
    def journal(self):
        j = MagicMock()
        j._open_trade = {"qty": 100.0}
        return j

    @pytest.fixture
    def executor(self, mock_data_feed, mock_client, journal):
        exec = OrderExecutor(mock_data_feed, journal)
        exec.client = mock_client  # Inyectar mock de BinanceFuturesClient
        return exec

    # ──── Estado inicial ────
    def test_initial_state(self, executor):
        assert executor.position_open == False
        assert executor.position_type is None
        assert executor.entry_price == 0.0
        assert executor.quantity == 0.0
        assert executor.tp1_hit == False

    # ──── Apertura de posición ────
    @pytest.mark.asyncio
    async def test_open_position_long(self, executor, mock_client):
        result = await executor.open_position(
            direction="LONG", entry=0.50, sl=0.495,
            tp1=0.51, tp2=0.52, qty=100.0
        )
        assert result == True
        assert executor.position_open == True
        assert executor.position_type == "LONG"

    @pytest.mark.asyncio
    async def test_open_position_short(self, executor, mock_client):
        await executor.open_position(
            direction="SHORT", entry=0.50, sl=0.505,
            tp1=0.49, tp2=0.48, qty=100.0
        )
        assert executor.position_type == "SHORT"

    @pytest.mark.asyncio
    async def test_open_position_closes_existing(self, executor, mock_client):
        executor.position_open = True
        executor.position_type = "LONG"
        executor.quantity = 50.0
        mock_client.get_positions.return_value = [
            {"size": 50.0, "side": "LONG", "avgPrice": 0.50}
        ]
        await executor.open_position(direction="SHORT", entry=0.50, sl=0.505,
                                     tp1=0.49, tp2=0.48, qty=80.0)
        # Verificar que se cerró la posición previa con market SELL
        close_calls = [
            c for c in mock_client.place_market_order.call_args_list
            if c[0][1] == "SELL"
        ]
        assert len(close_calls) >= 1

    @pytest.mark.asyncio
    async def test_open_position_invalid_qty_returns_false(self, executor):
        result = await executor.open_position("LONG", 0.50, 0.495, 0.51, 0.52, 0.0)
        assert result == False

    @pytest.mark.asyncio
    async def test_open_position_invalid_prices_returns_false(self, executor):
        result = await executor.open_position("LONG", 0.0, 0.495, 0.51, 0.52, 100.0)
        assert result == False

    # ──── Cierre de posición ────
    @pytest.mark.asyncio
    async def test_close_position(self, executor, mock_client):
        """Cierra posición activa y resetea estado vía _close_position_async."""
        executor.position_open = True
        executor.position_type = "LONG"
        executor.quantity = 100.0

        await executor._close_position_async()

        assert executor.position_open == False
        assert executor.position_type is None

    def test_close_position_no_position(self, executor, mock_client):
        """Si no hay posición, no hace nada (position_open=False por defecto)."""
        executor.close_position()
        mock_client.place_market_order.assert_not_called()

    # ──── Gestión de SL y Trailing ────
    @pytest.mark.asyncio
    async def test_update_stop_loss(self, executor, mock_client):
        """Actualiza SL vía _update_stop_loss_async."""
        executor.current_sl = 0.0
        executor.position_type = "LONG"
        await executor._update_stop_loss_async(0.50)
        assert executor.current_sl == 0.50
        mock_client.update_stop_loss.assert_called_once()

    @pytest.mark.asyncio
    async def test_activate_trailing_stop(self, executor, mock_client):
        """Activa trailing stop client-side vía _activate_trailing_async."""
        executor.position_type = "LONG"
        await executor._activate_trailing_async(0.005, 0.51)
        mock_client.set_trailing_stop.assert_called_once()

    # ──── Verificación de posición real en exchange ────

    @pytest.mark.asyncio
    async def test_has_real_position_true(self, executor, mock_client):
        """_has_real_position retorna True si hay posición abierta en exchange."""
        mock_client.get_positions.return_value = [
            {"size": 100.0, "side": "LONG"}
        ]
        assert await executor._has_real_position() == True

    @pytest.mark.asyncio
    async def test_has_real_position_false(self, executor, mock_client):
        """_has_real_position retorna False si no hay posición."""
        mock_client.get_positions.return_value = [
            {"size": 0.0, "side": "LONG"}
        ]
        assert await executor._has_real_position() == False

    @pytest.mark.asyncio
    async def test_has_real_position_empty_list(self, executor, mock_client):
        """_has_real_position retorna False si la lista está vacía."""
        mock_client.get_positions.return_value = []
        assert await executor._has_real_position() == False

    @pytest.mark.asyncio
    async def test_has_real_position_api_error_returns_false(self, executor, mock_client):
        """_has_real_position retorna False si la API falla (falso negativo seguro)."""
        mock_client.get_positions.side_effect = Exception("API error")
        assert await executor._has_real_position() == False

    @pytest.mark.asyncio
    async def test_open_position_rejects_when_real_position_exists(self, executor, mock_client):
        """open_position cierra la posición real existente antes de abrir una nueva."""
        # Simular posición real en exchange
        mock_client.get_positions.return_value = [
            {"size": 50.0, "side": "LONG", "avgPrice": 0.50}
        ]
        # No marcar position_open para probar detección por exchange
        executor.position_open = False
        result = await executor.open_position(
            direction="SHORT", entry=0.50, sl=0.505,
            tp1=0.49, tp2=0.48, qty=80.0
        )
        # Debería cerrar la posición real primero y luego abrir la nueva
        assert result == True

    # ──── Partial Fill Guard ────

    @pytest.mark.asyncio
    async def test_verify_position_qty_returns_size(self, executor, mock_client):
        """_verify_position_qty retorna el tamaño real de la posición."""
        # _verify_position_qty usa asyncio.run, que no funciona si el loop ya corre
        # Este test verifica el fallback: return self.quantity
        executor.quantity = 100.0
        qty = executor._verify_position_qty()
        assert qty == 100.0  # fallback porque el loop está corriendo

    @pytest.mark.asyncio
    async def test_full_fill_no_adjustment(self, executor, mock_client):
        """Fill completo (≥99.5%) no debe ajustar quantity."""
        result = await executor.open_position(
            direction="LONG", entry=0.50, sl=0.495,
            tp1=0.51, tp2=0.52, qty=100.0,
        )
        assert result == True
        assert executor.quantity == 100.0

    @pytest.mark.asyncio
    async def test_no_position_after_order_returns_false(self, executor, mock_client):
        """Si no se detecta posición tras colocar orden, open_position falla."""
        mock_client.get_positions.return_value = []
        result = await executor.open_position(
            direction="LONG", entry=0.50, sl=0.495,
            tp1=0.51, tp2=0.52, qty=100.0,
        )
        assert result == False
        assert executor.position_open == False
        assert executor.quantity == 0.0


class TestPhase3Execution:
    @pytest.fixture
    def mock_client(self):
        client = MagicMock()
        client.place_market_order = AsyncMock(
            return_value={"orderId": "o1", "executedQty": "100", "avgPrice": "0.50"}
        )
        client.place_limit_order = AsyncMock(return_value={"orderId": "tp"})
        client.get_positions = AsyncMock(
            return_value=[{"size": 100.0, "side": "LONG", "avgPrice": 0.50}]
        )
        client.get_open_orders = AsyncMock(return_value=[])
        client.set_stop_loss = AsyncMock(return_value={"orderId": "sl"})
        client.update_stop_loss = AsyncMock(return_value={"orderId": "sl2"})
        client.cancel_order = AsyncMock(return_value={})
        return client

    @pytest.fixture
    def data_feed(self):
        feed = MagicMock()
        feed.get_balance = AsyncMock(return_value=200.0)
        feed.symbol = "XRPUSDT"
        feed._instrument_cache = {"qtyStep": 1.0}
        return feed

    @pytest.fixture
    def executor(self, data_feed, mock_client):
        ex = OrderExecutor(data_feed)
        ex.client = mock_client
        ex._qty_step = 1.0
        return ex

    @pytest.mark.asyncio
    async def test_open_uses_actual_fill(self, executor, mock_client):
        mock_client.place_market_order.return_value = {
            "orderId": "o1", "executedQty": "97", "avgPrice": "0.5012",
        }
        mock_client.get_positions.return_value = [
            {"size": 97.0, "side": "LONG", "avgPrice": 0.5012}
        ]

        ok = await executor.open_position(
            "LONG", 0.50, 0.495, 0.51, 0.52, 100.0,
        )

        assert ok is True
        assert executor.entry_price == pytest.approx(0.5012)
        assert executor.quantity == pytest.approx(97.0)

    @pytest.mark.asyncio
    async def test_open_passes_unique_client_order_id(self, executor, mock_client):
        await executor.open_position("LONG", 0.50, 0.495, 0.51, 0.52, 100.0)

        kwargs = mock_client.place_market_order.call_args.kwargs
        assert kwargs["client_order_id"].startswith(executor._client_id_prefix)
        assert mock_client.set_stop_loss.call_args.kwargs["client_order_id"]

    @pytest.mark.asyncio
    async def test_close_uses_remaining_quantity_and_reduce_only(
        self, executor, mock_client
    ):
        executor.position_open = True
        executor.position_type = "LONG"
        executor.entry_price = 0.50
        executor.quantity = 35.0  # remanente tras TP1
        executor.tp1_hit = True
        executor.last_price = 0.52

        await executor._close_position_async()

        args, kwargs = mock_client.place_market_order.call_args
        assert args == ("XRPUSDT", "SELL", 35.0)
        assert kwargs["reduce_only"] is True

    @pytest.mark.asyncio
    async def test_sync_detects_tp1_partial_fill(self, executor, mock_client):
        executor.position_open = True
        executor.position_type = "LONG"
        executor.entry_price = 0.50
        executor.quantity = 100.0
        executor.tp1_price = 0.51
        executor.tp1_hit = False
        executor.journal = MagicMock()
        executor.journal._open_trade = {"qty": 100.0}
        mock_client.get_positions.return_value = [
            {"size": 35.0, "side": "LONG", "avgPrice": 0.50}
        ]

        result = await executor.sync_position_from_exchange()

        assert result["filled"] == pytest.approx(65.0)
        assert executor.quantity == pytest.approx(35.0)
        assert executor.tp1_hit is True
        assert executor.journal._open_trade["tp1_hit"] is True

    @pytest.mark.asyncio
    async def test_sync_detects_sl_external_close(self, executor, mock_client):
        executor.position_open = True
        executor.position_type = "LONG"
        executor.entry_price = 0.50
        executor.quantity = 100.0
        executor.current_sl = 0.495
        executor.last_price = 0.494
        executor.journal = MagicMock()
        executor.journal._open_trade = {}
        mock_client.get_positions.return_value = []

        result = await executor.sync_position_from_exchange()

        assert result["closed"] is True
        assert result["reason"] == "stop_loss"
        assert executor.position_open is False
        assert executor.unreported_close is True
        assert executor.last_close_pnl < 0

    @pytest.mark.asyncio
    async def test_cancel_limit_orders_only_cancels_bot_orders(
        self, executor, mock_client
    ):
        executor.position_open = True
        executor.position_type = "LONG"
        own = f"{executor._client_id_prefix}tp1"
        mock_client.get_open_orders.return_value = [
            {"orderId": "own", "type": "LIMIT", "reduceOnly": True,
             "clientOrderId": own},
            {"orderId": "other", "type": "LIMIT", "reduceOnly": True,
             "clientOrderId": "manual"},
        ]

        await executor._cancel_limit_orders()

        mock_client.cancel_order.assert_awaited_once_with("XRPUSDT", "own")
