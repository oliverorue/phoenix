"""Tests para el optimizador de parámetros (FASE 5.2)."""

from unittest.mock import MagicMock, patch
import pandas as pd


class TestOptimizerAllCombinations:
    """Verifica _all_combinations()."""

    def test_all_combinations_single_param(self):
        from bot.core.optimizer import _all_combinations

        grid = {"risk_per_trade": [0.008, 0.012]}
        combos = _all_combinations(grid)
        assert len(combos) == 2
        assert combos[0] == {"risk_per_trade": 0.008}
        assert combos[1] == {"risk_per_trade": 0.012}

    def test_all_combinations_multiple_params(self):
        from bot.core.optimizer import _all_combinations

        grid = {
            "risk_per_trade": [0.008, 0.012],
            "tp1_rr": [1.5, 2.0],
        }
        combos = _all_combinations(grid)
        assert len(combos) == 4  # 2 x 2

    def test_all_combinations_empty_grid(self):
        from bot.core.optimizer import _all_combinations

        combos = _all_combinations({})
        assert combos == [{}]

    def test_all_combinations_keys_preserved(self):
        from bot.core.optimizer import _all_combinations

        grid = {
            "risk_per_trade": [0.008],
            "max_sl_pct": [0.025],
            "tp1_rr": [1.5, 2.0],
        }
        combos = _all_combinations(grid)
        for combo in combos:
            assert set(combo.keys()) == {"risk_per_trade", "max_sl_pct", "tp1_rr"}


class TestOptimizerParamGrid:
    """Verifica la grilla de parámetros por defecto."""

    def test_param_grid_has_expected_keys(self):
        from bot.core.optimizer import PARAM_GRID

        expected_keys = {
            "risk_per_trade", "max_sl_pct", "tp1_rr",
            "tp2_rr", "breakeven_trigger", "trailing_dist",
        }
        assert set(PARAM_GRID.keys()) == expected_keys

    def test_param_grid_values_are_lists(self):
        from bot.core.optimizer import PARAM_GRID

        for key, values in PARAM_GRID.items():
            assert isinstance(values, list), f"{key} no es lista"
            assert len(values) > 0, f"{key} está vacío"


class TestOptimizerFunction:
    """Verifica la función optimize() con mocks."""

    def test_optimize_returns_dataframe(self):
        from bot.core.optimizer import optimize

        with patch("bot.core.optimizer.BacktestEngine") as mock_engine_cls:
            engine = MagicMock()
            result = MagicMock()
            result.total_trades = 10
            result.profit_factor = 1.5
            result.win_rate = 60.0
            result.total_pnl_pct = 15.0
            result.max_drawdown_pct = 5.0
            result.avg_win_pct = 2.0
            result.avg_loss_pct = -1.0
            result.max_consecutive_losses = 3
            result.sharpe_ratio = 1.2
            engine.run.return_value = result
            mock_engine_cls.return_value = engine

            df = optimize(
                symbol="XRPUSDT",
                fase_num=0,
                start_date="2026-01-01",
                end_date="2026-01-07",
                capital=100.0,
            )

            assert isinstance(df, pd.DataFrame)
            assert not df.empty
            assert "profit_factor" in df.columns
            assert df.iloc[0]["profit_factor"] == 1.5

    def test_optimize_min_trades_filter(self):
        from bot.core.optimizer import optimize

        with patch("bot.core.optimizer.BacktestEngine") as mock_engine_cls:
            engine = MagicMock()
            result = MagicMock()
            result.total_trades = 3  # Menos de min_trades=5
            engine.run.return_value = result
            mock_engine_cls.return_value = engine

            df = optimize(
                symbol="XRPUSDT",
                fase_num=0,
                start_date="2026-01-01",
                end_date="2026-01-07",
                capital=100.0,
                min_trades=5,
            )

            assert df.empty  # Filtrado por min_trades

    def test_optimize_cancel_flag(self):
        from bot.core.optimizer import optimize
        import threading

        cancel_flag = threading.Event()
        cancel_flag.set()  # Cancelar inmediatamente

        with patch("bot.core.optimizer.BacktestEngine") as mock_engine_cls:
            df = optimize(
                symbol="XRPUSDT",
                fase_num=0,
                start_date="2026-01-01",
                end_date="2026-01-07",
                capital=100.0,
                cancel_flag=cancel_flag,
            )

            assert mock_engine_cls.call_count == 0 or df.empty
            # No debe ejecutar backtests si está cancelado

    def test_optimize_sorts_by_profit_factor(self):
        from bot.core.optimizer import optimize

        results = [
            MagicMock(total_trades=10, profit_factor=2.0, win_rate=70.0,
                      total_pnl_pct=20.0, max_drawdown_pct=3.0,
                      avg_win_pct=3.0, avg_loss_pct=-1.0,
                      max_consecutive_losses=2, sharpe_ratio=1.5),
            MagicMock(total_trades=10, profit_factor=1.2, win_rate=55.0,
                      total_pnl_pct=8.0, max_drawdown_pct=8.0,
                      avg_win_pct=1.5, avg_loss_pct=-1.2,
                      max_consecutive_losses=4, sharpe_ratio=0.8),
        ]
        result_iter = iter(results)

        # Override PARAM_GRID to only have 2 combinations (matching the 2 mock results)
        small_grid = {
            "risk_per_trade": [0.008, 0.012],
            "max_sl_pct": [0.025],
        }

        with patch("bot.core.optimizer.PARAM_GRID", new=small_grid):
            with patch("bot.core.optimizer.BacktestEngine") as mock_engine_cls:
                engine = MagicMock()
                engine.run.side_effect = lambda *a, **kw: next(result_iter)
                mock_engine_cls.return_value = engine

                df = optimize(
                    symbol="XRPUSDT",
                    fase_num=0,
                    start_date="2026-01-01",
                    end_date="2026-01-07",
                    capital=100.0,
                    min_trades=1,
                )

                assert df.iloc[0]["profit_factor"] == 2.0  # Mejor PF primero
                assert df.iloc[1]["profit_factor"] == 1.2


class TestOptimizerCLI:
    """Verifica que el CLI del optimizer tenga argumentos correctos."""

    def test_cli_args(self):
        from bot.core.optimizer import parser

        # Simular args
        args = parser.parse_args([
            "--symbol", "XRPUSDT",
            "--start", "2026-01-01",
            "--end", "2026-01-07",
            "--capital", "100",
            "--fase", "0",
            "--min-trades", "5",
        ])
        assert args.symbol == "XRPUSDT"
        assert args.start == "2026-01-01"
        assert args.end == "2026-01-07"
        assert args.capital == 100.0
        assert args.fase == 0
        assert args.min_trades == 5

    def test_cli_defaults(self):
        from bot.core.optimizer import parser

        args = parser.parse_args([
            "--start", "2026-01-01",
            "--end", "2026-01-07",
        ])
        assert args.symbol == "XRPUSDT"  # default
        assert args.capital == 100.0  # default
        assert args.fase == 0  # default
        assert args.min_trades == 5  # default
