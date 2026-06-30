#!/usr/bin/env python3
"""
Watchdog avanzado: health checks de sistema, API y datos.
Reinicia el bot si detecta fallos críticos.
Notifica por Telegram/Discord cuando hay problemas.
"""

import asyncio
import subprocess
import sys
import time
from typing import Any
import psutil
from pathlib import Path
from datetime import datetime, timezone
from loguru import logger

logger.add("logs/watchdog.log", rotation="1 MB", retention="3 days", level="INFO")

ROOT = Path(__file__).resolve().parent.parent

MAX_CRASHES = 5
CRASH_WINDOW = 600
RESTART_DELAY = 15
HEALTH_INTERVAL = 300  # cada 5 minutos

# Notificador (se inicializa bajo demanda)
_notifier = None

def _get_notifier() -> Any:
    global _notifier
    if _notifier is None:
        try:
            from bot.core.notifier import Notifier
            _notifier = Notifier()
        except Exception:
            _notifier = None
    return _notifier


def check_system() -> bool:
    """Verifica CPU y memoria del proceso."""
    try:
        p = psutil.Process()
        cpu = p.cpu_percent(interval=1)
        mem = p.memory_info().rss / 1024 / 1024
        if cpu > 80:
            logger.warning(f"⚠️ CPU alta: {cpu:.1f}%")
        if mem > 500:
            logger.warning(f"⚠️ Memoria alta: {mem:.0f} MB")
        return True
    except Exception as e:
        logger.error(f"Error en health check de sistema: {e}")
        return True  # no reiniciar solo por error de monitoreo


async def _check_api_async() -> bool:
    """Versión async del health check de API."""
    from bot.core.data_feed import DataFeed
    feed = DataFeed(symbol="XRPUSDT", testnet=False)
    balance = await feed.get_balance()
    if balance <= 0:
        logger.warning("API respondió pero balance es 0")
    else:
        logger.debug(f"Health API OK, balance: {balance:.2f}")
    return True


async def _check_data_async() -> bool:
    """Versión async del health check de datos."""
    from bot.core.data_feed import DataFeed
    feed = DataFeed(symbol="XRPUSDT", testnet=False)
    df = await feed.current_15m()
    if df.empty:
        logger.warning("Data feed vacío")
        return False
    last_ts = df["timestamp"].iloc[-1]
    age = (datetime.now(timezone.utc) - last_ts).total_seconds()
    if age > 1200:  # 20 minutos
        logger.warning(f"Datos desactualizados: {age:.0f}s")
        return False
    logger.debug(f"Health data OK, última vela hace {age:.0f}s")
    return True


def check_api() -> bool:
    """Verifica conectividad con Bybit (wrapper sync)."""
    try:
        return asyncio.run(_check_api_async())
    except Exception as e:
        logger.error(f"Fallo health check API: {e}")
        return False


def check_data() -> bool:
    """Verifica que haya datos recientes (wrapper sync)."""
    try:
        return asyncio.run(_check_data_async())
    except Exception as e:
        logger.error(f"Fallo health check datos: {e}")
        return False


def run() -> None:
    crash_times: list[float] = []
    attempt = 0
    last_health: float = 0.0
    health_failures = 0
    notifier = _get_notifier()

    while True:
        attempt += 1
        logger.info(f"Iniciando bot (intento #{attempt})")
        if notifier:
            notifier.send("🤖 Watchdog", f"Iniciando bot (intento #{attempt})", "INFO", alert_type="watchdog_restart")

        proc = subprocess.Popen(
            [sys.executable, "-m", "bot.main"],
            cwd=ROOT,
        )

        while proc.poll() is None:
            now = time.time()
            if now - last_health >= HEALTH_INTERVAL:
                last_health = now
                check_system()
                api_ok = check_api()
                data_ok = check_data()

                if not api_ok:
                    health_failures += 1
                    if health_failures >= 3 and notifier:
                        notifier.send("⚠️ Watchdog: API caída",
                                      f"Bybit no responde tras {health_failures} chequeos",
                                      "WARNING", alert_type="watchdog_api")
                elif not data_ok:
                    health_failures += 1
                    if health_failures >= 3 and notifier:
                        notifier.send("⚠️ Watchdog: Datos desactualizados",
                                      "El feed de datos tiene más de 20 minutos",
                                      "WARNING", alert_type="watchdog_data")
                else:
                    health_failures = 0

            time.sleep(10)

        if proc.returncode == 0:
            logger.info("Bot detenido normalmente. Watchdog terminando.")
            if notifier:
                notifier.send("✅ Watchdog", "Bot detenido normalmente", "SUCCESS", alert_type="watchdog_stop")
            break

        now = time.time()
        crash_times = [t for t in crash_times if now - t < CRASH_WINDOW]
        crash_times.append(now)

        crash_count = len(crash_times)
        logger.warning(
            f"Bot terminó con código {proc.returncode} "
            f"({crash_count} crashes en {CRASH_WINDOW // 60} min)"
        )

        if notifier:
            notifier.send("🔄 Watchdog: Reinicio",
                          f"Bot crash con código {proc.returncode} ({crash_count}/{MAX_CRASHES} en {CRASH_WINDOW//60}min)",
                          "WARNING", alert_type="watchdog_crash")

        if crash_count >= MAX_CRASHES:
            logger.error(f"{MAX_CRASHES} crashes en {CRASH_WINDOW // 60} min. Deteniendo watchdog.")
            if notifier:
                notifier.send("🚨 Watchdog: Detenido",
                              f"{MAX_CRASHES} crashes consecutivos. Watchdog se detiene.",
                              "CRITICAL", alert_type="watchdog_stop")
            break

        logger.info(f"Reiniciando en {RESTART_DELAY}s...")
        try:
            time.sleep(RESTART_DELAY)
        except KeyboardInterrupt:
            logger.info("Watchdog detenido por usuario.")
            if notifier:
                notifier.send("⏹️ Watchdog", "Detenido por usuario", "INFO", alert_type="watchdog_stop")
            break


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        logger.info("Watchdog detenido por usuario.")