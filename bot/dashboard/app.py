"""Phoenix Monitor — dashboard (monitor-only).

Dashboard limpio: solo el Radar global de anomalías. La UI legacy de
trading (bot, posiciones, backtest, optimizador, P&L) fue eliminada en el
pivote a monitor de señales.
"""

import streamlit as st

st.set_page_config(
    page_title="Phoenix Monitor",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from bot.dashboard.scanner_page import render_scanner_page  # noqa: E402

render_scanner_page(st)
