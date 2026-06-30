import asyncio
import shutil
from pathlib import Path
from datetime import datetime
from typing import Any
from loguru import logger

class BotScheduler:
    def __init__(self, database: Any, journal: object) -> None:
        self.database = database
        self.journal = journal
        self._tasks: list[asyncio.Task[Any]] = []

    async def start(self) -> None:
        self._tasks.append(asyncio.create_task(self._cleanup_logs_loop()))
        self._tasks.append(asyncio.create_task(self._cleanup_csv_loop()))
        self._tasks.append(asyncio.create_task(self._backup_db_loop()))
        self._tasks.append(asyncio.create_task(self._health_check_loop()))
        logger.info("📅 Scheduler iniciado")

    async def _cleanup_csv_loop(self) -> None:
        """Limpia archivos CSV antiguos de backtests/data cada 7 días."""
        while True:
            await asyncio.sleep(86400)  # cada 24h
            data_dir = Path("data")
            cutoff = datetime.now().timestamp() - 14 * 86400  # 14 días
            removed = 0
            for f in data_dir.glob("*.csv"):
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            if removed:
                logger.info(f"🗑️ {removed} CSV(s) antiguo(s) eliminado(s) de data/")
            # También limpiar logs de backtest
            logs_dir = Path("logs")
            for f in logs_dir.glob("backtest_*.csv"):
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            if removed:
                logger.info(f"🗑️ {removed} backtest CSV(s) antiguo(s) eliminado(s) de logs/")

    async def _cleanup_logs_loop(self) -> None:
        while True:
            await asyncio.sleep(86400)
            logs_dir = Path("logs")
            cutoff = datetime.now().timestamp() - 7 * 86400
            for f in logs_dir.glob("*.log"):
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    logger.info(f"🗑️ Log antiguo eliminado: {f}")

    async def _backup_db_loop(self) -> None:
        while True:
            await asyncio.sleep(86400)
            backup_dir = Path("data/backups")
            backup_dir.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d")
            backup_file = backup_dir / f"bot_history_{timestamp}.db"
            shutil.copy(self.database.db_path, backup_file)
            logger.info(f"💾 Backup de DB creado: {backup_file}")

    async def _health_check_loop(self) -> None:
        while True:
            await asyncio.sleep(3600)  # cada hora
            try:
                # Verificar espacio en disco (mínimo 100 MB libres)
                usage = shutil.disk_usage(Path("."))
                free_mb = usage.free / (1024 * 1024)
                if free_mb < 100:
                    logger.critical(f"💾 Espacio en disco bajo: {free_mb:.1f} MB")
                else:
                    logger.debug(f"💾 Espacio en disco: {free_mb:.1f} MB")

                # Verificar conexión con Bybit (usar asyncio.run para llamada async)
                try:
                    from bot.core.data_feed import DataFeed
                    feed = DataFeed(symbol="XRPUSDT")
                    balance = await feed.get_balance()
                    if balance > 0:
                        logger.debug("✅ Health check: Bybit OK")
                    else:
                        logger.warning("⚠️ Health check: Bybit respondió pero balance es 0")
                except Exception as e:
                    logger.error(f"❌ Health check: sin conexión a Bybit: {e}")

            except Exception as e:
                logger.error(f"❌ Error en health check: {e}")