"""Tests for SUE (Standardized Unexpected Earnings) computation in PIT pipeline.

验证 _compute_sue_fields 函数正确计算：
- eps_yoy_prev: 去年同期 EPS
- eps_std: 历史 EPS surprise 标准差
- sue: (eps - eps_yoy_prev) / eps_std
- eps_history: 同季度历史 EPS 序列
"""
from __future__ import annotations

import math
import statistics
from datetime import date

import pytest

from aipha.backtest.config_runner import _compute_sue_fields


# ============================================================================
# Helper
# ============================================================================


def _make_record(ann_date_str: str, end_date: str, eps: float, **extra):
    """构造 PIT 记录: (ann_date, data_dict)."""
    d = date.fromisoformat(ann_date_str)
    data = {"eps": eps, "_end_date": end_date}
    data.update(extra)
    return (d, data)


# ============================================================================
# Basic Functionality
# ============================================================================


class TestComputeSueFieldsBasic:
    """基础 SUE 计算功能测试."""

    def test_single_record_no_sue(self):
        """只有 1 条记录 → 无法计算 SUE（无历史对照）."""
        records = [_make_record("2023-04-15", "20230331", 0.5)]
        _compute_sue_fields(records)
        data = records[0][1]
        assert "sue" not in data
        assert "eps_yoy_prev" not in data
        assert data["eps_history"] == [0.5]

    def test_two_same_quarter_records_compute_sue(self):
        """同季度 2 年 → 可计算 eps_yoy_prev，只有 1 个 surprise → 用归一化."""
        records = [
            _make_record("2022-04-20", "20220331", 0.3),
            _make_record("2023-04-18", "20230331", 0.5),
        ]
        _compute_sue_fields(records)

        d0 = records[0][1]
        d1 = records[1][1]

        # 第一条无 prev
        assert "eps_yoy_prev" not in d0

        # 第二条有 prev
        assert d1["eps_yoy_prev"] == 0.3
        # surprise = 0.5 - 0.3 = 0.2，只有 1 个 surprise，用 |prev| 归一化
        expected_sue = 0.2 / 0.3
        assert abs(d1["sue"] - expected_sue) < 1e-9
        assert d1["eps_history"] == [0.3, 0.5]

    def test_three_years_compute_std_sue(self):
        """同季度 3 年 → 2 个 surprise → 可计算真正的 std 和 SUE."""
        records = [
            _make_record("2021-04-20", "20210331", 0.2),
            _make_record("2022-04-18", "20220331", 0.3),
            _make_record("2023-04-15", "20230331", 0.6),
        ]
        _compute_sue_fields(records)

        d2 = records[2][1]
        # surprises = [0.3-0.2, 0.6-0.3] = [0.1, 0.3]
        surprises = [0.1, 0.3]
        expected_std = statistics.stdev(surprises)
        expected_sue = 0.3 / expected_std
        assert abs(d2["eps_std"] - expected_std) < 1e-9
        assert abs(d2["sue"] - expected_sue) < 1e-9
        assert d2["eps_yoy_prev"] == 0.3
        assert d2["eps_history"] == [0.2, 0.3, 0.6]

    def test_multiple_quarters_independent(self):
        """不同季度的 SUE 独立计算."""
        records = [
            _make_record("2022-04-20", "20220331", 0.2),  # Q1-2022
            _make_record("2022-08-25", "20220630", 0.5),  # Q2-2022
            _make_record("2023-04-18", "20230331", 0.4),  # Q1-2023
            _make_record("2023-08-22", "20230630", 0.8),  # Q2-2023
        ]
        _compute_sue_fields(records)

        # Q1: surprise = 0.4 - 0.2 = 0.2, normalized by |0.2| = 1.0
        d_q1_2023 = records[2][1]
        assert d_q1_2023["eps_yoy_prev"] == 0.2
        assert abs(d_q1_2023["sue"] - (0.2 / 0.2)) < 1e-9

        # Q2: surprise = 0.8 - 0.5 = 0.3, normalized by |0.5| = 0.6
        d_q2_2023 = records[3][1]
        assert d_q2_2023["eps_yoy_prev"] == 0.5
        assert abs(d_q2_2023["sue"] - (0.3 / 0.5)) < 1e-9


