"""协方差估计器边界条件测试

覆盖 covariance.py 中尚未测试的分支与边界场景。
"""

import numpy as np
import pytest

from aipha.portfolio.covariance import (
    ExponentialCovarianceEstimator,
    SampleCovarianceEstimator,
    ShrinkageCovarianceEstimator,
    ensure_positive_definite,
)


# ── helpers ──────────────────────────────────────────────────


def _returns(symbols, n=60, seed=42):
    """正常收益率"""
    rng = np.random.RandomState(seed)
    return {s: list(rng.normal(0.001, 0.02, n)) for s in symbols}


# ══════════════════════════════════════════════════════════════
# 1. SampleCovarianceEstimator 边界
# ══════════════════════════════════════════════════════════════


class TestSampleCovEdge:
    """SampleCovarianceEstimator 边界"""

    def test_empty_symbols(self):
        """空 symbols → 0×0 矩阵"""
        est = SampleCovarianceEstimator()
        cov = est.estimate_from_dict({}, [])
        assert cov.shape == (0, 0)

    def test_single_data_point(self):
        """只有 1 条数据 → 返回单位矩阵"""
        est = SampleCovarianceEstimator()
        cov = est.estimate_from_dict({"A": [0.01], "B": [0.02]}, ["A", "B"])
        np.testing.assert_array_equal(cov, np.eye(2))

    def test_single_symbol(self):
        """单标的 → 1×1 矩阵"""
        est = SampleCovarianceEstimator()
        cov = est.estimate_from_dict({"A": [0.01, 0.02, 0.03]}, ["A"])
        assert cov.shape == (1, 1)
        assert cov[0, 0] > 0

    def test_below_min_periods_still_works(self):
        """样本数 < min_periods → 仍计算，只发出警告"""
        est = SampleCovarianceEstimator(min_periods=100)
        cov = est.estimate_from_dict(
            {"A": [0.01] * 10, "B": [0.02] * 10}, ["A", "B"],
        )
        assert cov.shape == (2, 2)

    def test_constant_returns_zero_variance(self):
        """全相同收益率 → 方差为 0"""
        est = SampleCovarianceEstimator()
        cov = est.estimate_from_dict(
            {"A": [0.01] * 30, "B": [0.02] * 30}, ["A", "B"],
        )
        assert cov[0, 0] == pytest.approx(0.0)
        assert cov[1, 1] == pytest.approx(0.0)


# ══════════════════════════════════════════════════════════════
# 2. ShrinkageCovarianceEstimator 边界
# ══════════════════════════════════════════════════════════════


class TestShrinkageCovEdge:
    """ShrinkageCovarianceEstimator 边界"""

    def test_empty_symbols(self):
        """空 symbols → 0×0"""
        est = ShrinkageCovarianceEstimator()
        cov = est.estimate_from_dict({}, [])
        assert cov.shape == (0, 0)

    def test_single_data_point(self):
        """1 条数据 → 单位矩阵"""
        est = ShrinkageCovarianceEstimator()
        cov = est.estimate_from_dict({"A": [0.01]}, ["A"])
        np.testing.assert_array_equal(cov, np.eye(1))

    def test_constant_correlation_target(self):
        """shrinkage_target='constant_correlation' 路径"""
        est = ShrinkageCovarianceEstimator(shrinkage_target="constant_correlation")
        rets = _returns(["A", "B", "C"])
        cov = est.estimate_from_dict(rets, ["A", "B", "C"])
        assert cov.shape == (3, 3)
        # 对称
        np.testing.assert_array_almost_equal(cov, cov.T)

    def test_fixed_shrinkage_intensity(self):
        """手动指定 shrinkage_intensity"""
        est = ShrinkageCovarianceEstimator(shrinkage_intensity=0.5)
        rets = _returns(["A", "B"])
        cov = est.estimate_from_dict(rets, ["A", "B"])
        assert cov.shape == (2, 2)
        # 结果应在 target 和样本之间
        assert np.all(np.isfinite(cov))

    def test_full_shrinkage(self):
        """intensity=1.0 → 完全等于 target"""
        est = ShrinkageCovarianceEstimator(shrinkage_intensity=1.0)
        rets = _returns(["A", "B"])
        cov = est.estimate_from_dict(rets, ["A", "B"])
        # 对角线应该相等（identity target: avg_var * I）
        assert cov[0, 0] == pytest.approx(cov[1, 1], abs=1e-6)
        # 非对角线为 0
        assert cov[0, 1] == pytest.approx(0.0, abs=1e-6)

    def test_zero_shrinkage(self):
        """intensity=0.0 → 等于样本协方差"""
        est_shrink = ShrinkageCovarianceEstimator(shrinkage_intensity=0.0)
        est_sample = SampleCovarianceEstimator()
        rets = _returns(["A", "B"])
        cov_shrink = est_shrink.estimate_from_dict(rets, ["A", "B"])
        cov_sample = est_sample.estimate_from_dict(rets, ["A", "B"])
        np.testing.assert_array_almost_equal(cov_shrink, cov_sample)

    def test_matrix_with_2_observations(self):
        """最小有效样本（T=2, N=2）→ 收缩仍有效"""
        est = ShrinkageCovarianceEstimator()
        rets = {"A": [0.01, 0.02], "B": [-0.01, 0.03]}
        cov = est.estimate_from_dict(rets, ["A", "B"])
        assert cov.shape == (2, 2)
        assert np.all(np.isfinite(cov))


