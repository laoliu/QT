import json
import sqlite3

from scripts.audit_backtest_db import main


def test_audit_cli_is_read_only_and_returns_nonzero_for_conflicts(tmp_path, monkeypatch, capsys):
    source = tmp_path / "old.db"
    destination = tmp_path / "new.db"
    for path, payload in ((source, "old"), (destination, "new")):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, payload TEXT)")
            conn.execute("INSERT INTO runs VALUES (1, ?)", (payload,))
            conn.commit()
    before = destination.read_bytes()
    monkeypatch.setattr(
        "sys.argv", ["audit_backtest_db.py", "--source", str(source), "--destination", str(destination)]
    )

    assert main() == 2
    report = json.loads(capsys.readouterr().out)
    assert not report["safe_to_replace_destination"]
    assert report["tables"]["runs"]["same_key_conflicts"] == 1
    assert destination.read_bytes() == before


def test_audit_cli_exports_conflict_details_without_database_writes(
    tmp_path, monkeypatch, capsys,
):
    source = tmp_path / "old.db"
    destination = tmp_path / "new.db"
    for path, payload in ((source, "old"), (destination, "new")):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, payload TEXT)")
            conn.execute("INSERT INTO runs VALUES (1, ?)", (payload,))
            conn.commit()
    before = (source.read_bytes(), destination.read_bytes())
    export_path = tmp_path / "review" / "conflicts.json"
    monkeypatch.setattr(
        "sys.argv", [
            "audit_backtest_db.py", "--source", str(source),
            "--destination", str(destination), "--conflicts-out", str(export_path),
        ]
    )

    assert main() == 2
    output = capsys.readouterr().out
    assert "conflicts_exported=1" in output
    conflicts = json.loads(export_path.read_text(encoding="utf-8"))
    assert conflicts == [{
        "table": "runs", "primary_key": {"id": 1},
        "source": {"id": 1, "payload": "old"},
        "destination": {"id": 1, "payload": "new"},
    }]
    assert (source.read_bytes(), destination.read_bytes()) == before


def test_migrate_cli_publishes_only_when_destination_is_absent(tmp_path, monkeypatch, capsys):
    source = tmp_path / "old.db"
    destination = tmp_path / "new.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, payload TEXT)")
        conn.execute("INSERT INTO runs VALUES (1, 'history')")
        conn.commit()

    monkeypatch.setattr(
        "sys.argv", [
            "audit_backtest_db.py", "--migrate",
            "--source", str(source), "--destination", str(destination),
        ]
    )
    assert main() == 0
    report = json.loads(capsys.readouterr().out)
    assert report["legacy_writes_blocked"] is True
    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT payload FROM runs").fetchone() == ("history",)


def test_migrate_cli_refuses_existing_destination(tmp_path, monkeypatch, capsys):
    source = tmp_path / "old.db"
    destination = tmp_path / "new.db"
    for path in (source, destination):
        with sqlite3.connect(path) as conn:
            conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY)")
            conn.commit()
    before = destination.read_bytes()
    monkeypatch.setattr(
        "sys.argv", [
            "audit_backtest_db.py", "--migrate",
            "--source", str(source), "--destination", str(destination),
        ]
    )
    assert main() == 2
    assert "migration_failed=FileExistsError" in capsys.readouterr().err
    assert destination.read_bytes() == before


def test_migrate_cli_reports_sqlite_lock_as_controlled_failure(tmp_path, monkeypatch, capsys):
    import sqlite3

    monkeypatch.setattr(
        "scripts.audit_backtest_db.migrate_database",
        lambda *args: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")),
    )
    monkeypatch.setattr(
        "sys.argv", ["audit_backtest_db.py", "--migrate", "--source", str(tmp_path / "old.db"),
                     "--destination", str(tmp_path / "new.db")]
    )
    assert main() == 2
    assert "migration_failed=OperationalError: database is locked" in capsys.readouterr().err
