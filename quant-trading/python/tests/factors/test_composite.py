"""
复合因子和因子标准化测试
"""

from datetime import date
import math

import pytest

from aipha.factors.base import FactorResult
from aipha.factors.composite import (
    ZScoreNormalizer,
    RankNormalizer,
    MinMaxNormalizer,
    WinsorizeNormalizer,
    MADNormalizer,
    CompositeFactor,
    get_normalizer,
)


# ============================================================================
# ZScoreNormalizer Tests
# ============================================================================


class TestZScoreNormalizer:
    """Z-Score 标准化测试"""

    def test_basic_normalization(self):
        """基本标准化"""
        normalizer = ZScoreNormalizer(clip=False)
        results = [
            FactorResult("A", date(2025, 1, 6), "F", 10.0),
            FactorResult("B", date(2025, 1, 6), "F", 20.0),
            FactorResult("C", date(2025, 1, 6), "F", 30.0),
        ]

        normalized = normalizer.normalize(results)

        assert len(normalized) == 3
        # 均值 = 20, 标准差 ≈ 10
        assert normalized[0].z_score == pytest.approx(-1.0)
        assert normalized[1].z_score == pytest.approx(0.0)
        assert normalized[2].z_score == pytest.approx(1.0)

    def test_clipping(self):
        """极端值裁剪"""
        normalizer = ZScoreNormalizer(clip=True, clip_range=(-1.0, 1.0))
        results = [
            FactorResult("A", date(2025, 1, 6), "F", 10.0),
            FactorResult("B", date(2025, 1, 6), "F", 20.0),
            FactorResult("C", date(2025, 1, 6), "F", 30.0),
        ]

        normalized = normalizer.normalize(results)

        # 均值=20, 标准差=10, z-scores = [-1, 0, 1]
        # A 的 z-score = -1.0 应该被裁剪到 -1.0
        # C 的 z-score = 1.0 应该被裁剪到 1.0
        assert normalized[0].z_score == pytest.approx(-1.0)
        assert normalized[1].z_score == pytest.approx(0.0)
        assert normalized[2].z_score == pytest.approx(1.0)

    def test_handle_nan_drop(self):
        """NaN 处理 - drop"""
        normalizer = ZScoreNormalizer(handle_nan="drop")
        results = [
            FactorResult("A", date(2025, 1, 6), "F", 10.0),
            FactorResult("B", date(2025, 1, 6), "F", float("nan")),
            FactorResult("C", date(2025, 1, 6), "F", 30.0),
        ]

        normalized = normalizer.normalize(results)

        assert not math.isnan(normalized[0].z_score)
        assert math.isnan(normalized[1].z_score)
        assert not math.isnan(normalized[2].z_score)

    def test_handle_nan_mean(self):
        """NaN 处理 - mean"""
        normalizer = ZScoreNormalizer(handle_nan="mean", clip=False)
        results = [
            FactorResult("A", date(2025, 1, 6), "F", 10.0),
            FactorResult("B", date(2025, 1, 6), "F", float("nan")),
            FactorResult("C", date(2025, 1, 6), "F", 30.0),
        ]

        normalized = normalizer.normalize(results)

        # NaN 应该被替换为 0（均值的 z-score）
        assert normalized[1].z_score == pytest.approx(0.0)

    def test_empty_results(self):
        """空结果列表"""
        normalizer = ZScoreNormalizer()
        normalized = normalizer.normalize([])
        assert normalized == []

    def test_single_value(self):
        """单个值"""
        normalizer = ZScoreNormalizer()
        results = [FactorResult("A", date(2025, 1, 6), "F", 10.0)]

        normalized = normalizer.normalize(results)

        # 无法计算标准差
        assert math.isnan(normalized[0].z_score)

    def test_preserves_original_value(self):
        """保留原始值"""
        normalizer = ZScoreNormalizer()
        results = [
            FactorResult("A", date(2025, 1, 6), "F", 10.0),
            FactorResult("B", date(2025, 1, 6), "F", 20.0),
        ]

        normalized = normalizer.normalize(results)

        assert normalized[0].value == 10.0
        assert normalized[1].value == 20.0


# ============================================================================
# RankNormalizer Tests
# ============================================================================


