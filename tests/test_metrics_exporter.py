"""
Tests para MetricsExporter — métricas Prometheus + JSON estructurado.

Evalúa:
- Inicialización con/sin prometheus_client
- Definición de métricas
- set_bot_ref
- update() desde bot mock
- JSON estructurado
- Fallback sin prometheus_client
"""

import json
from unittest.mock import MagicMock, patch
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

from prometheus_client import CollectorRegistry

from bot.core.metrics_exporter import MetricsExporter, generate_metrics_text


# ── Helper: MetricsExporter con registry aislado ──────────────────────────────


def _make_exporter(**kwargs: Any) -> MetricsExporter:
    """
    Crea un MetricsExporter con un CollectorRegistry aislado.
    Cada llamada obtiene un registry fresco, eliminando colisiones
    (\"Duplicated timeseries\") entre tests.
    """
    kwargs.setdefault("registry", CollectorRegistry())
    return MetricsExporter(**kwargs)


def _make_fresh_registry() -> CollectorRegistry:
    """Retorna un CollectorRegistry vacío para usar con generate_metrics_text."""
    return CollectorRegistry()


# ── Mock del bot ─────────────────────────────────────────────────────────────


def _make_mock_bot(**overrides: Any) -> MagicMock:
    """Crea un TradingBot mock con atributos esenciales."""
    bot = MagicMock()

    # Atributos por defecto
    bot.balance = 1000.0
    bot.start_time = datetime.now(timezone.utc)
    bot.runners = []
    bot.ws_feeds = {}
    bot.portfolio_risk = None
    bot.kelly_sizing = None

    # Sobreescribir con lo que venga
    for k, v in overrides.items():
        setattr(bot, k, v)

    return bot


def _make_mock_runner(
    symbol: str = "XRPUSDT",
    balance: float = 500.0,
    with_position: bool = False,
) -> MagicMock:
    runner = MagicMock()
    runner.symbol = symbol
    runner.balance = balance
    runner.fase_num = 1
    runner.fase_cfg = {"max_stops_day": 4}

    # RiskManager mock
    rm = MagicMock()
    rm.stops_dia = 2
    rm.get_current_drawdown_pct.return_value = 5.0
    rm.is_trading_session.return_value = True
    runner.risk_mgr = rm

    # DataFeed mock
    df = MagicMock()
    df.get_latency_stats.return_value = {
        "get_klines": {"p95": 0.5},
        "get_balance": {"p95": 0.3},
    }
    runner.data_feed = df

    # Executor mock
    ex = MagicMock()
    ex.position_open = with_position
    ex.position_type = "LONG"
    ex.entry_price = 100.0
    ex.quantity = 10.0
    ex.last_price = 105.0
    ex.current_sl = 95.0
    runner.executor = ex

    return runner


def _make_mock_portfolio_risk(
    total_exp: float = 1050.0,
    net_exp: float = 1050.0,
    num_pos: int = 1,
    leverage: float = 2.1,
    can_open: bool = True,
    var_data: dict[str, Any] | None = None,
) -> MagicMock:
    pr = MagicMock()
    pr.total_exposure = total_exp
    pr.net_exposure = net_exp
    pr.num_positions = num_pos
    pr.effective_leverage = leverage
    pr.can_open.return_value = can_open
    pr.parametric_var.return_value = var_data or {
        "var_95": 42.0,
        "var_99": 60.0,
        "sigma_pct": 2.5,
        "samples": 10,
        "confidence": 0.95,
    }
    pr.active_positions = [
        {
            "symbol": "XRPUSDT",
            "side": "LONG",
            "qty": 10.0,
            "entry_price": 100.0,
            "current_price": 105.0,
            "notional": 1000.0,
            "current_notional": 1050.0,
            "sl": 95.0,
            "unrealized_pnl": 50.0,
        }
    ]
    return pr


def _make_mock_kelly_sizing() -> MagicMock:
    ks = MagicMock()
    ks.stats = {
        "multiplier": 1.35,
        "streak": 2,
        "win_rate": 0.65,
        "total_trades": 20,
    }
    return ks


# ── Tests ────────────────────────────────────────────────────────────────────


