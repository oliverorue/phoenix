#!/usr/bin/env python3
"""Monitor de señales Phoenix — solo lectura, NO ejecuta órdenes.

Usa datos públicos de Binance USDⓈ-M Futures (sin API keys) y la misma
estrategia que el bot. Imprime una ficha con entrada, SL, TP1/TP2 y el
tamaño sugerido para el capital real del operador.

Uso:
    python bot/monitor.py --once
    python bot/monitor.py --once --symbol XRPUSDT --capital-pyg 100000 --fx 7300
    python bot/monitor.py                # loop; evalúa al cerrar cada vela 15m
"""

from __future__ import annotations

import argparse
import asyncio
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pytz

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from loguru import logger  # noqa: E402

from config.loader import (  # noqa: E402
    FUNDING_RATE_ENABLED,
    MONITOR_CFG,
    SYMBOL,
    TIMEZONE_NAME,
    TIMEFRAME_ENTRY,
    TIMEFRAME_PRIMARY,
    VELAS_LIMIT_ENTRY,
    VELAS_LIMIT_PRIMARY,
    get_phase_config,
)
from bot.core.data_feed import DataFeed  # noqa: E402
from bot.core.indicators import Indicators  # noqa: E402
from bot.core.notifier import Notifier  # noqa: E402
from bot.core.strategy_engine import ScalpingStrategy, Signal  # noqa: E402


class MonitorError(RuntimeError):
    """Error recuperable al evaluar un símbolo en el monitor."""


@dataclass
class Sizing:
    """Resultado del dimensionamiento para una operación manual."""

    capital_usdt: float
    risk_usdt: float
    risk_pct: float
    qty: float
    notional_usdt: float
    risk_actual_usdt: float
    risk_actual_pct: float
    fees_usdt: float
    executable: bool
    risk_ok: bool
    note: str


@dataclass
class Card:
    """Ficha de monitoreo de un símbolo."""

    symbol: str
    now_local: datetime
    price: float
    signal: Signal
    sizing: Optional[Sizing]
    fase_num: int
    session_open: bool
    day_allowed: bool
    cooldown: int
    adx: float
    di_pos: float
    di_neg: float
    ema50: float
    ema20: float
    rsi: float
    atr_pct: float
    bb_width: float
    notes: list[str] = field(default_factory=list)


def _floor_step(value: float, step: float) -> float:
    """Redondea *value* hacia abajo al múltiplo de *step* (nunca hacia arriba)."""
    if step <= 0:
        return value
    return math.floor(value / step + 1e-12) * step


def _ceil_step(value: float, step: float) -> float:
    """Redondea *value* hacia arriba al múltiplo de *step*."""
    if step <= 0:
        return value
    return math.ceil(value / step - 1e-12) * step


def _score_factor(score: float) -> float:
    """Mismo escalado de riesgo por score que usa ``RiskManager``."""
    if score >= 85:
        return 1.0
    if score >= 72:
        return 0.75
    if score >= 65:
        return 0.50
    if score > 0:
        return 0.25
    return 1.0


