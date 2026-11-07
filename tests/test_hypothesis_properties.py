"""Property-based tests con Hypothesis para funciones puras del bot.

Estrategias de generación:
- `floats` para precios, volúmenes, qty steps
- `text` para mensajes kline
- `data_frames` built from lists para indicadores

Cada test define invariantes que deben cumplirse para TODA entrada válida.
"""

import pandas as pd
import numpy as np
from hypothesis import given, assume, strategies as st, settings


# ── Estrategias reutilizables ──────────────────────────────────────────────

# qty_step: valores que aparecen en exchanges reales
qty_steps = st.floats(
    min_value=0, max_value=1000, allow_nan=False, allow_infinity=False,
).filter(lambda x: x >= 0)

# Precios OHLCV: valores positivos razonables
prices = st.floats(
    min_value=0.0001, max_value=100_000, allow_nan=False, allow_infinity=False,
)

# Mensajes kline válidos (parciales, para probar parseo)
valid_kline_topics = st.sampled_from([
    "kline.15.XRPUSDT", "kline.60.BTCUSDT", "kline.5.ETHUSDT",
])

# Mensajes inválidos para parseo
invalid_messages = st.sampled_from([
    {},
    {"topic": "orderbook.XRPUSDT", "data": [{"start": "1"}]},
    {"topic": "kline.15.XRPUSDT"},
    {"topic": "kline.15.XRPUSDT", "data": []},
    {"topic": "", "data": [{"start": "1"}]},
])

# Estrategia para generar OHLCV DataFrames sintéticos
@st.composite
def ohlcv_dataframes(draw, min_rows=3, max_rows=200):
    """Genera un DataFrame con columnas OHLCV y timestamp.

    Usa `st.lists()` combinado con Hypothesis para que el RNG
    esté bajo control de Hypothesis, permitiendo reproducción de fallos.
    NO usar numpy RNG con seed fija dentro de @st.composite,
    pues anularía la variabilidad del PBT.
    """
    n = draw(st.integers(min_value=min_rows, max_value=max_rows))
    base_price = draw(prices)
    dates = pd.date_range("2026-01-01", periods=n, freq="15min")

    # Generar cada columna como lista de floats vía st.listas anidadas
    # para que Hypothesis controle cada valor individualmente
    closes = draw(st.lists(
        st.floats(min_value=base_price * 0.95, max_value=base_price * 1.05,
                  allow_nan=False, allow_infinity=False),
        min_size=n, max_size=n,
    ))
    opens = np.roll(closes, 1).tolist()
    opens[0] = closes[0]
    highs = closes * (1 + np.array(draw(st.lists(
        st.floats(min_value=0.0, max_value=0.02, allow_nan=False, allow_infinity=False),
        min_size=n, max_size=n,
    ))))
    lows = closes * (1 - np.array(draw(st.lists(
        st.floats(min_value=0.0, max_value=0.02, allow_nan=False, allow_infinity=False),
        min_size=n, max_size=n,
    ))))
    volumes = draw(st.lists(
        st.floats(min_value=100, max_value=10_000, allow_nan=False, allow_infinity=False),
        min_size=n, max_size=n,
    ))

    return pd.DataFrame({
        "timestamp": dates,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": volumes,
    })


# ══════════════════════════════════════════════════════════════════════════
# 1. decimal_places_for_qty_step
# ══════════════════════════════════════════════════════════════════════════

class TestDecimalPlacesProperty:
    """Propiedades de decimal_places_for_qty_step (función pura)."""

    def _func(self, x):
        from bot.core.data_feed import decimal_places_for_qty_step
        return decimal_places_for_qty_step(x)

    @given(qty_steps)
    @settings(deadline=None)
    def test_result_is_nonnegative_int(self, qty_step):
        """Siempre retorna un entero >= 0 y <= 12."""
        result = self._func(qty_step)
        assert isinstance(result, int)
        assert 0 <= result <= 12

    @given(st.floats(max_value=0, allow_nan=False, allow_infinity=False))
    def test_non_positive_returns_8(self, qty_step):
        """qty_step <= 0 → 8 decimales."""
        assume(qty_step <= 0)
        assert self._func(qty_step) == 8

    @given(
        st.floats(min_value=1, max_value=1e6, allow_nan=False, allow_infinity=False)
        .filter(lambda x: abs(x - round(x)) < 1e-12)
    )
    def test_integer_step_returns_0(self, qty_step):
        """qty_step entero (1.0, 10.0, 100.0) → 0 decimales."""
        assume(qty_step > 0)
        assume(abs(qty_step - round(qty_step)) < 1e-12)
        assert self._func(qty_step) == 0

    @given(
        st.floats(
            min_value=1e-10, max_value=0.9999,
            allow_nan=False, allow_infinity=False,
        ).filter(lambda x: x > 0 and abs(x - round(x)) >= 1e-12)
    )
    def test_fractional_step_returns_positive(self, qty_step):
        """qty_step fraccional (>0, no entero) → al menos 1 decimal."""
        assume(qty_step > 0)
        assume(abs(qty_step - round(qty_step)) >= 1e-12)
        result = self._func(qty_step)
        assert result >= 1

    @given(st.floats(min_value=0.001, max_value=0.001, allow_nan=False, allow_infinity=False))
    def test_known_qty_step_001(self, qty_step):
        """Caso conocido: 0.001 → 3 decimales."""
        assert self._func(qty_step) == 3


