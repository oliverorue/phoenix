#!/usr/bin/env python3
"""
Script de línea de comandos para lanzar backtests.
Uso: python scripts/backtest.py --symbol XRPUSDT --start 2026-01-01 --end 2026-04-01 --capital 100 --fase 0
"""

import argparse
import json
import logging
from pathlib import Path
import sys

logging.getLogger("bot.core.strategy_engine").setLevel(logging.DEBUG)

# Añadir el directorio raíz al path
sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.core.backtester import BacktestEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")

def _load_instrument_cache(symbol: str) -> dict | None:
    """
    Intenta cargar instrument_cache desde un archivo JSON local
    para evitar llamadas a la API de Bybit durante el backtest.
    """
    cache_path = Path("data") / f"instrument_cache_{symbol}.json"
    if cache_path.exists():
        try:
            with open(cache_path) as f:
                return json.load(f)
        except Exception:
            pass
    return None

def _save_instrument_cache(symbol: str, cache: dict) -> None:
    """Guarda el instrument_cache localmente para usos futuros."""
    cache_path = Path("data") / f"instrument_cache_{symbol}.json"
    try:
        with open(cache_path, "w") as f:
            json.dump(cache, f, indent=2)
        logging.info(f"💾 Instrument cache guardado en {cache_path}")
    except Exception:
        pass

def main():
    parser = argparse.ArgumentParser(description="Backtesting Engine")
    parser.add_argument("--symbol", default="XRPUSDT", help="Par de trading")
    parser.add_argument("--start", required=True, help="Fecha inicio (YYYY-MM-DD)")
    parser.add_argument("--end", required=True, help="Fecha fin (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=100.0, help="Capital inicial")
    parser.add_argument("--fase", type=int, default=0, help="Fase de escalamiento (0-4)")
    parser.add_argument("--offline", action="store_true", help="Modo offline (sin llamadas API)")
    args = parser.parse_args()

    print("=" * 60)
    mode_tag = " [OFFLINE]" if args.offline else ""
    print(f" BACKTEST{mode_tag} {args.symbol} | {args.start} → {args.end} | Capital: ${args.capital} | Fase: {args.fase}")
    print("=" * 60)

    # Cargar cache de instrumento si existe
    instrument_cache = _load_instrument_cache(args.symbol) if args.offline else None

    engine = BacktestEngine(
        symbol=args.symbol,
        fase_num=args.fase,
        instrument_cache=instrument_cache,
    )

    # Si no es offline y se obtuvo cache de la API, guardarlo para próximas ejecuciones
    if not args.offline and not instrument_cache:
        if hasattr(engine.data_feed, '_instrument_cache') and engine.data_feed._instrument_cache:
            _save_instrument_cache(args.symbol, engine.data_feed._instrument_cache)

    result = engine.run(args.start, args.end, args.capital)

    print("\nResultados:")
    for key, value in result.summary().items():
        print(f"  {key}: {value}")

    if result.total_trades > 0:
        # Guardar CSV
        csv_path = Path(f"logs/backtest_{args.symbol}_{args.start}_{args.end}.csv")
        csv_path.parent.mkdir(exist_ok=True)
        result.to_dataframe().to_csv(csv_path, index=False)
        print(f"\nTrades guardados en: {csv_path}")
    else:
        print("\nNo se generaron trades en el período.")


if __name__ == "__main__":
    main()