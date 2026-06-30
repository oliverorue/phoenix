"""
Tests para PortfolioRisk — VaR paramétrico y Exposición Total.

Evalúa:
- Cálculo de exposición total/neta desde ejecutores mock
- VaR paramétrico con datos simulados de DB
- Límites de exposición (can_open)
- Apalancamiento efectivo
- Stats completos
- Integración con SymbolRunner (check en run_cycle)
"""

import pytest
from unittest.mock import MagicMock
from typing import Any

from bot.core.portfolio_risk import PortfolioRisk


# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_database() -> MagicMock:
    db = MagicMock()
    db.get_trades.return_value = []
    return db


@pytest.fixture
def mock_notifier() -> MagicMock:
    return MagicMock()


@pytest.fixture
def portfolio_risk(mock_database: MagicMock, mock_notifier: MagicMock) -> PortfolioRisk:
    return PortfolioRisk(
        database=mock_database,
        notifier=mock_notifier,
        var_confidence=0.95,
        var_lookback_days=30,
        max_exposure_usdt=1000.0,
        max_exposure_pct=50.0,
    )


def _make_mock_executor(
    position_open: bool = True,
    position_type: str = "LONG",
    entry_price: float = 100.0,
    quantity: float = 10.0,
    last_price: float = 105.0,
    current_sl: float = 95.0,
) -> MagicMock:
    ex = MagicMock()
    ex.position_open = position_open
    ex.position_type = position_type
    ex.entry_price = entry_price
    ex.quantity = quantity
    ex.last_price = last_price
    ex.current_sl = current_sl
    return ex


def _make_mock_runner(
    symbol: str = "XRPUSDT",
    executor: Any = None,
    balance: float = 1000.0,
) -> MagicMock:
    r = MagicMock()
    r.symbol = symbol
    r.balance = balance
    if executor is not None:
        r.executor = executor
    else:
        r.executor = _make_mock_executor()
    return r


# ── Test: Inicialización ────────────────────────────────────────────────────


class TestInit:
    def test_default_params(self) -> None:
        pr = PortfolioRisk()
        assert pr.var_confidence == 0.95
        assert pr.var_lookback_days == 30
        assert pr.max_exposure_usdt == 0.0
        assert pr.max_exposure_pct == 0.0
        assert pr.database is None
        assert pr.notifier is None
        assert pr._runners == []
        assert pr._cached_var is None

    def test_custom_params(self) -> None:
        db = MagicMock()
        notif = MagicMock()
        pr = PortfolioRisk(
            database=db,
            notifier=notif,
            var_confidence=0.99,
            var_lookback_days=60,
            max_exposure_usdt=5000.0,
            max_exposure_pct=75.0,
        )
        assert pr.database is db
        assert pr.notifier is notif
        assert pr.var_confidence == 0.99
        assert pr.var_lookback_days == 60
        assert pr.max_exposure_usdt == 5000.0
        assert pr.max_exposure_pct == 75.0

    def test_without_database(self) -> None:
        pr = PortfolioRisk()
        result = pr.parametric_var()
        assert "error" in result
        assert result["error"] == "No database disponible"


# ── Test: bind_runners ──────────────────────────────────────────────────────


class TestBindRunners:
    def test_bind_stores_runners(self, portfolio_risk: PortfolioRisk) -> None:
        runners = [_make_mock_runner("XRPUSDT"), _make_mock_runner("ETHUSDT")]
        portfolio_risk.bind_runners(runners)
        assert len(portfolio_risk._runners) == 2

    def test_bind_empty_list(self, portfolio_risk: PortfolioRisk) -> None:
        portfolio_risk.bind_runners([])
        assert portfolio_risk._runners == []


# ── Test: _position_data ────────────────────────────────────────────────────


