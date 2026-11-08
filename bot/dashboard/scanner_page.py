"""
Página del Radar para el dashboard Streamlit — limpia e interactiva.

Muestra solo las mejores N señales (ranking por confianza, confluencia,
liquidez y frescura), con tarjetas grandes LONG/SHORT, dimensionamiento
Kelly y una ruta operativa. Se actualiza sola vía ``st.fragment``.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from bot.dashboard.scanner_bridge import ScannerBridge
from bot.scanner.ranker import RankedSignal

_RUTA = [
    ("🎯", "Esperá una tarjeta LONG/SHORT con score alto y 2+ motivos alineados."),
    ("📉", "Revisá la tendencia 1h: si va en contra de la dirección, descartá."),
    ("🕯️", "Confirmá en la vela 15m que no estás entrando justo en el pico."),
    ("📐", "Tomá el «USDT a arriesgar» y calculá cantidad = riesgo ÷ distancia al SL."),
    ("🛒", "Entrá con orden límite (menos fees) y colocá el SL apenas llene."),
    ("🏁", "TP1 parcial (50–65%), mové SL a breakeven, dejá correr el resto."),
    ("📓", "El bot registra el trade solo. Nunca más del 1–2% por operación."),
]

_CSS = """
<style>
.block-container { padding-top: 1.2rem; }
h1 { letter-spacing: -0.5px; }
div[data-testid="stMetric"] { background: rgba(255,255,255,0.03);
  border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 0.6rem 0.8rem; }
