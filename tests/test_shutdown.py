"""
Tests para el módulo de Graceful Shutdown.
"""
from __future__ import annotations

import asyncio
import signal
from typing import Any
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

from bot.core.shutdown import (
    GracefulShutdown,
    ShutdownConfig,
    create_shutdown,
)


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_bot() -> MagicMock:
    """Bot simulado con todos los atributos necesarios."""
    bot = MagicMock()
    bot.ws_feeds = {}
    bot.runners = []
    bot.notifier = MagicMock()
    bot._save_cycle_state = MagicMock()
    return bot


@pytest.fixture
def stop_event() -> asyncio.Event:
    return asyncio.Event()


@pytest.fixture
def shutdown(mock_bot: MagicMock, stop_event: asyncio.Event) -> GracefulShutdown:
    cfg = ShutdownConfig(timeout=5.0, close_positions=False, notify=False)
    return GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)


# ── Test ShutdownConfig ──────────────────────────────────────────────────────

class TestShutdownConfig:
    """Verifica los valores por defecto y personalizados de ShutdownConfig."""

    def test_defaults(self) -> None:
        cfg = ShutdownConfig()
        assert cfg.timeout == 30.0
        assert cfg.close_positions is True
        assert cfg.notify is True

    def test_custom_values(self) -> None:
        cfg = ShutdownConfig(timeout=15.0, close_positions=False, notify=False)
        assert cfg.timeout == 15.0
        assert cfg.close_positions is False
        assert cfg.notify is False


# ── Test GracefulShutdown — Init ─────────────────────────────────────────────

class TestInit:
    """Verifica la inicialización de GracefulShutdown."""

    def test_creates_with_defaults(self, mock_bot: MagicMock, stop_event: asyncio.Event) -> None:
        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event)
        assert gs._bot is mock_bot
        assert gs._stop_event is stop_event
        assert gs._config.timeout == 30.0
        assert gs._config.close_positions is True
        assert gs._config.notify is True

    def test_creates_with_custom_config(self, mock_bot: MagicMock, stop_event: asyncio.Event) -> None:
        cfg = ShutdownConfig(timeout=10.0, close_positions=False, notify=False)
        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)
        assert gs._config.timeout == 10.0
        assert gs._config.close_positions is False
        assert gs._config.notify is False

    def test_initial_state(self, shutdown: GracefulShutdown) -> None:
        assert shutdown._tasks == {}
        assert shutdown._callbacks == []
        assert shutdown._shutdown_start is None
        assert not shutdown._stop_event.is_set()

    def test_create_shutdown_helper(self, mock_bot: MagicMock, stop_event: asyncio.Event) -> None:
        with patch("bot.core.shutdown.SHUTDOWN_TIMEOUT", 15.0):
            with patch("bot.core.shutdown.SHUTDOWN_CLOSE_POSITIONS", False):
                with patch("bot.core.shutdown.SHUTDOWN_NOTIFY", False):
                    gs = create_shutdown(bot=mock_bot, stop_event=stop_event)
                    assert gs._config.timeout == 15.0
                    assert gs._config.close_positions is False
                    assert gs._config.notify is False

    def test_create_shutdown_no_stop_event(self, mock_bot: MagicMock) -> None:
        gs = create_shutdown(bot=mock_bot)
        assert gs._stop_event is not None
        assert not gs._stop_event.is_set()


# ── Test Signal Registration ─────────────────────────────────────────────────

class TestSignalRegistration:
    """Verifica el registro de manejadores de señales."""

    @pytest.mark.asyncio
    async def test_register_signals_calls_add_signal_handler(self, shutdown: GracefulShutdown) -> None:
        """Debe registrar SIGTERM, SIGINT, y SIGHUP."""
        with patch.object(asyncio.get_running_loop(), "add_signal_handler") as mock_add:
            shutdown.register_signals()
            # Verificar que se llamó 3 veces (SIGTERM, SIGINT, SIGHUP)
            assert mock_add.call_count == 3
            sigs = [call[0][0] for call in mock_add.call_args_list]
            assert signal.SIGTERM in sigs
            assert signal.SIGINT in sigs
            assert signal.SIGHUP in sigs

    def test_on_signal_sets_stop_event(self, shutdown: GracefulShutdown) -> None:
        assert not shutdown._stop_event.is_set()
        shutdown._on_signal()
        assert shutdown._stop_event.is_set()

    @pytest.mark.asyncio
    async def test_double_signal_does_not_raise(self, shutdown: GracefulShutdown) -> None:
        """Segunda señal no debe lanzar excepción (forza exit)."""
        shutdown._on_signal()
        assert shutdown._stop_event.is_set()
        # La segunda señal llama _force_exit → os._exit(1)
        with patch.object(shutdown, "_force_exit") as mock_force:
            shutdown._on_signal()
            mock_force.assert_called_once()


