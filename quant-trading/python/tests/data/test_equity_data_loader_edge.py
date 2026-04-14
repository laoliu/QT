"""Edge tests for data/equity_data_loader.py — 边界与极端场景。

覆盖:
- EquityDataBundle: symbols/trade_dates 空场景、get_daily_bar 空、
  get_industry 未知、is_index_member 不存在的索引
- EquityDataLoader: provider 异常降级、load_ohlcv=False、
  merge 一侧为空、sw_l1 行业源、index_codes 加载失败
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from aipha.data.equity_data_loader import EquityDataBundle, EquityDataLoader


# =========================================================================
# EquityDataBundle edge cases
# =========================================================================


class TestEquityDataBundleEdge:
    """EquityDataBundle 的属性和方法边界。"""

    def test_symbols_from_daily_bars(self) -> None:
        df = pd.DataFrame({"ts_code": ["000001.SZ", "000002.SZ", "000001.SZ"]})
        bundle = EquityDataBundle(daily_bars=df)
        assert bundle.symbols == ["000001.SZ", "000002.SZ"]

    def test_symbols_fallback_to_stock_basic(self) -> None:
        basic = pd.DataFrame({"ts_code": ["600000.SH"]})
        bundle = EquityDataBundle(stock_basic=basic)
        assert bundle.symbols == ["600000.SH"]

    def test_symbols_empty(self) -> None:
        bundle = EquityDataBundle()
        assert bundle.symbols == []

    def test_trade_dates_empty(self) -> None:
        bundle = EquityDataBundle()
        assert bundle.trade_dates == []

    def test_trade_dates_from_bars(self) -> None:
        df = pd.DataFrame({
            "ts_code": ["A", "B"],
            "trade_date": ["20240102", "20240103"],
        })
        bundle = EquityDataBundle(daily_bars=df)
        assert bundle.trade_dates == ["20240102", "20240103"]

    def test_get_daily_bar_empty(self) -> None:
        bundle = EquityDataBundle()
        result = bundle.get_daily_bar("20240102")
        assert result.empty

    def test_get_daily_bar_no_match(self) -> None:
        df = pd.DataFrame({"trade_date": ["20240101"], "close": [10.0]})
        bundle = EquityDataBundle(daily_bars=df)
        result = bundle.get_daily_bar("20240102")
        assert result.empty

    def test_get_daily_basic_for_date_empty(self) -> None:
        bundle = EquityDataBundle()
        assert bundle.get_daily_basic_for_date("20240102").empty

    def test_get_industry_unknown(self) -> None:
        bundle = EquityDataBundle()
        assert bundle.get_industry("999999.SZ") == "未知"

    def test_get_industry_known(self) -> None:
        bundle = EquityDataBundle(industry_mapping={"000001.SZ": "银行"})
        assert bundle.get_industry("000001.SZ") == "银行"

    def test_is_index_member_true(self) -> None:
        bundle = EquityDataBundle(
            index_members={"000300.SH": {"000001.SZ", "000002.SZ"}}
        )
        assert bundle.is_index_member("000001.SZ", "000300.SH") is True

    def test_is_index_member_false(self) -> None:
        bundle = EquityDataBundle(
            index_members={"000300.SH": {"000001.SZ"}}
        )
        assert bundle.is_index_member("600000.SH", "000300.SH") is False

    def test_is_index_member_unknown_index(self) -> None:
        bundle = EquityDataBundle()
        assert bundle.is_index_member("000001.SZ", "UNKNOWN") is False


# =========================================================================
# EquityDataLoader edge cases
# =========================================================================


class TestEquityDataLoaderEdge:
    """EquityDataLoader 降级与选项变体。"""

    def _mock_provider(self, fail_methods: set = frozenset()):
        """创建 mock equity provider，fail_methods 中的方法抛异常。"""
        p = MagicMock()
        p.load_stock_basic.return_value = pd.DataFrame({"ts_code": ["A"]})
        p.load_daily_bars_range.return_value = pd.DataFrame({
            "ts_code": ["A"], "trade_date": ["20240102"], "close": [10.0],
        })
        p.load_daily_basic_range.return_value = pd.DataFrame({
            "ts_code": ["A"], "trade_date": ["20240102"], "total_mv": [1e9],
        })
        p.load_industry_mapping.return_value = {"A": "银行"}
        p.load_sw_industry_mapping.return_value = {"A": "银行I"}
        p.load_index_members.return_value = pd.DataFrame({"con_code": ["A"]})

        for m in fail_methods:
            getattr(p, m).side_effect = RuntimeError(f"{m} failed")
        return p

    def test_all_success(self) -> None:
        loader = EquityDataLoader(self._mock_provider())
        bundle = loader.load_bundle("20240101", "20240131")
        assert not bundle.daily_bars.empty
        assert not bundle.daily_basic.empty
        assert "A" in bundle.industry_mapping
        assert bundle.metadata["start_date"] == "20240101"

    def test_stock_basic_fail_degrades(self) -> None:
        loader = EquityDataLoader(self._mock_provider({"load_stock_basic"}))
        bundle = loader.load_bundle("20240101", "20240131")
        assert bundle.stock_basic.empty
        # 其他数据仍正常加载
        assert not bundle.daily_bars.empty

    def test_daily_bars_fail_degrades(self) -> None:
        loader = EquityDataLoader(self._mock_provider({"load_daily_bars_range"}))
        bundle = loader.load_bundle("20240101", "20240131")
        assert bundle.daily_bars.empty
        assert not bundle.daily_basic.empty

    def test_load_ohlcv_false(self) -> None:
        p = self._mock_provider()
        loader = EquityDataLoader(p)
        bundle = loader.load_bundle("20240101", "20240131", load_ohlcv=False)
        p.load_daily_bars_range.assert_not_called()
        assert bundle.daily_bars.empty

    def test_load_basic_false(self) -> None:
        p = self._mock_provider()
        loader = EquityDataLoader(p)
        bundle = loader.load_bundle("20240101", "20240131", load_basic=False)
        p.load_daily_basic_range.assert_not_called()

    def test_load_industry_false(self) -> None:
        p = self._mock_provider()
        loader = EquityDataLoader(p)
        bundle = loader.load_bundle("20240101", "20240131", load_industry=False)
        p.load_industry_mapping.assert_not_called()

    def test_sw_l1_industry_source(self) -> None:
        p = self._mock_provider()
        loader = EquityDataLoader(p)
        bundle = loader.load_bundle(
            "20240101", "20240131", industry_source="sw_l1",
        )
        p.load_sw_industry_mapping.assert_called_once()
        assert "A" in bundle.industry_mapping

    def test_index_codes_load(self) -> None:
        p = self._mock_provider()
        loader = EquityDataLoader(p)
        bundle = loader.load_bundle(
            "20240101", "20240131", index_codes=["000300.SH"],
        )
        assert "000300.SH" in bundle.index_members
        assert "A" in bundle.index_members["000300.SH"]

    def test_index_codes_fail_degrades(self) -> None:
        p = self._mock_provider({"load_index_members"})
        loader = EquityDataLoader(p)
        bundle = loader.load_bundle(
            "20240101", "20240131", index_codes=["000300.SH"],
        )
        assert len(bundle.index_members) == 0

    def test_merge_both_present(self) -> None:
        p = self._mock_provider()
        loader = EquityDataLoader(p)
        bundle = loader.load_bundle("20240101", "20240131")
        merged = loader.merge_ohlcv_and_basic(bundle)
        assert not merged.empty

    def test_merge_bars_empty(self) -> None:
        loader = EquityDataLoader(self._mock_provider())
        bundle = EquityDataBundle(
            daily_basic=pd.DataFrame({"ts_code": ["A"], "trade_date": ["20240102"]}),
        )
        merged = loader.merge_ohlcv_and_basic(bundle)
        assert not merged.empty  # returns daily_basic copy

    def test_merge_basic_empty(self) -> None:
        loader = EquityDataLoader(self._mock_provider())
        bundle = EquityDataBundle(
            daily_bars=pd.DataFrame({"ts_code": ["A"], "trade_date": ["20240102"]}),
        )
        merged = loader.merge_ohlcv_and_basic(bundle)
        assert not merged.empty  # returns daily_bars copy

    def test_merge_both_empty(self) -> None:
        loader = EquityDataLoader(self._mock_provider())
        bundle = EquityDataBundle()
        merged = loader.merge_ohlcv_and_basic(bundle)
        assert merged.empty
