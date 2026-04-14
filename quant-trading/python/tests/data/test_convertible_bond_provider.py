"""tests/data/test_convertible_bond_provider.py – ConvertibleBondProvider 测试

通过 mock tushare 验证：解析、重试、缓存、数据转换。
"""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


@pytest.fixture()
def provider(tmp_path):
    """创建 ConvertibleBondProvider，mock tushare。"""
    from aipha.data.tushare_gateway import reset_tushare_gateway

    reset_tushare_gateway()
    with patch("tushare.pro_api") as mock_pro_api, \
         patch("tushare.set_token"):
        mock_api = MagicMock()
        mock_pro_api.return_value = mock_api
        from aipha.data.convertible_bond_provider import ConvertibleBondProvider

        p = ConvertibleBondProvider(
            token="fake",
            cache_dir=str(tmp_path / "cb_cache"),
            max_retries=2,
            backoff_factor=0.01,
        )
        p._mock_api = mock_api
        yield p
    reset_tushare_gateway()


def _bond_basic_df():
    return pd.DataFrame([{
        "ts_code": "127075.SZ",
        "bond_short_name": "盛路转债",
        "stk_code": "002446.SZ",
        "stk_short_name": "盛路通信",
        "conv_price": 8.88,
        "conv_start_date": "20230101",
        "conv_end_date": "20280101",
        "maturity_date": "20280630",
        "issue_size": 5.0,
        "list_status": "L",
        "list_date": "20220315",
        "delist_date": None,
    }])


def _cb_daily_df():
    return pd.DataFrame([{
        "ts_code": "127075.SZ",
        "trade_date": "20230901",
        "open": 105.0,
        "high": 108.0,
        "low": 103.0,
        "close": 106.5,
        "vol": 50000.0,
        "amount": 53000.0,
    }])


def _stock_daily_df():
    return pd.DataFrame([{
        "ts_code": "002446.SZ",
        "trade_date": "20230901",
        "open": 8.5,
        "high": 9.0,
        "low": 8.3,
        "close": 8.8,
        "vol": 120000.0,
    }])


# ── fingerprint ───────────────────────────────────────────


class TestFingerprint:
    def test_returns_datafingerprint(self, provider):
        fp = provider.fingerprint()
        assert fp.provider == "tushare_cb"
        assert fp.calendar == "SSE"


# ── _call_api_with_retry ─────────────────────────────────


class TestApiRetry:
    def test_success(self, provider):
        provider.api.cb_basic.return_value = _bond_basic_df()
        result = provider._call_api_with_retry("cb_basic", ts_code="127075.SZ")
        assert len(result) == 1

    def test_retry_on_failure(self, provider):
        provider.api.cb_basic.side_effect = [RuntimeError("err"), _bond_basic_df()]
        result = provider._call_api_with_retry("cb_basic")
        assert len(result) == 1

    def test_exhausted_raises(self, provider):
        provider.api.cb_daily.side_effect = RuntimeError("persistent")
        with pytest.raises(RuntimeError, match="cb_daily"):
            provider._call_api_with_retry("cb_daily")


# ── _parse_bond_info ─────────────────────────────────────


class TestParseBondInfo:
    def test_parse_normal(self, provider):
        row = _bond_basic_df().iloc[0]
        bond = provider._parse_bond_info(row)
        assert bond.ts_code == "127075.SZ"
        assert bond.stk_code == "002446.SZ"
        assert bond.conv_price == Decimal("8.88")

    def test_parse_missing_dates(self, provider):
        df = _bond_basic_df()
        df.at[0, "maturity_date"] = None
        bond = provider._parse_bond_info(df.iloc[0])
        assert bond.maturity_date == date(2099, 12, 31)


# ── _parse_bond_bars ─────────────────────────────────────


class TestParseBondBars:
    def test_parse(self, provider):
        bars = provider._parse_bond_bars(_cb_daily_df())
        assert len(bars) == 1
        assert bars[0].symbol == "127075.SZ"
        assert bars[0].close == 106.5

    def test_parse_empty(self, provider):
        # sort_values 需要 trade_date 列存在
        df = pd.DataFrame(columns=["ts_code", "trade_date", "open", "high", "low", "close"])
        bars = provider._parse_bond_bars(df)
        assert bars == []


# ── _parse_stock_bars ────────────────────────────────────


class TestParseStockBars:
    def test_parse(self, provider):
        bars = provider._parse_stock_bars(_stock_daily_df())
        assert len(bars) == 1
        assert bars[0].symbol == "002446.SZ"
        assert bars[0].close == 8.8


# ── load_bond_list ───────────────────────────────────────


class TestLoadBondList:
    def test_load(self, provider):
        provider.api.cb_basic.return_value = _bond_basic_df()
        bonds = provider.load_bond_list()
        assert len(bonds) == 1
        assert bonds[0].ts_code == "127075.SZ"

    def test_empty_response(self, provider):
        provider.api.cb_basic.return_value = pd.DataFrame()
        assert provider.load_bond_list() == []

    def test_none_response(self, provider):
        provider.api.cb_basic.return_value = None
        assert provider.load_bond_list() == []


# ── load_bond_info ───────────────────────────────────────


class TestLoadBondInfo:
    def test_found(self, provider):
        provider.api.cb_basic.return_value = _bond_basic_df()
        bond = provider.load_bond_info(symbol="127075.SZ")
        assert bond is not None
        assert bond.ts_code == "127075.SZ"

    def test_not_found(self, provider):
        provider.api.cb_basic.return_value = pd.DataFrame()
        assert provider.load_bond_info(symbol="999999.SZ") is None


# ── load_bond_bars ───────────────────────────────────────


class TestLoadBondBars:
    def test_from_api(self, provider):
        # 缓存子目录需要预创建
        (Path(provider.cache_dir) / "cb_daily").mkdir(parents=True, exist_ok=True)
        provider.api.cb_daily.return_value = _cb_daily_df()
        bars = provider.load_bond_bars(
            symbol="127075.SZ", start=date(2023, 9, 1), end=date(2023, 9, 1)
        )
        assert len(bars) == 1

    def test_empty(self, provider):
        provider.api.cb_daily.return_value = pd.DataFrame()
        bars = provider.load_bond_bars(
            symbol="127075.SZ", start=date(2023, 9, 1), end=date(2023, 9, 1)
        )
        assert bars == []


# ── load_stock_bars ──────────────────────────────────────


class TestLoadStockBars:
    def test_from_api(self, provider):
        provider.api.daily.return_value = _stock_daily_df()
        bars = provider.load_stock_bars(
            symbol="002446.SZ", start=date(2023, 9, 1), end=date(2023, 9, 1)
        )
        assert len(bars) == 1

    def test_empty(self, provider):
        provider.api.daily.return_value = pd.DataFrame()
        bars = provider.load_stock_bars(
            symbol="002446.SZ", start=date(2023, 9, 1), end=date(2023, 9, 1)
        )
        assert bars == []


# ── _cache_path ──────────────────────────────────────────


class TestCachePath:
    def test_deterministic(self, provider):
        p1 = provider._cache_path("127075.SZ", date(2023, 1, 1), date(2023, 12, 31), "cb_daily")
        p2 = provider._cache_path("127075.SZ", date(2023, 1, 1), date(2023, 12, 31), "cb_daily")
        assert p1 == p2

    def test_different_for_different_params(self, provider):
        p1 = provider._cache_path("127075.SZ", date(2023, 1, 1), date(2023, 6, 30), "cb_daily")
        p2 = provider._cache_path("127075.SZ", date(2023, 7, 1), date(2023, 12, 31), "cb_daily")
        assert p1 != p2