# ── Test Task Tracking ───────────────────────────────────────────────────────

class TestTaskTracking:
    """Verifica el seguimiento de tareas internas."""

    @pytest.mark.asyncio
    async def test_track_task(self, shutdown: GracefulShutdown) -> None:
        task = asyncio.create_task(asyncio.sleep(999))
        shutdown.track_task("test", task)
        assert "test" in shutdown._tasks
        assert shutdown._tasks["test"] is task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_track_multiple_tasks(self, shutdown: GracefulShutdown) -> None:
        t1 = asyncio.create_task(asyncio.sleep(999))
        t2 = asyncio.create_task(asyncio.sleep(999))
        shutdown.track_task("t1", t1)
        shutdown.track_task("t2", t2)
        assert len(shutdown._tasks) == 2
        t1.cancel()
        t2.cancel()
        try:
            await asyncio.gather(t1, t2)
        except asyncio.CancelledError:
            pass

    def test_register_callback(self, shutdown: GracefulShutdown) -> None:
        cb = MagicMock()
        shutdown.register_callback(cb)
        assert len(shutdown._callbacks) == 1
        assert shutdown._callbacks[0] is cb


# ── Test Shutdown — Basic ────────────────────────────────────────────────────

class TestShutdownBasic:
    """Verifica el flujo básico de shutdown sin posiciones."""

    @pytest.mark.asyncio
    async def test_shutdown_sets_stop_event(self, shutdown: GracefulShutdown) -> None:
        assert not shutdown._stop_event.is_set()
        await shutdown.shutdown()
        assert shutdown._stop_event.is_set()

    @pytest.mark.asyncio
    async def test_shutdown_calls_save_state(self, shutdown: GracefulShutdown, mock_bot: MagicMock) -> None:
        await shutdown.shutdown()
        mock_bot._save_cycle_state.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_stops_ws_feeds(self, shutdown: GracefulShutdown, mock_bot: MagicMock) -> None:
        ws_mock = MagicMock()
        mock_bot.ws_feeds = {"test": ws_mock}
        await shutdown.shutdown()
        ws_mock.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_cancels_tracked_tasks(
        self, shutdown: GracefulShutdown
    ) -> None:
        """Tareas trackeadas deben ser canceladas durante shutdown."""
        ran = False

        async def never_ending() -> None:
            nonlocal ran
            ran = True
            try:
                await asyncio.Event().wait()  # nunca se completa
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(never_ending())
        shutdown.track_task("test", task)

        await shutdown.shutdown()

        assert task.done()
        assert ran

    @pytest.mark.asyncio
    async def test_shutdown_runs_callbacks(self, shutdown: GracefulShutdown) -> None:
        cb = MagicMock()
        shutdown.register_callback(cb)
        await shutdown.shutdown()
        cb.assert_called_once()

    @pytest.mark.asyncio
    async def test_shutdown_async_callback(self, shutdown: GracefulShutdown) -> None:
        """Callbacks asíncronos deben ser awaited."""
        ran = False

        async def async_cb() -> None:
            nonlocal ran
            await asyncio.sleep(0.01)
            ran = True

        shutdown.register_callback(async_cb)
        await shutdown.shutdown()
        assert ran


# ── Test Shutdown — Close Positions ──────────────────────────────────────────