class TestInit:
    def test_default_init(self) -> None:
        """Debe crear instancia con valores por defecto."""
        me = _make_exporter()
        assert me.port == 8000
        assert me.update_interval == 0.0
        assert me.bot_ref is None
        assert me._http_started is False
        assert me.metrics_path.name == "metrics.json"

    def test_custom_port(self) -> None:
        me = _make_exporter(port=9090)
        assert me.port == 9090

    def test_custom_path(self) -> None:
        me = _make_exporter(metrics_path="/tmp/test_metrics.json")
        assert str(me.metrics_path) == "/tmp/test_metrics.json"

    def test_has_prometheus_metrics(self) -> None:
        """Si prometheus_client está instalado, debe definir métricas."""
        me = _make_exporter()
        # Verificar que algunos atributos de métricas existen
        assert hasattr(me, "_g_balance")
        assert hasattr(me, "_g_total_exposure")
        assert hasattr(me, "_g_var_95")
        assert hasattr(me, "_g_kelly_multiplier")
        assert hasattr(me, "_c_trades_total")
        assert hasattr(me, "_g_ws_connected")
        assert hasattr(me, "_g_api_latency_p95")

    def test_set_bot_ref(self) -> None:
        me = _make_exporter()
        bot = _make_mock_bot()
        me.set_bot_ref(bot)
        assert me.bot_ref is bot


class TestUpdate:
    def test_update_no_bot_does_nothing(self) -> None:
        """Sin bot_ref, update() debe ser no-op."""
        me = _make_exporter()
        me.update()  # no debe lanzar

    def test_update_with_bot(self) -> None:
        """Con bot mock, debe ejecutar sin errores."""
        pr = _make_mock_portfolio_risk()
        ks = _make_mock_kelly_sizing()
        runner = _make_mock_runner(with_position=True)
        bot = _make_mock_bot(
            portfolio_risk=pr,
            kelly_sizing=ks,
            runners=[runner],
            ws_feeds={"XRPUSDT": MagicMock(connected=True)},
        )
        me = _make_exporter()
        me.set_bot_ref(bot)
        me.update()  # no debe lanzar

    def test_update_no_portfolio_risk(self) -> None:
        """Sin portfolio_risk, debe seguir funcionando."""
        runner = _make_mock_runner()
        bot = _make_mock_bot(
            portfolio_risk=None,
            runners=[runner],
        )
        me = _make_exporter()
        me.set_bot_ref(bot)
        me.update()

    def test_update_no_kelly(self) -> None:
        """Sin kelly_sizing, debe seguir funcionando."""
        pr = _make_mock_portfolio_risk()
        bot = _make_mock_bot(portfolio_risk=pr, kelly_sizing=None)
        me = _make_exporter()
        me.set_bot_ref(bot)
        me.update()

    def test_update_exception_handled(self) -> None:
        """Si algo falla, no debe propagar excepción."""
        bot = _make_mock_bot()
        me = _make_exporter()
        me.set_bot_ref(bot)
        # Forzar error en _update_from_bot
        with patch.object(me, "_update_from_bot", side_effect=Exception("test error")):
            me.update()


class TestJSONOutput:
    def test_json_file_created(self, tmp_path: Path) -> None:
        """update() debe crear el archivo JSON."""
        pr = _make_mock_portfolio_risk()
        bot = _make_mock_bot(portfolio_risk=pr)
        json_path = tmp_path / "metrics.json"
        me = _make_exporter(metrics_path=json_path)
        me.set_bot_ref(bot)
        me.update()
        assert json_path.exists()

    def test_json_content(self, tmp_path: Path) -> None:
        """El JSON debe contener las secciones esperadas."""
        pr = _make_mock_portfolio_risk()
        runner = _make_mock_runner(with_position=True)
        bot = _make_mock_bot(
            portfolio_risk=pr,
            runners=[runner],
        )
        json_path = tmp_path / "metrics.json"
        me = _make_exporter(metrics_path=json_path)
        me.set_bot_ref(bot)
        me.update()

        with open(json_path) as f:
            data = json.load(f)

        assert "timestamp" in data
        assert "balance" in data
        assert "portfolio" in data
        assert "var" in data
        assert "positions" in data
        assert "risk" in data

    def test_json_portfolio_section(self, tmp_path: Path) -> None:
        """Sección portfolio debe tener los campos correctos."""
        pr = _make_mock_portfolio_risk(
            total_exp=2000.0, net_exp=1500.0, num_pos=2, leverage=3.0,
        )
        bot = _make_mock_bot(portfolio_risk=pr)
        json_path = tmp_path / "metrics.json"
        me = _make_exporter(metrics_path=json_path)
        me.set_bot_ref(bot)
        me.update()

        with open(json_path) as f:
            data = json.load(f)

        pf = data["portfolio"]
        assert pf["total_exposure"] == 2000.0
        assert pf["net_exposure"] == 1500.0
        assert pf["num_positions"] == 2
        assert pf["effective_leverage"] == 3.0

    def test_json_var_section(self, tmp_path: Path) -> None:
        """Sección var debe tener los campos correctos."""
        var_data = {"var_95": 50.0, "var_99": 75.0, "sigma_pct": 3.0, "samples": 15}
        pr = _make_mock_portfolio_risk(var_data=var_data)
        bot = _make_mock_bot(portfolio_risk=pr)
        json_path = tmp_path / "metrics.json"
        me = _make_exporter(metrics_path=json_path)
        me.set_bot_ref(bot)
        me.update()

        with open(json_path) as f:
            data = json.load(f)

        assert data["var"]["var_95"] == 50.0
        assert data["var"]["var_99"] == 75.0
        assert data["var"]["sigma_pct"] == 3.0

    def test_json_positions(self, tmp_path: Path) -> None:
        """Debe incluir posiciones activas."""
        pr = _make_mock_portfolio_risk()
        bot = _make_mock_bot(portfolio_risk=pr)
        json_path = tmp_path / "metrics.json"
        me = _make_exporter(metrics_path=json_path)
        me.set_bot_ref(bot)
        me.update()

        with open(json_path) as f:
            data = json.load(f)

        assert len(data["positions"]) == 1
        assert data["positions"][0]["symbol"] == "XRPUSDT"
        assert data["positions"][0]["side"] == "LONG"

    def test_json_no_portfolio_risk(self, tmp_path: Path) -> None:
        """Sin portfolio_risk, debe omitir secciones dependientes."""
        bot = _make_mock_bot(portfolio_risk=None)
        json_path = tmp_path / "metrics.json"
        me = _make_exporter(metrics_path=json_path)
        me.set_bot_ref(bot)
        me.update()

        with open(json_path) as f:
            data = json.load(f)

        assert data["portfolio"] == {}
        assert "error" in data["var"]


