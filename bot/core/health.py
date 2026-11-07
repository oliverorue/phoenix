"""
Monitor de salud del bot.
Ejecuta checks periódicos de sistema, API, latencia y datos como tarea asíncrona.
Se integra con el notifier para alertar proactivamente.
"""

import asyncio
import psutil
from datetime import datetime, timezone
from typing import Any
from loguru import logger
from bot.core.tracing import trace


class HealthMonitor:
    """Ejecuta health checks dentro del loop asíncrono del bot."""

    def __init__(
        self, data_feed: Any, notifier: Any = None, interval: int = 300,
        paper_mode: bool = False, balance_provider: Any = None,
    ) -> None:
        """
        Args:
            data_feed: instancia de DataFeed para verificar API y datos.
            notifier: instancia de Notifier para enviar alertas (opcional).
            interval: segundos entre chequeos (default 5 minutos).
            paper_mode: si True, no consulta endpoints privados.
            balance_provider: callable que devuelve el balance paper/live.
        """
        self.data_feed = data_feed
        self.notifier = notifier
        self.interval = interval
        self.paper_mode = paper_mode
        self.balance_provider = balance_provider
        self._consecutive_api_failures = 0
        self._consecutive_data_failures = 0
        self._consecutive_latency_warnings = 0
        # Umbrales de latencia en segundos
        self.latency_warning_p95 = 2.0   # alerta si p95 > 2s
        self.latency_critical_p95 = 5.0  # crítica si p95 > 5s

    async def run(self, stop_event: asyncio.Event) -> None:
        """Loop principal del monitor. Se ejecuta como tarea asíncrona."""
        logger.info("💚 Health monitor iniciado")
        while not stop_event.is_set():
            try:
                await self._run_checks()
            except Exception as e:
                logger.error(f"Error en health monitor: {e}")
            # Esperar hasta el próximo ciclo o hasta que se detenga el bot
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.interval)
                break  # Si se señaló stop, salir
            except asyncio.TimeoutError:
                pass  # Timeout normal, continuar

    @trace(attributes={"component": "HealthMonitor"})
    async def _run_checks(self) -> None:
        """Ejecuta todos los checks y notifica si algo está mal."""
        system_ok = await self._check_system()
        api_ok = await self._check_api()
        data_ok = await self._check_data()
        latency_ok = await self._check_latency()

        if not system_ok:
            logger.warning("⚠️ Health: Sistema con problemas")
        if not api_ok:
            self._consecutive_api_failures += 1
            logger.warning(f"⚠️ Health: API falló ({self._consecutive_api_failures} fallos consecutivos)")
        else:
            self._consecutive_api_failures = 0

        if not data_ok:
            self._consecutive_data_failures += 1
            logger.warning(f"⚠️ Health: Datos desactualizados ({self._consecutive_data_failures} fallos)")
        else:
            self._consecutive_data_failures = 0

        if not latency_ok:
            self._consecutive_latency_warnings += 1
        else:
            self._consecutive_latency_warnings = 0

        # Notificar si hay fallos repetidos
        if self.notifier:
            if self._consecutive_api_failures >= 3:
                self.notifier.send(
                    "🔴 API no responde",
                    f"La API de Binance falló {self._consecutive_api_failures} veces consecutivas.",
                    severity="CRITICAL",
                    alert_type="error"
                )
            if self._consecutive_data_failures >= 2:
                self.notifier.send(
                    "⚠️ Datos desactualizados",
                    f"Sin datos recientes durante {self._consecutive_data_failures * self.interval} segundos.",
                    severity="WARNING",
                    alert_type="error"
                )
            if self._consecutive_latency_warnings >= 2:
                stats = self.data_feed.get_all_latency_stats()
                worst = max(stats.values(), key=lambda s: s.get("p95", 0))
                self.notifier.send(
                    "🐢 Latencia alta en API",
                    f"p95 más alto: {worst['endpoint']} = {worst['p95']:.2f}s "
                    f"(umbral: {self.latency_warning_p95:.1f}s)",
                    severity="WARNING",
                    alert_type="error"
                )

    @trace(attributes={"component": "HealthMonitor"})
    async def _check_system(self) -> bool:
        """Verifica CPU y memoria. Retorna False si hay riesgo."""
        try:
            p = psutil.Process()
            cpu = p.cpu_percent(interval=1)
            mem = p.memory_info().rss / 1024 / 1024  # MB
            if cpu > 80:
                logger.warning(f"⚠️ CPU alta: {cpu:.1f}%")
                return False
            if mem > 500:
                logger.warning(f"⚠️ Memoria alta: {mem:.0f} MB")
                return False
            return True
        except Exception as e:
            logger.error(f"Error en check de sistema: {e}")
            return True  # No marcar fallo por error de monitoreo

    @trace(attributes={"component": "HealthMonitor"})
    async def _check_api(self) -> bool:
        """Verifica conectividad con Binance (o balance paper)."""
        if self.paper_mode:
            try:
                balance = (
                    float(self.balance_provider())
                    if self.balance_provider is not None
                    else 0.0
                )
                logger.debug(f"Health paper OK, balance virtual: {balance:.2f}")
                return True
            except Exception as e:
                logger.error(f"Health check paper falló: {e}")
                return False
        try:
            balance = await self.data_feed.get_balance()
            if balance <= 0:
                logger.warning("Health: API respondió pero balance es 0")
            else:
                logger.debug(f"Health API OK, balance: {balance:.2f}")
            return True
        except Exception as e:
            logger.error(f"Health check API falló: {e}")
            return False

    @trace(attributes={"component": "HealthMonitor"})
    async def _check_latency(self) -> bool:
        """
        Verifica que la latencia de las llamadas API esté dentro de lo aceptable.
        Retorna False si algún endpoint tiene p95 > umbral de advertencia.
        """
        try:
            stats = self.data_feed.get_all_latency_stats()
            if not stats:
                return True  # aún sin muestras
            
            all_ok = True
            for endpoint, st in stats.items():
                if st["count"] < 3:
                    continue  # pocas muestras, no concluyente
                p95 = st["p95"]
                if p95 > self.latency_critical_p95:
                    logger.warning(
                        f"🔴 Latencia crítica en {endpoint}: p95={p95:.2f}s "
                        f"(max={st['max']:.2f}s, n={st['count']})"
                    )
                    all_ok = False
                elif p95 > self.latency_warning_p95:
                    logger.warning(
                        f"🐢 Latencia alta en {endpoint}: p95={p95:.2f}s "
                        f"(max={st['max']:.2f}s, n={st['count']})"
                    )
                    all_ok = False
                else:
                    logger.debug(f"Latencia {endpoint}: p95={p95:.3f}s OK")
            return all_ok
        except Exception as e:
            logger.error(f"Error en check de latencia: {e}")
            return True  # no marcar fallo por error de monitoreo

    @trace(attributes={"component": "HealthMonitor"})
    async def _check_data(self) -> bool:
        """Verifica que los datos de velas estén actualizados."""
        try:
            df = await self.data_feed.current_15m()
            if df.empty:
                logger.warning("Health: Data feed vacío")
                return False
            last_ts = df["timestamp"].iloc[-1]
            age = (datetime.now(timezone.utc) - last_ts).total_seconds()
            if age > 1200:  # 20 minutos
                logger.warning(f"Health: Datos desactualizados ({age:.0f}s)")
                return False
            logger.debug(f"Health data OK, última vela hace {age:.0f}s")
            return True
        except Exception as e:
            logger.error(f"Health check datos falló: {e}")
            return False