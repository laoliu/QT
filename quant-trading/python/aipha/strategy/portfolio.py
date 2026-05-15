"""策略组合管理 — 多策略净值合成 / 绩效分析 / 风险平价"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
from numpy.typing import NDArray


@dataclass
class StrategyInfo:
    name: str
    nav: dict[date, float]
    weight: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyMetrics:
    name: str
    total_return: float
    annualized_return: float
    volatility: float
    sharpe: float
    max_drawdown: float
    contribution: float


@dataclass
class CorrelationMatrix:
    strategy_names: list[str]
    matrix: NDArray
    avg_correlation: float

    def get_correlation(self, name1: str, name2: str) -> float:
        i = self.strategy_names.index(name1)
        j = self.strategy_names.index(name2)
        return float(self.matrix[i, j])


@dataclass
class PortfolioResult:
    portfolio_nav: dict[date, float]
    total_return: float
    annualized_return: float
    volatility: float
    sharpe: float
    max_drawdown: float
    strategy_metrics: list[StrategyMetrics]
    correlation: CorrelationMatrix | None
    weights: dict[str, float]


class StrategyPortfolio:
    """多策略组合"""

    def __init__(self, risk_free_rate: float = 0.02, trading_days_per_year: int = 252):
        self._rf = risk_free_rate
        self._tpy = trading_days_per_year
        self._strategies: dict[str, StrategyInfo] = {}

    def add_strategy(self, name: str, nav: dict[date, float],
                     weight: float = 1.0, metadata: dict[str, Any] | None = None) -> None:
        self._strategies[name] = StrategyInfo(name, nav, weight, metadata or {})

    def remove_strategy(self, name: str) -> None:
        self._strategies.pop(name, None)

    def set_weights(self, weights: dict[str, float]) -> None:
        for name, w in weights.items():
            if name in self._strategies:
                self._strategies[name].weight = w

    def equal_weight(self) -> None:
        n = len(self._strategies)
        if n > 0:
            w = 1.0 / n
            for s in self._strategies.values():
                s.weight = w

    def _normalize_weights(self) -> dict[str, float]:
        total = sum(s.weight for s in self._strategies.values())
        if total <= 0:
            n = len(self._strategies)
            return {n: 1.0 / n for n in self._strategies} if n > 0 else {}
        return {n: s.weight / total for n, s in self._strategies.items()}

    @staticmethod
    def _compute_strategy_returns(nav: dict[date, float]) -> dict[date, float]:
        dates = sorted(nav.keys())
        returns: dict[date, float] = {}
        for i in range(1, len(dates)):
            prev = nav[dates[i - 1]]
            if prev > 0:
                returns[dates[i]] = nav[dates[i]] / prev - 1
        return returns

    @staticmethod
    def _compute_max_drawdown(nav: dict[date, float]) -> float:
        peak = 0.0
        max_dd = 0.0
        for v in sorted(nav.items()):
            peak = max(peak, v[1])
            if peak > 0:
                dd = (peak - v[1]) / peak
                max_dd = max(max_dd, dd)
        return max_dd

    def _compute_sharpe(self, returns: NDArray) -> float:
        if len(returns) < 2:
            return 0.0
        mu = np.mean(returns)
        std = np.std(returns, ddof=1)
        if std < 1e-10:
            return 0.0
        return float((mu * self._tpy - self._rf) / (std * np.sqrt(self._tpy)))

    def compute(self) -> PortfolioResult:
        if not self._strategies:
            return PortfolioResult({}, 0, 0, 0, 0, 0, [], None, {})

        weights = self._normalize_weights()

        # 公共日期
        all_dates: set[date] = set()
        for s in self._strategies.values():
            if not all_dates:
                all_dates = set(s.nav.keys())
            else:
                all_dates &= set(s.nav.keys())
        common = sorted(all_dates)
        if len(common) < 2:
            return PortfolioResult({}, 0, 0, 0, 0, 0, [], None, weights)

        # 组合净值
        portfolio_nav: dict[date, float] = {}
        for d in common:
            portfolio_nav[d] = sum(
                weights[n] * self._strategies[n].nav[d] / self._strategies[n].nav[common[0]]
                for n in self._strategies
            )

        # 组合指标
        vals = [portfolio_nav[d] for d in common]
        total_ret = vals[-1] / vals[0] - 1
        days = (common[-1] - common[0]).days or 1
        ann_ret = (1 + total_ret) ** (365 / days) - 1
        daily_rets = np.diff(vals) / np.array(vals[:-1])
        vol = float(np.std(daily_rets, ddof=1) * np.sqrt(self._tpy))
        sharpe = self._compute_sharpe(daily_rets)
        max_dd = self._compute_max_drawdown(portfolio_nav)

        # 子策略指标
        metrics: list[StrategyMetrics] = []
        for name, si in self._strategies.items():
            s_vals = [si.nav[d] for d in common]
            s_ret = s_vals[-1] / s_vals[0] - 1
            s_ann = (1 + s_ret) ** (365 / days) - 1
            s_rets = np.diff(s_vals) / np.array(s_vals[:-1])
            s_vol = float(np.std(s_rets, ddof=1) * np.sqrt(self._tpy))
            s_sharpe = self._compute_sharpe(s_rets)
            s_dd = self._compute_max_drawdown({d: si.nav[d] for d in common})
            metrics.append(StrategyMetrics(
                name=name, total_return=s_ret, annualized_return=s_ann,
                volatility=s_vol, sharpe=s_sharpe, max_drawdown=s_dd,
                contribution=weights[name] * s_ret,
            ))

        corr = self._compute_correlation(common)
        return PortfolioResult(portfolio_nav, total_ret, ann_ret, vol, sharpe, max_dd,
                               metrics, corr, weights)

    def _compute_correlation(self, common_dates: list[date]) -> CorrelationMatrix | None:
        names = list(self._strategies.keys())
        if len(names) < 2 or len(common_dates) < 3:
            return None
        ret_matrix = []
        for n in names:
            nav = self._strategies[n].nav
            vals = [nav[d] for d in common_dates]
            rets = np.diff(vals) / np.array(vals[:-1])
            ret_matrix.append(rets)
        mat = np.corrcoef(ret_matrix)
        n = len(names)
        avg = float((mat.sum() - n) / (n * (n - 1))) if n > 1 else 0.0
        return CorrelationMatrix(names, mat, avg)

    def summary(self) -> str:
        result = self.compute()
        lines = ["多策略组合报告"]
        lines.append("")
        lines.append("组合整体表现")
        lines.append(f"  ret={result.annualized_return:.2%} sharpe={result.sharpe:.2f} "
                     f"dd={result.max_drawdown:.2%}")
        lines.append("")
        lines.append("各策略表现")
        for m in result.strategy_metrics:
            lines.append(f"  {m.name}: ret={m.annualized_return:.2%} sharpe={m.sharpe:.2f}")
        lines.append("")
        lines.append("权重配置")
        for n, w in result.weights.items():
            lines.append(f"  {n}: {w:.2%}")
        if result.correlation:
            lines.append("")
            lines.append("策略相关性")
            lines.append(f"  平均相关性: {result.correlation.avg_correlation:.3f}")
        return "\n".join(lines)


class RiskParityAllocator:
    """风险平价权重分配"""

    def __init__(self, target_risk: float | None = None):
        self._target = target_risk

    def compute_weights(self, returns: dict[str, NDArray]) -> dict[str, float]:
        names = list(returns.keys())
        if not names:
            return {}
        vols = {n: float(np.std(r, ddof=1)) for n, r in returns.items()}
        # If any strategy has zero (or near-zero) volatility, fall back to equal weight
        if any(v < 1e-10 for v in vols.values()):
            n = len(names)
            return {name: 1.0 / n for name in names}
        inv_vol = {n: 1.0 / v for n, v in vols.items()}
        total = sum(inv_vol.values())
        return {n: v / total for n, v in inv_vol.items()}


def create_equal_weight_portfolio(
    strategies: dict[str, dict[date, float]],
) -> PortfolioResult:
    """便捷函数: 等权组合"""
    sp = StrategyPortfolio()
    for name, nav in strategies.items():
        sp.add_strategy(name, nav)
    sp.equal_weight()
    return sp.compute()
