#!/usr/bin/env python3
"""Launch the desktop application.

    python3 scripts/run_app.py
    python3 scripts/run_app.py --db /tmp/test.db --theme light

This is a thin wrapper around ``main.py`` so the application can be started from
anywhere in the project.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main import main  # noqa: E402  (path set up above)

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
