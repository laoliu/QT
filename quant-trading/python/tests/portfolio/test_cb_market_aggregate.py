"""Tests for CBMarketAggregator — Phase 2 CB 市场聚合特征。"""

from __future__ import annotations

import pytest
from aipha.portfolio.cb_market_aggregate import CBMarketAggregator, CBMarketSnapshot


class TestCBMarketSnapshot:
    """CBMarketSnapshot 数据结构测试。"""

    def test_minimal_values(self):
        snap = CBMarketSnapshot(
            date="20230101", n_active=0, median_close=0.0,
            mean_close=0.0, median_premium=0.0, total_amount=0.0,
            advance_ratio=0.0, index_value=1000.0,
        )
        assert snap.n_active == 0
        assert snap.index_value == 1000.0
        assert snap.median_premium == 0.0

    def test_custom_values(self):
        snap = CBMarketSnapshot(
            date="20230601",
            n_active=500,
            median_close=110.5,
            mean_close=115.2,
            median_premium=35.0,
            total_amount=1e8,
            advance_ratio=0.55,
            index_value=850.0,
        )
        assert snap.n_active == 500
        assert snap.median_premium == 35.0
        assert snap.index_value == 850.0

    def test_snapshot_repr(self):
        snap = CBMarketSnapshot(
            date="20230101", n_active=100, median_close=105.0,
            mean_close=110.0, median_premium=30.0, total_amount=5e7,
            advance_ratio=0.5, index_value=950.0,
        )
        assert "20230101" in repr(snap)


class TestCBMarketAggregator:
    """CBMarketAggregator 单元测试（不依赖实际缓存文件）。"""

    def test_init(self):
        agg = CBMarketAggregator()
        assert len(agg._snapshots) == 0

    def test_get_series_empty(self):
        agg = CBMarketAggregator()
        series = agg.get_series()
        assert "index_value" in series
        assert len(series["index_value"]) == 0

    def test_get_aligned_arrays_empty(self):
        agg = CBMarketAggregator()
        dates = ["20230101", "20230102"]
        arrays = agg.get_aligned_arrays(dates)
        assert "index_value" in arrays
        assert len(arrays["index_value"]) == 2

    def test_manual_snapshot_injection(self):
        """手动注入 snapshot 测试 get_series/get_aligned_arrays。"""
        agg = CBMarketAggregator()
        agg._snapshots["20230101"] = CBMarketSnapshot(
            date="20230101", n_active=100, median_close=105.0,
            mean_close=110.0, median_premium=30.0,
            total_amount=5e7, advance_ratio=0.6, index_value=900.0,
        )
        agg._snapshots["20230103"] = CBMarketSnapshot(
            date="20230103", n_active=105, median_close=106.0,
            mean_close=111.0, median_premium=32.0,
            total_amount=6e7, advance_ratio=0.55, index_value=905.0,
        )
        series = agg.get_series()
        assert len(series["index_value"]) == 2
        assert series["index_value"]["20230101"] == 900.0
        assert series["median_premium"]["20230103"] == 32.0

    def test_get_aligned_arrays_forward_fill(self):
        """验证 forward-fill 逻辑。"""
        agg = CBMarketAggregator()
        agg._snapshots["20230101"] = CBMarketSnapshot(
            date="20230101", n_active=100, median_close=105.0,
            mean_close=110.0, median_premium=30.0,
            total_amount=5e7, advance_ratio=0.5, index_value=900.0,
        )
        agg._snapshots["20230103"] = CBMarketSnapshot(
            date="20230103", n_active=102, median_close=106.0,
            mean_close=111.0, median_premium=31.0,
            total_amount=5.5e7, advance_ratio=0.52, index_value=910.0,
        )
        # 请求 01/02/03, 02 应该 forward-fill 01 的值
        arrays = agg.get_aligned_arrays(["20230101", "20230102", "20230103"])
        assert arrays["index_value"][0] == 900.0
        assert arrays["index_value"][1] == 900.0  # forward-fill
        assert arrays["index_value"][2] == 910.0

    def test_snapshot_fields_complete(self):
        snap = CBMarketSnapshot(
            date="20230601", n_active=500, median_close=110.5,
            mean_close=115.2, median_premium=35.0, total_amount=1e8,
            advance_ratio=0.55, index_value=850.0,
        )
        # 验证所有字段可访问
        assert snap.date == "20230601"
        assert snap.n_active == 500
        assert snap.median_close == 110.5
        assert snap.mean_close == 115.2
        assert snap.advance_ratio == 0.55


class TestCBMarketAggregatorSeries:
    """get_series 返回结构测试。"""

    def test_series_keys(self):
        agg = CBMarketAggregator()
        series = agg.get_series()
        expected_keys = {"index_value", "median_premium", "total_amount",
                         "advance_ratio", "n_active"}
        assert set(series.keys()) == expected_keys

    def test_series_sorted(self):
        agg = CBMarketAggregator()
        agg._snapshots["20230103"] = CBMarketSnapshot(
            date="20230103", n_active=100, median_close=106.0,
            mean_close=111.0, median_premium=30.0,
            total_amount=5e7, advance_ratio=0.5, index_value=910,
        )
        agg._snapshots["20230101"] = CBMarketSnapshot(
            date="20230101", n_active=100, median_close=105.0,
            mean_close=110.0, median_premium=30.0,
            total_amount=5e7, advance_ratio=0.5, index_value=900,
        )
        series = agg.get_series()
        dates = list(series["index_value"].keys())
        assert dates == sorted(dates)  # 按日期排序
