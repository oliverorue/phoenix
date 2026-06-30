import pytest
from unittest.mock import MagicMock, AsyncMock, patch
import pandas as pd
from bot.core.position_manager import PositionManager


class TestPositionManager:
    @pytest.fixture
    def mock_executor(self):
        exec = MagicMock()
        exec.position_open = True
        exec.position_type = "LONG"
        exec.entry_price = 1.00
        exec.current_sl = 0.95
        exec.tp1_price = 1.05
        exec.tp2_price = 1.10
        exec.tp1_hit = False
        exec.trailing_activated = False
        exec.journal = MagicMock()
        exec.journal._open_trade = {}
        return exec

    @pytest.fixture
    def mock_data_feed(self):
        data = MagicMock()
        data.get_balance = AsyncMock(return_value=50.0)
        return data

    @pytest.fixture
    def position_mgr(self, mock_executor, mock_data_feed):
        mgr = PositionManager(mock_executor, mock_data_feed)
        mgr.indicators = MagicMock()
        mgr.indicators.calc_15m_indicators.return_value = {}
        mgr.indicators.calc_1h_indicators.return_value = {}
        mgr.risk_phase_config = AsyncMock(return_value=(0, {'trailing_dist': 0.001}))
        return mgr

    @pytest.mark.asyncio
    async def test_tp1_hit_activates_trailing(self, position_mgr, mock_executor):
        """Cuando el precio alcanza TP1, se marca tp1_hit y se activa trailing stop."""
        df = pd.DataFrame()
        await position_mgr.update(current_price=1.05, df_15m=df, df_1h=df)
        assert mock_executor.tp1_hit == True
        mock_executor.activate_trailing_stop.assert_called_once()
        mock_executor.update_stop_loss.assert_called_with(1.00)

    @pytest.mark.asyncio
    async def test_tp1_reached_long(self, position_mgr, mock_executor):
        """TP1 se alcanza en LONG cuando price >= tp1_price."""
        assert position_mgr._tp1_reached(1.05) is True
        assert position_mgr._tp1_reached(1.06) is True
        assert position_mgr._tp1_reached(1.04) is False

    @pytest.mark.asyncio
    async def test_tp1_reached_short(self, position_mgr, mock_executor):
        """TP1 se alcanza en SHORT cuando price <= tp1_price."""
        mock_executor.position_type = "SHORT"
        assert position_mgr._tp1_reached(1.05) is True
        assert position_mgr._tp1_reached(1.04) is True
        assert position_mgr._tp1_reached(1.06) is False

    @pytest.mark.asyncio
    async def test_breakeven_early_triggered(self, position_mgr, mock_executor):
        """Breakeven temprano se activa si el progreso >= trigger."""
        mock_executor.tp1_hit = False
        # entry=1.00, tp1=1.05, trigger=0.4 → progreso = (1.02-1.00)/(1.05-1.00)=0.4
        position_mgr.risk_phase_config = AsyncMock(return_value=(0, {'breakeven_trigger': 0.4, 'trailing_dist': 0.001}))
        df = pd.DataFrame()
        await position_mgr.update(current_price=1.02, df_15m=df, df_1h=df)
        assert mock_executor.tp1_hit is True
        mock_executor.update_stop_loss.assert_called_with(1.00)

    @pytest.mark.asyncio
    async def test_breakeven_early_not_triggered(self, position_mgr, mock_executor):
        """Breakeven NO se activa si el progreso es menor al trigger."""
        mock_executor.tp1_hit = False
        position_mgr.risk_phase_config = AsyncMock(return_value=(0, {'breakeven_trigger': 0.4, 'trailing_dist': 0.001}))
        df = pd.DataFrame()
        await position_mgr.update(current_price=1.005, df_15m=df, df_1h=df)
        # progreso = (1.005-1.00)/(1.05-1.00) = 0.01 < 0.4
        assert mock_executor.tp1_hit is False

    @pytest.mark.asyncio
    async def test_detect_reversal_long_rsi_high(self, position_mgr, mock_executor):
        """Reversión LONG: RSI > 65 + bearish rejection."""
        ind_15m = {"RSI": 70}
        ind_1h = {"MA20": 0.98}
        with patch.object(PositionManager, "indicators", create=True):
            with patch("bot.core.indicators.Indicators.has_bearish_rejection", return_value=True):
                assert position_mgr._detect_reversal(ind_15m, ind_1h, 1.00) is True

    @pytest.mark.asyncio
    async def test_detect_reversal_long_price_below_ma(self, position_mgr, mock_executor):
        """Reversión LONG: precio cae bajo MA20."""
        ind_15m = {"RSI": 50}
        ind_1h = {"MA20": 1.02}
        assert position_mgr._detect_reversal(ind_15m, ind_1h, 1.00) is True

    @pytest.mark.asyncio
    async def test_detect_reversal_short_rsi_low(self, position_mgr, mock_executor):
        """Reversión SHORT: RSI < 35 + bullish rejection."""
        mock_executor.position_type = "SHORT"
        ind_15m = {"RSI": 30}
        ind_1h = {"MA20": 0.98}
        with patch("bot.core.indicators.Indicators.has_bullish_rejection", return_value=True):
            assert position_mgr._detect_reversal(ind_15m, ind_1h, 1.00) is True

    @pytest.mark.asyncio
    async def test_detect_reversal_short_price_above_ma(self, position_mgr, mock_executor):
        """Reversión SHORT: precio sube sobre MA20."""
        mock_executor.position_type = "SHORT"
        ind_15m = {"RSI": 50}
        ind_1h = {"MA20": 0.95}
        assert position_mgr._detect_reversal(ind_15m, ind_1h, 1.00) is True

    @pytest.mark.asyncio
    async def test_detect_no_reversal(self, position_mgr, mock_executor):
        """Sin condiciones de reversión."""
        ind_15m = {"RSI": 50}
        ind_1h = {"MA20": 0.95}
        assert position_mgr._detect_reversal(ind_15m, ind_1h, 1.00) is False

    @pytest.mark.asyncio
    async def test_update_no_position(self, position_mgr, mock_executor):
        """Si no hay posición abierta, update retorna None sin efectos."""
        mock_executor.position_open = False
        df = pd.DataFrame()
        result = await position_mgr.update(current_price=1.05, df_15m=df, df_1h=df)
        assert result is None

    @pytest.mark.asyncio
    async def test_risk_phase_config_uses_get_balance(self, mock_executor, mock_data_feed):
        """risk_phase_config debe llamar a data.get_balance."""
        mgr = PositionManager(mock_executor, mock_data_feed)
        fase, _ = await mgr.risk_phase_config()
        mock_data_feed.get_balance.assert_awaited_once()
        assert isinstance(fase, int)

    @pytest.mark.asyncio
    async def test_tp1_already_hit_does_not_call_update_stop_loss(self, position_mgr, mock_executor):
        """Si tp1_hit ya es True, no debe volver a llamar update_stop_loss."""
        mock_executor.tp1_hit = True
        df = pd.DataFrame()
        await position_mgr.update(current_price=1.05, df_15m=df, df_1h=df)
        mock_executor.update_stop_loss.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_reversal_returned(self, position_mgr, mock_executor):
        """Cuando se detecta reversión, update retorna 'close_reversal'."""
        mock_executor.position_open = True
        mock_executor.tp1_hit = True
        mock_executor.trailing_activated = True
        # update() llama internamente a self.indicators.calc_*_indicators(df)
        # con los DataFrames que pasamos. Parcheamos esos métodos para simular
        # indicadores que disparen la reversión.
        with patch.object(position_mgr.indicators, "calc_15m_indicators",
                          return_value={"RSI": 70}), \
             patch.object(position_mgr.indicators, "calc_1h_indicators",
                          return_value={"MA20": 1.02}), \
             patch("bot.core.indicators.Indicators.has_bearish_rejection", return_value=True):
            df = pd.DataFrame()
            result = await position_mgr.update(current_price=1.00, df_15m=df, df_1h=df)
            assert result == "close_reversal"
            mock_executor.close_position.assert_called_once()