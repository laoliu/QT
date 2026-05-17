"""
组合权重优化模块

提供多种权重计算方法：
- EqualWeight: 等权分配
- MarketCapWeight: 市值加权
- RiskParity: 风险平价
- MinVariance: 最小方差
- MaxSharpe: 最大夏普

使用示例：
    optimizer = EqualWeightOptimizer()
    weights = optimizer.optimize(symbols=["000001.SZ", "000002.SZ"])
    # {"000001.SZ": 0.5, "000002.SZ": 0.5}
"""

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


class WeightOptimizer(ABC):
    """权重优化器抽象基类"""

    @abstractmethod
    def optimize(
        self,
        symbols: list[str],
        returns: Optional[dict[str, list[float]]] = None,
        covariance: Optional[dict[str, dict[str, float]]] = None,
        market_caps: Optional[dict[str, float]] = None,
        constraints: Optional[dict] = None,
    ) -> dict[str, float]:
        """计算最优权重

        Args:
            symbols: 标的列表
            returns: 历史收益率序列 {symbol: [r1, r2, ...]}
            covariance: 协方差矩阵 {sym1: {sym2: cov}}
            market_caps: 市值 {symbol: market_cap}
            constraints: 约束条件

        Returns:
            权重字典 {symbol: weight}
        """
        raise NotImplementedError


@dataclass
class EqualWeightOptimizer(WeightOptimizer):
    """等权分配优化器

    所有标的分配相同权重。
    """

    def optimize(
        self,
        symbols: list[str],
        returns: Optional[dict[str, list[float]]] = None,
        covariance: Optional[dict[str, dict[str, float]]] = None,
        market_caps: Optional[dict[str, float]] = None,
        constraints: Optional[dict] = None,
    ) -> dict[str, float]:
        """等权优化：所有标的分配相同权重。"""
        if not symbols:
            return {}

        weight = 1.0 / len(symbols)
        return {symbol: weight for symbol in symbols}


@dataclass
class MarketCapWeightOptimizer(WeightOptimizer):
    """市值加权优化器

    按市值比例分配权重。
    """

    def optimize(
        self,
        symbols: list[str],
        returns: Optional[dict[str, list[float]]] = None,
        covariance: Optional[dict[str, dict[str, float]]] = None,
        market_caps: Optional[dict[str, float]] = None,
        constraints: Optional[dict] = None,
    ) -> dict[str, float]:
        """按市值比例分配权重；无市值数据时回退为等权。"""
        if not symbols:
            return {}

        if market_caps is None:
            # 回退到等权
            return EqualWeightOptimizer().optimize(symbols)

        # 过滤有市值数据的标的
        valid_caps = {s: market_caps[s] for s in symbols if s in market_caps and market_caps[s] > 0}

        if not valid_caps:
            return EqualWeightOptimizer().optimize(symbols)

        total_cap = sum(valid_caps.values())
        return {symbol: cap / total_cap for symbol, cap in valid_caps.items()}


@dataclass
class RiskParityOptimizer(WeightOptimizer):
    """风险平价优化器

    使每个标的对组合的风险贡献相等。

    Attributes:
        max_iterations: 最大迭代次数
        tolerance: 收敛容差
    """

    max_iterations: int = 100
    tolerance: float = 1e-6

    def optimize(
        self,
        symbols: list[str],
        returns: Optional[dict[str, list[float]]] = None,
        covariance: Optional[dict[str, dict[str, float]]] = None,
        market_caps: Optional[dict[str, float]] = None,
        constraints: Optional[dict] = None,
    ) -> dict[str, float]:
        """风险平价：权重与波动率倒数成正比，使各标的风险贡献近似相等。"""
        if not symbols:
            return {}

        _n = len(symbols)  # noqa: F841  # may be used later

        # 需要收益率数据来计算波动率
        if returns is None or not all(s in returns for s in symbols):
            # 回退到等权
            return EqualWeightOptimizer().optimize(symbols)

        # 计算每个标的的波动率
        volatilities = {}
        for symbol in symbols:
            ret = returns[symbol]
            if len(ret) < 2:
                return EqualWeightOptimizer().optimize(symbols)
            mean_ret = sum(ret) / len(ret)
            variance = sum((r - mean_ret) ** 2 for r in ret) / (len(ret) - 1)
            volatilities[symbol] = math.sqrt(variance)

        # 简化版风险平价：权重与波动率的倒数成正比
        # w_i ∝ 1 / σ_i
        inv_vols = {}
        for symbol in symbols:
            vol = volatilities[symbol]
            if vol > 0:
                inv_vols[symbol] = 1.0 / vol
            else:
                inv_vols[symbol] = 1.0  # 波动率为0时用1

        total_inv_vol = sum(inv_vols.values())
        if total_inv_vol <= 0:
            return EqualWeightOptimizer().optimize(symbols)

        return {symbol: inv_vol / total_inv_vol for symbol, inv_vol in inv_vols.items()}


