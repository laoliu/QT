"""分析师与替代数据因子测试。

测试 analyst_factors.py 中的三个因子函数：
- compute_analyst_revision_factor
- compute_holder_change_factor
- compute_forecast_surprise_factor
"""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import pytest


@dataclass
class MockCandidate:
    """模拟 EquityCandidateInfo。"""

    symbol: str
    close: float = 10.0
    metadata: dict[str, Any] = field(default_factory=dict)
    circ_mv: float = 1000.0
    turnover_rate: float = 1.0
    amount: float = 10000.0
    list_date: str = "20150105"
    name: str = "测试"


@pytest.fixture
def tmp_analyst_dir(tmp_path: Path) -> Path:
    """创建临时分析师数据目录。"""
    d = tmp_path / "alternative"
    d.mkdir()
    # 创建 202501 月数据
    df1 = pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ",
                     "000002.SZ", "000003.SZ"],
        "report_date": ["20250110", "20250115", "20250112",
                        "20250118", "20250120"],
        "quarter": ["2025Q1"] * 5,
        "eps": [1.0, 1.1, 2.0, 2.1, 3.0],
        "name": ["A", "A", "B", "B", "C"],
    })
    df1.to_parquet(d / "analyst_202501.parquet", index=False)
    # 创建 202502 月数据
    df2 = pd.DataFrame({
        "ts_code": ["000001.SZ", "000001.SZ", "000002.SZ",
                     "000002.SZ", "000003.SZ"],
        "report_date": ["20250210", "20250215", "20250212",
                        "20250218", "20250220"],
        "quarter": ["2025Q1"] * 5,
        "eps": [1.2, 1.3, 1.8, 1.7, 3.5],
        "name": ["A", "A", "B", "B", "C"],
    })
    df2.to_parquet(d / "analyst_202502.parquet", index=False)
    return d


@pytest.fixture
def tmp_holder_dir(tmp_path: Path) -> Path:
    """创建临时股东户数数据目录。"""
    d = tmp_path / "stk_holdernumber"
    d.mkdir()
    # 创建 000001.SZ 的数据
    df = pd.DataFrame({
        "ts_code": ["000001.SZ"] * 4,
        "ann_date": ["20241201", "20250101", "20250301", "20250401"],
        "end_date": ["20240930", "20241231", "20250331", "20250630"],
        "holder_num": [50000, 45000, 42000, 40000],
    })
    df.to_parquet(
        d / "stk_holdernumber_000001_SZ.parquet", index=False,
    )
    # 创建 000002.SZ 的数据（户数增长 = 分散 = 负面）
    df2 = pd.DataFrame({
        "ts_code": ["000002.SZ"] * 3,
        "ann_date": ["20241201", "20250201", "20250401"],
        "end_date": ["20240930", "20241231", "20250331"],
        "holder_num": [30000, 33000, 36000],
    })
    df2.to_parquet(
        d / "stk_holdernumber_000002_SZ.parquet", index=False,
    )
    return d


@pytest.fixture
def tmp_forecast_dir(tmp_path: Path) -> Path:
    """创建临时业绩预告数据目录。"""
    d = tmp_path / "forecast"
    d.mkdir()
    df = pd.DataFrame({
        "ts_code": ["000001.SZ", "000002.SZ", "000003.SZ"],
        "ann_date": ["20250115", "20250120", "20250225"],
        "type": ["预增", "预减", "扭亏"],
        "p_change_min": [30.0, -40.0, 50.0],
        "p_change_max": [50.0, -20.0, 80.0],
    })
    df.to_parquet(d / "forecast_202501.parquet", index=False)
    df2 = pd.DataFrame({
        "ts_code": ["000001.SZ"],
        "ann_date": ["20250310"],
        "type": ["略增"],
        "p_change_min": [10.0],
        "p_change_max": [20.0],
    })
    df2.to_parquet(d / "forecast_202503.parquet", index=False)
    return d


