"""分散化分析 — 策略相关性 / 冗余检测 / 边际分散化贡献"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations
from typing import Any

import numpy as np
from numpy.typing import NDArray

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StrategyPairCorrelation:
    strategy_a: str
    strategy_b: str
    correlation: float
    is_redundant: bool


@dataclass
class MarginalDiversification:
    strategy_name: str
    base_portfolio_sharpe: float
    new_portfolio_sharpe: float
    sharpe_improvement: float
    avg_correlation_with_base: float
    diversification_score: float


@dataclass
class DiversificationReport:
    strategy_names: list[str]
    correlation_matrix: NDArray
    pairwise_correlations: list[StrategyPairCorrelation]
    avg_pairwise_correlation: float
    diversification_ratio: float
    redundant_pairs: list[StrategyPairCorrelation]
    marginal_contributions: list[MarginalDiversification]
    strategy_volatilities: dict[str, float]
    strategy_sharpes: dict[str, float]

    def summary(self) -> str:
        lines = [
            f"分散化分析报告 ({len(self.strategy_names)} 个策略)",
            "",
            "相关性矩阵:",
        ]
        for i, name in enumerate(self.strategy_names):
            row_vals = " ".join(f"{self.correlation_matrix[i, j]:.2f}" for j in range(len(self.strategy_names)))
            lines.append(f"  {name}: [{row_vals}]")
        lines.append("")
        lines.append(f"平均两两相关性: {self.avg_pairwise_correlation:.3f}")
        lines.append(f"分散化比率: {self.diversification_ratio:.3f}")
        lines.append(f"冗余策略对: {len(self.redundant_pairs)}")
        if self.marginal_contributions:
            lines.append("")
            lines.append("边际分散化贡献:")
            for mc in self.marginal_contributions:
                lines.append(f"  {mc.strategy_name}: Sharpe改善={mc.sharpe_improvement:.3f}, 平均相关={mc.avg_correlation_with_base:.3f}")
        return "\n".join(lines)


class DiversificationAnalyzer:
    """策略分散化分析器"""

    def __init__(
        self,
        risk_free_rate: float = 0.02,
        trading_days_per_year: int = 252,
        redundancy_threshold: float = 0.85,
    ):
        self._rf = risk_free_rate
        self._tpy = trading_days_per_year
        self._threshold = redundancy_threshold
        self._navs: dict[str, dict[date, float]] = {}

    def add_strategy(self, name: str, nav: dict[date, float]) -> None:
        if len(nav) < 2:
            raise ValueError(f"策略 '{name}' 的净值序列太短（至少需要2个数据点）")
        self._navs[name] = nav

    def remove_strategy(self, name: str) -> None:
        self._navs.pop(name, None)

    @property
    def strategy_names(self) -> list[str]:
        return list(self._navs.keys())

    def _get_common_dates(self) -> list[date]:
        if not self._navs:
            return []
        common = set.intersection(*(set(n.keys()) for n in self._navs.values()))
        return sorted(common)

    def _compute_returns_matrix(self, dates: list[date]) -> tuple[list[str], NDArray]:
        names = list(self._navs.keys())
        n = len(dates)
        mat = np.zeros((len(names), n - 1))
        for i, name in enumerate(names):
            nav = self._navs[name]
            vals = [nav[d] for d in dates]
            for j in range(1, n):
                mat[i, j - 1] = vals[j] / vals[j - 1] - 1 if vals[j - 1] > 0 else 0
        return names, mat

    def compute_correlation_matrix(self) -> tuple[list[str], NDArray]:
        dates = self._get_common_dates()
        names = list(self._navs.keys())
        if len(names) == 1:
            # Single strategy: correlation is trivially 1.0
            if len(dates) >= 2:
                return names, np.array([[1.0]])
            return [], np.array([])
        if len(dates) < 3:
            return [], np.array([])
        names, mat = self._compute_returns_matrix(dates)
        corr = np.corrcoef(mat)
        if corr.ndim == 0:
            corr = np.array([[float(corr)]])
        return names, corr

    def compute_pairwise_correlations(self) -> list[StrategyPairCorrelation]:
        names, corr = self.compute_correlation_matrix()
        pairs: list[StrategyPairCorrelation] = []
        for i, j in combinations(range(len(names)), 2):
            c = float(corr[i, j])
            pairs.append(StrategyPairCorrelation(names[i], names[j], c, c >= self._threshold))
        return sorted(pairs, key=lambda p: p.correlation, reverse=True)

    def _compute_strategy_metrics(self, returns: NDArray) -> tuple[float, float]:
        mu = float(np.mean(returns))
        std = float(np.std(returns, ddof=1))
        vol = std * np.sqrt(self._tpy)
        sharpe = (mu * self._tpy - self._rf) / vol if vol > 0 else 0.0
        return vol, sharpe

    def _compute_portfolio_sharpe(self, returns_matrix: NDArray, weights: NDArray) -> float:
        port_ret = returns_matrix.T @ weights
        mu = float(np.mean(port_ret))
        std = float(np.std(port_ret, ddof=1))
        vol = std * np.sqrt(self._tpy)
        return (mu * self._tpy - self._rf) / vol if vol > 0 else 0.0

    def compute_diversification_ratio(self, weights: dict[str, float] | None = None) -> float:
        dates = self._get_common_dates()
        if len(dates) < 3:
            return 1.0
        names, mat = self._compute_returns_matrix(dates)
        if weights is None:
            w = np.ones(len(names)) / len(names)
        else:
            w = np.array([weights.get(n, 0) for n in names])
            if w.sum() > 0:
                w /= w.sum()
            else:
                w = np.ones(len(names)) / len(names)
        vols = np.std(mat, axis=1, ddof=1)
        port_vol = float(np.std(mat.T @ w, ddof=1))
        weighted_vol = float(np.dot(w, vols))
        return weighted_vol / port_vol if port_vol > 0 else 1.0

    def compute_marginal_diversification(
        self,
        base_strategies: list[str],
        candidate_strategies: list[str] | None = None,
    ) -> list[MarginalDiversification]:
        dates = self._get_common_dates()
        if len(dates) < 3:
            return []
        names, mat = self._compute_returns_matrix(dates)
        name_idx = {n: i for i, n in enumerate(names)}

        base_idx = [name_idx[n] for n in base_strategies if n in name_idx]
        if not base_idx:
            return []
        base_w = np.zeros(len(names))
        for i in base_idx:
            base_w[i] = 1.0 / len(base_idx)
        base_sharpe = self._compute_portfolio_sharpe(mat, base_w)

        candidates = candidate_strategies or [n for n in names if n not in base_strategies]
        results: list[MarginalDiversification] = []
        for cand in candidates:
            if cand not in name_idx:
                continue
            ci = name_idx[cand]
            new_w = base_w.copy()
            # Add candidate at 20% weight
            new_w = new_w * 0.8
            new_w[ci] = 0.2
            new_sharpe = self._compute_portfolio_sharpe(mat, new_w)
            corr_matrix = np.corrcoef(mat)
            avg_corr = float(np.mean([corr_matrix[ci, bi] for bi in base_idx]))
            improvement = new_sharpe - base_sharpe
            results.append(MarginalDiversification(
                cand, base_sharpe, new_sharpe, improvement, avg_corr,
                diversification_score=improvement - avg_corr * 0.5,
            ))
        return sorted(results, key=lambda m: m.sharpe_improvement, reverse=True)

    def compute_diversification_report(
        self, base_strategies: list[str] | None = None,
    ) -> DiversificationReport:
        """生成完整的分散化分析报告"""
        names, corr = self.compute_correlation_matrix()
        pairs = self.compute_pairwise_correlations()
        dr = self.compute_diversification_ratio()

        # Compute strategy metrics
        dates = self._get_common_dates()
        strategy_vols: dict[str, float] = {}
        strategy_sharpes: dict[str, float] = {}
        if len(dates) >= 3:
            all_names, mat = self._compute_returns_matrix(dates)
            for i, n_ in enumerate(all_names):
                vol, sharpe = self._compute_strategy_metrics(mat[i])
                strategy_vols[n_] = vol
                strategy_sharpes[n_] = sharpe
        else:
            for n_ in self.strategy_names:
                strategy_vols[n_] = 0.0
                strategy_sharpes[n_] = 0.0

        # Marginal contributions
        marginal: list[MarginalDiversification] = []
        if base_strategies:
            marginal = self.compute_marginal_diversification(base_strategies)

        # Redundant pairs
        redundant = [p for p in pairs if p.is_redundant]

        # Avg pairwise correlation
        if pairs:
            avg_corr = float(np.mean([p.correlation for p in pairs]))
        else:
            avg_corr = 0.0

        # Handle empty correlation matrix
        if corr.size == 0:
            corr = np.eye(len(self.strategy_names))
            names = self.strategy_names

        return DiversificationReport(
            strategy_names=names,
            correlation_matrix=corr,
            pairwise_correlations=pairs,
            avg_pairwise_correlation=avg_corr,
            diversification_ratio=dr,
            redundant_pairs=redundant,
            marginal_contributions=marginal,
            strategy_volatilities=strategy_vols,
            strategy_sharpes=strategy_sharpes,
        )

    def select_best_subset(
        self, n: int, base_strategies: list[str] | None = None,
        maximize: str = "sharpe",
    ) -> list[str]:
        """贪心选择 n 个最互补的策略"""
        selected = list(base_strategies or [])
        remaining = [s for s in self.strategy_names if s not in selected]
        # Seed with first available strategy if no base given
        if not selected and remaining and n > 0:
            selected.append(remaining.pop(0))
        while len(selected) < n and remaining:
            contribs = self.compute_marginal_diversification(selected, remaining)
            if not contribs:
                break
            best = contribs[0].strategy_name
            selected.append(best)
            remaining.remove(best)
        return selected

    def compute_diversification_report(
        self, base_strategies: list[str] | None = None,
    ) -> DiversificationReport:
        names, corr = self.compute_correlation_matrix()
        pairs = self.compute_pairwise_correlations()
        avg_corr = np.mean([p.correlation for p in pairs]) if pairs else 0.0
        redundant = [p for p in pairs if p.is_redundant]
        div_ratio = self.compute_diversification_ratio()
        base = base_strategies or names
        marginal = self.compute_marginal_diversification(base) if len(names) > 1 else []

        dates = self._get_common_dates()
        vols: dict[str, float] = {}
        sharpes: dict[str, float] = {}
        if len(dates) >= 3:
            _, mat = self._compute_returns_matrix(dates)
            for i, n in enumerate(names):
                v, s = self._compute_strategy_metrics(mat[i])
                vols[n] = v
                sharpes[n] = s

        return DiversificationReport(
            names, corr, pairs, float(avg_corr), div_ratio, redundant,
            marginal, vols, sharpes,
        )
