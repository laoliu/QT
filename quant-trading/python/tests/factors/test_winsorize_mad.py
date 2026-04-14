"""Winsorize 和 MAD 标准化器测试

测试覆盖：
- WinsorizeNormalizer:
  - 基本缩尾
  - 边界值确认
  - 后续 Z-Score 标准化
  - 不做 Z-Score（then_zscore=False）
  - NaN 处理
  - 数据不足
  - 空输入
- MADNormalizer:
  - 基本 MAD 标准化
  - 裁剪
  - 不裁剪
  - NaN 处理
  - 数据不足
  - 空输入
  - 全相同值
  - 一致性校正因子
"""

import math
from datetime import date

import pytest

from aipha.factors.base import FactorResult
from aipha.factors.composite import MADNormalizer, WinsorizeNormalizer


def _fr(symbol: str, value: float) -> FactorResult:
    """快速创建 FactorResult"""
    return FactorResult(
        symbol=symbol,
        trade_date=date(2025, 1, 10),
        factor_name="TEST",
        value=value,
    )


# ============================================================================
# WinsorizeNormalizer 测试
# ============================================================================


class TestWinsorizeNormalizer:
    """Winsorize 缩尾标准化测试"""

    def test_basic_winsorize(self):
        """基本缩尾：极端值被替换为边界值"""
        # 100 个均匀分布的值
        results = [_fr(f"S{i:03d}", float(i)) for i in range(100)]
        normalizer = WinsorizeNormalizer(
            percentile_low=5.0, percentile_high=95.0, then_zscore=False
        )
        normalized = normalizer.normalize(results)

        assert len(normalized) == 100
        # 极端低值应该被缩尾到 percentile_low 对应值
        # percentile_low=5% 对应 index 5 → value=5
        assert normalized[0].metadata["winsorized_value"] >= 5.0
        assert normalized[99].metadata["winsorized_value"] <= 95.0

    def test_boundary_values_preserved(self):
        """中间值不受缩尾影响"""
        results = [_fr(f"S{i:03d}", float(i)) for i in range(100)]
        normalizer = WinsorizeNormalizer(
            percentile_low=5.0, percentile_high=95.0, then_zscore=False
        )
        normalized = normalizer.normalize(results)

        # 中间值（50）应该保持不变
        assert normalized[50].metadata["winsorized_value"] == 50.0

    def test_then_zscore(self):
        """缩尾后做 Z-Score"""
        results = [_fr(f"S{i:03d}", float(i)) for i in range(50)]
        normalizer = WinsorizeNormalizer(
            percentile_low=5.0, percentile_high=95.0, then_zscore=True
        )
        normalized = normalizer.normalize(results)

        # Z-Score 均值应约 0
        z_scores = [r.z_score for r in normalized if not math.isnan(r.z_score)]
        mean_z = sum(z_scores) / len(z_scores)
        assert mean_z == pytest.approx(0.0, abs=0.1)

    def test_no_zscore(self):
        """不做 Z-Score → z_score 等于缩尾后值"""
        results = [_fr(f"S{i}", float(i)) for i in range(10)]
        normalizer = WinsorizeNormalizer(
            percentile_low=0.0, percentile_high=100.0, then_zscore=False
        )
        normalized = normalizer.normalize(results)
        # 无缩尾（0-100%），z_score = 原值
        assert normalized[0].z_score == 0.0
        assert normalized[5].z_score == 5.0
        assert normalized[9].z_score == 9.0

    def test_nan_handling(self):
        """NaN 值处理"""
        results = [
            _fr("A", 10.0),
            _fr("B", float("nan")),
            _fr("C", 30.0),
            _fr("D", 20.0),
        ]
        normalizer = WinsorizeNormalizer(then_zscore=True)
        normalized = normalizer.normalize(results)

        assert not math.isnan(normalized[0].z_score)
        assert math.isnan(normalized[1].z_score)
        assert not math.isnan(normalized[2].z_score)

    def test_insufficient_data(self):
        """数据太少（< 4）→ z_score=0"""
        results = [_fr("A", 10.0), _fr("B", 20.0)]
        normalizer = WinsorizeNormalizer()
        normalized = normalizer.normalize(results)

        assert len(normalized) == 2
        assert normalized[0].z_score == 0.0
        assert "insufficient_data" in normalized[0].metadata.get("error", "")

    def test_empty_input(self):
        """空输入 → 空输出"""
        normalizer = WinsorizeNormalizer()
        assert normalizer.normalize([]) == []

    def test_original_value_preserved(self):
        """原始 value 字段保持不变"""
        results = [_fr("A", 1.0), _fr("B", 1000.0)]
        normalizer = WinsorizeNormalizer(
            percentile_low=10.0, percentile_high=90.0, then_zscore=False
        )
        normalized = normalizer.normalize(results)
        assert normalized[0].value == 1.0
        assert normalized[1].value == 1000.0

    def test_metadata_contains_bounds(self):
        """metadata 包含缩尾边界"""
        results = [_fr(f"S{i}", float(i)) for i in range(20)]
        normalizer = WinsorizeNormalizer()
        normalized = normalizer.normalize(results)
        assert "low_bound" in normalized[0].metadata
        assert "high_bound" in normalized[0].metadata