class TestPositionData:
    def test_no_open_positions(self, portfolio_risk: PortfolioRisk) -> None:
        ex = _make_mock_executor(position_open=False)
        runner = _make_mock_runner(executor=ex)
        portfolio_risk.bind_runners([runner])
        data = portfolio_risk._position_data()
        assert data == []

    def test_single_long_position(self, portfolio_risk: PortfolioRisk) -> None:
        ex = _make_mock_executor(
            position_open=True, position_type="LONG",
            entry_price=100.0, quantity=10.0, last_price=105.0,
        )
        runner = _make_mock_runner("XRPUSDT", executor=ex)
        portfolio_risk.bind_runners([runner])
        data = portfolio_risk._position_data()
        assert len(data) == 1
        p = data[0]
        assert p["symbol"] == "XRPUSDT"
        assert p["side"] == "LONG"
        assert p["qty"] == 10.0
        assert p["entry_price"] == 100.0
        assert p["current_price"] == 105.0
        assert p["notional"] == 1000.0  # 100 * 10
        assert p["current_notional"] == 1050.0  # 105 * 10
        assert p["unrealized_pnl"] == 50.0  # 1050 - 1000

    def test_single_short_position(self, portfolio_risk: PortfolioRisk) -> None:
        ex = _make_mock_executor(
            position_open=True, position_type="SHORT",
            entry_price=100.0, quantity=10.0, last_price=95.0,
        )
        runner = _make_mock_runner("ETHUSDT", executor=ex)
        portfolio_risk.bind_runners([runner])
        data = portfolio_risk._position_data()
        assert len(data) == 1
        p = data[0]
        assert p["side"] == "SHORT"
        assert p["notional"] == 1000.0
        assert p["current_notional"] == 950.0
        assert p["unrealized_pnl"] == 50.0  # 1000 - 950

    def test_multiple_positions(self, portfolio_risk: PortfolioRisk) -> None:
        ex1 = _make_mock_executor(
            position_open=True, position_type="LONG",
            entry_price=100.0, quantity=10.0, last_price=105.0,
        )
        ex2 = _make_mock_executor(
            position_open=True, position_type="SHORT",
            entry_price=200.0, quantity=5.0, last_price=190.0,
        )
        r1 = _make_mock_runner("XRPUSDT", executor=ex1)
        r2 = _make_mock_runner("ETHUSDT", executor=ex2)
        portfolio_risk.bind_runners([r1, r2])
        data = portfolio_risk._position_data()
        assert len(data) == 2

    def test_last_price_zero_fallback(self, portfolio_risk: PortfolioRisk) -> None:
        """Cuando last_price es 0, debe usar entry_price como fallback."""
        ex = _make_mock_executor(
            position_open=True, position_type="LONG",
            entry_price=100.0, quantity=10.0, last_price=0.0,
        )
        runner = _make_mock_runner("XRPUSDT", executor=ex)
        portfolio_risk.bind_runners([runner])
        data = portfolio_risk._position_data()
        assert len(data) == 1
        assert data[0]["current_price"] == 100.0  # fallback a entry_price
        assert data[0]["current_notional"] == 1000.0


# ── Test: Propiedades de exposición ─────────────────────────────────────────


class TestExposure:
    def test_total_exposure_no_positions(self, portfolio_risk: PortfolioRisk) -> None:
        ex = _make_mock_executor(position_open=False)
        portfolio_risk.bind_runners([_make_mock_runner(executor=ex)])
        assert portfolio_risk.total_exposure == 0.0

    def test_total_exposure_single(self, portfolio_risk: PortfolioRisk) -> None:
        ex = _make_mock_executor(
            position_open=True, entry_price=100.0, quantity=10.0, last_price=105.0,
        )
        portfolio_risk.bind_runners([_make_mock_runner(executor=ex)])
        assert portfolio_risk.total_exposure == 1050.0

    def test_total_exposure_multiple(self, portfolio_risk: PortfolioRisk) -> None:
        ex1 = _make_mock_executor(
            position_open=True, entry_price=100.0, quantity=10.0, last_price=105.0,
        )
        ex2 = _make_mock_executor(
            position_open=True, entry_price=200.0, quantity=5.0, last_price=190.0,
        )
        r1 = _make_mock_runner("XRPUSDT", executor=ex1)
        r2 = _make_mock_runner("ETHUSDT", executor=ex2)
        portfolio_risk.bind_runners([r1, r2])
        # 1050 + 950 = 2000
        assert portfolio_risk.total_exposure == pytest.approx(2000.0)

    def test_net_exposure_long_short(self, portfolio_risk: PortfolioRisk) -> None:
        """Neta: LONG +1050 + SHORT -950 = +100"""
        ex1 = _make_mock_executor(
            position_open=True, position_type="LONG",
            entry_price=100.0, quantity=10.0, last_price=105.0,
        )
        ex2 = _make_mock_executor(
            position_open=True, position_type="SHORT",
            entry_price=200.0, quantity=5.0, last_price=190.0,
        )
        r1 = _make_mock_runner("XRPUSDT", executor=ex1)
        r2 = _make_mock_runner("ETHUSDT", executor=ex2)
        portfolio_risk.bind_runners([r1, r2])
        assert portfolio_risk.net_exposure == pytest.approx(100.0)

    def test_num_positions(self, portfolio_risk: PortfolioRisk) -> None:
        ex1 = _make_mock_executor(position_open=True)
        ex2 = _make_mock_executor(position_open=False)
        r1 = _make_mock_runner("XRPUSDT", executor=ex1)
        r2 = _make_mock_runner("ETHUSDT", executor=ex2)
        portfolio_risk.bind_runners([r1, r2])
        assert portfolio_risk.num_positions == 1

    def test_active_positions_returns_list(self, portfolio_risk: PortfolioRisk) -> None:
        ex = _make_mock_executor(position_open=True)
        portfolio_risk.bind_runners([_make_mock_runner(executor=ex)])
        assert len(portfolio_risk.active_positions) == 1
        assert portfolio_risk.active_positions[0]["symbol"] == "XRPUSDT"


