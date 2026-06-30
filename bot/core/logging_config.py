import sys
import logging
from loguru import logger

class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        logger_opt = logger.opt(depth=6, exception=record.exc_info)
        logger_opt.log(record.levelname, record.getMessage())

def setup_logging(trace_enabled: bool = False) -> None:
    """Configura loguru con formato opcional de trace_id.

    Args:
        trace_enabled: Si True, añade el trace_id de OpenTelemetry a cada línea.
    """
    # Formato base
    fmt = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan>"
    )

    if trace_enabled:
        # Incluye trace_id si está disponible
        fmt += " | <magenta>{extra[trace_id]:0>32}</magenta>"

    fmt += " | <level>{message}</level>\n"

    logger.remove()
    logger.add(
        sys.stderr,
        format=fmt,
        level="INFO",
        colorize=True,
        backtrace=True,
        diagnose=False,
    )
    # También a archivo
    logger.add(
        "logs/bot.log",
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} | {level:<8} | "
            "{name}:{function}:{line}"
            + (" | trace_id={extra[trace_id]}" if trace_enabled else "")
            + " | {message}\n"
        ),
        level="DEBUG",
        rotation="10 MB",
        retention=5,
        compression="gz",
    )

    logging.basicConfig(handlers=[InterceptHandler()], level=logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
