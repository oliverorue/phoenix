"""
🔥 Phoenix Scalper Dashboard — Interfaz profesional de trading en tiempo real.
Velas reales, animaciones, glassmorphism, métricas avanzadas, charts interactivos.
"""

from typing import Any, Optional
import streamlit as st
import pandas as pd
import numpy as np
from pathlib import Path
import sys
import time
import threading
from datetime import datetime, timezone

import plotly.graph_objects as go
import plotly.express as px
from plotly.subplots import make_subplots

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from bot.dashboard.shared_state import bot_state
from bot.dashboard.bot_controller import controller
from config.loader import (
    BOT_NAME, BOT_VERSION, BOT_EMOJI, LOG_DIR,
    SUPPORTED_COINS, FASES_ESCALAMIENTO,
)
from bot.core.database import DatabaseManager

DATA_DIR = ROOT_DIR / "data"

# ─────────────────────────────────────────────────────────────
# CSS BESTIAL — Animaciones, glassmorphism, neones, partículas
# ─────────────────────────────────────────────────────────────
CUSTOM_CSS = """
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700;800;900&family=JetBrains+Mono:wght@400;700&display=swap');

    /* ── Fondo animado con partículas ── */
    .stApp {
        background: #0a0e17 !important;
        font-family: 'Inter', sans-serif;
        color: #e6edf3;
    }
    .stApp::before {
        content: '';
        position: fixed;
        top: 0; left: 0; width: 100%; height: 100%;
        background:
            radial-gradient(ellipse at 20% 50%, rgba(56, 139, 253, 0.08) 0%, transparent 50%),
            radial-gradient(ellipse at 80% 20%, rgba(121, 40, 202, 0.06) 0%, transparent 50%),
            radial-gradient(ellipse at 50% 80%, rgba(12, 198, 112, 0.04) 0%, transparent 50%);
        pointer-events: none;
        z-index: 0;
    }

    #MainMenu, footer, header, .stDeployButton, .stAppToolbar { display: none !important; }

    /* ── Título con gradiente animado ── */
    .main-title {
        font-size: 1.6rem;
        font-weight: 800;
        background: linear-gradient(135deg, #58a6ff, #bc8cff, #3fb950);
        background-size: 300% 300%;
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        animation: gradientShift 4s ease infinite;
        margin: 0.2rem 0;
        letter-spacing: -0.5px;
    }
    @keyframes gradientShift {
        0% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
        100% { background-position: 0% 50%; }
    }

    /* ── Glassmorphism Cards ── */
    .glass-card {
        background: rgba(22, 27, 34, 0.85);
        backdrop-filter: blur(12px);
        -webkit-backdrop-filter: blur(12px);
        border: 1px solid rgba(48, 54, 61, 0.6);
        border-radius: 12px;
        padding: 1rem;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        overflow: hidden;
    }
    .glass-card::before {
        content: '';
        position: absolute;
        top: 0; left: -100%;
        width: 100%; height: 100%;
        background: linear-gradient(90deg, transparent, rgba(255,255,255,0.03), transparent);
        transition: left 0.6s;
    }
    .glass-card:hover::before { left: 100%; }
    .glass-card:hover {
        border-color: rgba(88, 166, 255, 0.4);
        transform: translateY(-2px);
        box-shadow: 0 8px 30px rgba(0,0,0,0.3);
    }

    /* ── Métricas con brillo ── */
    .metric-value {
        font-size: 1.6rem;
        font-weight: 800;
        font-family: 'JetBrains Mono', monospace;
        letter-spacing: -0.5px;
        transition: color 0.3s;
    }
    .metric-label {
        font-size: 0.6rem;
        color: #8b949e;
        text-transform: uppercase;
        letter-spacing: 1.5px;
        margin-top: 4px;
        font-weight: 600;
    }
    .metric-positive { color: #3fb950; text-shadow: 0 0 20px rgba(63,185,80,0.3); }
    .metric-negative { color: #f85149; text-shadow: 0 0 20px rgba(248,81,73,0.3); }
    .metric-neutral  { color: #e6edf3; }
    .metric-warning  { color: #d29922; text-shadow: 0 0 20px rgba(210,153,34,0.2); }

    /* ── Indicador de estado vivo ── */
    .status-dot {
        display: inline-block;
        width: 10px; height: 10px;
        border-radius: 50%;
        margin-right: 8px;
        animation: pulse 1.5s ease-in-out infinite;
    }
    .status-dot.on { background: #3fb950; box-shadow: 0 0 12px rgba(63,185,80,0.6); }
    .status-dot.off { background: #f85149; box-shadow: 0 0 12px rgba(248,81,73,0.6); animation: none; }
    .status-dot.warn { background: #d29922; box-shadow: 0 0 12px rgba(210,153,34,0.4); }
    @keyframes pulse {
        0%, 100% { opacity: 1; transform: scale(1); }
        50% { opacity: 0.6; transform: scale(0.85); }
    }

    /* ── Sidebar ── */
    section[data-testid="stSidebar"] {
        background: rgba(13, 17, 23, 0.95) !important;
        border-right: 1px solid rgba(48, 54, 61, 0.5);
        backdrop-filter: blur(20px);
    }
    section[data-testid="stSidebar"] .stButton button {
        border-radius: 8px !important;
        font-weight: 700 !important;
        letter-spacing: 0.5px;
        transition: all 0.2s;
    }
    section[data-testid="stSidebar"] .stButton button[kind="primary"] {
        background: linear-gradient(135deg, #238636, #2ea043) !important;
        border: none !important;
        box-shadow: 0 4px 15px rgba(35,134,54,0.3);
    }
    section[data-testid="stSidebar"] .stButton button[kind="primary"]:hover {
        transform: translateY(-1px);
        box-shadow: 0 6px 20px rgba(35,134,54,0.4);
    }

    /* ── Línea divisoria con brillo ── */
    hr {
        border: none !important;
        height: 1px !important;
        background: linear-gradient(90deg, transparent, rgba(48,54,61,0.9), transparent) !important;
        margin: 1.5rem 0 !important;
    }

    /* ── Scrollbar personalizada ── */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #0d1117; }
    ::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #484f58; }

    /* ── Badge de fase ── */
    .phase-badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 20px;
        font-size: 0.7rem;
        font-weight: 700;
        letter-spacing: 0.5px;
    }
    .phase-0 { background: rgba(210,153,34,0.2); color: #d29922; border: 1px solid rgba(210,153,34,0.3); }
    .phase-1 { background: rgba(88,166,255,0.15); color: #58a6ff; border: 1px solid rgba(88,166,255,0.3); }
    .phase-2 { background: rgba(63,185,80,0.15); color: #3fb950; border: 1px solid rgba(63,185,80,0.3); }
    .phase-3 { background: rgba(121,40,202,0.15); color: #bc8cff; border: 1px solid rgba(121,40,202,0.3); }
    .phase-4 { background: rgba(248,81,73,0.15); color: #f85149; border: 1px solid rgba(248,81,73,0.3); }

    /* ── Grid background para charts ── */
    .js-plotly-plot .plotly .gridlayer .grid { stroke: rgba(48,54,61,0.3) !important; }

    /* ── Dataframe custom ── */
    .stDataFrame { border-radius: 8px !important; overflow: hidden; }
    .stDataFrame thead tr th {
        background: rgba(22,27,34,0.9) !important;
        color: #8b949e !important;
        font-size: 0.7rem !important;
        text-transform: uppercase;
        letter-spacing: 1px;
    }
    .stDataFrame tbody tr:hover { background: rgba(88,166,255,0.05) !important; }
</style>
""".strip()


# ─────────────────────────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────────────────────────

def load_market_data(symbol: str, timeframe: str = "15", limit: int = 200) -> pd.DataFrame:
    """Carga velas desde los CSVs del directorio data/."""
    data_dir = DATA_DIR
    files = sorted(data_dir.glob(f"{symbol}_{timeframe}_*.csv"))
    if not files:
        return pd.DataFrame()
    dfs = []
    for f in files:
        try:
            df = pd.read_csv(f, parse_dates=["timestamp"])
            # Normalizar timestamps: convertir tz-aware → tz-naive para evitar
            # "Cannot compare tz-naive and tz-aware timestamps" al ordenar.
            if hasattr(df["timestamp"].dtype, "tz") and df["timestamp"].dtype.tz is not None:  # type: ignore[attr-defined,union-attr]
                df["timestamp"] = df["timestamp"].dt.tz_localize(None)
            dfs.append(df)
        except Exception:
            continue
    if not dfs:
        return pd.DataFrame()
    combined = pd.concat(dfs, ignore_index=True)
    combined = combined.drop_duplicates(subset=["timestamp"]).sort_values("timestamp")
    return combined.tail(limit)


def get_data_files_summary() -> list[dict[str, Any]]:
    """Lista los archivos de datos disponibles con metadatos."""
    files = sorted(DATA_DIR.glob("*.csv"))
    summary = []
    for f in files:
        parts = f.stem.split("_")
        symbol = parts[0] if len(parts) > 0 else "?"
        tf = parts[1] if len(parts) > 1 else "?"
        summary.append({
            "symbol": symbol,
            "timeframe": tf,
            "file": f.name,
            "size_kb": round(f.stat().st_size / 1024, 1),
        })
    return summary


