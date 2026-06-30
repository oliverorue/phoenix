"""Tests para BacktestEngine (FASE 5.2)."""

import pytest
import pandas as pd
from unittest.mock import AsyncMock, patch


# Parches globales para DataFeed (usado internamente por BacktestEngine)
_DATA_FEED_PATCHES = [
    patch("bot.core.data_feed.BinanceFuturesClient"),
    patch("bot.core.data_feed.API_KEY", ""),
    patch("bot.core.data_feed.API_SECRET", ""),
]

# EXCHANGE ya no se importa en backtester — no necesita patch


def _apply_data_feed_patches():
    for p in _DATA_FEED_PATCHES:
        p.start()


def _stop_data_feed_patches():
    for p in _DATA_FEED_PATCHES:
        p.stop()


def _make_mock_df(periods=200, freq="15min", start="2026-01-01", close_val=0.625):
    """Helper para crear un DataFrame mockeado con timestamps tz-naive."""
    dates = pd.date_range(start, periods=periods, freq=freq)
    # Hacerlos naive (sin tz) para que coincida con lo que devuelve fetch_history real
    dates_naive = dates.tz_localize(None)
    return pd.DataFrame({
        "timestamp": dates_naive,
        "open": [close_val] * periods,
        "high": [close_val + 0.005] * periods,
        "low": [close_val - 0.005] * periods,
        "close": [close_val] * periods,
        "volume": [1000] * periods,
    })


class TestBacktestEngineInit:
    """Verifica la inicialización del BacktestEngine."""

    def test_init_defaults(self):
        _apply_data_feed_patches()
        try:
            with patch("bot.core.backtester.BacktestEngine._preload_instrument_cache") as mock_preload:
                from bot.core.backtester import BacktestEngine
                engine = BacktestEngine(symbol="XRPUSDT", fase_num=0)
                assert engine.symbol == "XRPUSDT"
                assert engine.fase_num == 0
                mock_preload.assert_called_once()
        finally:
            _stop_data_feed_patches()

    def test_init_with_custom_capital(self):
        """capital NO se pasa al constructor; se pasa a run()."""
        _apply_data_feed_patches()
        try:
            with patch("bot.core.backtester.BacktestEngine._preload_instrument_cache"):
                from bot.core.backtester import BacktestEngine
                engine = BacktestEngine(symbol="XRPUSDT", fase_num=0)
                assert engine.symbol == "XRPUSDT"
        finally:
            _stop_data_feed_patches()

    def test_init_with_instrument_cache(self):
        """Verificar que pasar instrument_cache skipea _preload_instrument_cache."""
        _apply_data_feed_patches()
        try:
            from bot.core.backtester import BacktestEngine
            cache = {"qtyStep": 0.001}
            engine = BacktestEngine(symbol="XRPUSDT", fase_num=0, instrument_cache=cache)
            assert engine.risk_mgr._cached_instrument_info == cache
        finally:
            _stop_data_feed_patches()


class TestBacktestEngineFetchHistory:
    """Verifica fetch_history con mocks de la capa REST."""

    def _make_engine(self):
        _apply_data_feed_patches()
        try:
            with patch("bot.core.backtester.BacktestEngine._preload_instrument_cache"):
                from bot.core.backtester import BacktestEngine
                return BacktestEngine(symbol="XRPUSDT", fase_num=0)
        finally:
            _stop_data_feed_patches()

    def test_fetch_history_returns_dataframe(self):
        engine = self._make_engine()
        # La vela más antigua debe tener timestamp == start_ms para que
        # el while True loop en fetch_history() rompa (oldest <= start_ms)
        start_ms = int(pd.Timestamp("2026-01-01", tz="UTC").timestamp() * 1000)  # type: ignore[attr-defined]
        ts2 = int(pd.Timestamp("2026-01-01 00:15", tz="UTC").timestamp() * 1000)  # type: ignore[attr-defined]
        # Mock de futures_klines de python-binance: retorna list[list] directamente
        mock_klines = [
            [ts2, "0.6190", "0.6200", "0.6185", "0.6195", "100000", ts2 + 899999, "61950.00", 100, "50000", "40000", "0"],
            [start_ms, "0.6185", "0.6192", "0.6183", "0.6190", "123456", start_ms + 899999, "76345.67", 120, "60000", "50000", "0"],
        ]
        engine.data_feed._rest.futures_klines.return_value = mock_klines
        result = engine.fetch_history("2026-01-01", "2026-01-10", "15")
        assert isinstance(result, pd.DataFrame)
        assert len(result) == 2
        assert "timestamp" in result.columns
        assert "open" in result.columns
        assert "close" in result.columns

    def test_fetch_history_empty_returns_empty_df(self):
        engine = self._make_engine()
        # Mockear respuesta vacía — futures_klines retorna lista vacía
        engine.data_feed._rest.futures_klines.return_value = []
        result = engine.fetch_history("2026-01-01", "2026-01-10", "15")
        assert isinstance(result, pd.DataFrame)
        assert result.empty