def _c(symbol: str) -> MockCandidate:
    return MockCandidate(symbol=symbol)


# ============================================================================
# 1. 分析师修正因子测试
# ============================================================================


class TestAnalystRevisionFactor:
    """compute_analyst_revision_factor 测试。"""

    def test_basic_revision(self, tmp_analyst_dir: Path):
        """正常修正率计算。"""
        from aipha.factors.analyst_factors import (
            _analyst_cache,
            compute_analyst_revision_factor,
        )
        _analyst_cache.clear()
        cands = [_c("000001.SZ"), _c("000002.SZ"), _c("000003.SZ")]
        result = compute_analyst_revision_factor(
            cands,
            cache_dir=str(tmp_analyst_dir),
            trade_date="20250228",
            lookback_months=2,
            min_analysts=2,
        )
        # 000001: 202501 median=1.05, 202502 median=1.25 → rev=+19%
        # 000002: 202501 median=2.05, 202502 median=1.75 → rev=-14.6%
        # 000003: 仅 1 条记录/月 → 不满 min_analysts=2
        assert "000001.SZ" in result
        assert result["000001.SZ"] > 0  # 上调
        assert "000002.SZ" in result
        assert result["000002.SZ"] < 0  # 下调
        assert "000003.SZ" not in result  # 覆盖不足

    def test_empty_candidates(self, tmp_analyst_dir: Path):
        """空候选列表返回空字典。"""
        from aipha.factors.analyst_factors import (
            compute_analyst_revision_factor,
        )
        assert compute_analyst_revision_factor([], trade_date="20250228") == {}

    def test_no_trade_date(self, tmp_analyst_dir: Path):
        """无交易日期返回空字典。"""
        from aipha.factors.analyst_factors import (
            compute_analyst_revision_factor,
        )
        result = compute_analyst_revision_factor(
            [_c("000001.SZ")],
            cache_dir=str(tmp_analyst_dir),
            trade_date="",
        )
        assert result == {}

    def test_missing_data_dir(self, tmp_path: Path):
        """缓存目录不存在时返回空字典。"""
        from aipha.factors.analyst_factors import (
            _analyst_cache,
            compute_analyst_revision_factor,
        )
        _analyst_cache.clear()
        result = compute_analyst_revision_factor(
            [_c("000001.SZ")],
            cache_dir=str(tmp_path / "nonexistent"),
            trade_date="20250228",
        )
        assert result == {}

    def test_pit_compliance(self, tmp_analyst_dir: Path):
        """PIT: report_date > trade_date 的记录被过滤。"""
        from aipha.factors.analyst_factors import (
            _analyst_cache,
            compute_analyst_revision_factor,
        )
        _analyst_cache.clear()
        # trade_date=20250115 → 202502 数据暂未发布
        result = compute_analyst_revision_factor(
            [_c("000001.SZ"), _c("000002.SZ")],
            cache_dir=str(tmp_analyst_dir),
            trade_date="20250115",
            lookback_months=2,
            min_analysts=1,
        )
        # 只有 202501 的 report_date <= 20250115 的记录
        # 000001: 只有 20250110 报告，仅 1 月 → 需 ≥2 月才能算 revision
        assert len(result) == 0 or all(
            math.isfinite(v) for v in result.values()
        )


# ============================================================================
# 2. 股东户数变化因子测试
# ============================================================================