def create_phase_badge(fase: int) -> str:
    colors = ["phase-0", "phase-1", "phase-2", "phase-3", "phase-4"]
    cls = colors[min(fase, len(colors)-1)]
    return f'<span class="phase-badge {cls}">FASE {fase}</span>'


def format_time_ago(iso_str: str) -> str:
    if not iso_str:
        return "Nunca"
    try:
        dt = datetime.fromisoformat(iso_str)
        diff = datetime.now(timezone.utc).replace(tzinfo=None) - dt.replace(tzinfo=None)
        secs = int(diff.total_seconds())
        if secs < 5:
            return "Ahora"
        elif secs < 60:
            return f"hace {secs}s"
        elif secs < 3600:
            return f"hace {secs//60}m"
        else:
            return f"hace {secs//3600}h"
    except Exception:
        return iso_str


# ─────────────────────────────────────────────────────────────
# CHART BUILDERS (Plotly)
# ─────────────────────────────────────────────────────────────

def create_candlestick_chart(df: pd.DataFrame, title: str = "") -> go.Figure:
    """Velas reales + SMA + volumen."""
    if df.empty:
        return go.Figure()
    
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.75, 0.25],
    )
    
    # Velas
    fig.add_trace(go.Candlestick(
        x=df["timestamp"],
        open=df["open"], high=df["high"], low=df["low"], close=df["close"],
        name="OHLC",
        increasing_line_color="#3fb950",
        decreasing_line_color="#f85149",
        increasing_fillcolor="rgba(63,185,80,0.2)",
        decreasing_fillcolor="rgba(248,81,73,0.2)",
        line=dict(width=0.8),
    ), row=1, col=1)
    
    # SMA 7 y 25
    for period, color in [(7, "#58a6ff"), (25, "#bc8cff")]:
        sma = df["close"].rolling(period).mean()
        fig.add_trace(go.Scatter(
            x=df["timestamp"], y=sma,
            name=f"SMA {period}",
            line=dict(color=color, width=1.2),
            opacity=0.8,
        ), row=1, col=1)
    
    # Volumen
    colors_vol = ["#3fb950" if df["close"].iloc[i] >= df["open"].iloc[i] else "#f85149" for i in range(len(df))]
    fig.add_trace(go.Bar(
        x=df["timestamp"], y=df["volume"],
        name="Volumen",
        marker_color=colors_vol,
        opacity=0.6,
    ), row=2, col=1)
    
    # Layout
    fig.update_layout(
        title=dict(
            text=title,
            font=dict(size=16, color="#e6edf3", family="Inter"),
            x=0.5, xanchor="center",
        ),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=500,
        margin=dict(l=30, r=20, t=50, b=20),
        showlegend=True,
        legend=dict(
            orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
            font=dict(size=10, color="#8b949e"),
            bgcolor="rgba(0,0,0,0)",
        ),
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        font=dict(color="#e6edf3"),
    )
    
    fig.update_xaxes(
        gridcolor="rgba(48,54,61,0.3)",
        zerolinecolor="rgba(48,54,61,0.5)",
        tickfont=dict(size=10, color="#8b949e"),
        rangeslider=dict(visible=False),
    )
    fig.update_yaxes(
        gridcolor="rgba(48,54,61,0.3)",
        zerolinecolor="rgba(48,54,61,0.5)",
        tickfont=dict(size=10, color="#8b949e"),
        row=1, col=1,
    )
    fig.update_yaxes(
        gridcolor="rgba(48,54,61,0.3)",
        tickfont=dict(size=9, color="#8b949e"),
        row=2, col=1,
    )
    
    return fig


def create_rsi_gauge(rsi_value: float) -> go.Figure:
    """RSI gauge con diseño bestial."""
    if rsi_value == 0:
        rsi_value = 50
    
    if rsi_value <= 30:
        color = "#3fb950"
        status = "🟢 SOBREVENDIDO"
    elif rsi_value >= 70:
        color = "#f85149"
        status = "🔴 SOBRECOMPRADO"
    else:
        color = "#d29922"
        status = "🟡 NEUTRAL"
    
    fig = go.Figure(go.Indicator(
        mode="gauge+number+delta",
        value=rsi_value,
        title=dict(
            text=f"RSI 15m<br><span style='font-size:0.7rem;color:{color}'>{status}</span>",
            font=dict(size=14, color="#e6edf3", family="Inter"),
        ),
        number=dict(
            font=dict(size=28, color=color, family="JetBrains Mono"),
            suffix="",
        ),
        delta=dict(
            reference=50,
            increasing=dict(color="#f85149"),
            decreasing=dict(color="#3fb950"),
        ),
        gauge=dict(
            axis=dict(range=[0, 100], tickwidth=1, tickcolor="#8b949e",
                      tickfont=dict(size=10, color="#8b949e")),
            bar=dict(color=color, thickness=0.5),
            bgcolor="rgba(22,27,34,0.5)",
            borderwidth=1,
            bordercolor="rgba(48,54,61,0.5)",
            steps=[
                dict(range=[0, 30], color="rgba(63,185,80,0.12)"),
                dict(range=[30, 70], color="rgba(210,153,34,0.08)"),
                dict(range=[70, 100], color="rgba(248,81,73,0.12)"),
            ],
            threshold=dict(
                line=dict(color="white", width=3),
                thickness=0.75,
                value=rsi_value,
            ),
        ),
    ))
    fig.update_layout(
        height=240,
        margin=dict(t=60, b=10, l=30, r=30),
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e6edf3"),
    )
    return fig


def create_winrate_donut(wins: int, losses: int) -> go.Figure:
    """Donut de win rate."""
    total = wins + losses
    if total == 0:
        return go.Figure()
    win_rate = wins / total * 100
    
    fig = go.Figure(go.Pie(
        values=[wins, losses],
        labels=["Ganadas", "Perdidas"],
        marker=dict(colors=["#3fb950", "#f85149"]),
        hole=0.7,
        textinfo="none",
        hovertemplate="<b>%{label}</b>: %{value} (%{percent})<extra></extra>",
        domain=dict(x=[0, 1], y=[0, 1]),
    ))
    fig.update_layout(
        annotations=[dict(
            text=f"{win_rate:.1f}%<br><span style='font-size:0.5rem'>Win Rate</span>",
            x=0.5, y=0.5,
            font=dict(size=20, color="#e6edf3", family="JetBrains Mono"),
            showarrow=False,
        )],
        height=180,
        margin=dict(t=10, b=10, l=10, r=10),
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e6edf3"),
        showlegend=False,
    )
    return fig


def create_equity_curve(df_trades: pd.DataFrame) -> go.Figure:
    """Equity curve con drawdown sombreado."""
    if df_trades.empty:
        return go.Figure()
    
    df = df_trades.sort_values("exit_time").reset_index(drop=True)
    df["cum_pnl"] = df["pnl_usdt"].cumsum()
    df["peak"] = df["cum_pnl"].cummax()
    df["drawdown"] = df["cum_pnl"] - df["peak"]
    
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])
    
    # Equity
    equity_color = "#3fb950" if df["cum_pnl"].iloc[-1] >= 0 else "#f85149"
    fig.add_trace(go.Scatter(
        x=df["exit_time"], y=df["cum_pnl"],
        name="Equity",
        line=dict(color=equity_color, width=2),
        fill="tozeroy",
        fillcolor=f"rgba(63,185,80,0.08)" if df["cum_pnl"].iloc[-1] >= 0 else "rgba(248,81,73,0.08)",
    ), row=1, col=1)
    
    # Peak
    fig.add_trace(go.Scatter(
        x=df["exit_time"], y=df["peak"],
        name="Peak",
        line=dict(color="#58a6ff", width=1, dash="dot"),
        opacity=0.5,
    ), row=1, col=1)
    
    # Drawdown
    fig.add_trace(go.Bar(
        x=df["exit_time"], y=df["drawdown"],
        name="Drawdown",
        marker_color="#f85149",
        opacity=0.5,
    ), row=2, col=1)
    
    fig.update_layout(
        title=dict(text="📈 Equity Curve", font=dict(size=14, color="#e6edf3"), x=0.5, xanchor="center"),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=400,
        margin=dict(l=30, r=20, t=40, b=20),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                    font=dict(size=9, color="#8b949e"), bgcolor="rgba(0,0,0,0)"),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="rgba(48,54,61,0.3)", tickfont=dict(size=9, color="#8b949e"))
    fig.update_yaxes(gridcolor="rgba(48,54,61,0.3)", tickfont=dict(size=9, color="#8b949e"))
    
    return fig


