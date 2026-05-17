"""
波动因子

包含：
- VolatilityFactor: 波动率因子
"""

import math
from dataclasses import dataclass, field
from typing import Optional

from aipha.factors.base import Factor, FactorData, FactorResult
from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


@dataclass
class VolatilityFactor(Factor):
    """波动率因子

    计算过去 N 期收益率的标准差作为波动率。
    支持两种输入方式：
    1. 通过 data.returns 直接提供收益率序列
    2. 通过 data.historical_close 提供历史价格，自动计算收益率

    Attributes:
        lookback_days: 回望期（天数）
        annualization_factor: 年化因子（默认 sqrt(TRADING_DAYS_PER_YEAR)）
        invert: 是否取负值（低波动因子）
        name: 因子名称（可选，默认自动生成）
    """

    lookback_days: int = 20
    annualization_factor: float = field(default_factory=lambda: math.sqrt(TRADING_DAYS_PER_YEAR))
    invert: bool = False
    _name: Optional[str] = field(default=None, repr=False)

    def __init__(
        self,
        lookback_days: int = 20,
        annualization_factor: Optional[float] = None,
        invert: bool = False,
        name: Optional[str] = None,
    ):
        object.__setattr__(self, "lookback_days", lookback_days)
        object.__setattr__(
            self,
            "annualization_factor",
            annualization_factor if annualization_factor is not None else math.sqrt(TRADING_DAYS_PER_YEAR),
        )
        object.__setattr__(self, "invert", invert)
        object.__setattr__(self, "_name", name)

    @property
    def name(self) -> str:
        """因子名称，格式为 VOL_{lookback} 或 LOW_VOL_{lookback}。"""
        if self._name:
            return self._name
        prefix = "LOW_VOL" if self.invert else "VOL"
        return f"{prefix}_{self.lookback_days}"

    def compute(self, data: FactorData) -> FactorResult:
        """计算波动率因子"""
        # 优先使用 historical_close 计算
        if data.historical_close:
            return self._compute_from_prices(data)
        # 否则使用 returns
        return self._compute_from_returns(data)

    def _compute_from_prices(self, data: FactorData) -> FactorResult:
        """从历史价格计算波动率"""
        if not data.historical_close:
            return self._invalid_result(data, "no_historical_close")

        # 按日期排序
        sorted_dates = sorted(data.historical_close.keys())

        # 需要 lookback_days + 1 个数据点来计算 lookback_days 个收益率
        if len(sorted_dates) < self.lookback_days + 1:
            return self._invalid_result(data, "insufficient_history")

        # 取最近的数据
        recent_dates = sorted_dates[-(self.lookback_days + 1) :]

        # 计算收益率
        returns = []
        for i in range(1, len(recent_dates)):
            prev_price = data.historical_close[recent_dates[i - 1]]
            curr_price = data.historical_close[recent_dates[i]]

            if prev_price is None or curr_price is None:
                continue
            if prev_price <= 0:
                continue  # 跳过无效价格

            ret = (curr_price - prev_price) / prev_price
            returns.append(ret)

        if len(returns) < 2:
            # 至少需要 2 个收益率才能计算标准差
            return self._result_with_volatility(data, 0.0, len(returns))

        # 计算标准差
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance)

        # 年化
        volatility = std * self.annualization_factor

        return self._result_with_volatility(data, volatility, len(returns), std)

    def _compute_from_returns(self, data: FactorData) -> FactorResult:
        """从收益率序列计算波动率"""
        if data.returns is None or len(data.returns) < self.lookback_days:
            return self._invalid_result(data, "insufficient_returns")

        returns = data.returns[-self.lookback_days :]

        if len(returns) < 2:
            return self._result_with_volatility(data, 0.0, len(returns))

        # 计算标准差
        mean = sum(returns) / len(returns)
        variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance)

        # 年化
        volatility = std * self.annualization_factor

        return self._result_with_volatility(data, volatility, len(returns), std)

    def _result_with_volatility(
        self,
        data: FactorData,
        volatility: float,
        n_returns: int,
        std: Optional[float] = None,
    ) -> FactorResult:
        """生成波动率结果"""
        value = -volatility if self.invert else volatility

        metadata = {
            "volatility": volatility,
            "n_returns": n_returns,
            "annualization_factor": self.annualization_factor,
        }
        if std is not None:
            metadata["std"] = std

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=value,
            metadata=metadata,
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