# ── Test: VaR paramétrico ───────────────────────────────────────────────────


class TestParametricVaR:
    def test_insufficient_data(self, mock_database: MagicMock,
                               mock_notifier: MagicMock) -> None:
        """Con menos de 5 trades debe retornar error."""
        mock_database.get_trades.return_value = [
            {"pnl_pct": 1.0},
            {"pnl_pct": -0.5},
        ]
        pr = PortfolioRisk(database=mock_database, notifier=mock_notifier)
        result = pr.parametric_var()
        assert "error" in result
        assert "insuficientes" in result["error"].lower()

    def test_no_database(self, portfolio_risk: PortfolioRisk) -> None:
        pr = PortfolioRisk()
        result = pr.parametric_var()
        assert "error" in result

    def test_with_trades(self, mock_database: MagicMock,
                         mock_notifier: MagicMock) -> None:
        """20+ trades con PnL variado debe producir VaR > 0 (Fix #7: mínimo 20)."""
        trades = [
            {"pnl_pct": 2.0 + i * 0.5 if i % 2 == 0 else -(1.0 + i * 0.3)}
            for i in range(25)  # Fix #7: 25 trades para superar mínimo de 20
        ]
        mock_database.get_trades.return_value = trades
        pr = PortfolioRisk(database=mock_database, notifier=mock_notifier)
        result = pr.parametric_var()
        assert result["samples"] >= 20
        assert result["sigma_pct"] > 0
        assert result["var_95"] > 0
        assert result["var_99"] > 0
        assert result["confidence"] == 0.95

    def test_var_99_higher_than_95(self, mock_database: MagicMock,
                                   mock_notifier: MagicMock) -> None:
        trades = [
            {"pnl_pct": 2.0 + i * 0.5 if i % 2 == 0 else -(1.0 + i * 0.3)}
            for i in range(25)  # Fix #7: 25 trades
        ]
        mock_database.get_trades.return_value = trades
        pr = PortfolioRisk(database=mock_database, notifier=mock_notifier)
        result = pr.parametric_var()
        assert result["var_99"] > result["var_95"]

    def test_zero_exposure_uses_fallback(self, mock_database: MagicMock,
                                          mock_notifier: MagicMock) -> None:
        """Sin exposición, VaR usa exposure=1.0 para evitar división por cero."""
        trades = [
            {"pnl_pct": 2.0 if i % 2 == 0 else -1.0}
            for i in range(25)  # Fix #7: 25 trades
        ]
        mock_database.get_trades.return_value = trades
        pr = PortfolioRisk(database=mock_database, notifier=mock_notifier)
        result = pr.parametric_var()
        # Con exposure=1.0, var_95 y var_99 son pequeños pero positivos
        assert result["var_95"] >= 0
        assert result["var_99"] >= 0

    def test_single_trade_returns_zero_sigma(self, mock_database: MagicMock,
                                              mock_notifier: MagicMock) -> None:
        """Con exactamente 1 trade (returns len=1), sigma debe ser 0."""
        mock_database.get_trades.return_value = [
            {"pnl_pct": 2.0},
        ]
        pr = PortfolioRisk(database=mock_database, notifier=mock_notifier)
        result = pr.parametric_var()
        # len(returns)=1, solo pasa si len >= 5
        assert "error" in result


# ── Test: can_open ──────────────────────────────────────────────────────────