class TestShutdownClosePositions:
    """Verifica el cierre de posiciones durante el shutdown."""

    @pytest.mark.asyncio
    async def test_close_positions_called(
        self, mock_bot: MagicMock, stop_event: asyncio.Event
    ) -> None:
        cfg = ShutdownConfig(timeout=5.0, close_positions=True, notify=False)
        runner = MagicMock()
        runner.symbol = "XRPUSDT"
        runner.executor.position_open = True
        runner.executor.close_position_async = AsyncMock(return_value=True)
        mock_bot.runners = [runner]

        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)
        await gs.shutdown()

        runner.executor.close_position_async.assert_awaited_once_with(
            reason="shutdown"
        )

    @pytest.mark.asyncio
    async def test_close_positions_skipped_when_disabled(
        self, mock_bot: MagicMock, stop_event: asyncio.Event
    ) -> None:
        cfg = ShutdownConfig(timeout=5.0, close_positions=False, notify=False)
        runner = MagicMock()
        runner.executor.position_open = True
        mock_bot.runners = [runner]

        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)
        await gs.shutdown()

        runner.executor.close_position_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_positions_no_open_positions(
        self, mock_bot: MagicMock, stop_event: asyncio.Event
    ) -> None:
        cfg = ShutdownConfig(timeout=5.0, close_positions=True, notify=False)
        runner = MagicMock()
        runner.executor.position_open = False
        mock_bot.runners = [runner]

        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)
        await gs.shutdown()

        runner.executor.close_position_async.assert_not_called()

    @pytest.mark.asyncio
    async def test_close_positions_error_does_not_block(
        self, mock_bot: MagicMock, stop_event: asyncio.Event
    ) -> None:
        """Un error cerrando una posición no debe bloquear el shutdown."""
        cfg = ShutdownConfig(timeout=5.0, close_positions=True, notify=False)
        runner = MagicMock()
        runner.symbol = "XRPUSDT"
        runner.executor.position_open = True
        runner.executor.close_position_async = AsyncMock(
            side_effect=RuntimeError("API error")
        )
        mock_bot.runners = [runner]

        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)
        # No debe lanzar excepción
        await gs.shutdown()
        runner.executor.close_position_async.assert_awaited_once()


# ── Test Shutdown — Notify ───────────────────────────────────────────────────

class TestShutdownNotify:
    """Verifica que la notificación de cierre se envíe correctamente."""

    @pytest.mark.asyncio
    async def test_notify_sent_when_enabled(
        self, mock_bot: MagicMock, stop_event: asyncio.Event
    ) -> None:
        cfg = ShutdownConfig(timeout=5.0, close_positions=False, notify=True)
        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)
        await gs.shutdown()
        mock_bot.notifier.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_notify_skipped_when_disabled(
        self, mock_bot: MagicMock, stop_event: asyncio.Event
    ) -> None:
        cfg = ShutdownConfig(timeout=5.0, close_positions=False, notify=False)
        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)
        await gs.shutdown()
        mock_bot.notifier.send.assert_not_called()


# ── Test Shutdown — Timeout ──────────────────────────────────────────────────

class TestShutdownTimeout:
    """Verifica que el timeout force la salida si el shutdown se demora."""

    @pytest.mark.asyncio
    async def test_timeout_triggers_force_exit(
        self, mock_bot: MagicMock, stop_event: asyncio.Event
    ) -> None:
        """Un shutdown que nunca termina debe forzar salida tras timeout."""
        cfg = ShutdownConfig(timeout=0.05, close_positions=False, notify=False)

        # Hacer que _save_cycle_state nunca termine
        async def never_ending() -> None:
            await asyncio.Event().wait()

        mock_bot._save_cycle_state = MagicMock()
        original = mock_bot._save_cycle_state

        async def hang(*args: Any, **kwargs: Any) -> None:
            await asyncio.sleep(999)

        mock_bot._save_cycle_state = hang

        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)
        with patch.object(gs, "_force_exit") as mock_force:
            await gs.shutdown()
            mock_force.assert_called_once()

        mock_bot._save_cycle_state = original


# ── Test Shutdown — HTTP Sessions ────────────────────────────────────────────

class TestShutdownHTTPSessions:
    """Verifica que el shutdown cierre todos los recursos HTTP."""

    @pytest.mark.asyncio
    async def test_shutdown_does_not_crash_without_session_http(
        self, mock_bot: MagicMock, stop_event: asyncio.Event
    ) -> None:
        """Shutdown no debe fallar aunque runners no tengan session_http.

        session_http.close() fue eliminado en FASE 5 — BinanceFuturesClient
        usa aiohttp y no necesita sync close.
        """
        cfg = ShutdownConfig(timeout=5.0, close_positions=False, notify=False)
        runner = MagicMock(spec=[])  # runner sin session_http
        mock_bot.runners = [runner]

        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)
        await gs.shutdown()  # No debe lanzar

    @pytest.mark.asyncio
    async def test_shutdown_stops_ws_feeds_before_cleanup(
        self, mock_bot: MagicMock, stop_event: asyncio.Event
    ) -> None:
        """WS feeds deben detenerse durante shutdown."""
        cfg = ShutdownConfig(timeout=5.0, close_positions=False, notify=False)
        ws_mock_1 = MagicMock()
        ws_mock_2 = MagicMock()
        mock_bot.ws_feeds = {"test1": ws_mock_1, "test2": ws_mock_2}

        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)
        await gs.shutdown()

        ws_mock_1.stop.assert_called_once()
        ws_mock_2.stop.assert_called_once()