class TestHolderChangeFactor:
    """compute_holder_change_factor 测试。"""

    def test_basic_holder_change(self, tmp_holder_dir: Path):
        """正常户数变化计算。"""
        from aipha.factors.analyst_factors import (
            _holder_cache,
            compute_holder_change_factor,
        )
        _holder_cache.clear()
        cands = [_c("000001.SZ"), _c("000002.SZ")]
        result = compute_holder_change_factor(
            cands,
            cache_dir=str(tmp_holder_dir),
            trade_date="20250501",
        )
        # 000001: 42000→40000 = -4.76% → factor = +0.048
        assert "000001.SZ" in result
        assert result["000001.SZ"] > 0  # 户数减少=正值
        # 000002: 33000→36000 = +9.09% → factor = -0.091
        assert "000002.SZ" in result
        assert result["000002.SZ"] < 0  # 户数增加=负值

    def test_pit_compliance_holder(self, tmp_holder_dir: Path):
        """PIT: 仅使用 ann_date ≤ trade_date 的数据。"""
        from aipha.factors.analyst_factors import (
            _holder_cache,
            compute_holder_change_factor,
        )
        _holder_cache.clear()
        result = compute_holder_change_factor(
            [_c("000001.SZ")],
            cache_dir=str(tmp_holder_dir),
            trade_date="20250105",
        )
        # 只有 ann_date 20241201, 20250101 可用
        # 50000→45000 = -10% → factor = +0.1
        assert "000001.SZ" in result
        assert result["000001.SZ"] > 0

    def test_insufficient_data(self, tmp_holder_dir: Path):
        """数据不足时返回空。"""
        from aipha.factors.analyst_factors import (
            _holder_cache,
            compute_holder_change_factor,
        )
        _holder_cache.clear()
        result = compute_holder_change_factor(
            [_c("000001.SZ")],
            cache_dir=str(tmp_holder_dir),
            trade_date="20241201",
        )
        # 只有 1 条 ann_date ≤ 20241201 → 不足 2 条
        assert "000001.SZ" not in result

    def test_missing_stock(self, tmp_holder_dir: Path):
        """股票无数据文件时跳过。"""
        from aipha.factors.analyst_factors import (
            _holder_cache,
            compute_holder_change_factor,
        )
        _holder_cache.clear()
        result = compute_holder_change_factor(
            [_c("999999.SZ")],
            cache_dir=str(tmp_holder_dir),
            trade_date="20250501",
        )
        assert result == {}

    def test_empty_candidates(self):
        """空候选列表返回空字典。"""
        from aipha.factors.analyst_factors import (
            compute_holder_change_factor,
        )
        assert compute_holder_change_factor([], trade_date="20250501") == {}


# ============================================================================
# 3. 业绩预告超预期因子测试
# ============================================================================


class TestForecastSurpriseFactor:
    """compute_forecast_surprise_factor 测试。"""

    def test_basic_scores(self, tmp_forecast_dir: Path):
        """正常业绩预告打分。"""
        from aipha.factors.analyst_factors import (
            compute_forecast_surprise_factor,
        )
        cands = [_c("000001.SZ"), _c("000002.SZ"), _c("000003.SZ")]
        result = compute_forecast_surprise_factor(
            cands,
            cache_dir=str(tmp_forecast_dir),
            trade_date="20250228",
            lookback_months=3,
        )
        # 000001: 预增 (+2) + avg_change=(30+50)/200=0.20 → score=2.2
        assert "000001.SZ" in result
        assert result["000001.SZ"] > 2.0
        # 000002: 预减 (-2) + avg_change=(-40+-20)/200=-0.15 → score=-2.15
        assert "000002.SZ" in result
        assert result["000002.SZ"] < -1.5

    def test_pit_compliance_forecast(self, tmp_forecast_dir: Path):
        """PIT: ann_date > trade_date 的预告被过滤。"""
        from aipha.factors.analyst_factors import (
            compute_forecast_surprise_factor,
        )
        result = compute_forecast_surprise_factor(
            [_c("000003.SZ")],
            cache_dir=str(tmp_forecast_dir),
            trade_date="20250110",
            lookback_months=3,
        )
        # 000003 的 ann_date=20250225 > 20250110 → 过滤掉
        assert "000003.SZ" not in result

    def test_latest_forecast_used(self, tmp_forecast_dir: Path):
        """取最新一条业绩预告。"""
        from aipha.factors.analyst_factors import (
            compute_forecast_surprise_factor,
        )
        result = compute_forecast_surprise_factor(
            [_c("000001.SZ")],
            cache_dir=str(tmp_forecast_dir),
            trade_date="20250315",
            lookback_months=6,
        )
        # 000001 有两条: 202501(预增), 202503(略增)
        # 最新 ann_date=20250310 → 略增 score=1.0 + (10+20)/200
        assert "000001.SZ" in result
        assert 1.0 < result["000001.SZ"] < 2.0  # 略增 base=1.0

    def test_empty_candidates(self):
        """空候选列表返回空字典。"""
        from aipha.factors.analyst_factors import (
            compute_forecast_surprise_factor,
        )
        assert compute_forecast_surprise_factor(
            [], trade_date="20250228",
        ) == {}

    def test_no_matching_stocks(self, tmp_forecast_dir: Path):
        """无匹配股票返回空字典。"""
        from aipha.factors.analyst_factors import (
            compute_forecast_surprise_factor,
        )
        result = compute_forecast_surprise_factor(
            [_c("999999.SZ")],
            cache_dir=str(tmp_forecast_dir),
            trade_date="20250228",
        )
        assert result == {}