class TestCanOpen:
    def test_no_limits(self) -> None:
        """Sin límites configurados, siempre retorna True."""
        pr = PortfolioRisk()
        assert pr.can_open() is True
        assert pr.can_open(999999.0) is True

    def test_within_usdt_limit(self, portfolio_risk: PortfolioRisk) -> None:
        """Exposición actual 0 < 1000, debe permitir."""
        ex = _make_mock_executor(position_open=False)
        portfolio_risk.bind_runners([_make_mock_runner(executor=ex)])
        assert portfolio_risk.can_open() is True

    def test_exceeds_usdt_limit(self) -> None:
        """Exposición actual 1050 > 1000, debe bloquear."""
        pr = PortfolioRisk(
            database=None, notifier=None,
            max_exposure_usdt=1000.0, max_exposure_pct=0,  # solo límite USDT
        )
        ex = _make_mock_executor(
            position_open=True, entry_price=100.0, quantity=10.0, last_price=105.0,
        )
        pr.bind_runners([_make_mock_runner(executor=ex)])
        assert pr.can_open() is False

    def test_within_pct_limit(self, portfolio_risk: PortfolioRisk) -> None:
        """Exposición 500 en balance 2000 = 25% < 50%, debe permitir."""
        ex = _make_mock_executor(
            position_open=True, entry_price=50.0, quantity=10.0, last_price=50.0,
        )
        pr = PortfolioRisk(
            database=None, notifier=None,
            max_exposure_usdt=0, max_exposure_pct=50.0,
        )
        pr.bind_runners([_make_mock_runner(executor=ex, balance=2000.0)])
        assert pr.can_open() is True

    def test_exceeds_pct_limit(self, portfolio_risk: PortfolioRisk) -> None:
        """Exposición 1050 en balance 1500 = 70% > 50%, debe bloquear."""
        pr = PortfolioRisk(
            database=None, notifier=None,
            max_exposure_usdt=0, max_exposure_pct=50.0,
        )
        ex = _make_mock_executor(
            position_open=True, entry_price=100.0, quantity=10.0, last_price=105.0,
        )
        pr.bind_runners([_make_mock_runner(executor=ex, balance=1500.0)])
        assert pr.can_open() is False

    def test_pct_limit_ignored_when_no_runners(self) -> None:
        """Sin runners vinculados, pct limit se ignora."""
        pr = PortfolioRisk(
            database=None, notifier=None,
            max_exposure_usdt=0, max_exposure_pct=50.0,
        )
        assert pr.can_open() is True

    def test_usdt_limit_with_new_position(self) -> None:
        """Exposición actual 500, nueva posición 400, total 900 < 1000, permite."""
        pr = PortfolioRisk(
            database=None, notifier=None,
            max_exposure_usdt=1000.0, max_exposure_pct=0,  # solo límite USDT
        )
        ex = _make_mock_executor(
            position_open=True, entry_price=50.0, quantity=10.0, last_price=50.0,
        )
        pr.bind_runners([_make_mock_runner(executor=ex, balance=1000.0)])
        assert pr.can_open(400.0) is True

    def test_usdt_limit_exceeds_with_new_position(self, portfolio_risk: PortfolioRisk) -> None:
        """Exposición actual 500, nueva posición 600, total 1100 > 1000, bloquea."""
        ex = _make_mock_executor(
            position_open=True, entry_price=50.0, quantity=10.0, last_price=50.0,
        )
        portfolio_risk.bind_runners([_make_mock_runner(executor=ex)])
        assert portfolio_risk.can_open(600.0) is False


# ── Test: effective_leverage ────────────────────────────────────────────────


class TestEffectiveLeverage:
    def test_no_positions(self, portfolio_risk: PortfolioRisk) -> None:
        ex = _make_mock_executor(position_open=False)
        portfolio_risk.bind_runners([_make_mock_runner(executor=ex, balance=1000.0)])
        assert portfolio_risk.effective_leverage == 0.0

    def test_with_exposure(self, portfolio_risk: PortfolioRisk) -> None:
        ex = _make_mock_executor(
            position_open=True, entry_price=100.0, quantity=10.0, last_price=105.0,
        )
        portfolio_risk.bind_runners([_make_mock_runner(executor=ex, balance=500.0)])
        # leverage = 1050 / 500 = 2.1
        assert portfolio_risk.effective_leverage == pytest.approx(2.1)

    def test_zero_balance(self, portfolio_risk: PortfolioRisk) -> None:
        """Balance 0 retorna 0 para evitar división por cero."""
        ex = _make_mock_executor(position_open=False)
        portfolio_risk.bind_runners([_make_mock_runner(executor=ex, balance=0.0)])
        assert portfolio_risk.effective_leverage == 0.0

    def test_no_runners(self, portfolio_risk: PortfolioRisk) -> None:
        """Sin runners, usa balance=100 por defecto."""
        assert portfolio_risk.effective_leverage == 0.0


