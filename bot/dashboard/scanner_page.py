"""
Página del Radar para el dashboard Streamlit — "idiot-proof".

Conecta el scanner asincrónico (vía :mod:`bot.dashboard.scanner_bridge`) con
tarjetas gigantes LONG/SHORT, el dimensionamiento Kelly y una ruta operativa
para operar manualmente.

Para integrarlo en ``app.py``:
    from bot.dashboard.scanner_page import render_scanner_page
    render_scanner_page(st)
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from bot.dashboard.scanner_bridge import ScannerBridge
from bot.scanner.models import Signal

_RUTA = [
    ("🎯", "Esperá una tarjeta LONG/SHORT con confianza ≥ 60% y 2+ motivos alineados."),
    ("📉", "Revisá la tendencia 1h: si va en contra de la dirección, descartá la señal."),
    ("🕯️", "Confirmá en la vela 15m que no estás comprando/vendiendo justo en el pico."),
    ("📐", "Tomá el «USDT a arriesgar» del dashboard y calculá cantidad = riesgo ÷ distancia al SL."),
    ("🛒", "Entrá con orden límite (menos fees que market) y colocá el SL apenas se llene."),
    ("🏁", "TP1 parcial (50–65%), mové SL a breakeven, dejá correr el resto al TP2."),
    ("📓", "Anotá el trade en el journal. Nunca arriesgues más del 1–2% por operación."),
]


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


def _card_html(sig: Signal, risk_usdt: float, risk_pct: float,
               kelly: float) -> str:
    bg = "#16a34a" if sig.direction == "LONG" else "#dc2626"
    arrow = "▲ LONG" if sig.direction == "LONG" else "▼ SHORT"
    reasons = " · ".join(sig.reasons)
    return (
        f'<div style="background:{bg};color:#fff;border-radius:18px;'
        f'padding:22px;margin:14px 0;box-shadow:0 8px 24px rgba(0,0,0,.25)">'
        f'<h2 style="margin:0 0 6px;font-size:1.9rem">{arrow} — {sig.symbol}</h2>'
        f'<div style="font-size:1.05rem;opacity:.95">{reasons}</div>'
        f'<div style="margin-top:10px;font-size:1.1rem">Precio: <b>{sig.price}</b>'
        f' &nbsp;·&nbsp; Confianza: <b>{sig.confidence * 100:.0f}%</b></div>'
        f'<h3 style="margin:14px 0 0;font-size:1.6rem">Arriesgar: '
        f'${risk_usdt:,.2f} USDT</h3>'
        f'<div style="opacity:.85">{risk_pct:.3f}% del capital · Kelly ×{kelly}</div>'
        f'</div>'
    )


def _render_history(st_module: Any, bridge: ScannerBridge) -> None:
    """Historial persistido de señales + registro manual de resultados."""
    s = bridge.store.stats()
    c1, c2, c3 = st_module.columns(3)
    c1.metric("Resultados registrados", s["total"])
    c2.metric("Win rate", f"{s['win_rate'] * 100:.0f}%")
    c3.metric("PnL", f"${s['pnl']:+.2f}")

    rows = bridge.store.recent(30)
    if not rows:
        st_module.caption("Todavía no hay señales persistidas.")
        return

    options = {
        f"#{r['id']} {r['symbol']} {r['direction']} "
        f"({r['confidence'] * 100:.0f}%)": r["id"]
        for r in rows
    }
    sel = st_module.selectbox("Señal", list(options.keys()))
    col_o, col_p = st_module.columns(2)
    outcome = col_o.selectbox("Resultado", ["WIN", "LOSS", "SKIP"])
    pnl = col_p.number_input("PnL (USDT)", value=0.0, step=0.1)
    if st_module.button("Registrar resultado"):
        sid = options[sel]
        bridge.store.set_outcome(sid, outcome, pnl)
        if outcome in ("WIN", "LOSS"):
            bridge.risk.record_trade(pnl)
        st_module.toast(f"✅ {outcome} registrado para #{sid}")
        st_module.rerun()


def render_scanner_page(st_module: Any = st) -> None:
    """Renderiza el radar completo. Importar en ``app.py``."""
    st_module.title("📡 Radar Global — Binance USDⓈ-M Futures")
    st_module.caption("Monitor-only: muestra anomalías, no ejecuta órdenes.")

    # No arrancar el hilo del scanner en import/tests (bare mode).
    if not _in_streamlit():
        st_module.info(
            "Radar disponible al ejecutar `streamlit run bot/dashboard/app.py`."
        )
        return

    bridge = _get_bridge()

    col_cap, col_risk = st_module.columns(2)
    with col_cap:
        capital = st_module.number_input(
            "💵 Capital base (USDT)", min_value=10.0, value=500.0, step=10.0,
        )
    with col_risk:
        base_risk_pct = st_module.number_input(
            "🎚️ Riesgo base por trade (%)", min_value=0.1, max_value=3.0,
            value=1.0, step=0.1,
        ) / 100.0

    signals, stats, _tracked = bridge.snapshot()

    c1, c2, c3, c4 = st_module.columns(4)
    c1.metric("Pares rastreados", stats.tracked)
    c2.metric("Señales emitidas", stats.signals_emitted)
    c3.metric("Eventos", f"{stats.events_processed:,}")
    c4.metric("Reconexiones", stats.reconnects)

    d = stats.diagnostics
    st_module.markdown(
        f"🔍 Diagnóstico — evals `{d.get('evals', 0)}` · agg `{d.get('agg', 0)}` · "
        f"depth `{d.get('depth', 0)}` · funding `{d.get('funding', 0)}` · force `{d.get('force', 0)}`"
    )
    st_module.markdown(
        f"🎯 Hits — OBI `{d.get('obi_hits', 0)}` · delta `{d.get('delta_hits', 0)}` · "
        f"spike `{d.get('spike_hits', 0)}` · funding `{d.get('funding_hits', 0)}` · "
        f"cascade `{d.get('cascade_hits', 0)}`"
    )
    sc = stats.stream_counts
    if sc:
        st_module.markdown(
            "📊 Streams — " + " · ".join(f"`{k}` {v}" for k, v in sorted(sc.items()))
        )

    if stats.last_error:
        st_module.warning(f"Último error: {stats.last_error}")
    if not bridge.is_running():
        st_module.error("El scanner no está corriendo.")
    if st_module.button("🔄 Reiniciar radar"):
        _get_bridge.clear()
        st_module.rerun()

    st_module.markdown("---")
    st_module.markdown("### 🚨 Señales")

    if not signals:
        st_module.info("Esperando anomalías en el mercado…")
    else:
        for sig in reversed(signals[-20:]):
            sizing = bridge.risk.suggest(
                capital, sig, base_risk_pct=base_risk_pct,
            )
            st_module.markdown(
                _card_html(
                    sig, sizing.risk_usdt, sizing.risk_pct,
                    sizing.kelly_multiplier,
                ),
                unsafe_allow_html=True,
            )

    with st_module.expander("📋 Ruta para operar (manual)", expanded=True):
        for icon, step in _RUTA:
            st_module.markdown(f"{icon} {step}")
        st_module.caption(
            "El radar no garantiza ganancias: reduce el ruido y te da contexto. "
            "El edge está en la constancia y el control de riesgo, no en acertar siempre."
        )

    with st_module.expander("📒 Historial de señales", expanded=False):
        _render_history(st_module, bridge)

    with st_module.expander("🔧 Qué se puede cambiar", expanded=False):
        st_module.markdown(
            "- **Capital / riesgo base**: acá arriba.\n"
            "- **Umbrales de anomalías** (OBI, z-score, funding, liquidaciones): "
            "`config/settings.yaml` → `scanner`.\n"
            "- **Filtro de basura**: `min_quote_volume_24h` y `max_symbols` en esa sección.\n"
            "- **Niveles de book**: `depth_levels` (5/10/20) y `depth_symbols_limit`."
        )
