#!/usr/bin/env python3
"""
Diagnóstico de la estrategia: analiza cada vela 15m y registra
qué checkpoint está bloqueando la generación de señales.

Uso:
  python3 scripts/diagnose_strategy.py --symbol XRPUSDT --start 2026-04-01 --end 2026-04-15
"""
import argparse
import sys
import os
from collections import Counter
import pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bot.core.strategy_engine import ScalpingStrategy
from bot.core.indicators import Indicators
from config.loader import get_phase_config


def diagnose(df_15m, df_1h, strategy, fase_cfg):
    """Evalúa cada vela 15m y registra el punto de fallo."""
    results = {
        "total_velas": 0,
        "senales_emitidas": 0,
        "fallos": Counter(),
        "detalles": [],
        "scores_no_cero": [],
    }

    indicators = Indicators()

    for i in range(60, len(df_15m)):  # warmup 60 velas
        results["total_velas"] += 1

        # Slice de datos como en el backtest
        df_15m_slice = df_15m.iloc[: i + 1]
        df_1h_slice = df_1h[df_1h.index <= df_15m.index[i]]

        if len(df_1h_slice) < 14:
            results["fallos"]["1h_insuficiente"] += 1
            continue

        current_price = float(df_15m_slice["close"].iloc[-1])
        balance = 50.0  # Fase 0

        # --- Check 1: Fase config ---
        _, fc = get_phase_config(balance)
        if fc is None:
            results["fallos"]["sin_fase_config"] += 1
            continue

        # --- Check 2: 1h data ---
        if len(df_1h_slice) < 14:
            results["fallos"]["1h_muy_corto"] += 1
            continue

        ind_1h = indicators.calc_1h_indicators(df_1h_slice)
        adx_1h = ind_1h.get("ADX", 0)

        # --- Check 3: 15m indicators ---
        ind_15m = indicators.calc_15m_indicators(df_15m_slice)
        atr = ind_15m.get("ATR", 0)
        atr_pct = ind_15m.get("ATR_pct", 0)

        if atr <= 0:
            results["fallos"]["atr_invalido"] += 1
            continue

        # --- Check 4: Volatilidad ---
        max_vol = fc.get("max_volatility_pct", 20.0)
        if atr_pct * 100 > max_vol:
            results["fallos"]["volatilidad_alta"] += 1
            continue

        # --- Check 5: ADX mínimo ---
        if adx_1h < strategy.ADX_MIN_ABSOLUTE:
            results["fallos"]["adx_bajo"] += 1
            continue

        # --- Check 6: Dirección (EMA50) ---
        ema50_1h = df_1h_slice["close"].ewm(span=strategy.MA_TREND, adjust=False).mean().iloc[-1]

        if current_price > ema50_1h:
            direction = "LONG"
        elif current_price < ema50_1h:
            direction = "SHORT"
        else:
            results["fallos"]["sin_direccion"] += 1
            continue

        # --- Check 7: Tendencia consistente ---
        if not strategy._check_trend_consistency(df_1h_slice, ema50_1h, direction):
            results["fallos"]["tendencia_inconsistente"] += 1
            continue

        # --- Check 8: Mercado lateral ---
        if Indicators.is_market_ranging(ind_1h, ind_15m, fc):
            results["fallos"]["mercado_lateral"] += 1
            continue

        # --- Check 9: HH/LL ---
        hhll_ok = strategy._check_market_structure_hhll(df_1h_slice, direction)

        # --- Check 10: Pullback ---
        ema20_15m = ind_15m.get("EMA20", df_15m_slice["close"].iloc[-1])
        pb_distance = strategy._calc_pullback(current_price, ema20_15m, df_15m_slice, direction)
        if pb_distance is None or pb_distance > strategy.PULLBACK_ZONE_PCT * 2:
            results["fallos"]["pullback_fuera_zona"] += 1
            continue

        # --- Check 11: Momentum ---
        if not strategy._check_momentum_confirmation(df_15m_slice, direction):
            results["fallos"]["momentum"] += 1
            continue

        # --- Check 12: Cooldown ---
        if not strategy._check_cooldown(direction):
            results["fallos"]["cooldown"] += 1
            # Aún avanzamos el tick
            strategy.tick_cooldown()
            continue

        # --- Check 13: Scoring ---
        rsi = ind_15m.get("RSI", 50)
        rsi_min = fc.get("rsi_min_long", 25) if direction == "LONG" else fc.get("rsi_min_short", 65)
        rsi_max = fc.get("rsi_max_long", 35) if direction == "LONG" else fc.get("rsi_max_short", 75)

        score, reasons = strategy._calc_score(
            direction, current_price, ema50_1h, adx_1h,
            ema20_15m, pb_distance, rsi, rsi_min, rsi_max,
            ind_15m, fc, hhll_ok
        )

        if score <= 0:
            results["fallos"]["score_cero"] += 1
            strategy.tick_cooldown()
            continue

        if score < strategy.MIN_SCORE:
            results["fallos"][f"score_bajo_{score}"] += 1
            strategy.tick_cooldown()
            continue

        # --- ¡Señal emitida! ---
        results["senales_emitidas"] += 1
        results["detalles"].append({
            "time": df_15m.index[i],
            "direction": direction,
            "score": score,
            "reasons": " | ".join(reasons),
        })

        # Aplicar cooldown
        strategy._apply_cooldown(direction)

        results["scores_no_cero"].append(score)

        # Avanzar cooldown después de emitir
        strategy.tick_cooldown()

    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="XRPUSDT")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    args = parser.parse_args()

    # Cargar datos
    f_15m = f"data/{args.symbol}_15_{args.start}_{args.end}.csv"
    f_1h = f"data/{args.symbol}_60_{args.start}_{args.end}.csv"

    if not os.path.exists(f_15m):
        print(f"ERROR: No existe {f_15m}")
        sys.exit(1)
    if not os.path.exists(f_1h):
        print(f"ERROR: No existe {f_1h}")
        sys.exit(1)

    df_15m = pd.read_csv(f_15m, index_col=0, parse_dates=True)
    df_1h = pd.read_csv(f_1h, index_col=0, parse_dates=True)

    print(f"Datos 15m: {len(df_15m)} velas ({df_15m.index[0]} → {df_15m.index[-1]})")
    print(f"Datos 1h:  {len(df_1h)} velas ({df_1h.index[0]} → {df_1h.index[-1]})")

    strategy = ScalpingStrategy()
    fase_cfg = get_phase_config(50.0)

    results = diagnose(df_15m, df_1h, strategy, fase_cfg)

    print(f"\n{'='*60}")
    print("RESULTADOS DEL DIAGNÓSTICO")
    print(f"{'='*60}")
    print(f"Total velas evaluadas:  {results['total_velas']}")
    print(f"Señales emitidas:       {results['senales_emitidas']}")
    print(f"Tasa de señales:        {results['senales_emitidas']/results['total_velas']*100:.1f}%")
    print(f"\n--- FALLOS POR CHECKPOINT ---")
    for reason, count in results["fallos"].most_common():
        pct = count / results["total_velas"] * 100
        print(f"  {reason:35s} {count:5d}  ({pct:4.1f}%)")

    if results["detalles"]:
        print(f"\n--- SEÑALES EMITIDAS ({len(results['detalles'])}) ---")
        for d in results["detalles"][:20]:
            print(f"  {d['time']}  {d['direction']:6s}  score={d['score']:3d}  {d['reasons']}")

    if results["scores_no_cero"]:
        scores = results["scores_no_cero"]
        print(f"\n--- DISTRIBUCIÓN DE SCORES > 0 ---")
        print(f"  count={len(scores)}, min={min(scores)}, max={max(scores)}, avg={sum(scores)/len(scores):.1f}")
        buckets = Counter()
        for s in scores:
            bucket = (s // 10) * 10
            buckets[f"{bucket}-{bucket+9}"] += 1
        for b in sorted(buckets.keys()):
            print(f"  {b}: {buckets[b]}")


if __name__ == "__main__":
    main()
