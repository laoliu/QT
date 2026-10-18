"""The admin cache check is read-only and reports gaps instead of success."""
from datetime import datetime
import json

import pandas as pd
import pytest

from data_providers import parquet_data_updater as updater


@pytest.fixture
def cache(tmp_path, monkeypatch):
    class Sunday(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 18, 12)

    monkeypatch.setattr(updater, "datetime", Sunday)
    directory = tmp_path / "cache/equity_tushare"
    directory.mkdir(parents=True)
    return tmp_path, directory


def _month(directory, month, day, *, adj=True):
    pd.DataFrame({"trade_date": [day]}).to_parquet(directory / f"daily_{month}.parquet")
    if adj:
        pd.DataFrame({"adj_factor": [1.]}).to_parquet(directory / f"adj_factor_{month}.parquet")


def test_missing_directory_cli_is_flat_failure_not_success(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(updater, "get_input", lambda: {"action": "check", "data_dir": str(tmp_path)})
    updater.main()
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert "Cache dir not found" in result["error"]
    assert "data" not in result


def test_empty_cache_reports_zero_and_no_fake_latest(cache):
    root, directory = cache
    # Separate per-day/provider files must not masquerade as monthly caches.
    for name in ("daily_20261016.parquet", "daily_basic_202610.parquet", "daily_202613.parquet"):
        (directory / name).write_bytes(b"not a monthly cache")
    result = updater.do_check({"data_dir": str(root)})
    assert result["ok"] and result["cached_months"] == 0
    assert result["latest_month"] is None
    assert updater._latest_cached_month(directory) is None


def test_reports_older_daily_and_adjustment_gaps(cache):
    root, directory = cache
    _month(directory, "202607", "20260731", adj=False)
    _month(directory, "202609", "20260930")
    _month(directory, "202610", "20261016")
    result = updater.do_check({"data_dir": str(root)})
    assert result["cached_months"] == 3 and result["latest_month"] == "202610"
    assert {(r["month"], r["reason"]) for r in result["stale_months"]} == {
        ("202607", "missing_adj_factor"), ("202608", "missing_daily"),
    }
    assert result["cache_dir"] == str(directory.resolve())


@pytest.mark.parametrize("day", ["20261016", "2026-10-16", pd.Timestamp("2026-10-16")])
def test_weekend_check_accepts_friday_and_does_not_mutate_files(cache, day):
    root, directory = cache
    _month(directory, "202610", day)
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    result = updater.do_check({"data_dir": str(root)})
    assert result["stale_months"] == []
    assert result["freshness_basis"] == "previous_weekday_not_exchange_calendar"
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before


@pytest.mark.parametrize("invalid", ["corrupt", "empty", "bad_dates"])
def test_invalid_current_partition_is_preserved_and_reported(cache, invalid):
    root, directory = cache
    _month(directory, "202610", "20261016")
    path = directory / "daily_202610.parquet"
    if invalid == "corrupt":
        path.write_bytes(b"broken parquet")
    else:
        pd.DataFrame({"trade_date": [] if invalid == "empty" else ["invalid"]}).to_parquet(path)
    before = path.read_bytes()
    result = updater.do_check({"data_dir": str(root)})
    assert result["stale_months"][0]["reason"] == "unreadable_daily"
    assert path.read_bytes() == before


def test_sync_starts_at_earliest_reported_gap_not_latest_daily(cache, monkeypatch):
    root, directory = cache
    _month(directory, "202608", "20260831", adj=False)
    _month(directory, "202610", "20261016")
    monkeypatch.setattr(updater, "_get_token", lambda cfg: "local-test-token")
    monkeypatch.setattr(updater, "_get_api", lambda token: object())
    calls = []

    def sync(api, month, cache_dir, force=False):
        calls.append(month)
        return {"status": "skipped"}

    monkeypatch.setattr(updater, "_sync_daily_month", sync)
    monkeypatch.setattr(updater, "_sync_adj_factor_month", sync)
    result = updater.do_sync({"data_dir": str(root), "skip_stock_basic": True})
    assert result["ok"]
    assert calls == ["202608", "202609", "202610"] * 2


def test_partial_sync_cannot_report_success(cache, monkeypatch):
    root, directory = cache
    _month(directory, "202610", "20261016", adj=False)
    monkeypatch.setattr(updater, "_get_token", lambda cfg: "local-test-token")
    monkeypatch.setattr(updater, "_get_api", lambda token: object())
    monkeypatch.setattr(updater, "_sync_daily_month", lambda *a, **k: {"status": "skipped"})
    monkeypatch.setattr(updater, "_sync_adj_factor_month", lambda *a, **k: {"status": "error", "error": "unavailable"})
    result = updater.do_sync({"data_dir": str(root), "skip_stock_basic": True})
    assert result["ok"] is False and "1 cache update(s) failed" in result["error"]
    assert result["details"]["adj_factor"][0]["status"] == "error"