def create_pnl_distribution(df_trades: pd.DataFrame, var_95: float = 0) -> go.Figure:
    """Histograma de distribución de P&L."""
    if df_trades.empty or "pnl_pct" not in df_trades.columns:
        return go.Figure()
    
    fig = px.histogram(
        df_trades, x="pnl_pct", nbins=40,
        title="Distribución de Retornos (%)",
        color_discrete_sequence=["#58a6ff"],
        opacity=0.7,
    )
    
    # VaR line
    if var_95 != 0:
        fig.add_vline(
            x=var_95 * 100, line_dash="dash", line_color="#f85149", line_width=2,
            annotation_text=f"VaR 95%: {var_95*100:.2f}%",
            annotation_font=dict(size=10, color="#f85149"),
        )
    
    # Zero line
    fig.add_vline(x=0, line_color="#3fb950", line_width=1, opacity=0.5)
    
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=300,
        margin=dict(l=30, r=20, t=40, b=20),
        xaxis_title="Retorno (%)",
        yaxis_title="Frecuencia",
        font=dict(color="#e6edf3"),
        bargap=0.05,
    )
    fig.update_xaxes(gridcolor="rgba(48,54,61,0.3)", tickfont=dict(size=9, color="#8b949e"))
    fig.update_yaxes(gridcolor="rgba(48,54,61,0.3)", tickfont=dict(size=9, color="#8b949e"))
    
    return fig


def create_portfolio_pie(symbols_data: list[dict[str, Any]]) -> go.Figure:
    """Gráfico de asignación del portfolio."""
    if not symbols_data:
        return go.Figure()
    
    labels = [s.get("symbol", "?") for s in symbols_data]
    values = [s.get("allocation_pct", 0) * 100 for s in symbols_data]
    colors = px.colors.qualitative.Set2[:len(labels)]
    
    fig = go.Figure(go.Pie(
        labels=labels, values=values,
        marker=dict(colors=colors, line=dict(color="#0d1117", width=2)),
        textinfo="label+percent",
        textfont=dict(size=12, color="#e6edf3"),
        hovertemplate="<b>%{label}</b><br>Asignación: %{percent}<extra></extra>",
        hole=0.4,
        rotation=90,
    ))
    fig.update_layout(
        title=dict(text="💼 Asignación", font=dict(size=14, color="#e6edf3"), x=0.5, xanchor="center"),
        height=300,
        margin=dict(t=40, b=10, l=10, r=10),
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="#e6edf3"),
        showlegend=False,
    )
    return fig


def create_trade_timeline(df_trades: pd.DataFrame) -> go.Figure:
    """Timeline de trades: scatter plot precio entrada/salida."""
    if df_trades.empty:
        return go.Figure()
    
    df = df_trades.dropna(subset=["entry_time", "exit_time"]).copy()
    if df.empty:
        return go.Figure()
    
    colors = ["#3fb950" if pnl > 0 else "#f85149" for pnl in df["pnl_usdt"]]
    sizes = [abs(pnl) * 5 + 8 for pnl in df["pnl_usdt"]]
    
    fig = go.Figure()
    
    # Líneas conectando entry → exit
    for _, row in df.iterrows():
        color = "#3fb950" if row["pnl_usdt"] > 0 else "#f85149"
        fig.add_trace(go.Scatter(
            x=[row["entry_time"], row["exit_time"]],
            y=[row["entry_price"], row["exit_price"]],
            mode="lines",
            line=dict(color=color, width=1),
            opacity=0.3,
            showlegend=False,
            hoverinfo="skip",
        ))
    
    # Puntos de entrada
    fig.add_trace(go.Scatter(
        x=df["entry_time"], y=df["entry_price"],
        mode="markers",
        name="Entry",
        marker=dict(color=colors, size=sizes, symbol="circle",
                    line=dict(color="white", width=0.5)),
        hovertemplate="<b>Entry</b>: %{y:.4f}<br>%{x}<extra></extra>",
    ))
    
    # Puntos de salida
    fig.add_trace(go.Scatter(
        x=df["exit_time"], y=df["exit_price"],
        mode="markers",
        name="Exit",
        marker=dict(color=colors, size=sizes, symbol="x",
                    line=dict(color="white", width=0.5)),
        hovertemplate="<b>Exit</b>: %{y:.4f}<br>PnL: %{customdata:.2f} USDT<extra></extra>",
        customdata=df["pnl_usdt"],
    ))
    
    fig.update_layout(
        title=dict(text="📊 Trade Timeline", font=dict(size=14, color="#e6edf3"), x=0.5, xanchor="center"),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=350,
        margin=dict(l=30, r=20, t=40, b=20),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                    font=dict(size=9, color="#8b949e"), bgcolor="rgba(0,0,0,0)"),
        hovermode="closest",
    )
    fig.update_xaxes(gridcolor="rgba(48,54,61,0.3)", tickfont=dict(size=9, color="#8b949e"))
    fig.update_yaxes(gridcolor="rgba(48,54,61,0.3)", tickfont=dict(size=9, color="#8b949e"))
    
    return fig


def create_monthly_performance(df_trades: pd.DataFrame) -> go.Figure:
    """Heatmap mensual de rendimiento."""
    if df_trades.empty or "exit_time" not in df_trades.columns:
        return go.Figure()
    
    df = df_trades.copy()
    df["exit_time"] = pd.to_datetime(df["exit_time"])
    df["month"] = df["exit_time"].dt.month
    df["year"] = df["exit_time"].dt.year
    
    monthly = df.groupby(["year", "month"])["pnl_usdt"].sum().reset_index()  # type: ignore[union-attr]
    if monthly.empty:
        return go.Figure()
    
    # Crear matriz para heatmap
    years = sorted(monthly["year"].unique())
    
    z = np.full((len(years), 12), np.nan)
    for _, row in monthly.iterrows():
        yi = years.index(row["year"])
        mi = row["month"] - 1
        z[yi][mi] = row["pnl_usdt"]
    
    month_labels = ["Ene", "Feb", "Mar", "Abr", "May", "Jun",
                    "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]
    
    fig = go.Figure(go.Heatmap(
        z=z,
        x=month_labels,
        y=[str(y) for y in years],
        colorscale=[[0, "#f85149"], [0.5, "#0d1117"], [1, "#3fb950"]],
        zmid=0,
        text=np.where(np.isnan(z), "", [f"${v:+.1f}" if not np.isnan(v) else "" for v in z.flatten()]),
        texttemplate="%{text}",
        textfont=dict(size=10, color="#e6edf3"),
        hovertemplate="<b>%{y} - %{x}</b><br>PnL: $%{z:+.2f}<extra></extra>",
        colorbar=dict(
            title=dict(text="PnL ($)", font=dict(size=10, color="#8b949e")),
            tickfont=dict(size=9, color="#8b949e"),
        ),
    ))
    
    fig.update_layout(
        title=dict(text="📅 Rendimiento Mensual", font=dict(size=14, color="#e6edf3"), x=0.5, xanchor="center"),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=250,
        margin=dict(l=30, r=30, t=40, b=20),
        font=dict(color="#e6edf3"),
    )
    fig.update_xaxes(tickfont=dict(size=10, color="#8b949e"))
    fig.update_yaxes(tickfont=dict(size=10, color="#8b949e"))
    
    return fig


def create_symbol_performance(df_trades: pd.DataFrame) -> go.Figure:
    """Barras de rendimiento por símbolo."""
    if df_trades.empty or "symbol" not in df_trades.columns:
        return go.Figure()
    
    perf = df_trades.groupby("symbol").agg(
        PnL=("pnl_usdt", "sum"),
        Trades=("pnl_usdt", "count"),
        WinRate=("pnl_usdt", lambda x: (x > 0).mean() * 100),
    ).reset_index()
    
    colors = ["#3fb950" if pnl >= 0 else "#f85149" for pnl in perf["PnL"]]
    
    fig = go.Figure(go.Bar(
        x=perf["symbol"],
        y=perf["PnL"],
        marker_color=colors,
        text=[f"${v:+.2f}" for v in perf["PnL"]],
        textposition="outside",
        textfont=dict(size=12, color="#e6edf3", family="JetBrains Mono"),
        hovertemplate="<b>%{x}</b><br>PnL: $%{y:+.2f}<br>Trades: %{customdata[0]}<br>Win Rate: %{customdata[1]:.1f}%<extra></extra>",
        customdata=perf[["Trades", "WinRate"]].values,
    ))
    
    fig.update_layout(
        title=dict(text="🏆 Rendimiento por Símbolo", font=dict(size=14, color="#e6edf3"), x=0.5, xanchor="center"),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=300,
        margin=dict(l=30, r=20, t=40, b=20),
        font=dict(color="#e6edf3"),
        xaxis_title=None,
        yaxis_title="PnL (USDT)",
    )
    fig.update_xaxes(gridcolor="rgba(48,54,61,0.3)", tickfont=dict(size=11, color="#8b949e"))
    fig.update_yaxes(gridcolor="rgba(48,54,61,0.3)", tickfont=dict(size=9, color="#8b949e"))
    
    return fig


def create_live_equity_curve(equity_history: list[dict[str, Any]]) -> go.Figure:
    """Equity curve en tiempo real desde equity_history del shared state."""
    if not equity_history or len(equity_history) < 2:
        return go.Figure()

    timestamps = [e["timestamp"] for e in equity_history]
    equities = [e["equity"] for e in equity_history]
    balances = [e["balance"] for e in equity_history]

    final_equity = equities[-1]
    eq_color = "#3fb950" if final_equity >= balances[-1] else "#f85149"

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.05, row_heights=[0.7, 0.3])

    # Línea de Equity
    fig.add_trace(go.Scatter(
        x=timestamps, y=equities,
        name="Equity",
        line=dict(color=eq_color, width=2),
        fill="tozeroy",
        fillcolor=f"rgba(63,185,80,0.08)" if final_equity >= balances[-1] else "rgba(248,81,73,0.08)",
    ), row=1, col=1)

    # Línea de Balance (constante referencial)
    fig.add_trace(go.Scatter(
        x=timestamps, y=balances,
        name="Balance",
        line=dict(color="#58a6ff", width=1, dash="dot"),
        opacity=0.6,
    ), row=1, col=1)

    # Unrealized PnL como barras
    unrealized = [e["unrealized_pnl"] for e in equity_history]
    bar_color = ["#3fb950" if v >= 0 else "#f85149" for v in unrealized]
    fig.add_trace(go.Bar(
        x=timestamps, y=unrealized,
        name="PnL No Realizado",
        marker_color=bar_color,
        opacity=0.5,
    ), row=2, col=1)

    fig.update_layout(
        title=dict(text="💹 Equity en Tiempo Real", font=dict(size=14, color="#e6edf3"), x=0.5, xanchor="center"),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=400,
        margin=dict(l=30, r=20, t=40, b=20),
        showlegend=True,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                    font=dict(size=9, color="#8b949e"), bgcolor="rgba(0,0,0,0)"),
        hovermode="x unified",
    )
    fig.update_xaxes(gridcolor="rgba(48,54,61,0.3)", tickfont=dict(size=9, color="#8b949e"))
    fig.update_yaxes(gridcolor="rgba(48,54,61,0.3)", tickfont=dict(size=9, color="#8b949e"))

    return fig


