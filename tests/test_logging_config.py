"""Tests para logging_config (FASE 5.2)."""

import logging
from unittest.mock import patch, MagicMock


class TestLoggingConfig:
    """Verifica la configuración de logging."""

    def test_setup_logging_creates_intercept_handler(self):
        from bot.core.logging_config import setup_logging, InterceptHandler
        with patch("bot.core.logging_config.logging.basicConfig") as mock_basic:
            setup_logging()
            _, kwargs = mock_basic.call_args
            handlers = kwargs.get("handlers", [])
            assert len(handlers) == 1
            assert isinstance(handlers[0], InterceptHandler)
            assert kwargs.get("level") == logging.INFO

    def test_setup_logging_sets_urllib3_warning(self):
        from bot.core.logging_config import setup_logging
        with patch("bot.core.logging_config.logging.getLogger") as mock_get_logger:
            mock_logger = MagicMock()
            mock_get_logger.return_value = mock_logger
            setup_logging()
            mock_get_logger.assert_any_call("urllib3")
            mock_logger.setLevel.assert_called_with(logging.WARNING)

    def test_intercept_handler_emit(self):
        from bot.core.logging_config import InterceptHandler
        handler = InterceptHandler()
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname=__file__, lineno=1, msg="test message",
            args=(), exc_info=None,
        )
        with patch("bot.core.logging_config.logger.opt") as mock_opt:
            mock_logger_opt = MagicMock()
            mock_opt.return_value = mock_logger_opt
            handler.emit(record)
            mock_opt.assert_called_once_with(depth=6, exception=None)
            mock_logger_opt.log.assert_called_once_with("INFO", "test message")

    def test_intercept_handler_emit_with_exception(self):
        from bot.core.logging_config import InterceptHandler
        try:
            raise ValueError("test error")
        except ValueError:
            record = logging.LogRecord(
                name="test", level=logging.ERROR,
                pathname=__file__, lineno=1, msg="error occurred",
                args=(), exc_info=True,  # type: ignore[arg-type]
            )
        handler = InterceptHandler()
        with patch("bot.core.logging_config.logger.opt") as mock_opt:
            mock_logger_opt = MagicMock()
            mock_opt.return_value = mock_logger_opt
            handler.emit(record)
            _, kwargs = mock_opt.call_args
            assert kwargs["exception"] is not None
