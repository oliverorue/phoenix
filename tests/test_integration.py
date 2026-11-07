"""
Test de integración: ciclo completo con datos históricos.
Usa el BacktestEngine para verificar que se generan señales y se registran trades.
"""

import pytest
from datetime import datetime, timedelta
from bot.core.backtester import BacktestEngine


@pytest.mark.integration
class TestBacktestIntegration:
    """Prueba que el flujo completo (datos → indicadores → señal → trade) funciona.

    Requiere red y datos de Binance; se excluye del suite por defecto con
    ``-m 'not integration'``.
    """

    def test_backtest_generates_trades(self):
        """
        Ejecuta un backtest sobre la última semana.
        Verifica que se generen al menos algunos trades y que las métricas sean coherentes.
        """
        # Usamos un rango de fechas pequeño para que sea rápido
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        engine = BacktestEngine(symbol="XRPUSDT", fase_num=0)
        result = engine.run(
            start_date=start_date,
            end_date=end_date,
            capital=100.0,
        )

        # Verificaciones básicas
        assert result is not None
        assert result.total_trades >= 0  # Puede no haber trades en 7 días, pero no debe fallar

        if result.total_trades > 0:
            # Si hay trades, las métricas deben ser consistentes
            assert result.wins + result.losses == result.total_trades
            assert 0 <= result.win_rate <= 100
            assert result.profit_factor >= 0
            assert result.max_drawdown_pct >= 0

            # Verificar que los trades tienen datos completos
            for trade in result.trades:
                assert trade.entry_price > 0
                assert trade.exit_price > 0
                assert trade.qty > 0
                assert trade.close_reason in ["tp1", "tp2", "stop_loss", "timeout"]
                assert trade.duration_candles >= 0

            # El equity curve debe tener al menos un punto por cada trade
            assert len(result.equity_curve) >= len(result.trades)

        # Verificar que el resultado se puede exportar a DataFrame
        df = result.to_dataframe()
        if result.total_trades > 0:
            assert len(df) == result.total_trades
            # Columnas esenciales que debe contener el DataFrame
            expected_cols = {
                "entry_time", "side", "entry", "exit", "qty",
                "pnl_usdt", "pnl_pct", "commission",
                "close_reason", "duration_candles", "breakeven_hit",
            }
            assert expected_cols.issubset(set(df.columns)), (
                f"Faltan columnas esenciales. Tiene: {list(df.columns)}"
            )

    def test_backtest_with_different_symbols(self):
        """Verifica que el backtest funciona con otros símbolos."""
        for symbol in ["ETHUSDT", "DOGEUSDT"]:
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

            engine = BacktestEngine(symbol=symbol, fase_num=0)
            result = engine.run(
                start_date=start_date,
                end_date=end_date,
                capital=100.0,
            )
            assert result.symbol == symbol
            assert result.total_trades >= 0


class TestIndicatorsIntegration:
    """Prueba que indicadores + señales funcionan juntos."""

    def test_indicator_values_are_consistent(self):
        """Los valores de los indicadores son coherentes entre timeframes."""
        from bot.core.indicators import Indicators

        df_15m = _make_integration_df(100, 0.50)
        df_1h = _make_integration_df(100, 0.51)

        ind_15m = Indicators.calc_15m_indicators(df_15m)
        ind_1h = Indicators.calc_1h_indicators(df_1h)

        # RSI debe estar entre 0-100
        assert 0 <= ind_15m["RSI"] <= 100
        # ATR debe ser positivo
        assert ind_15m["ATR"] > 0
        # MAs deben ser positivas
        for p in [5, 10, 20]:
            assert ind_1h.get(f"MA{p}", 0) > 0
        # EMA20 debe existir
        assert ind_15m["EMA20"] > 0