def create_live_pnl_by_symbol(symbols_state: dict[str, Any]) -> go.Figure:
    """Barras de P&L no realizado por símbolo en vivo."""
    if not symbols_state:
        return go.Figure()

    symbols_data = []
    for sym, data in symbols_state.items():
        pos = data.get("position", {})
        upnl = pos.get("unrealized_pnl", 0) if isinstance(pos, dict) else 0
        symbols_data.append({"symbol": sym, "unrealized_pnl": upnl})

    if not symbols_data:
        return go.Figure()

    syms = [s["symbol"] for s in symbols_data]
    pnls = [s["unrealized_pnl"] for s in symbols_data]
    colors = ["#3fb950" if p >= 0 else "#f85149" for p in pnls]

    fig = go.Figure(go.Bar(
        x=syms, y=pnls,
        marker_color=colors,
        text=[f"${v:+.2f}" for v in pnls],
        textposition="outside",
        textfont=dict(size=12, color="#e6edf3", family="JetBrains Mono"),
        hovertemplate="<b>%{x}</b><br>PnL No Realizado: $%{y:+.2f}<extra></extra>",
    ))

    fig.update_layout(
        title=dict(text="📊 P&L No Realizado por Símbolo", font=dict(size=14, color="#e6edf3"), x=0.5, xanchor="center"),
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=250,
        margin=dict(l=30, r=20, t=40, b=20),
        font=dict(color="#e6edf3"),
        xaxis_title=None,
        yaxis_title="PnL (USDT)",
    )
    fig.update_xaxes(gridcolor="rgba(48,54,61,0.3)", tickfont=dict(size=11, color="#8b949e"))
    fig.update_yaxes(gridcolor="rgba(48,54,61,0.3)", tickfont=dict(size=9, color="#8b949e"))

    return fig


# ─────────────────────────────────────────────────────────────
# HELPER: Metric Cards con HTML
# ─────────────────────────────────────────────────────────────

def metric_card_html(label: str, value: str, extra: Optional[str] = None) -> str:
    """
    Renderiza una métrica como glass-card.
    IMPORTANTE: el $ se reemplaza con &#36; para evitar que
    Streamlit lo interprete como LaTeX en markdown.
    """
    value_safe = value.replace("$", "&#36;")
    extra_html = f'<div style="font-size:0.6rem;color:#8b949e;margin-top:4px">{extra}</div>' if extra else ""
    
    return f"""
    <div class="glass-card" style="text-align:center;padding:0.8rem 0.5rem;border-radius:12px">
        <div class="metric-label">{label}</div>
        <div class="metric-value">{value_safe}</div>
        {extra_html}
    </div>
    """


def _save_portfolio_to_yaml(portfolio: list[dict[str, Any]]) -> bool:
    """Guarda el portfolio actual en settings.yaml."""
    import yaml
    settings_path = ROOT_DIR / "config" / "settings.yaml"
    try:
        with open(settings_path, "r") as f:
            settings = yaml.safe_load(f)
        settings.setdefault("portfolio", {})["symbols"] = portfolio
        with open(settings_path, "w") as f:
            yaml.dump(settings, f, default_flow_style=False, allow_unicode=True)
        return True
    except Exception as e:
        st.toast(f"❌ Error al guardar portfolio: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# CONFIG STREAMLIT
# ─────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Phoenix Scalper Dashboard",
    page_icon="🔥",
    layout="wide",
    initial_sidebar_state="expanded",
)

db = DatabaseManager()
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown(f'<div class="main-title">{BOT_EMOJI} {BOT_NAME}</div>', unsafe_allow_html=True)
    st.caption(f"`{BOT_VERSION}` · Binance {'🧪 Testnet' if bot_state.get_state().get('testnet', True) else '🔴 Mainnet'}")
    
    st.markdown("---")
    
    # ── Controles ──
    col1, col2 = st.columns(2)
    with col1:
        if st.button("▶️ Iniciar", width="stretch"):
            if controller.start():
                st.toast("✅ Bot iniciado")
                st.rerun()
    with col2:
        if st.button("⏹️ Detener", width="stretch"):
            if controller.stop():
                st.toast("✅ Bot detenido")
                st.rerun()
    
    # ── Auto-refresh ──
    auto_refresh = st.checkbox("🔄 Auto-refresh", value=True)
    refresh_interval = st.slider("Intervalo (s)", 1, 15, 3, help="Tiempo entre actualizaciones automáticas")
    
    st.markdown("---")
    
    # ── Estado en vivo ──
    st.markdown("### ⚡ Estado en Vivo")
    s = bot_state.get_state()
    running = s.get("bot_running", False)
    
    dot_class = "on" if running else "off"
    status_text = "🟢 OPERANDO" if running else "🔴 DETENIDO"
    st.markdown(f'<div style="display:flex;align-items:center;gap:8px;font-size:0.9rem;font-weight:600">'
                f'<span class="status-dot {dot_class}"></span>{status_text}</div>',
                unsafe_allow_html=True)
    
    uptime = s.get("uptime_seconds", 0)
    if uptime > 0:
        st.caption(f"Uptime: {uptime//3600:.0f}h {(uptime%3600)//60:.0f}m {uptime%60:.0f}s")
    
    last_up = s.get("last_update", "")
    st.caption(f"Última actualización: {format_time_ago(last_up)}")
    
    st.markdown("---")
    
    # ── Datos disponibles ──
    st.markdown("### 📁 Datos Disponibles")
    data_files = get_data_files_summary()
    if data_files:
        symbols_in_data = set(d["symbol"] for d in data_files)
        for sym in sorted(symbols_in_data):
            tfs = [d["timeframe"] for d in data_files if d["symbol"] == sym]
            st.caption(f"{sym}: {', '.join(sorted(set(tfs)))}m")
    else:
        st.caption("Sin datos históricos")


# ─────────────────────────────────────────────────────────────
# MAIN — Estado global
# ─────────────────────────────────────────────────────────────

state = bot_state.get_state()
running = state.get("bot_running", False)

# ── Top metrics row ──
balance = state.get("balance", 0.0)
price = state.get("current_price", 0.0)
signal = state.get("last_signal", {})
signal_type = signal.get("type", None) or "—"
signal_score = signal.get("score", 0)
unrealised_pnl = state.get("unrealised_pnl_pct", 0.0)
unrealised_pnl_usdt = state.get("unrealised_pnl_usdt", 0.0)
equity = state.get("equity", 0.0)
daily_pnl = state.get("daily_pnl", 0.0)
active_syms = state.get("active_symbols", 0)
fase_actual = state.get("fase_actual", 0)

# Color coding
balance_color = "metric-positive" if balance > 100 else "metric-neutral"
pnl_color = ("metric-positive" if unrealised_pnl >= 0 else "metric-negative") if unrealised_pnl != 0 else "metric-neutral"
equity_color = "metric-positive" if equity >= balance else "metric-negative"
daily_pnl_color = "metric-positive" if daily_pnl >= 0 else "metric-negative"
signal_color = "metric-positive" if signal_type in ("LONG", "SHORT") else "metric-warning"

