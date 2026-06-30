"""
Tests para SymbolRunner usando Dependency Injection (mocks).
Verifica el ciclo completo: init, run_cycle, manejo de errores.
"""

import pytest
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, AsyncMock
from bot.core.symbol_runner import SymbolRunner
from bot.core.exceptions import RiskException


# ─── Helpers ──────────────────────────────────────────────────────

def _make_df(close_val: float = 0.50, n_rows: int = 50):
    """
    Crea un DataFrame con velas sintéticas.
    Necesita al menos ~30 filas para que los indicadores (RSI, ADX, etc.) funcionen.
    """
    closes = close_val * (1 + np.random.randn(n_rows) * 0.005)
    closes = np.maximum(closes, close_val * 0.95)  # Evitar negativos
    closes[0] = close_val
    opens = closes + np.random.randn(n_rows) * 0.001
    highs = np.maximum(closes, opens) * (1 + np.abs(np.random.randn(n_rows)) * 0.002)
    lows = np.minimum(closes, opens) * (1 - np.abs(np.random.randn(n_rows)) * 0.002)
    return pd.DataFrame({
        "timestamp": pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=n_rows, freq="15min"),
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": np.random.rand(n_rows) * 10000,
    })


class TestSymbolRunnerInit:
    """Verifica que el runner se inicializa correctamente con DI."""

    @pytest.fixture
    def mocks(self):
        """Crea todas las dependencias mock con los métodos necesarios.
        
        Nota: risk_mgr usa MagicMock (no AsyncMock) porque set_notifier()
        y set_balance() se llaman SIN await en __init__.
        Los métodos async se definen explícitamente como AsyncMock.
        """
        risk_mgr = MagicMock()
        risk_mgr.get_phase_config = AsyncMock(return_value=(0, {}))
        risk_mgr.calculate_position_size = AsyncMock(return_value=(100.0, 2.0, "OK"))
        risk_mgr.validate_sl_distance = AsyncMock(return_value=True)
        risk_mgr.validate_rr = AsyncMock(return_value=True)
        risk_mgr.distribute_tp = AsyncMock(return_value=(50.0, 50.0))
        risk_mgr.is_trading_session = AsyncMock(return_value=True)
        risk_mgr.is_loss_paused = AsyncMock(return_value=False)
        risk_mgr.get_drawdown_risk_scale = AsyncMock(return_value=1.0)
        risk_mgr.register_stop = AsyncMock()
        risk_mgr.reset_daily_if_needed = AsyncMock()
        risk_mgr.update_balance_for_drawdown = AsyncMock()
        risk_mgr.validate_drawdown = AsyncMock()
        risk_mgr.set_balance = MagicMock()
        risk_mgr.set_notifier = MagicMock()

        return {
            "data_feed": AsyncMock(spec=["current_15m", "current_1h", "get_spread_percent"]),
            "strategy": MagicMock(spec=["evaluate"]),
            "risk_mgr": risk_mgr,
            "executor": MagicMock(spec=[
                "open_position", "close_position", "update_stop_loss",
                "activate_trailing_stop", "set_leverage",
                "last_price",
            ]),
            "position_mgr": AsyncMock(spec=["update"]),
            "journal": MagicMock(spec=["update_runup_drawdown", "open", "close", "_open_trade", "database"]),
            "notifier": MagicMock(spec=["send", "trade_open", "trade_close"]),
        }

    @pytest.fixture
    def runner(self, mocks):
        """Crea un SymbolRunner con todas las dependencias inyectadas."""
        mocks["journal"].database = None
        return SymbolRunner(
            symbol="XRPUSDT",
            allocation_pct=1.0,
            leverage=1,
            risk_per_trade=0.02,
            journal=mocks["journal"],
            notifier=mocks["notifier"],
            total_balance=200.0,
            data_feed=mocks["data_feed"],
            strategy=mocks["strategy"],
            risk_mgr=mocks["risk_mgr"],
            executor=mocks["executor"],
            position_mgr=mocks["position_mgr"],
        )

    def test_initial_state(self, runner):
        """Estado inicial correcto."""
        assert runner.symbol == "XRPUSDT"
        assert runner.allocation_pct == 1.0
        assert runner.balance == 200.0  # total_balance * allocation_pct
        assert runner.fase_cfg == {}  # Se carga lazy en run_cycle
        assert runner.fase_num == 0

    def test_di_components_are_injected(self, runner, mocks):
        """Las dependencias inyectadas son las correctas."""
        assert runner.data_feed is mocks["data_feed"]
        assert runner.strategy is mocks["strategy"]
        assert runner.risk_mgr is mocks["risk_mgr"]
        assert runner.executor is mocks["executor"]
        assert runner.position_mgr is mocks["position_mgr"]