def size_position(
    entry: float,
    sl: float,
    capital_usdt: float,
    risk_pct: float,
    qty_step: float,
    min_qty: float,
    min_notional: float,
    fee_pct: float = 0.0005,
    max_risk_pct_hard: float = 0.02,
    score: float = 0.0,
    max_notional_usdt: Optional[float] = None,
) -> Sizing:
    """Calcula cantidad sugerida respetando riesgo y mínimos de Binance.

    Aplica el mismo factor por score que ``RiskManager``. Si el riesgo
    configurado produce un nocional menor al mínimo del instrumento, la
    cantidad se fuerza al mínimo (redondeando hacia arriba) y se reporta
    cuánto riesgo real implica. ``max_notional_usdt`` limita el tamaño al
    margen disponible (capital × leverage × 0.8, como el bot live).
    """
    if entry <= 0 or sl <= 0 or capital_usdt <= 0:
        raise ValueError("entry, sl y capital deben ser positivos")
    if risk_pct <= 0:
        raise ValueError("risk_pct debe ser positivo")

    sl_distance = abs(entry - sl)
    if sl_distance <= 0:
        raise ValueError("entry y sl no pueden ser iguales")

    effective_risk_pct = risk_pct * _score_factor(score)
    risk_usdt = capital_usdt * effective_risk_pct
    qty_by_risk = risk_usdt / sl_distance
    qty_by_min_notional = min_notional / entry if min_notional > 0 else 0.0

    risk_qty = _floor_step(qty_by_risk, qty_step)
    min_qty_required = _ceil_step(
        max(qty_by_min_notional, min_qty), qty_step,
    )
    qty = max(risk_qty, min_qty_required)

    cap_applied = False
    if max_notional_usdt is not None and max_notional_usdt > 0:
        max_qty = _floor_step(max_notional_usdt / entry, qty_step)
        if qty > max_qty:
            qty = max_qty
            cap_applied = True

    notional = qty * entry
    risk_actual = qty * sl_distance
    risk_actual_pct = risk_actual / capital_usdt
    fees = notional * fee_pct * 2.0

    executable = (
        qty > 0
        and qty >= min_qty
        and notional >= min_notional * (1.0 - 1e-9)
    )
    risk_ok = risk_actual_pct <= max_risk_pct_hard + 1e-12
    forced_by_min = risk_qty < min_qty_required

    if not executable:
        note = (
            f"No ejecutable: qty {qty:g} / nocional ${notional:.2f} "
            f"no alcanza el mínimo (${min_notional:.2f}) o el margen."
        )
    elif not risk_ok:
        note = (
            f"NO OPERAR: el tamaño mínimo obliga a arriesgar "
            f"{risk_actual_pct * 100:.2f}% (> {max_risk_pct_hard * 100:.1f}%)."
        )
    elif cap_applied:
        note = (
            f"Tamaño limitado por capital/leverage; riesgo real "
            f"{risk_actual_pct * 100:.2f}%."
        )
    elif forced_by_min:
        note = (
            f"Tamaño forzado al mínimo nocional; riesgo real "
            f"{risk_actual_pct * 100:.2f}%."
        )
    else:
        note = "Riesgo dentro del límite configurado."

    return Sizing(
        capital_usdt=capital_usdt,
        risk_usdt=risk_usdt,
        risk_pct=effective_risk_pct,
        qty=qty,
        notional_usdt=notional,
        risk_actual_usdt=risk_actual,
        risk_actual_pct=risk_actual_pct,
        fees_usdt=fees,
        executable=executable,
        risk_ok=risk_ok,
        note=note,
    )


def _session_state(
    fase_cfg: dict,
    tz_name: str = TIMEZONE_NAME,
    now: Optional[datetime] = None,
) -> tuple[bool, bool]:
    """Devuelve (sesión_abierta, día_permitido) según la fase configurada."""
    try:
        tz = pytz.timezone(tz_name)
    except Exception:
        tz = pytz.timezone("America/Asuncion")

    ahora = now.astimezone(tz) if now is not None else datetime.now(tz)
    excluidos = {
        str(d).strip().lower()
        for d in (fase_cfg.get("dias_excluir", []) or [])
    }
    day_allowed = ahora.strftime("%A").lower() not in excluidos

    def _hour(value: object) -> int:
        return int(str(value).split(":")[0])

    inicio = _hour(fase_cfg.get("sesion_inicio", 14))
    fin = _hour(fase_cfg.get("sesion_fin", 22))
    if fin < inicio:
        session_open = ahora.hour >= inicio or ahora.hour < fin
    else:
        session_open = inicio <= ahora.hour < fin

    return session_open, day_allowed


def _fmt_price(value: float) -> str:
    return f"{value:.6f}".rstrip("0").rstrip(".")


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z")


