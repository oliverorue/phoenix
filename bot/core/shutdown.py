"""
Graceful Shutdown — cierre limpio del bot ante SIGTERM/SIGINT/SIGHUP.

Fases del shutdown:
  1. Señalizar stop_event (detener nuevos ciclos)
  2. Esperar tareas en curso (máximo shutdown_timeout)
  3. Cerrar posiciones abiertas (opcional, según close_positions)
  4. Cancelar tareas internas (health, config_watcher)
  5. Detener WebSocket feeds
  6. Guardar estado final
  7. Cerrar sesiones HTTP
  8. Finalizar tracing
  9. Notificar cierre
"""

from __future__ import annotations

import asyncio
import inspect
import signal
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Callable

from loguru import logger

from config.loader import SHUTDOWN_TIMEOUT, SHUTDOWN_CLOSE_POSITIONS, SHUTDOWN_NOTIFY


# ── Tipos ───────────────────────────────────────────────────────────────────
ShutdownCallback = Callable[[], Any]


# ── Configuración ───────────────────────────────────────────────────────────
@dataclass
class ShutdownConfig:
    """Configuración del shutdown graceful.

    Attributes:
        timeout:       Tiempo máximo (segundos) esperando cleanup antes de forzar salida.
        close_positions: Si True, cierra posiciones abiertas durante el shutdown.
        notify:        Si True, envía notificación de cierre.
    """
    timeout: float = 30.0
    close_positions: bool = True
    notify: bool = True


# ── Excepción ───────────────────────────────────────────────────────────────
class ShutdownTimeoutError(Exception):
    """Se alcanzó el timeout durante el shutdown graceful."""


