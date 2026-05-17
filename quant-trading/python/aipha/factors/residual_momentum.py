"""残差动量因子

基于 Blitz, Huij, Martens (2011) "Residual Momentum" 的思想，
将普通动量信号回归掉市场因子（可选：规模/价值因子），
使用残差收益的累计值作为动量信号。

残差动量的优势：
1. 降低与市场 Beta 的相关性，提供更纯净的个股 alpha 信号
2. 对市场急涨急跌（系统性事件）更鲁棒
3. 可与传统动量因子正交化，用于多因子模型

学术参考：
- Blitz, Huij, Martens (2011) "Residual Momentum", Journal of Empirical Finance
- Grundy & Martin (2001) "Understanding the Nature of the Risks and the Source of the Rewards to Momentum Investing"

包含：
- ResidualMomentumFactor: 残差动量因子（回归掉市场收益后的残差累计收益）
"""

import math
from dataclasses import dataclass, field
from typing import Optional

from aipha.factors.base import Factor, FactorData, FactorResult


def _ols_residuals(
    y: list[float], x: list[float]
) -> tuple[list[float], float, float]:
    """简单 OLS 一元线性回归，返回残差序列 + alpha + beta。

    y = alpha + beta * x + residual

    参数:
        y: 因变量序列（个股收益）
        x: 自变量序列（市场收益）

    返回:
        (residuals, alpha, beta)
    """
    n = len(y)
    if n < 3 or n != len(x):
        return y[:], 0.0, 0.0

    x_mean = sum(x) / n
    y_mean = sum(y) / n

    # SSxy, SSxx
    ssxy = sum((xi - x_mean) * (yi - y_mean) for xi, yi in zip(x, y))
    ssxx = sum((xi - x_mean) ** 2 for xi in x)

    if ssxx < 1e-15:
        return [yi - y_mean for yi in y], y_mean, 0.0

    beta = ssxy / ssxx
    alpha = y_mean - beta * x_mean

    residuals = [yi - (alpha + beta * xi) for xi, yi in zip(x, y)]
    return residuals, alpha, beta


@dataclass
class ResidualMomentumFactor(Factor):
    """残差动量因子

    步骤：
    1. 取过去 lookback_days 的个股日收益率和市场收益率
    2. OLS 回归：r_stock = α + β × r_market + ε
    3. 累计残差 ε 作为因子值（skip_recent 个最近交易日可跳过）
    4. 可选对残差做 t-stat 标准化（除以残差标准差再乘以 √N）

    输入要求：
    - data.returns: 个股收益率序列（按时间升序，最新在最后）
    - data.metadata["market_returns"]: 市场收益率序列（同期，同长度）

    Attributes:
        lookback_days: 回望期（天数），论文推荐 60-120
        skip_recent: 跳过最近 N 天（避免短期反转效应）
        use_tstat: 是否使用 t-stat 标准化残差动量
    """

    lookback_days: int = 60
    skip_recent: int = 5
    use_tstat: bool = False
    _name: Optional[str] = field(default=None, repr=False)

    def __init__(
        self,
        lookback_days: int = 60,
        skip_recent: int = 5,
        use_tstat: bool = False,
        name: Optional[str] = None,
    ):
        object.__setattr__(self, "lookback_days", lookback_days)
        object.__setattr__(self, "skip_recent", skip_recent)
        object.__setattr__(self, "use_tstat", use_tstat)
        object.__setattr__(self, "_name", name)

    @property
    def name(self) -> str:
        """因子名称，格式为 RES_MOM_{lookback}[_{skip}][_tstat]。"""
        if self._name:
            return self._name
        suffix = "_tstat" if self.use_tstat else ""
        if self.skip_recent > 0:
            return f"RES_MOM_{self.lookback_days}_{self.skip_recent}{suffix}"
        return f"RES_MOM_{self.lookback_days}{suffix}"

    def compute(self, data: FactorData) -> FactorResult:
        """计算残差动量因子。

        需要 data.returns 和 data.metadata["market_returns"] 同期同长度。
        """
        # 验证输入
        if data.returns is None:
            return self._invalid_result(data, "no_returns")

        market_returns = data.metadata.get("market_returns")
        if market_returns is None:
            return self._invalid_result(data, "no_market_returns")

        min_len = self.lookback_days + self.skip_recent
        if len(data.returns) < min_len or len(market_returns) < min_len:
            return self._invalid_result(data, "insufficient_data")

        # 取回望期切片
        if self.skip_recent > 0:
            stock_ret = data.returns[
                -(self.lookback_days + self.skip_recent) : -self.skip_recent
            ]
            mkt_ret = market_returns[
                -(self.lookback_days + self.skip_recent) : -self.skip_recent
            ]
        else:
            stock_ret = data.returns[-self.lookback_days :]
            mkt_ret = market_returns[-self.lookback_days :]

        # 从 list 中过滤掉 NaN 对
        clean_stock, clean_mkt = [], []
        for s, m in zip(stock_ret, mkt_ret):
            if not (math.isnan(s) or math.isnan(m)):
                clean_stock.append(s)
                clean_mkt.append(m)

        if len(clean_stock) < 10:
            return self._invalid_result(data, "too_few_valid_returns")

        # OLS 回归
        residuals, alpha, beta = _ols_residuals(clean_stock, clean_mkt)

        # 累计残差动量
        cum_residual = sum(residuals)

        # 可选 t-stat 标准化
        if self.use_tstat and len(residuals) >= 3:
            res_mean = sum(residuals) / len(residuals)
            res_var = sum((r - res_mean) ** 2 for r in residuals) / (
                len(residuals) - 1
            )
            res_std = math.sqrt(res_var) if res_var > 0 else 1.0
            factor_value = (res_mean / res_std) * math.sqrt(len(residuals))
        else:
            factor_value = cum_residual

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=factor_value,
            metadata={
                "alpha": round(alpha, 6),
                "beta": round(beta, 4),
                "residual_sum": round(cum_residual, 6),
                "n_observations": len(clean_stock),
                "lookback_days": self.lookback_days,
                "skip_recent": self.skip_recent,
            },
        )

    def _invalid_result(self, data: FactorData, reason: str) -> FactorResult:
        """生成无效结果"""
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )
