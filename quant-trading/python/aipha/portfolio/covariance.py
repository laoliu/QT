"""
协方差矩阵估计模块

提供多种协方差矩阵估计方法：
- SampleCovarianceEstimator: 样本协方差
- ShrinkageCovarianceEstimator: Ledoit-Wolf 收缩估计
- ExponentialCovarianceEstimator: 指数加权协方差

使用示例：
    estimator = SampleCovarianceEstimator()
    cov_matrix = estimator.estimate(returns_df)
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    import pandas as pd


logger = logging.getLogger(__name__)


class CovarianceEstimator(ABC):
    """协方差估计器抽象基类"""

    @abstractmethod
    def estimate(self, returns: "pd.DataFrame") -> np.ndarray:
        """估计协方差矩阵

        Args:
            returns: 收益率 DataFrame，columns 为资产，rows 为时间

        Returns:
            协方差矩阵 (n_assets x n_assets)
        """
        raise NotImplementedError

    @abstractmethod
    def estimate_from_dict(
        self, returns: dict[str, list[float]], symbols: list[str]
    ) -> np.ndarray:
        """从字典估计协方差矩阵

        Args:
            returns: {symbol: [r1, r2, ...]} 收益率字典
            symbols: 资产符号列表（决定矩阵顺序）

        Returns:
            协方差矩阵 (n_assets x n_assets)
        """
        raise NotImplementedError


@dataclass
class SampleCovarianceEstimator(CovarianceEstimator):
    """样本协方差估计器

    使用历史收益率的样本协方差矩阵。

    Attributes:
        min_periods: 最少需要的观测期数
    """

    min_periods: int = 20

    def estimate(self, returns: "pd.DataFrame") -> np.ndarray:
        """从 DataFrame 估计协方差矩阵"""
        if len(returns) < self.min_periods:
            logger.warning(
                f"样本数 {len(returns)} 小于最小期数 {self.min_periods}，"
                "协方差估计可能不稳定"
            )

        # 使用 pandas 的协方差计算
        cov_matrix = returns.cov().values
        return cov_matrix

    def estimate_from_dict(
        self, returns: dict[str, list[float]], symbols: list[str]
    ) -> np.ndarray:
        """从字典估计协方差矩阵"""
        n = len(symbols)
        if n == 0:
            return np.array([]).reshape(0, 0)

        # 获取最小长度
        min_len = min(len(returns.get(s, [])) for s in symbols)
        if min_len < 2:
            # 返回单位矩阵作为默认
            logger.warning("收益率数据不足，返回单位协方差矩阵")
            return np.eye(n)

        # 构建收益率矩阵 (T x N)
        ret_matrix = np.zeros((min_len, n))
        for i, symbol in enumerate(symbols):
            ret_matrix[:, i] = returns[symbol][:min_len]

        # 计算协方差
        if min_len < self.min_periods:
            logger.warning(
                f"样本数 {min_len} 小于最小期数 {self.min_periods}，"
                "协方差估计可能不稳定"
            )

        # 使用 numpy 计算（ddof=1 为无偏估计）
        cov_matrix = np.cov(ret_matrix, rowvar=False, ddof=1)

        # 确保返回二维矩阵
        if cov_matrix.ndim == 0:
            cov_matrix = np.array([[cov_matrix]])

        return cov_matrix


@dataclass
class ShrinkageCovarianceEstimator(CovarianceEstimator):
    """Ledoit-Wolf 收缩协方差估计器

    将样本协方差矩阵向结构化目标（单位矩阵或恒等相关矩阵）收缩，
    提高小样本下的估计稳定性。

    Attributes:
        shrinkage_target: 收缩目标 ("identity" 或 "constant_correlation")
        shrinkage_intensity: 收缩强度（None 表示自动计算）
    """

    shrinkage_target: str = "identity"
    shrinkage_intensity: float | None = None

    def estimate(self, returns: "pd.DataFrame") -> np.ndarray:
        """从 DataFrame 估计协方差矩阵"""
        ret_matrix = returns.values
        return self._estimate_from_matrix(ret_matrix)

    def estimate_from_dict(
        self, returns: dict[str, list[float]], symbols: list[str]
    ) -> np.ndarray:
        """从字典估计协方差矩阵"""
        n = len(symbols)
        if n == 0:
            return np.array([]).reshape(0, 0)

        min_len = min(len(returns.get(s, [])) for s in symbols)
        if min_len < 2:
            logger.warning("收益率数据不足，返回单位协方差矩阵")
            return np.eye(n)

        ret_matrix = np.zeros((min_len, n))
        for i, symbol in enumerate(symbols):
            ret_matrix[:, i] = returns[symbol][:min_len]

        return self._estimate_from_matrix(ret_matrix)

    def _estimate_from_matrix(self, ret_matrix: np.ndarray) -> np.ndarray:
        """从收益率矩阵估计收缩协方差"""
        t, n = ret_matrix.shape

        if t < 2:
            return np.eye(n)

        # 样本协方差
        sample_cov = np.cov(ret_matrix, rowvar=False, ddof=1)
        if sample_cov.ndim == 0:
            sample_cov = np.array([[sample_cov]])

        # 收缩目标
        if self.shrinkage_target == "identity":
            # 目标：对角矩阵，对角元素为样本协方差的平均方差
            avg_var = np.trace(sample_cov) / n
            target = avg_var * np.eye(n)
        else:
            # 恒等相关矩阵
            variances = np.diag(sample_cov)
            avg_corr = (np.sum(sample_cov) - np.trace(sample_cov)) / (n * (n - 1))
            avg_corr = max(0, min(1, avg_corr / np.mean(variances)))  # 归一化
            target = np.eye(n)
            for i in range(n):
                for j in range(n):
                    if i != j:
                        target[i, j] = avg_corr * np.sqrt(variances[i] * variances[j])
                    else:
                        target[i, j] = variances[i]

        # 收缩强度
        if self.shrinkage_intensity is not None:
            delta = self.shrinkage_intensity
        else:
            # Ledoit-Wolf 最优收缩强度（简化版）
            delta = self._ledoit_wolf_shrinkage(ret_matrix, sample_cov, target)

        # 收缩
        shrunk_cov = delta * target + (1 - delta) * sample_cov

        return shrunk_cov

    def _ledoit_wolf_shrinkage(
        self,
        ret_matrix: np.ndarray,
        sample_cov: np.ndarray,
        target: np.ndarray,
    ) -> float:
        """计算 Ledoit-Wolf 最优收缩强度

        基于 Ledoit & Wolf (2004) "A Well-Conditioned Estimator for
        Large-Dimensional Covariance Matrices"
        """
        t, n = ret_matrix.shape

        # 中心化
        ret_centered = ret_matrix - np.mean(ret_matrix, axis=0)

        # 计算 π̂ (样本协方差元素的方差之和)
        y = ret_centered ** 2
        phi_mat = (y.T @ y) / t - sample_cov ** 2
        pi_sum = np.sum(phi_mat)

        # 计算 γ̂ (样本协方差与目标之差的 Frobenius 范数平方)
        gamma = np.sum((sample_cov - target) ** 2)

        # 计算 ρ̂ (简化版：假设目标是对角矩阵)
        rho = 0.0
        for i in range(n):
            for j in range(n):
                if i == j:
                    rho += phi_mat[i, j]
                else:
                    pass  # 非对角项在简化版中忽略

        # 收缩强度
        kappa = (pi_sum - rho) / gamma if gamma > 0 else 0
        delta = max(0, min(1, kappa / t))

        return delta


@dataclass
class ExponentialCovarianceEstimator(CovarianceEstimator):
    """指数加权协方差估计器

    对近期数据赋予更高权重，适合非平稳市场。

    Attributes:
        halflife: 半衰期（天数）
        min_periods: 最少需要的观测期数
    """

    halflife: int = 60
    min_periods: int = 20

    def estimate(self, returns: "pd.DataFrame") -> np.ndarray:
        """从 DataFrame 估计协方差矩阵"""
        # 使用 pandas 的指数加权协方差
        ewm_cov = returns.ewm(halflife=self.halflife, min_periods=self.min_periods).cov()
        # 取最后一个时间点的协方差矩阵
        last_idx = returns.index[-1]
        cov_matrix = ewm_cov.loc[last_idx].values
        return cov_matrix

    def estimate_from_dict(
        self, returns: dict[str, list[float]], symbols: list[str]
    ) -> np.ndarray:
        """从字典估计协方差矩阵"""
        n = len(symbols)
        if n == 0:
            return np.array([]).reshape(0, 0)

        min_len = min(len(returns.get(s, [])) for s in symbols)
        if min_len < 2:
            logger.warning("收益率数据不足，返回单位协方差矩阵")
            return np.eye(n)

        # 构建收益率矩阵
        ret_matrix = np.zeros((min_len, n))
        for i, symbol in enumerate(symbols):
            ret_matrix[:, i] = returns[symbol][:min_len]

        # 计算指数权重
        decay = 0.5 ** (1.0 / self.halflife)
        weights = np.array([decay ** i for i in range(min_len - 1, -1, -1)])
        weights = weights / np.sum(weights)  # 归一化

        # 加权协方差
        weighted_mean = np.average(ret_matrix, axis=0, weights=weights)
        centered = ret_matrix - weighted_mean

        # 加权协方差矩阵
        cov_matrix = np.zeros((n, n))
        for i in range(n):
            for j in range(n):
                cov_matrix[i, j] = np.sum(weights * centered[:, i] * centered[:, j])

        # 无偏修正（近似）
        effective_n = 1.0 / np.sum(weights ** 2)
        if effective_n > 1:
            cov_matrix = cov_matrix * effective_n / (effective_n - 1)

        # 确保正定
        cov_matrix = ensure_positive_definite(cov_matrix)

        return cov_matrix


def ensure_positive_definite(
    cov_matrix: np.ndarray,
    epsilon: float = 1e-8,
) -> np.ndarray:
    """确保协方差矩阵正定

    Args:
        cov_matrix: 输入协方差矩阵
        epsilon: 最小特征值

    Returns:
        正定协方差矩阵
    """
    # 特征分解
    eigenvalues, eigenvectors = np.linalg.eigh(cov_matrix)

    # 将负特征值替换为 epsilon
    eigenvalues = np.maximum(eigenvalues, epsilon)

    # 重构矩阵
    cov_fixed = eigenvectors @ np.diag(eigenvalues) @ eigenvectors.T

    # 确保对称
    cov_fixed = (cov_fixed + cov_fixed.T) / 2

    return cov_fixed
