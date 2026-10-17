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
import asyncio
from pathlib import Path
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock
from types import SimpleNamespace

import pytest

from aipha.tracker.cli import (
    _config_data_dir, _load_config, _portfolio_evidence, _should_notify,
    _write_audit_log,
)
from aipha.tracker.models import Holding, PortfolioSnapshot, PortfolioSource
from aipha.utils.paths import PROJECT_ROOT


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

    def test_relative_tracker_data_dir_is_project_anchored(self):
        assert _config_data_dir({"data_dir": "data/tracker"}) == PROJECT_ROOT / "data" / "tracker"
        assert _config_data_dir({}) == PROJECT_ROOT / "data" / "tracker"

    def test_legacy_fetch_failure_becomes_explicit_evidence(self):
        author = type("Author", (), {"platform_uid": "uid-1"})()
        evidence = _portfolio_evidence(author, "ZH1", None)
        assert evidence.failure_type.value == "fields_missing"
        assert evidence.evidence_quality.value == "failed"
        assert evidence.parsed_fields["holdings"] is None

    def test_empty_holdings_are_not_recorded_as_success(self):
        from datetime import datetime

        author = type("Author", (), {"platform_uid": "uid-1"})()
        snapshot = PortfolioSnapshot(
            snapshot_id="s1", source=PortfolioSource.XUEQIU,
            author_id="a1", author_name="A", portfolio_code="ZH1",
            snapshot_time=datetime.now(), fetch_time=datetime.now(), holdings=(),
        )
        evidence = _portfolio_evidence(author, "ZH1", snapshot)
        assert evidence.failure_type.value == "fields_missing"
        assert evidence.parsed_fields["holdings"] == []


def test_legacy_fetch_dual_writes_portfolio_and_evidence(tmp_path, monkeypatch):
    from aipha.tracker import cli
    from aipha.tracker.evidence import EvidenceStore
    from aipha.tracker.models import PortfolioSnapshot
    from aipha.tracker.storage import TrackerStorage

    snapshot = PortfolioSnapshot(
        snapshot_id="legacy-1", source=PortfolioSource.XUEQIU,
        author_id="author-1", author_name="Author", portfolio_code="ZH1",
        snapshot_time=datetime(2026, 10, 17), fetch_time=datetime(2026, 10, 17),
        holdings=(Holding("600519.SH", "贵州茅台", Decimal("0.25")),),
    )

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    class BrowserSessionStub(Session):
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        @staticmethod
        async def random_delay(*args):
            return None

    class Collector:
        def __init__(self, session):
            pass

        async def check_login(self):
            return True

        async def fetch_portfolio(self, **kwargs):
            return snapshot

    saved = []

    class StorageStub(TrackerStorage):
        def __init__(self, data_dir):
            self.data_dir = Path(data_dir)

        def get_latest_snapshot(self, author_id):
            return None

        def save_snapshot(self, value):
            saved.append(value)

        def close(self):
            pass

    monkeypatch.setattr("aipha.tracker.browser.base.BrowserSession", BrowserSessionStub)
    monkeypatch.setattr("aipha.tracker.browser.xueqiu.XueqiuCollector", Collector)
    monkeypatch.setattr("aipha.tracker.storage.TrackerStorage", StorageStub)
    # cmd_fetch imports its collaborators lazily, so patch the source modules' symbols.
    import aipha.tracker.browser.base as browser_base
    import aipha.tracker.browser.xueqiu as browser_xueqiu
    import aipha.tracker.storage as storage_module

    monkeypatch.setattr(browser_base, "BrowserSession", BrowserSessionStub)
    monkeypatch.setattr(browser_xueqiu, "XueqiuCollector", Collector)
    monkeypatch.setattr(storage_module, "TrackerStorage", StorageStub)

    config = {
        "data_dir": str(tmp_path / "tracker"),
        "authors_raw": [{
            "author_id": "author-1", "name": "Author", "platform": "xueqiu",
            "platform_uid": "uid-1", "portfolio_codes": ["ZH1"],
        }],
        "browser": {"headless": True}, "notification": {}, "audit": {},
    }
    asyncio.run(cli.cmd_fetch(SimpleNamespace(author_id=None, dry_run=False), config))

    assert len(saved) == 1
    assert saved[0].portfolio_code == "ZH1"
    store = EvidenceStore(tmp_path / "tracker")
    try:
        rows = store.get_history("xueqiu_uid-1", limit=5)
        assert len(rows) == 1
        assert rows[0].parsed_fields["holdings"][0]["symbol"] == "600519.SH"
        assert rows[0].parsed_fields["perf_summary"] == {}
        assert rows[0].source_url == "https://xueqiu.com/P/ZH1"
    finally:
        store.close()


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
        """未配置 log_dir 时落在 <项目根>/logs/audit，而不是当前工作目录。

        这条原先是 ``chdir(tmp_path)`` 后断言 ``tmp_path/logs/audit`` 存在
        —— 它断言的正是 CWD 依赖本身。审计日志跟着启动目录走，等于同一
        套配置在 cron 和手动执行下写进两棵树，查起来永远缺半截。
        """
        import aipha.utils.paths as paths_mod

        fake_root = tmp_path / "root"
        fake_root.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()

        monkeypatch.setattr(paths_mod, "PROJECT_ROOT", fake_root)
        monkeypatch.chdir(elsewhere)

        _write_audit_log({"enabled": True}, "test", "test_event", "detail")

        assert (fake_root / "logs" / "audit").exists()
        assert not (elsewhere / "logs").exists(), "审计日志漏到了当前工作目录"

    def test_relative_log_dir_is_anchored(self, tmp_path, monkeypatch):
        """配置里写相对路径也锚到项目根（绝对路径照用，见上面两条）。"""
        import aipha.utils.paths as paths_mod

        fake_root = tmp_path / "root"
        fake_root.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()

        monkeypatch.setattr(paths_mod, "PROJECT_ROOT", fake_root)
        monkeypatch.chdir(elsewhere)

        _write_audit_log(
            {"enabled": True, "log_dir": "logs/tracker_audit"},
            "test", "test_event", "detail",
        )

        assert (fake_root / "logs" / "tracker_audit").exists()
        assert not (elsewhere / "logs").exists()