cols_top = st.columns(6)
with cols_top[0]:
    st.markdown(metric_card_html("Balance", f"${balance:.2f}", extra=f"Fase {fase_actual}"), unsafe_allow_html=True)
with cols_top[1]:
    eq_extra = f"{((equity/balance)-1)*100:+.2f}%" if balance > 0 else ""
    st.markdown(metric_card_html("Equity", f"${equity:.2f}", extra=eq_extra), unsafe_allow_html=True)
with cols_top[2]:
    st.markdown(metric_card_html("Precio", f"${price:.4f}", extra=state.get("symbol", "XRPUSDT")), unsafe_allow_html=True)
with cols_top[3]:
    pnl_text = f"{unrealised_pnl:+.2f}%" if unrealised_pnl != 0 else "0.00%"
    st.markdown(metric_card_html("PnL No Realizado", pnl_text, extra=f"${unrealised_pnl_usdt:+.2f}"), unsafe_allow_html=True)
with cols_top[4]:
    dpnl_text = f"${daily_pnl:+.2f}" if isinstance(daily_pnl, (int, float)) else "—"
    st.markdown(metric_card_html("Daily P&L", dpnl_text), unsafe_allow_html=True)
with cols_top[5]:
    st.markdown(metric_card_html("Señal", str(signal_type), extra=f"Score: {signal_score}"), unsafe_allow_html=True)

# ── Posición activa ──
if state.get("position", {}).get("active", False):
    pos = state["position"]
    st.warning(
        f"📈 **{pos['type']}** @ ${pos['entry_price']:.4f} "
        f"| SL: ${pos['current_sl']:.4f} "
        f"| TP1: ${pos['tp1']:.4f} | TP2: ${pos['tp2']:.4f}",
        icon="⚠️",
    )

st.markdown("---")

# ─────────────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────────────

tab_icons = ["📊", "📋", "🧪", "⚙️", "📈", "💹", "⚙️"]
tab_labels = ["Market", "Historial", "Backtest", "Optimizar", "Rendimiento", "Live P&L", "Diagnóstico"]
tabs = st.tabs([f"{i} {l}" for i, l in zip(tab_icons, tab_labels)])

# ═════════════════════════════════════════════════════════════
# TAB 1 — Market (Indicadores + Velas Reales)
# ═════════════════════════════════════════════════════════════

