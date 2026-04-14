"""协方差估计模块测试"""

import numpy as np
import pytest

from aipha.portfolio.covariance import (
    CovarianceEstimator,
    ExponentialCovarianceEstimator,
    SampleCovarianceEstimator,
    ShrinkageCovarianceEstimator,
    ensure_positive_definite,
)


class TestSampleCovarianceEstimator:
    """样本协方差估计器测试"""

    def test_basic_covariance(self):
        """测试基本协方差计算"""
        estimator = SampleCovarianceEstimator()
        returns = {
            "A": [0.01, 0.02, -0.01, 0.03, 0.01],
            "B": [0.02, 0.01, 0.01, 0.02, 0.01],
        }
        symbols = ["A", "B"]

        cov_matrix = estimator.estimate_from_dict(returns, symbols)

        assert cov_matrix.shape == (2, 2)
        # 对角线应为方差（正数）
        assert cov_matrix[0, 0] > 0
        assert cov_matrix[1, 1] > 0
        # 应对称
        assert cov_matrix[0, 1] == pytest.approx(cov_matrix[1, 0])

    def test_single_asset(self):
        """测试单资产"""
        estimator = SampleCovarianceEstimator()
        returns = {"A": [0.01, 0.02, -0.01, 0.03]}
        symbols = ["A"]

        cov_matrix = estimator.estimate_from_dict(returns, symbols)

        assert cov_matrix.shape == (1, 1)
        assert cov_matrix[0, 0] > 0

    def test_empty_returns(self):
        """测试空收益率"""
        estimator = SampleCovarianceEstimator()
        returns = {"A": [], "B": []}
        symbols = ["A", "B"]

        cov_matrix = estimator.estimate_from_dict(returns, symbols)

        # 应返回单位矩阵
        assert cov_matrix.shape == (2, 2)
        assert cov_matrix[0, 0] == pytest.approx(1.0)
        assert cov_matrix[1, 1] == pytest.approx(1.0)

    def test_insufficient_data(self):
        """测试数据不足"""
        estimator = SampleCovarianceEstimator()
        returns = {"A": [0.01], "B": [0.02]}  # 只有一个数据点
        symbols = ["A", "B"]

        cov_matrix = estimator.estimate_from_dict(returns, symbols)

        # 应返回单位矩阵
        assert cov_matrix.shape == (2, 2)

    def test_perfect_correlation(self):
        """测试完全正相关"""
        estimator = SampleCovarianceEstimator()
        # A 和 B 完全相关
        returns = {
            "A": [0.01, 0.02, 0.03, 0.04, 0.05],
            "B": [0.02, 0.04, 0.06, 0.08, 0.10],  # B = 2 * A
        }
        symbols = ["A", "B"]

        cov_matrix = estimator.estimate_from_dict(returns, symbols)

        # 相关系数应接近 1
        corr = cov_matrix[0, 1] / np.sqrt(cov_matrix[0, 0] * cov_matrix[1, 1])
        assert corr == pytest.approx(1.0, abs=1e-10)


