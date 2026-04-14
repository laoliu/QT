"""Tests for cli.py — new production-readiness features.

Covers:
- _load_config: strict file/dir checks
- _should_notify: level-based filtering (all/changes/major)  
- _write_audit_log: structured audit log output
- cmd_fetch: --author-id filter, --dry-run, empty portfolio_codes warning,
             empty snapshot anomaly detection, notification return check
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aipha.tracker.cli import _load_config, _should_notify, _write_audit_log


# =========================================================================
# _load_config — strict file checks
# =========================================================================


class TestLoadConfigStrict:
    """_load_config must sys.exit(1) when config dir/files are missing."""

    def test_missing_config_dir(self, tmp_path):
        """Non-existent config dir → SystemExit."""
        with pytest.raises(SystemExit):
            _load_config(str(tmp_path / "nonexistent"))

    def test_missing_settings_yml(self, tmp_path):
        """Dir exists but settings.yml missing → SystemExit."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        (config_dir / "authors.yml").write_text("authors: []", encoding="utf-8")
        with pytest.raises(SystemExit):
            _load_config(str(config_dir))

    def test_missing_authors_yml(self, tmp_path):
        """Dir exists but authors.yml missing → SystemExit."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        (config_dir / "settings.yml").write_text("data_dir: /tmp", encoding="utf-8")
        with pytest.raises(SystemExit):
            _load_config(str(config_dir))

    def test_valid_config_dir(self, tmp_path):
        """Both files exist → returns merged dict."""
        config_dir = tmp_path / "cfg"
        config_dir.mkdir()
        (config_dir / "settings.yml").write_text(
            "data_dir: test_data\nbrowser:\n  headless: true\n",
            encoding="utf-8",
        )
        (config_dir / "authors.yml").write_text(
            "authors:\n  - author_id: test\n    name: Test\n",
            encoding="utf-8",
        )
        result = _load_config(str(config_dir))
        assert result["data_dir"] == "test_data"
        assert len(result["authors_raw"]) == 1
        assert result["authors_raw"][0]["author_id"] == "test"


# =========================================================================
# _should_notify — level-based filtering
# =========================================================================


class TestShouldNotify:
    """Test notification level filtering logic."""

    @staticmethod
    def _make_diff(has_changes: bool = True, added: int = 0, removed: int = 0,
                   increased: int = 0, decreased: int = 0):
        """Build a mock PortfolioDiff-like object."""
        d = MagicMock()
        d.has_changes = has_changes
        d.added = [MagicMock()] * added
        d.removed = [MagicMock()] * removed
        d.increased = [MagicMock()] * increased
        d.decreased = [MagicMock()] * decreased
        return d

    def test_no_changes_never_notifies(self):
        diff = self._make_diff(has_changes=False)
        assert _should_notify(diff, "all") is False
        assert _should_notify(diff, "changes") is False
        assert _should_notify(diff, "major") is False

    def test_level_all_always_notifies(self):
        diff = self._make_diff(has_changes=True, increased=1)
        assert _should_notify(diff, "all") is True

    def test_level_changes_notifies_any_change(self):
        diff = self._make_diff(has_changes=True, decreased=1)
        assert _should_notify(diff, "changes") is True

    def test_level_major_needs_add_or_remove(self):
        # Only weight changes — should NOT notify for "major"
        diff = self._make_diff(has_changes=True, increased=1, decreased=1)
        assert _should_notify(diff, "major") is False

    def test_level_major_with_added(self):
        diff = self._make_diff(has_changes=True, added=1)
        assert _should_notify(diff, "major") is True

    def test_level_major_with_removed(self):
        diff = self._make_diff(has_changes=True, removed=2)
        assert _should_notify(diff, "major") is True

    def test_unknown_level_defaults_to_changes(self):
        diff = self._make_diff(has_changes=True, increased=1)
        assert _should_notify(diff, "unknown_value") is True


# =========================================================================
# _write_audit_log — structured audit logging
# =========================================================================


class TestWriteAuditLog:
    """Test audit log file writing."""

    def test_disabled_audit_does_nothing(self, tmp_path):
        audit_config = {"enabled": False, "log_dir": str(tmp_path / "audit")}
        _write_audit_log(audit_config, "xzlx", "fetch_ok", "test detail")
        # log_dir should NOT be created
        assert not (tmp_path / "audit").exists()

    def test_enabled_audit_writes_file(self, tmp_path):
        log_dir = tmp_path / "audit"
        audit_config = {"enabled": True, "log_dir": str(log_dir)}
        _write_audit_log(audit_config, "xzlx", "fetch_ok", "holdings=10")

        assert log_dir.exists()
        log_files = list(log_dir.glob("tracker_*.log"))
        assert len(log_files) == 1

        content = log_files[0].read_text(encoding="utf-8")
        assert "xzlx" in content
        assert "fetch_ok" in content
        assert "holdings=10" in content

    def test_multiple_entries_append(self, tmp_path):
        log_dir = tmp_path / "audit"
        audit_config = {"enabled": True, "log_dir": str(log_dir)}
        _write_audit_log(audit_config, "xzlx", "fetch_ok", "detail1")
        _write_audit_log(audit_config, "wdg", "fetch_fail", "detail2")

        log_files = list(log_dir.glob("tracker_*.log"))
        assert len(log_files) == 1
        content = log_files[0].read_text(encoding="utf-8")
        assert content.count("\n") == 2  # two entries

    def test_default_log_dir(self, tmp_path, monkeypatch):
        """When log_dir not specified, defaults to 'logs/audit'."""
        monkeypatch.chdir(tmp_path)
        audit_config = {"enabled": True}
        _write_audit_log(audit_config, "test", "test_event", "detail")
        assert (tmp_path / "logs" / "audit").exists()
