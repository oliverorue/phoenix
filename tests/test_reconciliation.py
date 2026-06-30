"""
Tests para OrderReconciler — Reconciliación de órdenes (Fase 5 — async API).
"""

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.fixture
def reconciler():
    """Crea OrderReconciler con mocks."""
    with patch("bot.core.reconciliation.ExchangeStateRecovery"):
        from bot.core.reconciliation import OrderReconciler
        rec = OrderReconciler(
            database=MagicMock(),
            notifier=MagicMock(),
            interval_cycles=1,  # ejecutar siempre para tests
        )
        yield rec


def _make_client_mock() -> MagicMock:
    """Crea un mock de BinanceFuturesClient con métodos async."""
    client = MagicMock()
    client.get_open_orders = AsyncMock(return_value=[])
    client.get_positions = AsyncMock(return_value=[])
    client.cancel_order = AsyncMock()
    return client


@pytest.fixture
def mock_runner():
    """Crea un runner mock con executor que usa _ensure_client()."""
    runner = MagicMock()
    runner.symbol = "XRPUSDT"
    runner.executor = MagicMock()
    runner.executor.position_open = False
    runner.executor.position_type = None
    runner.executor.entry_price = 0.0
    runner.executor.quantity = 0.0
    runner.executor.current_sl = 0.0
    runner.executor.tp1_price = 0.0
    runner.executor.tp2_price = 0.0
    runner.executor.tp1_hit = False
    runner.executor.trailing_activated = False
    runner.executor._ensure_client = MagicMock()
    runner.executor._ensure_client.return_value = _make_client_mock()
    return runner


class TestOrderReconcilerInit:
    """Verifica inicialización."""

    def test_init_defaults(self):
        with patch("bot.core.reconciliation.ExchangeStateRecovery"):
            from bot.core.reconciliation import OrderReconciler
            rec = OrderReconciler()
            assert rec.interval_cycles == 12
            assert rec.database is None
            assert rec.notifier is None
            assert rec._cycle_counter == 0

    def test_init_custom(self, reconciler):
        assert reconciler.interval_cycles == 1
        assert reconciler.database is not None
        assert reconciler.notifier is not None

    def test_stats_initial(self, reconciler):
        stats = reconciler.stats
        assert stats["total_reconciliations"] == 0
        assert stats["orphans_cancelled"] == 0
        assert stats["desyncs_fixed"] == 0


class TestOrderReconcilerCycleCounting:
    """Verifica que el cycle counting funcione."""

    def test_skips_when_not_time(self):
        with patch("bot.core.reconciliation.ExchangeStateRecovery"):
            from bot.core.reconciliation import OrderReconciler
            rec = OrderReconciler(interval_cycles=5)
            import asyncio
            # Llamar reconcile_all 4 veces con interval_cycles=5
            # no debe ejecutar reconciliación (solo incrementa counter)
            for _ in range(4):
                asyncio.run(rec.reconcile_all([]))
            assert rec._cycle_counter == 4  # contó 4 ciclos, no reseteó
            assert rec._total_reconciliations == 0  # no ejecutó

    def test_interval_reached(self, reconciler):
        """Con interval_cycles=1, cada llamada ejecuta reconciliación."""
        runner = MagicMock()
        runner.symbol = "XRPUSDT"
        runner.executor = MagicMock()
        runner.executor.position_open = False
        client = _make_client_mock()
        runner.executor._ensure_client = MagicMock(return_value=client)

        with patch("bot.core.reconciliation.ExchangeStateRecovery"):
            from bot.core.reconciliation import OrderReconciler
            rec = OrderReconciler(interval_cycles=1)
            import asyncio
            asyncio.run(rec.reconcile_all([runner]))
            assert rec._total_reconciliations == 1


class TestOrderReconcilerNoPosition:
    """Escenario: sin posición en exchange ni en bot."""

    def test_cancels_orphan_tps(self, reconciler, mock_runner):
        """TPs huérfanos deben cancelarse."""
        client = mock_runner.executor._ensure_client.return_value
        # Simular órdenes limit reduce-only en exchange (formato Binance)
        client.get_open_orders = AsyncMock(return_value=[
            {"orderId": "tp1_id_123", "type": "LIMIT", "reduceOnly": True, "price": "0.6190"},
            {"orderId": "tp2_id_456", "type": "LIMIT", "reduceOnly": True, "price": "0.6200"},
        ])
        # Sin posición en exchange
        client.get_positions = AsyncMock(return_value=[])
        # Bot tampoco tiene posición
        mock_runner.executor.position_open = False

        import asyncio
        asyncio.run(reconciler.reconcile_all([mock_runner]))

        # Debe cancelar ambas órdenes
        assert client.cancel_order.call_count == 2
        assert reconciler._total_orphans_cancelled == 2

    def test_no_orphans_when_no_orders(self, reconciler, mock_runner):
        """Sin órdenes abiertas, no debe cancelar nada."""
        client = mock_runner.executor._ensure_client.return_value
        client.get_open_orders = AsyncMock(return_value=[])
        client.get_positions = AsyncMock(return_value=[])

        import asyncio
        asyncio.run(reconciler.reconcile_all([mock_runner]))

        assert client.cancel_order.call_count == 0


