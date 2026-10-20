"""Offline administration must inspect and update the configured shared cache."""

from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

from aipha.utils import paths
from data_providers import local_parquet_provider, parquet_data_updater as updater


@pytest.fixture
def cache_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("QTDATA_DIR", "")
    monkeypatch.setenv("DATA_CACHE_DIR", "")
    monkeypatch.setattr(paths, "PROJECT_ROOT", tmp_path / "project")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 19)

    monkeypatch.setattr(updater, "datetime", FixedDateTime)
    return tmp_path


@pytest.mark.parametrize("source", ["default", "qtdata", "shared", "explicit", "relative"])
def test_check_sync_and_health_use_same_cache(cache_environment, monkeypatch, source):
    root = cache_environment
    cfg = {}
    if source == "default":
        from aipha import config

        monkeypatch.setattr(config, "_Path", lambda value: root / "external/cache")
        expected = root / "external/cache/equity_tushare"
    elif source == "qtdata":
        monkeypatch.setenv("QTDATA_DIR", str(root / "external"))
        expected = root / "external/cache/equity_tushare"
    elif source == "shared":
        monkeypatch.setenv("DATA_CACHE_DIR", str(root / "shared-cache"))
        monkeypatch.setenv("QTDATA_DIR", str(root / "other-data"))
        expected = root / "shared-cache/equity_tushare"
    elif source == "explicit":
        monkeypatch.setenv("DATA_CACHE_DIR", str(root / "other-cache"))
        cfg["data_dir"] = str(root / "explicit-data")
        expected = root / "explicit-data/cache/equity_tushare"
    else:
        cfg["data_dir"] = "relative-data"
        expected = root / "project/relative-data/cache/equity_tushare"

    expected.mkdir(parents=True)
    pd.DataFrame({"trade_date": ["20261016"]}).to_parquet(expected / "daily_202610.parquet")
    pd.DataFrame({"adj_factor": [1.0]}).to_parquet(expected / "adj_factor_202610.parquet")
    before = {p.name: p.read_bytes() for p in expected.iterdir()}

    check = updater.do_check(cfg)
    assert check["ok"], check
    assert Path(check["cache_dir"]) == expected
    assert check["cached_months"] == 1
    assert check["stale_months"] == []
    health = updater.do_health(cfg)
    assert health["exists"]
    assert Path(health["cache_dir"]) == expected
    assert health["daily_files"] == 1
    if not cfg:
        assert Path(local_parquet_provider.check_data_dir()["cache_dir"]) == expected

    touched = []

    def sync_month(api, month, cache_dir, **kwargs):
        touched.append(cache_dir)
        return {"status": "skipped", "month": month}

    monkeypatch.setattr(updater, "_get_token", lambda cfg: "test-token")
    monkeypatch.setattr(updater, "_get_api", lambda token: object())
    monkeypatch.setattr(updater, "_sync_daily_month", sync_month)
    monkeypatch.setattr(updater, "_sync_adj_factor_month", sync_month)
    assert updater.do_sync({**cfg, "skip_stock_basic": True})["ok"]
    assert touched == [expected, expected]
    assert before == {p.name: p.read_bytes() for p in expected.iterdir()}
    assert not (Path.cwd() / "cache").exists()


def test_empty_environment_falls_back_to_project(cache_environment, monkeypatch):
    from aipha import config

    monkeypatch.setattr(config, "_Path", lambda value: cache_environment / "missing-cache")
    expected = cache_environment / "project/data/cache/equity_tushare"
    result = updater.do_check({})
    assert not result["ok"]
    assert Path(result["cache_dir"]) == expected
    assert str(expected) in result["error"]
    assert not expected.exists()
