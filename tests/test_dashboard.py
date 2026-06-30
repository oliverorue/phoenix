"""Tests para el Dashboard (FASE 5.2).

Prueba funciones utilitarias de app.py y el BotController.
Streamlit no se puede probar directamente sin su entorno, pero sí las helpers.
"""

import pandas as pd
from unittest.mock import patch


# ─────────────────────────────────────────────────────────────
# Helpers de app.py
# ─────────────────────────────────────────────────────────────

class TestDashboardHelpers:
    """Verifica funciones helper del dashboard."""

    def test_format_time_ago_now(self):
        from bot.dashboard.app import format_time_ago
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()
        result = format_time_ago(now)
        assert result == "Ahora" or result.startswith("hace")

    def test_format_time_ago_seconds(self):
        from bot.dashboard.app import format_time_ago
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
        result = format_time_ago(past)
        assert "hace" in result and "s" in result

    def test_format_time_ago_minutes(self):
        from bot.dashboard.app import format_time_ago
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        result = format_time_ago(past)
        assert "hace" in result and "m" in result

    def test_format_time_ago_hours(self):
        from bot.dashboard.app import format_time_ago
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
        result = format_time_ago(past)
        assert "hace" in result and "h" in result

    def test_format_time_ago_none(self):
        from bot.dashboard.app import format_time_ago
        assert format_time_ago("") == "Nunca"

    def test_format_time_ago_invalid(self):
        from bot.dashboard.app import format_time_ago
        assert format_time_ago("not-a-date") == "not-a-date"

    def test_create_phase_badge(self):
        from bot.dashboard.app import create_phase_badge
        badge = create_phase_badge(0)
        assert "phase-0" in badge
        assert "FASE 0" in badge
        badge = create_phase_badge(5)  # out of range
        assert "phase-4" in badge  # clamped

    def test_metric_card_html_basic(self):
        from bot.dashboard.app import metric_card_html
        html = metric_card_html("Balance", "$100.50")
        assert "Balance" in html
        assert "100.50" in html
        assert "glass-card" in html

    def test_metric_card_html_with_extra(self):
        from bot.dashboard.app import metric_card_html
        html = metric_card_html("Precio", "$0.6185", extra="XRPUSDT")
        assert "XRPUSDT" in html

    def test_metric_card_html_escapes_dollar(self):
        from bot.dashboard.app import metric_card_html
        html = metric_card_html("Test", "$100")
        assert "&#36;" in html

    def test_create_rsi_gauge(self):
        from bot.dashboard.app import create_rsi_gauge
        fig = create_rsi_gauge(45)
        assert fig is not None

    def test_create_rsi_gauge_zero(self):
        from bot.dashboard.app import create_rsi_gauge
        fig = create_rsi_gauge(0)
        assert fig is not None

    def test_create_winrate_donut_empty(self):
        from bot.dashboard.app import create_winrate_donut
        fig = create_winrate_donut(0, 0)
        assert fig is not None

    def test_create_winrate_donut_with_data(self):
        from bot.dashboard.app import create_winrate_donut
        fig = create_winrate_donut(10, 5)
        assert fig is not None

    def test_create_equity_curve_empty(self):
        from bot.dashboard.app import create_equity_curve
        fig = create_equity_curve(pd.DataFrame())
        assert fig is not None

    def test_create_pnl_distribution_empty(self):
        from bot.dashboard.app import create_pnl_distribution
        fig = create_pnl_distribution(pd.DataFrame())
        assert fig is not None

    def test_create_portfolio_pie_empty(self):
        from bot.dashboard.app import create_portfolio_pie
        fig = create_portfolio_pie([])
        assert fig is not None

    def test_create_trade_timeline_empty(self):
        from bot.dashboard.app import create_trade_timeline
        fig = create_trade_timeline(pd.DataFrame())
        assert fig is not None

    def test_create_monthly_performance_empty(self):
        from bot.dashboard.app import create_monthly_performance
        fig = create_monthly_performance(pd.DataFrame())
        assert fig is not None

    def test_create_symbol_performance_empty(self):
        from bot.dashboard.app import create_symbol_performance
        fig = create_symbol_performance(pd.DataFrame())
        assert fig is not None

    def test_get_data_files_summary(self, tmp_path):
        """Verifica que listar archivos funcione."""
        from bot.dashboard.app import get_data_files_summary
        summary = get_data_files_summary()
        assert isinstance(summary, list)