def format_card(card: Card) -> str:
    """Renderiza la ficha legible para consola/Telegram."""
    lines = [
        "=" * 58,
        f" {card.symbol} | {_fmt_time(card.now_local)}",
        "=" * 58,
        f" Precio: {_fmt_price(card.price)}   Fase: {card.fase_num}",
        (
            f" Sesión: {'ABIERTA' if card.session_open else 'CERRADA'}"
            f" | Día permitido: {'SÍ' if card.day_allowed else 'NO'}"
            f" | Cooldown: {card.cooldown} vela(s)"
        ),
        (
            f" 1h: ADX {card.adx:.1f} (DI+ {card.di_pos:.1f} / "
            f"DI- {card.di_neg:.1f}) | EMA50 {_fmt_price(card.ema50)}"
        ),
        (
            f" 15m: RSI {card.rsi:.1f} | ATR {card.atr_pct * 100:.2f}% | "
            f"BBw {card.bb_width * 100:.2f}% | EMA20 {_fmt_price(card.ema20)}"
        ),
        "-" * 58,
    ]

    sig = card.signal
    if not sig.has_signal:
        lines.append(" SIN SEÑAL — la estrategia no habilitó entrada en esta vela.")
    else:
        sl_pct = abs(sig.entry - sig.sl) / sig.entry * 100
        tp1_pct = abs(sig.tp1 - sig.entry) / sig.entry * 100
        tp2_pct = abs(sig.tp2 - sig.entry) / sig.entry * 100
        lines.extend([
            f" SEÑAL {sig.direction} · score {sig.score}/110",
            f"   Entrada : {_fmt_price(sig.entry)}",
            f"   Stop Loss: {_fmt_price(sig.sl)}  ({sl_pct:.2f}%)",
            f"   TP1      : {_fmt_price(sig.tp1)}  (+{tp1_pct:.2f}%)  [cerrar 65%]",
            f"   TP2      : {_fmt_price(sig.tp2)}  (+{tp2_pct:.2f}%)",
        ])
        if sig.reason:
            lines.append(f"   Motivo   : {sig.reason}")

        sizing = card.sizing
        if sizing is not None:
            state = "OK" if (sizing.executable and sizing.risk_ok) else "BLOQUEADO"
            lines.extend([
                "",
                f" Tamaño sugerido: {sizing.qty:g} unidades "
                f"(nocional ${sizing.notional_usdt:.2f})",
                (
                    f" Riesgo real: ${sizing.risk_actual_usdt:.4f} "
                    f"({sizing.risk_actual_pct * 100:.2f}% del capital) | "
                    f"Fees est.: ${sizing.fees_usdt:.4f}"
                ),
                f" Estado: {state} — {sizing.note}",
            ])

    if card.notes:
        lines.append("")
        lines.extend(f" ! {note}" for note in card.notes)

    lines.append("=" * 58)
    return "\n".join(lines)