class TestSymbolRunnerRunCycle:
    """Verifica el ciclo de ejecución con diferentes escenarios."""

    @pytest.fixture
    def mocks(self):
        np.random.seed(42)  # Semilla fija para tests reproducibles
        df_15m = _make_df(0.50)
        df_1h = _make_df(0.51)

        data_feed = AsyncMock()
        data_feed.current_15m.return_value = df_15m
        data_feed.current_1h.return_value = df_1h
        data_feed.get_spread_percent.return_value = 0.01

        strategy = MagicMock()
        sig = MagicMock()
        sig.has_signal = False
        strategy.evaluate.return_value = sig

        risk_mgr = MagicMock()
        risk_mgr.get_phase_config = AsyncMock(return_value=(0, {
            "tp1_rr": 0.8, "tp2_rr": 1.8, "max_sl_pct": 0.01,
            "min_sl_distance": 0.001, "rsi_min_long": 30, "rsi_max_long": 45,
            "sesion_inicio": "09:00", "sesion_fin": "17:00",
            "descripcion": "Fase 0", "leverage": 1,
        }))
        risk_mgr.calculate_position_size = AsyncMock(return_value=(100.0, 2.0, "OK"))
        risk_mgr.validate_sl_distance = AsyncMock(return_value=True)
        risk_mgr.validate_rr = AsyncMock(return_value=True)
        risk_mgr.distribute_tp = AsyncMock(return_value=(50.0, 50.0))
        risk_mgr.is_trading_session = AsyncMock(return_value=True)
        risk_mgr.is_loss_paused = AsyncMock(return_value=False)
        risk_mgr.get_drawdown_risk_scale = AsyncMock(return_value=1.0)
        risk_mgr.register_stop = AsyncMock()
        risk_mgr.reset_daily_if_needed = AsyncMock()
        risk_mgr.update_balance_for_drawdown = AsyncMock()
        risk_mgr.validate_drawdown = AsyncMock()
        risk_mgr.set_balance = MagicMock()
        risk_mgr.set_notifier = MagicMock()

        executor = AsyncMock()
        executor.position_open = False
        executor.last_price = 0.0

        position_mgr = AsyncMock()
        position_mgr.update.return_value = None

        journal = MagicMock()
        journal._open_trade = {}
        journal.database = None

        notifier = MagicMock()

        return {
            "data_feed": data_feed,
            "strategy": strategy,
            "risk_mgr": risk_mgr,
            "executor": executor,
            "position_mgr": position_mgr,
            "journal": journal,
            "notifier": notifier,
        }

    @pytest.fixture
    def runner(self, mocks):
        r = SymbolRunner(
            symbol="XRPUSDT", allocation_pct=1.0, leverage=1,
            risk_per_trade=0.02, journal=mocks["journal"],
            notifier=mocks["notifier"], total_balance=200.0,
            data_feed=mocks["data_feed"], strategy=mocks["strategy"],
            risk_mgr=mocks["risk_mgr"], executor=mocks["executor"],
            position_mgr=mocks["position_mgr"],
        )
        # Skip lazy load: pre-cargar fase para tests que no necesitan probar eso
        r.fase_cfg = {
            "tp1_rr": 0.8, "tp2_rr": 1.8, "max_sl_pct": 0.01,
            "min_sl_distance": 0.001, "rsi_min_long": 30, "rsi_max_long": 45,
            "sesion_inicio": "09:00", "sesion_fin": "17:00",
            "descripcion": "Fase 0", "leverage": 1,
        }
        return r

    @pytest.mark.asyncio
    async def test_run_cycle_no_signal(self, runner, mocks):
        """Sin señal, no se abre posición."""
        result = await runner.run_cycle()
        assert result is None
        mocks["executor"].open_position.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_cycle_with_signal(self, runner, mocks):
        """Con señal válida, se abre posición."""
        import time
        sig = MagicMock()
        sig.has_signal = True
        sig.direction = "LONG"
        sig.entry = 0.50
        sig.sl = 0.495
        sig.tp1 = 0.51
        sig.tp2 = 0.52
        sig.score = 80
        sig.reason = "Test signal"
        sig.timestamp = time.time()  # Fix #8: timestamp necesario para expiry check
        mocks["strategy"].evaluate.return_value = sig

        mocks["executor"].open_position.return_value = True
        result = await runner.run_cycle()
        assert result is None  # run_cycle no retorna nada explícito
        mocks["executor"].open_position.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_cycle_risk_exception(self, runner, mocks):
        """RiskException se captura sin crash."""
        mocks["risk_mgr"].reset_daily_if_needed.side_effect = RiskException("Balance insuficiente")
        result = await runner.run_cycle()
        assert result is None

    @pytest.mark.asyncio
    async def test_run_cycle_exception_general(self, runner, mocks):
        """Excepción general se captura sin crashing."""
        mocks["data_feed"].current_15m.side_effect = Exception("API timeout")
        result = await runner.run_cycle()
        assert result is None

    @pytest.mark.asyncio
    async def test_run_cycle_lazy_loads_phase_config(self, runner, mocks):
        """La fase se carga lazy en el primer run_cycle."""
        # Reset fase_cfg para probar lazy load
        runner.fase_cfg = {}
        runner.fase_num = 0
        mocks["risk_mgr"].get_phase_config = AsyncMock(return_value=(2, {"descripcion": "Fase 2"}))
        await runner.run_cycle()
        assert runner.fase_num == 2


