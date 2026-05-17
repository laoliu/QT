"""成长因子模块

基于 PIT 财务数据的成长性因子，捕捉高增长溢价。

包含：
- RevenueGrowthFactor: 营收同比增长率
- EarningsGrowthFactor: 净利润同比增长率
- RevenueAccelerationFactor: 营收增长加速度（增速变化）
- SUEFactor: 标准化未预期盈利（事件驱动核心因子）

所有因子继承 Factor ABC + PITAwareFactorMixin，确保 PIT 语义。

数据依赖：
- fina_indicator: revenue_yoy, dt_netprofit_yoy, net_profit_yoy
- 通过 PITFinancialProvider 注入

设计决策：
- 因子方向：值越大 = 增长越快 = 越优
- SUE 需要历史多期 EPS 序列计算标准化，是 PEAD 策略的核心
- 增长率以百分比表示（如 30.5 = 30.5%）
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from aipha.data.pit_financial import (
    FinancialFields,
    PITAwareFactorMixin,
    PITFinancialProvider,
)
from aipha.factors.base import Factor, FactorData, FactorResult


def _decimal_to_float(val: Decimal | None) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# 营收增长因子
# ---------------------------------------------------------------------------

@dataclass
class RevenueGrowthFactor(Factor, PITAwareFactorMixin):
    """营收同比增长率因子

    数据来源：fina_indicator.revenue_yoy（百分比）
    方向：值越大 = 增收越快 = 越优
    """

    @property
    def name(self) -> str:
        """因子名称: ``REVENUE_GROWTH``。"""
        return "REVENUE_GROWTH"

    def compute(self, data: FactorData) -> FactorResult:
        """计算营收同比增长率。

        Args:
            data: 因子输入数据（需含 PIT provider 或 metadata["revenue_yoy"]）

        Returns:
            FactorResult，value 为营收增速百分比；数据缺失时 value=NaN。
        """
        growth = self._get_growth(data)

        if growth is None:
            return self._invalid(data, "无营收增长率数据")

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=growth,
            metadata={"revenue_yoy": growth},
        )

    def _get_growth(self, data: FactorData) -> float | None:
        if self._pit_provider is not None:
            val = self.get_financial_value(
                data.symbol, data.trade_date, "revenue_yoy", "indicator"
            )
            fval = _decimal_to_float(val)
            if fval is not None:
                return fval

        g = data.metadata.get("revenue_yoy")
        if g is not None:
            try:
                return float(g)
            except (TypeError, ValueError):
                pass
        return None

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


# ---------------------------------------------------------------------------
# 盈利增长因子
# ---------------------------------------------------------------------------

@dataclass
class EarningsGrowthFactor(Factor, PITAwareFactorMixin):
    """盈利同比增长率因子

    默认使用扣非净利润增长率（dt_netprofit_yoy），更贴近主业。
    可切换为净利润增长率（net_profit_yoy）。

    Attributes:
        use_deducted: 是否使用扣非净利润增长率
    """

    use_deducted: bool = True

    @property
    def name(self) -> str:
        """因子名称: ``DEDUCTED_EARNINGS_GROWTH`` 或 ``EARNINGS_GROWTH``。"""
        return "DEDUCTED_EARNINGS_GROWTH" if self.use_deducted else "EARNINGS_GROWTH"

    def compute(self, data: FactorData) -> FactorResult:
        """计算盈利同比增长率。

        Args:
            data: 因子输入数据

        Returns:
            FactorResult，value 为盈利增速百分比；数据缺失时 value=NaN。
        """
        growth = self._get_growth(data)

        if growth is None:
            return self._invalid(data, "无盈利增长率数据")

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=growth,
            metadata={"earnings_growth": growth, "use_deducted": self.use_deducted},
        )

    def _get_growth(self, data: FactorData) -> float | None:
        field_name = "dt_netprofit_yoy" if self.use_deducted else "net_profit_yoy"

        if self._pit_provider is not None:
            val = self.get_financial_value(
                data.symbol, data.trade_date, field_name, "indicator"
            )
            fval = _decimal_to_float(val)
            if fval is not None:
                return fval

        g = data.metadata.get(field_name)
        if g is not None:
            try:
                return float(g)
            except (TypeError, ValueError):
                pass
        return None

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


# ---------------------------------------------------------------------------
# 营收加速度因子
# ---------------------------------------------------------------------------

@dataclass
class RevenueAccelerationFactor(Factor, PITAwareFactorMixin):
    """营收增长加速度因子

    衡量增长趋势变化：本期营收增速 - 上期营收增速。
    正值表示增长在加速，负值表示减速。

    需要两期连续的 revenue_yoy 数据。
    如果 PIT provider 不可用，仅从 metadata 读取。
    """

    @property
    def name(self) -> str:
        """因子名称: ``REVENUE_ACCELERATION``。"""
        return "REVENUE_ACCELERATION"

    def compute(self, data: FactorData) -> FactorResult:
        """计算营收增长加速度（本期增速 - 上期增速）。

        Args:
            data: 因子输入数据（需 metadata 含 revenue_yoy 与 revenue_yoy_prev）

        Returns:
            FactorResult，正值=加速，负值=减速；数据缺失时 value=NaN。
        """
        acc = self._get_acceleration(data)

        if acc is None:
            return self._invalid(data, "无营收加速度数据")

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=acc,
            metadata={"revenue_acceleration": acc},
        )

    def _get_acceleration(self, data: FactorData) -> float | None:
        # 简化实现：直接从 metadata 获取当前和上期增速
        current = data.metadata.get("revenue_yoy")
        previous = data.metadata.get("revenue_yoy_prev")

        if current is not None and previous is not None:
            try:
                return float(current) - float(previous)
            except (TypeError, ValueError):
                pass

        return None

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


# ---------------------------------------------------------------------------
# SUE 因子 (Standardized Unexpected Earnings)
# ---------------------------------------------------------------------------

@dataclass
class SUEFactor(Factor, PITAwareFactorMixin):
    """标准化未预期盈利因子 (SUE)

    SUE = (actual_eps - expected_eps) / std(historical_eps_surprises)

    经典事件驱动因子，用于 PEAD (Post-Earnings Announcement Drift) 策略。
    捕捉财报发布后的价格漂移。

    简化实现：
    - actual_eps: 当期 EPS
    - expected_eps: 去年同期 EPS（随机游走模型） 
    - std: 过去 8 期 EPS 差异的标准差

    Attributes:
        min_history: 计算标准差所需的最少历史期数
    """

    min_history: int = 4

    @property
    def name(self) -> str:
        """因子名称: ``SUE``。"""
        return "SUE"

    def compute(self, data: FactorData) -> FactorResult:
        """计算标准化未预期盈利 (SUE)。

        Args:
            data: 因子输入数据（需 eps_history 或 eps/eps_yoy_prev/eps_std）

        Returns:
            FactorResult，value 为 SUE 标准分；数据不足时 value=NaN。
        """
        sue = self._compute_sue(data)

        if sue is None:
            return self._invalid(data, "SUE 数据不足")

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=sue,
            metadata={"sue": sue},
        )

    def _compute_sue(self, data: FactorData) -> float | None:
        # 需要 eps 历史序列
        eps_history = data.metadata.get("eps_history")
        if eps_history is None or len(eps_history) < self.min_history + 1:
            # 降级：使用简单的 actual vs expected
            actual = data.metadata.get("eps")
            expected = data.metadata.get("eps_yoy_prev")
            eps_std = data.metadata.get("eps_std")

            if actual is not None and expected is not None and eps_std is not None:
                try:
                    actual_f = float(actual)
                    expected_f = float(expected)
                    std_f = float(eps_std)
                    if std_f > 0:
                        return (actual_f - expected_f) / std_f
                except (TypeError, ValueError):
                    pass
            return None

        # 从 eps_history 计算 SUE
        try:
            eps_list = [float(e) for e in eps_history]
        except (TypeError, ValueError):
            return None

        if len(eps_list) < self.min_history + 1:
            return None

        # 计算同比差异序列
        # eps_history 假设按时间升序排列，间隔为 1 个报告期
        # 简化：假设相邻差异
        surprises = []
        for i in range(1, len(eps_list)):
            surprises.append(eps_list[i] - eps_list[i - 1])

        if len(surprises) < 2:
            return None

        latest_surprise = surprises[-1]
        try:
            std = statistics.stdev(surprises[:-1]) if len(surprises) > 2 else statistics.stdev(surprises)
        except statistics.StatisticsError:
            return None

        if std <= 0:
            return None

        return latest_surprise / std

    def _invalid(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )
