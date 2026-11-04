"""Backwards-compatible wrapper; the implementation lives in :mod:`app.cli`.

``docker compose`` runs ``python main.py``, while ``python -m app`` and
``python -m app doctor`` are equivalent entry points.
"""

import sys

from app.cli import main

if __name__ == "__main__":
    sys.exit(main())