class TestOrderReconcilerPhantom:
    """Escenario: bot cree que hay posición pero exchange no tiene."""

    def test_clears_phantom_flag(self, reconciler, mock_runner):
        """Bot tiene flag position_open=True pero exchange no tiene posición."""
        client = mock_runner.executor._ensure_client.return_value
        mock_runner.executor.position_open = True
        mock_runner.executor.position_type = "LONG"
        mock_runner.executor.tp1_hit = True
        mock_runner.executor.trailing_activated = True

        client.get_open_orders = AsyncMock(return_value=[])
        client.get_positions = AsyncMock(return_value=[])

        import asyncio
        asyncio.run(reconciler.reconcile_all([mock_runner]))

        assert mock_runner.executor.position_open is False
        assert mock_runner.executor.position_type is None
        assert mock_runner.executor.tp1_hit is False
        assert mock_runner.executor.trailing_activated is False
        assert reconciler._total_desyncs_fixed == 1

    def test_phantom_cancels_tps(self, reconciler, mock_runner):
        """Phantom detectado + TPs huérfanos = todo cancelado."""
        client = mock_runner.executor._ensure_client.return_value
        mock_runner.executor.position_open = True
        client.get_open_orders = AsyncMock(return_value=[
            {"orderId": "orphan_tp", "type": "LIMIT", "reduceOnly": True, "price": "0.6190"},
        ])
        client.get_positions = AsyncMock(return_value=[])

        import asyncio
        asyncio.run(reconciler.reconcile_all([mock_runner]))

        assert mock_runner.executor.position_open is False
        assert client.cancel_order.call_count == 1


class TestOrderReconcilerUndetected:
    """Escenario: exchange tiene posición pero bot no la registró."""

    def test_recovers_position(self, reconciler, mock_runner):
        """Bot debe sincronizar con la posición real del exchange."""
        client = mock_runner.executor._ensure_client.return_value
        mock_runner.executor.position_open = False
        client.get_open_orders = AsyncMock(return_value=[])
        # Binance devuelve list directamente con side="LONG"
        client.get_positions = AsyncMock(return_value=[{
            "symbol": "XRPUSDT",
            "size": 100.0,
            "side": "LONG",
            "avgPrice": 0.6185,
            "unrealizedPnL": 0.0,
        }])

        import asyncio
        asyncio.run(reconciler.reconcile_all([mock_runner]))

        assert mock_runner.executor.position_open is True
        assert mock_runner.executor.position_type == "LONG"
        assert mock_runner.executor.entry_price == 0.6185
        assert mock_runner.executor.quantity == 100.0
        assert reconciler._total_desyncs_fixed == 1

    def test_recovers_short_position(self, reconciler, mock_runner):
        """Recuperación de posición SHORT."""
        client = mock_runner.executor._ensure_client.return_value
        mock_runner.executor.position_open = False
        client.get_open_orders = AsyncMock(return_value=[])
        client.get_positions = AsyncMock(return_value=[{
            "symbol": "XRPUSDT",
            "size": 200.0,
            "side": "SHORT",
            "avgPrice": 1.2500,
            "unrealizedPnL": 0.0,
        }])

        import asyncio
        asyncio.run(reconciler.reconcile_all([mock_runner]))

        assert mock_runner.executor.position_open is True
        assert mock_runner.executor.position_type == "SHORT"
        assert mock_runner.executor.entry_price == 1.2500


class TestOrderReconcilerConsistent:
    """Escenario: posición consistente (bot + exchange)."""

    def test_no_tps_recoloca(self, reconciler, mock_runner):
        """Posición abierta pero sin TPs en exchange — debe recolocar."""
        client = mock_runner.executor._ensure_client.return_value
        mock_runner.executor.position_open = True
        mock_runner.executor.tp1_price = 0.6250
        mock_runner.executor.tp2_price = 0.6300
        mock_runner.executor.quantity = 100.0

        client.get_open_orders = AsyncMock(return_value=[])
        client.get_positions = AsyncMock(return_value=[{
            "symbol": "XRPUSDT",
            "size": 100.0,
            "side": "LONG",
            "avgPrice": 0.6185,
            "unrealizedPnL": 0.0,
        }])

        import asyncio
        asyncio.run(reconciler.reconcile_all([mock_runner]))

        # Debe haber llamado get_open_orders
        assert client.get_open_orders.called

    def test_excess_tps(self, reconciler, mock_runner):
        """Más de 2 TPs — debe cancelarlos y recolocar."""
        client = mock_runner.executor._ensure_client.return_value
        mock_runner.executor.position_open = True
        mock_runner.executor.tp1_price = 0.6250
        mock_runner.executor.tp2_price = 0.6300

        client.get_open_orders = AsyncMock(return_value=[
            {"orderId": "tp1", "type": "LIMIT", "reduceOnly": True},
            {"orderId": "tp2", "type": "LIMIT", "reduceOnly": True},
            {"orderId": "tp3", "type": "LIMIT", "reduceOnly": True},
            {"orderId": "tp4", "type": "LIMIT", "reduceOnly": True},
        ])
        client.get_positions = AsyncMock(return_value=[{
            "symbol": "XRPUSDT",
            "size": 100.0,
            "side": "LONG",
            "avgPrice": 0.6185,
            "unrealizedPnL": 0.0,
        }])

        import asyncio
        asyncio.run(reconciler.reconcile_all([mock_runner]))

        # Debe cancelar los 4 TPs extra
        assert client.cancel_order.call_count == 4


