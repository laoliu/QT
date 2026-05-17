"""
动量因子

包含：
- ReturnMomentumFactor: 收益率动量因子
"""

from dataclasses import dataclass, field
from typing import Optional

from aipha.factors.base import Factor, FactorData, FactorResult


@dataclass
class ReturnMomentumFactor(Factor):
    """收益率动量因子

    计算过去 N 期的累计收益率作为动量因子。
    支持两种输入方式：
    1. 通过 data.returns 直接提供收益率序列
    2. 通过 data.historical_close 提供历史价格，自动计算收益率

    Attributes:
        lookback_days: 回望期（天数）
        skip_recent: 跳过最近 N 天（避免短期反转）
        name: 因子名称（可选，默认自动生成）
    """

    lookback_days: int = 20
    skip_recent: int = 0
    _name: Optional[str] = field(default=None, repr=False)

    def __init__(
        self,
        lookback_days: int = 20,
        skip_recent: int = 0,
        name: Optional[str] = None,
    ):
        object.__setattr__(self, "lookback_days", lookback_days)
        object.__setattr__(self, "skip_recent", skip_recent)
        object.__setattr__(self, "_name", name)

    @property
    def name(self) -> str:
        """因子名称，格式为 MOM_{lookback}[_{skip}]。"""
        if self._name:
            return self._name
        if self.skip_recent > 0:
            return f"MOM_{self.lookback_days}_{self.skip_recent}"
        return f"MOM_{self.lookback_days}"

    def compute(self, data: FactorData) -> FactorResult:
        """计算动量因子"""
        # 优先使用 historical_close 计算
        if data.historical_close:
            return self._compute_from_prices(data)
        # 否则使用 returns
        return self._compute_from_returns(data)

    def _compute_from_prices(self, data: FactorData) -> FactorResult:
        """从历史价格计算动量"""
        if not data.historical_close:
            return self._invalid_result(data, "no_historical_close")

        # 按日期排序
        sorted_dates = sorted(data.historical_close.keys())

        # 需要 lookback_days + 1 个数据点（包括当前日期）
        if len(sorted_dates) < self.lookback_days + 1:
            return self._invalid_result(data, "insufficient_history")

        # 获取起始和结束价格
        if self.skip_recent > 0:
            if len(sorted_dates) < self.lookback_days + self.skip_recent + 1:
                return self._invalid_result(data, "insufficient_history_with_skip")
            start_idx = -(self.lookback_days + self.skip_recent + 1)
            end_idx = -(self.skip_recent + 1)
            start_date = sorted_dates[start_idx]
            end_date = sorted_dates[end_idx]
        else:
            start_date = sorted_dates[-(self.lookback_days + 1)]
            end_date = sorted_dates[-1]

        start_price = data.historical_close.get(start_date)
        end_price = data.historical_close.get(end_date)

        # 验证价格有效性
        if start_price is None or end_price is None:
            return self._invalid_result(data, "missing_price")

        if start_price <= 0:
            return self._invalid_result(data, "zero_start_price")

        # 计算收益率
        momentum = (end_price - start_price) / start_price

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=momentum,
            metadata={
                "start_price": start_price,
                "end_price": end_price,
                "start_date": str(start_date),
                "end_date": str(end_date),
            },
        )

    def _compute_from_returns(self, data: FactorData) -> FactorResult:
        """从收益率序列计算动量"""
        if data.returns is None or len(data.returns) < self.lookback_days:
            return self._invalid_result(data, "insufficient_returns")

        # 获取有效的收益率序列
        if self.skip_recent > 0:
            if len(data.returns) < self.lookback_days + self.skip_recent:
                return self._invalid_result(data, "insufficient_returns_with_skip")
            returns = data.returns[
                -(self.lookback_days + self.skip_recent) : -self.skip_recent
            ]
        else:
            returns = data.returns[-self.lookback_days :]

        # 计算累计收益率
        cumulative_return = 1.0
        for r in returns:
            cumulative_return *= 1 + r
        cumulative_return -= 1

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=cumulative_return,
            metadata={"lookback_days": self.lookback_days, "skip_recent": self.skip_recent},
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
