from __future__ import annotations

import json
import sqlite3

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
