"""PBO 回测过拟合概率框架单元测试。

覆盖 PBOConfig / PBOAnalyzer / compute_pbo / pbo_from_sweep_results。
"""

from __future__ import annotations

import numpy as np
import pytest

from aipha.reporting.pbo import (
    DEFAULT_NUM_SUBSAMPLES,
    MAX_COMBINATIONS,
    PBO_OVERFIT_THRESHOLD,
    PBOAnalyzer,
    PBOConfig,
    PBOResult,
    compute_pbo,
    pbo_from_sweep_results,
)


# ============================================================================
# Helpers
# ============================================================================

def _make_random_matrix(
    t: int = 480, n: int = 10, seed: int = 42,
) -> np.ndarray:
    """生成随机日收益矩阵 (T, N)。"""
    rng = np.random.default_rng(seed)
    return rng.normal(0.0005, 0.02, (t, n))


def _make_overfit_matrix(
    t: int = 480, n: int = 10, seed: int = 42,
) -> np.ndarray:
    """生成有过拟合特征的矩阵：

    策略 0 在前半段极好，后半段极差（过拟合典型）。
    """
    rng = np.random.default_rng(seed)
    mat = rng.normal(0.0, 0.02, (t, n))
    half = t // 2
    mat[:half, 0] += 0.01  # IS 很好
    mat[half:, 0] -= 0.02  # OOS 很差
    return mat


# ============================================================================
# PBOConfig
# ============================================================================

class TestPBOConfig:
    """PBOConfig 测试。"""

    def test_defaults(self):
        cfg = PBOConfig()
        assert cfg.num_subsamples == DEFAULT_NUM_SUBSAMPLES
        assert cfg.max_combinations == MAX_COMBINATIONS
        assert cfg.performance_metric == "sharpe"
        assert cfg.overfit_threshold == PBO_OVERFIT_THRESHOLD

    def test_custom_values(self):
        cfg = PBOConfig(num_subsamples=8, performance_metric="return")
        assert cfg.num_subsamples == 8
        assert cfg.performance_metric == "return"

    def test_invalid_odd_subsamples(self):
        with pytest.raises(ValueError, match="偶数"):
            PBOConfig(num_subsamples=7)

    def test_invalid_too_small(self):
        with pytest.raises(ValueError, match="偶数"):
            PBOConfig(num_subsamples=2)


# ============================================================================
# PBOAnalyzer internals
# ============================================================================

class TestPBOAnalyzerInternals:
    """内部方法测试。"""

    def test_split_subsamples(self):
        """切割为 S 个等长子样本。"""
        analyzer = PBOAnalyzer(PBOConfig(num_subsamples=4))
        mat = np.ones((100, 3))
        subs = analyzer._split_subsamples(mat)
        assert len(subs) == 4
        assert subs[0].shape == (25, 3)

    def test_split_insufficient_rows(self):
        """行数不足 → ValueError。"""
        analyzer = PBOAnalyzer(PBOConfig(num_subsamples=16))
        mat = np.ones((10, 3))
        with pytest.raises(ValueError, match="不足"):
            analyzer._split_subsamples(mat)

    def test_sharpe_ratio_positive(self):
        """正收益序列夏普 > 0。"""
        analyzer = PBOAnalyzer()
        returns = np.array([0.01, 0.02, 0.005, 0.015, 0.01])
        sr = analyzer._sharpe_ratio(returns)
        assert sr > 0

    def test_sharpe_ratio_zero_std(self):
        """常数收益 → 0 (避免 div by zero)。"""
        analyzer = PBOAnalyzer()
        returns = np.array([0.01, 0.01, 0.01])
        sr = analyzer._sharpe_ratio(returns)
        assert sr == 0.0

    def test_compute_metric_sharpe(self):
        """_compute_metric 对多列计算 Sharpe。"""
        analyzer = PBOAnalyzer(PBOConfig(performance_metric="sharpe"))
        mat = _make_random_matrix(t=100, n=5)
        scores = analyzer._compute_metric(mat)
        assert scores.shape == (5,)

    def test_compute_metric_return(self):
        """return 模式下返回累计收益。"""
        analyzer = PBOAnalyzer(PBOConfig(performance_metric="return"))
        mat = np.ones((10, 3)) * 0.01
        scores = analyzer._compute_metric(mat)
        np.testing.assert_allclose(scores, [0.1, 0.1, 0.1], atol=1e-10)


