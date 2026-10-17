"""
凸优化组合优化器

使用 cvxpy 实现真正的组合优化：
- ConvexMinVarianceOptimizer: 最小方差优化（二次规划）
- ConvexRiskParityOptimizer: 风险平价优化（迭代算法）
- ConvexMaxSharpeOptimizer: 最大夏普比优化（有效前沿）
- MeanVarianceOptimizer: 均值-方差优化

依赖: pip install cvxpy scipy

使用示例：
    from aipha.portfolio.convex_optimizer import ConvexMinVarianceOptimizer
    from aipha.portfolio.covariance import SampleCovarianceEstimator

    optimizer = ConvexMinVarianceOptimizer(
        cov_estimator=SampleCovarianceEstimator(),
    )
    weights = optimizer.optimize(symbols=["A", "B", "C"], returns=returns_dict)
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from aipha.portfolio.covariance import (
    CovarianceEstimator,
    SampleCovarianceEstimator,
    ensure_positive_definite,
)
from aipha.portfolio.optimizer import EqualWeightOptimizer, WeightOptimizer
from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


logger = logging.getLogger(__name__)


# Do not import cvxpy during module collection. On Windows, cvxpy probes all
# installed native solvers (including highspy) at import time; that probe can
# crash the interpreter when the whole test suite imports many extensions.
# Keep the public availability flag cheap and load the solver only on demand.
CVXPY_AVAILABLE = importlib.util.find_spec("cvxpy") is not None
cp = None
_cvxpy_module: Any | None = None


def _get_cvxpy():
    global _cvxpy_module
    if _cvxpy_module is None:
        if not CVXPY_AVAILABLE:
            raise ImportError("cvxpy 未安装，凸优化器不可用")
        # cvxpy eagerly probes every installed conic solver.  The bundled
        # highspy wheel is unstable when imported after several other native
        # extensions on Windows/Python 3.13; hide only that optional solver so
        # cvxpy can safely use its other solvers and its own fallback path.
        if sys.platform == "win32" and sys.version_info >= (3, 13):
            sys.modules.setdefault("highspy", None)
        import cvxpy as module

        _cvxpy_module = module
    return _cvxpy_module


@dataclass
class OptimizationConstraints:
    """优化约束配置

    Attributes:
        min_weight: 单个资产最小权重
        max_weight: 单个资产最大权重
        long_only: 是否仅做多（权重 >= 0）
        target_return: 目标收益率（用于均值-方差优化）
        max_turnover: 最大换手率（相对于 prev_weights）
        sector_limits: 行业权重上限 {sector: max_weight}
    """

    min_weight: float = 0.0
    max_weight: float = 1.0
    long_only: bool = True
    target_return: float | None = None
    max_turnover: float | None = None
    sector_limits: dict[str, float] = field(default_factory=dict)
    max_cvar_95: float | None = None  # CVaR(95%) 上限约束 (日度)


@dataclass
class ConvexMinVarianceOptimizer(WeightOptimizer):
    """最小方差优化器（二次规划）

    求解: min w^T Σ w
    约束: sum(w) = 1, w >= 0 (可选)

    Attributes:
        cov_estimator: 协方差估计器
        constraints: 优化约束
        solver: cvxpy 求解器 (None 表示自动选择)
    """

    cov_estimator: CovarianceEstimator = field(
        default_factory=SampleCovarianceEstimator
    )
    constraints: OptimizationConstraints = field(
        default_factory=OptimizationConstraints
    )
    solver: str | None = None

    def optimize(
        self,
        symbols: list[str],
        returns: Optional[dict[str, list[float]]] = None,
        covariance: Optional[dict[str, dict[str, float]]] = None,
        market_caps: Optional[dict[str, float]] = None,
        constraints: Optional[dict] = None,
        prev_weights: Optional[dict[str, float]] = None,
        sector_map: Optional[dict[str, str]] = None,
    ) -> dict[str, float]:
        """计算最小方差组合权重

        Args:
            symbols: 资产列表
            returns: 收益率字典
            covariance: 协方差字典
            market_caps: 市值字典
            constraints: 额外约束（覆盖默认）
            prev_weights: 前一期权重{symbol: weight}，用于换手率约束
            sector_map: 行业归属{symbol: sector}，用于行业约束
        """
        if not symbols:
            return {}

        n = len(symbols)

        # 构建协方差矩阵
        if covariance is not None:
            # 从字典构建
            cov_matrix = np.zeros((n, n))
            for i, s1 in enumerate(symbols):
                for j, s2 in enumerate(symbols):
                    cov_matrix[i, j] = covariance.get(s1, {}).get(s2, 0.0)
        elif returns is not None:
            # 从收益率估计
            if not all(s in returns for s in symbols):
                logger.warning("部分标的缺少收益率数据，回退到等权")
                return EqualWeightOptimizer().optimize(symbols)
            cov_matrix = self.cov_estimator.estimate_from_dict(returns, symbols)
        else:
            logger.warning("无协方差或收益率数据，回退到等权")
            return EqualWeightOptimizer().optimize(symbols)

        # 确保正定
        cov_matrix = ensure_positive_definite(cov_matrix)

        # 使用 cvxpy 求解
        if not CVXPY_AVAILABLE:
            logger.warning("cvxpy 未安装，使用简化求解")
            return self._fallback_solve(symbols, cov_matrix)

        try:
            weights = self._cvxpy_solve(
                n, cov_matrix, symbols, prev_weights, sector_map,
            )
            return {symbols[i]: float(weights[i]) for i in range(n)}
        except Exception as e:
            logger.error(f"cvxpy 求解失败: {e}，回退到简化实现")
            return self._fallback_solve(symbols, cov_matrix)

    def _cvxpy_solve(
        self,
        n: int,
        cov_matrix: np.ndarray,
        symbols: list[str] | None = None,
        prev_weights: dict[str, float] | None = None,
        sector_map: dict[str, str] | None = None,
    ) -> np.ndarray:
        """使用 cvxpy 求解最小方差"""
        w = _get_cvxpy().Variable(n)

        # 目标：最小化组合方差
        portfolio_variance = _get_cvxpy().quad_form(w, cov_matrix)
        objective = _get_cvxpy().Minimize(portfolio_variance)

        # 约束
        constraint_list = [_get_cvxpy().sum(w) == 1]

        if self.constraints.long_only:
            constraint_list.append(w >= self.constraints.min_weight)
        else:
            constraint_list.append(w >= self.constraints.min_weight)

        constraint_list.append(w <= self.constraints.max_weight)

        # 换手率约束
        if (
            self.constraints.max_turnover is not None
            and prev_weights is not None
            and symbols is not None
        ):
            w_prev = np.array([prev_weights.get(s, 0.0) for s in symbols])
            constraint_list.append(
                _get_cvxpy().norm(w - w_prev, 1) <= self.constraints.max_turnover * 2
            )

        # 行业约束
        if (
            self.constraints.sector_limits
            and sector_map is not None
            and symbols is not None
        ):
            sectors_to_indices: dict[str, list[int]] = {}
            for i, s in enumerate(symbols):
                sector = sector_map.get(s, "unknown")
                sectors_to_indices.setdefault(sector, []).append(i)
            for sector, max_wt in self.constraints.sector_limits.items():
                indices = sectors_to_indices.get(sector, [])
                if indices:
                    constraint_list.append(_get_cvxpy().sum(w[indices]) <= max_wt)

        # 求解
        problem = _get_cvxpy().Problem(objective, constraint_list)
        problem.solve(solver=self.solver)

        if problem.status not in ["optimal", "optimal_inaccurate"]:
            raise ValueError(f"优化问题状态: {problem.status}")

        return w.value

    def _fallback_solve(
        self, symbols: list[str], cov_matrix: np.ndarray
    ) -> dict[str, float]:
        """回退求解（解析解，假设无约束）"""
        try:
            # 最小方差解析解: w = Σ^{-1} 1 / (1^T Σ^{-1} 1)
            inv_cov = np.linalg.inv(cov_matrix)
            ones = np.ones(len(symbols))
            weights = inv_cov @ ones / (ones @ inv_cov @ ones)

            # 应用约束（迭代修正）
            for _ in range(10):  # 迭代修正
                weights = np.clip(weights, self.constraints.min_weight, self.constraints.max_weight)
                if np.sum(weights) > 0:
                    weights = weights / np.sum(weights)
                else:
                    weights = np.ones(len(symbols)) / len(symbols)
                    break

            return {symbols[i]: float(weights[i]) for i in range(len(symbols))}
        except np.linalg.LinAlgError:
            return EqualWeightOptimizer().optimize(symbols)


@dataclass
class ConvexRiskParityOptimizer(WeightOptimizer):
    """风险平价优化器

    使每个资产对组合风险的贡献相等。
    使用迭代优化算法（Spinu 2013）。

    Attributes:
        cov_estimator: 协方差估计器
        target_risk_contrib: 目标风险贡献（None 表示等风险贡献）
        max_iterations: 最大迭代次数
        tolerance: 收敛容差
    """

    cov_estimator: CovarianceEstimator = field(
        default_factory=SampleCovarianceEstimator
    )
    target_risk_contrib: list[float] | None = None
    max_iterations: int = 500
    tolerance: float = 1e-8

    def optimize(
        self,
        symbols: list[str],
        returns: Optional[dict[str, list[float]]] = None,
        covariance: Optional[dict[str, dict[str, float]]] = None,
        market_caps: Optional[dict[str, float]] = None,
        constraints: Optional[dict] = None,
    ) -> dict[str, float]:
        """计算风险平价权重"""
        if not symbols:
            return {}

        n = len(symbols)

        # 构建协方差矩阵
        if covariance is not None:
            cov_matrix = np.zeros((n, n))
            for i, s1 in enumerate(symbols):
                for j, s2 in enumerate(symbols):
                    cov_matrix[i, j] = covariance.get(s1, {}).get(s2, 0.0)
        elif returns is not None:
            if not all(s in returns for s in symbols):
                logger.warning("部分标的缺少收益率数据，回退到等权")
                return EqualWeightOptimizer().optimize(symbols)
            cov_matrix = self.cov_estimator.estimate_from_dict(returns, symbols)
        else:
            logger.warning("无协方差或收益率数据，回退到等权")
            return EqualWeightOptimizer().optimize(symbols)

        # 确保正定
        cov_matrix = ensure_positive_definite(cov_matrix)

        # 目标风险贡献
        if self.target_risk_contrib is not None:
            b = np.array(self.target_risk_contrib)
            b = b / np.sum(b)  # 归一化
        else:
            b = np.ones(n) / n  # 等风险贡献

        # 求解
        weights = self._solve_risk_parity(cov_matrix, b)

        return {symbols[i]: float(weights[i]) for i in range(n)}

    def _solve_risk_parity(
        self, cov_matrix: np.ndarray, target_contrib: np.ndarray
    ) -> np.ndarray:
        """求解风险平价问题

        使用 Cyclical Coordinate Descent (CCD) 算法。
        基于 Griveau-Billion et al. (2013)
        """
        n = len(target_contrib)

        # 初始化：按波动率倒数
        volatilities = np.sqrt(np.diag(cov_matrix))
        volatilities = np.where(volatilities > 0, volatilities, 1e-8)
        w = target_contrib / volatilities
        w = w / np.sum(w)

        for iteration in range(self.max_iterations):
            w_old = w.copy()

            for i in range(n):
                # 计算 Σw 的第 i 个元素（不含 w_i 的贡献）
                sigma_w = cov_matrix @ w
                sigma_w_minus_i = sigma_w[i] - cov_matrix[i, i] * w[i]

                # 更新 w_i
                # 解一元二次方程: σ_ii * w_i^2 + (Σ_j≠i σ_ij w_j) * w_i - b_i * σ_p = 0
                a = cov_matrix[i, i]
                b = sigma_w_minus_i
                c = -target_contrib[i] * np.sqrt(w @ cov_matrix @ w)

                # 求正根
                discriminant = b * b - 4 * a * c
                if discriminant >= 0:
                    w[i] = (-b + np.sqrt(discriminant)) / (2 * a)
                    w[i] = max(1e-10, w[i])  # 确保正权重

            # 归一化
            w = w / np.sum(w)

            # 检查收敛
            if np.max(np.abs(w - w_old)) < self.tolerance:
                logger.debug(f"风险平价在第 {iteration + 1} 次迭代收敛")
                break

        return w

    def get_risk_contributions(
        self, weights: dict[str, float], cov_matrix: np.ndarray
    ) -> dict[str, float]:
        """计算各资产的风险贡献

        Returns:
            {symbol: risk_contribution_ratio}
        """
        symbols = list(weights.keys())
        w = np.array([weights[s] for s in symbols])

        # 组合波动率
        portfolio_vol = np.sqrt(w @ cov_matrix @ w)

        if portfolio_vol <= 0:
            return {s: 1.0 / len(symbols) for s in symbols}

        # 边际风险贡献
        marginal_risk = cov_matrix @ w / portfolio_vol

        # 风险贡献 = w_i * 边际风险
        risk_contrib = w * marginal_risk

        # 归一化为比例
        total_risk = np.sum(risk_contrib)
        if total_risk > 0:
            risk_contrib = risk_contrib / total_risk

        return {symbols[i]: float(risk_contrib[i]) for i in range(len(symbols))}


@dataclass
class ConvexMaxSharpeOptimizer(WeightOptimizer):
    """最大夏普比优化器

    求解有效前沿上的切线组合（最大夏普比率）。

    Attributes:
        cov_estimator: 协方差估计器
        risk_free_rate: 无风险利率（年化）
        constraints: 优化约束
        solver: cvxpy 求解器
    """

    cov_estimator: CovarianceEstimator = field(
        default_factory=SampleCovarianceEstimator
    )
    risk_free_rate: float = 0.02
    constraints: OptimizationConstraints = field(
        default_factory=OptimizationConstraints
    )
    solver: str | None = None

    def optimize(
        self,
        symbols: list[str],
        returns: Optional[dict[str, list[float]]] = None,
        covariance: Optional[dict[str, dict[str, float]]] = None,
        market_caps: Optional[dict[str, float]] = None,
        constraints: Optional[dict] = None,
    ) -> dict[str, float]:
        """计算最大夏普比权重"""
        if not symbols:
            return {}

        n = len(symbols)

        # 需要收益率数据
        if returns is None or not all(s in returns for s in symbols):
            logger.warning("缺少收益率数据，回退到等权")
            return EqualWeightOptimizer().optimize(symbols)

        # 计算预期收益（年化）
        expected_returns = np.zeros(n)
        daily_rf = self.risk_free_rate / TRADING_DAYS_PER_YEAR

        for i, symbol in enumerate(symbols):
            ret = returns[symbol]
            if len(ret) < 2:
                return EqualWeightOptimizer().optimize(symbols)
            mean_ret = np.mean(ret)
            expected_returns[i] = mean_ret * TRADING_DAYS_PER_YEAR  # 年化

        # 构建协方差矩阵
        if covariance is not None:
            cov_matrix = np.zeros((n, n))
            for i, s1 in enumerate(symbols):
                for j, s2 in enumerate(symbols):
                    cov_matrix[i, j] = covariance.get(s1, {}).get(s2, 0.0)
        else:
            cov_matrix = self.cov_estimator.estimate_from_dict(returns, symbols)

        # 年化协方差
        cov_matrix = cov_matrix * TRADING_DAYS_PER_YEAR

        # 确保正定
        cov_matrix = ensure_positive_definite(cov_matrix)

        # 超额收益
        excess_returns = expected_returns - self.risk_free_rate

        # 检查是否有正超额收益
        if np.all(excess_returns <= 0):
            logger.warning("所有资产超额收益非正，回退到最小方差")
            return ConvexMinVarianceOptimizer(
                cov_estimator=self.cov_estimator
            ).optimize(symbols, returns=returns)

        # 使用 cvxpy 求解
        if not CVXPY_AVAILABLE:
            logger.warning("cvxpy 未安装，使用简化求解")
            return self._fallback_solve(symbols, excess_returns, cov_matrix)

        try:
            weights = self._cvxpy_solve(n, excess_returns, cov_matrix)
            return {symbols[i]: float(weights[i]) for i in range(n)}
        except Exception as e:
            logger.error(f"cvxpy 求解失败: {e}，回退到简化实现")
            return self._fallback_solve(symbols, excess_returns, cov_matrix)

    def _cvxpy_solve(
        self, n: int, excess_returns: np.ndarray, cov_matrix: np.ndarray
    ) -> np.ndarray:
        """使用 cvxpy 求解最大夏普

        使用 Markowitz (1956) 变换：
        max μ^T w - γ/2 * w^T Σ w 等价于找切线组合
        """
        # 使用变换：y = w / (μ^T w), κ = 1 / (μ^T w)
        # 问题变为: min y^T Σ y, s.t. μ^T y = 1, y >= 0
        y = _get_cvxpy().Variable(n)

        portfolio_variance = _get_cvxpy().quad_form(y, cov_matrix)
        objective = _get_cvxpy().Minimize(portfolio_variance)

        # 约束
        constraint_list = [excess_returns @ y == 1]

        if self.constraints.long_only:
            constraint_list.append(y >= 0)

        problem = _get_cvxpy().Problem(objective, constraint_list)
        problem.solve(solver=self.solver)

        if problem.status not in ["optimal", "optimal_inaccurate"]:
            raise ValueError(f"优化问题状态: {problem.status}")

        # 转换回权重
        y_val = y.value
        weights = y_val / np.sum(y_val)

        return weights

    def _fallback_solve(
        self,
        symbols: list[str],
        excess_returns: np.ndarray,
        cov_matrix: np.ndarray,
    ) -> dict[str, float]:
        """回退求解（解析解）"""
        try:
            # 切线组合: w* = Σ^{-1} μ / (1^T Σ^{-1} μ)
            inv_cov = np.linalg.inv(cov_matrix)
            weights = inv_cov @ excess_returns
            weights = weights / np.sum(weights)

            # long-only 约束
            if self.constraints.long_only:
                weights = np.maximum(weights, 0)
                if np.sum(weights) > 0:
                    weights = weights / np.sum(weights)
                else:
                    weights = np.ones(len(symbols)) / len(symbols)

            return {symbols[i]: float(weights[i]) for i in range(len(symbols))}
        except np.linalg.LinAlgError:
            return EqualWeightOptimizer().optimize(symbols)


@dataclass
class MeanVarianceOptimizer(WeightOptimizer):
    """均值-方差优化器

    求解 Markowitz 有效前沿上的组合：
    - 给定目标收益，最小化风险
    - 或给定风险厌恶系数，最大化效用

    Attributes:
        cov_estimator: 协方差估计器
        risk_aversion: 风险厌恶系数（gamma）
        target_return: 目标年化收益率
        constraints: 优化约束
        solver: cvxpy 求解器
    """

    cov_estimator: CovarianceEstimator = field(
        default_factory=SampleCovarianceEstimator
    )
    risk_aversion: float = 1.0
    target_return: float | None = None
    constraints: OptimizationConstraints = field(
        default_factory=OptimizationConstraints
    )
    solver: str | None = None

    def optimize(
        self,
        symbols: list[str],
        returns: Optional[dict[str, list[float]]] = None,
        covariance: Optional[dict[str, dict[str, float]]] = None,
        market_caps: Optional[dict[str, float]] = None,
        constraints: Optional[dict] = None,
    ) -> dict[str, float]:
        """计算均值-方差最优权重"""
        if not symbols:
            return {}

        n = len(symbols)

        if returns is None or not all(s in returns for s in symbols):
            logger.warning("缺少收益率数据，回退到等权")
            return EqualWeightOptimizer().optimize(symbols)

        # 计算预期收益（年化）
        expected_returns = np.zeros(n)
        for i, symbol in enumerate(symbols):
            ret = returns[symbol]
            if len(ret) < 2:
                return EqualWeightOptimizer().optimize(symbols)
            expected_returns[i] = np.mean(ret) * TRADING_DAYS_PER_YEAR

        # 构建协方差矩阵
        if covariance is not None:
            cov_matrix = np.zeros((n, n))
            for i, s1 in enumerate(symbols):
                for j, s2 in enumerate(symbols):
                    cov_matrix[i, j] = covariance.get(s1, {}).get(s2, 0.0)
        else:
            cov_matrix = self.cov_estimator.estimate_from_dict(returns, symbols)

        cov_matrix = cov_matrix * TRADING_DAYS_PER_YEAR  # 年化
        cov_matrix = ensure_positive_definite(cov_matrix)

        if not CVXPY_AVAILABLE:
            logger.warning("cvxpy 未安装，使用简化求解")
            return self._fallback_solve(symbols, expected_returns, cov_matrix)

        try:
            weights = self._cvxpy_solve(n, expected_returns, cov_matrix)
            return {symbols[i]: float(weights[i]) for i in range(n)}
        except Exception as e:
            logger.error(f"cvxpy 求解失败: {e}，回退到简化实现")
            return self._fallback_solve(symbols, expected_returns, cov_matrix)

    def _cvxpy_solve(
        self, n: int, expected_returns: np.ndarray, cov_matrix: np.ndarray
    ) -> np.ndarray:
        """使用 cvxpy 求解均值-方差优化"""
        w = _get_cvxpy().Variable(n)

        # 效用: U = μ^T w - γ/2 * w^T Σ w
        portfolio_return = expected_returns @ w
        portfolio_variance = _get_cvxpy().quad_form(w, cov_matrix)

        if self.target_return is not None:
            # 给定目标收益，最小化方差
            objective = _get_cvxpy().Minimize(portfolio_variance)
            constraint_list = [
                _get_cvxpy().sum(w) == 1,
                portfolio_return >= self.target_return,
            ]
        else:
            # 最大化效用
            utility = portfolio_return - (self.risk_aversion / 2) * portfolio_variance
            objective = _get_cvxpy().Maximize(utility)
            constraint_list = [_get_cvxpy().sum(w) == 1]

        if self.constraints.long_only:
            constraint_list.append(w >= self.constraints.min_weight)
        constraint_list.append(w <= self.constraints.max_weight)

        problem = _get_cvxpy().Problem(objective, constraint_list)
        problem.solve(solver=self.solver)

        if problem.status not in ["optimal", "optimal_inaccurate"]:
            raise ValueError(f"优化问题状态: {problem.status}")

        return w.value

    def _fallback_solve(
        self,
        symbols: list[str],
        expected_returns: np.ndarray,
        cov_matrix: np.ndarray,
    ) -> dict[str, float]:
        """回退求解"""
        try:
            inv_cov = np.linalg.inv(cov_matrix)
            ones = np.ones(len(symbols))

            # 效用最大化解析解
            # w* = (1/γ) * Σ^{-1} μ + λ * Σ^{-1} 1
            weights = (1 / self.risk_aversion) * (inv_cov @ expected_returns)
            # 归一化满足 sum(w) = 1
            weights = weights / np.sum(weights)

            if self.constraints.long_only:
                weights = np.maximum(weights, 0)
                if np.sum(weights) > 0:
                    weights = weights / np.sum(weights)
                else:
                    weights = np.ones(len(symbols)) / len(symbols)

            return {symbols[i]: float(weights[i]) for i in range(len(symbols))}
        except np.linalg.LinAlgError:
            return EqualWeightOptimizer().optimize(symbols)


@dataclass
class BlackLittermanOptimizer(WeightOptimizer):
    """Black-Litterman 模型优化器

    融合市场均衡观点与投资者主观观点。

    Attributes:
        cov_estimator: 协方差估计器
        risk_aversion: 市场风险厌恶系数
        tau: 不确定性参数（通常 0.01-0.05）
        views: 投资者观点 [(assets, weights, expected_return, confidence), ...]
    """

    cov_estimator: CovarianceEstimator = field(
        default_factory=SampleCovarianceEstimator
    )
    risk_aversion: float = 2.5
    tau: float = 0.05
    views: list[tuple[list[str], list[float], float, float]] = field(
        default_factory=list
    )
    constraints: OptimizationConstraints = field(
        default_factory=OptimizationConstraints
    )

    def optimize(
        self,
        symbols: list[str],
        returns: Optional[dict[str, list[float]]] = None,
        covariance: Optional[dict[str, dict[str, float]]] = None,
        market_caps: Optional[dict[str, float]] = None,
        constraints: Optional[dict] = None,
    ) -> dict[str, float]:
        """计算 Black-Litterman 最优权重"""
        if not symbols:
            return {}

        n = len(symbols)

        # 需要市值数据计算市场均衡
        if market_caps is None or not all(s in market_caps for s in symbols):
            logger.warning("缺少市值数据，回退到等权")
            return EqualWeightOptimizer().optimize(symbols)

        # 需要收益率数据估计协方差
        if returns is None or not all(s in returns for s in symbols):
            logger.warning("缺少收益率数据，回退到等权")
            return EqualWeightOptimizer().optimize(symbols)

        # 市场权重
        total_cap = sum(market_caps[s] for s in symbols)
        market_weights = np.array([market_caps[s] / total_cap for s in symbols])

        # 协方差矩阵
        cov_matrix = self.cov_estimator.estimate_from_dict(returns, symbols)
        cov_matrix = cov_matrix * TRADING_DAYS_PER_YEAR  # 年化
        cov_matrix = ensure_positive_definite(cov_matrix)

        # 隐含均衡收益: π = γ * Σ * w_mkt
        implied_returns = self.risk_aversion * cov_matrix @ market_weights

        # 如果没有观点，使用隐含收益
        if not self.views:
            # 使用隐含收益做均值-方差优化
            optimizer = MeanVarianceOptimizer(
                cov_estimator=self.cov_estimator,
                risk_aversion=self.risk_aversion,
            )
            # 直接使用市场权重
            return {symbols[i]: float(market_weights[i]) for i in range(n)}

        # 构建观点矩阵 P 和观点向量 Q
        k = len(self.views)  # 观点数量
        P = np.zeros((k, n))
        Q = np.zeros(k)
        Omega_diag = np.zeros(k)  # 观点置信度矩阵（对角）

        for v_idx, (assets, weights, exp_ret, confidence) in enumerate(self.views):
            for a_idx, asset in enumerate(assets):
                if asset in symbols:
                    s_idx = symbols.index(asset)
                    P[v_idx, s_idx] = weights[a_idx]
            Q[v_idx] = exp_ret
            # Omega = (1/c) * P * τΣ * P^T 对角元素
            view_var = P[v_idx] @ (self.tau * cov_matrix) @ P[v_idx]
            Omega_diag[v_idx] = view_var / confidence if confidence > 0 else view_var

        Omega = np.diag(Omega_diag)

        # Black-Litterman 公式
        # E[R] = [(τΣ)^{-1} + P^T Ω^{-1} P]^{-1} * [(τΣ)^{-1} π + P^T Ω^{-1} Q]
        tau_sigma = self.tau * cov_matrix
        try:
            inv_tau_sigma = np.linalg.inv(tau_sigma)
            inv_omega = np.linalg.inv(Omega)

            M = inv_tau_sigma + P.T @ inv_omega @ P
            inv_M = np.linalg.inv(M)

            bl_returns = inv_M @ (inv_tau_sigma @ implied_returns + P.T @ inv_omega @ Q)
        except np.linalg.LinAlgError:
            logger.warning("BL 矩阵求逆失败，使用市场权重")
            return {symbols[i]: float(market_weights[i]) for i in range(n)}

        # 使用 BL 预期收益做均值-方差优化
        # w* = (1/γ) * Σ^{-1} * E[R]
        try:
            inv_cov = np.linalg.inv(cov_matrix)
            weights = (1 / self.risk_aversion) * inv_cov @ bl_returns
            weights = weights / np.sum(weights)

            if self.constraints.long_only:
                weights = np.maximum(weights, 0)
                if np.sum(weights) > 0:
                    weights = weights / np.sum(weights)
                else:
                    weights = market_weights
        except np.linalg.LinAlgError:
            weights = market_weights

        return {symbols[i]: float(weights[i]) for i in range(n)}


def create_optimizer(
    method: str,
    **kwargs: Any,
) -> WeightOptimizer:
    """工厂函数：创建优化器

    Args:
        method: 优化方法名称
        **kwargs: 优化器参数

    Returns:
        WeightOptimizer 实例

    Examples:
        optimizer = create_optimizer("min_variance")
        optimizer = create_optimizer("risk_parity", max_iterations=1000)
        optimizer = create_optimizer("max_sharpe", risk_free_rate=0.02)
    """
    method_lower = method.lower().replace("-", "_").replace(" ", "_")

    if method_lower in ("equal", "equal_weight", "ew"):
        return EqualWeightOptimizer()

    if method_lower in ("market_cap", "market_cap_weight", "mcw"):
        from aipha.portfolio.optimizer import MarketCapWeightOptimizer

        return MarketCapWeightOptimizer()

    if method_lower in ("min_variance", "min_var", "mv"):
        return ConvexMinVarianceOptimizer(**kwargs)

    if method_lower in ("risk_parity", "rp"):
        return ConvexRiskParityOptimizer(**kwargs)

    if method_lower in ("max_sharpe", "tangency"):
        return ConvexMaxSharpeOptimizer(**kwargs)

    if method_lower in ("mean_variance", "markowitz"):
        return MeanVarianceOptimizer(**kwargs)

    if method_lower in ("black_litterman", "bl"):
        return BlackLittermanOptimizer(**kwargs)

    raise ValueError(f"未知优化方法: {method}")


def check_cvar_constraint(
    weights: dict[str, float],
    returns: dict[str, list[float]],
    max_cvar: float = 0.03,
    confidence: float = 0.95,
) -> dict:
    """检查组合 CVaR 是否满足约束 (原则E: CVaR(95%) < 3.0%).

    Args:
        weights: {symbol: weight}
        returns: {symbol: [daily_returns]}
        max_cvar: CVaR 上限 (正数)
        confidence: 置信度

    Returns:
        {"pass": bool, "cvar": float, "limit": float, "msg": str}
    """
    from aipha.risk.euler_decomposition import EulerDecomposer

    symbols = list(weights.keys())
    n = len(symbols)
    if n == 0:
        return {"pass": True, "cvar": 0.0, "limit": max_cvar, "msg": "空组合"}

    w = [weights[s] for s in symbols]
    n_obs = min(len(returns.get(s, [])) for s in symbols)
    if n_obs < 2:
        return {"pass": False, "cvar": float("inf"), "limit": max_cvar,
                "msg": "收益率数据不足"}

    # 构建协方差矩阵
    means = [np.mean(returns[s][:n_obs]) for s in symbols]
    cov = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            ri = returns[symbols[i]][:n_obs]
            rj = returns[symbols[j]][:n_obs]
            cov[i, j] = np.mean(
                [(ri[t] - means[i]) * (rj[t] - means[j]) for t in range(n_obs)]
            ) * n_obs / (n_obs - 1)

    cov_list = cov.tolist()
    decomposer = EulerDecomposer()
    result = decomposer.decompose_cvar(w, cov_list, confidence, symbols)
    passed = result.total_risk <= max_cvar
    return {
        "pass": passed,
        "cvar": result.total_risk,
        "limit": max_cvar,
        "contributions": result.pct_contributions,
        "msg": f"CVaR={result.total_risk:.4f} {'≤' if passed else '>'} {max_cvar}",
    }