with tabs[0]:
    col_ind, col_chart = st.columns([1, 2.5])
    
    with col_ind:
        ind = state.get("indicators", {})
        rsi_val = ind.get("rsi_15m", 50)
        st.plotly_chart(create_rsi_gauge(rsi_val), width="stretch")
        
        # Indicadores adicionales
        adx = ind.get("adx_1h", 0)
        ma_spread = ind.get("ma_spread", 0)
        ranging = ind.get("market_ranging", False)
        
        st.markdown('<div class="glass-card">', unsafe_allow_html=True)
        st.markdown("**📊 Indicadores 1h**")
        
        adx_color = "#3fb950" if adx >= 25 else "#d29922"
        adx_label = "Tendencia fuerte" if adx >= 25 else "Lateral / Débil"
        st.markdown(f"**ADX**: `{adx:.1f}` <span style='color:{adx_color};font-size:0.8rem'>({adx_label})</span>", unsafe_allow_html=True)
        
        spread_color = "#3fb950" if abs(ma_spread) < 0.01 else "#d29922" if abs(ma_spread) < 0.02 else "#f85149"
        st.markdown(f"**MA Spread**: `{ma_spread:.5f}`", unsafe_allow_html=True)
        
        ranging_icon = "🟡" if ranging else "🔵"
        st.markdown(f"**Mercado**: {ranging_icon} {'Lateral' if ranging else 'Tendencia'}")
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Fase actual
        fase_config = FASES_ESCALAMIENTO.get(fase_actual, {})
        st.markdown(f'<div class="glass-card" style="margin-top:0.5rem">', unsafe_allow_html=True)
        st.markdown(f"**{create_phase_badge(fase_actual)}**", unsafe_allow_html=True)
        st.caption(fase_config.get("descripcion", ""))
        
        # Métricas de fase
        if fase_config:
            st.markdown(
                f"Risk: `{fase_config.get('risk_per_trade', 0)*100:.1f}%` | "
                f"Apalanc: `{fase_config.get('leverage', 1)}x` | "
                f"TP1: `{fase_config.get('tp1_rr', 0)}R` "
            )
        st.markdown('</div>', unsafe_allow_html=True)
    
    with col_chart:
        # ── Velas Reales ──
        current_sym = state.get("symbol", "XRPUSDT")
        tf_options = {"15m (Entrada)": "15", "60m (Tendencia)": "60"}
        selected_tf_label = st.radio("Timeframe", list(tf_options.keys()), horizontal=True, label_visibility="collapsed")
        selected_tf = tf_options[selected_tf_label]
        
        df_candles = load_market_data(current_sym, selected_tf, limit=150)
        
        if not df_candles.empty:
            latest = df_candles.iloc[-1]
            change = latest["close"] - df_candles.iloc[-2]["close"] if len(df_candles) > 1 else 0
            change_pct = (change / latest["close"]) * 100 if latest["close"] else 0
            change_str = f"{change:+.4f} ({change_pct:+.2f}%)"
            change_color = "#3fb950" if change >= 0 else "#f85149"
            
            st.markdown(
                f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:5px">'
                f'<span style="font-size:1.1rem;font-weight:700">{current_sym}</span>'
                f'<span style="font-size:1rem;font-weight:600;color:{change_color}">'
                f'${latest["close"]:.4f} {change_str}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            
            fig_candle = create_candlestick_chart(df_candles, title="")
            st.plotly_chart(fig_candle, width="stretch")
        else:
            st.info("📭 No hay datos de velas disponibles. Esperando datos del exchange...")
            st.markdown("### Datos disponibles:")
            for df_info in get_data_files_summary():
                st.caption(f"• {df_info['symbol']} ({df_info['timeframe']}m) — {df_info['size_kb']} KB")


# ═════════════════════════════════════════════════════════════
# TAB 2 — Historial
# ═════════════════════════════════════════════════════════════

with tabs[1]:
    st.markdown("### 📋 Historial de Operaciones")
    
    trades_db = db.get_trades(limit=500)
    if trades_db:
        df_trades = pd.DataFrame(trades_db)
        if "entry_time" in df_trades.columns:
            df_trades["entry_time"] = pd.to_datetime(df_trades["entry_time"])
        if "exit_time" in df_trades.columns:
            df_trades["exit_time"] = pd.to_datetime(df_trades["exit_time"])
        
        total_trades = len(df_trades)
        wins = (df_trades["pnl_usdt"] > 0).sum()
        losses = total_trades - wins
        win_rate = wins / total_trades * 100 if total_trades else 0
        total_pnl = df_trades["pnl_usdt"].sum()
        
        col_h1, col_h2, col_h3, col_h4 = st.columns(4)
        col_h1.metric("Total Trades", total_trades)
        col_h2.metric("Win Rate", f"{win_rate:.1f}%")
        col_h3.metric("Ganadas", wins, delta_color="normal")
        col_h4.metric("Perdidas", losses, delta_color="inverse")
        
        # Mini charts row
        col_donut, col_dist = st.columns(2)
        with col_donut:
            fig_donut = create_winrate_donut(wins, losses)
            st.plotly_chart(fig_donut, width="stretch")
        with col_dist:
            fig_dist = create_pnl_distribution(df_trades)
            st.plotly_chart(fig_dist, width="stretch")
        
        # Timeline
        fig_timeline = create_trade_timeline(df_trades)
        st.plotly_chart(fig_timeline, width="stretch")
        
        # Tabla
        st.markdown("#### Últimas operaciones")
        cols_to_show = ["entry_time", "symbol", "side", "entry_price", "exit_price",
                        "pnl_usdt", "close_reason"]
        available_cols = [c for c in cols_to_show if c in df_trades.columns]
        for extra in ["rsi_entry", "adx_entry", "atr_entry"]:
            if extra in df_trades.columns:
                available_cols.append(extra)
        
        df_display = df_trades[available_cols].tail(20).copy()
        
        # Color PnL
        def color_pnl(val: Any) -> str:
            if isinstance(val, (int, float)):
                return f"color: {'#3fb950' if val > 0 else '#f85149' if val < 0 else '#e6edf3'}"
            return ""
        
        st.dataframe(
            df_display.style.map(color_pnl, subset=["pnl_usdt"] if "pnl_usdt" in df_display.columns else []),
            width="stretch",
            height=400,
        )
        
        # CSV Download
        csv = df_trades.to_csv(index=False)
        st.download_button(
            "📥 Descargar CSV",
            data=csv,
            file_name=f"trades_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv",
        )
    else:
        st.info("📭 Aún no hay trades registrados en la base de datos")


# ═════════════════════════════════════════════════════════════
# TAB 3 — Backtest (MEJORADO)
# ═════════════════════════════════════════════════════════════

with tabs[2]:
    st.markdown("### 🧪 Backtesting Engine")
    st.caption("Simula estrategias con datos históricos reales")
    
    col_form, col_results = st.columns([1, 2.2])
    
    with col_form:
        st.markdown("#### ⚙️ Parámetros")
        bt_symbol = st.selectbox("Símbolo", options=list(SUPPORTED_COINS.keys()), key="bt_sym")
        bt_fase = st.selectbox("Fase", options=list(FASES_ESCALAMIENTO.keys()),
                               format_func=lambda x: f"Fase {x}: {FASES_ESCALAMIENTO[x].get('descripcion', '')[:40]}...",
                               key="bt_fase")
        bt_capital = st.number_input("Capital inicial (USDT)", min_value=10.0, value=100.0, step=10.0, key="bt_cap")
        col_s, col_e = st.columns(2)
        with col_s:
            bt_start = st.date_input("Inicio", value=(pd.Timestamp.now() - pd.Timedelta(days=30)).date(), key="bt_start")  # type: ignore[arg-type]
        with col_e:
            bt_end = st.date_input("Fin", value=pd.Timestamp.now().date(), key="bt_end")  # type: ignore[arg-type]
        run_bt = st.button("🚀 Ejecutar Backtest", type="primary", width="stretch")
    
    with col_results:
        if run_bt:
            if bt_start >= bt_end:
                st.toast("❌ La fecha de inicio debe ser anterior a la fecha fin.")
            else:
                with st.spinner(f"⏳ Ejecutando backtest de {bt_symbol}..."):
                    from bot.core.backtester import BacktestEngine
                    engine = BacktestEngine(symbol=bt_symbol, fase_num=bt_fase)
                    result = engine.run(
                        start_date=bt_start.strftime("%Y-%m-%d"),
                        end_date=bt_end.strftime("%Y-%m-%d"),
                        capital=bt_capital,
                    )
                    st.session_state["bt_result"] = result
        
        if "bt_result" in st.session_state and st.session_state["bt_result"]:
            result = st.session_state["bt_result"]
            if result.total_trades > 0:
                st.toast(f"✅ Backtest completado: {result.total_trades} trades en {result.total_pnl_pct:+.2f}%")
                
                met1, met2, met3, met4 = st.columns(4)
                final_capital = result.capital_inicial + result.total_pnl_usdt
                met1.metric("Capital Final", f"${final_capital:.2f}", delta=f"${result.total_pnl_usdt:+.2f}")
                met2.metric("PnL Total", f"{result.total_pnl_pct:+.2f}%")
                met3.metric("Win Rate", f"{result.win_rate:.1f}%")
                met4.metric("Profit Factor", f"{result.profit_factor:.2f}")
                
                met5, met6, met7, met8 = st.columns(4)
                met5.metric("Max Drawdown", f"{result.max_drawdown_pct:.2f}%")
                met6.metric("Avg Win", f"{result.avg_win_pct:+.2f}%")
                met7.metric("Avg Loss", f"{result.avg_loss_pct:+.2f}%")
                met8.metric("Total Trades", result.total_trades)
                
                # Equity curve del backtest
                if result.equity_curve:
                    st.markdown("#### 📈 Equity Curve")
                    equity_df = pd.DataFrame({
                        "Trade": range(1, len(result.equity_curve) + 1),
                        "Equity": result.equity_curve,
                    })
                    fig_bt = go.Figure()
                    fig_bt.add_trace(go.Scatter(
                        x=equity_df["Trade"], y=equity_df["Equity"],
                        fill="tozeroy",
                        line=dict(color="#58a6ff", width=2),
                        fillcolor="rgba(88,166,255,0.08)",
                    ))
                    fig_bt.update_layout(
                        template="plotly_dark",
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        height=300,
                        margin=dict(l=30, r=20, t=10, b=20),
                        xaxis_title="Trade #",
                        yaxis_title="Equity ($)",
                        font=dict(color="#e6edf3"),
                        hovermode="x",
                    )
                    fig_bt.update_xaxes(gridcolor="rgba(48,54,61,0.3)")
                    fig_bt.update_yaxes(gridcolor="rgba(48,54,61,0.3)")
                    st.plotly_chart(fig_bt, width="stretch")
                
                # Detalle de operaciones
                st.markdown("#### 📋 Detalle de operaciones")
                df_bt = result.to_dataframe()
                st.dataframe(df_bt, width="stretch", height=250)
                
                csv_bt = df_bt.to_csv(index=False)
                st.download_button(
                    "📥 Descargar CSV",
                    data=csv_bt,
                    file_name=f"backtest_{bt_symbol}_{bt_start}_{bt_end}.csv",
                    mime="text/csv",
                )
            else:
                st.toast("⚠️ No se generaron trades en el período seleccionado.")


# ═════════════════════════════════════════════════════════════
# TAB 4 — Optimizar
# ═════════════════════════════════════════════════════════════

with tabs[3]:
    st.markdown("### ⚙️ Optimización de Parámetros")
    st.caption("Prueba cientos de combinaciones y encuentra la configuración óptima")
    
    if "optimization_running" not in st.session_state:
        st.session_state.optimization_running = False
    if "cancel_flag" not in st.session_state:
        st.session_state.cancel_flag = threading.Event()
    if "opt_thread" not in st.session_state:
        st.session_state.opt_thread = None
    
    col_form_opt, col_res_opt = st.columns([1, 2])
    
    with col_form_opt:
        st.markdown("#### Datos del backtest")
        opt_symbol = st.selectbox("Símbolo", options=list(SUPPORTED_COINS.keys()), key="opt_symbol")
        opt_fase = st.selectbox("Fase base", options=list(FASES_ESCALAMIENTO.keys()),
                                format_func=lambda x: f"Fase {x}: {FASES_ESCALAMIENTO[x].get('descripcion', '')[:40]}...",
                                key="opt_fase")
        opt_capital = st.number_input("Capital inicial (USDT)", min_value=10.0, value=100.0, step=10.0, key="opt_capital")
        col_opt_start, col_opt_end = st.columns(2)
        with col_opt_start:
            opt_start = st.date_input("Inicio", value=(pd.Timestamp.now() - pd.Timedelta(days=60)).date(), key="opt_start")  # type: ignore[arg-type]
        with col_opt_end:
            opt_end = st.date_input("Fin", value=pd.Timestamp.now().date(), key="opt_end")  # type: ignore[arg-type]
        
        st.markdown("#### Parámetros a optimizar")
        tp1_rr_values = st.text_input("tp1_rr", value="0.8,1.0,1.2,1.5,2.0")
        tp2_rr_values = st.text_input("tp2_rr", value="1.5,2.0,2.5,3.0")
        max_sl_pct_values = st.text_input("max_sl_pct", value="0.008,0.01,0.012,0.015")
        rsi_min_long_values = st.text_input("rsi_min_long", value="20,25,30,35")
        rsi_max_long_values = st.text_input("rsi_max_long", value="35,38,42,45")
        
        col_btn1, col_btn2 = st.columns(2)
        with col_btn1:
            run_opt = st.button("🚀 Optimizar", type="primary", width="stretch",
                                disabled=st.session_state.optimization_running)
        with col_btn2:
            if st.button("⏹️ Cancelar", width="stretch", disabled=not st.session_state.optimization_running):
                st.session_state.cancel_flag.set()
                st.session_state.optimization_running = False
                st.rerun()
    
    with col_res_opt:
        if run_opt and not st.session_state.optimization_running:
            if opt_start >= opt_end:
                st.toast("❌ La fecha de inicio debe ser anterior a la final.")
            else:
                param_grid: dict[str, Any] = {}
                param_grid_valid = True
                try:
                    if tp1_rr_values.strip():
                        param_grid["tp1_rr"] = [float(x.strip()) for x in tp1_rr_values.split(",")]
                    if tp2_rr_values.strip():
                        param_grid["tp2_rr"] = [float(x.strip()) for x in tp2_rr_values.split(",")]
                    if max_sl_pct_values.strip():
                        param_grid["max_sl_pct"] = [float(x.strip()) for x in max_sl_pct_values.split(",")]
                    if rsi_min_long_values.strip():
                        param_grid["rsi_min_long"] = [int(x.strip()) for x in rsi_min_long_values.split(",")]
                    if rsi_max_long_values.strip():
                        param_grid["rsi_max_long"] = [int(x.strip()) for x in rsi_max_long_values.split(",")]
                except ValueError:
                    st.toast("❌ Error en el formato de los parámetros.")
                    param_grid_valid = False
                
                if param_grid_valid:
                    total_combos = 1
                    for v in param_grid.values():
                        total_combos *= len(v)
                    st.toast(f"⏳ Se probarán {total_combos} combinaciones.")
                    
                    st.session_state.cancel_flag = threading.Event()
                    st.session_state.cancel_flag.clear()
                    st.session_state.optimization_running = True
                    
                    from bot.core.optimizer import optimize
                    cancel_flag_local = st.session_state.cancel_flag
                    
                    def run_optimization() -> None:
                        result_df = optimize(
                            symbol=opt_symbol,
                            fase_num=opt_fase,
                            start_date=opt_start.strftime("%Y-%m-%d"),
                            end_date=opt_end.strftime("%Y-%m-%d"),
                            capital=opt_capital,
                            param_grid=param_grid,
                            min_trades=5,
                            cancel_flag=cancel_flag_local,
                        )
                        st.session_state.opt_result = result_df
                        st.session_state.optimization_running = False
                    
                    thread = threading.Thread(target=run_optimization, daemon=True)
                    st.session_state.opt_thread = thread
                    thread.start()
        
        if st.session_state.optimization_running:
            with st.spinner("⚡ Optimización en curso..."):
                while st.session_state.optimization_running:
                    time.sleep(1)
                    st.rerun()
        
        if "opt_result" in st.session_state and st.session_state.opt_result is not None:
            df_opt = st.session_state.opt_result
            if not df_opt.empty:
                best = df_opt.iloc[0]
                
                st.toast(f"🏆 Optimización completada — Mejor resultado:")
                
                col_b1, col_b2, col_b3, col_b4 = st.columns(4)
                col_b1.metric("Profit Factor", f"{best.get('profit_factor', 0):.2f}")
                col_b2.metric("Win Rate", f"{best.get('win_rate', 0):.1f}%")
                col_b3.metric("Total Trades", int(best.get('total_trades', 0)))
                col_b4.metric("PnL Total", f"{best.get('total_pnl_pct', 0):+.2f}%")
                
                with st.expander("🔍 Ver parámetros óptimos", expanded=True):
                    st.json({
                        k: v for k, v in best.to_dict().items()
                        if k in ("tp1_rr", "tp2_rr", "max_sl_pct", "rsi_min_long", "rsi_max_long")
                    })
                
                st.markdown("#### 🏆 Top 10 combinaciones")
                st.dataframe(df_opt.head(10), width="stretch")
                
                csv_opt = df_opt.to_csv(index=False)
                st.download_button(
                    "📥 Descargar CSV",
                    data=csv_opt,
                    file_name=f"optimization_{opt_symbol}_{datetime.now().strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                )
            else:
                st.toast("⚠️ Ninguna combinación generó suficientes trades.")


# ═════════════════════════════════════════════════════════════
# TAB 5 — Rendimiento
# ═════════════════════════════════════════════════════════════

with tabs[4]:
    st.markdown("### 📈 Rendimiento del Bot")
    
    trades_all = db.get_trades(limit=10000)
    if trades_all:
        df_all = pd.DataFrame(trades_all)
        if "exit_time" in df_all.columns:
            df_all["exit_time"] = pd.to_datetime(df_all["exit_time"])
        
        total_trades = len(df_all)
        wins = (df_all["pnl_usdt"] > 0).sum()
        win_rate = wins / total_trades * 100 if total_trades else 0
        total_pnl = df_all["pnl_usdt"].sum()
        profit_factor = (
            df_all[df_all["pnl_usdt"] > 0]["pnl_usdt"].sum()
            / abs(df_all[df_all["pnl_usdt"] < 0]["pnl_usdt"].sum())
            if total_trades > 0 and (df_all["pnl_usdt"] < 0).sum() > 0
            else float("inf") if (df_all["pnl_usdt"] >= 0).all() and total_trades > 0
            else 0
        )
        
        col_r1, col_r2, col_r3, col_r4 = st.columns(4)
        col_r1.metric("Total Trades", total_trades)
        col_r2.metric("Win Rate", f"{win_rate:.1f}%")
        col_r3.metric("PnL Total", f"${total_pnl:+.2f}")
        col_r4.metric("Profit Factor", f"{profit_factor:.2f}")
        
        # Equity Curve
        if "exit_time" in df_all.columns and "pnl_usdt" in df_all.columns:
            fig_eq = create_equity_curve(df_all)
            st.plotly_chart(fig_eq, width="stretch")
        
        # Performance por símbolo + monthly heatmap
        col_sym, col_month = st.columns(2)
        with col_sym:
            fig_sym = create_symbol_performance(df_all)
            st.plotly_chart(fig_sym, width="stretch")
        with col_month:
            fig_month = create_monthly_performance(df_all)
            st.plotly_chart(fig_month, width="stretch")
        
        # Últimos trades
        st.markdown("#### Últimos Trades")
        cols_to_show = ["entry_time", "symbol", "side", "entry_price", "exit_price",
                        "pnl_usdt", "close_reason"]
        available_cols = [c for c in cols_to_show if c in df_all.columns]
        for extra in ["rsi_entry", "adx_entry", "atr_entry"]:
            if extra in df_all.columns:
                available_cols.append(extra)
        
        st.dataframe(
            df_all[available_cols].tail(15),
            width="stretch",
            height=350,
        )
    else:
        st.info("📭 Aún no hay trades registrados en la base de datos")
    
    # ── Métricas Avanzadas (colapsable dentro de Rendimiento) ──
    with st.expander("📊 Ver Métricas Avanzadas de Riesgo", expanded=False):
        trades_all = db.get_trades(limit=10000)
        if trades_all and len(trades_all) >= 5:
            df_all = pd.DataFrame(trades_all)
            if "exit_time" in df_all.columns:
                df_all["exit_time"] = pd.to_datetime(df_all["exit_time"])
            
            cutoff = pd.Timestamp.now() - pd.Timedelta(days=90)
            df_period = df_all[df_all["exit_time"] >= cutoff] if "exit_time" in df_all.columns else df_all
            
            if len(df_period) >= 5 and "pnl_pct" in df_period.columns:
                returns = df_period["pnl_pct"].to_numpy() / 100.0  # type: ignore[union-attr]
                
                rf_rate = 0.02 / 35040
                excess = returns - rf_rate
                sharpe = (excess.mean() / (returns.std() + 1e-8)) * np.sqrt(35040)
                
                downside = returns[returns < 0]
                sortino = (excess.mean() / (downside.std() + 1e-8)) * np.sqrt(35040) if len(downside) > 0 else 0
                
                df_p = df_period.sort_values("exit_time").copy()  # type: ignore[union-attr]
                df_p["cum_pnl"] = df_p["pnl_usdt"].cumsum()
                df_p["peak"] = df_p["cum_pnl"].cummax()
                df_p["drawdown"] = (df_p["cum_pnl"] - df_p["peak"]) / df_p["peak"].replace(0, 1) * 100
                max_dd = abs(df_p["drawdown"].min())
                total_ret_pct = (
                    (df_p["cum_pnl"].iloc[-1] / df_p["cum_pnl"].iloc[0] - 1) * 100
                    if len(df_p) > 1 and df_p["cum_pnl"].iloc[0] != 0
                    else 0
                )
                calmar = total_ret_pct / max_dd if max_dd > 0 else 0
                
                var_95 = np.percentile(returns, 5)
                cvar_95 = returns[returns <= var_95].mean() if len(returns[returns <= var_95]) > 0 else var_95
                daily_vol = returns.std() * np.sqrt(96) * 100
                expectancy = returns.mean() * 100
                
                col_m1, col_m2, col_m3, col_m4 = st.columns(4)
                col_m1.metric("Sharpe Ratio", f"{sharpe:.2f}")
                col_m2.metric("Sortino Ratio", f"{sortino:.2f}")
                col_m3.metric("Calmar Ratio", f"{calmar:.2f}")
                col_m4.metric("Expectancy", f"{expectancy:+.3f}%")
                
                col_m5, col_m6, col_m7, col_m8 = st.columns(4)
                col_m5.metric("VaR 95%", f"{var_95*100:.2f}%")
                col_m6.metric("CVaR 95%", f"{cvar_95*100:.2f}%")
                col_m7.metric("Volatilidad Diaria", f"{daily_vol:.2f}%")
                col_m8.metric("Máx Drawdown", f"{max_dd:.2f}%")
                
                fig_dist = create_pnl_distribution(df_period, var_95)  # type: ignore[arg-type]
                st.plotly_chart(fig_dist, width="stretch")
                
                st.markdown("#### 📉 Drawdown Chart")
                fig_dd = go.Figure()
                fig_dd.add_trace(go.Scatter(
                    x=df_p["exit_time"], y=df_p["drawdown"],
                    fill="tozeroy", line=dict(color="#f85149", width=1.5),
                    fillcolor="rgba(248,81,73,0.1)", name="Drawdown",
                ))
                fig_dd.update_layout(
                    template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)", height=250,
                    margin=dict(l=30, r=20, t=10, b=20),
                    font=dict(color="#e6edf3"), hovermode="x", yaxis_title="Drawdown (%)",
                )
                fig_dd.update_xaxes(gridcolor="rgba(48,54,61,0.3)")
                fig_dd.update_yaxes(gridcolor="rgba(48,54,61,0.3)")
                st.plotly_chart(fig_dd, width="stretch")
            else:
                st.info(f"📭 Se necesitan al menos 5 trades con datos de pnl_pct en los últimos 90 días. "
                        f"Disponibles: {len(df_period)}")
        else:
            st.info(f"📭 Se necesitan al menos 5 trades. Actuales: {len(trades_all) if trades_all else 0}")


# ═════════════════════════════════════════════════════════════
# TAB 6 — Live P&L (Equity en Tiempo Real)
# ═════════════════════════════════════════════════════════════

with tabs[5]:
    st.markdown("### 💹 Live P&L — Equity en Tiempo Real")
    
    # Equity en vivo
    equity_hist = state.get("equity_history", [])
    if equity_hist and len(equity_hist) >= 2:
        fig_eq = create_live_equity_curve(equity_hist)
        st.plotly_chart(fig_eq, width="stretch")
    else:
        st.info("📭 Esperando datos de equity... El bot debe estar corriendo para generar la curva.")
    
    # P&L por símbolo desde el estado de cada runner
    st.markdown("### 📊 P&L No Realizado por Símbolo")
    symbols_state = {k: v for k, v in state.items() if k.startswith("position_")}
    if symbols_state:
        fig_pnl = create_live_pnl_by_symbol(symbols_state)
        st.plotly_chart(fig_pnl, width="stretch")
    else:
        st.info("📭 Sin posiciones activas para mostrar P&L por símbolo.")
    
    # Métricas rápidas de equity
    if equity_hist and len(equity_hist) >= 2:
        st.markdown("### 📈 Estadísticas de Equity")
        first_eq = equity_hist[0]["equity"]
        last_eq = equity_hist[-1]["equity"]
        eq_change = last_eq - first_eq
        eq_change_pct = (eq_change / first_eq * 100) if first_eq else 0
        peak_eq = max(e["equity"] for e in equity_hist)
        valley_eq = min(e["equity"] for e in equity_hist)
        dd_from_peak = ((last_eq - peak_eq) / peak_eq * 100) if peak_eq else 0
        
        col_eq1, col_eq2, col_eq3, col_eq4 = st.columns(4)
        col_eq1.metric("Equity Actual", f"${last_eq:.2f}", f"{eq_change_pct:+.2f}%")
        col_eq2.metric("Pico", f"${peak_eq:.2f}")
        col_eq3.metric("Valle", f"${valley_eq:.2f}")
        col_eq4.metric("Drawdown desde Pico", f"{dd_from_peak:.2f}%")
        
        # Volatilidad de equity (desviación estándar de cambios porcentuales)
        eq_vals = [e["equity"] for e in equity_hist]
        eq_returns = [(eq_vals[i] - eq_vals[i-1]) / eq_vals[i-1] * 100
                      for i in range(1, len(eq_vals)) if eq_vals[i-1] > 0]
        if eq_returns:
            eq_vol = np.std(eq_returns)
            col_eq5, col_eq6 = st.columns(2)
            col_eq5.metric("Volatilidad Equity", f"{eq_vol:.2f}%")
            col_eq6.metric("Rango (Pico-Valle)", f"${peak_eq - valley_eq:.2f}")


# ═════════════════════════════════════════════════════════════
# TAB 7 — Diagnóstico
# ═════════════════════════════════════════════════════════════

with tabs[6]:
    st.markdown("### ⚙️ Diagnóstico del Sistema")
    
    col_d1, col_d2 = st.columns(2)
    
    with col_d1:
        st.markdown("#### 📡 Estado del Sistema")
        st.markdown(f'<div class="glass-card">', unsafe_allow_html=True)
        
        # Status row
        dot_class = "on" if running else "off"
        st.markdown(
            f'<div style="display:flex;justify-content:space-between;margin-bottom:8px">'
            f'<span>Bot</span>'
            f'<span><span class="status-dot {dot_class}"></span>{"🟢 Corriendo" if running else "🔴 Detenido"}</span>'
            f'</div>',
            unsafe_allow_html=True,
        )
        
        st.json({
            "Modo": state.get("mode", "STOPPED"),
            "Testnet": state.get("testnet", True),
            "Símbolos activos": active_syms,
            "Fase actual": fase_actual,
            "Uptime (s)": f"{uptime:.0f}" if uptime > 0 else "0",
            "Última actualización": state.get("last_update", ""),
        })
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Errores
        st.markdown("#### ⚠️ Últimos errores")
        errors = state.get("errors", [])
        if errors:
            for err in errors[-5:]:
                ts = err.get("timestamp", "")[11:19] if err.get("timestamp") else "?"
                msg = err.get("message", "")[:120]
                st.caption(f"🔴 `{ts}` — {msg}")
        else:
            st.success("✅ Sin errores recientes")
    
    with col_d2:
        st.markdown("#### 📁 Archivos del Sistema")
        files_to_check = {
            "📓 Trade Journal": LOG_DIR / "trade_journal.csv",
            "📝 Log Bot": ROOT_DIR / "logs" / "bot.log",
            "🗄️ DB Historial": ROOT_DIR / "data" / "bot_history.db",
            "⚙️ Estado Dashboard": Path(__file__).parent / "bot_state.json",
            "📜 Watchdog Log": ROOT_DIR / "logs" / "watchdog.log",
        }
        
        for name, path in files_to_check.items():
            if path.exists():
                size = path.stat().st_size
                if size > 1024 * 1024:
                    size_str = f"{size / 1024 / 1024:.1f} MB"
                else:
                    size_str = f"{size / 1024:.1f} KB"
                st.markdown(f"✅ **{name}**: `{size_str}`")
            else:
                st.markdown(f"❌ **{name}**: No encontrado")
        
        # Datos disponibles
        st.markdown("#### 📊 Datos de Mercado")
        data_summary = get_data_files_summary()
        symbols_found = set(d["symbol"] for d in data_summary)
        for s in sorted(symbols_found):
            files_for_sym = [d for d in data_summary if d["symbol"] == s]
            total_size = sum(d["size_kb"] for d in files_for_sym)
            st.caption(f"📈 **{s}**: {len(files_for_sym)} archivo(s), {total_size:.0f} KB")
        
        # Portfolio
        st.markdown("#### 💼 Portfolio")
        portfolio_syms = state.get("symbols", {})
        if portfolio_syms:
            for sym, info in portfolio_syms.items():
                pos_icon = "📈" if info.get("position_open", False) else "💤"
                st.caption(f"{pos_icon} **{sym}** — Fase {info.get('fase_actual', 0)}")
    
    st.markdown("---")
    st.markdown("### 🎛️ Gestión de Portfolio")
    
    import yaml
    settings_path = ROOT_DIR / "config" / "settings.yaml"
    with open(settings_path) as f:
        current_settings = yaml.safe_load(f)
    current_portfolio = current_settings.get("portfolio", {}).get("symbols", [])
    
    if current_portfolio:
        fig_portfolio = create_portfolio_pie(current_portfolio)
        st.plotly_chart(fig_portfolio, width="stretch")
    
    sym_col1, sym_col2 = st.columns(2)
    
    with sym_col1:
        st.markdown("#### ➕ Agregar símbolo")
        new_symbol = st.selectbox("Símbolo", options=list(SUPPORTED_COINS.keys()), key="new_sym")
        new_alloc = st.slider("Asignación %", 5, 100, 25, 5, key="new_alloc")
        if st.button("Agregar al portfolio", width="stretch"):
            if not any(s["symbol"] == new_symbol for s in current_portfolio):
                current_portfolio.append({
                    "symbol": new_symbol,
                    "allocation_pct": new_alloc / 100,
                    "leverage": 1,
                    "risk_per_trade": 0.02,
                    "active": True,
                })
                total = sum(s["allocation_pct"] for s in current_portfolio)
                for s in current_portfolio:
                    s["allocation_pct"] = round(s["allocation_pct"] / total, 2)
                if _save_portfolio_to_yaml(current_portfolio):
                    st.toast(f"✅ {new_symbol} agregado y guardado en settings.yaml")
                    st.rerun()
            else:
                st.toast("⚠️ Este símbolo ya está en el portfolio")
    
    with sym_col2:
        if current_portfolio:
            st.markdown("#### ➖ Quitar símbolo")
            remove_sym = st.selectbox(
                "Quitar",
                options=[s["symbol"] for s in current_portfolio],
                key="rem_sym",
            )
            if st.button("Quitar del portfolio", width="stretch"):
                current_portfolio = [s for s in current_portfolio if s["symbol"] != remove_sym]
                if _save_portfolio_to_yaml(current_portfolio):
                    st.toast(f"✅ {remove_sym} eliminado de settings.yaml")
                    st.rerun()
    
    col_rst1, col_rst2, col_rst3 = st.columns([1, 2, 1])
    with col_rst2:
        if st.button("🔄 Reiniciar Bot con nueva configuración", type="primary", width="stretch"):
            if controller.restart_with_config(current_portfolio):
                st.toast("✅ Bot reiniciado con nueva configuración")
                time.sleep(2)
                st.rerun()
            else:
                st.toast("❌ Error al reiniciar")


# ─────────────────────────────────────────────────────────────
# AUTO-REFRESH
# ─────────────────────────────────────────────────────────────

if auto_refresh:
    time.sleep(refresh_interval)
    st.rerun()