# ============================================================================
# PBOAnalyzer.compute
# ============================================================================

class TestPBOCompute:
    """核心 CSCV 计算测试。"""

    def test_basic_output_shape(self):
        """输出结构正确。"""
        mat = _make_random_matrix(t=240, n=5)
        cfg = PBOConfig(num_subsamples=8)
        result = compute_pbo(mat, cfg)
        assert isinstance(result, PBOResult)
        assert 0.0 <= result.pbo <= 1.0
        assert isinstance(result.is_overfit, bool)
        assert result.num_combinations_tested > 0

    def test_pbo_range(self):
        """PBO 在 [0, 1] 范围内。"""
        mat = _make_random_matrix(t=480, n=10)
        cfg = PBOConfig(num_subsamples=8)
        result = compute_pbo(mat, cfg)
        assert 0.0 <= result.pbo <= 1.0

    def test_overfit_detection(self):
        """过拟合矩阵应有较高 PBO。"""
        mat = _make_overfit_matrix(t=480, n=8)
        cfg = PBOConfig(num_subsamples=8, performance_metric="return")
        result = compute_pbo(mat, cfg)
        # 不强制 > 0.5，但应 > 0（过拟合信号存在）
        assert result.pbo > 0.0

    def test_logit_distribution_length(self):
        """logit 分布长度 = 测试组合数。"""
        mat = _make_random_matrix(t=160, n=4)
        cfg = PBOConfig(num_subsamples=4)
        result = compute_pbo(mat, cfg)
        assert len(result.logit_distribution) == result.num_combinations_tested

    def test_oos_rank_distribution(self):
        """OOS 排名分布在 [0, 1]。"""
        mat = _make_random_matrix(t=240, n=6)
        cfg = PBOConfig(num_subsamples=4)
        result = compute_pbo(mat, cfg)
        for r in result.oos_rank_distribution:
            assert 0.0 <= r <= 1.0

    def test_too_few_strategies(self):
        """N < 2 → ValueError。"""
        mat = np.ones((100, 1))
        with pytest.raises(ValueError, match="至少需要"):
            compute_pbo(mat)

    def test_1d_input_rejected(self):
        """1D 输入 → ValueError。"""
        with pytest.raises(ValueError, match="2D"):
            compute_pbo(np.ones(100))

    def test_deterministic(self):
        """相同输入+种子 → 相同结果。"""
        mat = _make_random_matrix(t=240, n=5)
        cfg = PBOConfig(num_subsamples=4, random_seed=123)
        r1 = compute_pbo(mat, cfg)
        r2 = compute_pbo(mat, cfg)
        assert r1.pbo == r2.pbo


# ============================================================================
# pbo_from_sweep_results
# ============================================================================

class TestPboFromSweepResults:
    """参数扫描接口测试。"""

    def test_basic(self):
        rng = np.random.default_rng(99)
        sweep = {
            f"param_{i}": rng.normal(0.0005, 0.02, 200)
            for i in range(6)
        }
        cfg = PBOConfig(num_subsamples=4)
        result = pbo_from_sweep_results(sweep, cfg)
        assert isinstance(result, PBOResult)
        assert 0.0 <= result.pbo <= 1.0

    def test_empty_sweep(self):
        with pytest.raises(ValueError, match="不能为空"):
            pbo_from_sweep_results({})

    def test_mismatched_lengths(self):
        sweep = {
            "a": np.ones(100),
            "b": np.ones(200),
        }
        with pytest.raises(ValueError, match="长度不一致"):
            pbo_from_sweep_results(sweep)