class TestRankNormalizer:
    """排名标准化测试"""

    def test_basic_ranking(self):
        """基本排名"""
        normalizer = RankNormalizer(ascending=True)
        results = [
            FactorResult("A", date(2025, 1, 6), "F", 30.0),
            FactorResult("B", date(2025, 1, 6), "F", 10.0),
            FactorResult("C", date(2025, 1, 6), "F", 20.0),
        ]

        normalized = normalizer.normalize(results)

        # B(10) < C(20) < A(30)
        # ascending=True: 最小值排名靠前
        assert normalized[1].rank < normalized[2].rank < normalized[0].rank

    def test_descending_ranking(self):
        """降序排名"""
        normalizer = RankNormalizer(ascending=False)
        results = [
            FactorResult("A", date(2025, 1, 6), "F", 30.0),
            FactorResult("B", date(2025, 1, 6), "F", 10.0),
            FactorResult("C", date(2025, 1, 6), "F", 20.0),
        ]

        normalized = normalizer.normalize(results)

        # ascending=False: 最大值排名靠前
        assert normalized[0].rank < normalized[2].rank < normalized[1].rank

    def test_rank_range(self):
        """排名范围 0-1"""
        normalizer = RankNormalizer()
        results = [
            FactorResult("A", date(2025, 1, 6), "F", 10.0),
            FactorResult("B", date(2025, 1, 6), "F", 20.0),
            FactorResult("C", date(2025, 1, 6), "F", 30.0),
        ]

        normalized = normalizer.normalize(results)

        for r in normalized:
            assert 0.0 <= r.rank <= 1.0

    def test_handle_nan_worst(self):
        """NaN 处理 - worst"""
        normalizer = RankNormalizer(ascending=True, handle_nan="worst")
        results = [
            FactorResult("A", date(2025, 1, 6), "F", 10.0),
            FactorResult("B", date(2025, 1, 6), "F", float("nan")),
            FactorResult("C", date(2025, 1, 6), "F", 30.0),
        ]

        normalized = normalizer.normalize(results)

        # NaN 应该排名最差（ascending=True 时为 1.0）
        assert normalized[1].rank == 1.0

    def test_handle_nan_middle(self):
        """NaN 处理 - middle"""
        normalizer = RankNormalizer(handle_nan="middle")
        results = [
            FactorResult("A", date(2025, 1, 6), "F", 10.0),
            FactorResult("B", date(2025, 1, 6), "F", float("nan")),
        ]

        normalized = normalizer.normalize(results)

        assert normalized[1].rank == 0.5

    def test_empty_results(self):
        """空结果列表"""
        normalizer = RankNormalizer()
        normalized = normalizer.normalize([])
        assert normalized == []


# ============================================================================
# MinMaxNormalizer Tests
# ============================================================================


class TestMinMaxNormalizer:
    """MinMax 标准化测试"""

    def test_basic_normalization(self):
        """基本标准化"""
        normalizer = MinMaxNormalizer()
        results = [
            FactorResult("A", date(2025, 1, 6), "F", 10.0),
            FactorResult("B", date(2025, 1, 6), "F", 20.0),
            FactorResult("C", date(2025, 1, 6), "F", 30.0),
        ]

        normalized = normalizer.normalize(results)

        assert normalized[0].value == pytest.approx(0.0)
        assert normalized[1].value == pytest.approx(0.5)
        assert normalized[2].value == pytest.approx(1.0)

    def test_custom_range(self):
        """自定义范围"""
        normalizer = MinMaxNormalizer(feature_range=(-1.0, 1.0))
        results = [
            FactorResult("A", date(2025, 1, 6), "F", 0.0),
            FactorResult("B", date(2025, 1, 6), "F", 100.0),
        ]

        normalized = normalizer.normalize(results)

        assert normalized[0].value == pytest.approx(-1.0)
        assert normalized[1].value == pytest.approx(1.0)

    def test_constant_values(self):
        """所有值相同"""
        normalizer = MinMaxNormalizer()
        results = [
            FactorResult("A", date(2025, 1, 6), "F", 10.0),
            FactorResult("B", date(2025, 1, 6), "F", 10.0),
        ]

        normalized = normalizer.normalize(results)

        # 应该返回中间值
        assert normalized[0].value == pytest.approx(0.5)
        assert normalized[1].value == pytest.approx(0.5)

    def test_preserves_original_in_metadata(self):
        """保留原始值在元数据中"""
        normalizer = MinMaxNormalizer()
        results = [
            FactorResult("A", date(2025, 1, 6), "F", 10.0),
            FactorResult("B", date(2025, 1, 6), "F", 20.0),
        ]

        normalized = normalizer.normalize(results)

        assert normalized[0].metadata["original_value"] == 10.0

    def test_empty_results(self):
        """空结果列表"""
        normalizer = MinMaxNormalizer()
        normalized = normalizer.normalize([])
        assert normalized == []


# ============================================================================
# CompositeFactor Tests
# ============================================================================