# ══════════════════════════════════════════════════════════════════════════
# 3. RateLimiter — propiedades del Token Bucket
# ══════════════════════════════════════════════════════════════════════════

class TestRateLimiterProperty:
    """Propiedades del algoritmo Token Bucket."""

    def _make_limiter(self, tps=10, max_tokens=20):
        from bot.core.rate_limiter import RateLimiter
        return RateLimiter(tokens_per_second=tps, max_tokens=max_tokens)

    @given(
        tps=st.floats(0.1, 100, allow_nan=False, allow_infinity=False),
        max_tokens=st.floats(1, 1000, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_tokens_never_exceed_max(self, tps, max_tokens):
        """_tokens nunca supera _max_tokens, incluso tras refill prolongado."""
        limiter = self._make_limiter(tps=tps, max_tokens=max_tokens)
        # Simular refill después de mucho tiempo
        import time
        limiter._last_refill = time.monotonic() - 3600  # 1 hora
        limiter._refill()
        assert limiter._tokens <= limiter._max_tokens

    @given(
        tps=st.floats(0.1, 100, allow_nan=False, allow_infinity=False),
        max_tokens=st.floats(1, 100, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_tokens_never_negative(self, tps, max_tokens):
        """_consume nunca deja _tokens negativo."""
        limiter = self._make_limiter(tps=tps, max_tokens=max_tokens)
        # Consumir muchos tokens forzadamente
        for _ in range(1000):
            limiter._consume()
            assert limiter._tokens >= 0

    @given(
        tps=st.floats(0.1, 10, allow_nan=False, allow_infinity=False),
        max_tokens=st.floats(1, 10, allow_nan=False, allow_infinity=False),
    )
    @settings(max_examples=50)
    def test_consume_returns_false_when_depleted(self, tps, max_tokens):
        """Sin tokens, _consume retorna False."""
        limiter = self._make_limiter(tps=tps, max_tokens=max_tokens)
        # Forzar tokens a 0
        limiter._tokens = 0.0
        assert not limiter._consume()


# ══════════════════════════════════════════════════════════════════════════
# 4. Indicadores — propiedades invariantes
# ══════════════════════════════════════════════════════════════════════════

class TestIndicatorsProperty:
    """Propiedades invariantes de los cálculos de indicadores."""

    @given(ohlcv_dataframes(min_rows=20, max_rows=100))
    @settings(max_examples=30)
    def test_rsi_between_0_and_100(self, df):
        """RSI siempre está entre 0 y 100."""
        from bot.core.indicators import Indicators
        ind = Indicators.calc_15m_indicators(df)
        rsi = ind.get("RSI", None)
        if rsi is not None:
            assert 0 <= rsi <= 100

    @given(ohlcv_dataframes(min_rows=20, max_rows=100))
    @settings(max_examples=30)
    def test_ma_values_are_finite(self, df):
        """MAs son valores finitos (no NaN, no inf)."""
        from bot.core.indicators import Indicators
        ind = Indicators.calc_1h_indicators(df)
        for key, val in ind.items():
            if key.startswith("MA") and isinstance(val, (int, float)):
                assert np.isfinite(val), f"{key}={val} no es finito"

    @given(ohlcv_dataframes(min_rows=20, max_rows=100))
    @settings(max_examples=30)
    def test_adx_is_nonnegative(self, df):
        """ADX nunca es negativo."""
        from bot.core.indicators import Indicators
        ind = Indicators.calc_1h_indicators(df)
        assert ind.get("ADX", 0) >= 0
