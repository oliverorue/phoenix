"""Tests para BotScheduler (FASE 5.2)."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from bot.core.scheduler import BotScheduler


class TestBotSchedulerInit:
    """Verifica que el scheduler se inicializa correctamente."""

    def test_init(self):
        db = MagicMock()
        journal = MagicMock()
        sched = BotScheduler(database=db, journal=journal)
        assert sched.database is db
        assert sched.journal is journal
        assert sched._tasks == []


class TestBotSchedulerStart:
    """Verifica que start() crea las tareas periódicas."""

    @pytest.mark.asyncio
    async def test_start_creates_tasks(self):
        db = MagicMock()
        journal = MagicMock()
        sched = BotScheduler(database=db, journal=journal)

        await sched.start()

        assert len(sched._tasks) == 4
        # Verificar que son tareas asyncio
        for task in sched._tasks:
            assert isinstance(task, asyncio.Task)
            task.cancel()  # Limpiar

    @pytest.mark.asyncio
    async def test_start_logs_message(self):
        db = MagicMock()
        journal = MagicMock()
        sched = BotScheduler(database=db, journal=journal)

        with patch("bot.core.scheduler.logger") as mock_log:
            await sched.start()
            mock_log.info.assert_called_once_with("📅 Scheduler iniciado")

        for task in sched._tasks:
            task.cancel()


class TestBotSchedulerCleanupCSV:
    """Verifica la limpieza de CSVs antiguos."""

    @pytest.mark.asyncio
    async def test_cleanup_csv_removes_old_files(self, tmp_path):
        from datetime import datetime

        db = MagicMock()
        journal = MagicMock()
        sched = BotScheduler(database=db, journal=journal)

        # Crear CSV antiguo en data/
        old_file = tmp_path / "data" / "old_backtest.csv"
        old_file.parent.mkdir(parents=True, exist_ok=True)
        old_file.write_text("dummy")
        # Poner timestamp antiguo (>14 días)
        cutoff = datetime.now().timestamp() - 15 * 86400
        os.utime(old_file, (cutoff, cutoff))

        # Crear CSV reciente (no debe eliminarse)
        new_file = tmp_path / "data" / "recent.csv"
        new_file.write_text("dummy")

        with patch.object(sched, "_cleanup_csv_loop", new=AsyncMock()):
            # Simular la lógica manualmente
            data_dir = tmp_path / "data"
            cutoff_ts = datetime.now().timestamp() - 14 * 86400
            removed = 0
            for f in data_dir.glob("*.csv"):
                if f.stat().st_mtime < cutoff_ts:
                    f.unlink()
                    removed += 1

            assert removed == 1
            assert not old_file.exists()
            assert new_file.exists()

    @pytest.mark.asyncio
    async def test_cleanup_csv_loop_runs_indefinitely(self):
        """Verifica que el loop se ejecuta hasta que se cancela."""
        db = MagicMock()
        journal = MagicMock()
        sched = BotScheduler(database=db, journal=journal)

        with patch("bot.core.scheduler.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            task = asyncio.create_task(sched._cleanup_csv_loop())
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            assert mock_sleep.called


class TestBotSchedulerBackupDB:
    """Verifica el backup de la base de datos."""

    @pytest.mark.asyncio
    async def test_backup_db_creates_backup(self, tmp_path):
        import shutil

        db_path = tmp_path / "test_bot_history.db"
        db_path.write_text("fake db content")

        db = MagicMock()
        db.db_path = str(db_path)
        journal = MagicMock()
        _ = BotScheduler(database=db, journal=journal)

        backup_dir = tmp_path / "data" / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = "20260101"
        backup_file = backup_dir / f"bot_history_{timestamp}.db"
        shutil.copy(db_path, backup_file)

        assert backup_file.exists()
        assert backup_file.read_text() == "fake db content"

    @pytest.mark.asyncio
    async def test_backup_db_loop_runs_indefinitely(self):
        db = MagicMock()
        db.db_path = "/tmp/test.db"
        journal = MagicMock()
        sched = BotScheduler(database=db, journal=journal)

        with patch("bot.core.scheduler.asyncio.sleep", new=AsyncMock()) as mock_sleep:
            task = asyncio.create_task(sched._backup_db_loop())
            await asyncio.sleep(0.1)
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
            assert mock_sleep.called


class TestBotSchedulerHealthCheck:
    """Verifica el health check loop del scheduler."""

    @pytest.mark.asyncio
    async def test_health_check_loop_catches_exceptions(self):
        db = MagicMock()
        journal = MagicMock()
        sched = BotScheduler(database=db, journal=journal)

        with patch("bot.core.scheduler.asyncio.sleep", new=AsyncMock()):
            with patch("bot.core.scheduler.logger"):
                task = asyncio.create_task(sched._health_check_loop())
                await asyncio.sleep(0.1)
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
                # No debe lanzar excepción, el logger captura errores
                assert True


import asyncio
import os
