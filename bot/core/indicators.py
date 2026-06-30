"""
Cálculos de indicadores técnicos:
- Medias móviles (MA5, MA10, MA20)
- RSI
- ATR (14 y 7 para SL dinámico)
- ADX
- Volumen (MAs)
- Detección de patrones de velas
- Bollinger Bands width (para medir contracción/expansión)
"""

from typing import Any, Optional
import pandas as pd
import ta
from loguru import logger



# Valores por defecto (se pueden sobrescribir con config de fase)
DEFAULT_MA_PERIODS = [5, 10, 20]
DEFAULT_RSI_PERIOD = 14
DEFAULT_VOL_MA_FAST = 5
DEFAULT_VOL_MA_SLOW = 10
DEFAULT_BB_PERIOD = 20
DEFAULT_BB_STD = 2.0


class Indicators:
    """Clase para calcular todos los indicadores técnicos."""

    @staticmethod
    def calc_1h_indicators(df: pd.DataFrame, config: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Calcula indicadores del timeframe de 1 hora.
        - MAs (según MA_PERIODS)
        - ADX (tendencia)
        - MA spread (distancia entre MAs)
        """
        resultado = {}
        close_vals = df["close"]
        ma_periods = config.get("ma_periods", DEFAULT_MA_PERIODS) if config else DEFAULT_MA_PERIODS

        for p in ma_periods:
            if len(close_vals) >= p:
                resultado[f"MA{p}"] = close_vals.rolling(p).mean().iloc[-1]  # type: ignore[attr-defined]
            else:
                resultado[f"MA{p}"] = close_vals.iloc[-1]  # fallback

        # ADX + DI+/DI- (direccionalidad)
        try:
            adx_indicator = ta.trend.ADXIndicator(  # type: ignore[attr-defined]
                high=df["high"], low=df["low"], close=df["close"],
                window=min(14, len(df)-1)
            )
            adx_vals = adx_indicator.adx()
            resultado["ADX"] = adx_vals.iloc[-1] if not pd.isna(adx_vals.iloc[-1]) else 0

            # DI+ y DI- para determinar dirección del momentum
            # DI+ > DI- → momentum alcista (LONG)
            # DI- > DI+ → momentum bajista (SHORT)
            if len(df) >= min(14, len(df)-1) + 1:
                adx_pos_vals = adx_indicator.adx_pos()
                adx_neg_vals = adx_indicator.adx_neg()
                resultado["ADX_pos"] = adx_pos_vals.iloc[-1] if not pd.isna(adx_pos_vals.iloc[-1]) else 0
                resultado["ADX_neg"] = adx_neg_vals.iloc[-1] if not pd.isna(adx_neg_vals.iloc[-1]) else 0
            else:
                resultado["ADX_pos"] = 0
                resultado["ADX_neg"] = 0
        except Exception as e:
            logger.debug(f"Error calculando ADX: {e}")
            resultado["ADX"] = 0
            resultado["ADX_pos"] = 0
            resultado["ADX_neg"] = 0

        # MA spread
        mas = [resultado[f"MA{p}"] for p in ma_periods if resultado.get(f"MA{p}", 0) > 0]
        if mas and mas[-1] > 0 and len(mas) > 1:
            resultado["MA_spread"] = (max(mas) - min(mas)) / mas[-1]
        else:
            resultado["MA_spread"] = 0

        return resultado

    @staticmethod
    def calc_15m_indicators(df: pd.DataFrame, config: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Calcula indicadores del timeframe de 15 minutos.
        - RSI (14)
        - ATR (14) y ATR rápido (7) para SL dinámico
        - Bollinger Bands width
        - MAs de volumen (5, 10)
        - EMA 20
        - Datos de velas para patrones
        """
        rsi_period = config.get("rsi_period", DEFAULT_RSI_PERIOD) if config else DEFAULT_RSI_PERIOD
        vol_fast = config.get("vol_ma_fast", DEFAULT_VOL_MA_FAST) if config else DEFAULT_VOL_MA_FAST
        vol_slow = config.get("vol_ma_slow", DEFAULT_VOL_MA_SLOW) if config else DEFAULT_VOL_MA_SLOW
        bb_period = config.get("bb_period", DEFAULT_BB_PERIOD) if config else DEFAULT_BB_PERIOD
        bb_std = config.get("bb_std", DEFAULT_BB_STD) if config else DEFAULT_BB_STD

        # RSI
        rsi_series = ta.momentum.RSIIndicator(df["close"], window=rsi_period).rsi()  # type: ignore[attr-defined]

        # ATR estándar (14 periodos)
        atr_indicator = ta.volatility.AverageTrueRange(  # type: ignore[attr-defined]
            high=df["high"], low=df["low"], close=df["close"], window=14
        )
        atr_14 = atr_indicator.average_true_range().iloc[-1]

        # ATR rápido (7 periodos) — reacciona más rápido a expansiones de volatilidad
        # Fix F: SL dinámico usa max(ATR7, ATR14)
        atr_fast = ta.volatility.AverageTrueRange(  # type: ignore[attr-defined]
            high=df["high"], low=df["low"], close=df["close"], window=7
        )
        atr_7 = atr_fast.average_true_range().iloc[-1]

        atr_actual = max(atr_7, atr_14)
        atr_pct = atr_actual / df["close"].iloc[-1] if df["close"].iloc[-1] != 0 else 0

        # Bollinger Bands width — mide contracción/expansión del mercado
        # BB_width alto = mercado expandiéndose (alta volatilidad)
        # BB_width bajo = mercado contrayéndose (baja volatilidad, posible breakout)
        try:
            bb = ta.volatility.BollingerBands(  # type: ignore[attr-defined]
                close=df["close"], window=bb_period, window_dev=bb_std
            )
            bb_upper = bb.bollinger_hband().iloc[-1]
            bb_lower = bb.bollinger_lband().iloc[-1]
            bb_mid = bb.bollinger_mavg().iloc[-1]
            bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid != 0 else 0
        except Exception:
            bb_width = 0

        # Volumen
        vol_ma5 = df["volume"].rolling(vol_fast).mean()
        vol_ma10 = df["volume"].rolling(vol_slow).mean()

        # EMA 20
        ema_20 = df["close"].ewm(span=20, adjust=False).mean()

        return {
            "RSI": rsi_series.iloc[-1],  # type: ignore[attr-defined]
            "ATR": atr_actual,
            "ATR_14": atr_14,
            "ATR_7": atr_7,
            "ATR_pct": atr_pct,
            "BB_width": bb_width,
            "vol_actual": df["volume"].iloc[-1],  # type: ignore[attr-defined]
            "vol_MA5": vol_ma5.iloc[-1],  # type: ignore[attr-defined]
            "vol_MA10": vol_ma10.iloc[-1],  # type: ignore[attr-defined]
            "EMA20": ema_20.iloc[-1],  # type: ignore[attr-defined]
            "high_actual": df["high"].iloc[-1],  # type: ignore[attr-defined]
            "low_actual": df["low"].iloc[-1],  # type: ignore[attr-defined]
            "open_actual": df["open"].iloc[-1],  # type: ignore[attr-defined]
            "close_actual": df["close"].iloc[-1],  # type: ignore[attr-defined]
            "high_prev": df["high"].iloc[-2],
            "low_prev": df["low"].iloc[-2],
            "open_prev": df["open"].iloc[-2],
            "close_prev": df["close"].iloc[-2],
        }

    # ─────────────────────────────────────
    #  PATRONES DE VELAS
    # ─────────────────────────────────────
    @staticmethod
    def is_pin_bar_bajista(ind: dict[str, Any]) -> bool:
        o, h, c, l = ind["open_actual"], ind["high_actual"], ind["close_actual"], ind["low_actual"]
        cuerpo = abs(c - o)
        sombra_sup = h - max(o, c)
        sombra_inf = min(o, c) - l
        if cuerpo == 0: return False
        return bool((sombra_sup >= cuerpo * 2) and (sombra_sup > sombra_inf) and (c < o))

    @staticmethod
    def is_pin_bar_alcista(ind: dict[str, Any]) -> bool:
        o, h, c, l = ind["open_actual"], ind["high_actual"], ind["close_actual"], ind["low_actual"]
        cuerpo = abs(c - o)
        sombra_sup = h - max(o, c)
        sombra_inf = min(o, c) - l
        if cuerpo == 0: return False
        return bool((sombra_inf >= cuerpo * 2) and (sombra_inf > sombra_sup) and (c > o))

    @staticmethod
    def is_engulfing_bajista(ind: dict[str, Any]) -> bool:
        o_act, c_act = ind["open_actual"], ind["close_actual"]
        o_prev, c_prev = ind["open_prev"], ind["close_prev"]
        if c_prev <= o_prev or c_act >= o_act: return False
        return bool((o_act >= c_prev) and (c_act <= o_prev))

    @staticmethod
    def is_engulfing_alcista(ind: dict[str, Any]) -> bool:
        o_act, c_act = ind["open_actual"], ind["close_actual"]
        o_prev, c_prev = ind["open_prev"], ind["close_prev"]
        if c_prev >= o_prev or c_act <= o_act: return False
        return bool((o_act <= c_prev) and (c_act >= o_prev))

    @staticmethod
    def is_shooting_star(ind: dict[str, Any]) -> bool:
        o, h, c, l = ind["open_actual"], ind["high_actual"], ind["close_actual"], ind["low_actual"]
        rango_total = h - l
        if rango_total == 0: return False
        cuerpo = abs(c - o)
        sombra_sup = h - max(o, c)
        return bool((sombra_sup >= rango_total * 0.6) and (cuerpo <= rango_total * 0.3))

    @staticmethod
    def is_martillo(ind: dict[str, Any]) -> bool:
        o, h, c, l = ind["open_actual"], ind["high_actual"], ind["close_actual"], ind["low_actual"]
        rango_total = h - l
        if rango_total == 0: return False
        cuerpo = abs(c - o)
        sombra_inf = min(o, c) - l
        return bool((sombra_inf >= rango_total * 0.6) and (cuerpo <= rango_total * 0.3))

    @classmethod
    def has_bearish_rejection(cls, ind: dict[str, Any]) -> bool:
        return cls.is_pin_bar_bajista(ind) or cls.is_engulfing_bajista(ind) or cls.is_shooting_star(ind)

    @classmethod
    def has_bullish_rejection(cls, ind: dict[str, Any]) -> bool:
        return cls.is_pin_bar_alcista(ind) or cls.is_engulfing_alcista(ind) or cls.is_martillo(ind)

    @staticmethod
    def is_market_ranging(ind_1h: dict[str, Any], ind_15m: Optional[dict[str, Any]] = None, config: Optional[dict[str, Any]] = None) -> bool:
        """Detecta si el mercado está lateral."""
        ma_distance = config.get("lateral_ma_distance", 0.008) if config else 0.008
        adx_min = config.get("lateral_adx_min", 18) if config else 18

        mas_cercanas = ind_1h.get("MA_spread", 0) < ma_distance
        adx = ind_1h.get("ADX", 0)
        adx_bajo = adx < adx_min
        rsi_neutral = False
        if ind_15m and "RSI" in ind_15m:
            rsi_val = ind_15m["RSI"]
            rsi_neutral = 42 < rsi_val < 58

        criterios = [mas_cercanas, adx_bajo, rsi_neutral]
        return bool(sum(criterios) >= 2)
