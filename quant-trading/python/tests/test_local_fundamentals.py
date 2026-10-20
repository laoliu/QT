"""Local fundamental snapshots must preserve dates, units and source boundaries."""
import json

import pandas as pd
import pytest

from cache import local_fundamentals as local
from cache.stock_cache import StockCache


@pytest.fixture
def root(tmp_path, monkeypatch):
    folder = tmp_path / "equity_tushare"
    folder.mkdir()
    monkeypatch.setattr(local, "cache_dir", lambda: folder)
    return folder


def write(root, name, rows):
    pd.DataFrame(rows).to_parquet(root / name, index=False)


def test_snapshot_uses_latest_eligible_dates_and_converts_market_cap(root):
    # A single-day partition must not hide a newer row in the monthly file.
    write(root, "daily_basic_20261016.parquet", [
        dict(ts_code="600519.SH", trade_date="20261016", pe_ttm=20, total_mv=100),
    ])
    write(root, "daily_basic_202610.parquet", [
        dict(ts_code="600519.SH", trade_date="20261019", pe_ttm=21, pb=3, total_mv=200, circ_mv=150),
        dict(ts_code="600519.SH", trade_date="20261020", pe_ttm=999, total_mv=999),
    ])
    write(root, "fina_indicator_600519_SH.parquet", [
        dict(ts_code="600519.SH", end_date="20260630", ann_date="20260801", eps=8, bps=30),
        dict(ts_code="600519.SH", end_date="20260930", ann_date="20261020", eps=999),
    ])
    data = local.load_current(["600519"], "2026-10-19")["600519"]
    assert data["total_market_val"] == 2_000_000
    assert data["total_market_val_yi"] == .02
    assert data["circulating_market_val"] == 1_500_000
    assert data["valuation_date"] == "20261019"
    assert data["pe_ttm"] == 21
    assert data["eps"] == 8
    assert data["report_date"] == "20260630"
    assert data["announcement_date"] == "20260801"
    assert data["source"] == "local_tushare" and not data["pit_verified"]
    json.dumps(data, allow_nan=False)


def test_effective_announcement_date_and_symbol_identity_are_respected(root):
    write(root, "fina_indicator_000001_SZ.parquet", [
        dict(ts_code="000001.SH", end_date="20260630", ann_date="20260701", eps=999),
        dict(ts_code="000001.SZ", end_date="20260630", ann_date="20260701", f_ann_date="20261020", eps=9),
        dict(ts_code="000001.SZ", end_date="20260331", ann_date="20260430", eps=1),
    ])
    write(root, "fina_indicator_pit_v2_000001_SZ_20210101_20261019.parquet", [
        dict(ts_code="000001.SZ", end_date="20260630", ann_date="20260801", eps=2),
    ])
    result = local.load_current(["000001"], "2026-10-19")
    assert result["000001"]["eps"] == 2
    assert result["000001"]["report_date"] == "20260630"


def test_corrupt_source_is_preserved_and_older_valuation_retains_its_date(root):
    broken = root / "daily_basic_202610.parquet"
    broken.write_bytes(b"broken parquet")
    write(root, "daily_basic_202609.parquet", [
        dict(ts_code="600519.SH", trade_date="20260930", pe_ttm=20),
    ])
    result = local.load_current(["600519"], "2026-10-19")
    assert result["600519"]["valuation_date"] == "20260930"
    assert broken.read_bytes() == b"broken parquet"


def test_coverage_counts_financial_stocks_separately_from_files(root):
    for name in ["fina_indicator_600519_SH.parquet", "income_600519_SH.parquet",
                 "income_000001_SZ.parquet", "income_pit_v2_600519_SH_all_all.parquet",
                 "income_backup.parquet", "daily_basic_202610.parquet"]:
        (root / name).touch()
    result = local.coverage()
    assert result["financial_unique_stocks"] == 2
    assert result["datasets"]["income"] == dict(files=3, unique_stocks=2)
    assert result["valuation_files"] == 1


def test_refresh_fills_real_sqlite_and_historical_reads_do_not_use_current_cache(root, tmp_path, monkeypatch):
    from cache import stock_cache, prefetch
    monkeypatch.setattr(stock_cache, "_today_date_str", lambda: "2026-10-19")
    monkeypatch.setattr(prefetch, "prefetch_fundamentals", lambda *a, **k: pytest.fail("unexpected network fallback"))
    write(root, "stock_basic.parquet", [dict(ts_code="600519.SH", name="stock", industry="industry")])
    write(root, "daily_basic_202610.parquet", [dict(ts_code="600519.SH", trade_date="20261016", pe_ttm=20)])
    cache = StockCache(tmp_path / "sqlite")
    try:
        cache.save_stock_list({"600519": {"name": "stock"}}, "2026-10-19")
        cache.save_fundamentals({"600519": {"pe_ttm": 10}}, "2026-10-18")
        assert cache.auto_populate()["fundamentals"] == 1
        values, missing = cache.get_fundamentals(["600519"])
        assert not missing and values["600519"]["pe_ttm"] == 20
        assert cache.get_fundamentals(["600519"], "2026-10-17") == ({}, ["600519"])
        stats = cache.stats()
        assert stats["fundamentals"]["unique_stocks"] == 1
        assert stats["fundamentals"]["latest_valuation_date"] == "20261016"
        assert stats["local_fundamentals"]["valuation_files"] == 1
    finally:
        cache.close()


def test_missing_cache_reports_unavailable_without_creating_it(root):
    root.rmdir()
    assert not local.coverage()["available"]
    assert local.load_current(["600519"], "2026-10-19") == {}
    assert not root.exists()