class TestOrderReconcilerMultipleSymbols:
    """Verifica reconciliación multi-símbolo."""

    def test_two_symbols(self, reconciler):
        def make_runner(sym):
            r = MagicMock()
            r.symbol = sym
            r.executor = MagicMock()
            r.executor.position_open = False
            client = _make_client_mock()
            r.executor._ensure_client = MagicMock(return_value=client)
            return r

        runner1 = make_runner("XRPUSDT")
        runner2 = make_runner("ETHUSDT")

        import asyncio
        asyncio.run(reconciler.reconcile_all([runner1, runner2]))

        assert runner1.executor._ensure_client.called
        assert runner2.executor._ensure_client.called
        assert reconciler._total_reconciliations == 1


class TestOrderReconcilerEdgeCases:
    """Casos borde."""

    def test_exception_in_fetch_orders(self, reconciler, mock_runner):
        """Error al obtener órdenes no debe detener la reconciliación."""
        client = mock_runner.executor._ensure_client.return_value
        client.get_open_orders = AsyncMock(side_effect=Exception("API error"))
        client.get_positions = AsyncMock(return_value=[])

        import asyncio
        # No debe lanzar excepción
        asyncio.run(reconciler.reconcile_all([mock_runner]))

    def test_exception_in_fetch_position(self, reconciler, mock_runner):
        """Error al obtener posición no debe detener la reconciliación."""
        client = mock_runner.executor._ensure_client.return_value
        client.get_open_orders = AsyncMock(return_value=[])
        client.get_positions = AsyncMock(side_effect=Exception("API error"))

        import asyncio
        asyncio.run(reconciler.reconcile_all([mock_runner]))

    def test_empty_runner_list(self, reconciler):
        """Lista vacía de runners no debe causar error."""
        import asyncio
        asyncio.run(reconciler.reconcile_all([]))
        assert reconciler._total_reconciliations == 1


class TestOrderReconcilerFetchOpenOrders:
    """Verifica _fetch_open_orders (async)."""

    def test_returns_list_on_success(self, reconciler):
        client = _make_client_mock()
        client.get_open_orders = AsyncMock(return_value=[{"orderId": "123"}])
        import asyncio
        result = asyncio.run(reconciler._fetch_open_orders(client, "XRPUSDT"))
        assert len(result) == 1
        assert result[0]["orderId"] == "123"

    def test_returns_empty_on_exception(self, reconciler):
        client = _make_client_mock()
        client.get_open_orders = AsyncMock(side_effect=Exception("API error"))
        import asyncio
        result = asyncio.run(reconciler._fetch_open_orders(client, "XRPUSDT"))
        assert result == []


class TestOrderReconcilerFetchRealPosition:
    """Verifica _fetch_real_position (async)."""

    def test_returns_position(self, reconciler):
        client = _make_client_mock()
        client.get_positions = AsyncMock(return_value=[{
            "symbol": "XRPUSDT",
            "size": 100.0,
            "side": "LONG",
            "avgPrice": 0.6185,
            "unrealizedPnL": 0.0,
        }])
        import asyncio
        pos = asyncio.run(reconciler._fetch_real_position(client, "XRPUSDT"))
        assert pos is not None
        assert pos["type"] == "LONG"
        assert pos["size"] == 100.0
        assert pos["entry"] == 0.6185

    def test_returns_none_when_size_zero(self, reconciler):
        client = _make_client_mock()
        client.get_positions = AsyncMock(return_value=[{
            "symbol": "XRPUSDT",
            "size": 0.0,
            "side": "LONG",
            "avgPrice": 0.0,
            "unrealizedPnL": 0.0,
        }])
        import asyncio
        pos = asyncio.run(reconciler._fetch_real_position(client, "XRPUSDT"))
        assert pos is None

    def test_returns_none_when_empty(self, reconciler):
        client = _make_client_mock()
        client.get_positions = AsyncMock(return_value=[])
        import asyncio
        pos = asyncio.run(reconciler._fetch_real_position(client, "XRPUSDT"))
        assert pos is None

    def test_returns_none_on_exception(self, reconciler):
        client = _make_client_mock()
        client.get_positions = AsyncMock(side_effect=Exception("API error"))
        import asyncio
        pos = asyncio.run(reconciler._fetch_real_position(client, "XRPUSDT"))
        assert pos is None
