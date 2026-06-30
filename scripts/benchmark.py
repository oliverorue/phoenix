#!/usr/bin/env python3
"""
Benchmarks de rendimiento para el trading bot.
Mide tiempos de ejecución de los componentes críticos.
"""
import time
import statistics
import pandas as pd
import numpy as np
from bot.core.indicators import Indicators
from bot.core.strategy_engine import ScalpingStrategy
from bot.core.risk_manager import RiskManager
from unittest.mock import MagicMock, AsyncMock


def _make_df(n_rows: int = 100, close_val: float = 0.50):
    closes = close_val * (1 + np.random.randn(n_rows) * 0.005)
    closes = np.maximum(closes, close_val * 0.95)
    closes[0] = close_val
    return pd.DataFrame({
        "timestamp": pd.date_range(end="now", periods=n_rows, freq="15min"),
        "open": closes + np.random.randn(n_rows) * 0.001,
        "high": np.maximum(closes, closes) * (1 + np.abs(np.random.randn(n_rows)) * 0.002),
        "low": np.minimum(closes, closes) * (1 - np.abs(np.random.randn(n_rows)) * 0.002),
        "close": closes,
        "volume": np.random.rand(n_rows) * 10000,
    })


def benchmark_indicators(iterations: int = 1000):
    df_15m = _make_df(100)
    df_1h = _make_df(100)
    config = {
        "ma_periods": [5, 10, 20],
        "rsi_period": 14,
        "vol_ma_fast": 5,
        "vol_ma_slow": 10,
    }

    times_15m = []
    times_1h = []

    for _ in range(iterations):
        t0 = time.perf_counter()
        Indicators.calc_15m_indicators(df_15m, config)
        times_15m.append(time.perf_counter() - t0)

        t0 = time.perf_counter()
        Indicators.calc_1h_indicators(df_1h, config)
        times_1h.append(time.perf_counter() - t0)

    print(f"\n📊 Indicadores ({iterations} iteraciones):")
    print(f"   15m: media={statistics.mean(times_15m)*1000:.2f}ms, "
          f"mediana={statistics.median(times_15m)*1000:.2f}ms, "
          f"p99={sorted(times_15m)[int(iterations*0.99)]*1000:.2f}ms")
    print(f"   1h:  media={statistics.mean(times_1h)*1000:.2f}ms, "
          f"mediana={statistics.median(times_1h)*1000:.2f}ms, "
          f"p99={sorted(times_1h)[int(iterations*0.99)]*1000:.2f}ms")


def benchmark_strategy(iterations: int = 1000):
    df_15m = _make_df(100, 0.50)
    df_1h = _make_df(100, 0.51)
    strategy = ScalpingStrategy()
    balance = 200.0
    current_price = df_15m["close"].iloc[-1]

    times = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        strategy.evaluate(df_1h, df_15m, current_price, balance)
        times.append(time.perf_counter() - t0)

    print(f"\n📊 Estrategia ({iterations} iteraciones):")
    print(f"   media={statistics.mean(times)*1000:.2f}ms, "
          f"mediana={statistics.median(times)*1000:.2f}ms, "
          f"p99={sorted(times)[int(iterations*0.99)]*1000:.2f}ms, "
          f"min={min(times)*1000:.2f}ms, max={max(times)*1000:.2f}ms")


def benchmark_risk_manager(iterations: int = 500):
    feed = MagicMock()
    feed.get_balance = AsyncMock(return_value=200.0)
    feed.get_instrument_info = AsyncMock(return_value={
        "qtyStep": 0.1, "minOrderQty": 1.0, "maxOrderQty": 500000.0,
        "minNotionalValue": 5.0, "maxNotionalValue": 1000000.0,
    })
    rm = RiskManager(feed)

    times_size = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        import asyncio
        asyncio.run(rm.calculate_position_size(0.50, 0.495))
        times_size.append(time.perf_counter() - t0)

    print(f"\n📊 RiskManager.calculate_position_size ({iterations} iteraciones):")
    print(f"   media={statistics.mean(times_size)*1000:.2f}ms, "
          f"mediana={statistics.median(times_size)*1000:.2f}ms")


def main():
    print("=" * 60)
    print("   🔬 BENCHMARKS - Trading Bot")
    print("=" * 60)

    benchmark_indicators(500)
    benchmark_strategy(500)
    benchmark_risk_manager(100)

    print("\n" + "=" * 60)
    print("   ✅ Benchmarks completados")
    print("=" * 60)


if __name__ == "__main__":
    main()
