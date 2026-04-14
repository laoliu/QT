#!/usr/bin/env python
"""兼容入口：请改用 `scripts/data/prefetch_fina.py`。"""

from __future__ import annotations

import runpy
from pathlib import Path


if __name__ == "__main__":
    target = Path(__file__).resolve().parent / "data" / "prefetch_fina.py"
    runpy.run_path(str(target), run_name="__main__")
