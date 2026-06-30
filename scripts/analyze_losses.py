#!/usr/bin/env python3
"""Clasifica cada pérdida en 3 categorías según MFE y close_reason."""
import pandas as pd
import sys
import os

CSV = sys.argv[1] if len(sys.argv) > 1 else "logs/backtest_XRPUSDT_2026-04-01_2026-05-01.csv"

if not os.path.exists(CSV):
    print(f"ERROR: {CSV} no existe")
    sys.exit(1)

df = pd.read_csv(CSV)
losses = df[df["pnl_usdt"] < 0].copy()

print(f"Total trades: {len(df)}")
print(f"Pérdidas: {len(losses)} ({len(losses)/len(df)*100:.1f}%)")
print()

# Contadores
cats = {"REVERSIÓN": 0, "FALLO INMEDIATO": 0, "PULLBACK FALSO": 0, "OTRO": 0}
pnl_por_cat = {"REVERSIÓN": 0.0, "FALLO INMEDIATO": 0.0, "PULLBACK FALSO": 0.0, "OTRO": 0.0}

for _, row in losses.iterrows():
    mfe = row["mfe_pct"]
    mae = row["mae_pct"]
    reason = row["close_reason"]
    
    if reason == "stop_loss" and mfe > 30:
        cat = "REVERSIÓN"
        desc = "Se movió +30% a favor y luego revirtió a SL"
    elif reason == "stop_loss" and mfe < 10:
        cat = "FALLO INMEDIATO"
        desc = "Nunca se movió a favor"
    elif reason == "stop_loss":
        cat = "PULLBACK FALSO"
        desc = "Retroceso que no rebotó"
    else:
        cat = "OTRO"
        desc = f"{reason}"
    
    cats[cat] += 1
    pnl_por_cat[cat] += row["pnl_usdt"]
    
    print(f"  {row['entry_time']:19s} | {row['side']:5s} | MFE={mfe:5.1f}% | MAE={mae:5.1f}% | {cat:20s} | {desc}")

print()
print("=" * 70)
print("RESUMEN DE CATEGORÍAS")
print("=" * 70)
total = sum(cats.values())
for cat in ["REVERSIÓN", "PULLBACK FALSO", "FALLO INMEDIATO", "OTRO"]:
    n = cats[cat]
    pct = n / max(total, 1) * 100
    pnl = pnl_por_cat[cat]
    print(f"  {cat:20s}: {n:3d} pérdidas ({pct:4.1f}%) → PnL ${pnl:+.2f}")
print(f"  {'TOTAL':20s}: {total:3d} pérdidas → PnL ${sum(pnl_por_cat.values()):+.2f}")