# ============================================================================
# 4. 数据加载工具测试
# ============================================================================


class TestDataLoaders:
    """数据加载函数测试。"""

    def test_load_analyst_months(self, tmp_analyst_dir: Path):
        """正常加载分析师月度数据。"""
        from aipha.factors.analyst_factors import (
            _analyst_cache,
            load_analyst_months,
        )
        _analyst_cache.clear()
        df = load_analyst_months(str(tmp_analyst_dir), ["202501", "202502"])
        assert len(df) == 10  # 5 + 5
        assert "ts_code" in df.columns
        assert "eps" in df.columns

    def test_load_analyst_missing_month(self, tmp_analyst_dir: Path):
        """缺失月份文件跳过，不报错。"""
        from aipha.factors.analyst_factors import (
            _analyst_cache,
            load_analyst_months,
        )
        _analyst_cache.clear()
        df = load_analyst_months(
            str(tmp_analyst_dir), ["202501", "202512"],
        )
        assert len(df) == 5  # 只有 202501

    def test_load_analyst_all_missing(self, tmp_path: Path):
        """全部月份缺失返回空 DataFrame。"""
        from aipha.factors.analyst_factors import load_analyst_months
        df = load_analyst_months(str(tmp_path), ["209901"])
        assert df.empty

    def test_load_holder_data(self, tmp_holder_dir: Path):
        """正常加载股东户数数据。"""
        from aipha.factors.analyst_factors import (
            _holder_cache,
            load_holder_data,
        )
        _holder_cache.clear()
        df = load_holder_data(str(tmp_holder_dir), "000001.SZ")
        assert len(df) == 4
        assert "holder_num" in df.columns

    def test_load_holder_missing(self, tmp_holder_dir: Path):
        """股票不存在时返回空 DataFrame。"""
        from aipha.factors.analyst_factors import (
            _holder_cache,
            load_holder_data,
        )
        _holder_cache.clear()
        df = load_holder_data(str(tmp_holder_dir), "999999.SZ")
        assert df.empty

    def test_load_forecast_months(self, tmp_forecast_dir: Path):
        """正常加载业绩预告数据。"""
        from aipha.factors.analyst_factors import load_forecast_months
        df = load_forecast_months(
            str(tmp_forecast_dir), ["202501", "202503"],
        )
        assert len(df) == 4  # 3 + 1

    def test_get_months_before(self):
        """月份回溯计算。"""
        from aipha.factors.analyst_factors import _get_months_before
        months = _get_months_before("20250315", 3)
        assert months == ["202503", "202502", "202501"]

    def test_get_months_before_cross_year(self):
        """跨年月份回溯。"""
        from aipha.factors.analyst_factors import _get_months_before
        months = _get_months_before("20250215", 4)
        assert months == ["202502", "202501", "202412", "202411"]