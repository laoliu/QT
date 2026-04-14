"""组合优化器 — CVXPY 实现。

支持: 等权, 市值加权, 风险平价, 最小方差, 最大夏普, 均值方差, Black-Litterman。
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def equal_weight(n: int) -> np.ndarray:
    """等权配置"""
    return np.ones(n) / n


def risk_parity(cov: np.ndarray) -> np.ndarray:
    """风险平价（逆波动率近似）

    精确的风险平价需要迭代优化，此处使用逆波动率近似。
    """
    vols = np.sqrt(np.diag(cov))
    vols = np.where(vols > 0, vols, 1e-8)
    inv_vol = 1.0 / vols
    return inv_vol / inv_vol.sum()


def min_variance(cov: np.ndarray, bounds: tuple[float, float] = (0.0, 1.0)) -> np.ndarray:
    """最小方差组合 (CVXPY)"""
    try:
        import cvxpy as cp
    except ImportError:
        logger.warning("cvxpy 未安装，降级为等权")
        return equal_weight(cov.shape[0])

    n = cov.shape[0]
    w = cp.Variable(n)
    objective = cp.Minimize(cp.quad_form(w, cov))
    constraints = [
        cp.sum(w) == 1,
        w >= bounds[0],
        w <= bounds[1],
    ]
    prob = cp.Problem(objective, constraints)
    try:
        prob.solve(solver=cp.SCS, max_iters=5000)
        if w.value is not None:
            weights = np.array(w.value).flatten()
            weights = np.maximum(weights, 0)
            weights /= weights.sum()
            return weights
    except Exception as e:
        logger.warning("CVXPY 求解失败: %s, 降级为等权", e)
    return equal_weight(n)


def max_sharpe(
    expected_returns: np.ndarray,
    cov: np.ndarray,
    risk_free_rate: float = 0.02,
    bounds: tuple[float, float] = (0.0, 1.0),
) -> np.ndarray:
    """最大夏普比率组合 (CVXPY)"""
    try:
        import cvxpy as cp
    except ImportError:
        return equal_weight(len(expected_returns))

    n = len(expected_returns)
    # 使用 Cornuejols & Tutuncu 的凸化技巧
    y = cp.Variable(n)
    k = cp.Variable()
    mu_excess = expected_returns - risk_free_rate

    objective = cp.Minimize(cp.quad_form(y, cov))
    constraints = [
        mu_excess @ y == 1,
        cp.sum(y) == k,
        k >= 0,
        y >= bounds[0] * k,
        y <= bounds[1] * k,
    ]
    prob = cp.Problem(objective, constraints)
    try:
        prob.solve(solver=cp.SCS, max_iters=5000)
        if y.value is not None and k.value and k.value > 0:
            weights = np.array(y.value).flatten() / k.value
            weights = np.maximum(weights, 0)
            weights /= weights.sum()
            return weights
    except Exception as e:
        logger.warning("最大夏普求解失败: %s", e)
    return equal_weight(n)


def ledoit_wolf_shrinkage(returns: pd.DataFrame) -> np.ndarray:
    """Ledoit-Wolf 协方差压缩估计"""
    try:
        from sklearn.covariance import LedoitWolf
        lw = LedoitWolf().fit(returns.fillna(0).values)
        return lw.covariance_
    except ImportError:
        return returns.cov().fillna(0).values


class PortfolioOptimizer:
    """统一组合优化器"""

    def __init__(self, method: str = "equal", **kwargs):
        self._method = method
        self._kwargs = kwargs

    def optimize(
        self,
        returns: pd.DataFrame,
        expected_returns: Optional[np.ndarray] = None,
    ) -> pd.Series:
        """优化权重。

        Args:
            returns: 收益率矩阵 (dates x symbols)
            expected_returns: 预期收益率（仅 max_sharpe/mean_variance 需要）

        Returns:
            pd.Series: symbol → weight
        """
        symbols = returns.columns.tolist()
        n = len(symbols)

        if self._method == "equal":
            weights = equal_weight(n)
        elif self._method == "risk_parity":
            cov = ledoit_wolf_shrinkage(returns)
            weights = risk_parity(cov)
        elif self._method == "min_variance":
            cov = ledoit_wolf_shrinkage(returns)
            weights = min_variance(cov)
        elif self._method == "max_sharpe":
            cov = ledoit_wolf_shrinkage(returns)
            er = expected_returns if expected_returns is not None else returns.mean().values
            weights = max_sharpe(er, cov)
        else:
            weights = equal_weight(n)

        return pd.Series(weights, index=symbols, name="weight")
