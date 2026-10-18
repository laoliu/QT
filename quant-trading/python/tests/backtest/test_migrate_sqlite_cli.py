from __future__ import annotations

import json
import sqlite3

import pytest

from scripts.migrate_sqlite import main


def _database(path, value: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY, payload TEXT)")
        connection.execute("INSERT INTO runs VALUES (1, ?)", (value,))


def test_cli_audit_and_migration_are_json_and_keep_source(tmp_path, capsys) -> None:
    source = tmp_path / "legacy.db"
    _database(source, "preserved")
    destination = tmp_path / "history.db"

    assert main(["--migrate", str(source), str(destination)]) == 0
    migrated = json.loads(capsys.readouterr().out)
    assert migrated["table_rows"] == {"runs": 1}
    assert source.is_file()
    assert destination.is_file()

    assert main(["--audit", str(source), str(destination)]) == 0
    audit = json.loads(capsys.readouterr().out)
    assert audit["schema_equal"] is True
    # The legacy source is fenced after migration, so its trigger schema differs.
    assert audit["data_equal"] is True


def test_cli_conflict_export_is_read_only(tmp_path, capsys) -> None:
    source = tmp_path / "legacy.db"
    destination = tmp_path / "current.db"
    _database(source, "old")
    _database(destination, "new")

    assert main(["--conflicts", str(source), str(destination)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["conflicts"][0]["source"]["payload"] == "old"
    assert result["conflicts"][0]["destination"]["payload"] == "new"
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT payload FROM runs WHERE id=1").fetchone() == ("old",)


@pytest.mark.parametrize("failure", ["missing", "existing", "corrupt"])
def test_cli_failure_is_json_and_preserves_existing_files(tmp_path, capsys, failure):
    source, destination = tmp_path / "legacy.db", tmp_path / "current.db"
    if failure == "corrupt":
        source.write_bytes(b"invalid database")
    elif failure == "existing":
        _database(source, "old")
        destination.write_bytes(b"keep this file")
    before = {path: path.read_bytes() for path in (source, destination) if path.exists()}
    assert main(["--migrate", str(source), str(destination)]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False and report["error"]["type"]
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not list(tmp_path.glob(".backtest-migrate-*"))
    if failure != "existing":
        assert not destination.exists()
