#!/usr/bin/env python3
"""Test rapido de puntuacion de estrategia."""
import asyncio
import logging
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.core.data_feed import DataFeed
from bot.core.indicators import Indicators
from bot.core.strategy_engine import ScalpingStrategy
from config.loader import SYMBOL

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)


async def main() -> None:
    feed = DataFeed(SYMBOL)
    df_1h = await feed.current_1h()
    df_15m = await feed.current_15m()
    if df_1h.empty or df_15m.empty:
        print("No hay datos")
        sys.exit(1)

    strategy = ScalpingStrategy()
    for _ in range(20):
        i = random.randint(30, len(df_15m) - 2)
        price = df_15m.iloc[i]["close"]
        # ventanas (ignoramos el precalculo, usamos las ultimas velas)
        window_15 = df_15m.iloc[i - 29 : i + 1]  # 30 velas para indicadores
        ts = df_15m.iloc[i]["timestamp"]
        window_1h = df_1h[df_1h["timestamp"] <= ts].tail(30)
        if len(window_1h) < 10:
            continue
        _ = Indicators.calc_1h_indicators(window_1h)  # type: ignore[arg-type]
        _ = Indicators.calc_15m_indicators(window_15)  # type: ignore[arg-type]
        signal = strategy.evaluate(window_1h, window_15, price, 100)  # type: ignore[arg-type]
        logger.info(
            f"Vela {ts} | Precio {price:.4f} | Dir={signal.direction} "
            f"Score={signal.score} {signal.reason}"
        )


if __name__ == "__main__":
    asyncio.run(main())
