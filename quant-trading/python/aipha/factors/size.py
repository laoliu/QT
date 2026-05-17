"""市值因子与换手率因子

A 股量化中最重要的风格因子之一：
- **小市值因子**（MarketCapFactor）：流通市值越小，预期收益越高（A 股小盘效应）
- **换手率因子**（TurnoverFactor）：低换手率标的通常被低估

数据来源：
- 流通市值（circ_mv）和总市值（total_mv）通过 TuShare daily_basic 获取
- 策略运行时通过 Bar.metadata["circ_mv"] 传入

设计决策：
- 小市值因子默认 invert=True（值越小判定为越好 → 取倒数使得因子值越大越好）
- 换手率因子默认 invert=True（低换手更好 → 取负/倒数）
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from aipha.factors.base import Factor, FactorData, FactorResult


@dataclass
class MarketCapFactor(Factor):
    """流通市值因子

    小市值效应：流通市值越小、预期收益越高（A 股经验性规律）。

    因子值处理：
    - invert=True（默认）：factor_value = -log(circ_mv)，值越大=市值越小=越优
    - invert=False：factor_value = log(circ_mv)，原始方向
    - 使用对数变换减少右偏分布的影响

    数据来源（从 FactorData.metadata["circ_mv"] 读取）：
    - circ_mv: 流通市值（万元），TuShare daily_basic 字段

    Attributes:
        use_log: 是否使用对数变换
        invert: 是否取反（小市值优先）
        mv_key: metadata 中市值字段名
    """

    use_log: bool = True
    invert: bool = True
    mv_key: str = "circ_mv"

    @property
    def name(self) -> str:
        """返回因子名称：invert=True 时为 'SMALL_CAP'，否则为 'MARKET_CAP'。"""
        return "SMALL_CAP" if self.invert else "MARKET_CAP"

    def compute(self, data: FactorData) -> FactorResult:
        """计算市值因子"""
        mv = data.metadata.get(self.mv_key)

        if mv is None:
            # 尝试从 fundamentals 获取
            if data.fundamentals:
                mv = data.fundamentals.get(self.mv_key)

        if mv is None or mv <= 0:
            return FactorResult(
                symbol=data.symbol,
                trade_date=data.trade_date,
                factor_name=self.name,
                value=float("nan"),
                metadata={"invalid": True, "reason": f"缺少 {self.mv_key}"},
            )

        if self.use_log:
            value = math.log(mv)
        else:
            value = mv

        if self.invert:
            value = -value

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=value,
            metadata={self.mv_key: mv},
        )


@dataclass
class TurnoverRateFactor(Factor):
    """换手率因子

    低换手率标的通常被市场忽视，存在估值折价机会。

    因子值处理：
    - invert=True（默认）：factor_value = -turnover_rate，低换手=正因子=更优
    - invert=False：原始换手率

    数据来源（从 FactorData.metadata["turnover_rate"] 读取）

    Attributes:
        invert: 是否取反（低换手优先）
    """

    invert: bool = True

    @property
    def name(self) -> str:
        """返回因子名称：invert=True 时为 'LOW_TURNOVER'，否则为 'TURNOVER'。"""
        return "LOW_TURNOVER" if self.invert else "TURNOVER"

    def compute(self, data: FactorData) -> FactorResult:
        """计算换手率因子"""
        tr = data.metadata.get("turnover_rate")

        if tr is None:
            if data.fundamentals:
                tr = data.fundamentals.get("turnover_rate")

        if tr is None:
            return FactorResult(
                symbol=data.symbol,
                trade_date=data.trade_date,
                factor_name=self.name,
                value=float("nan"),
                metadata={"invalid": True, "reason": "缺少 turnover_rate"},
            )

        value = -tr if self.invert else tr

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=value,
            metadata={"turnover_rate": tr},
        )