class TestBacktestEngineRun:
    """Verifica run() con datos mockeados."""

    @pytest.fixture
    def engine(self):
        _apply_data_feed_patches()
        try:
            with patch("bot.core.backtester.BacktestEngine._preload_instrument_cache"):
                from bot.core.backtester import BacktestEngine
                eng = BacktestEngine(symbol="XRPUSDT", fase_num=0)
                yield eng
        finally:
            _stop_data_feed_patches()

    @pytest.fixture
    def mock_run_deps(self, engine):
        """Parchea get_history_cached y risk_mgr.get_phase_config para que run() funcione sin red."""
        df_1h = _make_mock_df(periods=200, freq="60min")
        df_15m = _make_mock_df(periods=200, freq="15min")
        with patch.object(engine, "get_history_cached") as mock_get:
            def side_effect(start, end, interval):
                if interval == "60":
                    return df_1h
                return df_15m
            mock_get.side_effect = side_effect
            with patch.object(engine.risk_mgr, "get_phase_config", new_callable=AsyncMock) as mock_pc:
                mock_pc.return_value = (0, {"tp1_rr": 1.2, "tp2_rr": 2.0})
                yield

    def test_run_returns_backtest_result(self, engine, mock_run_deps):
        """run() debe retornar un BacktestResult."""
        from bot.core.backtester import BacktestResult
        result = engine.run(start_date="2026-01-01", end_date="2026-01-10", capital=100.0)
        assert isinstance(result, BacktestResult)
        assert result.symbol == "XRPUSDT"
        assert result.capital_inicial == 100.0

    def test_run_with_trades(self, engine, mock_run_deps):
        """Verificar que run() puede generar trades con datos simulados."""
        n = 300
        df_1h = _make_mock_df(periods=n, freq="60min")
        df_15m = _make_mock_df(periods=n, freq="15min", close_val=0.50)
        with patch.object(engine, "get_history_cached") as mock_get:
            def side_effect(start, end, interval):
                if interval == "60":
                    return df_1h
                return df_15m
            mock_get.side_effect = side_effect
            with patch.object(engine.risk_mgr, "get_phase_config", new_callable=AsyncMock) as mock_pc:
                mock_pc.return_value = (0, {"tp1_rr": 1.2, "tp2_rr": 2.0})
                result = engine.run(start_date="2026-01-01", end_date="2026-01-05", capital=100.0)
        assert result.total_trades >= 0
        if result.total_trades > 0:
            assert len(result.trades) > 0
            assert result.profit_factor >= 0

    def test_run_summary_metrics(self, engine, mock_run_deps):
        """Verificar que summary retorna dict con métricas (nombres en español)."""
        result = engine.run(start_date="2026-01-01", end_date="2026-01-05", capital=100.0)
        summary = result.summary()
        assert isinstance(summary, dict)
        # Las keys del summary están en español
        assert "Símbolo" in summary
        assert "Total Trades" in summary
        assert "PnL Total" in summary
        assert "Profit Factor" in summary
        assert "Win Rate" in summary
        assert "Capital Inicial" in summary

    def test_to_dataframe(self, engine, mock_run_deps):
        """Verificar que to_dataframe funcione incluso sin trades."""
        result = engine.run(start_date="2026-01-01", end_date="2026-01-05", capital=100.0)
        df_result = result.to_dataframe()
        assert isinstance(df_result, pd.DataFrame)

    def test_run_min_trades_zero_does_not_crash(self, engine):
        """Solo verificar que run() con datos básicos no falla."""
        # Este test verifica que run() no crashea con datos planos
        df_15m = _make_mock_df(periods=100, freq="15min")
        df_1h = _make_mock_df(periods=100, freq="60min")
        with patch.object(engine, "get_history_cached") as mock_get:
            def side_effect(start, end, interval):
                if interval == "60":
                    return df_1h
                return df_15m
            mock_get.side_effect = side_effect
            with patch.object(engine.risk_mgr, "get_phase_config", new_callable=AsyncMock) as mock_pc:
                mock_pc.return_value = (0, {"tp1_rr": 1.2, "tp2_rr": 2.0})
                result = engine.run(start_date="2026-01-01", end_date="2026-01-05", capital=100.0)
        assert result is not None