.signal-card { border-radius: 16px; padding: 18px 20px; margin: 12px 0;
  box-shadow: 0 10px 28px rgba(0,0,0,0.35); color: #fff; }
.score-badge { background: rgba(0,0,0,0.28); border-radius: 999px;
  padding: 2px 10px; font-weight: 700; font-size: 0.85rem; }
.conf-bar { background: rgba(0,0,0,0.25); border-radius: 999px; height: 8px; margin: 10px 0 4px; }
.conf-fill { background: #fff; border-radius: 999px; height: 8px; }
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


def _card_html(ranked: RankedSignal, rank: int, risk_usdt: float,
               risk_pct: float, kelly: float) -> str:
    sig = ranked.signal
    bg = "#16a34a" if sig.direction == "LONG" else "#dc2626"
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


def _pnl_color(value: float) -> str:
    return "#16a34a" if value >= 0 else "#dc2626"


def _render_paper(st_module: Any, bridge: ScannerBridge) -> None:
    """Panel de trades paper: capital automático, PnL en vivo, abiertos y cerrados."""
    s = bridge.paper_stats()
    open_pos = bridge.paper_open_positions()
    unrealized = sum(upnl for _t, upnl, _p in open_pos)

    c1, c2, c3, c4 = st_module.columns(4)
    c1.metric("💵 Capital", f"${bridge.equity():,.2f}")
    c2.metric("PnL realizado", f"${s['pnl']:+.2f}")
    c3.metric("PnL latente", f"${unrealized:+.2f}")
    c4.metric("Win rate", f"{s['win_rate'] * 100:.0f}%")

    st_module.caption(f"**Etapa:** {bridge.progress_line()}")

    if st_module.button("📨 Enviar estado a Telegram"):
        if bridge.notifications_enabled():
            bridge.notify_status()
            st_module.toast("Estado enviado a Telegram")
        else:
            st_module.warning(
                "Telegram desactivado: configurá TELEGRAM_TOKEN y "
                "TELEGRAM_CHAT_ID en config/secrets.env"
            )

    if open_pos:
        st_module.markdown("**🔓 Abiertos**")
        for t, upnl, price in open_pos:
            dist_sl = abs(price - t.sl_price) / price * 100 if price else 0.0
            st_module.markdown(
                f'<div style="border-left:4px solid {_pnl_color(upnl)};'
                f'padding:6px 10px;margin:4px 0;background:rgba(255,255,255,0.03);'
                f'border-radius:8px">'
                f'<b>{t.id} {t.symbol} {t.direction.value}</b> · '
                f'precio {price:g} · entró {t.entry_price:g} · SL {t.sl_price:g} '
                f'({dist_sl:.2f}%) · '
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
                f'<div style="padding:4px 10px;margin:2px 0">'
                f'{t.id} {t.symbol} {t.direction.value} · {t.exit_reason} · '
                f'<span style="color:{_pnl_color(t.realized_pnl)}">'
                f'PnL {t.realized_pnl:+.2f} USDT</span></div>',
                unsafe_allow_html=True,
            )


def render_scanner_page(st_module: Any = st) -> None:
    """Renderiza el radar completo. Importar en ``app.py``."""
    st_module.markdown(_CSS, unsafe_allow_html=True)
    st_module.title("📡 Radar Global — Binance USDⓈ-M Futures")
    st_module.caption("Monitor-only: muestra anomalías, no ejecuta órdenes.")

    if not _in_streamlit():
        st_module.info(
            "Radar disponible al ejecutar `streamlit run bot/dashboard/app.py`."
        )
        return

    bridge = _get_bridge()

    # ── Controles (capital y riesgo son AUTOMÁTICOS) ─────
    c_n, c_conf = st_module.columns(2)
    with c_n:
        top_n = st_module.slider("Nº señales", 1, 10, 3)
    with c_conf:
        min_conf = st_module.slider("Confianza mínima", 0, 90, 40, step=5) / 100.0
    st_module.caption(
        f"Capital automático (paper): **${bridge.equity():,.2f}** · "
        "riesgo por trade: Kelly con techo 10% · 100% del capital disponible."
    )
    if st_module.button("🛑 Cerrar TODO (kill switch)"):
        n = bridge.kill_switch()
        st_module.toast(f"✅ {n} trade(s) cerrado(s)")
        st_module.rerun()

    # ── Métricas ────────────────────────────────────────
    _signals, stats, _tracked = bridge.snapshot()
    m1, m2, m3, m4 = st_module.columns(4)
    m1.metric("💵 Capital (auto)", f"${bridge.equity():,.2f}")
    m2.metric("Pares rastreados", stats.tracked)
    m3.metric("Señales en buffer", stats.signals_emitted)
    m4.metric("Eventos", f"{stats.events_processed:,}")

    with st_module.expander("🔍 Diagnóstico técnico", expanded=False):
        d = stats.diagnostics
        st_module.markdown(
            f"evals `{d.get('evals', 0)}` · agg `{d.get('agg', 0)}` · "
            f"depth `{d.get('depth', 0)}` · funding `{d.get('funding', 0)}` · "
            f"force `{d.get('force', 0)}` · reconexiones `{stats.reconnects}`"
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
        if st_module.button("🔄 Reiniciar radar"):
            _get_bridge.clear()
            st_module.rerun()

    st_module.markdown("---")
    st_module.markdown("### 🚨 Mejores señales")

    ranked = bridge.top_signals(limit=max(top_n, 10))
    ranked = [r for r in ranked if r.signal.confidence >= min_conf][:top_n]

    if not ranked:
        st_module.info("Sin señales que cumplan los filtros. Ajustá confianza o esperá.")
    else:
        for i, r in enumerate(ranked, start=1):
            sizing = bridge.suggest_sizing(r.signal)
            st_module.markdown(
                _card_html(r, i, sizing.risk_usdt, sizing.risk_pct,
                           sizing.kelly_multiplier),
                unsafe_allow_html=True,
            )
            mode = "paper" if bridge.is_dry_run() else "LIVE"
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

    with st_module.expander("📋 Ruta para operar (manual)", expanded=True):
        for icon, step in _RUTA:
            st_module.markdown(f"{icon} {step}")
        st_module.caption(
            "El radar no garantiza ganancias: reduce el ruido y te da contexto. "
            "El edge está en la constancia y el control de riesgo, no en acertar siempre."
        )

    with st_module.expander("📊 Paper (dry-run)", expanded=False):
        _render_paper(st_module, bridge)

    with st_module.expander("🔧 Qué se puede cambiar", expanded=False):
        st_module.markdown(
            "- **Capital / riesgo / nº de señales / confianza mínima**: acá arriba.\n"
            "- **Pesos del ranking** (`w_confidence`, `w_reasons`, `w_liquidity`, "
            "`w_freshness`): `config/settings.yaml` → `scanner`.\n"
            "- **Filtro de basura**: `min_quote_volume_24h` y `max_symbols`.\n"
            "- **Umbrales de anomalías**: `obi_extreme`, `volume_zscore`, "
            "`delta_extreme`, `funding_extreme`, liquidaciones."
        )
