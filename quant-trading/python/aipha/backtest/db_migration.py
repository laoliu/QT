"""Publish a verified SQLite snapshot and fence writes to the retained old database."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path


FENCE_PREFIX = "__qt_migrated_"


def _identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _fingerprint(conn: sqlite3.Connection) -> tuple[str, dict[str, int]]:
    digest = hashlib.sha256()
    counts = {}
    schema = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    for entry in schema:
        if entry[0] == "trigger" and entry[1].startswith(FENCE_PREFIX):
            continue
        digest.update(repr(entry).encode("utf-8"))
        if entry[0] != "table":
            continue
        table = _identifier(entry[1])
        columns = len(conn.execute(f"PRAGMA table_info({table})").fetchall())
        order = ",".join(str(i + 1) for i in range(columns))
        count = 0
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}"):
            digest.update(repr(row).encode("utf-8"))
            count += 1
        counts[entry[1]] = count
    return digest.hexdigest(), counts


def audit_databases(source: Path, destination: Path) -> dict:
    """Read-only comparison of two SQLite databases before migration/cleanup.

    Rows are compared by declared primary key. Any destination-only row or
    same-key content conflict requires an explicit merge decision; this helper
    never mutates either database.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file() or not destination.is_file():
        raise FileNotFoundError(source if not source.is_file() else destination)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src, \
            closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as dst:
        src_schema = _schema_signature(src)
        dst_schema = _schema_signature(dst)
        src_tables = {row[1] for row in src_schema if row[0] == "table"}
        dst_tables = {row[1] for row in dst_schema if row[0] == "table"}
        table_reports = {}
        for table in sorted(src_tables | dst_tables):
            if table not in src_tables or table not in dst_tables:
                table_reports[table] = {"schema_missing": "destination" if table in src_tables else "source"}
                continue
            src_cols = src.execute(f"PRAGMA table_info({_identifier(table)})").fetchall()
            dst_cols = dst.execute(f"PRAGMA table_info({_identifier(table)})").fetchall()
            src_names = [row[1] for row in src_cols]
            dst_names = [row[1] for row in dst_cols]
            if src_names != dst_names:
                table_reports[table] = {"schema_mismatch": {"source": src_names, "destination": dst_names}}
                continue
            pk = [row[1] for row in sorted(src_cols, key=lambda row: row[5]) if row[5]]
            if not pk:
                source_hash, source_count = _table_digest(src, table)
                dest_hash, dest_count = _table_digest(dst, table)
                table_reports[table] = {
                    "source_rows": source_count, "destination_rows": dest_count,
                    "content_equal": source_hash == dest_hash,
                    "unkeyed_table": True,
                }
                continue
            source_rows = _keyed_rows(src, table, src_names, pk)
            dest_rows = _keyed_rows(dst, table, dst_names, pk)
            source_keys, dest_keys = set(source_rows), set(dest_rows)
            common = source_keys & dest_keys
            table_reports[table] = {
                "source_rows": len(source_rows), "destination_rows": len(dest_rows),
                "source_only": len(source_keys - dest_keys),
                "destination_only": len(dest_keys - source_keys),
                "same_key_conflicts": sum(source_rows[key] != dest_rows[key] for key in common),
                "primary_key": pk,
            }
        exact_schema = src_schema == dst_schema
        exact_data = all(
            report.get("source_rows") == report.get("destination_rows")
            and report.get("source_only", 0) == 0
            and report.get("destination_only", 0) == 0
            and report.get("same_key_conflicts", 0) == 0
            and report.get("content_equal", True)
            and not report.get("schema_mismatch")
            and not report.get("schema_missing")
            for report in table_reports.values()
        )
        destination_empty = all(report.get("destination_rows", 0) == 0 for report in table_reports.values())
        source_integrity = _integrity(src)
        destination_integrity = _integrity(dst)
        return {
            "source": str(source), "destination": str(destination),
            "source_integrity": source_integrity, "destination_integrity": destination_integrity,
            "schema_equal": exact_schema, "data_equal": exact_data,
            "destination_empty": destination_empty,
            "safe_to_replace_destination": (
                source_integrity == "ok" and destination_integrity == "ok"
                and exact_schema and (destination_empty or exact_data)
            ),
            "tables": table_reports,
        }


