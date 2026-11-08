"""
Puente asincrónico entre el scanner (asyncio) y la UI Streamlit (síncrona).

Streamlit re-ejecuta un script síncrono en cada interacción. Para no bloquear
la UI ni crear un event loop por rerun, el scanner corre en un **hilo daemon**
con su propio ``asyncio.run``, y Streamlit lee snapshots thread-safe en cada
rerun (con ``@st.cache_resource`` para crear una única instancia).

No hay estructuras compartidas sin protección: el scanner expone
``signals_snapshot()`` y ``stats()`` con lock interno.
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Optional

from loguru import logger

from bot.core.notifier import Notifier
from bot.execution.manager import TradeManager
from bot.execution.models import Direction, Trade
from bot.execution.progress import format_progress, stage_progress
from bot.execution.sizing import plan_trade
from bot.execution.store import TradeStore
from bot.scanner.config import ScannerConfig
from bot.scanner.market_scanner import MarketScanner
from bot.scanner.models import ScannerStats, Signal, Sizing
from bot.scanner.ranker import RankedSignal, SignalRanker
from bot.scanner.risk_engine import RiskEngine


class ScannerBridge:
    """Dueño del hilo del scanner y fachada para la UI."""

    def __init__(
        self,
        config: Optional[ScannerConfig] = None,
        base_risk_pct: float = 0.01,
    ) -> None:
        self._config = config or ScannerConfig.from_settings()
        self._dry_run = self._config.dry_run
        self.risk = RiskEngine(base_risk_pct=base_risk_pct)
        self.ranker = SignalRanker(self._config)
        self.trade_store = TradeStore()
        self.trades = TradeManager(
            store=self.trade_store, on_close=self._on_paper_close,
            max_hold_seconds=self._config.max_hold_seconds,
        )
        self.trades.load(self.trade_store)
        self.risk.load_history(self.trade_store.load_pnl())
        self.notifier = Notifier()
        self._scanner = MarketScanner(self._config)
        self._scanner.on_price = self.trades.on_price
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Arranca el scanner en un hilo daemon (idempotente)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="market-scanner",
        )
        self._thread.start()
        self._notify_async(self._send_start)

    def _run(self) -> None:
        try:
            asyncio.run(self._scanner.run())
        except Exception as exc:  # noqa: BLE001
            logger.error(f"[scanner] error fatal: {exc}")
            self._scanner._record_error(exc)

    def stop(self) -> None:
        self._scanner.stop()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def is_dry_run(self) -> bool:
        return self._dry_run

    def snapshot(self) -> tuple[list[Signal], ScannerStats, list[str]]:
        """(señales recientes, stats, símbolos rastreados) thread-safe."""
        return (
            self._scanner.signals_snapshot(),
            self._scanner.stats(),
            self._scanner.tracked_symbols(),
        )

    def top_signals(
        self,
        now: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> list[RankedSignal]:
        """Señales rankeadas (mejores primero), recortadas a ``limit``."""
        now = now if now is not None else time.time()
        ranked = self.ranker.rank(
            self._scanner.signals_snapshot(),
            now,
            self._scanner.volume_map(),
        )
        return ranked[: limit if limit is not None else self._config.top_n]

    def _on_paper_close(self, trade: Trade) -> None:
        """Alimenta el Kelly con el PnL del trade y notifica el cierre."""
        self.risk.record_trade(trade.realized_pnl)
        self._notify_async(self._send_trade_close, trade)

    def equity(self) -> float:
        """Capital actual: inicial ($15) + PnL realizado de los trades paper.

        En live se sincronizará con el balance USDT de Binance (100% del capital).
        """
        return self._config.paper_initial_capital + self.trades.stats()["pnl"]

    def suggest_sizing(self, signal: Signal) -> Sizing:
        """Riesgo automático para una señal: Kelly, con techo ``max_risk_pct``."""
        return self.risk.suggest(
            self.equity(), signal,
            base_risk_pct=self._config.max_risk_pct,
            max_risk_pct=self._config.max_risk_pct,
        )

    def open_paper_trade(self, signal: Signal) -> Optional[Trade]:
        """Abre un trade dry-run con capital y riesgo automáticos (Kelly)."""
        if signal.direction not in ("LONG", "SHORT"):
            return None
        sizing = self.suggest_sizing(signal)
        if sizing.risk_usdt <= 0:
            return None
        direction = Direction(signal.direction)
        plan = plan_trade(
            symbol=signal.symbol,
            direction=direction,
            price=signal.price,
            risk_usdt=sizing.risk_usdt,
            sl_distance_pct=self._config.sl_distance_pct,
            tp1_ratio=self._config.tp1_ratio,
            trailing_dist_pct=self._config.trailing_dist_pct,
        )
        trade = self.trades.open_trade(
            symbol=plan.symbol,
            direction=plan.direction,
            entry_price=plan.entry_price,
            sl_price=plan.sl_price,
            tp1_price=plan.tp1_price,
            tp2_price=plan.tp2_price,
            qty=plan.qty,
            tp1_ratio=plan.tp1_ratio,
            trailing_dist=plan.trailing_dist,
        )
        self._notify_async(self._send_trade_open, trade)
        return trade

    def paper_open_trades(self) -> list[Trade]:
        return self.trades.open_trades()

    def paper_closed_trades(self) -> list[Trade]:
        return self.trades.closed_trades()

    def paper_stats(self) -> dict:
        return self.trades.stats()

    def paper_open_positions(self) -> list[tuple[Trade, float, float]]:
        """(trade, unrealized_pnl, current_price) de los trades abiertos."""
        prices = self.trades.last_prices()
        result: list[tuple[Trade, float, float]] = []
        for trade in self.trades.open_trades():
            price = prices.get(trade.symbol, trade.entry_price)
            result.append((trade, self.trades.unrealized_pnl(trade), price))
        return result

    def kill_switch(self) -> int:
        """Cierra TODOS los trades paper abiertos al último precio conocido.

        Devuelve cuántos cerró. (Cuando se cablee el live, además cancelará las
        órdenes abiertas y aplanará las posiciones reales del exchange.)
        """
        prices = self.trades.last_prices()
        closed = 0
        for trade in self.trades.open_trades():
            price = prices.get(trade.symbol, trade.entry_price)
            if self.trades.close_manual(trade.id, price) is not None:
                closed += 1
        return closed

    # ── Notificaciones (Telegram/Discord) ────────────────
    def notifications_enabled(self) -> bool:
        notifier = getattr(self, "notifier", None)
        return notifier is not None and notifier.enabled

    def _notify_async(self, fn, *args) -> None:
        if not self.notifications_enabled():
            return
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _send_start(self) -> None:
        self.notifier.send(
            "🚀 PAPER INICIADO",
            f"Capital: ${self.equity():,.2f} · esperando señales",
            "INFO", "generic", force=True,
        )

    def _send_trade_open(self, trade: Trade) -> None:
        self.notifier.paper_open(trade, self.equity(), self.risk.multiplier())

    def _send_trade_close(self, trade: Trade) -> None:
        stats = self.trades.stats()
        progress = stage_progress(stats)
        self.notifier.paper_close(
            trade, self.equity(), self.risk.multiplier(), stats,
            format_progress(progress),
        )

    def _send_status(self) -> None:
        stats = self.trades.stats()
        progress = stage_progress(stats)
        self.notifier.paper_status(
            self.equity(), self.risk.multiplier(), stats,
            format_progress(progress),
        )

    def notify_status(self) -> None:
        """Envía un estado del paper a Telegram/Discord (no bloqueante)."""
        self._notify_async(self._send_status)

    def progress_line(self) -> str:
        """Línea de progreso hacia la siguiente etapa (gates)."""
        return format_progress(stage_progress(self.trades.stats()))
