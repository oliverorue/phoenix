"""
Optimizador de parámetros de estrategia (v3.0 para Trading Bot v6.0).
Incluye walk-forward testing y análisis de sensibilidad con heatmap.

Uso desde CLI:
    python -m bot.core.optimizer --symbol XRPUSDT --start 2026-04-01 --end 2026-04-15 --capital 100 --fase 0

El resultado se guarda en logs/optimizer_<SYMBOL>_<fecha>.csv

Fixes:
  #4 Walk-forward testing: 80% in-sample + 20% out-of-sample
  #8 Análisis de sensibilidad: heatmap 2D + detección de overfitting
"""

import itertools
import sys
from pathlib import Path
from typing import Any, Optional
import threading
import json

import pandas as pd
import numpy as np
from loguru import logger

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from bot.core.backtester import BacktestEngine


PARAM_GRID: dict[str, list[Any]] = {
    "risk_per_trade":      [0.008, 0.012],
    "max_sl_pct":          [0.025],
    "tp1_rr":              [1.5, 2.0],
    "tp2_rr":              [2.5, 3.0],
    "breakeven_trigger":   [0.10, 0.15],
    "trailing_dist":       [0.0012],
}

# Fix #4: Split out-of-sample
OOS_SPLIT = 0.20  # 20% para out-of-sample

DEFAULT_MIN_TRADES = 5


