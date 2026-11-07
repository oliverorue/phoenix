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

from bot.scanner.config import ScannerConfig
from bot.scanner.market_scanner import MarketScanner
from bot.scanner.models import ScannerStats, Signal
from bot.scanner.ranker import RankedSignal, SignalRanker
from bot.scanner.risk_engine import RiskEngine
from bot.scanner.store import SignalStore


class ScannerBridge:
    """Dueño del hilo del scanner y fachada para la UI."""

    def __init__(
        self,
        config: Optional[ScannerConfig] = None,
        base_risk_pct: float = 0.01,
    ) -> None:
        self._config = config or ScannerConfig.from_settings()
        self.store = SignalStore()
        self.risk = RiskEngine(base_risk_pct=base_risk_pct)
        self.risk.load_history(self.store.load_pnl())
        self.ranker = SignalRanker(self._config)
        self._scanner = MarketScanner(self._config, on_signal=self.store.save)
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Arranca el scanner en un hilo daemon (idempotente)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="market-scanner",
        )
        self._thread.start()

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