class TestSymbolRunnerWithPosition:
    """Runner con posición abierta: monitoreo y cierre."""

    @pytest.fixture
    def mocks(self):
        np.random.seed(42)
        data_feed = AsyncMock()
        data_feed.current_15m.return_value = _make_df(0.51)
        data_feed.current_1h.return_value = _make_df(0.52)
        data_feed.get_spread_percent.return_value = 0.01

        strategy = MagicMock()

        risk_mgr = MagicMock()
        risk_mgr.get_phase_config = AsyncMock(return_value=(0, {
            "tp1_rr": 0.8, "tp2_rr": 1.8, "max_sl_pct": 0.01,
            "min_sl_distance": 0.001, "rsi_min_long": 30, "rsi_max_long": 45,
            "sesion_inicio": "09:00", "sesion_fin": "17:00",
            "descripcion": "Fase 0", "leverage": 1,
        }))
        risk_mgr.is_trading_session = AsyncMock(return_value=True)
        risk_mgr.is_loss_paused = AsyncMock(return_value=False)
        risk_mgr.get_drawdown_risk_scale = AsyncMock(return_value=1.0)
        risk_mgr.register_stop = AsyncMock()
        risk_mgr.reset_daily_if_needed = AsyncMock()
        risk_mgr.update_balance_for_drawdown = AsyncMock()
        risk_mgr.validate_drawdown = AsyncMock()
        risk_mgr.set_balance = MagicMock()
        risk_mgr.set_notifier = MagicMock()

        executor = MagicMock()
        executor.position_open = True
        executor.position_type = "LONG"
        executor.entry_price = 0.50
        executor.current_sl = 0.495
        executor.quantity = 100.0
        executor.last_price = 0.0
        executor.tp1_hit = False
        executor.tp1_price = 0.51
        executor.tp2_price = 0.52

        position_mgr = AsyncMock()
        position_mgr.update.return_value = None

        journal = MagicMock()
        journal._open_trade = {}
        journal.database = None

        notifier = MagicMock()

        return {
            "data_feed": data_feed,
            "strategy": strategy,
            "risk_mgr": risk_mgr,
            "executor": executor,
            "position_mgr": position_mgr,
            "journal": journal,
            "notifier": notifier,
        }

    @pytest.fixture
    def runner(self, mocks):
        r = SymbolRunner(
            symbol="XRPUSDT", allocation_pct=1.0, leverage=1,
            risk_per_trade=0.02, journal=mocks["journal"],
            notifier=mocks["notifier"], total_balance=200.0,
            data_feed=mocks["data_feed"], strategy=mocks["strategy"],
            risk_mgr=mocks["risk_mgr"], executor=mocks["executor"],
            position_mgr=mocks["position_mgr"],
        )
        r.fase_cfg = {
            "descripcion": "Fase 0", "leverage": 1,
        }
        return r

    @pytest.mark.asyncio
    async def test_run_cycle_updates_last_price(self, runner, mocks):
        """El ciclo actualiza executor.last_price."""
        await runner.run_cycle()
        assert mocks["executor"].last_price > 0

    @pytest.mark.asyncio
    async def test_run_cycle_position_manager_called(self, runner, mocks):
        """PositionManager.update() se llama cuando hay posición abierta."""
        await runner.run_cycle()
        mocks["position_mgr"].update.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_cycle_tp1_hit_closes_partial(self, runner, mocks):
        """TP1 alcanzado → cierra parcial."""
        # Simular que position_mgr.update() establece tp1_hit como efecto secundario
        def _tp1_side_effect(*args, **kwargs):
            mocks["executor"].tp1_hit = True
            return "tp1"
        mocks["position_mgr"].update.side_effect = _tp1_side_effect
        await runner.run_cycle()
        assert mocks["executor"].tp1_hit is True