# ── GracefulShutdown ────────────────────────────────────────────────────────
class GracefulShutdown:
    """
    Orquestador de shutdown graceful para el TradingBot.

    Uso:
        shutdown = GracefulShutdown(bot, stop_event, config=...)
        shutdown.register_signals()
        # ...
        await shutdown.shutdown()
    """

    def __init__(
        self,
        bot: Any,
        stop_event: asyncio.Event,
        config: Optional[ShutdownConfig] = None,
    ) -> None:
        self._bot = bot
        self._stop_event = stop_event
        self._config = config or ShutdownConfig(
            timeout=SHUTDOWN_TIMEOUT,
            close_positions=SHUTDOWN_CLOSE_POSITIONS,
            notify=SHUTDOWN_NOTIFY,
        )
        self._tasks: dict[str, asyncio.Task] = {}
        self._callbacks: list[ShutdownCallback] = []
        self._shutdown_start: Optional[datetime] = None

    # ── Registro de señales ────────────────────────────────────────────────
    def register_signals(self) -> None:
        """Registra manejadores para SIGTERM, SIGINT y SIGHUP."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            try:
                loop.add_signal_handler(sig, self._on_signal)
            except (NotImplementedError, ValueError) as exc:
                logger.warning(f"No se pudo registrar signal handler para {sig.name}: {exc}")

    def _on_signal(self) -> None:
        """Callback invocado cuando llega una señal de terminación."""
        if self._stop_event.is_set():
            # Segunda señal → shutdown forzado
            logger.warning("🛑 Segunda señal recibida — Forzando shutdown inmediato")
            self._force_exit()
            return
        logger.info("🛑 Señal de terminación recibida — Iniciando shutdown graceful...")
        self._stop_event.set()

    @staticmethod
    def _force_exit() -> None:
        """Sale del proceso inmediatamente (segunda señal)."""
        import os
        os._exit(1)

    # ── Gestión de tareas internas ─────────────────────────────────────────
    def track_task(self, name: str, task: asyncio.Task) -> None:
        """Registra una tarea para ser cancelada durante el shutdown."""
        self._tasks[name] = task

    def register_callback(self, callback: ShutdownCallback) -> None:
        """Registra un callback a ejecutar durante la fase de limpieza."""
        self._callbacks.append(callback)

    # ── Shutdown principal ─────────────────────────────────────────────────
    async def shutdown(self) -> None:
        """
        Ejecuta todas las fases del shutdown graceful.

        Fases (con timeout global):
          1. Señalizar stop_event
          2. Esperar tareas en curso
          3. Cerrar posiciones abiertas
          4. Cancelar tareas internas trackeadas
          5. Ejecutar callbacks registrados
          6. Detener WebSocket feeds
          7. Guardar estado final
          8. Cerrar sesiones HTTP
          9. Finalizar tracing
         10. Notificar cierre
        """
        self._shutdown_start = datetime.now(timezone.utc)
        logger.info("🔻 Iniciando shutdown graceful...")

        try:
            await asyncio.wait_for(
                self._async_shutdown(),
                timeout=self._config.timeout,
            )
            elapsed = (datetime.now(timezone.utc) - self._shutdown_start).total_seconds()
            logger.info(f"✅ Shutdown graceful completado en {elapsed:.1f}s")
        except asyncio.TimeoutError:
            elapsed = (datetime.now(timezone.utc) - self._shutdown_start).total_seconds()
            logger.error(f"⏰ Shutdown timeout ({self._config.timeout}s) tras {elapsed:.1f}s — Forzando salida")
            self._force_exit()

    async def _async_shutdown(self) -> None:
        """Implementación asíncrona del shutdown (wrapped con timeout)."""
        bot = self._bot

        # ── Fase 1: stop_event ya está seteado ─────────────────────────
        # (quien llamó shutdown() ya debería haberlo hecho)
        if not self._stop_event.is_set():
            self._stop_event.set()

        # Pequeña pausa para que el bucle principal vea el evento
        await asyncio.sleep(0.1)

        # ── Fase 2: Cerrar posiciones abiertas ─────────────────────────
        if self._config.close_positions:
            await self._close_open_positions(bot)

        # ── Fase 3: Cancelar tareas internas trackeadas ───────────────
        await self._cancel_tracked_tasks()

        # ── Fase 4: Ejecutar callbacks registrados ────────────────────
        for callback in self._callbacks:
            try:
                result = callback()
                if hasattr(result, "__await__"):
                    await result
            except Exception as exc:
                logger.warning(f"⚠️ Error en shutdown callback: {exc}")

        # ── Fase 5: Detener WebSocket feeds ───────────────────────────
        if hasattr(bot, "ws_feeds"):
            for ws in bot.ws_feeds.values():
                try:
                    ws.stop()
                except Exception as exc:
                    logger.warning(f"⚠️ Error deteniendo WS feed: {exc}")

        # ── Fase 6: Guardar estado final ──────────────────────────────
        if hasattr(bot, "_save_cycle_state"):
            try:
                bot._save_cycle_state()
            except Exception as exc:
                logger.warning(f"⚠️ Error guardando estado final: {exc}")

        # ── Fase 7: Cerrar sesiones HTTP ──────────────────────────────
        # Nota: BinanceFuturesClient no tiene .close() sync.
        # El cierre de WS se maneja en ws_feed. No es necesario cerrar sesiones REST.

        # ── Fase 8: Finalizar tracing ─────────────────────────────────
        try:
            from bot.core.tracing import shutdown_tracing
            shutdown_tracing()
        except Exception as exc:
            logger.warning(f"⚠️ Error finalizando tracing: {exc}")

        # ── Fase 9: Notificar cierre ──────────────────────────────────
        if self._config.notify and hasattr(bot, "notifier") and self._shutdown_start is not None:
            try:
                elapsed = (datetime.now(timezone.utc) - self._shutdown_start).total_seconds()  # type: ignore[operator]
                bot.notifier.send(
                    "✅ Bot detenido",
                    f"Shutdown graceful completado en {elapsed:.1f}s",
                    severity="SUCCESS",
                    alert_type="shutdown",
                )
            except Exception as exc:
                logger.warning(f"⚠️ Error notificando cierre: {exc}")

    # ── Cierre de posiciones ───────────────────────────────────────────────
    async def _close_open_positions(self, bot: Any) -> None:
        """Cierra posiciones abiertas en todos los runners."""
        if not hasattr(bot, "runners"):
            return

        open_positions = [
            r for r in bot.runners
            if getattr(getattr(r, "executor", None), "position_open", False)
        ]
        if not open_positions:
            logger.info("📭 Sin posiciones abiertas para cerrar")
            return

        logger.info(f"🔒 Cerrando {len(open_positions)} posición(es) abierta(s)...")
        for runner in open_positions:
            symbol = runner.symbol if hasattr(runner, "symbol") else "?"
            executor = getattr(runner, "executor", None)
            try:
                if executor is None:
                    continue
                close_async = getattr(executor, "close_position_async", None)
                if callable(close_async):
                    result = close_async(reason="shutdown")
                    if inspect.isawaitable(result):
                        ok = await asyncio.shield(result)
                    else:
                        ok = bool(result)
                else:
                    result = executor.close_position()
                    if hasattr(result, "__await__"):
                        ok = await asyncio.shield(result)
                    else:
                        ok = True
                if ok:
                    logger.info(f"🔒 Posición cerrada: {symbol}")
                else:
                    logger.error(f"❌ No se pudo cerrar la posición {symbol}")
            except asyncio.CancelledError:
                logger.warning(f"⚠️ Cancelado cierre de posición {symbol}")
                break
            except Exception as exc:
                logger.warning(f"⚠️ Error cerrando posición {symbol}: {exc}")

    # ── Cancelación de tareas ──────────────────────────────────────────────
    async def _cancel_tracked_tasks(self) -> None:
        """Cancela todas las tareas internas trackeadas."""
        if not self._tasks:
            logger.debug("📭 Sin tareas internas para cancelar")
            return

        logger.info(f"🔻 Cancelando {len(self._tasks)} tarea(s) interna(s)...")
        for name, task in self._tasks.items():
            if task.done():
                continue
            task.cancel()
            try:
                await asyncio.shield(task)
            except (asyncio.CancelledError, Exception):
                pass
            logger.debug(f"   Tarea cancelada: {name}")


# ── Función auxiliar para crear instancia desde el bot ─────────────────────
def create_shutdown(
    bot: Any,
    stop_event: Optional[asyncio.Event] = None,
    timeout: Optional[float] = None,
    close_positions: Optional[bool] = None,
    notify: Optional[bool] = None,
) -> GracefulShutdown:
    """
    Crea un GracefulShutdown listo para usar.
    Toma defaults de config.loader si no se pasan explícitamente.
    """
    if stop_event is None:
        stop_event = asyncio.Event()
    cfg = ShutdownConfig(
        timeout=timeout if timeout is not None else SHUTDOWN_TIMEOUT,
        close_positions=close_positions if close_positions is not None else SHUTDOWN_CLOSE_POSITIONS,
        notify=notify if notify is not None else SHUTDOWN_NOTIFY,
    )
    return GracefulShutdown(bot=bot, stop_event=stop_event, config=cfg)
