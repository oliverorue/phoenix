"""
Backtesting Engine - Profesional (v7.0)
Incluye slippage realista, fees diferenciados (maker/taker),
trailing stop inteligente (como Botsito), y métricas avanzadas:
Sharpe, Sortino, Calmar, MFE, MAE, breakeven rate, LONG/SHORT stats.

Fixes v7.0:
  #1  Look-ahead bias corregido: indicadores con ventana [i-WARMUP : i]
  #2  Slippage dinámico según ratio ATR actual / ATR promedio
  #3  Cooldown homogéneo con el bot real (unidad ≈ 1 vela 15m)
  #4  Walk-forward testing en optimizer.py (split 80/20)
  #5  Gap cierre→apertura: entrada al open de la vela, no al close previo
  #6  Clasificación de trades por régimen de mercado (ADX)
  #7  Monte Carlo para estimar drawdown P95 en mainnet
  #8  Análisis de sensibilidad en optimizer.py (heatmap 2D)
  #9  MAX_HOLD dinámico desde sesión de fase
  #10 Simulación de spread bid-ask en cada punta
  #11 Sharpe/Sortino calculados sobre equity curve diaria
"""

import asyncio
import threading
import time
from typing import Any, Optional
from pathlib import Path
from dataclasses import dataclass, field
import pytz

import pandas as pd
import numpy as np

from config.loader import (
    SYMBOL, TIMEFRAME_PRIMARY, TIMEFRAME_ENTRY,
)
from bot.core.data_feed import DataFeed
from bot.core.strategy_engine import ScalpingStrategy
from bot.core.risk_manager import RiskManager
from bot.core.indicators import Indicators
from loguru import logger

# ── Comisiones y slippage realistas ─────────────────────────
TAKER_FEE_RATE = 0.0005       # 0.05% taker fee (igual que Botsito)
MAKER_FEE_RATE = 0.0001       # 0.01% maker fee (igual que Botsito)
SLIPPAGE_BASE  = 0.0003       # 0.03% base de slippage
SLIPPAGE_CAP   = 0.0015       # 0.15% techo para volatilidad extrema
SPREAD_BPS     = 0.0002       # 0.02% spread bid-ask estimado (XRP)

# Breakeven offset para cubrir comisiones (debe coincidir con position_manager.py)
# Mover SL exactamente a entry_price causa micro-pérdidas porque las comisiones
# de entrada+salida (~0.08%) aún no se han pagado. Este offset asegura que el
# backtest refleje el comportamiento real del bot.
BREAKEVEN_OFFSET_PCT = 0.0008


@dataclass
class BacktestTrade:
    signal_type: str
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    qty: float
    score: int

    exit_price: float = 0.0
    close_reason: str = ""
    pnl_usdt: float = 0.0
    pnl_pct: float = 0.0
    gross_pnl_usdt: float = 0.0   # sin comisiones
    commission_usdt: float = 0.0  # comisiones pagadas
    tp1_hit: bool = False
    tp2_hit: bool = False
    duration_candles: int = 0
    entry_time: Optional[pd.Timestamp] = None
    exit_time: Optional[pd.Timestamp] = None
    max_favorable_excursion: float = 0.0   # MFE (% hacia TP2)
    max_adverse_excursion: float = 0.0     # MAE (% hacia SL)
    breakeven_hit: bool = False

    # Fix #6: Clasificación por régimen
    adx_entry: float = 0.0
    market_regime: str = ""  # "trend" | "transition" | "ranging"

    # Costos extras
    spread_cost_usdt: float = 0.0
    slippage_entry_bps: float = 0.0