class TestComputeSueFieldsEdgeCases:
    """SUE 计算边界条件测试."""

    def test_zero_prev_eps(self):
        """去年同期 EPS 为 0 → 特殊归一化."""
        records = [
            _make_record("2022-04-20", "20220331", 0.0),
            _make_record("2023-04-18", "20230331", 0.5),
        ]
        _compute_sue_fields(records)
        d = records[1][1]
        assert d["eps_yoy_prev"] == 0.0
        # |prev| ≈ 0, surprise=0.5, copysign(1.0, 0.5) = 1.0
        assert d["sue"] == 1.0

    def test_negative_surprise(self):
        """EPS 下降 → 负 SUE."""
        records = [
            _make_record("2022-04-20", "20220331", 0.5),
            _make_record("2023-04-18", "20230331", 0.2),
        ]
        _compute_sue_fields(records)
        d = records[1][1]
        # surprise = 0.2 - 0.5 = -0.3, normalized by |0.5| = -0.6
        assert d["sue"] < 0
        assert abs(d["sue"] - (-0.3 / 0.5)) < 1e-9

    def test_identical_eps_across_years(self):
        """EPS 不变 → surprise=0, sue=0."""
        records = [
            _make_record("2021-04-20", "20210331", 0.5),
            _make_record("2022-04-18", "20220331", 0.5),
            _make_record("2023-04-15", "20230331", 0.5),
        ]
        _compute_sue_fields(records)
        d2 = records[2][1]
        # surprises = [0.0, 0.0], std ≈ 0
        # copysign(3.0, 0.0) 但|surprise|=0 → sue=0.0
        assert d2["sue"] == 0.0

    def test_missing_eps_field(self):
        """某条记录无 eps → 跳过."""
        records = [
            (date(2022, 4, 20), {"_end_date": "20220331", "roe": 10.0}),
            _make_record("2023-04-18", "20230331", 0.5),
        ]
        _compute_sue_fields(records)
        d1 = records[1][1]
        # 只有 1 条有 eps → 无 prev
        assert "eps_yoy_prev" not in d1

    def test_missing_end_date(self):
        """某条记录无 _end_date → 跳过 SUE 计算."""
        records = [
            (date(2022, 4, 20), {"eps": 0.3}),
            _make_record("2023-04-18", "20230331", 0.5),
        ]
        _compute_sue_fields(records)
        d1 = records[1][1]
        # 第二条所在季度只有自己，无 prev
        assert "eps_yoy_prev" not in d1

    def test_empty_records(self):
        """空列表 → 不报错."""
        records = []
        _compute_sue_fields(records)
        assert records == []

    def test_negative_eps_values(self):
        """负 EPS（亏损）→ SUE 仍可计算."""
        records = [
            _make_record("2022-04-20", "20220331", -0.3),
            _make_record("2023-04-18", "20230331", -0.1),
        ]
        _compute_sue_fields(records)
        d = records[1][1]
        # surprise = -0.1 - (-0.3) = 0.2 (改善)
        # normalized by |-0.3| = 0.2/0.3 ≈ 0.667
        assert d["sue"] > 0
        assert abs(d["sue"] - (0.2 / 0.3)) < 1e-9

    def test_large_eps_change(self):
        """大幅盈利变动."""
        records = [
            _make_record("2022-04-20", "20220331", 0.01),
            _make_record("2023-04-18", "20230331", 1.00),
        ]
        _compute_sue_fields(records)
        d = records[1][1]
        # surprise = 0.99, normalized by |0.01| = 99.0
        assert abs(d["sue"] - 99.0) < 1e-6


class TestComputeSueFieldsHistory:
    """eps_history 字段测试."""

    def test_history_limited_to_8(self):
        """eps_history 最多保留最近 8 个季度."""
        records = [
            _make_record(f"20{14 + i}-04-20", f"20{14 + i}0331", 0.1 * (i + 1))
            for i in range(10)  # 2014-2023, 10 年
        ]
        _compute_sue_fields(records)
        # 最后一条应该有 8 个历史值
        d_last = records[-1][1]
        assert len(d_last["eps_history"]) == 8

    def test_history_ascending_order(self):
        """eps_history 按年份升序排列."""
        records = [
            _make_record("2020-04-20", "20200331", 0.2),
            _make_record("2021-04-20", "20210331", 0.3),
            _make_record("2022-04-20", "20220331", 0.4),
            _make_record("2023-04-20", "20230331", 0.5),
        ]
        _compute_sue_fields(records)
        d = records[3][1]
        assert d["eps_history"] == [0.2, 0.3, 0.4, 0.5]


class TestComputeSueFieldsCleanup:
    """验证 _end_date 字段行为 — 当前实现保留 _end_date 供策略层使用."""

    def test_end_date_preserved(self):
        """_end_date 字段应被保留（策略层需要用它判断报告期以做 ROE 年化）."""
        records = [
            _make_record("2023-04-15", "20230331", 0.5),
        ]
        _compute_sue_fields(records)
        assert "_end_date" in records[0][1]

    def test_other_fields_preserved(self):
        """其他字段（roe 等）应被保留."""
        records = [
            _make_record("2023-04-15", "20230331", 0.5, roe=15.0),
        ]
        _compute_sue_fields(records)
        assert records[0][1]["roe"] == 15.0
        assert records[0][1]["eps"] == 0.5


class TestComputeSueFieldsMultiYear:
    """多年数据的 SUE 滚动计算测试."""

    def test_four_years_rolling_sue(self):
        """4 年数据 → 验证滚动 surprise 累积正确."""
        records = [
            _make_record("2020-04-20", "20200331", 0.10),
            _make_record("2021-04-20", "20210331", 0.15),
            _make_record("2022-04-20", "20220331", 0.25),
            _make_record("2023-04-20", "20230331", 0.20),
        ]
        _compute_sue_fields(records)

        # Year 2 (2021): surprise1 = 0.15-0.10 = 0.05, 1 surprise, sue = 0.05/|0.10| = 0.5
        d1 = records[1][1]
        assert abs(d1["sue"] - 0.5) < 1e-9

        # Year 3 (2022): surprise2 = 0.25-0.15 = 0.10
        # surprises = [0.05, 0.10], std
        d2 = records[2][1]
        surprises_2 = [0.05, 0.10]
        expected_std_2 = statistics.stdev(surprises_2)
        assert abs(d2["sue"] - (0.10 / expected_std_2)) < 1e-9

        # Year 4 (2023): surprise3 = 0.20-0.25 = -0.05
        # surprises = [0.05, 0.10, -0.05], std
        d3 = records[3][1]
        surprises_3 = [0.05, 0.10, -0.05]
        expected_std_3 = statistics.stdev(surprises_3)
        assert abs(d3["sue"] - (-0.05 / expected_std_3)) < 1e-9
        assert d3["sue"] < 0  # 盈利下滑 → 负 SUE