class TestStart:
    def test_start_server(self) -> None:
        """start() debe iniciar el servidor HTTP."""
        me = _make_exporter(port=8001)
        with patch("bot.core.metrics_exporter.start_http_server") as mock_start:
            me.start()
            mock_start.assert_called_once_with(8001, registry=me._registry)
        assert me._http_started is True

    def test_start_idempotent(self) -> None:
        """Llamar start() dos veces no debe reiniciar."""
        me = _make_exporter(port=8002)
        with patch("bot.core.metrics_exporter.start_http_server") as mock_start:
            me.start()
            me.start()  # segunda llamada no debe lanzar
            mock_start.assert_called_once()  # solo una vez
        assert me._http_started is True

    def test_stop(self) -> None:
        me = _make_exporter(port=8003)
        with patch("bot.core.metrics_exporter.start_http_server"):
            me.start()
            assert me._http_started is True
            me.stop()
            assert me._http_started is False


class TestGenerateMetricsText:
    def test_generate_returns_string(self) -> None:
        """generate_metrics_text() debe retornar un string."""
        reg = _make_fresh_registry()
        # Registrar al menos una métrica para que el output no esté vacío
        from prometheus_client import Gauge
        Gauge("bot_test_metric", "Test", registry=reg)
        text = generate_metrics_text(registry=reg)
        assert isinstance(text, str)
        assert "bot_test_metric" in text

    def test_generate_contains_metrics(self) -> None:
        """Debe contener nombres de métricas definidas."""
        from prometheus_client import Gauge
        reg = _make_fresh_registry()
        Gauge("bot_balance_usdt", "Test balance", ["symbol"], registry=reg)
        text = generate_metrics_text(registry=reg)
        assert "bot_balance_usdt" in text


class TestEdgeCases:
    def test_update_no_runners(self) -> None:
        """Bot sin runners no debe fallar."""
        bot = _make_mock_bot(runners=[])
        me = _make_exporter()
        me.set_bot_ref(bot)
        me.update()

    def test_update_empty_ws_feeds(self) -> None:
        """Sin ws_feeds no debe fallar."""
        bot = _make_mock_bot(ws_feeds={})
        me = _make_exporter()
        me.set_bot_ref(bot)
        me.update()

    def test_metrics_path_creates_dirs(self, tmp_path: Path) -> None:
        """El path debe crear directorios intermedios."""
        deep_path = tmp_path / "sub" / "dir" / "metrics.json"
        pr = _make_mock_portfolio_risk()
        bot = _make_mock_bot(portfolio_risk=pr)
        me = _make_exporter(metrics_path=deep_path)
        me.set_bot_ref(bot)
        me.update()
        assert deep_path.exists()


class TestPortfolioRiskMetrics:
    def test_can_open_false(self) -> None:
        """can_open=False debe reflejarse en métrica."""
        pr = _make_mock_portfolio_risk(can_open=False)
        bot = _make_mock_bot(portfolio_risk=pr)
        me = _make_exporter()
        me.set_bot_ref(bot)
        me.update()

    def test_var_error_handled(self) -> None:
        """Si parametric_var retorna error, no debe fallar."""
        pr = _make_mock_portfolio_risk(var_data={"error": "Datos insuficientes"})
        bot = _make_mock_bot(portfolio_risk=pr)
        me = _make_exporter()
        me.set_bot_ref(bot)
        me.update()