# ══════════════════════════════════════════════════════════════
# 3. ExponentialCovarianceEstimator 边界
# ══════════════════════════════════════════════════════════════


class TestExponentialCovEdge:
    """ExponentialCovarianceEstimator 边界"""

    def test_empty_symbols(self):
        """空 symbols → 0×0"""
        est = ExponentialCovarianceEstimator()
        cov = est.estimate_from_dict({}, [])
        assert cov.shape == (0, 0)

    def test_single_data_point(self):
        """1 条数据 → 单位矩阵"""
        est = ExponentialCovarianceEstimator()
        cov = est.estimate_from_dict({"A": [0.01]}, ["A"])
        np.testing.assert_array_equal(cov, np.eye(1))

    def test_halflife_1(self):
        """halflife=1 → 近期数据权重极大"""
        est = ExponentialCovarianceEstimator(halflife=1)
        rets = _returns(["A", "B"], n=30)
        cov = est.estimate_from_dict(rets, ["A", "B"])
        assert cov.shape == (2, 2)
        assert np.all(np.isfinite(cov))

    def test_very_large_halflife(self):
        """halflife 很大 → 接近等权"""
        est_exp = ExponentialCovarianceEstimator(halflife=10000)
        est_sample = SampleCovarianceEstimator()
        rets = _returns(["A", "B"], n=100)
        cov_exp = est_exp.estimate_from_dict(rets, ["A", "B"])
        cov_sample = est_sample.estimate_from_dict(rets, ["A", "B"])
        # 应该非常接近
        np.testing.assert_array_almost_equal(cov_exp, cov_sample, decimal=2)

    def test_result_positive_definite(self):
        """输出一定是正定的（经过 ensure_positive_definite）"""
        est = ExponentialCovarianceEstimator(halflife=30)
        rets = _returns(["A", "B", "C"], n=60)
        cov = est.estimate_from_dict(rets, ["A", "B", "C"])
        eigenvalues = np.linalg.eigvalsh(cov)
        assert np.all(eigenvalues > 0)


# ══════════════════════════════════════════════════════════════
# 4. ensure_positive_definite 边界
# ══════════════════════════════════════════════════════════════


class TestEnsurePositiveDefiniteEdge:
    """ensure_positive_definite 边界"""

    def test_already_positive_definite(self):
        """已正定矩阵 → 不变（近似）"""
        original = np.array([[2.0, 0.5], [0.5, 1.0]])
        fixed = ensure_positive_definite(original)
        np.testing.assert_array_almost_equal(fixed, original, decimal=6)

    def test_negative_eigenvalues_fixed(self):
        """含负特征值 → 修正为正定"""
        # 构造非正定矩阵
        bad = np.array([[1.0, 2.0], [2.0, 1.0]])  # eigenvalues: 3, -1
        fixed = ensure_positive_definite(bad)
        eigenvalues = np.linalg.eigvalsh(fixed)
        assert np.all(eigenvalues > 0)

    def test_zero_matrix_becomes_epsilon_eye(self):
        """全零矩阵 → 特征值全为 epsilon"""
        zero = np.zeros((3, 3))
        fixed = ensure_positive_definite(zero, epsilon=1e-6)
        eigenvalues = np.linalg.eigvalsh(fixed)
        assert np.all(eigenvalues >= 1e-6 - 1e-10)

    def test_1x1_matrix(self):
        """1×1 矩阵"""
        m = np.array([[-0.5]])
        fixed = ensure_positive_definite(m)
        assert fixed[0, 0] > 0

    def test_symmetric_output(self):
        """输出始终对称"""
        rng = np.random.RandomState(1)
        m = rng.randn(5, 5)
        m = m + m.T  # 对称但可能不正定
        fixed = ensure_positive_definite(m)
        np.testing.assert_array_almost_equal(fixed, fixed.T)