class SignalMonitor:
    """Evalúa señales con datos públicos y sugiere tamaño. No ejecuta."""

    def __init__(
        self,
        symbols: list[str],
        capital_pyg: float,
        fx_usd_pyg: float,
        risk_pct: Optional[float] = None,
        max_risk_pct_hard: float = 0.02,
        kline_limit: Optional[int] = None,
        use_mainnet_data: bool = True,
        notify: bool = False,
        session_only: bool = False,
        fee_pct: float = 0.0005,
    ) -> None:
        if fx_usd_pyg <= 0:
            raise ValueError("fx_usd_pyg debe ser positivo")
        if capital_pyg <= 0:
            raise ValueError("capital_pyg debe ser positivo")
        if not symbols:
            raise ValueError("se requiere al menos un símbolo")

        self.symbols = symbols
        self.capital_usdt = capital_pyg / fx_usd_pyg
        self.risk_pct = risk_pct
        self.max_risk_pct_hard = max_risk_pct_hard
        self.kline_limit = kline_limit
        self.session_only = session_only
        self.fee_pct = fee_pct

        testnet = not use_mainnet_data
        self._feeds = {s: DataFeed(symbol=s, testnet=testnet) for s in symbols}
        self._strategies = {s: ScalpingStrategy() for s in symbols}
        self._last_candle: dict[str, object] = {s: None for s in symbols}
        self.notifier = Notifier() if notify else None

        self._market_data: dict[str, Any] = {}
        if FUNDING_RATE_ENABLED:
            from bot.core.market_data import MarketDataProvider
            self._market_data = {
                s: MarketDataProvider(http_session=self._feeds[s]._client)
                for s in symbols
            }

    async def evaluate(self, symbol: str) -> Card:
        feed = self._feeds[symbol]
        strategy = self._strategies[symbol]

        limit_1h = self.kline_limit or VELAS_LIMIT_PRIMARY
        limit_15m = self.kline_limit or VELAS_LIMIT_ENTRY
        df_1h_full = await feed.get_klines(TIMEFRAME_PRIMARY, limit_1h)
        df_15m_full = await feed.get_klines(TIMEFRAME_ENTRY, limit_15m)
        if df_1h_full.empty or df_15m_full.empty:
            raise MonitorError(f"{symbol}: sin velas disponibles")

        # Binance incluye la vela en curso como última fila. Evaluar solo
        # velas cerradas evita repintado y señales sobre una barra parcial.
        df_1h = df_1h_full.iloc[:-1].reset_index(drop=True)
        df_15m = df_15m_full.iloc[:-1].reset_index(drop=True)
        if len(df_1h) < 15 or len(df_15m) < 20:
            raise MonitorError(f"{symbol}: historial insuficiente")

        candle_ts = df_15m["timestamp"].iloc[-1]
        if self._last_candle[symbol] is not None and candle_ts != self._last_candle[symbol]:
            strategy.tick_cooldown()
        self._last_candle[symbol] = candle_ts

        price = float(df_15m["close"].iloc[-1])
        fase_num, fase_cfg = get_phase_config(self.capital_usdt)
        risk_pct = self.risk_pct or float(fase_cfg.get("risk_per_trade", 0.006))

        ind_1h = Indicators.calc_1h_indicators(df_1h, fase_cfg)
        ind_15m = Indicators.calc_15m_indicators(df_15m, fase_cfg)
        ema50 = float(
            df_1h["close"].ewm(
                span=ScalpingStrategy.MA_TREND, adjust=False
            ).mean().iloc[-1]
        )

        market_signal = None
        provider = self._market_data.get(symbol)
        if provider is not None:
            fr_data = await provider.get_funding_rate(symbol)
            if fr_data:
                market_signal = provider.get_market_signal(symbol)

        signal = strategy.evaluate(
            df_1h, df_15m, price, self.capital_usdt,
            market_signal=market_signal,
        )

        sizing = None
        if signal.has_signal:
            info = await feed.get_instrument_info()
            leverage = float(fase_cfg.get("leverage", 1.0))
            max_notional_usdt = self.capital_usdt * leverage * 0.8
            sizing = size_position(
                entry=signal.entry,
                sl=signal.sl,
                capital_usdt=self.capital_usdt,
                risk_pct=risk_pct,
                qty_step=float(info["qtyStep"]),
                min_qty=float(info["minOrderQty"]),
                min_notional=float(info["minNotionalValue"]),
                fee_pct=self.fee_pct,
                max_risk_pct_hard=self.max_risk_pct_hard,
                score=float(signal.score),
                max_notional_usdt=max_notional_usdt,
            )

        session_open, day_allowed = _session_state(fase_cfg)
        notes = self._context_notes(signal, sizing, session_open, day_allowed)

        try:
            tz = pytz.timezone(TIMEZONE_NAME)
        except Exception:
            tz = pytz.timezone("America/Asuncion")

        return Card(
            symbol=symbol,
            now_local=datetime.now(tz),
            price=price,
            signal=signal,
            sizing=sizing,
            fase_num=fase_num,
            session_open=session_open,
            day_allowed=day_allowed,
            cooldown=strategy._cooldown_candles_remaining,
            adx=float(ind_1h.get("ADX", 0) or 0),
            di_pos=float(ind_1h.get("ADX_pos", 0) or 0),
            di_neg=float(ind_1h.get("ADX_neg", 0) or 0),
            ema50=ema50,
            ema20=float(ind_15m.get("EMA20", price) or price),
            rsi=float(ind_15m.get("RSI", 50) or 0),
            atr_pct=float(ind_15m.get("ATR_pct", 0) or 0),
            bb_width=float(ind_15m.get("BB_width", 0) or 0),
            notes=notes,
        )

    @staticmethod
    def _context_notes(
        signal: Signal,
        sizing: Optional[Sizing],
        session_open: bool,
        day_allowed: bool,
    ) -> list[str]:
        notes: list[str] = []
        if not session_open:
            notes.append("Fuera de la sesión configurada (hora local).")
        if not day_allowed:
            notes.append("Día excluido por configuración (dias_excluir).")
        if signal.has_signal and sizing is not None:
            if not sizing.executable:
                notes.append("Con este capital no se puede abrir tamaño válido.")
            elif not sizing.risk_ok:
                notes.append("El mínimo del exchange excede tu límite de riesgo.")
        return notes

    def _should_alert(self, card: Card) -> bool:
        if not card.signal.has_signal or self.notifier is None:
            return False
        if self.session_only and not (card.session_open and card.day_allowed):
            return False
        return True

    async def run_once(self) -> list[Card]:
        cards: list[Card] = []
        for symbol in self.symbols:
            try:
                card = await self.evaluate(symbol)
            except Exception as exc:
                print(f"[{symbol}] ERROR: {exc}")
                logger.warning(f"Monitor {symbol}: {exc}")
                continue

            print(format_card(card))
            cards.append(card)

            if self._should_alert(card):
                assert self.notifier is not None
                await asyncio.to_thread(
                    self.notifier.send,
                    f"📡 {symbol} {card.signal.direction}",
                    format_card(card),
                    severity="INFO",
                    alert_type="monitor_signal",
                )
        return cards

    async def run_forever(self) -> None:
        while True:
            await self.run_once()
            wait = DataFeed.seconds_until_next_candle(15)
            print(f"\n⏳ Próxima vela 15m en {wait / 60:.1f} min...\n")
            await asyncio.sleep(wait)


