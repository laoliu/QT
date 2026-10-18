import sqlite3
from contextlib import closing

import pytest

from aipha.backtest.db_migration import audit_databases, export_conflicts, migrate_database


def _source(tmp_path):
    path = tmp_path / "legacy.db"
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, payload BLOB)")
    conn.execute("INSERT INTO runs VALUES (1, ?)", (b"history",))
    conn.commit()
    return path, conn


def test_migration_copies_wal_and_blocks_existing_writers(tmp_path):
    source, old = _source(tmp_path)
    destination = tmp_path / "new/history.db"
    try:
        report = migrate_database(source, destination)
        assert report["table_rows"] == {"runs": 1}
        assert source.exists()
        with pytest.raises(sqlite3.IntegrityError, match="Database migrated"):
            old.execute("INSERT INTO runs VALUES (2, NULL)")
        old.rollback()
        assert old.execute("SELECT payload FROM runs").fetchone() == (b"history",)
        with closing(sqlite3.connect(destination)) as current:
            assert current.execute("SELECT payload FROM runs").fetchone() == (b"history",)
            current.execute("INSERT INTO runs VALUES (2, NULL)")
            current.commit()
    finally:
        old.close()


def test_existing_destination_is_never_overwritten(tmp_path):
    source, old = _source(tmp_path)
    destination = tmp_path / "existing.db"
    destination.write_bytes(b"do not overwrite")
    try:
        with pytest.raises(FileExistsError):
            migrate_database(source, destination)
        assert destination.read_bytes() == b"do not overwrite"
        old.execute("INSERT INTO runs VALUES (2, NULL)")
        old.commit()
    finally:
        old.close()


def test_publication_failure_can_be_retried_without_losing_history(tmp_path, monkeypatch):
    from aipha.backtest import db_migration

    source, old = _source(tmp_path)
    destination = tmp_path / "new.db"
    original = db_migration.os.link
    try:
        def fail(*args):
            raise OSError("publication unavailable")
        monkeypatch.setattr(db_migration.os, "link", fail)
        with pytest.raises(OSError, match="publication unavailable"):
            migrate_database(source, destination)
        assert not destination.exists()
        monkeypatch.setattr(db_migration.os, "link", original)
        assert migrate_database(source, destination)["table_rows"] == {"runs": 1}
        assert not list(tmp_path.glob(".backtest-migrate-*"))
    finally:
        old.close()


def test_active_writer_prevents_migration(tmp_path):
    source, old = _source(tmp_path)
    try:
        old.execute("INSERT INTO runs VALUES (2, NULL)")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            migrate_database(source, tmp_path / "new.db", timeout=.02)
        assert not (tmp_path / "new.db").exists()
        assert not list(tmp_path.glob(".backtest-migrate-*"))
    finally:
        old.close()


def test_audit_reports_conflicts_and_destination_only_rows_read_only(tmp_path):
    source, old = _source(tmp_path)
    destination = tmp_path / "new.db"
    old.execute("INSERT INTO runs VALUES (2, ?)", (b"source",))
    old.commit()
    with closing(sqlite3.connect(destination)) as new:
        new.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, payload BLOB)")
        new.execute("INSERT INTO runs VALUES (1, ?)", (b"changed",))
        new.execute("INSERT INTO runs VALUES (3, ?)", (b"destination-only",))
        new.commit()
    try:
        report = audit_databases(source, destination)
        assert report["source_integrity"] == report["destination_integrity"] == "ok"
        assert report["schema_equal"]
        assert not report["data_equal"]
        assert not report["safe_to_replace_destination"]
        assert report["tables"]["runs"]["source_only"] == 1
        assert report["tables"]["runs"]["destination_only"] == 1
        assert report["tables"]["runs"]["same_key_conflicts"] == 1
        assert old.execute("SELECT count(*) FROM runs").fetchone()[0] == 2
        with closing(sqlite3.connect(destination)) as new:
            assert new.execute("SELECT count(*) FROM runs").fetchone()[0] == 2
    finally:
        old.close()


def test_audit_never_approves_database_with_failed_integrity_check(tmp_path, monkeypatch):
    from aipha.backtest import db_migration

    source, old = _source(tmp_path)
    destination = tmp_path / "copy.db"
    with closing(sqlite3.connect(destination)) as new:
        old.backup(new)
    original = db_migration._integrity
    try:
        monkeypatch.setattr(
            db_migration, "_integrity",
            lambda conn: "corrupt" if conn.execute("PRAGMA database_list").fetchone()[2] == str(destination) else original(conn),
        )
        report = audit_databases(source, destination)
        assert report["source_integrity"] == "ok"
        assert report["destination_integrity"] == "corrupt"
        assert not report["safe_to_replace_destination"]
    finally:
        old.close()


def test_export_conflicts_is_read_only_and_includes_records(tmp_path):
    source, old = _source(tmp_path)
    destination = tmp_path / "new.db"
    old.execute("UPDATE runs SET payload = ? WHERE id = 1", (b"source",))
    old.commit()
    with closing(sqlite3.connect(destination)) as new:
        new.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, payload BLOB)")
        new.execute("INSERT INTO runs VALUES (1, ?)", (b"destination",))
        new.commit()
    try:
        conflicts = export_conflicts(source, destination)
        assert len(conflicts) == 1
        assert conflicts[0]["table"] == "runs"
        assert conflicts[0]["primary_key"] == {"id": 1}
        assert conflicts[0]["source"]["payload"] == {"__bytes_hex__": b"source".hex()}
        assert conflicts[0]["destination"]["payload"] == {"__bytes_hex__": b"destination".hex()}
        assert old.execute("SELECT payload FROM runs").fetchone() == (b"source",)
    finally:
        old.close()


def test_dangling_destination_is_rejected_without_creating_its_target(tmp_path):
    source, old = _source(tmp_path)
    destination, target = tmp_path / "link.db", tmp_path / "missing.db"
    try:
        try:
            destination.symlink_to(target)
        except OSError:
            pytest.skip("symbolic-link creation unavailable on this host")
        with pytest.raises(FileExistsError):
            migrate_database(source, destination)
        assert destination.is_symlink() and not target.exists()
        old.execute("INSERT INTO runs VALUES (2, NULL)")
        old.commit()  # Rejected destinations must not fence the source.
    finally:
        old.close()


@pytest.mark.parametrize("timeout", [-1, float("nan"), float("inf")])
def test_invalid_timeout_leaves_no_files_or_fences(tmp_path, timeout):
    source, old = _source(tmp_path)
    try:
        with pytest.raises(ValueError, match="finite and nonnegative"):
            migrate_database(source, tmp_path / "new/history.db", timeout=timeout)
        assert not (tmp_path / "new").exists()
        old.execute("INSERT INTO runs VALUES (2, NULL)")
        old.commit()
    finally:
        old.close()


def test_connection_failure_removes_staging_file(tmp_path, monkeypatch):
    import aipha.backtest.db_migration as migration

    source, old = _source(tmp_path)
    old.close()
    before = source.read_bytes()
    def unavailable(*a, **k):
        raise sqlite3.OperationalError("connection unavailable")
    monkeypatch.setattr(migration.sqlite3, "connect", unavailable)
    with pytest.raises(sqlite3.OperationalError, match="connection unavailable"):
        migrate_database(source, tmp_path / "new.db")
    assert not list(tmp_path.glob(".backtest-migrate-*"))
    assert source.read_bytes() == before
