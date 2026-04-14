"""tests/scripts/test_data_preheat.py — 数据预热脚本单元测试."""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJ = Path(__file__).resolve().parents[2]
if str(_PROJ / "scripts" / "ops") not in sys.path:
    sys.path.insert(0, str(_PROJ / "scripts" / "ops"))

import data_preheat


class TestGetLatestCacheDate:
    def test_no_files_returns_none(self, tmp_path):
        assert data_preheat._get_latest_cache_date(tmp_path, "daily_") is None

    def test_reads_parquet(self, tmp_path):
        import pandas as pd
        df = pd.DataFrame({"trade_date": ["20260401", "20260402"], "close": [1, 2]})
        (tmp_path / "daily_202604.parquet").parent.mkdir(exist_ok=True)
        df.to_parquet(tmp_path / "daily_202604.parquet", index=False)
        result = data_preheat._get_latest_cache_date(tmp_path, "daily_")
        assert result == date(2026, 4, 2)


class TestNeedsUpdate:
    def test_none_needs_update(self):
        assert data_preheat._needs_update(None) is True

    def test_recent_no_update(self):
        assert data_preheat._needs_update(date.today()) is False

    def test_stale_needs_update(self):
        old = date.today() - timedelta(days=10)
        assert data_preheat._needs_update(old) is True


class TestPreheatDryRun:
    def test_equity_daily_dry_run_ok(self):
        with patch.object(data_preheat, "_get_latest_cache_date", return_value=date.today()):
            r = data_preheat.preheat_equity_daily(dry_run=True)
            assert r["status"] == "OK"

    def test_equity_daily_dry_run_need_update(self):
        with patch.object(data_preheat, "_get_latest_cache_date", return_value=None):
            r = data_preheat.preheat_equity_daily(dry_run=True)
            assert r["status"] == "NEED_UPDATE"

    def test_daily_basic_dry_run_ok(self):
        with patch.object(data_preheat, "_get_latest_cache_date", return_value=date.today()):
            r = data_preheat.preheat_daily_basic(dry_run=True)
            assert r["status"] == "OK"

    def test_cb_daily_dry_run_ok(self):
        with patch.object(data_preheat, "_get_latest_cache_date", return_value=date.today()):
            r = data_preheat.preheat_cb_daily(dry_run=True)
            assert r["status"] == "OK"


class TestAudit:
    def test_write_audit_creates_file(self, tmp_path):
        with patch.object(data_preheat, "_AUDIT_DIR", tmp_path):
            data_preheat._write_audit([{"check": "test", "status": "OK", "msg": "ok"}])
        files = list(tmp_path.glob("data_preheat_*.jsonl"))
        assert len(files) == 1


class TestSendAlert:
    def test_skips_without_token(self):
        with patch.dict("os.environ", {}, clear=True):
            data_preheat._send_alert("test", [{"check": "x", "msg": "y"}])