class TestCompositeFactor:
    """复合因子测试"""

    def test_basic_combination(self):
        """基本因子组合"""
        composite = CompositeFactor(
            weights={"F1": 0.5, "F2": 0.5},
            normalize_method="none",
            name="COMBO",
        )

        factor_results = {
            "F1": [
                FactorResult("A", date(2025, 1, 6), "F1", 1.0),
                FactorResult("B", date(2025, 1, 6), "F1", 2.0),
            ],
            "F2": [
                FactorResult("A", date(2025, 1, 6), "F2", 3.0),
                FactorResult("B", date(2025, 1, 6), "F2", 4.0),
            ],
        }

        combined = composite.combine(factor_results)

        assert len(combined) == 2
        # A: (0.5 * 1.0 + 0.5 * 3.0) / 1.0 = 2.0
        # B: (0.5 * 2.0 + 0.5 * 4.0) / 1.0 = 3.0
        result_a = next(r for r in combined if r.symbol == "A")
        result_b = next(r for r in combined if r.symbol == "B")
        assert result_a.value == pytest.approx(2.0)
        assert result_b.value == pytest.approx(3.0)

    def test_weight_normalization(self):
        """权重归一化"""
        composite = CompositeFactor(
            weights={"F1": 2.0, "F2": 2.0},
            weight_normalize=True,
            normalize_method="none",
        )

        # 权重应该被归一化为 0.5, 0.5
        assert composite.weights["F1"] == pytest.approx(0.5)
        assert composite.weights["F2"] == pytest.approx(0.5)

    def test_with_zscore_normalization(self):
        """使用 Z-Score 标准化"""
        composite = CompositeFactor(
            weights={"F1": 1.0},
            normalize_method="z_score",
            name="COMBO",
        )

        factor_results = {
            "F1": [
                FactorResult("A", date(2025, 1, 6), "F1", 10.0),
                FactorResult("B", date(2025, 1, 6), "F1", 20.0),
                FactorResult("C", date(2025, 1, 6), "F1", 30.0),
            ],
        }

        combined = composite.combine(factor_results)

        # 应该使用 z-score
        result_a = next(r for r in combined if r.symbol == "A")
        result_b = next(r for r in combined if r.symbol == "B")
        result_c = next(r for r in combined if r.symbol == "C")

        assert result_a.value == pytest.approx(-1.0, abs=0.1)
        assert result_b.value == pytest.approx(0.0, abs=0.1)
        assert result_c.value == pytest.approx(1.0, abs=0.1)

    def test_missing_factor(self):
        """处理缺失因子"""
        composite = CompositeFactor(
            weights={"F1": 0.5, "F2": 0.5},
            normalize_method="none",
        )

        # 只有 F1，没有 F2
        factor_results = {
            "F1": [
                FactorResult("A", date(2025, 1, 6), "F1", 10.0),
            ],
        }

        combined = composite.combine(factor_results)

        # 应该只使用 F1
        assert len(combined) == 1
        assert combined[0].value == pytest.approx(10.0)

    def test_empty_results(self):
        """空结果"""
        composite = CompositeFactor(weights={"F1": 1.0})
        combined = composite.combine({})
        assert combined == []

    def test_composite_metadata(self):
        """复合因子元数据"""
        composite = CompositeFactor(
            weights={"F1": 1.0},
            normalize_method="none",
            name="MY_COMBO",
        )

        factor_results = {
            "F1": [FactorResult("A", date(2025, 1, 6), "F1", 10.0)],
        }

        combined = composite.combine(factor_results)

        assert combined[0].factor_name == "MY_COMBO"
        assert "component_factors" in combined[0].metadata
        assert "weights" in combined[0].metadata


# ============================================================================
# get_normalizer 工厂函数测试
# ============================================================================


class TestGetNormalizer:
    """配置驱动标准化器工厂测试"""

    def test_zscore_by_name(self):
        n = get_normalizer("z_score")
        assert isinstance(n, ZScoreNormalizer)

    def test_zscore_alias(self):
        n = get_normalizer("zscore")
        assert isinstance(n, ZScoreNormalizer)

    def test_rank_by_name(self):
        n = get_normalizer("rank")
        assert isinstance(n, RankNormalizer)

    def test_minmax_by_name(self):
        n = get_normalizer("minmax")
        assert isinstance(n, MinMaxNormalizer)

    def test_winsorize_by_name(self):
        n = get_normalizer("winsorize")
        assert isinstance(n, WinsorizeNormalizer)

    def test_mad_by_name(self):
        n = get_normalizer("mad")
        assert isinstance(n, MADNormalizer)

    def test_none_returns_none(self):
        assert get_normalizer(None) is None

    def test_empty_string_returns_none(self):
        assert get_normalizer("") is None

    def test_unknown_name_raises(self):
        with pytest.raises(ValueError, match="未知的标准化器名称"):
            get_normalizer("nonexistent")

    def test_case_insensitive(self):
        n = get_normalizer("Z_Score")
        assert isinstance(n, ZScoreNormalizer)

    def test_kwargs_passed_through(self):
        n = get_normalizer("z_score", clip=True, clip_range=(-2.0, 2.0))
        assert isinstance(n, ZScoreNormalizer)
        assert n.clip is True
        assert n.clip_range == (-2.0, 2.0)
