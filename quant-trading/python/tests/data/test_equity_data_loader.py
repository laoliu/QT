"""tests/data/test_equity_data_loader.py – EquityDataBundle / EquityDataLoader 测试

通过注入 mock provider 验证：加载逻辑、降级策略、合并规则。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from aipha.data.equity_data_loader import EquityDataBundle, EquityDataLoader


# ── Helper ─────────────────────────────────────────────────

def _bars_df():
    """3 只股票 × 2 天行情"""
    rows = []
    for d in ("20230901", "20230904"):
        for sym in ("000001.SZ", "000002.SZ", "600036.SH"):
            rows.append({"ts_code": sym, "trade_date": d,
                         "open": 10.0, "high": 11.0, "low": 9.5,
                         "close": 10.5, "vol": 1000.0})
    return pd.DataFrame(rows)


def _basic_df():
    rows = []
    for d in ("20230901", "20230904"):
        for sym in ("000001.SZ", "000002.SZ"):
            rows.append({"ts_code": sym, "trade_date": d,
                         "close": 10.5, "pe": 8.0, "pb": 1.2,
                         "total_mv": 5e9, "turnover_rate": 3.0})
    return pd.DataFrame(rows)


def _stock_basic_df():
    return pd.DataFrame([
        {"ts_code": "000001.SZ", "name": "平安银行", "industry": "银行",
         "list_date": "19910403", "list_status": "L"},
        {"ts_code": "000002.SZ", "name": "万科A", "industry": "房地产",
         "list_date": "19910129", "list_status": "L"},
    ])


# ── EquityDataBundle ──────────────────────────────────────


class TestEquityDataBundleDefaults:
    def test_empty_bundle(self):
        b = EquityDataBundle()
        assert b.symbols == []
        assert b.trade_dates == []

    def test_get_daily_bar_empty(self):
        b = EquityDataBundle()
        assert b.get_daily_bar("20230901").empty

    def test_get_daily_basic_empty(self):
        b = EquityDataBundle()
        assert b.get_daily_basic_for_date("20230901").empty

    def test_get_industry_default(self):
        b = EquityDataBundle()
        assert b.get_industry("000001.SZ") == "未知"

    def test_is_index_member_false(self):
        b = EquityDataBundle()
        assert b.is_index_member("000001.SZ", "000300.SH") is False


class TestEquityDataBundleProperties:
    def test_symbols_from_daily_bars(self):
        b = EquityDataBundle(daily_bars=_bars_df())
        assert b.symbols == ["000001.SZ", "000002.SZ", "600036.SH"]

    def test_symbols_from_stock_basic_fallback(self):
        b = EquityDataBundle(stock_basic=_stock_basic_df())
        assert b.symbols == ["000001.SZ", "000002.SZ"]

    def test_trade_dates(self):
        b = EquityDataBundle(daily_bars=_bars_df())
        assert b.trade_dates == ["20230901", "20230904"]

    def test_get_daily_bar(self):
        b = EquityDataBundle(daily_bars=_bars_df())
        df = b.get_daily_bar("20230901")
        assert len(df) == 3

    def test_get_daily_basic_for_date(self):
        b = EquityDataBundle(daily_basic=_basic_df())
        df = b.get_daily_basic_for_date("20230901")
        assert len(df) == 2

    def test_get_industry(self):
        b = EquityDataBundle(industry_mapping={"000001.SZ": "银行"})
        assert b.get_industry("000001.SZ") == "银行"

    def test_is_index_member_true(self):
        b = EquityDataBundle(index_members={"000300.SH": {"000001.SZ", "000002.SZ"}})
        assert b.is_index_member("000001.SZ", "000300.SH") is True

    def test_is_index_member_not_in(self):
        b = EquityDataBundle(index_members={"000300.SH": {"000001.SZ"}})
        assert b.is_index_member("600036.SH", "000300.SH") is False


# ── EquityDataLoader ──────────────────────────────────────


class TestEquityDataLoaderLoadBundle:
    def _mock_provider(self, **overrides):
        m = MagicMock()
        m.load_stock_basic.return_value = overrides.get("stock_basic", _stock_basic_df())
        m.load_daily_bars_range.return_value = overrides.get("bars", _bars_df())
        m.load_daily_basic_range.return_value = overrides.get("basic", _basic_df())
        m.load_industry_mapping.return_value = overrides.get(
            "industry", {"000001.SZ": "银行", "000002.SZ": "房地产"}
        )
        m.load_sw_industry_mapping.return_value = overrides.get("sw", {"000001.SZ": "银行II"})
        idx_df = overrides.get("index_members", pd.DataFrame([
            {"con_code": "000001.SZ"}, {"con_code": "000002.SZ"},
        ]))
        m.load_index_members.return_value = idx_df
        return m

    def test_full_load(self):
        mp = self._mock_provider()
        loader = EquityDataLoader(equity_provider=mp)
        b = loader.load_bundle("20230901", "20230904")
        assert len(b.symbols) == 3
        assert len(b.trade_dates) == 2
        assert len(b.industry_mapping) == 2
        assert b.metadata["start_date"] == "20230901"

    def test_skip_ohlcv(self):
        mp = self._mock_provider()
        loader = EquityDataLoader(equity_provider=mp)
        b = loader.load_bundle("20230901", "20230904", load_ohlcv=False)
        assert b.daily_bars.empty
        mp.load_daily_bars_range.assert_not_called()

    def test_skip_basic(self):
        mp = self._mock_provider()
        loader = EquityDataLoader(equity_provider=mp)
        b = loader.load_bundle("20230901", "20230904", load_basic=False)
        assert b.daily_basic.empty
        mp.load_daily_basic_range.assert_not_called()

    def test_skip_industry(self):
        mp = self._mock_provider()
        loader = EquityDataLoader(equity_provider=mp)
        b = loader.load_bundle("20230901", "20230904", load_industry=False)
        assert b.industry_mapping == {}
        mp.load_industry_mapping.assert_not_called()

    def test_sw_industry_source(self):
        mp = self._mock_provider()
        loader = EquityDataLoader(equity_provider=mp)
        b = loader.load_bundle("20230901", "20230904", industry_source="sw_l1")
        mp.load_sw_industry_mapping.assert_called_once_with(level="L1")
        assert "000001.SZ" in b.industry_mapping

    def test_index_codes(self):
        mp = self._mock_provider()
        loader = EquityDataLoader(equity_provider=mp)
        b = loader.load_bundle("20230901", "20230904", index_codes=["000300.SH"])
        assert "000300.SH" in b.index_members
        assert "000001.SZ" in b.index_members["000300.SH"]

    def test_stock_basic_failure_degrades(self):
        mp = self._mock_provider()
        mp.load_stock_basic.side_effect = RuntimeError("network")
        loader = EquityDataLoader(equity_provider=mp)
        b = loader.load_bundle("20230901", "20230904")
        assert b.stock_basic.empty  # 降级为空

    def test_bars_failure_degrades(self):
        mp = self._mock_provider()
        mp.load_daily_bars_range.side_effect = RuntimeError("timeout")
        loader = EquityDataLoader(equity_provider=mp)
        b = loader.load_bundle("20230901", "20230904")
        assert b.daily_bars.empty


class TestEquityDataLoaderMerge:
    def test_merge_both(self):
        b = EquityDataBundle(daily_bars=_bars_df(), daily_basic=_basic_df())
        loader = EquityDataLoader(equity_provider=MagicMock())
        merged = loader.merge_ohlcv_and_basic(b)
        assert "pe" in merged.columns
        assert "open" in merged.columns
        # left join: 所有 bars 行都保留
        assert len(merged) == len(_bars_df())

    def test_merge_empty_bars(self):
        b = EquityDataBundle(daily_basic=_basic_df())
        loader = EquityDataLoader(equity_provider=MagicMock())
        merged = loader.merge_ohlcv_and_basic(b)
        assert not merged.empty  # 返回 basic 的副本

    def test_merge_empty_basic(self):
        b = EquityDataBundle(daily_bars=_bars_df())
        loader = EquityDataLoader(equity_provider=MagicMock())
        merged = loader.merge_ohlcv_and_basic(b)
        assert "open" in merged.columns
        assert len(merged) == len(_bars_df())

    def test_merge_both_empty(self):
        b = EquityDataBundle()
        loader = EquityDataLoader(equity_provider=MagicMock())
        merged = loader.merge_ohlcv_and_basic(b)
        assert merged.empty
