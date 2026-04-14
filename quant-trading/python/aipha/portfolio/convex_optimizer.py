"""凸优化器 — 基于 CVXPY 的最小方差 / 风险平价 / 最大夏普"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from aipha.portfolio.optimizer import PortfolioOptimizer as WeightOptimizer

logger = logging.getLogger(__name__)

try:
    import cvxpy as cp
    CVXPY_AVAILABLE = True
except ImportError:
    CVXPY_AVAILABLE = False


@dataclass
class OptimizationConstraints:
    min_weight: float = 0.0
    max_weight: float = 1.0
    long_only: bool = True
    target_return: float | None = None
    max_turnover: float | None = None
    sector_limits: dict[str, float] = field(default_factory=dict)
    max_cvar_95: float | None = None


@dataclass
class ConvexMinVarianceOptimizer(WeightOptimizer):
    """最小方差优化"""
    constraints: OptimizationConstraints = field(default_factory=OptimizationConstraints)
    solver: str | None = None

    def optimize(
        self,
        symbols: list[str],
        returns: np.ndarray | None = None,
        covariance: np.ndarray | None = None,
        market_caps: dict[str, float] | None = None,
        prev_weights: dict[str, float] | None = None,
        sector_map: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> dict[str, float]:
        n = len(symbols)
        if n == 0:
            return {}
        if covariance is None:
            # 等权 fallback
            w = 1.0 / n
            return {s: w for s in symbols}
        cov = np.array(covariance)
        if CVXPY_AVAILABLE:
            return self._cvxpy_solve(n, cov, symbols, prev_weights, sector_map)
        return self._fallback_solve(n, cov, symbols)

    def _cvxpy_solve(
        self, n: int, cov: np.ndarray, symbols: list[str],
        prev_weights: dict[str, float] | None, sector_map: dict[str, str] | None,
    ) -> dict[str, float]:
        w = cp.Variable(n)
        constraints = [cp.sum(w) == 1]
        c = self.constraints
        if c.long_only:
            constraints.append(w >= c.min_weight)
        else:
            constraints.append(w >= -c.max_weight)
        constraints.append(w <= c.max_weight)

        if c.max_turnover is not None and prev_weights is not None:
            w_prev = np.array([prev_weights.get(s, 0) for s in symbols])
            constraints.append(cp.norm(w - w_prev, 1) <= c.max_turnover)

        if sector_map and c.sector_limits:
            sectors: dict[str, list[int]] = {}
            for i, s in enumerate(symbols):
                sec = sector_map.get(s, "other")
                sectors.setdefault(sec, []).append(i)
            for sec, limit in c.sector_limits.items():
                if sec in sectors:
                    idx = sectors[sec]
                    constraints.append(cp.sum(w[idx]) <= limit)

        prob = cp.Problem(cp.Minimize(cp.quad_form(w, cov)), constraints)
        try:
            prob.solve(solver=self.solver)
            if w.value is not None:
                weights = np.maximum(w.value, 0)
                weights /= weights.sum()
                return {s: float(weights[i]) for i, s in enumerate(symbols)}
        except Exception as e:
            logger.warning("CVXPY solve failed: %s, falling back", e)
        return self._fallback_solve(len(symbols), cov, symbols)

    def _fallback_solve(self, n: int, cov: np.ndarray, symbols: list[str]) -> dict[str, float]:
        """最小方差的解析近似: w ∝ Σ^-1 · 1"""
        try:
            inv_cov = np.linalg.inv(cov)
            ones = np.ones(n)
            w = inv_cov @ ones
            w = np.maximum(w, 0)
            total = w.sum()
            if total > 0:
                w /= total
            else:
                w = np.ones(n) / n
        except np.linalg.LinAlgError:
            w = np.ones(n) / n
        return {s: float(w[i]) for i, s in enumerate(symbols)}


@dataclass
class ConvexRiskParityOptimizer(WeightOptimizer):
    """风险平价优化 (凸近似)"""
    constraints: OptimizationConstraints = field(default_factory=OptimizationConstraints)

    def optimize(
        self, symbols: list[str], covariance: np.ndarray | None = None, **kwargs: Any,
    ) -> dict[str, float]:
        n = len(symbols)
        if n == 0:
            return {}
        if covariance is None:
            return {s: 1.0 / n for s in symbols}
        cov = np.array(covariance)
        # 逆波动率作为风险平价近似
        diag = np.diag(cov)
        inv_vol = 1.0 / np.sqrt(np.maximum(diag, 1e-10))
        w = inv_vol / inv_vol.sum()
        return {s: float(w[i]) for i, s in enumerate(symbols)}


@dataclass
class ConvexMaxSharpeOptimizer(WeightOptimizer):
    """最大夏普比率优化"""
    risk_free_rate: float = 0.03
    constraints: OptimizationConstraints = field(default_factory=OptimizationConstraints)

    def optimize(
        self, symbols: list[str], returns: np.ndarray | None = None,
        covariance: np.ndarray | None = None, **kwargs: Any,
    ) -> dict[str, float]:
        n = len(symbols)
        if n == 0:
            return {}
        if returns is None or covariance is None:
            return {s: 1.0 / n for s in symbols}
        mu = np.mean(returns, axis=0) * 252 - self.risk_free_rate
        cov = np.array(covariance)
        try:
            inv_cov = np.linalg.inv(cov)
            w = inv_cov @ mu
            w = np.maximum(w, 0)
            total = w.sum()
            if total > 0:
                w /= total
            else:
                w = np.ones(n) / n
        except np.linalg.LinAlgError:
            w = np.ones(n) / n
        return {s: float(w[i]) for i, s in enumerate(symbols)}
