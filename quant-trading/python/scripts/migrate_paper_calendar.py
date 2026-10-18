"""Verify a legacy Paper book against a signed calendar snapshot and publish a copy.

Preview is the default. --execute publishes to a new path without altering
the old book. Rebuild the snapshot through the canonical TuShare builder,
ending at the old book's last committed close and covering all its history.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aipha.engines.aipha_ml_engine import _read_parquet
from aipha.research.paper_book import upgrade_paper_book_calendar


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("destination")
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = upgrade_paper_book_calendar(args.source, args.destination, _read_parquet(args.snapshot), commit=args.execute)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