def _all_combinations(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    keys = list(grid.keys())
    values = list(grid.values())
    return [dict(zip(keys, combo)) for combo in itertools.product(*values)]


# ── Fix #4: Walk-Forward Testing ──────────────────────────────

def _walk_forward_split(total_dates: list[str], oos_pct: float = OOS_SPLIT) -> tuple[str, str, str, str]:
    """
    Divide un rango de fechas ordenado en in-sample (80%) y out-of-sample (20%).
    Retorna (is_start, is_end, oos_start, oos_end).

    El split es secuencial: los primeros 80% de fechas son in-sample,
    el restante 20% es out-of-sample.
    """
    n = len(total_dates)
    split_idx = int(n * (1 - oos_pct))
    split_idx = max(split_idx, 1)  # mínimo 1 fecha en IS
    if split_idx >= n:
        split_idx = n - 1  # mínimo 1 fecha en OOS

    is_start = total_dates[0]
    is_end = total_dates[split_idx - 1]
    oos_start = total_dates[split_idx]
    oos_end = total_dates[-1]

    return is_start, is_end, oos_start, oos_end


def _detect_overfitting(is_pf: float, oos_pf: float) -> tuple[bool, float]:
    """
    Detecta overfitting comparando profit factor in-sample vs out-of-sample.
    Retorna (overfitted, drop_pct).

    Criterio: si el PF cae más del 30% en OOS, está overfitteado.
    """
    if is_pf <= 0:
        return True, 0.0
    drop_pct = (is_pf - oos_pf) / is_pf * 100
    return drop_pct > 30, drop_pct


def _evaluate_combo(engine: BacktestEngine,
                     start: str, end: str, capital: float,
                     combo: dict[str, Any],
                     cancel_flag: Optional[threading.Event] = None) -> dict[str, Any]:
    """Evalúa una combinación de parámetros y retorna métricas."""
    result = engine.run(
        start_date=start,
        end_date=end,
        capital=capital,
        override_params=combo,
        cancel_flag=cancel_flag,
    )

    return {
        **combo,
        "profit_factor": result.profit_factor,
        "win_rate": result.win_rate,
        "total_trades": result.total_trades,
        "total_pnl_pct": result.total_pnl_pct,
        "max_drawdown_pct": result.max_drawdown_pct,
        "avg_win_pct": result.avg_win_pct,
        "avg_loss_pct": result.avg_loss_pct,
        "max_consec_losses": result.max_consecutive_losses,
        "sharpe_ratio": result.sharpe_ratio,
        "sortino_ratio": result.sortino_ratio,
        "calmar_ratio": result.calmar_ratio,
        "mc_dd_p95": result.mc_drawdown_p95,
        "regime_trend_wr": result.regime_trend_win_rate,
        "regime_transition_wr": result.regime_transition_win_rate,
        "regime_ranging_wr": result.regime_ranging_win_rate,
    }


# ── Fix #8: Análisis de sensibilidad ──────────────────────────

def _build_heatmap(df: pd.DataFrame, param_x: str, param_y: str,
                   metric: str = "profit_factor") -> dict[str, Any]:
    """
    Construye un heatmap 2D para un par de parámetros y una métrica.

    Retorna un dict con:
      - "param_x": nombre del parámetro X
      - "param_y": nombre del parámetro Y
      - "metric": métrica usada
      - "values": matriz 2D de valores promedio
      - "x_labels": etiquetas del eje X
      - "y_labels": etiquetas del eje Y
      - "stable": True si la zona óptima es amplia (robusto)
      - "peak_sharpness": qué tan agudo es el pico (0=meseta, 1=pico agudo)

    Solo funciona si ambos parámetros tienen al menos 2 valores distintos.
    """
    if df.empty:
        return {"error": "DataFrame vacío"}

    if param_x not in df.columns or param_y not in df.columns:
        return {"error": f"Parámetros {param_x}, {param_y} no encontrados"}

    if metric not in df.columns:
        return {"error": f"Métrica {metric} no encontrada"}

    x_values = sorted(df[param_x].unique())
    y_values = sorted(df[param_y].unique())

    if len(x_values) < 2 or len(y_values) < 2:
        return {
            "param_x": param_x,
            "param_y": param_y,
            "metric": metric,
            "warning": "Se necesitan al menos 2 valores distintos por parámetro",
            "stable": True,
            "peak_sharpness": 0.0,
        }

    # Construir matriz de valores promedio
    matrix = np.full((len(y_values), len(x_values)), np.nan)
    for i, xv in enumerate(x_values):
        for j, yv in enumerate(y_values):
            subset = df[(df[param_x] == xv) & (df[param_y] == yv)]
            if not subset.empty:
                matrix[j, i] = subset[metric].mean()

    # Detectar estabilidad: si el mejor resultado tiene vecinos cercanos
    # con rendimiento similar, es robusto.
    best_idx = np.unravel_index(np.nanargmax(matrix), matrix.shape)
    best_val = matrix[best_idx]

    # Examinar vecinos inmediatos (arriba, abajo, izquierda, derecha)
    neighbors = []
    for di, dj in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        ni, nj = best_idx[0] + di, best_idx[1] + dj
        if 0 <= ni < len(y_values) and 0 <= nj < len(x_values):
            if not np.isnan(matrix[ni, nj]):
                neighbors.append(matrix[ni, nj])

    if neighbors and best_val > 0:
        # sharpness: 1 - (avg neighbor / best)
        # Si vecinos son 90% del best → sharpness=0.1 (meseta)
        # Si vecinos son 40% del best → sharpness=0.6 (pico agudo)
        avg_neighbor = np.mean(neighbors)
        peak_sharpness = max(0.0, min(1.0, 1.0 - avg_neighbor / best_val))
        stable = peak_sharpness < 0.3
    else:
        peak_sharpness = 0.0
        stable = True

    return {
        "param_x": param_x,
        "param_y": param_y,
        "metric": metric,
        "x_values": [str(v) for v in x_values],
        "y_values": [str(v) for v in y_values],
        "matrix": [[float(v) if not np.isnan(v) else None for v in row] for row in matrix.tolist()],
        "best_value": float(best_val) if not np.isnan(best_val) else None,
        "best_params": {
            param_x: float(x_values[best_idx[1]]),
            param_y: float(y_values[best_idx[0]]),
        },
        "stable": stable,
        "peak_sharpness": round(peak_sharpness, 3),
    }


# ── Función principal ─────────────────────────────────────────

def optimize(
    symbol: str,
    fase_num: int,
    start_date: str,
    end_date: str,
    capital: float,
    param_grid: Optional[dict[str, list[Any]]] = None,
    min_trades: int = DEFAULT_MIN_TRADES,
    cancel_flag: Optional[threading.Event] = None,
    # Fix #4: Parámetros de walk-forward
    walk_forward: bool = False,
    oos_pct: float = OOS_SPLIT,
    # Fix #8: Análisis de sensibilidad
    sensitivity_params: Optional[list[str]] = None,
) -> pd.DataFrame:
    """
    Optimiza parámetros con walk-forward testing opcional (Fix #4)
    y análisis de sensibilidad (Fix #8).

    Args:
        symbol, fase_num, start_date, end_date, capital: parámetros del backtest.
        param_grid: grilla de parámetros a probar (default: PARAM_GRID).
        min_trades: mínimo de trades para considerar un resultado válido.
        cancel_flag: flag para cancelar la optimización.
        walk_forward: si True, ejecuta walk-forward testing (80/20).
        oos_pct: porcentaje out-of-sample (default 0.20).
        sensitivity_params: lista de parámetros para análisis de sensibilidad 2D.
                            Si es None, se usan ["tp1_rr", "tp2_rr"].
    """
    grid = param_grid or PARAM_GRID
    combos = _all_combinations(grid)
    resultados = []
    total = len(combos)

    mode = "WALK-FORWARD" if walk_forward else "STANDARD"
    logger.info(f"Optimizando {symbol} | Fase {fase_num} | Modo: {mode} | {total} combinaciones")
    logger.info(f"Período: {start_date} → {end_date} | Capital: ${capital:.2f}")

    # ── Fix #4: Preparar split walk-forward ──
    is_start, is_end, oos_start, oos_end = start_date, end_date, start_date, end_date
    total_dates_parsed: list[str] = []

    if walk_forward:
        # Generar lista de fechas entre start_date y end_date (diarias)
        try:
            total_dates_parsed = [
                str(d.date()) for d in pd.date_range(start_date, end_date, freq="D")
            ]
        except Exception:
            logger.warning("No se pudieron generar fechas para walk-forward. Usando modo standard.")
            walk_forward = False

        if walk_forward and len(total_dates_parsed) < 10:
            logger.warning(f"Solo {len(total_dates_parsed)} días disponibles. Walk-forward requiere ≥10.")
            walk_forward = False

    if walk_forward:
        is_start, is_end, oos_start, oos_end = _walk_forward_split(total_dates_parsed, oos_pct)
        logger.info(
            f"📊 Walk-Forward: IS={is_start}→{is_end} ({is_end} días) "
            f"OOS={oos_start}→{oos_end} ({len(total_dates_parsed) - len(total_dates_parsed[:int(len(total_dates_parsed)*(1-oos_pct))])} días)"
        )

    engine = BacktestEngine(symbol=symbol, fase_num=fase_num)

    for idx, combo in enumerate(combos, 1):
        if cancel_flag and cancel_flag.is_set():
            logger.warning("Optimización cancelada por el usuario")
            break

        logger.info(f"Combinación {idx}/{total}: {combo}")

        # Evaluar in-sample
        is_result = _evaluate_combo(engine, is_start, is_end, capital, combo, cancel_flag)

        if is_result["total_trades"] < min_trades:
            continue

        # Si es walk-forward, evaluar out-of-sample también
        oos_result = None
        if walk_forward and oos_start != oos_end:
            oos_result = _evaluate_combo(engine, oos_start, oos_end, capital, combo, cancel_flag)
            if oos_result["total_trades"] < min_trades:
                continue

        # Combinar resultados
        entry = {
            **is_result,
            # Prefijo IS para métricas in-sample
        }

        if oos_result:
            # Métricas out-of-sample con prefijo OOS
            oos_entry = {f"OOS_{k}": v for k, v in oos_result.items()
                         if k not in combo}  # excluir params repetidos
            entry.update(oos_entry)

            # Detectar overfitting
            is_pf = is_result.get("profit_factor", 0)
            oos_pf = oos_result.get("profit_factor", 0)
            overfitted, drop_pct = _detect_overfitting(is_pf, oos_pf)
            entry["overfitted"] = overfitted
            entry["oos_pf_drop_pct"] = round(drop_pct, 1)

            # La métrica de ranking es OOS profit factor
            entry["ranking_metric"] = oos_pf
        else:
            entry["ranking_metric"] = is_result.get("profit_factor", 0)

        resultados.append(entry)

        if idx % 10 == 0:
            logger.info(f"Progreso: {idx}/{total} ({idx/total*100:.1f}%) | "
                        f"válidos: {len(resultados)}")

    df = pd.DataFrame(resultados)
    if not df.empty:
        df = df.sort_values("ranking_metric", ascending=False).reset_index(drop=True)
        best = df.iloc[0]
        logger.info(f"Optimización completada. Mejor ranking: {best.get('ranking_metric', 0):.2f}")
        if walk_forward and "oos_pf_drop_pct" in best:
            logger.info(f"  IS PF: {best.get('profit_factor', 0):.2f} | "
                        f"OOS PF: {best.get('OOS_profit_factor', 0):.2f} | "
                        f"Drop: {best.get('oos_pf_drop_pct', 0):.1f}% | "
                        f"Overfitted: {best.get('overfitted', False)}")
    else:
        logger.warning("Ninguna combinación generó suficientes trades")

    # ── Fix #8: Análisis de sensibilidad ──
    if not df.empty and len(df) > 1:
        heatmaps = []
        sens_params = sensitivity_params or ["tp1_rr", "tp2_rr"]

        # Generar heatmaps para todos los pares posibles
        for i in range(len(sens_params)):
            for j in range(i + 1, len(sens_params)):
                hm = _build_heatmap(df, sens_params[i], sens_params[j], "profit_factor")
                heatmaps.append(hm)

        if heatmaps:
            # Guardar heatmaps como JSON adjunto
            hm_path = Path("logs") / f"optimizer_heatmap_{symbol}_{start_date}_{end_date}.json"
            hm_path.parent.mkdir(exist_ok=True)

            # Aplanar matrices numpy para JSON serializable
            hm_clean = []
            for hm in heatmaps:
                hc = dict(hm)
                if "matrix" in hc and isinstance(hc["matrix"], list):
                    hc["matrix"] = [[v if v is not None else None for v in row] for row in hc["matrix"]]
                hm_clean.append(hc)

            with open(hm_path, "w") as f:
                json.dump(hm_clean, f, indent=2, default=str)
            logger.info(f"📊 Heatmaps guardados en: {hm_path}")

            # Resumen de estabilidad
            for hm in heatmaps:
                if "stable" in hm:
                    stability = "✅ Robusto" if hm["stable"] else "⚠️ Pico agudo (overfitting)"
                    logger.info(
                        f"  Sensibilidad {hm['param_x']}×{hm['param_y']}: "
                        f"{stability} (sharpness={hm.get('peak_sharpness', 'N/A')})"
                    )

        # Guardar ranking de todas las combinaciones
        out = Path("logs") / f"optimizer_{symbol}_{start_date}_{end_date}.csv"
        out.parent.mkdir(exist_ok=True)
        df.to_csv(out, index=False)
        logger.info(f"Resultados completos guardados en: {out}")
    elif not df.empty:
        out = Path("logs") / f"optimizer_{symbol}_{start_date}_{end_date}.csv"
        out.parent.mkdir(exist_ok=True)
        df.to_csv(out, index=False)
        logger.info(f"Resultados guardados en: {out}")

    return df


import argparse

parser = argparse.ArgumentParser(description="Optimizador de parámetros del bot (v3.0)")
parser.add_argument("--symbol", default="XRPUSDT", help="Par de trading")
parser.add_argument("--start", required=True, help="Fecha inicio (YYYY-MM-DD)")
parser.add_argument("--end", required=True, help="Fecha fin (YYYY-MM-DD)")
parser.add_argument("--capital", type=float, default=100.0, help="Capital inicial USDT")
parser.add_argument("--fase", type=int, default=0, help="Fase base (0-4)")
parser.add_argument("--min-trades", type=int, default=5, help="Mínimo de trades por combo")
parser.add_argument("--walk-forward", action="store_true",
                    help="Activar walk-forward testing (80%% IS + 20%% OOS)")
parser.add_argument("--no-sensitivity", action="store_true",
                    help="Desactivar analisis de sensibilidad")


if __name__ == "__main__":
    args = parser.parse_args()

    print("=" * 65)
    mode_str = "WALK-FORWARD" if args.walk_forward else "STANDARD"
    print(f"  OPTIMIZADOR v3.0 — {args.symbol} | {args.start} → {args.end} | {mode_str}")
    print("=" * 65)

    combos = _all_combinations(PARAM_GRID)
    print(f"  {len(combos)} combinaciones a probar\n")

    df = optimize(
        symbol=args.symbol,
        fase_num=args.fase,
        start_date=args.start,
        end_date=args.end,
        capital=args.capital,
        min_trades=args.min_trades,
        walk_forward=args.walk_forward,
        sensitivity_params=None if args.no_sensitivity else ["tp1_rr", "tp2_rr", "breakeven_trigger"],
    )

    if df.empty:
        print("\nSin resultados. Intenta con más días o reduce --min-trades.")
        sys.exit(0)

    print(f"\n{'=' * 65}")
    print(f"  TOP 10 — ordenado por {'OOS Profit Factor' if args.walk_forward else 'Profit Factor'}")
    print(f"{'=' * 65}")
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)

    display_cols = [c for c in df.columns if c != "ranking_metric"]
    if "overfitted" in df.columns and args.walk_forward:
        # Mostrar columnas clave en walk-forward
        key_cols = list(PARAM_GRID.keys()) + [
            "profit_factor", "OOS_profit_factor", "oos_pf_drop_pct",
            "overfitted", "total_trades", "max_drawdown_pct", "sharpe_ratio"
        ]
        key_cols = [c for c in key_cols if c in df.columns]
        print(df.head(10)[key_cols].to_string(index=True))
    else:
        print(df.head(10).to_string(index=True))

    print(f"{'=' * 65}")

    best = df.iloc[0]
    print(f"\n  Mejor configuración encontrada:")
    for col in PARAM_GRID.keys():
        print(f"    {col}: {best[col]}")
    if args.walk_forward and "OOS_profit_factor" in best:
        print(f"  → IS Profit Factor: {best['profit_factor']:.2f} | "
              f"OOS Profit Factor: {best['OOS_profit_factor']:.2f} | "
              f"Drop: {best.get('oos_pf_drop_pct', 0):.1f}%")
        if best.get("overfitted"):
            print("  ⚠️  ADVERTENCIA: La combinación ganadora muestra overfitting (>30% drop en OOS)")
    else:
        print(f"  → Profit Factor: {best['profit_factor']:.2f} | "
              f"Win Rate: {best['win_rate']:.1f}% | "
              f"PnL: {best['total_pnl_pct']:+.2f}% | "
              f"Max DD: {best['max_drawdown_pct']:.2f}%")

    # Mostrar resumen de sensibilidad si hay heatmaps
    hm_path = Path("logs") / f"optimizer_heatmap_{args.symbol}_{args.start}_{args.end}.json"
    if hm_path.exists():
        print(f"\n  📊 Heatmap de sensibilidad guardado en: {hm_path}")
        try:
            with open(hm_path) as f:
                heatmaps = json.load(f)
            for hm in heatmaps:
                if "stable" in hm:
                    status = "✅ Robusto (meseta amplia)" if hm["stable"] else "⚠️ Pico agudo (posible overfitting)"
                    print(f"    {hm['param_x']}×{hm['param_y']}: {status} (sharpness={hm.get('peak_sharpness', 'N/A')})")
        except Exception:
            pass