def export_conflicts(source: Path, destination: Path) -> list[dict]:
    """Return keyed row conflicts for operator review without mutating either DB.

    The audit summary intentionally contains counts only. This companion view
    exposes the exact old/new records needed for a manual merge decision.
    ``bytes`` values are represented textually because the result is commonly
    serialized as JSON; no database value is changed.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file() or not destination.is_file():
        raise FileNotFoundError(source if not source.is_file() else destination)

    def jsonable(value):
        if isinstance(value, bytes):
            return {"__bytes_hex__": value.hex()}
        return value

    conflicts: list[dict] = []
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src, \
            closing(sqlite3.connect(destination.as_uri() + "?mode=ro", uri=True)) as dst:
        src_tables = {row[1] for row in _schema_signature(src) if row[0] == "table"}
        dst_tables = {row[1] for row in _schema_signature(dst) if row[0] == "table"}
        for table in sorted(src_tables & dst_tables):
            src_cols = src.execute(f"PRAGMA table_info({_identifier(table)})").fetchall()
            dst_cols = dst.execute(f"PRAGMA table_info({_identifier(table)})").fetchall()
            columns = [row[1] for row in src_cols]
            if columns != [row[1] for row in dst_cols]:
                continue
            pk = [row[1] for row in sorted(src_cols, key=lambda row: row[5]) if row[5]]
            if not pk:
                continue
            old_rows = _keyed_rows(src, table, columns, pk)
            new_rows = _keyed_rows(dst, table, columns, pk)
            for key in sorted(set(old_rows) & set(new_rows), key=repr):
                if old_rows[key] == new_rows[key]:
                    continue
                conflicts.append({
                    "table": table,
                    "primary_key": dict(zip(pk, key)),
                    "source": dict(zip(columns, (jsonable(v) for v in old_rows[key]))),
                    "destination": dict(zip(columns, (jsonable(v) for v in new_rows[key]))),
                })
    return conflicts


def _schema_signature(conn: sqlite3.Connection) -> list[tuple]:
    return conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE name NOT LIKE 'sqlite_%' AND name NOT LIKE ? ORDER BY type, name",
        (FENCE_PREFIX + "%",),
    ).fetchall()


def _integrity(conn: sqlite3.Connection) -> str:
    return "; ".join(row[0] for row in conn.execute("PRAGMA quick_check"))


def _keyed_rows(conn: sqlite3.Connection, table: str, columns: list[str], pk: list[str]) -> dict[tuple, tuple]:
    quoted_cols = ",".join(_identifier(column) for column in columns)
    quoted_pk = ",".join(_identifier(column) for column in pk)
    rows = {}
    for row in conn.execute(f"SELECT {quoted_cols} FROM {_identifier(table)} ORDER BY {quoted_pk}"):
        values = tuple(row)
        rows[tuple(values[columns.index(column)] for column in pk)] = values
    return rows


def _table_digest(conn: sqlite3.Connection, table: str) -> tuple[str, int]:
    digest = hashlib.sha256()
    count = 0
    for row in conn.execute(f"SELECT * FROM {_identifier(table)}"):
        digest.update(repr(tuple(row)).encode("utf-8"))
        count += 1
    return digest.hexdigest(), count


def migrate_database(source: Path, destination: Path, *, timeout: float = 10) -> dict:
    """Copy committed WAL data, verify schema/rows, then atomically publish.

    The old database is retained. Its data tables reject INSERT/UPDATE/DELETE so
    already connected writers fail explicitly instead of forking history. Restart
    those clients to pick up the new default path. If publication fails after the
    fence commits, rerun this function; source data remains readable and intact.
    An existing destination is never overwritten or merged.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if destination.exists() or source == destination:
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".backtest-migrate-", suffix=".db", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary)
    lock = sqlite3.connect(source, timeout=timeout)
    try:
        # Hold the writer reservation throughout snapshot, verification and fencing.
        # Use a separate reader: backing up the connection holding this transaction
        # itself would wait for its own transaction to end.
        lock.execute("BEGIN IMMEDIATE")
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as reader:
            with closing(sqlite3.connect(temporary)) as target:
                reader.backup(target)
                fences = target.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'"
                ).fetchall()
                for (name,) in fences:
                    if name.startswith(FENCE_PREFIX):
                        target.execute(f"DROP TRIGGER {_identifier(name)}")
                target.commit()
                target.execute("PRAGMA journal_mode=DELETE")
                if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ValueError("Destination SQLite integrity check failed")
                expected, counts = _fingerprint(reader)
                if _fingerprint(target) != (expected, counts):
                    raise ValueError("Database schema/data verification failed")
        for table in counts:
            for operation in ("INSERT", "UPDATE", "DELETE"):
                name = FENCE_PREFIX + hashlib.sha256(table.encode()).hexdigest()[:20] + operation
                lock.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {_identifier(name)} BEFORE {operation} "
                    f"ON {_identifier(table)} BEGIN SELECT RAISE(ABORT, "
                    "'Database migrated; restart client using the new database path'); END"
                )
        lock.commit()
        with temporary.open("r+b") as snapshot:
            os.fsync(snapshot.fileno())
        # Hard-link publication is atomic and fails if another client created the
        # destination in the meantime. Never replace a database another client uses.
        os.link(temporary, destination)
        return {"source": str(source), "destination": str(destination),
                "sha256": expected, "table_rows": counts, "legacy_writes_blocked": True}
    finally:
        lock.close()
        temporary.unlink(missing_ok=True)
        for suffix in ("-wal", "-shm", "-journal"):
            Path(str(temporary) + suffix).unlink(missing_ok=True)
