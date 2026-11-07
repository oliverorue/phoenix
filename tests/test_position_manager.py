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
        exec.sync_position_from_exchange = AsyncMock(return_value=None)
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
    async def test_tp1_fill_activates_trailing(self, position_mgr, mock_executor):
        """Cuando TP1 ya tiene fill, se mueve breakeven y se activa trailing."""
        mock_executor.tp1_hit = True
        df = pd.DataFrame()
        await position_mgr.update(current_price=1.05, df_15m=df, df_1h=df)
        assert mock_executor.trailing_activated is True
        mock_executor.update_stop_loss.assert_any_call(1.0008)

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
        # Breakeven temprano NO marca TP1 como ejecutado.
        assert mock_executor.tp1_hit is False
        mock_executor.update_stop_loss.assert_called_with(1.0008)

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
    async def test_tp1_fill_does_not_lower_existing_sl(self, position_mgr, mock_executor):
        """Si el SL ya está mejor que breakeven/trailing, no se mueve hacia atrás."""
        mock_executor.tp1_hit = True
        mock_executor.current_sl = 1.10
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
    @pytest.mark.asyncio
    async def test_tp1_price_touch_without_fill_does_not_mark(
        self, position_mgr, mock_executor
    ):
        """Tocar TP1 no es un fill: no se marca tp1_hit."""
        position_mgr.risk_phase_config = AsyncMock(
            return_value=(0, {"breakeven_trigger": 10.0, "trailing_dist": 0.001})
        )
        mock_executor.tp1_hit = False
        await position_mgr.update(
            current_price=1.06, df_15m=pd.DataFrame(), df_1h=pd.DataFrame()
        )
        assert mock_executor.tp1_hit is False
        assert mock_executor.trailing_activated is False

    @pytest.mark.asyncio
    async def test_trailing_moves_sl_with_price(self, position_mgr, mock_executor):
        """El trailing mueve el SL detrás del extremo alcanzado."""
        mock_executor.tp1_hit = True
        mock_executor.trailing_activated = False
        mock_executor.current_sl = 1.0008
        position_mgr._breakeven_done = True
        position_mgr.risk_phase_config = AsyncMock(
            return_value=(0, {"trailing_dist": 0.001})
        )

        await position_mgr.update(
            current_price=1.05, df_15m=pd.DataFrame(), df_1h=pd.DataFrame()
        )

        assert mock_executor.trailing_activated is True
        mock_executor.update_stop_loss.assert_called_with(
            pytest.approx(1.05 * 0.999)
        )
