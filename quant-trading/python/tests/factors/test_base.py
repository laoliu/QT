"""
因子基础框架测试
"""

from datetime import date
import math

import pytest

from aipha.factors.base import Factor, FactorData, FactorResult


# ============================================================================
# FactorData Tests
# ============================================================================


class TestFactorData:
    """因子数据测试"""

    def test_creation_minimal(self):
        """最小数据创建"""
        data = FactorData(symbol="000001.SZ", trade_date=date(2025, 1, 6))
        assert data.symbol == "000001.SZ"
        assert data.trade_date == date(2025, 1, 6)
        assert data.close is None
        assert data.returns is None

    def test_creation_full(self):
        """完整数据创建"""
        data = FactorData(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            close=10.5,
            volume=1_000_000,
            returns=[0.01, 0.02, -0.01],
            fundamentals={"eps": 1.5, "bps": 8.0},
            metadata={"source": "test"},
        )
        assert data.close == 10.5
        assert data.volume == 1_000_000
        assert len(data.returns) == 3
        assert data.fundamentals["eps"] == 1.5


# ============================================================================
# FactorResult Tests
# ============================================================================


class TestFactorResult:
    """因子结果测试"""

    def test_creation(self):
        """结果创建"""
        result = FactorResult(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            factor_name="EP",
            value=0.05,
        )
        assert result.symbol == "000001.SZ"
        assert result.factor_name == "EP"
        assert result.value == 0.05
        assert result.rank is None

    def test_with_rank_and_zscore(self):
        """带排名和 Z-Score"""
        result = FactorResult(
            symbol="000001.SZ",
            trade_date=date(2025, 1, 6),
            factor_name="EP",
            value=0.05,
            rank=0.8,
            z_score=1.5,
        )
        assert result.rank == 0.8
        assert result.z_score == 1.5


# ============================================================================
# Factor Base Tests (using concrete implementation)
# ============================================================================


class SimpleFactor(Factor):
    """测试用简单因子"""

    @property
    def name(self) -> str:
        return "SimpleFactor"

    def compute(self, data: FactorData) -> FactorResult:
        value = data.close if data.close else 0.0
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=value,
        )


class TestFactorBase:
    """因子基类测试"""

    def test_repr(self):
        """字符串表示"""
        factor = SimpleFactor()
        assert "SimpleFactor" in repr(factor)

    def test_compute_batch_default(self):
        """批量计算默认实现"""
        factor = SimpleFactor()
        data_list = [
            FactorData(symbol="000001.SZ", trade_date=date(2025, 1, 6), close=10.0),
            FactorData(symbol="000002.SZ", trade_date=date(2025, 1, 6), close=20.0),
        ]
        results = factor.compute_batch(data_list)
        assert len(results) == 2
        assert results[0].value == 10.0
        assert results[1].value == 20.0


# ============================================================================
# Normalize Tests
# ============================================================================


class TestNormalize:
    """归一化测试"""

    @pytest.fixture
    def factor(self) -> SimpleFactor:
        return SimpleFactor()

    @pytest.fixture
    def sample_results(self) -> list[FactorResult]:
        return [
            FactorResult(
                symbol="A", trade_date=date(2025, 1, 6), factor_name="test", value=10.0
            ),
            FactorResult(
                symbol="B", trade_date=date(2025, 1, 6), factor_name="test", value=30.0
            ),
            FactorResult(
                symbol="C", trade_date=date(2025, 1, 6), factor_name="test", value=20.0
            ),
        ]

    def test_rank_normalize(self, factor: SimpleFactor, sample_results):
        """排名归一化"""
        normalized = factor.normalize(sample_results, method="rank")
        assert len(normalized) == 3

        # A=10 最小，排名 0
        # C=20 中间，排名 0.5
        # B=30 最大，排名 1
        rank_by_symbol = {r.symbol: r.rank for r in normalized}
        assert rank_by_symbol["A"] == 0.0
        assert rank_by_symbol["C"] == 0.5
        assert rank_by_symbol["B"] == 1.0

    def test_zscore_normalize(self, factor: SimpleFactor, sample_results):
        """Z-Score 归一化"""
        normalized = factor.normalize(sample_results, method="zscore")
        assert len(normalized) == 3

        # 均值 = 20，标准差 = 10
        zscore_by_symbol = {r.symbol: r.z_score for r in normalized}
        assert abs(zscore_by_symbol["A"] - (-1.0)) < 0.01
        assert abs(zscore_by_symbol["C"] - 0.0) < 0.01
        assert abs(zscore_by_symbol["B"] - 1.0) < 0.01

    def test_normalize_empty(self, factor: SimpleFactor):
        """空列表归一化"""
        normalized = factor.normalize([], method="rank")
        assert normalized == []

    def test_normalize_single(self, factor: SimpleFactor):
        """单元素归一化"""
        results = [
            FactorResult(
                symbol="A", trade_date=date(2025, 1, 6), factor_name="test", value=10.0
            )
        ]
        # Rank
        normalized = factor.normalize(results, method="rank")
        assert normalized[0].rank == 0.5  # 单元素默认 0.5

        # Z-Score
        normalized = factor.normalize(results, method="zscore")
        assert normalized[0].z_score == 0.0  # 单元素 z-score = 0

    def test_normalize_invalid_method(self, factor: SimpleFactor, sample_results):
        """无效归一化方法"""
        with pytest.raises(ValueError, match="未知的归一化方法"):
            factor.normalize(sample_results, method="invalid")
