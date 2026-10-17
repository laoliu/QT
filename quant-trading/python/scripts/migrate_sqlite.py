"""Audit or migrate a legacy Quant Trading SQLite database.

The migration is deliberately explicit and fail-closed:

* ``--audit`` only reads both databases and prints a JSON report.
* ``--conflicts`` only reads both databases and prints keyed conflicts.
* ``--migrate`` copies committed WAL data to a new destination, verifies the
  copy, and fences writes to the retained legacy database.

The destination must not already exist.  No database is deleted or replaced.

Examples::

    python scripts/migrate_sqlite.py --audit legacy.db current.db
    python scripts/migrate_sqlite.py --conflicts legacy.db current.db
    python scripts/migrate_sqlite.py --migrate legacy.db data/history.db
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aipha.backtest.db_migration import audit_databases, export_conflicts, migrate_database


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="legacy SQLite database")
    parser.add_argument("destination", type=Path, help="new SQLite database path")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--audit", action="store_true", help="read-only schema/data audit")
    mode.add_argument("--conflicts", action="store_true", help="read-only keyed conflict export")
    mode.add_argument("--migrate", action="store_true", help="publish a verified migrated copy")
    parser.add_argument("--timeout", type=float, default=10.0, help="SQLite lock timeout in seconds")
    args = parser.parse_args(argv)

    if args.audit:
        result = audit_databases(args.source, args.destination)
    elif args.conflicts:
        result = {"conflicts": export_conflicts(args.source, args.destination)}
    else:
        result = migrate_database(args.source, args.destination, timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
