"""Read-only audit for the legacy and current backtest SQLite indexes.

Examples::

    python scripts/audit_backtest_db.py
    python scripts/audit_backtest_db.py --source old.db --destination new.db
    python scripts/audit_backtest_db.py --migrate --source old.db --destination new.db

By default the command is read-only. ``--migrate`` explicitly publishes a
verified snapshot only when the destination does not exist, then fences writes
to the retained source database. Existing destinations are never overwritten.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

# Allow direct execution from any working directory.
_PYTHON_ROOT = Path(__file__).resolve().parents[1]
if str(_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(_PYTHON_ROOT))

from aipha.backtest.db_migration import (  # noqa: E402
    audit_databases, export_conflicts, migrate_database,
)
from aipha.backtest.result_store import _LEGACY_DB_PATH, _resolve_db_path  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=_LEGACY_DB_PATH)
    parser.add_argument("--destination", type=Path, default=_resolve_db_path())
    parser.add_argument(
        "--conflicts-out", type=Path,
        help="只读导出同主键冲突明细 JSON，不修改任何数据库",
    )
    parser.add_argument(
        "--migrate", action="store_true",
        help="仅在目标不存在时发布已校验快照并封存旧库写入；绝不覆盖已有目标",
    )
    args = parser.parse_args()
    if args.migrate:
        try:
            report = migrate_database(args.source, args.destination)
        except (FileNotFoundError, FileExistsError, OSError, ValueError, sqlite3.Error) as exc:
            print(f"migration_failed={type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    report = audit_databases(args.source, args.destination)
    if args.conflicts_out:
        conflicts = export_conflicts(args.source, args.destination)
        args.conflicts_out.parent.mkdir(parents=True, exist_ok=True)
        args.conflicts_out.write_text(
            json.dumps(conflicts, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"conflicts_exported={len(conflicts)} path={args.conflicts_out}")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["safe_to_replace_destination"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
