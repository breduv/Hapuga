"""Command line entry points.

``python -m app`` runs the bot, ``python -m app doctor`` runs the pre-flight
checks. ``main.py`` is a thin wrapper around this module so the Docker CMD and
the documented ``python main.py`` keep working.
"""

from __future__ import annotations

import asyncio
import sys

from loguru import logger

from app import doctor
from app.application import run_application
from app.core.config import get_settings
from app.core.logging import configure_logging

USAGE = """usage: python -m app [command]

commands:
  (none)   run the Telegram bot
  doctor   check configuration and external tools
  help     show this message
"""


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if not args:
        return run_bot()

    command, rest = args[0], args[1:]

    if command in ("-h", "--help", "help"):
        print(USAGE, end="")
        return 0

    if command == "doctor":
        return doctor.main(rest)

    print(f"unknown command: {command}", file=sys.stderr)
    print(USAGE, end="", file=sys.stderr)
    return 2


def run_bot() -> int:
    configure_logging("INFO")

    try:
        settings = get_settings()
        configure_logging(settings.log_level)
        asyncio.run(run_application(settings))
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
        return 0
    except Exception:  # noqa: BLE001
        logger.exception("Application stopped due to unhandled exception")
        return 1

    return 0
