"""PyInstaller entry — keep import path stable when frozen."""

from __future__ import annotations

import sys
from pathlib import Path

# Dev / source tree
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from box_online.ui_main import run_app

if __name__ == "__main__":
    run_app()
