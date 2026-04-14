"""tests for scripts/ops/ — db_backup, nav_monitor, clean_output, daily_ops"""

from __future__ import annotations

import json
import shutil
import sqlite3
import textwrap
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# ============================================================
# fixtures
# ============================================================


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    """Create a minimal valid SQLite DB."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()
    return db


@pytest.fixture()
def backup_dir(tmp_path: Path) -> Path:
    d = tmp_path / "backups"
    d.mkdir()
    return d


@pytest.fixture()
def paper_state_dir(tmp_path: Path) -> Path:
    """Create a paper state directory with state.json."""
    d = tmp_path / "paper_state" / "test_strategy"
    d.mkdir(parents=True)
    state = {
        "strategy_name": "test_strategy",
        "date": "2025-01-15",
        "total_value": 1_050_000.0,
        "positions": {"bond_a": 100, "bond_b": 200},
    }
    (d / "state.json").write_text(json.dumps(state), encoding="utf-8")
    return d


@pytest.fixture()
def output_dir_with_scatter(tmp_path: Path) -> Path:
    """Create output/ with scattered files + a proper strategy dir."""
    out = tmp_path / "output"
    out.mkdir()
    # Scattered files
    (out / "some_log.txt").write_text("log content")
    (out / "result.json").write_text("{}")
    (out / "old_report.html").write_text("<html></html>")
    (out / "temp_script.py").write_text("pass")
    # Keep file
    (out / "backtest_index.db").write_text("fake db")
    # Strategy subdir (should not be touched)
    strat = out / "my_strategy"
    strat.mkdir()
    (strat / "summary.json").write_text("{}")
    return out


# ============================================================
# db_backup tests
# ============================================================


class TestVerifyDbIntegrity:
    """verify_db_integrity ——完整性验证。"""

    def test_valid_db(self, tmp_db: Path):
        from scripts.ops.db_backup import verify_db_integrity

        assert verify_db_integrity(str(tmp_db)) is True

    def test_missing_db(self, tmp_path: Path):
        from scripts.ops.db_backup import verify_db_integrity

        assert verify_db_integrity(str(tmp_path / "nonexistent.db")) is False

    def test_corrupt_db(self, tmp_path: Path):
        from scripts.ops.db_backup import verify_db_integrity

        bad = tmp_path / "corrupt.db"
        bad.write_bytes(b"not a sqlite database" * 100)
        assert verify_db_integrity(str(bad)) is False


class TestBackupDatabase:
    """backup_database —— SQLite 备份流程。"""

    def test_successful_backup(self, tmp_db: Path, backup_dir: Path):
        from scripts.ops.db_backup import backup_database

        result = backup_database(
            db_path=str(tmp_db),
            backup_dir=str(backup_dir),
            keep_days=30,
            notify=False,
        )
        assert result is not None
        assert result.exists()
        assert result.name.startswith("backtest_index_")
        assert result.suffix == ".db"
        # Verify the backup is a valid DB
        conn = sqlite3.connect(str(result))
        row = conn.execute("SELECT COUNT(*) FROM t").fetchone()
        conn.close()
        assert row[0] == 1

    def test_missing_source(self, tmp_path: Path, backup_dir: Path):
        from scripts.ops.db_backup import backup_database

        result = backup_database(
            db_path=str(tmp_path / "missing.db"),
            backup_dir=str(backup_dir),
        )
        assert result is None

    def test_backup_creates_dir(self, tmp_db: Path, tmp_path: Path):
        from scripts.ops.db_backup import backup_database

        new_dir = tmp_path / "new_backup_dir"
        result = backup_database(
            db_path=str(tmp_db),
            backup_dir=str(new_dir),
        )
        assert result is not None
        assert new_dir.exists()


class TestCleanupOldBackups:
    """cleanup_old_backups —— 过期备份清理。"""

    def test_removes_old_backups(self, backup_dir: Path):
        from scripts.ops.db_backup import cleanup_old_backups

        # Create backups with old timestamps
        old_ts = (datetime.now() - timedelta(days=40)).strftime("%Y%m%d_%H%M%S")
        new_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (backup_dir / f"backtest_index_{old_ts}.db").write_text("old")
        (backup_dir / f"backtest_index_{new_ts}.db").write_text("new")

        removed = cleanup_old_backups(str(backup_dir), keep_days=30)
        assert removed == 1
        assert not (backup_dir / f"backtest_index_{old_ts}.db").exists()
        assert (backup_dir / f"backtest_index_{new_ts}.db").exists()

    def test_keeps_recent(self, backup_dir: Path):
        from scripts.ops.db_backup import cleanup_old_backups

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        (backup_dir / f"backtest_index_{ts}.db").write_text("recent")
        removed = cleanup_old_backups(str(backup_dir), keep_days=7)
        assert removed == 0

    def test_empty_dir(self, backup_dir: Path):
        from scripts.ops.db_backup import cleanup_old_backups

        removed = cleanup_old_backups(str(backup_dir), keep_days=7)
        assert removed == 0


class TestDbBackupMain:
    """db_backup.main() CLI 入口。"""

    def test_verify_only_pass(self, tmp_db: Path):
        from scripts.ops.db_backup import main

        rc = main(["--db-path", str(tmp_db), "--verify-only"])
        assert rc == 0

    def test_verify_only_fail(self, tmp_path: Path):
        from scripts.ops.db_backup import main

        bad = tmp_path / "bad.db"
        bad.write_bytes(b"garbage" * 50)
        rc = main(["--db-path", str(bad), "--verify-only"])
        assert rc == 1

    def test_full_backup_cli(self, tmp_db: Path, backup_dir: Path):
        from scripts.ops.db_backup import main

        rc = main(["--db-path", str(tmp_db), "--backup-dir", str(backup_dir)])
        assert rc == 0
        backups = list(backup_dir.glob("backtest_index_*.db"))
        assert len(backups) == 1


# ============================================================
# nav_monitor tests
# ============================================================


class TestLoadPaperNav:
    """load_paper_nav —— 读取模拟盘状态。"""

    def test_load_state_json(self, paper_state_dir: Path):
        from scripts.ops.nav_monitor import load_paper_nav

        result = load_paper_nav(str(paper_state_dir))
        assert result is not None
        assert result["nav"] == 1_050_000.0
        assert result["strategy_name"] == "test_strategy"

    def test_load_nav_history_jsonl(self, tmp_path: Path):
        from scripts.ops.nav_monitor import load_paper_nav

        d = tmp_path / "strat"
        d.mkdir()
        lines = [
            json.dumps({"date": "2025-01-14", "nav": 1_020_000}),
            json.dumps({"date": "2025-01-15", "nav": 1_035_000}),
        ]
        (d / "nav_history.jsonl").write_text("\n".join(lines), encoding="utf-8")
        result = load_paper_nav(str(d))
        assert result is not None
        assert result["nav"] == 1_035_000

    def test_missing_dir(self, tmp_path: Path):
        from scripts.ops.nav_monitor import load_paper_nav

        result = load_paper_nav(str(tmp_path / "nonexistent"))
        assert result is None

    def test_empty_dir(self, tmp_path: Path):
        from scripts.ops.nav_monitor import load_paper_nav

        d = tmp_path / "empty"
        d.mkdir()
        result = load_paper_nav(str(d))
        assert result is None


class TestCheckNavDeviation:
    """check_nav_deviation —— 偏差计算与判定。"""

    def test_within_threshold(self):
        from scripts.ops.nav_monitor import check_nav_deviation

        r = check_nav_deviation(1_030_000, 1_000_000, threshold=0.05)
        assert r.passed is True
        assert r.deviation_pct == pytest.approx(0.03, abs=0.001)

    def test_exceeds_threshold(self):
        from scripts.ops.nav_monitor import check_nav_deviation

        r = check_nav_deviation(1_100_000, 1_000_000, threshold=0.05)
        assert r.passed is False
        assert r.deviation_pct == pytest.approx(0.10, abs=0.001)

    def test_zero_baseline(self):
        from scripts.ops.nav_monitor import check_nav_deviation

        r = check_nav_deviation(1_000_000, 0, threshold=0.05)
        assert r.passed is False

    def test_exact_match(self):
        from scripts.ops.nav_monitor import check_nav_deviation

        r = check_nav_deviation(1_000_000, 1_000_000, threshold=0.01)
        assert r.passed is True
        assert r.deviation_pct == 0.0

    def test_negative_deviation(self):
        from scripts.ops.nav_monitor import check_nav_deviation

        r = check_nav_deviation(900_000, 1_000_000, threshold=0.05)
        assert r.passed is False
        assert r.deviation_pct == pytest.approx(0.10)


class TestRunNavCheck:
    """run_nav_check —— 完整检查流程。"""

    def test_empty_state_dir(self, tmp_path: Path):
        from scripts.ops.nav_monitor import run_nav_check

        d = tmp_path / "empty_state"
        d.mkdir()
        results = run_nav_check(str(d))
        assert results == []

    def test_nonexistent_dir(self, tmp_path: Path):
        from scripts.ops.nav_monitor import run_nav_check

        results = run_nav_check(str(tmp_path / "nonexistent"))
        assert results == []


class TestNavMonitorMain:
    """nav_monitor.main() CLI 入口。"""

    def test_no_state_returns_zero(self, tmp_path: Path):
        from scripts.ops.nav_monitor import main

        d = tmp_path / "empty_paper"
        d.mkdir()
        rc = main(["--paper-state", str(d)])
        assert rc == 0


# ============================================================
# clean_output tests
# ============================================================


class TestCleanOutputScan:
    """scan_scattered_files + classify_file。"""

    def test_scan_finds_scattered(self, output_dir_with_scatter: Path):
        from scripts.ops.clean_output import scan_scattered_files

        files = scan_scattered_files(output_dir_with_scatter)
        names = {f.name for f in files}
        assert "some_log.txt" in names
        assert "result.json" in names
        assert "old_report.html" in names
        assert "temp_script.py" in names
        # Keep file should NOT be listed
        assert "backtest_index.db" not in names

    def test_classify_file_types(self):
        from scripts.ops.clean_output import classify_file

        assert classify_file("error.log") == "logs"
        assert classify_file("output.txt") == "logs"
        assert classify_file("data.json") == "json_archive"
        assert classify_file("config.yaml") == "config_archive"
        assert classify_file("report.html") == "html_archive"
        assert classify_file("script.py") == "script_archive"
        assert classify_file("binary.bin") == "misc"


class TestCleanOutputPreview:
    """preview —— 按分类分组。"""

    def test_groups_by_category(self, output_dir_with_scatter: Path):
        from scripts.ops.clean_output import preview

        groups = preview(output_dir_with_scatter)
        assert "logs" in groups
        assert "json_archive" in groups
        assert "html_archive" in groups
        assert "script_archive" in groups

    def test_empty_dir(self, tmp_path: Path):
        from scripts.ops.clean_output import preview

        out = tmp_path / "clean_output"
        out.mkdir()
        groups = preview(out)
        assert groups == {}


class TestCleanOutputExecute:
    """execute_archive / execute_delete。"""

    def test_archive_moves_files(self, output_dir_with_scatter: Path):
        from scripts.ops.clean_output import execute_archive

        moved, nbytes = execute_archive(output_dir_with_scatter)
        assert moved == 4
        assert nbytes > 0
        # Original files should be gone
        assert not (output_dir_with_scatter / "some_log.txt").exists()
        # _archive should exist
        archive = output_dir_with_scatter / "_archive"
        assert archive.exists()
        # Strategy subdir untouched
        assert (output_dir_with_scatter / "my_strategy" / "summary.json").exists()
        # Keep file untouched
        assert (output_dir_with_scatter / "backtest_index.db").exists()

    def test_delete_removes_files(self, output_dir_with_scatter: Path):
        from scripts.ops.clean_output import execute_delete

        deleted, nbytes = execute_delete(output_dir_with_scatter)
        assert deleted == 4
        # Files should be gone (not archived)
        assert not (output_dir_with_scatter / "some_log.txt").exists()
        assert not (output_dir_with_scatter / "_archive").exists()
        # Strategy dir + keep file untouched
        assert (output_dir_with_scatter / "my_strategy").exists()
        assert (output_dir_with_scatter / "backtest_index.db").exists()


class TestCleanOutputMain:
    """clean_output.main() CLI。"""

    def test_dry_run(self, output_dir_with_scatter: Path):
        from scripts.ops.clean_output import main

        rc = main(["--output-dir", str(output_dir_with_scatter)])
        assert rc == 0
        # Files should still exist (dry run)
        assert (output_dir_with_scatter / "some_log.txt").exists()

    def test_execute_flag(self, output_dir_with_scatter: Path):
        from scripts.ops.clean_output import main

        rc = main(["--output-dir", str(output_dir_with_scatter), "--execute"])
        assert rc == 0
        assert not (output_dir_with_scatter / "some_log.txt").exists()

    def test_clean_dir_returns_zero(self, tmp_path: Path):
        from scripts.ops.clean_output import main

        out = tmp_path / "empty_output"
        out.mkdir()
        rc = main(["--output-dir", str(out)])
        assert rc == 0

    def test_nonexistent_dir(self, tmp_path: Path):
        from scripts.ops.clean_output import main

        rc = main(["--output-dir", str(tmp_path / "nosuchdir")])
        assert rc == 1