@dataclass
class MinVarianceOptimizer(WeightOptimizer):
    """最小方差优化器

    寻找使组合波动率最小的权重配置。
    简化版：使用波动率倒数的平方作为权重近似。

    注意：完整实现需要二次规划求解器（如 cvxpy）。
    """

    def optimize(
        self,
        symbols: list[str],
        returns: Optional[dict[str, list[float]]] = None,
        covariance: Optional[dict[str, dict[str, float]]] = None,
        market_caps: Optional[dict[str, float]] = None,
        constraints: Optional[dict] = None,
    ) -> dict[str, float]:
        """最小方差近似：权重与波动率倒数的平方成正比，最小化组合波动。"""
        if not symbols:
            return {}

        if returns is None or not all(s in returns for s in symbols):
            return EqualWeightOptimizer().optimize(symbols)

        # 计算波动率
        volatilities = {}
        for symbol in symbols:
            ret = returns[symbol]
            if len(ret) < 2:
                return EqualWeightOptimizer().optimize(symbols)
            mean_ret = sum(ret) / len(ret)
            variance = sum((r - mean_ret) ** 2 for r in ret) / (len(ret) - 1)
            volatilities[symbol] = math.sqrt(variance)

        # 简化版：权重与波动率倒数的平方成正比
        inv_vol_sq = {}
        for symbol in symbols:
            vol = volatilities[symbol]
            if vol > 0:
                inv_vol_sq[symbol] = 1.0 / (vol ** 2)
            else:
                inv_vol_sq[symbol] = 1.0

        total = sum(inv_vol_sq.values())
        if total <= 0:
            return EqualWeightOptimizer().optimize(symbols)

        return {symbol: v / total for symbol, v in inv_vol_sq.items()}


@dataclass
class MaxSharpeOptimizer(WeightOptimizer):
    """最大夏普比优化器

    寻找使夏普比率最大的权重配置。
    简化版：使用收益率/波动率作为权重参考。

    Attributes:
        risk_free_rate: 无风险利率（年化）
    """

    risk_free_rate: float = 0.02

    def optimize(
        self,
        symbols: list[str],
        returns: Optional[dict[str, list[float]]] = None,
        covariance: Optional[dict[str, dict[str, float]]] = None,
        market_caps: Optional[dict[str, float]] = None,
        constraints: Optional[dict] = None,
    ) -> dict[str, float]:
        """最大夏普近似：按各标的夏普比率正值加权分配，负夏普标的权重为零。"""
        if not symbols:
            return {}

        if returns is None or not all(s in returns for s in symbols):
            return EqualWeightOptimizer().optimize(symbols)

        # 计算每个标的的夏普比率
        sharpe_ratios = {}
        daily_rf = self.risk_free_rate / TRADING_DAYS_PER_YEAR  # 日频

        for symbol in symbols:
            ret = returns[symbol]
            if len(ret) < 2:
                return EqualWeightOptimizer().optimize(symbols)

            mean_ret = sum(ret) / len(ret)
            variance = sum((r - mean_ret) ** 2 for r in ret) / (len(ret) - 1)
            std = math.sqrt(variance)

            if std > 0:
                sharpe = (mean_ret - daily_rf) / std
            else:
                sharpe = 0

            # 只考虑正夏普的标的
            sharpe_ratios[symbol] = max(0, sharpe)

        total_sharpe = sum(sharpe_ratios.values())
        if total_sharpe <= 0:
            return EqualWeightOptimizer().optimize(symbols)

        return {symbol: sr / total_sharpe for symbol, sr in sharpe_ratios.items()}


def apply_weight_constraints(
    weights: dict[str, float],
    min_weight: float = 0.0,
    max_weight: float = 1.0,
    normalize: bool = True,
) -> dict[str, float]:
    """应用权重约束

    Args:
        weights: 原始权重
        min_weight: 最小权重
        max_weight: 最大权重
        normalize: 是否归一化到和为1

    Returns:
        调整后的权重
    """
    if not weights:
        return {}

    # 应用约束
    constrained = {}
    for symbol, weight in weights.items():
        constrained[symbol] = max(min_weight, min(max_weight, weight))

    # 归一化
    if normalize:
        total = sum(constrained.values())
        if total > 0:
            constrained = {s: w / total for s, w in constrained.items()}

    return constrained


def calculate_portfolio_metrics(
    weights: dict[str, float],
    returns: dict[str, list[float]],
    risk_free_rate: float = 0.02,
) -> dict[str, float]:
    """计算组合指标

    Args:
        weights: 权重
        returns: 历史收益率
        risk_free_rate: 无风险利率

    Returns:
        {"expected_return": ..., "volatility": ..., "sharpe_ratio": ...}
    """
    if not weights or not returns:
        return {"expected_return": 0.0, "volatility": 0.0, "sharpe_ratio": 0.0}

    symbols = list(weights.keys())

    # 检查数据完整性
    min_len = min(len(returns.get(s, [])) for s in symbols)
    if min_len < 2:
        return {"expected_return": 0.0, "volatility": 0.0, "sharpe_ratio": 0.0}

    # 计算组合收益率序列
    portfolio_returns = []
    for i in range(min_len):
        port_ret = sum(weights[s] * returns[s][i] for s in symbols)
        portfolio_returns.append(port_ret)

    # 计算组合均值和波动率
    mean_ret = sum(portfolio_returns) / len(portfolio_returns)
    variance = sum((r - mean_ret) ** 2 for r in portfolio_returns) / (len(portfolio_returns) - 1)
    volatility = math.sqrt(variance)

    # 年化
    annualized_return = mean_ret * TRADING_DAYS_PER_YEAR
    annualized_vol = volatility * math.sqrt(TRADING_DAYS_PER_YEAR)

    # 夏普比率
    sharpe = (annualized_return - risk_free_rate) / annualized_vol if annualized_vol > 0 else 0

    return {
        "expected_return": annualized_return,
        "volatility": annualized_vol,
        "sharpe_ratio": sharpe,
    }