def _build_monitor_from_config() -> SignalMonitor:
    """Construye el monitor usando solo la sección `monitor` de settings.yaml."""
    risk_cfg = MONITOR_CFG.get("risk_per_trade")
    kline_cfg = MONITOR_CFG.get("kline_limit")
    return SignalMonitor(
        symbols=[s.upper() for s in (MONITOR_CFG.get("symbols") or [SYMBOL])],
        capital_pyg=float(MONITOR_CFG.get("capital_pyg", 100000.0)),
        fx_usd_pyg=float(MONITOR_CFG.get("fx_usd_pyg", 7300.0)),
        risk_pct=float(risk_cfg) if risk_cfg is not None else None,
        max_risk_pct_hard=float(MONITOR_CFG.get("max_risk_pct_hard", 0.02)),
        kline_limit=int(kline_cfg) if kline_cfg else None,
        use_mainnet_data=bool(MONITOR_CFG.get("use_mainnet_data", True)),
        notify=bool(MONITOR_CFG.get("notify", True)),
        session_only=bool(MONITOR_CFG.get("session_only", False)),
        fee_pct=float(MONITOR_CFG.get("fee_pct", 0.0005)),
    )


async def run_from_config(once: bool = False) -> None:
    """Ejecuta el monitor sin argparse usando `monitor.*` de settings.yaml."""
    if not bool(MONITOR_CFG.get("enabled", True)):
        print("Monitor deshabilitado (monitor.enabled: false).")
        return

    monitor = _build_monitor_from_config()
    print(
        f"Phoenix Monitor | {monitor.capital_usdt:.2f} USDT | "
        f"riesgo {monitor.risk_pct if monitor.risk_pct is not None else 'fase'} | "
        f"ejecución: NINGUNA"
    )
    try:
        await (monitor.run_once() if once else monitor.run_forever())
    except KeyboardInterrupt:
        print("\nMonitor detenido.")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Monitor de señales Phoenix (solo lectura, sin órdenes)",
    )
    parser.add_argument(
        "--symbol", action="append", dest="symbols",
        help="Símbolo a monitorear (repetible). Por defecto: monitor.symbols",
    )
    parser.add_argument(
        "--capital-pyg", type=float, default=None,
        help="Capital en guaraníes (default: monitor.capital_pyg)",
    )
    parser.add_argument(
        "--fx", type=float, dest="fx_usd_pyg", default=None,
        help="Guaraníes por USDT (default: monitor.fx_usd_pyg)",
    )
    parser.add_argument(
        "--risk", type=float, default=None,
        help="Riesgo por trade (fracción). Default: fase o monitor.risk_per_trade",
    )
    parser.add_argument(
        "--once", action="store_true", help="Evalúa una vez y sale",
    )
    parser.add_argument(
        "--no-notify", action="store_true", help="No enviar alertas",
    )
    parser.add_argument(
        "--session-only", action="store_true",
        help="Alertar solo dentro de sesión/día permitido",
    )
    parser.add_argument(
        "--testnet-data", action="store_true",
        help="Usar datos públicos de Testnet en vez de Mainnet",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()

    if not bool(MONITOR_CFG.get("enabled", True)):
        print("Monitor deshabilitado (monitor.enabled: false).")
        return

    symbols = args.symbols or list(MONITOR_CFG.get("symbols") or [SYMBOL])
    capital_pyg = (
        args.capital_pyg
        if args.capital_pyg is not None
        else float(MONITOR_CFG.get("capital_pyg", 100000.0))
    )
    fx_usd_pyg = (
        args.fx_usd_pyg
        if args.fx_usd_pyg is not None
        else float(MONITOR_CFG.get("fx_usd_pyg", 7300.0))
    )
    risk_cfg = MONITOR_CFG.get("risk_per_trade")
    risk_pct = args.risk if args.risk is not None else (
        float(risk_cfg) if risk_cfg is not None else None
    )
    notify = bool(MONITOR_CFG.get("notify", True)) and not args.no_notify
    session_only = args.session_only or bool(MONITOR_CFG.get("session_only", False))
    use_mainnet_data = bool(MONITOR_CFG.get("use_mainnet_data", True)) and not args.testnet_data
    kline_limit_cfg = MONITOR_CFG.get("kline_limit")
    kline_limit = int(kline_limit_cfg) if kline_limit_cfg else None

    monitor = SignalMonitor(
        symbols=[s.upper() for s in symbols],
        capital_pyg=capital_pyg,
        fx_usd_pyg=fx_usd_pyg,
        risk_pct=risk_pct,
        max_risk_pct_hard=float(MONITOR_CFG.get("max_risk_pct_hard", 0.02)),
        kline_limit=kline_limit,
        use_mainnet_data=use_mainnet_data,
        notify=notify,
        session_only=session_only,
        fee_pct=float(MONITOR_CFG.get("fee_pct", 0.0005)),
    )

    print(
        f"Phoenix Monitor | capital {capital_pyg:,.0f} PYG ≈ "
        f"${monitor.capital_usdt:.2f} USDT | "
        f"riesgo {risk_pct if risk_pct is not None else 'fase'} | "
        f"datos {'Mainnet' if use_mainnet_data else 'Testnet'} | "
        f"ejecución: NINGUNA"
    )

    try:
        asyncio.run(monitor.run_once() if args.once else monitor.run_forever())
    except KeyboardInterrupt:
        print("\nMonitor detenido.")


if __name__ == "__main__":
    main()