@dataclass
class BacktestResult:
    symbol: str
    start_date: str
    end_date: str
    capital_inicial: float
    fase_num: int

    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl_usdt: float = 0.0
    total_pnl_pct: float = 0.0
    max_drawdown_pct: float = 0.0
    profit_factor: float = 0.0
    avg_win_pct: float = 0.0
    avg_loss_pct: float = 0.0
    max_consecutive_losses: int = 0

    # 📊 Métricas avanzadas
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0
    avg_trade_duration_candles: float = 0.0
    total_commission_usdt: float = 0.0
    commission_pct_of_capital: float = 0.0
    avg_mfe_pct: float = 0.0
    avg_mae_pct: float = 0.0
    breakeven_rate: float = 0.0
    long_trades: int = 0
    short_trades: int = 0
    long_win_rate: float = 0.0
    short_win_rate: float = 0.0
    best_trade_pnl: float = 0.0
    worst_trade_pnl: float = 0.0

    trades: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    equity_dates: list[pd.Timestamp] = field(default_factory=list)

    # Fix #7: Monte Carlo
    mc_drawdown_p95: float = 0.0
    mc_drawdown_mean: float = 0.0

    # Fix #6: Stats por régimen
    regime_trend_trades: int = 0
    regime_trend_win_rate: float = 0.0
    regime_transition_trades: int = 0
    regime_transition_win_rate: float = 0.0
    regime_ranging_trades: int = 0
    regime_ranging_win_rate: float = 0.0

    # Costos totales
    total_spread_usdt: float = 0.0
    avg_slippage_bps: float = 0.0

    def summary(self) -> dict[str, Any]:
        capital_final = self.capital_inicial + self.total_pnl_usdt
        d = {
            "Símbolo": self.symbol,
            "Período": f"{self.start_date} → {self.end_date}",
            "Fase": self.fase_num,
            "Capital Inicial": f"${self.capital_inicial:.2f}",
            "Capital Final": f"${capital_final:.2f}",
            "PnL Total": f"{self.total_pnl_pct:+.2f}% (${self.total_pnl_usdt:+.2f})",
            "Comisiones": f"${self.total_commission_usdt:.4f}",
            "Spread Total": f"${self.total_spread_usdt:.4f}",
            "Avg Slippage (bps)": f"{self.avg_slippage_bps:.1f}",
            "Total Trades": self.total_trades,
            "Win Rate": f"{self.win_rate:.1f}%",
            "Avg Win": f"{self.avg_win_pct:+.2f}%",
            "Avg Loss": f"{self.avg_loss_pct:+.2f}%",
            "Profit Factor": f"{self.profit_factor:.2f}",
            "Max Drawdown": f"{self.max_drawdown_pct:.2f}%",
            "MC DD P95 (mainnet)": f"{self.mc_drawdown_p95:.2f}%",
            "Max Pérdidas Seguidas": self.max_consecutive_losses,
            "Sharpe Ratio": f"{self.sharpe_ratio:.2f}",
            "Sortino Ratio": f"{self.sortino_ratio:.2f}",
            "Calmar Ratio": f"{self.calmar_ratio:.2f}",
            "Duración Promedio": f"{self.avg_trade_duration_candles:.1f} velas",
            "Avg MFE": f"{self.avg_mfe_pct:.1f}% del TP2",
            "Avg MAE": f"{self.avg_mae_pct:.1f}% del SL",
            "Breakeven Rate": f"{self.breakeven_rate:.1f}%",
            "LONG/SHORT": f"{self.long_trades}/{self.short_trades} (WR: {self.long_win_rate:.0f}%/{self.short_win_rate:.0f}%)",
            "Best/Worst": f"+${self.best_trade_pnl:.2f}/-${abs(self.worst_trade_pnl):.2f}",
        }
        # Régimen (Fix #6)
        if self.regime_trend_trades > 0:
            d["Régimen Tendencia"] = f"{self.regime_trend_trades} trades (WR: {self.regime_trend_win_rate:.0f}%)"
        if self.regime_transition_trades > 0:
            d["Régimen Transición"] = f"{self.regime_transition_trades} trades (WR: {self.regime_transition_win_rate:.0f}%)"
        if self.regime_ranging_trades > 0:
            d["Régimen Lateral"] = f"{self.regime_ranging_trades} trades (WR: {self.regime_ranging_win_rate:.0f}%)"
        return d

    def to_dataframe(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame()
        rows = [{
            "entry_time": t.entry_time,
            "side": t.signal_type,
            "entry": t.entry_price,
            "exit": t.exit_price,
            "qty": t.qty,
            "pnl_usdt": t.pnl_usdt,
            "pnl_pct": t.pnl_pct,
            "gross_pnl": t.gross_pnl_usdt,
            "commission": t.commission_usdt,
            "spread_cost": t.spread_cost_usdt,
            "close_reason": t.close_reason,
            "duration_candles": t.duration_candles,
            "breakeven_hit": t.breakeven_hit,
            "tp1_hit": t.tp1_hit,
            "tp2_hit": t.tp2_hit,
            "mfe_pct": t.max_favorable_excursion,
            "mae_pct": t.max_adverse_excursion,
            "adx_entry": t.adx_entry,
            "market_regime": t.market_regime,
            "slippage_entry_bps": t.slippage_entry_bps,
        } for t in self.trades]
        return pd.DataFrame(rows)


class BacktestEngine:
    # Fix #9: WARMUP aumentado de 30 → 100 para estabilizar EMA50, ADX y BB
    # Con 30 velas 15m (7.5h) la EMA50 no converge y genera asimetría LONG/SHORT
    # 100 velas 15m = 25h → más de 1 día completo de datos para indicadores robustos
    WARMUP = 100
    # Fix #9: MAX_HOLD se calcula por fase (sesion_fin - sesion_inicio) * 4
    # Default: 6h * 4 = 24 velas

    def __init__(self, symbol: str = SYMBOL, fase_num: int = 0,
                 data_feed: Optional[DataFeed] = None,
                 instrument_cache: Optional[dict[str, Any]] = None) -> None:
        """
        Args:
            symbol: Símbolo a backtestear.
            fase_num: Número de fase a utilizar.
            data_feed: DataFeed opcional (inyección de dependencia).
            instrument_cache: Cache de info del instrumento opcional.
        """
        self.symbol = symbol
        self.fase_num = fase_num
        self.data_feed = data_feed or DataFeed(symbol=symbol, testnet=False)
        self.strategy = ScalpingStrategy()
        self.risk_mgr = RiskManager(self.data_feed)
        self.indicators = Indicators()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.fase_cfg: dict[str, Any] = {}

        # Fix #2: rolling ATR promedio para slippage dinámico
        self._atr_pct_history: list[float] = []

        if instrument_cache:
            self.risk_mgr._cached_instrument_info = instrument_cache
            if hasattr(self.data_feed, '_instrument_cache'):
                self.data_feed._instrument_cache = instrument_cache
        else:
            self._preload_instrument_cache()

    # ── Fix #9: MAX_HOLD dinámico desde sesión ──────────────

    def _get_max_hold(self) -> int:
        """Calcula MAX_HOLD según la sesión de la fase actual.
        Por defecto 24 velas (6h de sesión) si no hay configuración."""
        sesion_inicio = self.fase_cfg.get("sesion_inicio", 14)
        sesion_fin = self.fase_cfg.get("sesion_fin", 22)
        horas_sesion = max(sesion_fin - sesion_inicio, 4)  # mínimo 4h
        return horas_sesion * 4  # 4 velas 15m por hora

    # ── Async helper ────────────────────────────────────────

    def _run_async(self, coro: Any) -> Any:
        """Ejecuta una coroutine en el event loop compartido (sync wrapper)."""
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        if self._loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor():
                future = asyncio.run_coroutine_threadsafe(coro, self._loop)
                return future.result()
        return self._loop.run_until_complete(coro)

    def _preload_instrument_cache(self) -> None:
        """Carga la info del instrumento de forma sync para el backtest."""
        try:
            if hasattr(self.data_feed, '_instrument_cache') and self.data_feed._instrument_cache:
                self.risk_mgr._cached_instrument_info = self.data_feed._instrument_cache
            else:
                info = self._run_async(self.data_feed.get_instrument_info())
                cache = {
                    "qtyStep": float(info.get("step_size", 0.001)),
                    "minOrderQty": float(info.get("min_qty", 1.0)),
                    "maxOrderQty": float(info.get("max_qty", 500000.0)),
                    "minNotionalValue": float(info.get("min_notional", 5.0)),
                    "maxNotionalValue": 1000000.0,
                }
                self.data_feed._instrument_cache = cache
                self.risk_mgr._cached_instrument_info = cache
        except Exception as e:
            logger.warning(f"No se pudo precargar instrument info: {e}. Usando defaults.")
            default_cache = {
                "qtyStep": 0.001,
                "minOrderQty": 1.0,
                "maxOrderQty": 500000.0,
                "minNotionalValue": 5.0,
                "maxNotionalValue": 1000000.0,
            }
            self.data_feed._instrument_cache = default_cache
            self.risk_mgr._cached_instrument_info = default_cache

    # ── Obtención de datos ──────────────────────────────────

    # Fix #2: Mapeo de intervalos para Binance (espera "15m"/"1h", no "15"/"60")
    _BINANCE_INTERVAL_MAP = {"15": "15m", "60": "1h", "5": "5m", "30": "30m", "120": "2h", "240": "4h", "1D": "1d", "1W": "1w"}

    def fetch_history(self, start_date: str, end_date: str, interval: str) -> pd.DataFrame:
        start_ms = int(pd.Timestamp(start_date, tz="UTC").timestamp() * 1000)  # type: ignore[attr-defined]
        end_ms = int(pd.Timestamp(end_date, tz="UTC").timestamp() * 1000)  # type: ignore[attr-defined]

        # Fix #2: Traducir intervalo al formato que Binance espera
        binance_interval = self._BINANCE_INTERVAL_MAP.get(interval, interval)

        client = self.data_feed._rest
        collected = []
        cursor = end_ms
        while True:
            try:
                candles = client.futures_klines(
                    symbol=self.symbol,
                    interval=binance_interval,
                    limit=200,
                    endTime=cursor,
                )
                if not candles:
                    break
                for c in candles:
                    ts = int(c[0])
                    if ts >= start_ms:
                        collected.append([c[0], c[1], c[2], c[3], c[4], c[5], c[7]])
                oldest = int(candles[-1][0])
                if oldest <= start_ms:
                    break
                cursor = oldest - 1
                time.sleep(0.3)
            except Exception as e:
                logger.error(f"Error descargando {interval}: {e}")
                break

        if not collected:
            return pd.DataFrame()

        df = pd.DataFrame(collected, columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"])
        df = df.astype({col: float for col in ["open", "high", "low", "close", "volume"]})
        df["timestamp"] = pd.to_datetime(df["timestamp"].astype(int), unit="ms")
        df.sort_values("timestamp", inplace=True)
        df.drop_duplicates("timestamp", inplace=True)
        df.reset_index(drop=True, inplace=True)
        return df

    def get_history_cached(self, start_date: str, end_date: str, interval: str) -> pd.DataFrame:
        cache_dir = Path("data")
        cache_dir.mkdir(exist_ok=True)
        cache_file = cache_dir / f"{self.symbol}_{interval}_{start_date}_{end_date}.csv"

        if cache_file.exists():
            logger.info(f"📦 Usando caché: {cache_file}")
            df = pd.read_csv(cache_file)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            return df

        logger.info(f"📥 Descargando {interval} para {self.symbol}...")
        df = self.fetch_history(start_date, end_date, interval)
        if not df.empty:
            df.to_csv(cache_file, index=False)
            logger.info(f"💾 Datos guardados en: {cache_file}")
        return df

    def _calc_commission(self, price: float, qty: float, is_taker: bool = True) -> float:
        """Calcula comisión para una orden."""
        rate = TAKER_FEE_RATE if is_taker else MAKER_FEE_RATE
        return price * qty * rate

    # ── Fix #2: Slippage dinámico en función de ATR ─────────

    def _calc_slippage_bps(self, atr_pct: float) -> float:
        """
        Calcula slippage dinámico basado en ATR actual vs ATR promedio.
        Si ATR actual es 2× el promedio, el slippage se duplica.
        Con techo de SLIPPAGE_CAP (0.15%).
        """
        if not self._atr_pct_history:
            return SLIPPAGE_BASE

        avg_atr = np.mean(self._atr_pct_history[-30:])  # ventana de 30 velas
        if avg_atr <= 0:
            return SLIPPAGE_BASE

        ratio = atr_pct / avg_atr
        slippage = float(SLIPPAGE_BASE * ratio)
        return min(slippage, float(SLIPPAGE_CAP))

    # ── Motor de simulación de un trade (CORAZÓN DEL BACKTEST) ─

    def _simulate(self, signal: Any, df_15m: pd.DataFrame, entry_idx: int,
                  capital: float, qty_validated: float,
                  atr_pct: float = 0.0) -> BacktestTrade:
        """
        Simula la evolución de un trade desde entry_idx, vela a vela,
        con prioridad TP1 > SL, trailing stop adaptativo (ATR real),
        breakeven dinámico y spread bid-ask (Fix #10).

        Fix #1 + #5: El entry se toma al OPEN de la vela entry_idx
        (no al close de la vela anterior), con slippage dinámico (Fix #2)
        y spread bid-ask (Fix #10).
        """
        cfg = self.fase_cfg

        # ── Fix #5: Entry al open de la vela (gap cierre→apertura) ──
        entry_candle = df_15m.iloc[entry_idx]
        open_price = entry_candle["open"]

        # ── Fix #2: Slippage dinámico ──
        effective_slippage_bps = self._calc_slippage_bps(atr_pct)
        slippage = open_price * effective_slippage_bps
        if signal.direction == "LONG":
            entry = open_price + slippage
        else:
            entry = open_price - slippage

        # ── Fix #10: Spread bid-ask en entrada ──
        spread_entry = open_price * SPREAD_BPS
        if signal.direction == "LONG":
            entry += spread_entry  # compras al ask
        else:
            entry -= spread_entry  # vendes al bid

        # Distancia original del SL (sin slippage) → recalcular con entrada real
        sl_dist_original = abs(signal.entry - signal.sl)
        tp1_rr = cfg.get("tp1_rr", 1.2)
        tp2_rr = cfg.get("tp2_rr", 2.0)
        # Breakeven trigger diferenciado: SHORT necesita trigger más agresivo
        # porque el MFE de SHORT revierte el 85% de las veces antes de TP1
        if signal.direction == "SHORT":
            breakeven_trigger = cfg.get("breakeven_trigger_short", cfg.get("breakeven_trigger", 0.35))
        else:
            breakeven_trigger = cfg.get("breakeven_trigger", 0.35)

        if signal.direction == "LONG":
            sl  = entry - sl_dist_original
            tp1 = entry + sl_dist_original * tp1_rr
            tp2 = entry + sl_dist_original * tp2_rr
        else:
            sl  = entry + sl_dist_original
            tp1 = entry - sl_dist_original * tp1_rr
            tp2 = entry - sl_dist_original * tp2_rr

        # Validación rápida: TP1 demasiado cerca
        atr_real = signal.atr if hasattr(signal, 'atr') and signal.atr > 0 else entry * 0.008
        if abs(tp1 - entry) < atr_real * 0.3:
            return BacktestTrade(
                signal_type=signal.direction,
                entry_price=entry, sl=sl, tp1=tp1, tp2=tp2,
                qty=0, score=signal.score, close_reason="tp_too_close"
            )

        if qty_validated <= 0:
            return BacktestTrade(
                signal_type=signal.direction,
                entry_price=entry, sl=sl, tp1=tp1, tp2=tp2,
                qty=0, score=signal.score, close_reason="invalid_size"
            )
        qty = qty_validated

        commission_entry = self._calc_commission(entry, qty, is_taker=True)

        trade = BacktestTrade(
            signal_type=signal.direction,
            entry_price=entry, sl=sl, tp1=tp1, tp2=tp2,
            qty=qty, score=signal.score,
            entry_time=df_15m.iloc[entry_idx]["timestamp"],
            spread_cost_usdt=spread_entry * qty * 2,  # entrada + salida
            slippage_entry_bps=round(effective_slippage_bps * 10000, 1),  # convertir a bps
        )

        # ── Estado interno ────────────────────────────────
        tp1_hit = False
        current_sl = sl
        breakeven_hit = False
        max_favorable = 0.0
        max_adverse = 0.0
        tp2_movement = abs(tp2 - entry)
        sl_distance = abs(entry - sl)

        # Trailing stop basado en ATR real
        trailing_trigger = 0.7
        trailing_distance = atr_real * 2.0
        trailing_active = False
        highest_price = entry if signal.direction == "LONG" else entry
        lowest_price = entry if signal.direction == "SHORT" else entry

        max_hold = self._get_max_hold()
        end_idx = min(entry_idx + max_hold, len(df_15m))

        for i in range(entry_idx, end_idx):
            candle = df_15m.iloc[i]
            h, l = candle["high"], candle["low"]

            # MFE / MAE
            if signal.direction == "LONG":
                favorable = (h - entry) / tp2_movement * 100 if tp2_movement > 0 else 0
                adverse = (entry - l) / sl_distance * 100 if sl_distance > 0 else 0
            else:
                favorable = (entry - l) / tp2_movement * 100 if tp2_movement > 0 else 0
                adverse = (h - entry) / sl_distance * 100 if sl_distance > 0 else 0

            max_favorable = max(max_favorable, favorable)
            max_adverse = max(max_adverse, adverse)

            if signal.direction == "LONG":
                # PRIORIDAD: TP1 se evalúa ANTES que SL en la misma vela
                if not tp1_hit and h >= tp1:
                    tp1_hit = True
                    trade.tp1_hit = True
                    # Breakeven offset: subimos SL ligeramente sobre entry para cubrir comisiones
                    current_sl = entry * (1 + BREAKEVEN_OFFSET_PCT)
                    breakeven_hit = True

                    if h >= tp2:
                        exit_price = tp2
                        trade.exit_price = exit_price
                        trade.close_reason = "tp2"
                        trade.tp2_hit = True
                        trade.exit_time = candle["timestamp"]
                        trade.duration_candles = i - entry_idx
                        break

                # Breakeven dinámico
                if not tp1_hit and entry > 0:
                    progreso = (h - entry) / (tp1 - entry) if (tp1 - entry) > 0 else 0
                    if progreso >= breakeven_trigger and not breakeven_hit:
                        # Breakeven offset: SL sobre entry para cubrir comisiones
                        current_sl = entry * (1 + BREAKEVEN_OFFSET_PCT)
                        breakeven_hit = True

                # SL check (después de TP1)
                if l <= current_sl:
                    exit_price = current_sl - current_sl * effective_slippage_bps
                    # Fix #10: Spread en salida
                    exit_price -= exit_price * SPREAD_BPS
                    trade.exit_price = exit_price
                    trade.close_reason = "stop_loss" if not tp1_hit else "tp1_breakeven"
                    trade.exit_time = candle["timestamp"]
                    trade.duration_candles = i - entry_idx
                    break

                # Trailing stop
                if tp1_hit:
                    if h > highest_price:
                        highest_price = h
                    precio_objetivo_trail = entry + tp2_movement * trailing_trigger
                    if h >= precio_objetivo_trail and not trailing_active:
                        if candle["close"] >= precio_objetivo_trail:
                            trailing_active = True
                    if trailing_active:
                        new_sl = highest_price - trailing_distance
                        if new_sl > current_sl:
                            current_sl = new_sl

                if tp1_hit and h >= tp2:
                    exit_price = tp2
                    trade.exit_price = exit_price
                    trade.close_reason = "tp2"
                    trade.tp2_hit = True
                    trade.exit_time = candle["timestamp"]
                    trade.duration_candles = i - entry_idx
                    break

            else:  # SHORT
                if not tp1_hit and l <= tp1:
                    tp1_hit = True
                    trade.tp1_hit = True
                    # Breakeven offset: bajamos SL ligeramente bajo entry para cubrir comisiones
                    current_sl = entry * (1 - BREAKEVEN_OFFSET_PCT)
                    breakeven_hit = True

                    if l <= tp2:
                        exit_price = tp2
                        trade.close_reason = "tp2"
                        trade.tp2_hit = True
                        trade.exit_time = candle["timestamp"]
                        trade.duration_candles = i - entry_idx
                        break

                if not tp1_hit and entry > 0:
                    progreso = (entry - l) / (entry - tp1) if (entry - tp1) > 0 else 0
                    if progreso >= breakeven_trigger and not breakeven_hit:
                        # Breakeven offset: SL bajo entry para cubrir comisiones
                        current_sl = entry * (1 - BREAKEVEN_OFFSET_PCT)
                        breakeven_hit = True

                if h >= current_sl:
                    exit_price = current_sl + current_sl * effective_slippage_bps
                    # Fix #10: Spread en salida
                    exit_price += exit_price * SPREAD_BPS
                    trade.exit_price = exit_price
                    trade.close_reason = "stop_loss" if not tp1_hit else "tp1_breakeven"
                    trade.exit_time = candle["timestamp"]
                    trade.duration_candles = i - entry_idx
                    break

                # Trailing stop para SHORT
                if tp1_hit:
                    if l < lowest_price:
                        lowest_price = l
                    precio_objetivo_trail = entry - tp2_movement * trailing_trigger
                    if l <= precio_objetivo_trail and not trailing_active:
                        if candle["close"] <= precio_objetivo_trail:
                            trailing_active = True
                    if trailing_active:
                        new_sl = lowest_price + trailing_distance
                        if new_sl < current_sl:
                            current_sl = new_sl

                if tp1_hit and l <= tp2:
                    exit_price = tp2
                    trade.close_reason = "tp2"
                    trade.tp2_hit = True
                    trade.exit_time = candle["timestamp"]
                    trade.duration_candles = i - entry_idx
                    break

        # ── Timeout: cerrar al cierre de la última vela ──
        if trade.exit_price == 0:
            last = df_15m.iloc[min(end_idx - 1, len(df_15m) - 1)]
            exit_price = last["close"]
            if signal.direction == "LONG":
                exit_price -= exit_price * effective_slippage_bps
                exit_price -= exit_price * SPREAD_BPS
            else:
                exit_price += exit_price * effective_slippage_bps
                exit_price += exit_price * SPREAD_BPS
            trade.exit_price = exit_price
            trade.close_reason = "timeout"
            trade.exit_time = last["timestamp"]
            trade.duration_candles = max_hold

        # ── PnL y comisiones ──
        if signal.direction == "LONG":
            trade.gross_pnl_usdt = (trade.exit_price - entry) * qty
        else:
            trade.gross_pnl_usdt = (entry - trade.exit_price) * qty

        if trade.close_reason in ("tp2",):
            commission_exit = self._calc_commission(trade.exit_price, qty, is_taker=False)
        else:
            commission_exit = self._calc_commission(trade.exit_price, qty, is_taker=True)

        trade.commission_usdt = commission_entry + commission_exit
        trade.pnl_usdt = trade.gross_pnl_usdt - trade.commission_usdt

        if signal.direction == "LONG":
            trade.pnl_pct = (trade.exit_price - entry) / entry * 100
        else:
            trade.pnl_pct = (entry - trade.exit_price) / entry * 100

        trade.breakeven_hit = breakeven_hit
        trade.max_favorable_excursion = round(max_favorable, 1)
        trade.max_adverse_excursion = round(max_adverse, 1)

        return trade

    # ── Loop principal de backtesting ──────────────────────────

    def run(self, start_date: str, end_date: str, capital: float = 100,
            override_params: Optional[dict[str, Any]] = None,
            cancel_flag: Optional[threading.Event] = None) -> BacktestResult:
        result = BacktestResult(
            symbol=self.symbol,
            start_date=start_date,
            end_date=end_date,
            capital_inicial=capital,
            fase_num=self.fase_num,
        )

        # Fijar balance y obtener config
        self.risk_mgr.set_balance(capital)
        _, self.fase_cfg = self._run_async(self.risk_mgr.get_phase_config())
        if override_params:
            self.fase_cfg.update(override_params)
            self.strategy._override_cfg = dict(override_params)
            self.risk_mgr._override_cfg = dict(override_params)

        # Resetear historial de ATR para slippage dinámico
        self._atr_pct_history = []

        logger.info("Descargando datos históricos...")
        df_1h = self.get_history_cached(start_date, end_date, TIMEFRAME_PRIMARY)
        df_15m = self.get_history_cached(start_date, end_date, TIMEFRAME_ENTRY)
        if df_1h.empty or df_15m.empty:
            logger.error("No se pudieron obtener datos históricos.")
            return result

        total_velas = len(df_15m)
        logger.info(f"Velas 15m: {total_velas}, 1h: {len(df_1h)}")

        logger.info("Precalculando indicadores...")
        indicators_15m = []
        # Fix #1: ventana [i-WARMUP : i] — EXCLUYE la vela actual
        for i in range(self.WARMUP, total_velas):
            window = df_15m.iloc[i - self.WARMUP : i]
            ind = self.indicators.calc_15m_indicators(window)
            indicators_15m.append(ind)

        logger.info("Iniciando simulación...")
        equity = capital
        max_equity = capital
        consec_losses = 0
        # Fix #1: Cooldown basado en velas manejado por strategy.tick_cooldown()
        # Fix #3: Circuit breaker — fase 0 permite max 2 pérdidas consecutivas

        # Para métricas de scoring
        scores_vistos: list[float] = []

        # Fix #11: equity curve diaria
        daily_equity: dict[str, float] = {}

        # Configurar timezone para filtro de sesión
        tz = pytz.timezone("America/Asuncion")

        # Precargar timestamps 1h
        df_1h_ts = df_1h["timestamp"].values

        # ── Fix #3b: Reset diario de pérdidas consecutivas (refleja reset_daily_if_needed del bot real) ──
        ultimo_dia_reset: str = ""

        for idx, i in enumerate(range(self.WARMUP, total_velas - 1)):
            if idx % 500 == 0:
                logger.info(f"Progreso: {idx}/{len(indicators_15m)} velas")
            if cancel_flag and cancel_flag.is_set():
                logger.info("Backtest cancelado por el usuario")
                break

            # Fix #1: ts de la vela ANTERIOR (i-1), no la actual
            #         current_price = close de i-1 (último dato conocido)
            #         La señal se evalúa al inicio de la vela i
            ts = df_15m.iloc[i]["timestamp"]  # timestamp de la vela actual (para filtro sesión)

            # Fix #1 + #5: current_price es el close de la vela i-1
            current_price = df_15m.iloc[i - 1]["close"]

            # También usar timestamp i-1 para searchsorted (evitar look-ahead en 1h)
            ts_prev = df_15m.iloc[i - 1]["timestamp"]

            _ind_15m = indicators_15m[idx]

            # ── Fix #3b: Reset diario de consec_losses (el bot real lo hace vía reset_daily_if_needed) ──
            dia_actual = ts.strftime("%Y-%m-%d")
            if dia_actual != ultimo_dia_reset:
                if consec_losses > 0:
                    logger.debug(f"🔁 Reset diario: consec_losses {consec_losses} → 0 (nuevo día {dia_actual})")
                consec_losses = 0
                ultimo_dia_reset = dia_actual
                # También reseteamos cooldown de estrategia al inicio del día
                self.strategy.reset_cooldown()

            # ── Fix #2: Trackear ATR rolling ──
            atr_pct = _ind_15m.get("ATR_pct", 0)
            if atr_pct > 0:
                self._atr_pct_history.append(atr_pct)

            # Ventana de 1h (usando ts_prev para evitar look-ahead)
            n_1h = np.searchsorted(df_1h_ts, ts_prev, side="right")  # type: ignore[arg-type]
            if n_1h < 14:
                continue
            n_1h = min(n_1h, self.WARMUP)
            df_1h_window = df_1h.iloc[n_1h - self.WARMUP : n_1h] if n_1h >= self.WARMUP else df_1h.iloc[:n_1h]
            if len(df_1h_window) < 14:
                continue

            # ── Fix #1: Avanzar cooldown de la estrategia (1 tick por vela 15m) ──
            self.strategy.tick_cooldown()

            # ── Fix #3: Circuit breaker — pausar si demasiadas pérdidas consecutivas ──
            max_consecutive = self.fase_cfg.get("max_consecutive_losses", 2)
            if consec_losses >= max_consecutive:
                continue

            # Filtro de sesión horaria
            sesion_inicio = self.fase_cfg.get("sesion_inicio", 14)
            sesion_fin = self.fase_cfg.get("sesion_fin", 22)
            ts_utc = ts.tz_localize('UTC') if ts.tzinfo is None else ts
            hora_local = ts_utc.astimezone(tz).hour
            if hora_local < sesion_inicio or hora_local >= sesion_fin:
                continue

            # ── Filtro de día de semana excluido (ej: martes tóxico) ──
            dias_excluir = self.fase_cfg.get("dias_excluir", ["Tuesday"])
            dia_semana = ts_utc.astimezone(tz).strftime("%A")  # Monday, Tuesday, ...
            if dia_semana in dias_excluir:
                continue

            # ── Evaluar señal (con datos hasta i-1) ──
            # Fix #1: ventana EXCLUYE la vela actual i
            df_15m_window = df_15m.iloc[i - self.WARMUP : i]
            signal = self.strategy.evaluate(df_1h_window, df_15m_window, current_price, equity)
            scores_vistos.append(signal.score if signal else 0)

            # ── Fix #4: Primera hora de sesión (14:00-15:00 local) más exigente ──
            # El 53% de pérdidas ocurren en esta franja. Requerir score ≥ 70.
            # Relajado de 80 → 70 para backtest exploratorio de 4 meses.
            hora_riesgo = (hora_local == sesion_inicio)
            if hora_riesgo and signal.has_signal and signal.score < 70:
                logger.debug(f"⏰ Hora riesgo (14:00-15:00): score {signal.score} < 70 → señal ignorada")
                continue

            if signal.has_signal:
                # Validaciones
                sl_ok = self._run_async(self.risk_mgr.validate_sl_distance(signal.entry, signal.sl))
                rr_ok = self._run_async(self.risk_mgr.validate_rr(signal.entry, signal.tp2, signal.sl))
                qty, _, msg = self._run_async(self.risk_mgr.calculate_position_size(signal.entry, signal.sl))

                if not sl_ok or not rr_ok or qty <= 0:
                    logger.debug(
                        f"Señal rechazada: SL_ok={sl_ok} RR_ok={rr_ok} "
                        f"Qty={qty:.2f} Msg={msg} | Score={signal.score}"
                    )
                    continue

                # ── Fix #1 + #5: entry_idx = i (vela actual, entrada al open) ──
                trade = self._simulate(signal, df_15m, i, equity, qty, atr_pct=atr_pct)
                if trade.qty <= 0 or trade.duration_candles <= 0:
                    continue

                # Fix #6: Clasificar régimen por ADX
                adx_val = _ind_15m.get("ADX_1h", 0) or 0
                # Tomar ADX de la ventana 1h
                # Ya tenemos ind_1h, pero no está aislado. Usamos el ADX de strategy.
                # Vamos a calcularlo inline:
                try:
                    ind_1h_local = self.indicators.calc_1h_indicators(df_1h_window)
                    adx_val = ind_1h_local.get("ADX", 0)
                except Exception:
                    adx_val = 0

                if adx_val > 25:
                    regime = "trend"
                elif adx_val >= 15:
                    regime = "transition"
                else:
                    regime = "ranging"
                trade.adx_entry = adx_val
                trade.market_regime = regime

                equity += trade.pnl_usdt
                result.trades.append(trade)
                result.equity_curve.append(equity)
                result.equity_dates.append(ts)

                # Fix #11: Trackear equity diaria
                day_key = ts.strftime("%Y-%m-%d")
                daily_equity[day_key] = equity

                # Sincronizar risk_manager con equity actual
                self.risk_mgr.update_backtest_balance(equity)

                if trade.pnl_usdt > 0:
                    result.wins += 1
                    consec_losses = 0
                    # Fix #3 + Fix E: Notificar a la estrategia (resetea racha)
                    self.strategy.register_trade_outcome(signal.direction or "LONG", was_loss=False)
                else:
                    result.losses += 1
                    consec_losses += 1
                    result.max_consecutive_losses = max(
                        result.max_consecutive_losses, consec_losses
                    )
                    # Fix E: Notificar a la estrategia (incrementa racha)
                    self.strategy.register_trade_outcome(signal.direction or "SHORT", was_loss=True)

                # Max drawdown
                if equity > max_equity:
                    max_equity = equity
                dd = (max_equity - equity) / max_equity * 100 if max_equity > 0 else 0
                result.max_drawdown_pct = max(result.max_drawdown_pct, dd)

        if scores_vistos:
            logger.info(
                f"📊 Scores — max:{max(scores_vistos)} "
                f"ceros:{scores_vistos.count(0)}/{len(scores_vistos)} "
                f"avg:{np.mean(scores_vistos):.1f}"
            )

        # ── Calcular métricas avanzadas ─────────────────────
        result.total_trades = len(result.trades)
        if result.total_trades > 0:
            result.win_rate = result.wins / result.total_trades * 100
            result.total_pnl_usdt = equity - capital
            result.total_pnl_pct = result.total_pnl_usdt / capital * 100
            wins_pnl = [t.pnl_pct for t in result.trades if t.pnl_usdt > 0]
            losses_pnl = [t.pnl_pct for t in result.trades if t.pnl_usdt <= 0]
            result.avg_win_pct = float(np.mean(wins_pnl)) if wins_pnl else 0.0
            result.avg_loss_pct = float(np.mean(losses_pnl)) if losses_pnl else 0.0
            gross_profit = sum(t.pnl_usdt for t in result.trades if t.pnl_usdt > 0)
            gross_loss = abs(sum(t.pnl_usdt for t in result.trades if t.pnl_usdt < 0))
            result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999

            result.total_commission_usdt = sum(t.commission_usdt for t in result.trades)
            result.commission_pct_of_capital = result.total_commission_usdt / capital * 100

            # Fix #10: Spread total
            result.total_spread_usdt = sum(t.spread_cost_usdt for t in result.trades)
            result.avg_slippage_bps = float(np.mean([t.slippage_entry_bps for t in result.trades]))

            # ── Fix #11: Sharpe / Sortino sobre equity diaria ──
            if len(daily_equity) > 1:
                daily_series = pd.Series(daily_equity)
                daily_series.index = pd.to_datetime(list(daily_equity.keys()))
                daily_series = daily_series.sort_index()
                daily_returns = daily_series.pct_change().dropna()

                if len(daily_returns) > 1:
                    avg_daily_return = daily_returns.mean()
                    std_daily = daily_returns.std()

                    total_days = (daily_series.index[-1] - daily_series.index[0]).days
                    if total_days < 1:
                        total_days = 1
                    periods_per_year = 365 / total_days

                    rf_daily = 0.02 / 365
                    excess_daily = daily_returns - rf_daily

                    if std_daily > 0:
                        result.sharpe_ratio = (
                            excess_daily.mean() / std_daily
                        ) * np.sqrt(365)
                    else:
                        result.sharpe_ratio = 0

                    downside_daily = daily_returns[daily_returns < 0]
                    if len(downside_daily) > 0 and downside_daily.std() > 0:
                        result.sortino_ratio = (
                            excess_daily.mean() / downside_daily.std()
                        ) * np.sqrt(365)
                    else:
                        result.sortino_ratio = 0
            # Fallback: si no hay suficientes días, usar método anterior
            if result.sharpe_ratio == 0 and len(result.trades) > 1:
                returns = pd.Series([t.pnl_pct / 100 for t in result.trades])
                try:
                    t0 = pd.Timestamp(result.trades[0].entry_time)  # type: ignore[arg-type]
                    t1 = pd.Timestamp(result.trades[-1].exit_time)  # type: ignore[arg-type]
                    total_days = max((t1 - t0).days, 1)
                except Exception:
                    total_days = 30
                periods_per_year = max(365 / total_days, 1)
                rf_rate_daily = 0.02 / 365
                rf_rate_period = rf_rate_daily * (total_days / len(result.trades))
                excess = returns - rf_rate_period  # type: ignore[operator]
                if returns.std() > 0:
                    result.sharpe_ratio = (
                        excess.mean() / returns.std()
                    ) * np.sqrt(len(result.trades) * periods_per_year)
                downside = returns[returns < 0]
                if len(downside) > 0 and downside.std() > 0:
                    result.sortino_ratio = (
                        excess.mean() / downside.std()
                    ) * np.sqrt(len(downside) * periods_per_year)

            result.calmar_ratio = result.total_pnl_pct / result.max_drawdown_pct if result.max_drawdown_pct > 0 else 0
            result.avg_trade_duration_candles = float(np.mean([t.duration_candles for t in result.trades]))
            result.avg_mfe_pct = float(np.mean([t.max_favorable_excursion for t in result.trades]))
            result.avg_mae_pct = float(np.mean([t.max_adverse_excursion for t in result.trades]))
            be_count = sum(1 for t in result.trades if t.breakeven_hit)
            result.breakeven_rate = be_count / result.total_trades * 100

            longs = [t for t in result.trades if t.signal_type == "LONG"]
            shorts = [t for t in result.trades if t.signal_type == "SHORT"]
            result.long_trades = len(longs)
            result.short_trades = len(shorts)
            result.long_win_rate = sum(1 for t in longs if t.pnl_usdt > 0) / len(longs) * 100 if longs else 0
            result.short_win_rate = sum(1 for t in shorts if t.pnl_usdt > 0) / len(shorts) * 100 if shorts else 0

            result.best_trade_pnl = max(t.pnl_usdt for t in result.trades)
            result.worst_trade_pnl = min(t.pnl_usdt for t in result.trades)

            # ── Fix #6: Stats por régimen ──
            trend_trades = [t for t in result.trades if t.market_regime == "trend"]
            transition_trades = [t for t in result.trades if t.market_regime == "transition"]
            ranging_trades = [t for t in result.trades if t.market_regime == "ranging"]

            result.regime_trend_trades = len(trend_trades)
            result.regime_trend_win_rate = sum(1 for t in trend_trades if t.pnl_usdt > 0) / len(trend_trades) * 100 if trend_trades else 0
            result.regime_transition_trades = len(transition_trades)
            result.regime_transition_win_rate = sum(1 for t in transition_trades if t.pnl_usdt > 0) / len(transition_trades) * 100 if transition_trades else 0
            result.regime_ranging_trades = len(ranging_trades)
            result.regime_ranging_win_rate = sum(1 for t in ranging_trades if t.pnl_usdt > 0) / len(ranging_trades) * 100 if ranging_trades else 0

        # ── Fix #7: Monte Carlo ──
        if result.total_trades >= 10:
            mc_result = self._monte_carlo_drawdown(result.trades, capital, n_simulations=1000)
            result.mc_drawdown_p95 = mc_result["dd_p95"]
            result.mc_drawdown_mean = mc_result["dd_mean"]

        return result

    # ── Fix #7: Monte Carlo para drawdown ───────────────────────

    @staticmethod
    def _monte_carlo_drawdown(trades: list[BacktestTrade], initial_capital: float,
                               n_simulations: int = 1000) -> dict[str, float]:
        """
        Baraja los trades aleatoriamente n_simulations veces y calcula
        la distribución de max drawdowns.

        Returns:
            dict con "dd_p95" (percentil 95) y "dd_mean".
        """
        pnl_list = np.array([t.pnl_usdt for t in trades])
        n = len(pnl_list)
        max_dds = []

        for _ in range(n_simulations):
            shuffled = np.random.permutation(pnl_list)
            equity_curve = initial_capital + np.cumsum(shuffled)
            running_max = np.maximum.accumulate(equity_curve)
            dd_pct = (running_max - equity_curve) / running_max * 100
            max_dds.append(np.max(dd_pct))

        max_dds = np.array(max_dds)
        return {
            "dd_p95": float(np.percentile(max_dds, 95)),
            "dd_mean": float(np.mean(max_dds)),
        }

    # ── Fix #8 (en optimizer.py) y Fix #4 (en optimizer.py) ──
    # Los métodos de walk-forward testing y análisis de sensibilidad
    # se implementan en bot/core/optimizer.py
