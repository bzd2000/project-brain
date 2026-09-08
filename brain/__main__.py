"""`python -m brain` — the fallback where a console shim cannot be installed."""
from __future__ import annotations

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
