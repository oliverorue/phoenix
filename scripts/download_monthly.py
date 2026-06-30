#!/usr/bin/env python3
"""
Descarga datos históricos mes por mes con timeout forzado.
Combina CSVs existentes con nuevas descargas para cubrir el rango completo.
Uso: python3 scripts/download_monthly.py --symbol XRPUSDT --start 2026-01-01 --end 2026-05-01
"""
import argparse
import signal
import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import time

sys.path.insert(0, str(Path(__file__).parent.parent))

from bot.core.backtester import BacktestEngine


class TimeoutError(Exception):
    pass


def handler(signum, frame):
    raise TimeoutError("Timeout: la descarga tardó >60s")


def download_month(engine, symbol, start, end, interval, cache_file):
    """Descarga un mes con timeout de 60s."""
    if cache_file.exists():
        print(f"  📦 Ya cacheado: {cache_file.name}")
        return pd.read_csv(cache_file)

    print(f"  📥 Descargando {interval} {start} → {end}...", end=" ", flush=True)
    signal.signal(signal.SIGALRM, handler)
    signal.alarm(60)
    try:
        df = engine.fetch_history(start, end, interval)
        signal.alarm(0)
        if not df.empty:
            df.to_csv(cache_file, index=False)
            print(f"OK ({len(df)} velas)")
        else:
            print("VACÍO")
        return df
    except TimeoutError:
        print("TIMEOUT — saltando")
        return pd.DataFrame()
    except Exception as e:
        signal.alarm(0)
        print(f"ERROR: {e}")
        return pd.DataFrame()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="XRPUSDT")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    symbol = args.symbol
    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end, "%Y-%m-%d")

    engine = BacktestEngine(symbol=symbol, fase_num=0)

    for interval in ["60", "15"]:
        print(f"\n{'='*50}")
        print(f"  Intervalo: {interval}")
        print(f"{'='*50}")

        all_dfs = []
        current = start_dt
        while current < end_dt:
            # Mes siguiente (o fin del rango)
            if current.month == 12:
                next_month = current.replace(year=current.year + 1, month=1, day=1)
            else:
                next_month = current.replace(month=current.month + 1, day=1)
            chunk_end = min(next_month, end_dt)

            chunk_start_str = current.strftime("%Y-%m-%d")
            chunk_end_str = chunk_end.strftime("%Y-%m-%d")

            cache_file = Path("data") / f"{symbol}_{interval}_{chunk_start_str}_{chunk_end_str}.csv"

            df = download_month(engine, symbol, chunk_start_str, chunk_end_str, interval, cache_file)
            if not df.empty:
                df["timestamp"] = pd.to_datetime(df["timestamp"])
                all_dfs.append(df)

            current = chunk_end
            time.sleep(0.5)  # rate limit entre meses

        if all_dfs:
            combined = pd.concat(all_dfs, ignore_index=True)
            combined.sort_values("timestamp", inplace=True)
            combined.drop_duplicates("timestamp", inplace=True)
            combined.reset_index(drop=True, inplace=True)

            final_path = Path("data") / f"{symbol}_{interval}_{args.start}_{args.end}.csv"
            combined.to_csv(final_path, index=False)
            print(f"\n✅ Combinado: {final_path} ({len(combined)} velas)")
        else:
            print(f"\n⚠️  Sin datos para {interval}")


if __name__ == "__main__":
    main()
