"""ST 前视偏差修复 — 回归测试

验证：
- _build_st_interval_index 正确构建 ST 区间索引
- _is_st_by_namechange 按交易日准确判断 ST 状态
- _enrich_bar_metadata 优先使用 namechange 而非 stock_basic.name
- TuShareEquityProvider.is_st_at_date 与 load_namechange
"""
from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from aipha.backtest.config_runner import (
    _build_st_interval_index,
    _enrich_bar_metadata,
    _is_st_by_namechange,
)


# ============================================================================
# _build_st_interval_index 测试
# ============================================================================


class TestBuildSTIntervalIndex:
    """验证从 namechange DataFrame 构建 ST 区间索引。"""

    @staticmethod
    def _make_nc_df(records: list[dict]) -> pd.DataFrame:
        df = pd.DataFrame(records)
        for col in ("start_date", "end_date"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
        return df

    def test_extracts_st_intervals(self):
        """ST 名称的记录应被提取，非 ST 记录忽略。"""
        nc = self._make_nc_df([
            {"ts_code": "000001.SZ", "name": "深发展A", "start_date": "20000101", "end_date": "20120101"},
            {"ts_code": "000001.SZ", "name": "平安银行", "start_date": "20120102", "end_date": None},
            {"ts_code": "000002.SZ", "name": "万科A", "start_date": "20000101", "end_date": "20230601"},
            {"ts_code": "000002.SZ", "name": "ST万科", "start_date": "20230602", "end_date": "20240101"},
            {"ts_code": "000002.SZ", "name": "万科A", "start_date": "20240102", "end_date": None},
        ])
        idx = _build_st_interval_index(nc)
        assert "000001.SZ" not in idx, "000001 不含 ST 名称"
        assert "000002.SZ" in idx
        assert len(idx["000002.SZ"]) == 1
        start, end = idx["000002.SZ"][0]
        assert start == date(2023, 6, 2)
        assert end == date(2024, 1, 1)

    def test_star_st_detected(self):
        """*ST 应被识别。"""
        nc = self._make_nc_df([
            {"ts_code": "600888.SH", "name": "*ST新雅", "start_date": "20200101", "end_date": "20210101"},
        ])
        idx = _build_st_interval_index(nc)
        assert "600888.SH" in idx

    def test_delisting_detected(self):
        """退市整理 应被识别。"""
        nc = self._make_nc_df([
            {"ts_code": "000999.SZ", "name": "退市长油", "start_date": "20190101", "end_date": "20190601"},
        ])
        idx = _build_st_interval_index(nc)
        assert "000999.SZ" in idx

    def test_empty_df_returns_empty(self):
        idx = _build_st_interval_index(pd.DataFrame())
        assert idx == {}

    def test_none_df_returns_empty(self):
        idx = _build_st_interval_index(None)
        assert idx == {}

    def test_no_end_date_uses_far_future(self):
        """end_date 为 NaN 时应视为持续到未来。"""
        nc = self._make_nc_df([
            {"ts_code": "600666.SH", "name": "ST瑞德", "start_date": "20220501", "end_date": None},
        ])
        idx = _build_st_interval_index(nc)
        _, end = idx["600666.SH"][0]
        assert end == date(2099, 12, 31)

    def test_multiple_st_periods(self):
        """同一股票多次 ST 应产生多个区间。"""
        nc = self._make_nc_df([
            {"ts_code": "000005.SZ", "name": "ST星源", "start_date": "20100101", "end_date": "20110101"},
            {"ts_code": "000005.SZ", "name": "世纪星源", "start_date": "20110102", "end_date": "20150101"},
            {"ts_code": "000005.SZ", "name": "*ST星源", "start_date": "20150102", "end_date": "20160101"},
        ])
        idx = _build_st_interval_index(nc)
        assert len(idx["000005.SZ"]) == 2


# ============================================================================
# _is_st_by_namechange 测试
# ============================================================================


class TestIsSTByNamechange:
    """验证区间查询的正确性。"""

    @pytest.fixture
    def st_index(self):
        return {
            "000002.SZ": [(date(2023, 6, 2), date(2024, 1, 1))],
            "000005.SZ": [
                (date(2010, 1, 1), date(2011, 1, 1)),
                (date(2015, 1, 2), date(2016, 1, 1)),
            ],
        }

    def test_in_st_period(self, st_index):
        assert _is_st_by_namechange("000002.SZ", date(2023, 8, 15), st_index) is True

    def test_before_st_period(self, st_index):
        assert _is_st_by_namechange("000002.SZ", date(2023, 3, 1), st_index) is False

    def test_after_st_period(self, st_index):
        assert _is_st_by_namechange("000002.SZ", date(2024, 6, 1), st_index) is False

    def test_at_start_boundary(self, st_index):
        assert _is_st_by_namechange("000002.SZ", date(2023, 6, 2), st_index) is True

    def test_at_end_boundary(self, st_index):
        assert _is_st_by_namechange("000002.SZ", date(2024, 1, 1), st_index) is True

    def test_unknown_symbol(self, st_index):
        assert _is_st_by_namechange("999999.SZ", date(2023, 8, 15), st_index) is False

    def test_second_st_period(self, st_index):
        assert _is_st_by_namechange("000005.SZ", date(2015, 6, 1), st_index) is True

    def test_between_st_periods(self, st_index):
        assert _is_st_by_namechange("000005.SZ", date(2013, 1, 1), st_index) is False

    def test_empty_index(self):
        assert _is_st_by_namechange("000002.SZ", date(2023, 8, 15), {}) is False


# ============================================================================
# _enrich_bar_metadata 集成测试（namechange vs stock_basic）
# ============================================================================


class TestEnrichBarMetadataSTSource:
    """验证 _enrich_bar_metadata 优先使用 namechange ST 索引。"""

    @staticmethod
    def _make_stock_basic(records: list[dict]) -> pd.DataFrame:
        df = pd.DataFrame(records)
        df = df.set_index("ts_code")
        return df

    def test_namechange_marks_st_when_basic_says_normal(self):
        """股票当前名称正常，但历史上该日期处于 ST 期间 → 应标记 is_st。

        这是修复前视偏差的核心场景：摘帽后 stock_basic.name 不含 ST，
        但回测日期恰处 ST 区间。
        """
        stock_basic = self._make_stock_basic([
            {"ts_code": "000002.SZ", "name": "万科A", "list_date": "19910129", "industry": "房地产"},
        ])
        st_index = {"000002.SZ": [(date(2023, 6, 2), date(2024, 1, 1))]}

        meta: dict = {}
        _enrich_bar_metadata(
            meta, "000002.SZ", date(2023, 8, 15),
            stock_basic, {}, None,
            namechange_st_index=st_index,
        )
        assert meta.get("is_st") is True, "namechange 应标记历史 ST 区间"

    def test_namechange_not_st_when_basic_says_st(self):
        """股票当前名称含 ST，但历史上该日期不在 ST 区间 → 不应标记 is_st。

        场景：2024年6月后还在 ST，但回测 2020 年时不在 ST 区间。
        """
        stock_basic = self._make_stock_basic([
            {"ts_code": "000099.SZ", "name": "ST测试", "list_date": "20100101", "industry": "测试"},
        ])
        st_index = {"000099.SZ": [(date(2024, 1, 1), date(2099, 12, 31))]}

        meta: dict = {}
        _enrich_bar_metadata(
            meta, "000099.SZ", date(2020, 6, 15),
            stock_basic, {}, None,
            namechange_st_index=st_index,
        )
        assert "is_st" not in meta or meta["is_st"] is not True, (
            "2020 年不在 ST 区间，不应标记"
        )

    def test_fallback_to_basic_name_when_no_namechange(self):
        """无 namechange 索引时，回退到 stock_basic.name 判断。"""
        stock_basic = self._make_stock_basic([
            {"ts_code": "000888.SZ", "name": "ST运盛", "list_date": "20000101", "industry": "医药"},
        ])

        meta: dict = {}
        _enrich_bar_metadata(
            meta, "000888.SZ", date(2023, 1, 1),
            stock_basic, {}, None,
            namechange_st_index=None,
        )
        assert meta.get("is_st") is True, "回退到 stock_basic.name 判断"

    def test_fallback_with_empty_index(self):
        """空 namechange 索引应回退到 stock_basic.name。"""
        stock_basic = self._make_stock_basic([
            {"ts_code": "000888.SZ", "name": "*ST运盛", "list_date": "20000101", "industry": "医药"},
        ])

        meta: dict = {}
        _enrich_bar_metadata(
            meta, "000888.SZ", date(2023, 1, 1),
            stock_basic, {}, None,
            namechange_st_index={},
        )
        # 空 dict 是 falsy，应回退
        assert meta.get("is_st") is True

    def test_non_st_stock_not_marked(self):
        """正常股票无论用哪种来源都不应标记 is_st。"""
        stock_basic = self._make_stock_basic([
            {"ts_code": "600519.SH", "name": "贵州茅台", "list_date": "20010827", "industry": "白酒"},
        ])

        meta: dict = {}
        _enrich_bar_metadata(
            meta, "600519.SH", date(2024, 1, 1),
            stock_basic, {}, None,
            namechange_st_index={"000002.SZ": [(date(2023, 6, 2), date(2024, 1, 1))]},
        )
        assert "is_st" not in meta or meta["is_st"] is not True


# ============================================================================
# TuShareEquityProvider.is_st_at_date 单元测试（不需要 API）
# ============================================================================


class TestIsSTAtDate:
    """验证 is_st_at_date 方法的逻辑正确性。"""

    @staticmethod
    def _make_nc_df(records: list[dict]) -> pd.DataFrame:
        df = pd.DataFrame(records)
        for col in ("start_date", "end_date"):
            if col in df.columns:
                df[col] = pd.to_datetime(df[col])
        return df

    def test_st_in_range(self):
        from aipha.data.tushare_equity_provider import TuShareEquityProvider

        nc = self._make_nc_df([
            {"ts_code": "000002.SZ", "name": "万科A",
             "start_date": "20200101", "end_date": "20230601",
             "ann_date": None, "change_reason": None},
            {"ts_code": "000002.SZ", "name": "ST万科",
             "start_date": "20230602", "end_date": "20240101",
             "ann_date": None, "change_reason": None},
        ])
        # 直接调用类方法（不需要实例化）
        provider = object.__new__(TuShareEquityProvider)
        result = provider.is_st_at_date("000002.SZ", date(2023, 8, 15), nc)
        assert result is True

    def test_not_st_before_range(self):
        from aipha.data.tushare_equity_provider import TuShareEquityProvider

        nc = self._make_nc_df([
            {"ts_code": "000002.SZ", "name": "ST万科",
             "start_date": "20230602", "end_date": "20240101",
             "ann_date": None, "change_reason": None},
        ])
        provider = object.__new__(TuShareEquityProvider)
        result = provider.is_st_at_date("000002.SZ", date(2023, 1, 1), nc)
        assert result is False