# ── Test create_shutdown ─────────────────────────────────────────────────────

class TestCreateShutdown:
    """Verifica la función auxiliar create_shutdown."""

    def test_uses_config_loader_defaults(self, mock_bot: MagicMock) -> None:
        with patch("bot.core.shutdown.SHUTDOWN_TIMEOUT", 42.0):
            with patch("bot.core.shutdown.SHUTDOWN_CLOSE_POSITIONS", False):
                with patch("bot.core.shutdown.SHUTDOWN_NOTIFY", True):
                    gs = create_shutdown(bot=mock_bot)
                    assert gs._config.timeout == 42.0
                    assert gs._config.close_positions is False
                    assert gs._config.notify is True

    def test_override_timeout(self, mock_bot: MagicMock) -> None:
        gs = create_shutdown(bot=mock_bot, timeout=99.0)
        assert gs._config.timeout == 99.0

    def test_override_close_positions(self, mock_bot: MagicMock) -> None:
        gs = create_shutdown(bot=mock_bot, close_positions=True)
        assert gs._config.close_positions is True

    def test_override_notify(self, mock_bot: MagicMock) -> None:
        gs = create_shutdown(bot=mock_bot, notify=False)
        assert gs._config.notify is False


# ── Test Edge Cases ──────────────────────────────────────────────────────────

class TestEdgeCases:
    """Casos borde del shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_without_runners(self, mock_bot: MagicMock, stop_event: asyncio.Event) -> None:
        """Shutdown con lista de runners vacía no debe fallar."""
        cfg = ShutdownConfig(timeout=5.0, close_positions=True, notify=False)
        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)
        mock_bot.runners = []
        await gs.shutdown()  # no debe lanzar

    @pytest.mark.asyncio
    async def test_shutdown_without_ws_feeds(self, mock_bot: MagicMock, stop_event: asyncio.Event) -> None:
        """Shutdown sin ws_feeds no debe fallar."""
        cfg = ShutdownConfig(timeout=5.0, close_positions=False, notify=False)
        del mock_bot.ws_feeds
        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)
        await gs.shutdown()  # no debe lanzar

    @pytest.mark.asyncio
    async def test_shutdown_without_notifier(self, mock_bot: MagicMock, stop_event: asyncio.Event) -> None:
        """Shutdown sin notifier no debe fallar."""
        cfg = ShutdownConfig(timeout=5.0, close_positions=False, notify=True)
        del mock_bot.notifier
        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)
        await gs.shutdown()  # no debe lanzar

    @pytest.mark.asyncio
    async def test_tracked_task_already_done(
        self, shutdown: GracefulShutdown
    ) -> None:
        """Tarea ya completada no debe causar problemas."""
        async def quick() -> None:
            pass

        task = asyncio.create_task(quick())
        await task  # esperar que termine
        shutdown.track_task("done", task)
        await shutdown.shutdown()  # no debe lanzar

    @pytest.mark.asyncio
    async def test_shutdown_is_idempotent(self, shutdown: GracefulShutdown) -> None:
        """Llamar shutdown() dos veces no debe fallar."""
        await shutdown.shutdown()
        assert shutdown._stop_event.is_set()
        # Segunda llamada
        await shutdown.shutdown()
        assert shutdown._stop_event.is_set()


# ── Test Shutdown — Tracing ──────────────────────────────────────────────────

class TestShutdownTracing:
    """Verifica que se finalice el tracing durante el shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_tracing_called(
        self, mock_bot: MagicMock, stop_event: asyncio.Event
    ) -> None:
        cfg = ShutdownConfig(timeout=5.0, close_positions=False, notify=False)
        gs = GracefulShutdown(bot=mock_bot, stop_event=stop_event, config=cfg)
        with patch("bot.core.tracing.shutdown_tracing") as mock_st:
            await gs.shutdown()
            mock_st.assert_called_once()
