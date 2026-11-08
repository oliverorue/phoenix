"""
Página del Radar — dashboard limpio, gráfico y dinámico.

Organiza todo en tabs (Señales / Paper / Ruta / Diagnóstico), muestra KPIs arriba,
curva de capital y PnL por trade, y el progreso de la etapa pre-live con barras.
Se actualiza sola vía ``st.fragment``.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from bot.dashboard.scanner_bridge import ScannerBridge
from bot.execution.progress import stage_progress
from bot.scanner.ranker import RankedSignal

_CSS = """
<style>
.block-container { padding-top: 1.1rem; max-width: 1180px; }
h1 { letter-spacing: -0.6px; }
div[data-testid="stMetric"] {
  background: linear-gradient(180deg, rgba(255,255,255,0.06), rgba(255,255,255,0.02));
  border: 1px solid rgba(255,255,255,0.10); border-radius: 14px;
  padding: 0.7rem 0.9rem;
}
div[data-testid="stMetricLabel"] { opacity: 0.8; }
.signal-card { border-radius: 18px; padding: 18px 20px; margin: 12px 0;
  box-shadow: 0 12px 30px rgba(0,0,0,0.38); color: #fff; position: relative; }
.score-badge { background: rgba(0,0,0,0.30); border-radius: 999px;
  padding: 2px 10px; font-weight: 700; font-size: 0.85rem; }
.conf-bar { background: rgba(0,0,0,0.25); border-radius: 999px; height: 8px; margin: 10px 0 4px; }
.conf-fill { background: #fff; border-radius: 999px; height: 8px; }
.trade-row { border-left: 4px solid #888; padding: 6px 12px; margin: 4px 0;
  background: rgba(255,255,255,0.03); border-radius: 10px; }
</style>
"""


@st.cache_resource
def _get_bridge() -> ScannerBridge:
    """Única instancia del scanner por proceso Streamlit."""
    bridge = ScannerBridge()
    bridge.start()
    return bridge


def _in_streamlit() -> bool:
    """True solo dentro de una sesión real de Streamlit (no en import/tests)."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx() is not None
    except Exception:
        return False


def _pnl_color(value: float) -> str:
    return "#22c55e" if value >= 0 else "#ef4444"


def _card_html(ranked: RankedSignal, rank: int, risk_usdt: float,
               risk_pct: float, kelly: float) -> str:
    sig = ranked.signal
    bg = "linear-gradient(135deg, #16a34a, #0f7a37)" if sig.direction == "LONG" \
        else "linear-gradient(135deg, #dc2626, #a51d1d)"
    arrow = "▲ LONG" if sig.direction == "LONG" else "▼ SHORT"
    reasons = " · ".join(sig.reasons)
    conf_pct = sig.confidence * 100
    b = ranked.breakdown
    return (
        f'<div class="signal-card" style="background:{bg}">'
        f'<h2 style="margin:0 0 4px;font-size:1.7rem">#{rank} {arrow} — {sig.symbol} '
        f'<span class="score-badge">score {ranked.score:.2f}</span></h2>'
        f'<div style="font-size:1.05rem;opacity:.95">{reasons}</div>'
        f'<div style="margin-top:8px;font-size:1.05rem">Precio: <b>{sig.price}</b>'
        f' &nbsp;·&nbsp; Confianza: <b>{conf_pct:.0f}%</b></div>'
        f'<div class="conf-bar"><div class="conf-fill" style="width:{conf_pct:.0f}%"></div></div>'
        f'<h3 style="margin:10px 0 0;font-size:1.5rem">Arriesgar: ${risk_usdt:,.2f} USDT</h3>'
        f'<div style="opacity:.85">{risk_pct:.3f}% del capital · Kelly ×{kelly}</div>'
        f'<div style="opacity:.7;font-size:.85rem;margin-top:6px">'
        f'motivos {b.get("motivos", 0):.2f} · liquidez {b.get("liquidez", 0):.0%} · '
        f'frescura {b.get("frescura", 0):.0%}</div>'
        f'</div>'
    )


def _render_signals(st_module: Any, bridge: ScannerBridge,
                    top_n: int, min_conf: float) -> None:
    ranked = bridge.top_signals(limit=max(top_n, 10))
    ranked = [r for r in ranked if r.signal.confidence >= min_conf][:top_n]

    if not ranked:
        st_module.info("Sin señales que cumplan los filtros. Ajustá confianza o esperá.")
        return

    mode = "paper" if bridge.is_dry_run() else "LIVE"
    for i, r in enumerate(ranked, start=1):
        sizing = bridge.suggest_sizing(r.signal)
        st_module.markdown(
            _card_html(r, i, sizing.risk_usdt, sizing.risk_pct,
                       sizing.kelly_multiplier),
            unsafe_allow_html=True,
        )
        if st_module.button(
            f"🛒 Operar {r.signal.symbol} {r.signal.direction} ({mode})",
            key=f"operar_{r.signal.symbol}_{i}",
        ):
            trade = bridge.execute(r.signal)
            if trade is not None:
                st_module.toast(
                    f"✅ Trade {trade.id} abierto: {trade.symbol} "
                    f"{trade.direction.value} @ {trade.entry_price}"
                )
            else:
                st_module.toast("No se pudo abrir el trade")
            st_module.rerun()


def _render_paper(st_module: Any, bridge: ScannerBridge) -> None:
    s = bridge.paper_stats()
    open_pos = bridge.paper_open_positions()
    unrealized = sum(upnl for _t, upnl, _p in open_pos)

    c1, c2, c3, c4 = st_module.columns(4)
    c1.metric("💵 Capital", f"${bridge.equity():,.2f}")
    c2.metric("PnL realizado", f"${s['pnl']:+.2f}")
    c3.metric("PnL latente", f"${unrealized:+.2f}")
    c4.metric("Win rate", f"{s['win_rate'] * 100:.0f}%")

    curve = bridge.equity_curve()
    if len(curve) >= 2:
        st_module.markdown("**📈 Curva de capital**")
        st_module.line_chart(curve, height=190)
    else:
        st_module.caption("La curva aparece cuando cierres tu primer trade.")

    pnls = bridge.pnl_series()
    if pnls:
        st_module.markdown("**📊 PnL por trade**")
        st_module.bar_chart(pnls, height=170)

    if open_pos:
        st_module.markdown("**🔓 Abiertos**")
        for t, upnl, price in open_pos:
            dist_sl = abs(price - t.sl_price) / price * 100 if price else 0.0
            st_module.markdown(
                f'<div class="trade-row" style="border-left-color:{_pnl_color(upnl)}">'
                f'<b>{t.id} {t.symbol} {t.direction.value}</b> · precio {price:g} · '
                f'entró {t.entry_price:g} · SL {t.sl_price:g} ({dist_sl:.2f}%) · '
                f'<span style="color:{_pnl_color(upnl)}">uPnL {upnl:+.2f} USDT</span> · '
                f'{t.status.value}</div>',
                unsafe_allow_html=True,
            )
    else:
        st_module.caption("Sin trades abiertos.")

    closed = bridge.paper_closed_trades()
    if closed:
        st_module.markdown("**🔒 Cerrados (recientes)**")
        for t in reversed(closed[-10:]):
            st_module.markdown(
                f'<div class="trade-row" style="border-left-color:{_pnl_color(t.realized_pnl)}">'
                f'{t.id} {t.symbol} {t.direction.value} · {t.exit_reason} · '
                f'<span style="color:{_pnl_color(t.realized_pnl)}">'
                f'PnL {t.realized_pnl:+.2f} USDT</span></div>',
                unsafe_allow_html=True,
            )

    if st_module.button("📨 Enviar estado a Telegram"):
        if bridge.notifications_enabled():
            bridge.notify_status()
            st_module.toast("Estado enviado a Telegram")
        else:
            st_module.warning(
                "Telegram desactivado: configurá TELEGRAM_TOKEN y "
                "TELEGRAM_CHAT_ID en config/secrets.env"
            )


def _render_roadmap(st_module: Any, bridge: ScannerBridge) -> None:
    progress = stage_progress(bridge.paper_stats())
    gates = progress["gates"]

    st_module.markdown("### 🗺️ Ruta antes de pasar a LIVE")

    trades_ratio = progress["trades"] / progress["trades_goal"]
    st_module.progress(
        min(trades_ratio, 1.0),
        text=f"Trades: {progress['trades']}/{progress['trades_goal']}",
    )
    wr_ratio = progress["win_rate"] / progress["win_rate_goal"]
    st_module.progress(
        min(wr_ratio, 1.0),
        text=f"Win rate: {progress['win_rate'] * 100:.0f}% "
             f"(meta ≥{progress['win_rate_goal'] * 100:.0f}%)",
    )
    pf = progress["profit_factor"]
    pf_ratio = 1.0 if pf == float("inf") else min(pf / progress["profit_factor_goal"], 1.0)
    pf_txt = "∞" if pf == float("inf") else f"{pf:.2f}"
    st_module.progress(
        pf_ratio,
        text=f"Profit factor: {pf_txt} (meta ≥{progress['profit_factor_goal']})",
    )
    st_module.progress(
        1.0 if progress["pnl"] > 0 else 0.0,
        text=f"PnL: ${progress['pnl']:+.2f} (meta > 0)",
    )

    if progress["passed"]:
        st_module.success("✅ Etapa cumplida: edge probado. Podés ir a testnet.")
    else:
        st_module.info("Falta: " + ", ".join(progress["missing"]))

    st_module.markdown("**Checklist pre-live**")
    st_module.markdown(
        "- ✅ 40 trades paper con los gates cumplidos\n"
        "- ⏳ Timeout + kill switch + persistencia probados\n"
        "- ⏳ Telegram funcionando (abrir/cerrar/estado)\n"
        "- ⏳ Capa live validada en testnet\n"
        "- ⏳ Keys solo en `config/secrets.env`\n"
        "- ⏳ `max_risk_pct` bajado a 1–2% para real\n"
        "- ⏳ VPS o máquina 24/7"
    )
    st_module.caption("Detalle completo en `RUTA_PRE_LIVE.md`.")


def _render_diagnostics(st_module: Any, bridge: ScannerBridge, stats: Any) -> None:
    d = stats.diagnostics
    st_module.markdown(
        f"**Contadores** — pares `{stats.tracked}` · señales `{stats.signals_emitted}` · "
        f"eventos `{stats.events_processed:,}` · reconexiones `{stats.reconnects}`"
    )
    st_module.markdown(
        f"evals `{d.get('evals', 0)}` · agg `{d.get('agg', 0)}` · "
        f"depth `{d.get('depth', 0)}` · funding `{d.get('funding', 0)}` · "
        f"force `{d.get('force', 0)}`"
    )
    st_module.markdown(
        f"hits — OBI `{d.get('obi_hits', 0)}` · delta `{d.get('delta_hits', 0)}` · "
        f"spike `{d.get('spike_hits', 0)}` · funding `{d.get('funding_hits', 0)}` · "
        f"cascade `{d.get('cascade_hits', 0)}`"
    )
    sc = stats.stream_counts
    if sc:
        st_module.markdown(
            "streams — " + " · ".join(f"`{k}` {v}" for k, v in sorted(sc.items()))
        )
    if stats.last_error:
        st_module.warning(f"Último error: {stats.last_error}")
    if not bridge.is_running():
        st_module.error("El scanner no está corriendo.")
    st_module.markdown(
        "- **Pesos del ranking**: `settings.yaml → scanner.w_*`\n"
        "- **Filtros**: `min_quote_volume_24h`, `max_symbols`\n"
        "- **Umbrales**: `obi_extreme`, `volume_zscore`, `delta_extreme`, "
        "`funding_extreme`\n"
        "- **Riesgo**: `max_risk_pct`, `sl_distance_pct`, `tp1_ratio`, "
        "`trailing_dist_pct`, `max_hold_seconds`"
    )
    if st_module.button("🔄 Reiniciar radar"):
        _get_bridge.clear()
        st_module.rerun()


def render_scanner_page(st_module: Any = st) -> None:
    """Renderiza el radar completo. Importar en ``app.py``."""
    st_module.markdown(_CSS, unsafe_allow_html=True)
    st_module.title("📡 Radar — Binance USDⓈ-M Futures")

    if not _in_streamlit():
        st_module.info(
            "Radar disponible al ejecutar `streamlit run bot/dashboard/app.py`."
        )
        return

    bridge = _get_bridge()
    _signals, stats, _tracked = bridge.snapshot()

    # ── KPIs arriba ─────────────────────────────────────
    s = bridge.paper_stats()
    open_pos = bridge.paper_open_positions()
    unrealized = sum(upnl for _t, upnl, _p in open_pos)
    k1, k2, k3, k4 = st_module.columns(4)
    k1.metric("💵 Capital", f"${bridge.equity():,.2f}")
    k2.metric("PnL", f"${s['pnl']:+.2f}", f"{unrealized:+.2f} latente")
    k3.metric("🎯 Win rate", f"{s['win_rate'] * 100:.0f}%")
    k4.metric("🔢 Trades", f"{s['total']}/40")

    # ── Controles ───────────────────────────────────────
    c_n, c_conf, c_auto, c_kill = st_module.columns([2, 2, 1, 1])
    with c_n:
        top_n = st_module.slider("Nº señales", 1, 10, 3)
    with c_conf:
        min_conf = st_module.slider("Confianza mínima", 0, 90, 40, step=5) / 100.0
    with c_auto:
        st_module.write("")
        new_auto = st_module.toggle(
            "🤖 Auto top-1", value=bridge.auto_trade_enabled(),
        )
        if new_auto != bridge.auto_trade_enabled():
            bridge.set_auto_trade(new_auto)
            st_module.rerun()
    with c_kill:
        st_module.write("")
        if st_module.button("🛑 Cerrar TODO", use_container_width=True):
            n = bridge.kill_switch()
            st_module.toast(f"✅ {n} trade(s) cerrado(s)")
            st_module.rerun()

    mode = "PAPER" if bridge.is_dry_run() else "LIVE"
    auto_txt = "ON" if bridge.auto_trade_enabled() else "OFF"
    st_module.caption(
        f"Modo **{mode}** · capital y riesgo automáticos (Kelly, techo 10%) · "
        f"100% del capital · Auto-entrada **{auto_txt}**."
    )

    # ── Tabs ────────────────────────────────────────────
    tab_signals, tab_paper, tab_roadmap, tab_diag = st_module.tabs(
        ["🚨 Señales", "📊 Paper", "🗺️ Ruta", "🔍 Diagnóstico"]
    )
    with tab_signals:
        _render_signals(st_module, bridge, top_n, min_conf)
    with tab_paper:
        _render_paper(st_module, bridge)
    with tab_roadmap:
        _render_roadmap(st_module, bridge)
    with tab_diag:
        _render_diagnostics(st_module, bridge, stats)
