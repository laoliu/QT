"""残差动量因子 — OLS 回归剔除 beta 后的残差累积收益"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional

from aipha.factors.base import Factor, FactorData, FactorResult


def _ols_residuals(y: list[float], x: list[float]) -> tuple[list[float], float, float]:
    """简单 OLS: y = alpha + beta * x + eps"""
    n = len(y)
    if n < 3 or n != len(x):
        return y[:], 0.0, 0.0
    mx = sum(x) / n
    my = sum(y) / n
    cov_xy = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    var_x = sum((xi - mx) ** 2 for xi in x)
    beta = cov_xy / var_x if var_x > 1e-15 else 0.0
    alpha = my - beta * mx
    residuals = [yi - alpha - beta * xi for yi, xi in zip(y, x)]
    return residuals, alpha, beta


@dataclass
class ResidualMomentumFactor(Factor):
    """残差动量: 用基准收益回归剔除系统性 beta 后衡量个股 alpha 动量

    Parameters
    ----------
    lookback_days : 回看窗口
    skip_recent : 跳过最近 N 天（避免短期反转）
    use_tstat : True 时返回 t 统计量而非累积残差
    """
    lookback_days: int = 60
    skip_recent: int = 5
    use_tstat: bool = False
    _name: str | None = None

    @property
    def name(self) -> str:
        if self._name:
            return self._name
        parts = [f"RES_MOM_{self.lookback_days}"]
        if self.skip_recent != 5:
            parts.append(str(self.skip_recent))
        if self.use_tstat:
            parts.append("tstat")
        return "_".join(parts)

    def compute(self, data: FactorData) -> FactorResult:
        prices = data.close_prices
        benchmark = data.benchmark_prices
        if prices is None or benchmark is None:
            return self._invalid_result(data, "missing price data")
        if len(prices) < self.lookback_days + self.skip_recent:
            return self._invalid_result(data, "insufficient history")

        end = len(prices) - self.skip_recent
        start = end - self.lookback_days
        if start < 1:
            return self._invalid_result(data, "lookback exceeds history")

        # 日收益率
        stock_ret = [(prices[i] / prices[i - 1] - 1) for i in range(start, end)]
        bench_ret = [(benchmark[i] / benchmark[i - 1] - 1) for i in range(start, end)]

        residuals, alpha, beta = _ols_residuals(stock_ret, bench_ret)

        if self.use_tstat:
            if len(residuals) < 3:
                return self._invalid_result(data, "too few residuals")
            mean_r = sum(residuals) / len(residuals)
            var_r = sum((r - mean_r) ** 2 for r in residuals) / (len(residuals) - 1)
            std_r = math.sqrt(var_r) if var_r > 0 else 1e-10
            value = mean_r / (std_r / math.sqrt(len(residuals)))
        else:
            # 累积残差收益
            cum = 0.0
            for r in residuals:
                cum = (1 + cum) * (1 + r) - 1
            value = cum

        return FactorResult(
            symbol=data.symbol,
            factor_name=self.name,
            value=value,
            timestamp=data.timestamp,
            metadata={"alpha": alpha, "beta": beta, "n": len(residuals)},
        )

    def _invalid_result(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            factor_name=self.name,
            value=float("nan"),
            timestamp=data.timestamp,
            metadata={"error": reason},
        )
