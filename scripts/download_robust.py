#!/usr/bin/env python3
"""
Descarga datos históricos de Binance Futures en chunks pequeños con reintentos.
Ligero — no usa BacktestEngine ni dependencias internas del bot.

Uso:
  python3 scripts/download_robust.py --symbol XRPUSDT --start 2026-01-01 --end 2026-05-01
"""

import argparse
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

import pandas as pd
from binance.client import Client

# ── Config ──────────────────────────────────────────────────
CHUNK_DAYS = 7          # descargar de a 7 días (máx ~200 velas 15m o ~168 velas 1h por chunk)
MAX_RETRIES = 3          # reintentos por chunk
RETRY_DELAY = 2.0        # segundos entre reintentos
REQUEST_DELAY = 0.5      # segundos entre requests de paginación
TIMEOUT = 45             # timeout HTTP (segundos)

def ms_to_dt(ms: int) -> datetime:
    return datetime.utcfromtimestamp(ms / 1000)

def dt_to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)

def download_chunk(client: Client, symbol: str, interval: str,
                   start_dt: datetime, end_dt: datetime) -> pd.DataFrame:
    """
    Descarga un chunk de datos usando paginación hacia atrás desde end_dt.
    Retorna DataFrame o vacío si falla tras reintentos.
    """
    start_ms = dt_to_ms(start_dt)
    end_ms = dt_to_ms(end_dt)
    collected = []
    cursor = end_ms

    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        collected.clear()
        cursor = end_ms
        try:
            while True:
                candles = client.futures_klines(
                    symbol=symbol,
                    interval=interval,
                    limit=200,
                    endTime=cursor,
                )
                if not candles:
                    break
                added = 0
                for c in candles:
                    ts = int(c[0])
                    if ts >= start_ms:
                        collected.append([c[0], c[1], c[2], c[3], c[4], c[5], c[7]])
                        added += 1
                oldest = int(candles[-1][0])
                if oldest <= start_ms or added == 0:
                    break
                cursor = oldest - 1
                time.sleep(REQUEST_DELAY)

            if collected:
                df = pd.DataFrame(
                    collected,
                    columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"],
                )
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = df[col].astype(float)
                df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
                df.sort_values("timestamp", inplace=True)
                df.drop_duplicates("timestamp", inplace=True)
                df.reset_index(drop=True, inplace=True)
                return df
            else:
                # Sin datos — puede ser fin de semana o vacío real
                return pd.DataFrame()

        except Exception as e:
            last_exc = e
            print(f"    ⚠️  Intento {attempt}/{MAX_RETRIES} falló: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)
            else:
                print(f"    ❌ Agotados reintentos para {start_dt.date()} → {end_dt.date()}")
                return pd.DataFrame()

    # Nunca debería llegar aquí, pero por si acaso
    return pd.DataFrame()


def merge_csvs(cache_dir: Path, symbol: str, interval: str,
               start_str: str, end_str: str) -> Path:
    """Combina CSVs de chunks individuales en un archivo final."""
    final_path = cache_dir / f"{symbol}_{interval}_{start_str}_{end_str}.csv"
    parts = sorted(cache_dir.glob(f"{symbol}_{interval}_*.csv"))

    # Filtrar: solo los que están en el rango y NO son el final
    dfs = []
    for p in parts:
        if p == final_path:
            continue
        try:
            df = pd.read_csv(p)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            dfs.append(df)
        except Exception:
            pass

    if not dfs:
        return final_path

    combined = pd.concat(dfs, ignore_index=True)
    combined.sort_values("timestamp", inplace=True)
    combined.drop_duplicates("timestamp", inplace=True)
    combined.reset_index(drop=True, inplace=True)
    combined.to_csv(final_path, index=False)
    return final_path


def main():
    parser = argparse.ArgumentParser(description="Descarga robusta de datos Binance Futures")
    parser.add_argument("--symbol", default="XRPUSDT")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--chunk-days", type=int, default=CHUNK_DAYS)
    parser.add_argument("--intervals", nargs="+", default=["60", "15"])
    args = parser.parse_args()

    symbol = args.symbol
    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt = datetime.strptime(args.end, "%Y-%m-%d")
    cache_dir = Path("data")
    cache_dir.mkdir(exist_ok=True)

    client = Client(requests_params={"timeout": TIMEOUT})

    # Verificar conectividad
    try:
        client.ping()
        print("✅ Conectado a Binance Futures")
    except Exception as e:
        print(f"❌ No se pudo conectar a Binance: {e}")
        sys.exit(1)

    for interval in args.intervals:
        # Mapear al formato que Binance espera
        binance_interval = {"15": "15m", "60": "1h", "5": "5m", "30": "30m"}.get(interval, interval)
        print(f"\n{'='*60}")
        print(f"  Descargando {binance_interval} | {args.start} → {args.end}")
        print(f"{'='*60}")

        chunk = timedelta(days=args.chunk_days)
        current = start_dt
        total_velas = 0

        while current < end_dt:
            chunk_end = min(current + chunk, end_dt)
            chunk_start_str = current.strftime("%Y-%m-%d")
            chunk_end_str = chunk_end.strftime("%Y-%m-%d")
            cache_file = cache_dir / f"{symbol}_{interval}_{chunk_start_str}_{chunk_end_str}.csv"

            if cache_file.exists():
                try:
                    df = pd.read_csv(cache_file)
                    n = len(df)
                    total_velas += n
                    print(f"  📦 {chunk_start_str} → {chunk_end_str}: {n} velas (cache)")
                    current = chunk_end
                    continue
                except Exception:
                    cache_file.unlink(missing_ok=True)

            print(f"  📥 {chunk_start_str} → {chunk_end_str}...", end=" ", flush=True)
            df = download_chunk(client, symbol, str(binance_interval), current, chunk_end)

            if not df.empty:
                df.to_csv(cache_file, index=False)
                n = len(df)
                total_velas += n
                print(f"{n} velas ✅")
            else:
                # Crear archivo vacío para no reintentar
                cache_file.touch()
                print("0 velas (vacío)")

            current = chunk_end
            time.sleep(0.3)  # rate limit entre chunks

        # Combinar en archivo final
        final_path = merge_csvs(cache_dir, symbol, interval, args.start, args.end)
        final_df = pd.read_csv(final_path)
        print(f"\n  ✅ FINAL: {final_path.name} → {len(final_df)} velas totales")

    print(f"\n{'='*60}")
    print("  ✅ Descarga completa")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
