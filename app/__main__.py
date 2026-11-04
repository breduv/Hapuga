"""Allow ``python -m app`` to run the bot or the doctor."""

import sys

from app.cli import main

if __name__ == "__main__":
    sys.exit(main())
