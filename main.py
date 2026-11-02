import asyncio
import sys

from loguru import logger

from app.application import run_application
from app.core.config import get_settings
from app.core.logging import configure_logging


def main() -> int:
    configure_logging("INFO")

    try:
        settings = get_settings()
        configure_logging(settings.log_level)
        asyncio.run(run_application(settings))
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
        return 0
    except Exception:
        logger.exception("Application stopped due to unhandled exception")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