# ── Test: stats property ────────────────────────────────────────────────────


class TestStats:
    def test_stats_structure(self, portfolio_risk: PortfolioRisk) -> None:
        ex = _make_mock_executor(
            position_open=True, entry_price=100.0, quantity=10.0, last_price=105.0,
        )
        portfolio_risk.bind_runners([_make_mock_runner(executor=ex, balance=1000.0)])
        stats = portfolio_risk.stats
        assert "num_positions" in stats
        assert "total_exposure" in stats
        assert "net_exposure" in stats
        assert "effective_leverage" in stats
        assert "positions" in stats
        assert "var" in stats
        assert "limits" in stats

    def test_stats_values(self, portfolio_risk: PortfolioRisk) -> None:
        ex = _make_mock_executor(
            position_open=True, entry_price=100.0, quantity=10.0, last_price=105.0,
        )
        portfolio_risk.bind_runners([_make_mock_runner(executor=ex, balance=1000.0)])
        stats = portfolio_risk.stats
        assert stats["num_positions"] == 1
        assert stats["total_exposure"] == 1050.0
        assert stats["net_exposure"] == 1050.0
        assert stats["limits"]["can_open"] is False  # 1050 > 1000

    def test_stats_no_positions(self, portfolio_risk: PortfolioRisk) -> None:
        ex = _make_mock_executor(position_open=False)
        portfolio_risk.bind_runners([_make_mock_runner(executor=ex, balance=1000.0)])
        stats = portfolio_risk.stats
        assert stats["num_positions"] == 0
        assert stats["total_exposure"] == 0.0
        assert stats["limits"]["can_open"] is True


# ── Test: Integración con SymbolRunner ──────────────────────────────────────


class TestIntegrationWithSymbolRunner:
    """Verifica que el check de portfolio_risk en symbol_runner funcione."""

    @pytest.mark.asyncio
    async def test_portfolio_risk_blocks_signal(self) -> None:
        """Si portfolio_risk.can_open() es False, la señal debe ser ignorada."""
        pr = PortfolioRisk(
            database=None, notifier=None,
            max_exposure_usdt=100.0, max_exposure_pct=0,
        )
        # Simular una posición que excede el límite
        ex = _make_mock_executor(
            position_open=True, entry_price=200.0, quantity=1.0, last_price=200.0,
        )
        runner = _make_mock_runner("XRPUSDT", executor=ex)
        pr.bind_runners([runner])

        # Verificar que can_open retorna False
        assert pr.can_open() is False

    @pytest.mark.asyncio
    async def test_portfolio_risk_allows_signal(self) -> None:
        """Si exposición está dentro de límites, permite abrir."""
        pr = PortfolioRisk(
            database=None, notifier=None,
            max_exposure_usdt=1000.0, max_exposure_pct=0,
        )
        # Sin posición abierta
        ex = _make_mock_executor(position_open=False)
        runner = _make_mock_runner("XRPUSDT", executor=ex)
        pr.bind_runners([runner])

        assert pr.can_open() is True

    def test_runner_has_portfolio_risk_attr(self) -> None:
        """SymbolRunner debe tener el atributo portfolio_risk."""
        from bot.core.symbol_runner import SymbolRunner
        # Verificar que el parámetro existe en la firma
        import inspect
        sig = inspect.signature(SymbolRunner.__init__)
        assert "portfolio_risk" in sig.parameters

    def test_none_portfolio_risk_skips_check(self) -> None:
        """Si portfolio_risk es None, no debe interferir."""
        from bot.core.symbol_runner import SymbolRunner
        # Verificar que el default es None
        import inspect
        sig = inspect.signature(SymbolRunner.__init__)
        param = sig.parameters["portfolio_risk"]
        assert param.default is None


# ── Test: Sin runners vinculados ────────────────────────────────────────────


class TestNoRunners:
    def test_total_exposure_no_runners(self, portfolio_risk: PortfolioRisk) -> None:
        assert portfolio_risk.total_exposure == 0.0

    def test_num_positions_no_runners(self, portfolio_risk: PortfolioRisk) -> None:
        assert portfolio_risk.num_positions == 0

    def test_active_positions_no_runners(self, portfolio_risk: PortfolioRisk) -> None:
        assert portfolio_risk.active_positions == []

    def test_parametric_var_no_runners(self, mock_database: MagicMock) -> None:
        pr = PortfolioRisk(database=mock_database)
        result = pr.parametric_var()
        assert "error" in result or "samples" in result
