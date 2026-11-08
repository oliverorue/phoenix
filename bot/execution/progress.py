"""Progreso hacia la siguiente etapa de escalamiento (gates de edge probado).

Los umbrales salen de ``PLAN_ESCALAMIENTO.md``: ≥40 trades, win-rate ≥35%,
profit factor ≥1.3 y PnL > 0. Cuando se cumplen todos, el edge se considera
probado y se puede subir de etapa / inyectar más capital.
"""

from __future__ import annotations

TARGET_TRADES = 40
TARGET_WIN_RATE = 0.35
TARGET_PROFIT_FACTOR = 1.3


def _fmt_pf(pf: float) -> str:
    if pf == float("inf"):
        return "∞"
    return f"{pf:.2f}"


def stage_progress(stats: dict) -> dict:
    """Evalúa los gates de la etapa a partir de las métricas de trades."""
    trades = int(stats.get("total", 0))
    win_rate = float(stats.get("win_rate", 0.0))
    profit_factor = float(stats.get("profit_factor", 0.0))
    pnl = float(stats.get("pnl", 0.0))

    gates = {
        "trades": trades >= TARGET_TRADES,
        "win_rate": win_rate >= TARGET_WIN_RATE,
        "profit_factor": profit_factor >= TARGET_PROFIT_FACTOR,
        "pnl": pnl > 0,
    }
    passed = all(gates.values())

    missing: list[str] = []
    if not gates["trades"]:
        missing.append(f"{TARGET_TRADES - trades} trades")
    if not gates["win_rate"]:
        missing.append(f"WR ≥{TARGET_WIN_RATE * 100:.0f}%")
    if not gates["profit_factor"]:
        missing.append(f"PF ≥{TARGET_PROFIT_FACTOR}")
    if not gates["pnl"]:
        missing.append("PnL > 0")

    return {
        "trades": trades,
        "trades_goal": TARGET_TRADES,
        "win_rate": win_rate,
        "win_rate_goal": TARGET_WIN_RATE,
        "profit_factor": profit_factor,
        "profit_factor_goal": TARGET_PROFIT_FACTOR,
        "pnl": pnl,
        "gates": gates,
        "passed": passed,
        "missing": missing,
    }


def format_progress(progress: dict) -> str:
    """Línea legible del progreso de etapa."""
    gates = progress["gates"]

    def mark(ok: bool) -> str:
        return "✅" if ok else "⏳"

    line = (
        f"{progress['trades']}/{progress['trades_goal']} trades "
        f"{mark(gates['trades'])} · "
        f"WR {progress['win_rate'] * 100:.0f}% {mark(gates['win_rate'])} · "
        f"PF {_fmt_pf(progress['profit_factor'])} {mark(gates['profit_factor'])} · "
        f"PnL {progress['pnl']:+.2f} {mark(gates['pnl'])}"
    )
    if progress["passed"]:
        line += " · ✅ ETAPA CUMPLIDA"
    elif progress["missing"]:
        line += " · falta: " + ", ".join(progress["missing"])
    return line
