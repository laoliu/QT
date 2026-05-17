"""
多策略组合模块

支持多个策略的组合管理和绩效归因：
- 策略组合构建（等权/风险平价/自定义权重）
- 策略绩效聚合
- 策略相关性分析
- 策略贡献归因

使用示例：
    portfolio = StrategyPortfolio()
    portfolio.add_strategy("momentum", momentum_nav, weight=0.4)
    portfolio.add_strategy("value", value_nav, weight=0.6)
    result = portfolio.compute()
    print(portfolio.summary())
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
from numpy.typing import NDArray

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


@dataclass
class StrategyInfo:
    """策略信息"""

    name: str
    nav: dict[date, float]
    weight: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class StrategyMetrics:
    """单策略指标"""

    name: str
    total_return: float
    annualized_return: float
    volatility: float
    sharpe: float
    max_drawdown: float
    contribution: float  # 对组合的贡献


@dataclass
class CorrelationMatrix:
    """策略相关性矩阵"""

    strategy_names: list[str]
    matrix: NDArray  # (n, n) 相关性矩阵
    avg_correlation: float

    def get_correlation(self, name1: str, name2: str) -> float:
        """获取两个策略的相关性"""
        idx1 = self.strategy_names.index(name1)
        idx2 = self.strategy_names.index(name2)
        return float(self.matrix[idx1, idx2])


@dataclass
class PortfolioResult:
    """组合结果"""

    # 组合净值
    portfolio_nav: dict[date, float]

    # 组合整体指标
    total_return: float
    annualized_return: float
    volatility: float
    sharpe: float
    max_drawdown: float

    # 各策略指标
    strategy_metrics: list[StrategyMetrics]

    # 相关性
    correlation: CorrelationMatrix | None

    # 权重
    weights: dict[str, float]


class StrategyPortfolio:
    """多策略组合"""

    def __init__(
        self,
        risk_free_rate: float = 0.02,
        trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
    ):
        """初始化组合

        Args:
            risk_free_rate: 无风险利率
            trading_days_per_year: 年交易日数
        """
        self._strategies: dict[str, StrategyInfo] = {}
        self._risk_free_rate = risk_free_rate
        self._trading_days = trading_days_per_year

    def add_strategy(
        self,
        name: str,
        nav: dict[date, float],
        weight: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """添加策略

        Args:
            name: 策略名称
            nav: 净值序列 {date: nav}
            weight: 权重（后续会归一化）
            metadata: 额外元数据
        """
        self._strategies[name] = StrategyInfo(
            name=name,
            nav=nav,
            weight=weight,
            metadata=metadata or {},
        )

    def remove_strategy(self, name: str) -> None:
        """移除策略"""
        if name in self._strategies:
            del self._strategies[name]

    def set_weights(self, weights: dict[str, float]) -> None:
        """设置权重

        Args:
            weights: {strategy_name: weight}
        """
        for name, weight in weights.items():
            if name in self._strategies:
                self._strategies[name].weight = weight

    def equal_weight(self) -> None:
        """设置等权"""
        n = len(self._strategies)
        if n > 0:
            for strategy in self._strategies.values():
                strategy.weight = 1.0 / n

    def _normalize_weights(self) -> dict[str, float]:
        """归一化权重"""
        total = sum(s.weight for s in self._strategies.values())
        if total == 0:
            return {name: 0 for name in self._strategies}
        return {name: s.weight / total for name, s in self._strategies.items()}

    def _compute_strategy_returns(
        self,
        nav: dict[date, float],
    ) -> dict[date, float]:
        """计算日收益率"""
        returns = {}
        dates = sorted(nav.keys())
        for i in range(1, len(dates)):
            prev_date = dates[i - 1]
            curr_date = dates[i]
            if nav[prev_date] > 0:
                returns[curr_date] = nav[curr_date] / nav[prev_date] - 1
        return returns

    def _compute_max_drawdown(self, nav: dict[date, float]) -> float:
        """计算最大回撤"""
        if not nav:
            return 0.0

        nav_values = np.array([nav[d] for d in sorted(nav.keys())])
        cummax = np.maximum.accumulate(nav_values)
        drawdown = (cummax - nav_values) / cummax
        return float(np.max(drawdown))

    def _compute_sharpe(self, returns: NDArray) -> float:
        """计算夏普比率"""
        if len(returns) < 2:
            return 0.0

        daily_rf = self._risk_free_rate / self._trading_days
        excess_returns = returns - daily_rf
        mean_excess = np.mean(excess_returns)
        std_returns = np.std(returns, ddof=1)

        if std_returns == 0:
            return 0.0

        return float(mean_excess * np.sqrt(self._trading_days) / std_returns)

    def compute(self) -> PortfolioResult:
        """计算组合结果

        Returns:
            组合结果
        """
        if not self._strategies:
            return PortfolioResult(
                portfolio_nav={},
                total_return=0,
                annualized_return=0,
                volatility=0,
                sharpe=0,
                max_drawdown=0,
                strategy_metrics=[],
                correlation=None,
                weights={},
            )

        weights = self._normalize_weights()

        # 找到公共日期
        all_dates: set[date] = set()
        for strategy in self._strategies.values():
            if not all_dates:
                all_dates = set(strategy.nav.keys())
            else:
                all_dates &= set(strategy.nav.keys())

        common_dates = sorted(all_dates)

        if not common_dates:
            return PortfolioResult(
                portfolio_nav={},
                total_return=0,
                annualized_return=0,
                volatility=0,
                sharpe=0,
                max_drawdown=0,
                strategy_metrics=[],
                correlation=None,
                weights=weights,
            )

        # 构建组合净值
        portfolio_nav = {}
        for d in common_dates:
            nav_sum = 0.0
            for name, strategy in self._strategies.items():
                w = weights[name]
                nav_sum += w * strategy.nav[d]
            portfolio_nav[d] = nav_sum

        # 计算组合指标
        nav_list = [portfolio_nav[d] for d in common_dates]
        total_return = nav_list[-1] / nav_list[0] - 1 if nav_list[0] > 0 else 0
        years = len(common_dates) / self._trading_days
        annualized_return = (1 + total_return) ** (1 / years) - 1 if years > 0 else 0

        portfolio_returns = self._compute_strategy_returns(portfolio_nav)
        returns_array = np.array(list(portfolio_returns.values()))
        volatility = float(np.std(returns_array, ddof=1) * np.sqrt(self._trading_days)) if len(returns_array) > 1 else 0
        sharpe = self._compute_sharpe(returns_array)
        max_dd = self._compute_max_drawdown(portfolio_nav)

        # 计算各策略指标
        strategy_metrics = []
        for name, strategy in self._strategies.items():
            s_nav = {d: strategy.nav[d] for d in common_dates if d in strategy.nav}
            s_returns = self._compute_strategy_returns(s_nav)
            s_returns_arr = np.array(list(s_returns.values()))

            s_nav_list = [s_nav[d] for d in sorted(s_nav.keys())]
            s_total_return = s_nav_list[-1] / s_nav_list[0] - 1 if s_nav_list and s_nav_list[0] > 0 else 0
            s_annual = (1 + s_total_return) ** (1 / years) - 1 if years > 0 else 0
            s_vol = float(np.std(s_returns_arr, ddof=1) * np.sqrt(self._trading_days)) if len(s_returns_arr) > 1 else 0
            s_sharpe = self._compute_sharpe(s_returns_arr)
            s_max_dd = self._compute_max_drawdown(s_nav)

            # 贡献 = 权重 * 策略收益
            contribution = weights[name] * s_total_return

            strategy_metrics.append(StrategyMetrics(
                name=name,
                total_return=s_total_return,
                annualized_return=s_annual,
                volatility=s_vol,
                sharpe=s_sharpe,
                max_drawdown=s_max_dd,
                contribution=contribution,
            ))

        # 计算相关性矩阵
        correlation = self._compute_correlation(common_dates)

        return PortfolioResult(
            portfolio_nav=portfolio_nav,
            total_return=total_return,
            annualized_return=annualized_return,
            volatility=volatility,
            sharpe=sharpe,
            max_drawdown=max_dd,
            strategy_metrics=strategy_metrics,
            correlation=correlation,
            weights=weights,
        )

    def _compute_correlation(self, common_dates: list[date]) -> CorrelationMatrix | None:
        """计算策略相关性矩阵"""
        if len(self._strategies) < 2:
            return None

        names = list(self._strategies.keys())
        n = len(names)

        # 构建收益率矩阵
        returns_matrix = []
        for name in names:
            strategy = self._strategies[name]
            nav = {d: strategy.nav[d] for d in common_dates if d in strategy.nav}
            returns = self._compute_strategy_returns(nav)
            returns_matrix.append([returns.get(d, 0) for d in common_dates[1:]])

        returns_array = np.array(returns_matrix)

        # 计算相关性
        if returns_array.shape[1] < 2:
            return None

        corr_matrix = np.corrcoef(returns_array)

        # 平均相关性（不包含对角线）
        mask = ~np.eye(n, dtype=bool)
        avg_corr = float(np.mean(corr_matrix[mask]))

        return CorrelationMatrix(
            strategy_names=names,
            matrix=corr_matrix,
            avg_correlation=avg_corr,
        )

    def summary(self) -> str:
        """生成摘要报告"""
        result = self.compute()

        lines = [
            "=" * 60,
            "多策略组合报告",
            "=" * 60,
            "",
            f"策略数量: {len(self._strategies)}",
            "",
            "【组合整体表现】",
            f"  总收益: {result.total_return:.2%}",
            f"  年化收益: {result.annualized_return:.2%}",
            f"  波动率: {result.volatility:.2%}",
            f"  夏普比率: {result.sharpe:.2f}",
            f"  最大回撤: {result.max_drawdown:.2%}",
            "",
            "【各策略表现】",
        ]

        for m in result.strategy_metrics:
            lines.append(
                f"  {m.name}: "
                f"收益={m.total_return:.2%}, "
                f"夏普={m.sharpe:.2f}, "
                f"贡献={m.contribution:.2%}"
            )

        lines.extend(["", "【权重配置】"])
        for name, weight in result.weights.items():
            lines.append(f"  {name}: {weight:.2%}")

        if result.correlation:
            lines.extend([
                "",
                "【策略相关性】",
                f"  平均相关性: {result.correlation.avg_correlation:.4f}",
            ])

        lines.extend(["", "=" * 60])
        return "\n".join(lines)


class RiskParityAllocator:
    """风险平价权重分配器"""

    def __init__(self, target_risk: float | None = None):
        """初始化

        Args:
            target_risk: 目标风险（年化波动率）。None则使用最小化组合风险
        """
        self.target_risk = target_risk

    def compute_weights(
        self,
        returns: dict[str, NDArray],
    ) -> dict[str, float]:
        """计算风险平价权重

        Args:
            returns: {strategy_name: daily_returns_array}

        Returns:
            {strategy_name: weight}
        """
        if not returns:
            return {}

        names = list(returns.keys())
        n = len(names)

        # 计算各策略波动率
        vols = np.array([np.std(returns[name], ddof=1) for name in names])

        # 风险平价：权重与波动率成反比
        if np.any(vols == 0):
            # 有零波动率策略，使用等权
            weights = np.ones(n) / n
        else:
            inv_vols = 1.0 / vols
            weights = inv_vols / np.sum(inv_vols)

        return {name: float(weights[i]) for i, name in enumerate(names)}


def create_equal_weight_portfolio(
    strategies: dict[str, dict[date, float]],
) -> PortfolioResult:
    """便捷函数：创建等权组合

    Args:
        strategies: {strategy_name: {date: nav}}

    Returns:
        组合结果
    """
    portfolio = StrategyPortfolio()
    for name, nav in strategies.items():
        portfolio.add_strategy(name, nav)
    portfolio.equal_weight()
    return portfolio.compute()