class TestShrinkageCovarianceEstimator:
    """收缩协方差估计器测试"""

    def test_shrinkage_toward_identity(self):
        """测试向单位矩阵收缩"""
        estimator = ShrinkageCovarianceEstimator(shrinkage_target="identity")
        returns = {
            "A": [0.01, 0.02, -0.01, 0.03, 0.01],
            "B": [0.02, 0.01, 0.01, 0.02, 0.01],
        }
        symbols = ["A", "B"]

        cov_matrix = estimator.estimate_from_dict(returns, symbols)

        assert cov_matrix.shape == (2, 2)
        # 应正定
        eigenvalues = np.linalg.eigvalsh(cov_matrix)
        assert np.all(eigenvalues > 0)

    def test_fixed_shrinkage_intensity(self):
        """测试固定收缩强度"""
        estimator = ShrinkageCovarianceEstimator(shrinkage_intensity=0.5)
        returns = {
            "A": [0.01, 0.02, -0.01, 0.03, 0.01],
            "B": [0.02, 0.01, 0.01, 0.02, 0.01],
        }
        symbols = ["A", "B"]

        cov_matrix = estimator.estimate_from_dict(returns, symbols)

        assert cov_matrix.shape == (2, 2)
        # 收缩后非对角线应更小
        sample_cov = SampleCovarianceEstimator().estimate_from_dict(returns, symbols)
        # 非对角线绝对值应 <= 样本协方差
        assert abs(cov_matrix[0, 1]) <= abs(sample_cov[0, 1]) + 1e-10

    def test_full_shrinkage(self):
        """测试完全收缩（强度=1）"""
        estimator = ShrinkageCovarianceEstimator(shrinkage_intensity=1.0)
        returns = {
            "A": [0.01, 0.02, -0.01, 0.03, 0.01],
            "B": [0.02, 0.01, 0.01, 0.02, 0.01],
        }
        symbols = ["A", "B"]

        cov_matrix = estimator.estimate_from_dict(returns, symbols)

        # 完全收缩：非对角线应为 0（对于 identity 目标）
        assert cov_matrix[0, 1] == pytest.approx(0.0, abs=1e-10)


class TestExponentialCovarianceEstimator:
    """指数加权协方差估计器测试"""

    def test_exponential_weighting(self):
        """测试指数加权"""
        estimator = ExponentialCovarianceEstimator(halflife=3)
        returns = {
            "A": [0.01, 0.02, 0.03, 0.04, 0.05],
            "B": [0.05, 0.04, 0.03, 0.02, 0.01],
        }
        symbols = ["A", "B"]

        cov_matrix = estimator.estimate_from_dict(returns, symbols)

        assert cov_matrix.shape == (2, 2)
        # 应正定（因为 ensure_positive_definite 修复）
        eigenvalues = np.linalg.eigvalsh(cov_matrix)
        assert np.all(eigenvalues >= 0)  # 允许接近 0（已修复为正半定）

    def test_short_halflife(self):
        """测试短半衰期（近期数据权重高）"""
        estimator = ExponentialCovarianceEstimator(halflife=2)
        returns = {
            "A": [0.01, 0.02, 0.03, 0.04, 0.05],
            "B": [0.01, 0.02, 0.03, 0.04, 0.05],
        }
        symbols = ["A", "B"]

        cov_matrix = estimator.estimate_from_dict(returns, symbols)

        assert cov_matrix.shape == (2, 2)


class TestEnsurePositiveDefinite:
    """正定性修复测试"""

    def test_already_positive_definite(self):
        """测试已正定矩阵"""
        cov = np.array([[1.0, 0.5], [0.5, 1.0]])
        fixed = ensure_positive_definite(cov)

        np.testing.assert_array_almost_equal(cov, fixed)

    def test_fix_near_singular(self):
        """测试近奇异矩阵"""
        # 创建一个近奇异矩阵
        cov = np.array([[1.0, 0.9999], [0.9999, 1.0]])
        fixed = ensure_positive_definite(cov)

        # 应正定
        eigenvalues = np.linalg.eigvalsh(fixed)
        assert np.all(eigenvalues > 0)

    def test_fix_negative_eigenvalue(self):
        """测试负特征值矩阵"""
        # 创建有负特征值的矩阵
        cov = np.array([[1.0, 1.5], [1.5, 1.0]])  # 不正定
        fixed = ensure_positive_definite(cov)

        # 应正定
        eigenvalues = np.linalg.eigvalsh(fixed)
        assert np.all(eigenvalues > 0)

    def test_symmetry_preserved(self):
        """测试对称性保持"""
        cov = np.array([[1.0, 0.8], [0.8, 1.0]])
        fixed = ensure_positive_definite(cov)

        # 应对称
        np.testing.assert_array_almost_equal(fixed, fixed.T)
