import logging
import sys

from loguru import logger


class _InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno < logging.WARNING:
            return
        logger.opt(exception=record.exc_info).log(
            record.levelname, f"{record.name}: {' '.join(record.getMessage().split())}"
        )


def configure_logging(level: str) -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        backtrace=False,
        diagnose=False,
    )
    logging.basicConfig(
        handlers=[_InterceptHandler()], level=logging.WARNING, force=True
    )
    logging.captureWarnings(True)
    # HTTP request lines can contain credentials; request lifecycle is logged by us.
    for name in (
        "aiogram",
        "aiohttp",
        "asyncio",
        "spotipy",
        "urllib3",
        "gallery-dl",
    ):
        dependency = logging.getLogger(name)
        dependency.handlers.clear()
        dependency.propagate = True
        dependency.setLevel(logging.WARNING)
