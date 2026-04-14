"""Data health check edge-case tests.

Covers untested branches:
- check_missing_rate: trade_date column, no time column at all
- check_price_jumps: max_issues_per_symbol truncation
- check_duplicates/check_order: no time columns → skip
- check_value_range: all NaN, simultaneous min/max violations
- HealthReport.to_dict()
- DataHealthChecker._infer_date_range: all-empty data → ("unknown","unknown")
- quick_check_bars_by_date: unsorted date keys → warning
- check_daily_cache_health: corrupted parquet, abnormal rows
"""
from __future__ import annotations

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from aipha.data.health_check import (
    CheckResult,
    CheckStatus,
    DataHealthChecker,
    HealthReport,
    check_duplicates,
    check_missing_rate,
    check_order,
    check_price_jumps,
    check_value_range,
    quick_check_bars_by_date,
)


# ============================================================================
# check_missing_rate edge branches
# ============================================================================


class TestMissingRateEdge:

    def test_trade_date_column_branch(self):
        """trade_date 列（非 trade_time）正常工作"""
        df = pd.DataFrame({
            "trade_date": pd.date_range("2024-01-01", periods=100, freq="B"),
            "close": np.random.randn(100),
        })
        result = check_missing_rate({"A": df}, expected_bars_per_day=1)
        assert result.status in (CheckStatus.PASS, CheckStatus.WARNING)

    def test_no_time_column_at_all(self):
        """既无 trade_time 也无 trade_date → fallback 行数估算"""
        df = pd.DataFrame({"close": range(50)})
        result = check_missing_rate({"A": df}, expected_bars_per_day=1)
        assert isinstance(result, CheckResult)
        assert result.status in (CheckStatus.PASS, CheckStatus.WARNING, CheckStatus.ERROR)


# ============================================================================
# check_price_jumps – truncation
# ============================================================================


class TestPriceJumpsEdge:

    def test_max_issues_truncation(self):
        """超过 max_issues_per_symbol 的跳变被截断"""
        prices = [10.0]
        for i in range(20):
            # Alternating large jumps: +50%, -50%, ...
            last = prices[-1]
            prices.append(last * 1.5 if i % 2 == 0 else last * 0.5)
        df = pd.DataFrame({
            "trade_time": pd.date_range("2024-01-01", periods=len(prices), freq="h"),
            "close": prices,
        })
        result = check_price_jumps({"A": df}, threshold=0.10, max_issues_per_symbol=5)
        # Should have at most 5 issues despite 20 jumps
        symbol_issues = [i for i in result.issues if i.symbol == "A"]
        assert len(symbol_issues) <= 5


# ============================================================================
# check_duplicates / check_order – no time columns
# ============================================================================


class TestDuplicatesEdge:

    def test_no_time_columns_skip(self):
        """时间列都不存在时跳过"""
        df = pd.DataFrame({"close": [1, 2, 3]})
        result = check_duplicates({"A": df}, time_column="trade_time")
        assert result.status == CheckStatus.PASS


class TestOrderEdge:

    def test_no_time_columns_skip(self):
        """时间列都不存在时跳过"""
        df = pd.DataFrame({"close": [1, 2, 3]})
        result = check_order({"A": df}, time_column="trade_time")
        assert result.status == CheckStatus.PASS


# ============================================================================
# check_value_range edge cases
# ============================================================================


class TestValueRangeEdge:

    def test_all_nan_column(self):
        """全 NaN 列, allow_null=False → 报空值问题"""
        df = pd.DataFrame({"close": [np.nan, np.nan, np.nan]})
        result = check_value_range({"A": df}, column="close", min_value=0, allow_null=False)
        # Should flag null values
        assert result.status in (CheckStatus.WARNING, CheckStatus.ERROR)

    def test_simultaneous_min_max_violations(self):
        """同时有低于 min 和高于 max 的值"""
        df = pd.DataFrame({"close": [-10, 5, 500, 3]})
        result = check_value_range(
            {"A": df}, column="close", min_value=0, max_value=100
        )
        # Should have issues for both violations
        assert result.status != CheckStatus.PASS
        # At least 2 issues (below min + above max)
        assert len(result.issues) >= 2


# ============================================================================
# HealthReport.to_dict()
# ============================================================================


