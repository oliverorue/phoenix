"""Tests de Fase 2: balance live, drawdown, stops y sesión."""

from datetime import datetime, timedelta

import pytest

from bot.core.risk_manager import RiskManager
from config.loader import TIMEZONE_NAME
import pytz


@pytest.fixture
def data_feed():
    from unittest.mock import AsyncMock, MagicMock

    feed = MagicMock()
    feed.get_balance = AsyncMock(return_value=200.0)
    feed.get_instrument_info = AsyncMock(return_value={
        "qtyStep": 1.0, "minOrderQty": 1.0, "maxOrderQty": 500000.0,
        "minNotionalValue": 5.0, "maxNotionalValue": 1_000_000.0,
    })
    feed.symbol = "XRPUSDT"
    return feed


@pytest.fixture
def risk_mgr(data_feed):
    return RiskManager(data_feed)


class TestBalanceSource:
    @pytest.mark.asyncio
    async def test_live_balance_overrides_data_feed(self, risk_mgr, data_feed):
        risk_mgr.set_initial_balance(150.0)
        data_feed.get_balance.return_value = 999.0
        assert await risk_mgr._current_balance() == pytest.approx(150.0)

        risk_mgr.update_live_balance(175.0)
        assert await risk_mgr._current_balance() == pytest.approx(175.0)

    @pytest.mark.asyncio
    async def test_backtest_balance_is_isolated(self, risk_mgr, data_feed):
        risk_mgr.set_backtest_balance(100.0)
        data_feed.get_balance.return_value = 500.0
        assert await risk_mgr._current_balance() == pytest.approx(100.0)

        # Volver a live no arrastra el balance de backtest.
        risk_mgr.set_initial_balance(200.0)
        assert await risk_mgr._current_balance() == pytest.approx(200.0)

    def test_initial_balance_preserves_notifier(self, risk_mgr):
        notifier = object()
        risk_mgr.set_notifier(notifier)
        risk_mgr.set_initial_balance(100.0)
        assert risk_mgr._notifier is notifier

    def test_backtest_balance_preserves_notifier(self, risk_mgr):
        notifier = object()
        risk_mgr.set_notifier(notifier)
        risk_mgr.set_backtest_balance(100.0)
        assert risk_mgr._notifier is notifier


class TestDailyReference:
    @pytest.mark.asyncio
    async def test_new_day_resets_capital_reference(self, risk_mgr):
        tz = pytz.timezone(TIMEZONE_NAME)
        risk_mgr.fecha_dia = datetime.now(tz).date() - timedelta(days=1)
        risk_mgr.capital_inicio_dia = 100.0
        risk_mgr.update_live_balance(150.0)

        day_changed = await risk_mgr.reset_daily_if_needed()

        assert day_changed is True
        assert risk_mgr.capital_inicio_dia == pytest.approx(150.0)
        assert risk_mgr._peak_balance_daily == pytest.approx(150.0)
        assert risk_mgr._peak_balance_session == pytest.approx(150.0)

    @pytest.mark.asyncio
    async def test_same_day_keeps_capital_reference(self, risk_mgr):
        tz = pytz.timezone(TIMEZONE_NAME)
        risk_mgr.fecha_dia = datetime.now(tz).date()
        risk_mgr.capital_inicio_dia = 100.0
        # Dentro del umbral de discrepancia (5%) para no rebasar la referencia.
        risk_mgr.update_live_balance(102.0)

        day_changed = await risk_mgr.reset_daily_if_needed()

        assert day_changed is False
        assert risk_mgr.capital_inicio_dia == pytest.approx(100.0)


class TestTradeResultAccounting:
    @pytest.mark.asyncio
    async def test_stop_increments_stops_and_losses(self, risk_mgr):
        await risk_mgr.register_trade_result(was_loss=True, is_stop=True)
        assert risk_mgr.stops_dia == 1
        assert risk_mgr._consecutive_losses == 1

    @pytest.mark.asyncio
    async def test_take_profit_does_not_count_as_stop(self, risk_mgr):
        await risk_mgr.register_trade_result(was_loss=False, is_stop=False)
        assert risk_mgr.stops_dia == 0
        assert risk_mgr._consecutive_losses == 0

    @pytest.mark.asyncio
    async def test_win_resets_consecutive_losses(self, risk_mgr):
        await risk_mgr.register_trade_result(was_loss=True, is_stop=True)
        assert risk_mgr._consecutive_losses == 1
        await risk_mgr.register_trade_result(was_loss=False, is_stop=False)
        assert risk_mgr._consecutive_losses == 0
        assert risk_mgr.stops_dia == 1

    @pytest.mark.asyncio
    async def test_reversal_loss_does_not_count_as_stop(self, risk_mgr):
        await risk_mgr.register_trade_result(was_loss=True, is_stop=False)
        assert risk_mgr.stops_dia == 0
        assert risk_mgr._consecutive_losses == 1


class TestSessionFilters:
    @pytest.mark.asyncio
    async def test_excluded_day_blocks_session(self, risk_mgr):
        tz = pytz.timezone(TIMEZONE_NAME)
        today = datetime.now(tz).strftime("%A")
        risk_mgr._override_cfg = {"dias_excluir": [today]}
        assert await risk_mgr.is_trading_session() is False

    @pytest.mark.asyncio
    async def test_session_accepts_hh_mm_strings(self, risk_mgr):
        tz = pytz.timezone(TIMEZONE_NAME)
        now_hour = datetime.now(tz).hour
        risk_mgr._override_cfg = {
            "dias_excluir": [],
            "sesion_inicio": f"{now_hour:02d}:00",
            "sesion_fin": f"{(now_hour + 1) % 24:02d}:00",
        }
        assert await risk_mgr.is_trading_session() is True


class TestRiskReward:
    @pytest.mark.asyncio
    async def test_validates_tp1_not_tp2(self, risk_mgr):
        risk_mgr._override_cfg = {"tp1_rr": 1.5}

        # TP1 = 0.5R aunque TP2 = 5R: debe rechazar.
        assert await risk_mgr.validate_rr(100.0, 100.5, 99.0) is False
        # TP1 = 2.5R: debe aceptar.
        assert await risk_mgr.validate_rr(100.0, 102.5, 99.0) is True
