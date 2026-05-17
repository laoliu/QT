"""
策略分散化分析器

系统性评估策略间的相关性与分散化效果，帮助选择最优策略组合。

核心功能：
- 策略收益相关性矩阵计算
- 边际分散化贡献评估（Marginal Diversification Benefit）
- 基于相关性的最优策略子集筛选
- 策略冗余检测（高度正相关的策略对）
- 组合分散化比率计算

使用示例：
    analyzer = DiversificationAnalyzer()
    analyzer.add_strategy("small_size", small_size_nav)
    analyzer.add_strategy("new_bond", new_bond_nav)
    analyzer.add_strategy("triple_low", triple_low_nav)

    report = analyzer.compute_diversification_report()
    print(report.summary())

    # 筛选最优的 3 个策略
    best = analyzer.select_best_subset(n=3, base_strategies=["small_size"])
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from itertools import combinations
from typing import Any

import numpy as np
from numpy.typing import NDArray

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StrategyPairCorrelation:
    """策略对相关性"""

    strategy_a: str
    strategy_b: str
    correlation: float
    is_redundant: bool  # True 表示相关性超过冗余阈值


@dataclass
class MarginalDiversification:
    """边际分散化贡献"""

    strategy_name: str
    base_portfolio_sharpe: float
    new_portfolio_sharpe: float
    sharpe_improvement: float  # 加入后 Sharpe 提升比例
    avg_correlation_with_base: float  # 与基准组合的平均相关性
    diversification_score: float  # 综合分散化得分（越高越好）


@dataclass
class DiversificationReport:
    """分散化分析报告"""

    strategy_names: list[str]
    correlation_matrix: NDArray
    pairwise_correlations: list[StrategyPairCorrelation]
    avg_pairwise_correlation: float
    diversification_ratio: float  # 组合分散化比率 = sum(w*σ) / σ_port
    redundant_pairs: list[StrategyPairCorrelation]  # 高相关性对
    marginal_contributions: list[MarginalDiversification]
    strategy_volatilities: dict[str, float]
    strategy_sharpes: dict[str, float]

    def summary(self) -> str:
        """生成文本摘要"""
        lines = [
            "=" * 70,
            "策略分散化分析报告",
            "=" * 70,
            "",
            f"策略数量: {len(self.strategy_names)}",
            f"平均两两相关性: {self.avg_pairwise_correlation:.4f}",
            f"分散化比率: {self.diversification_ratio:.4f}",
            "",
            "【各策略指标】",
        ]

        for name in self.strategy_names:
            vol = self.strategy_volatilities.get(name, 0)
            sharpe = self.strategy_sharpes.get(name, 0)
            lines.append(f"  {name}: 波动率={vol:.2%}, 夏普={sharpe:.2f}")

        lines.extend(["", "【相关性矩阵】"])
        header = "            " + "  ".join(f"{n[:10]:>10}" for n in self.strategy_names)
        lines.append(header)
        for i, name in enumerate(self.strategy_names):
            row_vals = "  ".join(
                f"{self.correlation_matrix[i, j]:10.4f}"
                for j in range(len(self.strategy_names))
            )
            lines.append(f"{name[:10]:>10}  {row_vals}")

        if self.redundant_pairs:
            lines.extend(["", "【冗余警告: 高相关性策略对】"])
            for pair in self.redundant_pairs:
                lines.append(
                    f"  {pair.strategy_a} <-> {pair.strategy_b}: "
                    f"ρ={pair.correlation:.4f}"
                )

        if self.marginal_contributions:
            lines.extend(["", "【边际分散化贡献】"])
            sorted_mc = sorted(
                self.marginal_contributions,
                key=lambda x: x.diversification_score,
                reverse=True,
            )
            for mc in sorted_mc:
                lines.append(
                    f"  {mc.strategy_name}: "
                    f"分散化得分={mc.diversification_score:.4f}, "
                    f"Sharpe提升={mc.sharpe_improvement:+.4f}, "
                    f"平均相关性={mc.avg_correlation_with_base:.4f}"
                )

        lines.extend(["", "=" * 70])
        return "\n".join(lines)


class DiversificationAnalyzer:
    """策略分散化分析器

    通过日频收益率的统计特征，评估策略间的分散化效果。
    """

    def __init__(
        self,
        risk_free_rate: float = 0.02,
        trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
        redundancy_threshold: float = 0.85,
    ):
        """初始化

        Args:
            risk_free_rate: 无风险利率
            trading_days_per_year: 年交易日数
            redundancy_threshold: 相关性超过此阈值视为冗余
        """
        self._navs: dict[str, dict[date, float]] = {}
        self._risk_free_rate = risk_free_rate
        self._trading_days = trading_days_per_year
        self._redundancy_threshold = redundancy_threshold

    def add_strategy(self, name: str, nav: dict[date, float]) -> None:
        """添加策略 NAV 序列

        Args:
            name: 策略名称
            nav: 净值序列 {date: nav_value}
        """
        if len(nav) < 2:
            raise ValueError(f"策略 '{name}' NAV 序列太短（至少需要 2 个交易日）")
        self._navs[name] = nav

    def remove_strategy(self, name: str) -> None:
        """移除策略"""
        self._navs.pop(name, None)

    @property
    def strategy_names(self) -> list[str]:
        """已添加的策略名称"""
        return list(self._navs.keys())

    def _get_common_dates(self) -> list[date]:
        """获取所有策略的公共日期"""
        if not self._navs:
            return []
        common = set.intersection(*(set(nav.keys()) for nav in self._navs.values()))
        return sorted(common)

    def _compute_returns_matrix(
        self, dates: list[date],
    ) -> tuple[list[str], NDArray]:
        """计算收益率矩阵

        Returns:
            (策略名称列表, 收益率矩阵 shape=(n_strategies, n_days-1))
        """
        names = list(self._navs.keys())
        n = len(names)
        t = len(dates)

        if t < 2:
            return names, np.empty((n, 0))

        matrix = np.zeros((n, t - 1))
        for i, name in enumerate(names):
            nav = self._navs[name]
            for j in range(t - 1):
                prev_val = nav[dates[j]]
                curr_val = nav[dates[j + 1]]
                matrix[i, j] = (curr_val / prev_val - 1) if prev_val > 0 else 0.0

        return names, matrix

    def compute_correlation_matrix(self) -> tuple[list[str], NDArray]:
        """计算策略相关性矩阵

        Returns:
            (策略名称列表, 相关性矩阵 shape=(n, n))
        """
        dates = self._get_common_dates()
        names, returns = self._compute_returns_matrix(dates)

        if returns.shape[1] < 2:
            n = len(names)
            return names, np.eye(n)

        with np.errstate(invalid='ignore'):
            corr = np.corrcoef(returns)
        # corrcoef 对单策略返回标量，需升维为 2D
        if corr.ndim < 2:
            corr = np.array([[float(corr)]])
        # 零方差策略 → NaN 相关性，替换为 0（无线性相关）
        corr = np.nan_to_num(corr, nan=0.0)
        np.fill_diagonal(corr, 1.0)
        return names, corr

    def compute_pairwise_correlations(self) -> list[StrategyPairCorrelation]:
        """计算所有策略对的相关性"""
        names, corr_matrix = self.compute_correlation_matrix()
        pairs = []

        for i, j in combinations(range(len(names)), 2):
            rho = float(corr_matrix[i, j])
            pairs.append(StrategyPairCorrelation(
                strategy_a=names[i],
                strategy_b=names[j],
                correlation=rho,
                is_redundant=rho >= self._redundancy_threshold,
            ))

        return pairs

    def _compute_strategy_metrics(
        self, returns: NDArray,
    ) -> tuple[float, float]:
        """计算单策略波动率和夏普

        Returns:
            (年化波动率, 年化夏普)
        """
        if len(returns) < 2:
            return 0.0, 0.0

        vol = float(np.std(returns, ddof=1) * np.sqrt(self._trading_days))

        daily_rf = self._risk_free_rate / self._trading_days
        excess = returns - daily_rf
        if vol > 0:
            sharpe = float(
                np.mean(excess) * np.sqrt(self._trading_days)
                / np.std(returns, ddof=1)
            )
        else:
            sharpe = 0.0

        return vol, sharpe

    def _compute_portfolio_sharpe(
        self,
        returns_matrix: NDArray,
        weights: NDArray,
    ) -> float:
        """计算等权组合的夏普"""
        portfolio_returns = weights @ returns_matrix
        _, sharpe = self._compute_strategy_metrics(portfolio_returns)
        return sharpe

    def compute_diversification_ratio(
        self,
        weights: dict[str, float] | None = None,
    ) -> float:
        """计算分散化比率

        DR = sum(w_i * σ_i) / σ_portfolio
        DR > 1 说明分散化有效，DR = 1 表示完全正相关（无分散化效果）

        Args:
            weights: 策略权重，None 则等权

        Returns:
            分散化比率
        """
        dates = self._get_common_dates()
        names, returns = self._compute_returns_matrix(dates)
        n = len(names)

        if n == 0 or returns.shape[1] < 2:
            return 1.0

        if weights is None:
            w = np.ones(n) / n
        else:
            w = np.array([weights.get(name, 1.0 / n) for name in names])
            w_sum = w.sum()
            if w_sum > 0:
                w = w / w_sum

        # 各策略波动率
        vols = np.array(
            [float(np.std(returns[i], ddof=1)) for i in range(n)]
        )

        # 加权平均波动率
        weighted_vol_sum = float(np.dot(w, vols))

        # 组合波动率
        portfolio_returns = w @ returns
        port_vol = float(np.std(portfolio_returns, ddof=1))

        if port_vol < 1e-12:
            return 1.0

        return weighted_vol_sum / port_vol

    def compute_marginal_diversification(
        self,
        base_strategies: list[str],
        candidate_strategies: list[str] | None = None,
    ) -> list[MarginalDiversification]:
        """计算候选策略相对于基准组合的边际分散化贡献

        Args:
            base_strategies: 基准组合中的策略
            candidate_strategies: 待评估的候选策略，None 则取所有非基准策略

        Returns:
            各候选策略的边际分散化贡献
        """
        if candidate_strategies is None:
            candidate_strategies = [
                n for n in self._navs if n not in base_strategies
            ]

        dates = self._get_common_dates()
        names, returns = self._compute_returns_matrix(dates)

        if returns.shape[1] < 2:
            return []

        # 基准组合等权 Sharpe
        base_indices = [names.index(s) for s in base_strategies if s in names]
        if not base_indices:
            return []

        n_base = len(base_indices)
        base_w = np.zeros(len(names))
        for idx in base_indices:
            base_w[idx] = 1.0 / n_base

        base_sharpe = self._compute_portfolio_sharpe(returns, base_w)

        # 各候选策略和基准的平均相关性
        _, corr_matrix = self.compute_correlation_matrix()

        results = []
        for cand_name in candidate_strategies:
            if cand_name not in names:
                continue

            cand_idx = names.index(cand_name)

            # 基准+候选 等权
            new_indices = base_indices + [cand_idx]
            n_new = len(new_indices)
            new_w = np.zeros(len(names))
            for idx in new_indices:
                new_w[idx] = 1.0 / n_new

            new_sharpe = self._compute_portfolio_sharpe(returns, new_w)
            sharpe_improvement = new_sharpe - base_sharpe

            # 候选与基准策略的平均相关性
            avg_corr = float(np.mean(
                [corr_matrix[cand_idx, bi] for bi in base_indices]
            ))

            # 分散化得分：Sharpe 提升 × (1 - 平均相关性)
            # 高 Sharpe 提升 + 低相关性 = 高分散化价值
            diversification_score = sharpe_improvement * max(0.01, 1.0 - avg_corr)

            results.append(MarginalDiversification(
                strategy_name=cand_name,
                base_portfolio_sharpe=base_sharpe,
                new_portfolio_sharpe=new_sharpe,
                sharpe_improvement=sharpe_improvement,
                avg_correlation_with_base=avg_corr,
                diversification_score=diversification_score,
            ))

        return results

    def select_best_subset(
        self,
        n: int,
        base_strategies: list[str] | None = None,
        maximize: str = "sharpe",
    ) -> list[str]:
        """贪心选择最优策略子集

        从所有可用策略中贪心选择 n 个策略组成组合，最大化目标指标。

        Args:
            n: 目标组合策略数量
            base_strategies: 必须包含的基础策略
            maximize: 优化目标 ("sharpe" | "diversification_ratio")

        Returns:
            选中策略名称列表
        """
        if n <= 0:
            return []

        selected = list(base_strategies or [])
        remaining = [s for s in self._navs if s not in selected]

        dates = self._get_common_dates()
        names, returns = self._compute_returns_matrix(dates)

        if returns.shape[1] < 2:
            return selected[:n]

        while len(selected) < n and remaining:
            best_name = None
            best_score = float("-inf")

            for cand in remaining:
                if cand not in names:
                    continue

                trial = selected + [cand]
                trial_indices = [names.index(s) for s in trial if s in names]

                if not trial_indices:
                    continue

                n_trial = len(trial_indices)
                w = np.zeros(len(names))
                for idx in trial_indices:
                    w[idx] = 1.0 / n_trial

                if maximize == "sharpe":
                    score = self._compute_portfolio_sharpe(returns, w)
                elif maximize == "diversification_ratio":
                    weight_dict = {names[idx]: w[idx] for idx in trial_indices}
                    score = self.compute_diversification_ratio(weight_dict)
                else:
                    score = self._compute_portfolio_sharpe(returns, w)

                if score > best_score:
                    best_score = score
                    best_name = cand

            if best_name is None:
                break

            selected.append(best_name)
            remaining.remove(best_name)

        return selected

    def compute_diversification_report(
        self,
        base_strategies: list[str] | None = None,
    ) -> DiversificationReport:
        """生成完整的分散化分析报告

        Args:
            base_strategies: 基准策略（用于计算边际贡献），
                           None 则使用所有策略

        Returns:
            DiversificationReport
        """
        dates = self._get_common_dates()
        names, returns = self._compute_returns_matrix(dates)

        # 相关性矩阵
        _, corr_matrix = self.compute_correlation_matrix()

        # 两两相关性
        pairwise = self.compute_pairwise_correlations()
        redundant = [p for p in pairwise if p.is_redundant]

        # 平均两两相关性
        if pairwise:
            avg_corr = float(np.mean([p.correlation for p in pairwise]))
        else:
            avg_corr = 0.0

        # 分散化比率
        dr = self.compute_diversification_ratio()

        # 各策略指标
        vols: dict[str, float] = {}
        sharpes: dict[str, float] = {}
        for i, name in enumerate(names):
            if returns.shape[1] >= 2:
                v, s = self._compute_strategy_metrics(returns[i])
            else:
                v, s = 0.0, 0.0
            vols[name] = v
            sharpes[name] = s

        # 边际分散化贡献
        if base_strategies:
            marginal = self.compute_marginal_diversification(
                base_strategies=base_strategies,
            )
        else:
            marginal = []

        return DiversificationReport(
            strategy_names=names,
            correlation_matrix=corr_matrix,
            pairwise_correlations=pairwise,
            avg_pairwise_correlation=avg_corr,
            diversification_ratio=dr,
            redundant_pairs=redundant,
            marginal_contributions=marginal,
            strategy_volatilities=vols,
            strategy_sharpes=sharpes,
        )