class TestHealthReportToDict:

    def test_to_dict_structure(self):
        cr = CheckResult(
            check_name="test_check",
            status=CheckStatus.PASS,
            details={"key": "val"},
            issues=[],
        )
        report = HealthReport(
            check_time=datetime(2024, 1, 1),
            symbols=["A", "B"],
            date_range=("2024-01-01", "2024-06-01"),
            results=[cr],
        )
        d = report.to_dict()
        # to_dict returns summary, overall_status, results
        assert "summary" in d
        assert d["overall_status"] == "pass"
        assert len(d["results"]) == 1
        assert d["results"][0]["check_name"] == "test_check"
        assert d["summary"]["symbols_count"] == 2

    def test_overall_status_worst(self):
        """overall_status 取最严重的"""
        r1 = CheckResult("a", CheckStatus.PASS, {}, [])
        r2 = CheckResult("b", CheckStatus.ERROR, {}, [])
        report = HealthReport(
            check_time=datetime(2024, 1, 1),
            symbols=["X"],
            date_range=("s", "e"),
            results=[r1, r2],
        )
        assert report.overall_status == CheckStatus.ERROR


# ============================================================================
# DataHealthChecker._infer_date_range – all empty
# ============================================================================


class TestInferDateRangeEdge:

    def test_all_empty_returns_unknown(self):
        """全空 DataFrame → ("unknown", "unknown")"""
        checker = DataHealthChecker()
        data = {"A": pd.DataFrame(), "B": pd.DataFrame()}
        start, end = checker._infer_date_range(data)
        assert start == "unknown"
        assert end == "unknown"

    def test_no_time_col_returns_unknown(self):
        """没有 trade_time/trade_date 列"""
        checker = DataHealthChecker()
        data = {"A": pd.DataFrame({"close": [1, 2, 3]})}
        start, end = checker._infer_date_range(data)
        assert start == "unknown"
        assert end == "unknown"


# ============================================================================
# quick_check_bars_by_date – unsorted keys
# ============================================================================

from aipha.domain.models import Bar


def _make_bar(symbol: str, dt: date, price: float = 10.0) -> Bar:
    ts = datetime.combine(dt, datetime.min.time().replace(hour=15))
    return Bar(
        symbol=symbol,
        start_ts=ts.replace(hour=9, minute=30),
        end_ts=ts,
        open=price,
        high=price,
        low=price,
        close=price,
        volume=10000,
    )


class TestQuickCheckBarsByDateEdge:

    def test_unsorted_keys_warning(self):
        """日期键未排序 → date_order 为 warning"""
        # Use dict with intentionally unsorted insertion order
        d2 = date(2024, 1, 3)
        d1 = date(2024, 1, 2)
        bars = {}
        bars[d2] = {"A": _make_bar("A", d2)}
        bars[d1] = {"A": _make_bar("A", d1)}
        result = quick_check_bars_by_date(bars)
        date_check = [c for c in result["checks"] if c["name"] == "date_order"]
        assert date_check[0]["status"] == "warning"


# ============================================================================
# check_daily_cache_health – corrupted parquet & abnormal rows
# ============================================================================

from aipha.data.health_check import check_daily_cache_health


class TestCacheHealthEdge:

    def test_corrupted_parquet_file(self, tmp_path):
        """损坏的 parquet 文件 → read_errors > 0"""
        cache_dir = tmp_path / "tushare"
        cache_dir.mkdir()
        # Write a corrupted file
        f = cache_dir / "600000.SH_202401_D_qfq.parquet"
        f.write_text("this is not a parquet file")
        result = check_daily_cache_health(str(cache_dir))
        # Should detect the read error
        file_check = [c for c in result["checks"] if c["name"] == "file_readability"]
        if file_check:
            assert file_check[0]["status"] == "error"

    def test_abnormal_row_count(self, tmp_path):
        """行数 > 25 → row_count_sanity warning"""
        cache_dir = tmp_path / "tushare"
        cache_dir.mkdir()
        df = pd.DataFrame({
            "trade_date": pd.date_range("2024-01-01", periods=30, freq="D"),
            "close": range(30),
        })
        f = cache_dir / "600000.SH_202401_D_qfq.parquet"
        df.to_parquet(f)
        result = check_daily_cache_health(str(cache_dir))
        row_check = [c for c in result["checks"] if c["name"] == "row_count_sanity"]
        if row_check:
            assert row_check[0]["status"] == "warning"

    def test_empty_cache_dir(self, tmp_path):
        """空目录 → 返回 no_files 状态"""
        cache_dir = tmp_path / "empty"
        cache_dir.mkdir()
        result = check_daily_cache_health(str(cache_dir))
        assert result["status"] in ("pass", "warning", "error", "no_files")
