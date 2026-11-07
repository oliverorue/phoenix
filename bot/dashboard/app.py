"""Phoenix Monitor — dashboard (monitor-only).

Dashboard limpio: solo el Radar global de anomalías. La UI legacy de
trading (bot, posiciones, backtest, optimizador, P&L) fue eliminada en el
pivote a monitor de señales.
"""

import sys
from pathlib import Path

# Streamlit ejecuta este archivo desde bot/dashboard/, no desde la raíz.
# Agregamos la raíz del repo para que `import bot` funcione.
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st  # noqa: E402

st.set_page_config(
    page_title="Phoenix Monitor",
    page_icon="📡",
    layout="wide",
    initial_sidebar_state="collapsed",
)

from bot.dashboard.scanner_page import render_scanner_page  # noqa: E402

render_scanner_page(st)