class TestDashboardCandlestickChart:
    """Verifica create_candlestick_chart."""

    def test_candlestick_empty(self):
        from bot.dashboard.app import create_candlestick_chart
        fig = create_candlestick_chart(pd.DataFrame())
        assert fig is not None

    def test_candlestick_with_data(self):
        from bot.dashboard.app import create_candlestick_chart
        import numpy as np
        dates = pd.date_range("2026-01-01", periods=10, freq="15min", tz="UTC")
        df = pd.DataFrame({
            "timestamp": dates,
            "open": np.random.rand(10) + 0.6,
            "high": np.random.rand(10) + 0.65,
            "low": np.random.rand(10) + 0.55,
            "close": np.random.rand(10) + 0.6,
            "volume": np.random.rand(10) * 1000,
        })
        fig = create_candlestick_chart(df, title="Test")
        assert fig is not None


class TestDashboardSavePortfolio:
    """Verifica _save_portfolio_to_yaml."""

    def test_save_portfolio_to_yaml(self, tmp_path):
        # Simular guardado de portfolio
        from bot.dashboard.app import _save_portfolio_to_yaml
        with patch("bot.dashboard.app.ROOT_DIR", tmp_path):
            config_dir = tmp_path / "config"
            config_dir.mkdir()
            settings_file = config_dir / "settings.yaml"
            settings_file.write_text("portfolio:\n  symbols: []\n")
            result = _save_portfolio_to_yaml([
                {"symbol": "XRPUSDT", "allocation_pct": 0.5, "active": True}
            ])
            assert result is True

    def test_save_portfolio_failure_returns_false(self, tmp_path):
        from bot.dashboard.app import _save_portfolio_to_yaml
        with patch("builtins.open", side_effect=PermissionError("denied")):
            result = _save_portfolio_to_yaml([{"symbol": "XRP"}])
            assert result is False


# ─────────────────────────────────────────────────────────────
# BotController
# ─────────────────────────────────────────────────────────────

class TestBotController:
    """Verifica BotController."""

    def test_controller_init(self):
        from bot.dashboard.bot_controller import BotController
        ctrl = BotController()
        assert ctrl._thread is None
        assert ctrl.is_running() is False

    def test_start_creates_thread(self):
        from bot.dashboard.bot_controller import BotController
        ctrl = BotController()
        result = ctrl.start()
        assert result is True
        assert ctrl._thread is not None
        assert ctrl._thread.is_alive()
        ctrl.stop()

    def test_start_when_already_running(self):
        from bot.dashboard.bot_controller import BotController
        ctrl = BotController()
        ctrl.start()
        result = ctrl.start()  # segundo start
        assert result is False
        ctrl.stop()

    def test_stop_sets_event(self):
        from bot.dashboard.bot_controller import BotController
        ctrl = BotController()
        ctrl.start()
        result = ctrl.stop()
        assert result is True
        assert ctrl._stop_event.is_set()
        assert ctrl.is_running() is False

    def test_stop_when_not_running(self):
        from bot.dashboard.bot_controller import BotController
        ctrl = BotController()
        result = ctrl.stop()
        assert result is True

    def test_restart_stops_and_starts(self):
        from bot.dashboard.bot_controller import BotController
        ctrl = BotController()
        result = ctrl.restart()
        assert result is True
        ctrl.stop()

    def test_restart_with_config(self, tmp_path):
        from bot.dashboard.bot_controller import BotController
        with patch("bot.dashboard.bot_controller.ROOT_DIR", tmp_path):
            config_dir = tmp_path / "config"
            config_dir.mkdir()
            settings_file = config_dir / "settings.yaml"
            settings_file.write_text("portfolio:\n  symbols: []\n")
            ctrl = BotController()
            result = ctrl.restart_with_config([
                {"symbol": "XRPUSDT", "allocation_pct": 0.5, "active": True}
            ])
            assert result is True
            # Verificar que se guardó en settings.yaml
            import yaml
            with open(settings_file) as f:
                data = yaml.safe_load(f)
            assert data["portfolio"]["symbols"][0]["symbol"] == "XRPUSDT"
            ctrl.stop()

    def test_is_running_after_stop(self):
        from bot.dashboard.bot_controller import BotController
        ctrl = BotController()
        ctrl.start()
        ctrl.stop()
        assert ctrl.is_running() is False

    def test_is_running_thread_dead(self):
        from bot.dashboard.bot_controller import BotController
        ctrl = BotController()
        assert ctrl.is_running() is False
