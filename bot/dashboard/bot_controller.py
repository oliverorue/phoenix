"""
Controlador del Bot desde el Dashboard.
Inicia, detiene y reinicia el bot en un hilo separado.
Todas las operaciones son NO BLOQUEANTES para no congelar Streamlit.
"""

import asyncio
import os
import threading
import logging
from pathlib import Path
import sys
from typing import Any, Optional

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT_DIR))

from config.loader import MONITOR_ONLY
from bot.main import TradingBot
from bot.dashboard.shared_state import bot_state

logger = logging.getLogger("dashboard.controller")


class BotController:
    """Controla el ciclo de vida del bot sin bloquear la UI de Streamlit."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._bot: Any = None

    # ── Control habilitado ───────────────────────────────────

    @staticmethod
    def control_allowed() -> bool:
        """True si el dashboard puede iniciar/detener el bot.

        En modo monitor-only el control siempre está deshabilitado. En
        Docker/producción también se desactiva con
        ``PHOENIX_DASHBOARD_ALLOW_BOT_CONTROL=false`` para evitar un
        segundo bot contra la misma cuenta API.
        """
        if MONITOR_ONLY:
            return False
        return os.getenv(
            "PHOENIX_DASHBOARD_ALLOW_BOT_CONTROL", "true"
        ).strip().lower() in ("1", "true", "yes", "on")

    def can_start(self) -> bool:
        """True si el control está habilitado y no hay un bot corriendo."""
        return self.control_allowed() and not self.is_running()

    # ── Start no bloqueante ──────────────────────────────────

    def start(self) -> bool:
        """Inicia el bot. NO bloquea — actualiza estado inmediatamente."""
        if MONITOR_ONLY:
            logger.warning(
                "Control del bot deshabilitado: build monitor-only (sin ejecución)."
            )
            return False
        if not self.control_allowed():
            logger.warning(
                "Control del bot deshabilitado desde el dashboard "
                "(PHOENIX_DASHBOARD_ALLOW_BOT_CONTROL=false)"
            )
            return False

        if self.is_running():
            logger.warning("El bot ya está corriendo")
            return False

        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_bot, daemon=True, name="bot-main")
        self._thread.start()

        # No hacemos sleep — confiamos en que arrancará
        bot_state.update({"bot_running": True, "mode": "RUNNING"})
        logger.info("Bot iniciado (estado optimista)")
        return True

    # ── Stop no bloqueante ───────────────────────────────────

    def stop(self) -> bool:
        """
        Detiene el bot señalizando el evento.
        NO hace thread.join() para no congelar Streamlit.
        El hilo se detiene solo cuando el bot complete su ciclo.
        """
        if not self.is_running():
            bot_state.update({"bot_running": False, "mode": "STOPPED", "position": {"active": False}})
            return True

        logger.info("Señalizando parada del bot...")
        self._stop_event.set()
        bot_state.update({
            "bot_running": False,
            "mode": "STOPPED",
            "position": {"active": False},
        })
        return True

    # ── Restart no bloqueante ────────────────────────────────

    def restart(self) -> bool:
        """Detiene (sin bloqueo) y luego inicia."""
        self.stop()
        return self.start()

    # ── Estado ───────────────────────────────────────────────

    def is_running(self) -> bool:
        """¿El hilo del bot está vivo y no se ha pedido detenerlo?"""
        return (
            self._thread is not None
            and self._thread.is_alive()
            and not self._stop_event.is_set()
        )

    # ── Reinicio con nueva config ────────────────────────────

    def restart_with_config(self, portfolio: list[dict[str, Any]]) -> bool:
        """Reinicia el bot con una nueva configuración de portfolio."""
        self.stop()

        # Guardar la nueva configuración en settings.yaml
        import yaml
        settings_path = ROOT_DIR / "config" / "settings.yaml"
        try:
            with open(settings_path, "r") as f:
                settings = yaml.safe_load(f)
            settings.setdefault("portfolio", {})["symbols"] = portfolio
            with open(settings_path, "w") as f:
                yaml.dump(settings, f, default_flow_style=False, allow_unicode=True)
            logger.info("Portfolio actualizado en settings.yaml")
        except Exception as e:
            logger.error(f"Error al guardar settings.yaml: {e}")

        return self.start()

    # ── Hilo interno ─────────────────────────────────────────

    def _run_bot(self) -> None:
        """Ejecuta el bot en un bucle asyncio. Se ejecuta en un hilo daemon."""
        try:
            self._bot = TradingBot(state_manager=bot_state)
            async_stop = asyncio.Event()

            # Puente: threading.Event → asyncio.Event
            def watch_stop() -> None:
                self._stop_event.wait()
                async_stop.set()

            threading.Thread(target=watch_stop, daemon=True).start()

            asyncio.run(self._bot.run(stop_event=async_stop))
        except Exception as e:
            logger.error(f"Error fatal en el bot: {e}", exc_info=True)
            bot_state.add_error(str(e))
        finally:
            # El finally se ejecuta cuando el bot realmente termina
            bot_state.update({"bot_running": False, "mode": "STOPPED"})


# Instancia global (singleton) usada por el dashboard
controller = BotController()
