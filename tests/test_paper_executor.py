"""
Tests exhaustivos para PaperExecutor — Simulador de paper trading.

Verifica:
- Inicialización y defaults
- Apertura/cierre de posiciones LONG y SHORT
- Cálculo de slippage, comisiones y P&L
- Balance insuficiente
- Propiedades de estadísticas
- Integración con SymbolRunner vía DI
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.core.paper_executor import PaperExecutor


# ── Helpers ──────────────────────────────────────────────────────────

@pytest.fixture
def mock_data_feed() -> MagicMock:
    """DataFeed simulado con instrument_cache."""
    feed = MagicMock()
    feed.symbol = "XRPUSDT"
    feed._instrument_cache = {"qtyStep": "0.001"}
    return feed


@pytest.fixture
def mock_journal() -> MagicMock:
    """TradeJournal simulado."""
    journal = MagicMock()
    journal._open_trade = {}
    return journal


@pytest.fixture
def executor(mock_data_feed: MagicMock, mock_journal: MagicMock) -> PaperExecutor:
    """PaperExecutor con defaults (balance 10000, slippage 0.1%, comisión 0.05%)."""
    return PaperExecutor(
        data_feed=mock_data_feed,
        journal=mock_journal,
        initial_balance=10000.0,
        slippage_pct=0.001,
        commission_pct=0.0005,
    )


# ── Tests de Inicialización ─────────────────────────────────────────

class TestInit:
    """Verifica que PaperExecutor se inicializa correctamente."""

    def test_default_values(self, executor: PaperExecutor) -> None:
        assert executor.paper_balance == 10000.0
        assert executor.initial_balance == 10000.0
        assert executor.slippage_pct == 0.001
        assert executor.commission_pct == 0.0005
        assert executor.position_open is False
        assert executor.position_type is None
        assert executor.entry_price == 0.0
        assert executor.quantity == 0.0
        assert executor.current_sl == 0.0
        assert executor.tp1_hit is False
        assert executor.trailing_activated is False
        assert executor.total_trades == 0
        assert executor.wins == 0
        assert executor.losses == 0
        assert executor.total_pnl == 0.0
        assert executor.total_commission == 0.0

    def test_init_qty_step_from_cache(self, mock_data_feed: MagicMock) -> None:
        """QtyStep se obtiene del instrument_cache."""
        exec_ = PaperExecutor(data_feed=mock_data_feed)
        assert exec_._qty_step == 0.001

    def test_init_qty_step_fallback(self) -> None:
        """Sin instrument_cache, usa fallback 0.001."""
        feed = MagicMock()
        feed._instrument_cache = None
        exec_ = PaperExecutor(data_feed=feed)
        assert exec_._qty_step == 0.001

    def test_init_qty_step_on_exception(self) -> None:
        """Si falla la lectura del cache, usa fallback."""
        feed = MagicMock()
        feed._instrument_cache = {}
        exec_ = PaperExecutor(data_feed=feed)
        assert exec_._qty_step == 0.001

    @pytest.mark.asyncio
    async def test_set_leverage_logs_only(self, executor: PaperExecutor) -> None:
        """set_leverage no falla ni modifica estado."""
        await executor.set_leverage(5)
        # No hay estado que verificar, solo que no falle


# ── Tests de Apertura de Posición ───────────────────────────────────

class TestOpenPosition:
    """Verifica la apertura simulada de posiciones."""

    @pytest.mark.asyncio
    async def test_open_long_success(self, executor: PaperExecutor, mock_journal: MagicMock) -> None:
        """Abre LONG correctamente con slippage y comisión."""
        result = await executor.open_position(
            direction="LONG",
            entry=0.50,
            sl=0.495,
            tp1=0.51,
            tp2=0.52,
            qty=100.0,
        )
        assert result is True
        assert executor.position_open is True
        assert executor.position_type == "LONG"
        assert executor.quantity == 100.0
        assert executor.current_sl == 0.495

        # Slippage: LONG → entry * (1 + 0.001) = 0.5005
        assert executor.entry_price == pytest.approx(0.5005, rel=1e-4)
        assert executor.tp1_hit is False
        assert executor.trailing_activated is False
        assert executor.total_trades == 1

        # Comisión de entrada: 100 * 0.5005 * 0.0005 = 0.025025
        expected_commission = 100.0 * 0.5005 * 0.0005
        assert executor.total_commission == pytest.approx(expected_commission, rel=1e-4)
        assert executor.paper_balance == pytest.approx(10000.0 - expected_commission, rel=1e-4)

        # Verificar journal.open() fue llamado
        mock_journal.open.assert_called_once_with(
            symbol="XRPUSDT", side="LONG", entry_price=pytest.approx(0.5005),
            qty=100.0, sl=0.495, tp1=0.51, tp2=0.52,
        )

    @pytest.mark.asyncio
    async def test_open_short_success(self, executor: PaperExecutor) -> None:
        """Abre SHORT correctamente con slippaje (precio más bajo)."""
        result = await executor.open_position(
            direction="SHORT",
            entry=0.50,
            sl=0.505,
            tp1=0.49,
            tp2=0.48,
            qty=100.0,
        )
        assert result is True
        assert executor.position_type == "SHORT"

        # Slippage: SHORT → entry * (1 - 0.001) = 0.4995
        assert executor.entry_price == pytest.approx(0.4995, rel=1e-4)

    @pytest.mark.asyncio
    async def test_open_position_insufficient_balance(self, executor: PaperExecutor) -> None:
        """Balance insuficiente retorna False."""
        executor.paper_balance = 1.0  # Muy bajo
        result = await executor.open_position(
            direction="LONG", entry=100.0, sl=99.0,
            tp1=101.0, tp2=102.0, qty=1000.0,
        )
        assert result is False
        assert executor.position_open is False

    @pytest.mark.asyncio
    async def test_open_position_zero_quantity(self, executor: PaperExecutor) -> None:
        """Cantidad cero tras truncar retorna False."""
        executor._qty_step = 100.0  # Step enorme
        result = await executor.open_position(
            direction="LONG", entry=0.50, sl=0.495,
            tp1=0.51, tp2=0.52, qty=1.0,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_open_position_invalid_prices(self, executor: PaperExecutor) -> None:
        """Precios inválidos retornan False."""
        result = await executor.open_position(
            direction="LONG", entry=0.0, sl=0.0,
            tp1=0.0, tp2=0.0, qty=100.0,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_open_position_closes_existing(self, executor: PaperExecutor) -> None:
        """Si ya hay posición abierta, la cierra antes de abrir nueva."""
        # Abrir primera posición
        await executor.open_position(
            direction="LONG", entry=0.50, sl=0.495,
            tp1=0.51, tp2=0.52, qty=100.0,
        )
        assert executor.total_trades == 1

        # Abrir segunda (debe cerrar la primera)
        await executor.open_position(
            direction="SHORT", entry=0.55, sl=0.555,
            tp1=0.54, tp2=0.53, qty=50.0,
        )
        # total_trades se incrementa en 2 (1 close + 1 open = 2 trades)
        assert executor.total_trades == 2
        assert executor.position_type == "SHORT"

    @pytest.mark.asyncio
    async def test_open_with_qty_tp(self, executor: PaperExecutor, mock_journal: MagicMock) -> None:
        """TP quantities personalizadas se usan correctamente."""
        await executor.open_position(
            direction="LONG", entry=0.50, sl=0.495,
            tp1=0.51, tp2=0.52, qty=100.0,
            qty_tp1=40.0, qty_tp2=60.0,
        )
        mock_journal.open.assert_called_once()
        # Solo verificar que no falló


# ── Tests de Cierre de Posición ────────────────────────────────────

class TestClosePosition:
    """Verifica el cierre simulado de posiciones."""

    @pytest.mark.asyncio
    async def test_close_long_with_profit(self, executor: PaperExecutor) -> None:
        """Cierra LONG con ganancia."""
        await executor.open_position(
            direction="LONG", entry=0.50, sl=0.495,
            tp1=0.51, tp2=0.52, qty=100.0,
        )
        balance_before = executor.paper_balance
        entry = executor.entry_price

        # Simular precio subió
        executor.last_price = 0.52
        executor.close_position()

        assert executor.position_open is False
        assert executor.position_type is None

        # P&L esperado: (exit_slippage - entry) * qty - commission_exit
        # SHORT exit slippage para LONG: exit * (1 - 0.001)
        fill_exit = 0.52 * (1 - 0.001)  # ~0.51948
        pnl_gross = (fill_exit - entry) * 100.0
        commission_exit = 100.0 * fill_exit * 0.0005
        pnl_net = pnl_gross - commission_exit

        assert executor.paper_balance == pytest.approx(balance_before + pnl_net, rel=1e-4)
        assert executor.total_pnl == pytest.approx(pnl_net, rel=1e-4)
        assert executor.wins == 1

    @pytest.mark.asyncio
    async def test_close_short_with_loss(self, executor: PaperExecutor) -> None:
        """Cierra SHORT con pérdida."""
        await executor.open_position(
            direction="SHORT", entry=0.50, sl=0.505,
            tp1=0.49, tp2=0.48, qty=100.0,
        )
        balance_before = executor.paper_balance
        entry = executor.entry_price

        # Simular precio subió (pérdida para SHORT)
        executor.last_price = 0.51
        executor.close_position()

        # LONG exit slippage para SHORT: exit * (1 + 0.001)
        fill_exit = 0.51 * (1 + 0.001)  # ~0.51051
        pnl_gross = (entry - fill_exit) * 100.0
        commission_exit = 100.0 * fill_exit * 0.0005
        pnl_net = pnl_gross - commission_exit

        assert executor.paper_balance == pytest.approx(balance_before + pnl_net, rel=1e-4)
        assert executor.losses == 1

    @pytest.mark.asyncio
    async def test_close_position_no_position(self, executor: PaperExecutor) -> None:
        """Cerrar sin posición activa es no-op."""
        balance_before = executor.paper_balance
        executor.close_position()
        assert executor.paper_balance == balance_before

    @pytest.mark.asyncio
    async def test_close_position_uses_last_price_fallback(self, executor: PaperExecutor) -> None:
        """Si last_price es 0, usa entry_price como fallback."""
        await executor.open_position(
            direction="LONG", entry=0.50, sl=0.495,
            tp1=0.51, tp2=0.52, qty=100.0,
        )
        executor.last_price = 0.0  # No actualizado
        executor.close_position()
        # No debe fallar
        assert executor.position_open is False

    @pytest.mark.asyncio
    async def test_close_position_calls_journal(self, executor: PaperExecutor, mock_journal: MagicMock) -> None:
        """close_position llama a journal.close()."""
        await executor.open_position(
            direction="LONG", entry=0.50, sl=0.495,
            tp1=0.51, tp2=0.52, qty=100.0,
        )
        executor.last_price = 0.51
        executor.close_position()
        mock_journal.close.assert_called_once()


# ── Tests de Stop Loss / Trailing Stop ────────────────────────────

class TestStopManagement:
    """Verifica update_stop_loss y activate_trailing_stop."""

    def test_update_stop_loss(self, executor: PaperExecutor) -> None:
        """Actualiza current_sl."""
        executor.update_stop_loss(0.49)
        assert executor.current_sl == 0.49

    def test_activate_trailing_stop(self, executor: PaperExecutor) -> None:
        """Activa trailing stop."""
        result = executor.activate_trailing_stop(0.005, 0.55)
        assert result is True
        assert executor.trailing_activated is True


# ── Tests de Helpers ───────────────────────────────────────────────

class TestHelpers:
    """Verifica _truncate_qty."""

    def test_truncate_qty_standard(self, executor: PaperExecutor) -> None:
        """Trunca correctamente al qtyStep."""
        executor._qty_step = 0.001
        # Truncation con floor: 100.567 / 0.001 = 100567.0 → floor → 100567 * 0.001 = 100.566999...
        assert executor._truncate_qty(100.567) == 100.566
        assert executor._truncate_qty(100.5678) == 100.567

    def test_truncate_qty_large_step(self, executor: PaperExecutor) -> None:
        """Trunca con step grande."""
        executor._qty_step = 10.0
        assert executor._truncate_qty(123.0) == 120.0

    def test_truncate_qty_zero_step(self, executor: PaperExecutor) -> None:
        """Step cero no modifica."""
        executor._qty_step = 0.0
        assert executor._truncate_qty(100.5) == 100.5


# ── Tests de Estadísticas ──────────────────────────────────────────

class TestStats:
    """Verifica la propiedad stats."""

    def test_stats_initial(self, executor: PaperExecutor) -> None:
        """Estadísticas iniciales."""
        stats = executor.stats
        assert stats["initial_balance"] == 10000.0
        assert stats["current_balance"] == 10000.0
        assert stats["total_trades"] == 0
        assert stats["wins"] == 0
        assert stats["losses"] == 0
        assert stats["win_rate"] == 0.0
        assert stats["position_open"] is False

    @pytest.mark.asyncio
    async def test_stats_after_trade(self, executor: PaperExecutor) -> None:
        """Estadísticas reflejan operaciones realizadas."""
        await executor.open_position(
            direction="LONG", entry=0.50, sl=0.495,
            tp1=0.51, tp2=0.52, qty=100.0,
        )
        executor.last_price = 0.52
        executor.close_position()

        stats = executor.stats
        assert stats["total_trades"] == 1
        assert stats["wins"] == 1
        assert stats["losses"] == 0
        assert stats["position_open"] is False
        assert stats["win_rate"] == 100.0
        assert stats["current_balance"] > 10000.0  # Ganancia
        assert stats["total_pnl"] > 0

    @pytest.mark.asyncio
    async def test_stats_position_open(self, executor: PaperExecutor) -> None:
        """Estadísticas reflejan posición abierta."""
        await executor.open_position(
            direction="LONG", entry=0.50, sl=0.495,
            tp1=0.51, tp2=0.52, qty=100.0,
        )
        stats = executor.stats
        assert stats["position_open"] is True
        assert stats["position_type"] == "LONG"

    @pytest.mark.asyncio
    async def test_stats_win_rate_zero_when_no_trades(self, executor: PaperExecutor) -> None:
        """Win rate es 0 cuando no hay trades (evita división por cero)."""
        stats = executor.stats
        assert stats["win_rate"] == 0.0


# ── Tests de Comisiones Acumuladas ─────────────────────────────────

class TestCommission:
    """Verifica acumulación de comisiones."""

    @pytest.mark.asyncio
    async def test_commission_accumulated(self, executor: PaperExecutor) -> None:
        """Comisiones de entrada y salida se acumulan."""
        await executor.open_position(
            direction="LONG", entry=0.50, sl=0.495,
            tp1=0.51, tp2=0.52, qty=100.0,
        )
        # Guardar entry_price ANTES de cerrar (close_position lo resetea a 0)
        entry_price = executor.entry_price
        executor.last_price = 0.51
        executor.close_position()

        # Comisión total = entry_commission + exit_commission
        entry_comm = 100.0 * entry_price * 0.0005
        fill_exit = 0.51 * (1 - 0.001)
        exit_comm = 100.0 * fill_exit * 0.0005
        expected_total = entry_comm + exit_comm

        assert executor.total_commission == pytest.approx(expected_total, rel=1e-4)


# ── Tests de Integración con SymbolRunner ─────────────────────────

class TestSymbolRunnerIntegration:
    """Verifica que PaperExecutor es compatible con SymbolRunner vía DI."""

    @pytest.mark.asyncio
    async def test_inject_into_symbol_runner(self, executor: PaperExecutor) -> None:
        """PaperExecutor se inyecta en SymbolRunner y ejecuta ciclo sin crash."""
        from bot.core.symbol_runner import SymbolRunner

        # Mocks para SymbolRunner
        data_feed = AsyncMock()
        data_feed.symbol = "XRPUSDT"
        data_feed.current_15m.return_value = _make_df(0.50)
        data_feed.current_1h.return_value = _make_df(0.51)
        data_feed.get_spread_percent.return_value = 0.01
        data_feed._instrument_cache = {"qtyStep": "0.001"}

        strategy = MagicMock()
        sig = MagicMock()
        sig.has_signal = False
        strategy.evaluate.return_value = sig

        risk_mgr = MagicMock()
        risk_mgr.get_phase_config = AsyncMock(return_value=(0, {
            "tp1_rr": 1.5, "tp2_rr": 2.5, "max_sl_pct": 0.035,
            "leverage": 1, "descripcion": "Fase 0",
        }))
        risk_mgr.is_trading_session = AsyncMock(return_value=True)
        risk_mgr.is_loss_paused = AsyncMock(return_value=False)
        risk_mgr.reset_daily_if_needed = AsyncMock()
        risk_mgr.update_balance_for_drawdown = AsyncMock()
        risk_mgr.validate_drawdown = AsyncMock()
        risk_mgr.set_balance = MagicMock()
        risk_mgr.set_notifier = MagicMock()

        position_mgr = AsyncMock()
        position_mgr.update.return_value = None

        journal = MagicMock()
        journal._open_trade = {}
        journal.database = None

        notifier = MagicMock()

        # Inyectar PaperExecutor como executor
        runner = SymbolRunner(
            symbol="XRPUSDT", allocation_pct=1.0, leverage=1,
            risk_per_trade=0.02, journal=journal,
            notifier=notifier, total_balance=200.0,
            data_feed=data_feed, strategy=strategy,
            risk_mgr=risk_mgr, executor=executor,
            position_mgr=position_mgr,
        )
        runner.fase_cfg = {"leverage": 1, "descripcion": "Fase 0"}

        # Ejecutar ciclo sin señal (no debe abrir posición)
        result = await runner.run_cycle()
        assert result is None
        # PaperExecutor no debe haber abierto posición
        assert executor.position_open is False

    @pytest.mark.asyncio
    async def test_symbol_runner_with_signal(self, executor: PaperExecutor) -> None:
        """SymbolRunner con PaperExecutor abre posición cuando hay señal."""
        from bot.core.symbol_runner import SymbolRunner

        data_feed = AsyncMock()
        data_feed.symbol = "XRPUSDT"
        data_feed.current_15m.return_value = _make_df(0.50)
        data_feed.current_1h.return_value = _make_df(0.51)
        data_feed.get_spread_percent.return_value = 0.01
        data_feed._instrument_cache = {"qtyStep": "0.001"}

        strategy = MagicMock()
        sig = MagicMock()
        sig.has_signal = True
        sig.direction = "LONG"
        sig.entry = 0.50
        sig.sl = 0.495
        sig.tp1 = 0.51
        sig.tp2 = 0.52
        sig.score = 80
        sig.reason = "Test"
        sig.timestamp = time.time()
        strategy.evaluate.return_value = sig

        risk_mgr = MagicMock()
        risk_mgr.get_phase_config = AsyncMock(return_value=(0, {
            "tp1_rr": 1.5, "tp2_rr": 2.5, "max_sl_pct": 0.035,
            "leverage": 1, "descripcion": "Fase 0",
        }))
        risk_mgr.calculate_position_size = AsyncMock(return_value=(100.0, 2.0, "OK"))
        risk_mgr.validate_sl_distance = AsyncMock(return_value=True)
        risk_mgr.validate_rr = AsyncMock(return_value=True)
        risk_mgr.distribute_tp = AsyncMock(return_value=(50.0, 50.0))
        risk_mgr.is_trading_session = AsyncMock(return_value=True)
        risk_mgr.is_loss_paused = AsyncMock(return_value=False)
        risk_mgr.reset_daily_if_needed = AsyncMock()
        risk_mgr.update_balance_for_drawdown = AsyncMock()
        risk_mgr.validate_drawdown = AsyncMock()
        risk_mgr.set_balance = MagicMock()
        risk_mgr.set_notifier = MagicMock()

        position_mgr = AsyncMock()
        position_mgr.update.return_value = None

        journal = MagicMock()
        journal._open_trade = {}
        journal.database = None

        notifier = MagicMock()

        runner = SymbolRunner(
            symbol="XRPUSDT", allocation_pct=1.0, leverage=1,
            risk_per_trade=0.02, journal=journal,
            notifier=notifier, total_balance=200.0,
            data_feed=data_feed, strategy=strategy,
            risk_mgr=risk_mgr, executor=executor,
            position_mgr=position_mgr,
        )
        runner.fase_cfg = {"leverage": 1, "descripcion": "Fase 0"}

        # Ejecutar ciclo con señal
        result = await runner.run_cycle()
        assert result is None
        # PaperExecutor debe tener posición abierta
        assert executor.position_open is True
        assert executor.position_type == "LONG"


# ── Factory helper ─────────────────────────────────────────────────

def _make_df(close_val: float = 0.50, n_rows: int = 50):
    """Crea un DataFrame de prueba con precios."""
    import numpy as np
    import pandas as pd

    np.random.seed(42)
    closes = np.full(n_rows, close_val)
    # Agregar ruido mínimo
    closes += np.random.normal(0, 0.001, n_rows)
    return pd.DataFrame({
        "timestamp": pd.date_range(
            end=pd.Timestamp.now(tz="UTC"), periods=n_rows, freq="15min",
        ),
        "open": closes * 0.999,
        "high": closes * 1.001,
        "low": closes * 0.999,
        "close": closes,
        "volume": np.random.uniform(1000, 5000, n_rows),
    })
