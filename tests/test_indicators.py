import pytest
import pandas as pd
import numpy as np
from bot.core.indicators import Indicators

# ── Fixtures ───────────────────────────────────────────────
@pytest.fixture
def df_1h():
    np.random.seed(42)
    n = 50
    close = 0.50 + np.cumsum(np.random.randn(n) * 0.005)
    close = np.abs(close)
    return pd.DataFrame({
        'open': close - 0.001,
        'high': close + 0.003,
        'low': close - 0.003,
        'close': close,
        'volume': np.random.uniform(100, 1000, n),
    })

@pytest.fixture
def df_15m():
    np.random.seed(24)
    n = 50
    close = 0.50 + np.cumsum(np.random.randn(n) * 0.002)
    close = np.abs(close)
    return pd.DataFrame({
        'open': close - 0.0005,
        'high': close + 0.002,
        'low': close - 0.002,
        'close': close,
        'volume': np.random.uniform(50, 500, n),
    })

# ── Patrones de velas ─────────────────────────────────────
class TestCandlePatterns:
    def test_pin_bar_bajista_valido(self):
        ind = {'open_actual': 0.50, 'close_actual': 0.49,
               'high_actual': 0.53, 'low_actual': 0.488}
        assert Indicators.is_pin_bar_bajista(ind) == True

    def test_pin_bar_bajista_no_valido(self):
        ind = {'open_actual': 0.50, 'close_actual': 0.49,
               'high_actual': 0.505, 'low_actual': 0.48}
        assert Indicators.is_pin_bar_bajista(ind) == False

    def test_pin_bar_alcista_valido(self):
        ind = {'open_actual': 0.49, 'close_actual': 0.50,
               'high_actual': 0.502, 'low_actual': 0.47}
        assert Indicators.is_pin_bar_alcista(ind) == True

    def test_pin_bar_alcista_no_valido(self):
        ind = {'open_actual': 0.49, 'close_actual': 0.50,
               'high_actual': 0.53, 'low_actual': 0.488}
        assert Indicators.is_pin_bar_alcista(ind) == False

    def test_engulfing_bajista_valido(self):
        ind = {'open_actual': 0.52, 'close_actual': 0.48,
               'open_prev': 0.49, 'close_prev': 0.51}
        assert Indicators.is_engulfing_bajista(ind) == True

    def test_engulfing_alcista_valido(self):
        ind = {'open_actual': 0.48, 'close_actual': 0.52,
               'open_prev': 0.51, 'close_prev': 0.49}
        assert Indicators.is_engulfing_alcista(ind) == True

    def test_shooting_star_valido(self):
        ind = {'open_actual': 0.505, 'close_actual': 0.50,
               'high_actual': 0.52, 'low_actual': 0.498}
        assert Indicators.is_shooting_star(ind) == True

    def test_martillo_valido(self):
        ind = {'open_actual': 0.51, 'close_actual': 0.515,
               'high_actual': 0.518, 'low_actual': 0.48}
        assert Indicators.is_martillo(ind) == True

    def test_has_bullish_rejection(self):
        ind = {'open_actual': 0.49, 'close_actual': 0.50,
               'high_actual': 0.502, 'low_actual': 0.47}
        assert Indicators.has_bullish_rejection(ind) == True

    def test_has_bearish_rejection(self):
        ind = {'open_actual': 0.50, 'close_actual': 0.49,
               'high_actual': 0.53, 'low_actual': 0.488}
        assert Indicators.has_bearish_rejection(ind) == True

    def test_has_bullish_rejection_none(self):
        ind = {'open_actual': 0.50, 'close_actual': 0.505,
               'high_actual': 0.51, 'low_actual': 0.498,
               'open_prev': 0.50, 'close_prev': 0.50}
        assert Indicators.has_bullish_rejection(ind) == False

# ── Mercado lateral ───────────────────────────────────────
class TestMarketRanging:
    def test_is_market_ranging_true(self):
        ind_1h = {'MA_spread': 0.003, 'ADX': 14}
        ind_15m = {'RSI': 50}
        assert Indicators.is_market_ranging(ind_1h, ind_15m) == True

    def test_is_market_ranging_two_criterios(self):
        ind_1h = {'MA_spread': 0.003, 'ADX': 14}
        ind_15m = {'RSI': 30}
        assert Indicators.is_market_ranging(ind_1h, ind_15m) == True

    def test_is_market_ranging_false(self):
        ind_1h = {'MA_spread': 0.02, 'ADX': 35}
        ind_15m = {'RSI': 65}
        assert Indicators.is_market_ranging(ind_1h, ind_15m) == False

    def test_is_market_ranging_custom_config(self):
        ind_1h = {'MA_spread': 0.01, 'ADX': 20}
        ind_15m = {'RSI': 50}
        config = {'lateral_ma_distance': 0.015, 'lateral_adx_min': 22}
        assert Indicators.is_market_ranging(ind_1h, ind_15m, config) == True

# ── Cálculo de indicadores ────────────────────────────────
class TestIndicatorsCalc:
    def test_calc_1h_ma_values(self, df_1h):
        result = Indicators.calc_1h_indicators(df_1h)
        for p in [5, 10, 20]:
            assert result[f'MA{p}'] > 0

    def test_calc_15m_rsi_range(self, df_15m):
        result = Indicators.calc_15m_indicators(df_15m)
        assert 0 <= result['RSI'] <= 100

    def test_calc_15m_atr_positive(self, df_15m):
        result = Indicators.calc_15m_indicators(df_15m)
        assert result['ATR'] > 0

    def test_calc_15m_returns_all_keys(self, df_15m):
        result = Indicators.calc_15m_indicators(df_15m)
        for key in ['RSI', 'ATR', 'ATR_pct', 'vol_actual', 'vol_MA5', 'EMA20']:
            assert key in result