# ============================================================================
# MADNormalizer 测试
# ============================================================================


class TestMADNormalizer:
    """MAD 标准化测试"""

    def test_basic_mad(self):
        """基本 MAD 标准化"""
        results = [_fr("A", 10.0), _fr("B", 20.0), _fr("C", 30.0)]
        normalizer = MADNormalizer(clip=False)
        normalized = normalizer.normalize(results)

        assert len(normalized) == 3
        # 中位数 = 20, MAD = median(|10-20|, |20-20|, |30-20|) = 10
        # mad_std = 1.4826 * 10 = 14.826
        # z_A = (10 - 20) / 14.826 ≈ -0.6745
        assert normalized[0].z_score == pytest.approx(-0.6745, abs=0.01)
        assert normalized[1].z_score == pytest.approx(0.0, abs=0.01)
        assert normalized[2].z_score == pytest.approx(0.6745, abs=0.01)

    def test_clipping(self):
        """极端值裁剪"""
        # 极端值应该被裁剪
        results = [_fr(f"S{i}", float(i)) for i in range(100)]
        results.append(_fr("EXTREME", 10000.0))
        normalizer = MADNormalizer(clip=True, clip_range=(-3.0, 3.0))
        normalized = normalizer.normalize(results)

        for r in normalized:
            if not math.isnan(r.z_score):
                assert -3.0 <= r.z_score <= 3.0

    def test_no_clipping(self):
        """不裁剪 → 保留极端 z-score"""
        results = [_fr("A", 0.0), _fr("B", 1.0), _fr("C", 100.0)]
        normalizer = MADNormalizer(clip=False)
        normalized = normalizer.normalize(results)

        # C 的 z-score 可能很大
        assert abs(normalized[2].z_score) > 3.0

    def test_nan_handling(self):
        """NaN 处理"""
        results = [
            _fr("A", 10.0),
            _fr("B", float("nan")),
            _fr("C", 30.0),
            _fr("D", 20.0),
        ]
        normalizer = MADNormalizer(clip=False)
        normalized = normalizer.normalize(results)

        assert not math.isnan(normalized[0].z_score)
        assert math.isnan(normalized[1].z_score)
        assert not math.isnan(normalized[2].z_score)

    def test_insufficient_data(self):
        """数据不足（< 3）"""
        results = [_fr("A", 10.0), _fr("B", 20.0)]
        normalizer = MADNormalizer()
        normalized = normalizer.normalize(results)

        assert len(normalized) == 2
        assert "insufficient_data" in normalized[0].metadata.get("error", "")

    def test_empty_input(self):
        """空输入"""
        normalizer = MADNormalizer()
        assert normalizer.normalize([]) == []

    def test_constant_values(self):
        """全相同值 → MAD=0 → mad_std=1.0 → z_score=0"""
        results = [_fr("A", 5.0), _fr("B", 5.0), _fr("C", 5.0)]
        normalizer = MADNormalizer(clip=False)
        normalized = normalizer.normalize(results)

        for r in normalized:
            assert r.z_score == pytest.approx(0.0)

    def test_metadata_contents(self):
        """metadata 包含 MAD 计算细节"""
        results = [_fr("A", 10.0), _fr("B", 20.0), _fr("C", 30.0)]
        normalizer = MADNormalizer()
        normalized = normalizer.normalize(results)

        for r in normalized:
            if not math.isnan(r.z_score):
                assert "median" in r.metadata
                assert "mad" in r.metadata
                assert "mad_std" in r.metadata

    def test_consistency_factor(self):
        """自定义一致性校正因子"""
        results = [_fr("A", 10.0), _fr("B", 20.0), _fr("C", 30.0)]
        normalizer = MADNormalizer(
            clip=False, consistency_factor=1.0  # 不做校正
        )
        normalized = normalizer.normalize(results)

        # MAD = 10, mad_std = 1.0 * 10 = 10
        # z_A = (10 - 20) / 10 = -1.0
        assert normalized[0].z_score == pytest.approx(-1.0, abs=0.01)

    def test_robustness_to_outliers(self):
        """MAD 对离群值更鲁棒（vs Z-Score）"""
        # 正常数据 + 一个极端离群值
        results = [_fr(f"S{i}", float(i)) for i in range(20)]
        results.append(_fr("OUTLIER", 10000.0))

        mad_norm = MADNormalizer(clip=False)
        mad_results = mad_norm.normalize(results)

        # 正常值的 z_mad 应该在合理范围内
        normal_z = [r.z_score for r in mad_results[:20]]
        assert all(abs(z) < 5.0 for z in normal_z), (
            "MAD should keep normal values in reasonable z-score range"
        )