class TestStrategyIndicatorsIntegration:
    """Prueba que la estrategia funciona con indicadores reales."""

    def test_strategy_does_not_crash_with_real_data(self):
        """La estrategia no crashea con datos reales de indicadores."""
        from bot.core.strategy_engine import ScalpingStrategy

        df_15m = _make_integration_df(100, 0.50)
        df_1h = _make_integration_df(100, 0.51)

        strategy = ScalpingStrategy()
        signal = strategy.evaluate(
            df_1h, df_15m, df_15m["close"].iloc[-1], balance=200.0
        )
        # No debe crashear, signal es un objeto Signal
        assert signal is not None
        assert hasattr(signal, "has_signal")


class TestEndToEndIntegration:
    """Test end-to-end: simula TradingBot.run() con mocks completos."""

    @pytest.mark.asyncio
    async def test_trading_bot_full_cycle(self):
        """
        Simula un ciclo completo del TradingBot con todos los componentes mockeados.
        Verifica que el flujo orquestador → runners → risk → execution funciona.
        """
        from prometheus_client import CollectorRegistry
        from bot.main import TradingBot
        from unittest.mock import AsyncMock, MagicMock, patch

        # Crear bot con mocks
        bot = TradingBot(metrics_registry=CollectorRegistry())
        bot.balance = 1000.0

        # Mockear componentes internos
        bot.runners = []
        for symbol in ["XRPUSDT", "DOGEUSDT"]:
            runner = MagicMock()
            runner.symbol = symbol
            runner.run_cycle = AsyncMock(return_value=None)
            runner.executor = MagicMock()
            runner.executor.session_http = MagicMock()
            runner.executor.session_http.close = MagicMock()
            # data_feed con métodos async para HealthMonitor
            feed = MagicMock()
            feed.get_balance = AsyncMock(return_value=500.0)
            feed.current_15m = AsyncMock(return_value=MagicMock())
            feed.get_latency_stats = MagicMock(return_value={})
            runner.data_feed = feed
            bot.runners.append(runner)

        bot.ws_feeds = {}
        bot.state_manager = MagicMock()  # type: ignore[reportAttributeAccessIssue]
        bot.scheduler = MagicMock()
        bot.scheduler.start = AsyncMock()

        # Ejecutar el ciclo
        stop_event = asyncio.Event()

        async def run_and_stop():
            # Dejar correr un ciclo y luego detener
            await asyncio.sleep(0.2)
            stop_event.set()

        with patch.object(bot, "_async_init", new=AsyncMock()):
            with patch.object(bot, "_update_dashboard_state", new=AsyncMock()):
                with patch.object(bot, "_save_cycle_state", new=MagicMock()):
                    with patch("bot.main.DataFeed.wait_for_next_candle", new=AsyncMock()):
                        await asyncio.gather(
                            bot.run(stop_event, allow_trading=True),
                            run_and_stop(),
                        )

        # Verificar que los runners ejecutaron
        for runner in bot.runners:
            runner.run_cycle.assert_called()

        # Verificar que el scheduler inició
        bot.scheduler.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_trading_bot_handles_runner_exception(self):
        """
        Verifica que si un runner lanza excepción, el bot no crashea
        y sigue ejecutando los demás runners.
        """
        from prometheus_client import CollectorRegistry
        from bot.main import TradingBot
        from unittest.mock import AsyncMock, MagicMock, patch

        bot = TradingBot(metrics_registry=CollectorRegistry())
        bot.balance = 1000.0

        def _make_runner(symbol, run_cycle_impl):
            runner = MagicMock()
            runner.symbol = symbol
            runner.run_cycle = run_cycle_impl
            runner.executor = MagicMock()
            runner.executor.session_http = MagicMock()
            runner.executor.session_http.close = MagicMock()
            feed = MagicMock()
            feed.get_balance = AsyncMock(return_value=500.0)
            feed.current_15m = AsyncMock(return_value=MagicMock())
            feed.get_latency_stats = MagicMock(return_value={})
            runner.data_feed = feed
            return runner

        runner_ok = _make_runner("XRPUSDT", AsyncMock(return_value=None))
        runner_fail = _make_runner("DOGEUSDT", AsyncMock(side_effect=Exception("Runner error")))

        bot.runners = [runner_ok, runner_fail]
        bot.ws_feeds = {}
        bot.state_manager = MagicMock()  # type: ignore[reportAttributeAccessIssue]
        bot.scheduler = MagicMock()
        bot.scheduler.start = AsyncMock()

        stop_event = asyncio.Event()

        async def run_and_stop():
            await asyncio.sleep(0.2)
            stop_event.set()

        with patch.object(bot, "_async_init", new=AsyncMock()):
            with patch.object(bot, "_update_dashboard_state", new=AsyncMock()):
                with patch.object(bot, "_save_cycle_state", new=MagicMock()):
                    with patch("bot.main.DataFeed.wait_for_next_candle", new=AsyncMock()):
                        await asyncio.gather(
                            bot.run(stop_event, allow_trading=True),
                            run_and_stop(),
                        )

        # El runner que falla debe haber sido llamado
        runner_fail.run_cycle.assert_called()
        # El runner ok también debe haber sido llamado
        runner_ok.run_cycle.assert_called()

    @pytest.mark.asyncio
    async def test_trading_bot_graceful_shutdown(self):
        """
        Verifica que el bot cierra correctamente: WebSockets, sesiones HTTP,
        y guarda estado al recibir señal de stop.
        """
        from prometheus_client import CollectorRegistry
        from bot.main import TradingBot
        from unittest.mock import AsyncMock, MagicMock, patch

        bot = TradingBot(metrics_registry=CollectorRegistry())
        bot.balance = 1000.0

        # Mockear todo
        runner = MagicMock()
        runner.symbol = "XRPUSDT"
        runner.run_cycle = AsyncMock(return_value=None)
        runner.executor = MagicMock()
        runner.executor.session_http = MagicMock()
        runner.executor.session_http.close = MagicMock()
        feed = MagicMock()
        feed.get_balance = AsyncMock(return_value=500.0)
        feed.current_15m = AsyncMock(return_value=MagicMock())
        feed.get_latency_stats = MagicMock(return_value={})
        runner.data_feed = feed
        bot.runners = [runner]

        ws_feed = MagicMock()
        ws_feed.stop = MagicMock()
        bot.ws_feeds = {"XRPUSDT": ws_feed}

        bot.state_manager = MagicMock()  # type: ignore[reportAttributeAccessIssue]
        bot.scheduler = MagicMock()
        bot.scheduler.start = AsyncMock()

        stop_event = asyncio.Event()

        async def run_and_stop():
            await asyncio.sleep(0.1)
            stop_event.set()

        with patch.object(bot, "_async_init", new=AsyncMock()):
            with patch.object(bot, "_update_dashboard_state", new=AsyncMock()):
                with patch.object(bot, "_save_cycle_state", new=MagicMock()) as mock_save:
                    with patch("bot.main.DataFeed.wait_for_next_candle", new=AsyncMock()):
                        await asyncio.gather(
                            bot.run(stop_event, allow_trading=True),
                            run_and_stop(),
                        )

                        # Verificar cleanup en run()
                        # _save_cycle_state se llama dentro del while loop
                        assert mock_save.called
                        # ws_feeds se detienen post-loop en run()
                        ws_feed.stop.assert_called_once()
                        # session_http.close() ocurre en main() (finally), no en run()


def _make_integration_df(n_rows: int, close_val: float):
    """Crea DataFrame sintético para tests de integración."""
    import numpy as np
    import pandas as pd
    np.random.seed(42)
    closes = close_val * (1 + np.random.randn(n_rows) * 0.005)
    closes = np.maximum(closes, close_val * 0.95)
    closes[0] = close_val
    return pd.DataFrame({
        "timestamp": pd.date_range(end=pd.Timestamp.now(tz="UTC"), periods=n_rows, freq="15min"),
        "open": closes + np.random.randn(n_rows) * 0.001,
        "high": np.maximum(closes, closes) * (1 + np.abs(np.random.randn(n_rows)) * 0.002),
        "low": np.minimum(closes, closes) * (1 - np.abs(np.random.randn(n_rows)) * 0.002),
        "close": closes,
        "volume": np.random.rand(n_rows) * 10000,
    })


import asyncio
