"""可转债下修条款因子

衡量可转债下修转股价（向下修正条款）的"期权价值"：
即发行人主动下调转股价以避免回售/促进转股的概率与潜在收益。

**下修触发条件（A股常见条款）**：
- 正股价格在任意连续 N 个交易日中有 M 个交易日收盘价
  低于当期转股价的一定比例（通常 80%-90%）。
- 典型条款："30/15/85%" = 连续 30 交易日中 15 日收盘价
  低于转股价的 85%。

**因子设计思路**：
1. 下修距离：(正股价 / 转股价) 越小 → 越接近下修触发线 → 下修概率越大
2. 下修空间：下修后转股价值提升的理论空间
3. 综合评分：同时考虑发行人下修动机（临近回售期、转债余额大）

注意：
- 下修因子属于**事件驱动**因子，只在特定窗口期有效
- 需要配合正股停牌/特别处理等数据使用
- 本因子不预测是否"真的会下修"，仅量化触发条件的接近程度

包含：
- PutbackProximityFactor: 下修接近度因子（正股价 / 转股价 距触发线的距离）
- putback_optionality_score: 综合下修期权价值评分函数
"""

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from aipha.factors.base import Factor, FactorData, FactorResult


@dataclass
class PutbackProximityFactor(Factor):
    """下修接近度因子

    量化正股价格距下修触发线的距离。

    因子值 = 1 - (stock_price / conversion_price) / trigger_ratio

    - 值 > 0：正股已低于触发线，下修可能已触发
    - 值 ≈ 0：正在触发线附近
    - 值 < 0：尚未接近触发线

    值越大 → 下修可能性越高 → 因子看好（做多信号）

    输入要求：
    - data.metadata["stock_price"]: 正股当前价格
    - data.metadata["conversion_price"]: 当前转股价
    - data.metadata["trigger_ratio"]: 下修触发比例（默认 0.85）
    - data.metadata["remaining_years"]: 剩余年限（可选，用于加权）
    - data.metadata["putback_start_date"]: 回售期起始日（可选）

    Attributes:
        trigger_ratio: 默认假设的下修触发比例（当 metadata 中无此字段时使用）
        decay_years: 距回售期超过此年数则衰减因子值
    """

    trigger_ratio: float = 0.85
    decay_years: float = 2.0
    _name: Optional[str] = field(default=None, repr=False)

    def __init__(
        self,
        trigger_ratio: float = 0.85,
        decay_years: float = 2.0,
        name: Optional[str] = None,
    ):
        object.__setattr__(self, "trigger_ratio", trigger_ratio)
        object.__setattr__(self, "decay_years", decay_years)
        object.__setattr__(self, "_name", name)

    @property
    def name(self) -> str:
        """因子名称，格式为 PUTBACK_PROX_{trigger_pct}。"""
        if self._name:
            return self._name
        return f"PUTBACK_PROX_{int(self.trigger_ratio * 100)}"

    def compute(self, data: FactorData) -> FactorResult:
        """计算下修接近度因子"""
        meta = data.metadata or {}
        stock_price = meta.get("stock_price")
        conversion_price = meta.get("conversion_price")

        if stock_price is None or conversion_price is None:
            return self._invalid_result(data, "missing_stock_or_conversion_price")

        try:
            stock_price = float(stock_price)
            conversion_price = float(conversion_price)
        except (TypeError, ValueError):
            return self._invalid_result(data, "invalid_price_type")

        if conversion_price <= 0 or stock_price <= 0:
            return self._invalid_result(data, "non_positive_price")

        trigger = meta.get("trigger_ratio", self.trigger_ratio)
        try:
            trigger = float(trigger)
        except (TypeError, ValueError):
            trigger = self.trigger_ratio

        # 核心计算：距触发线的距离
        price_ratio = stock_price / conversion_price
        proximity = 1.0 - price_ratio / trigger

        # 可选时间衰减：距回售期很远时降低权重
        remaining_years = meta.get("remaining_years")
        if remaining_years is not None:
            try:
                remaining_years = float(remaining_years)
                if remaining_years > self.decay_years:
                    decay = math.exp(-(remaining_years - self.decay_years) / self.decay_years)
                    proximity *= decay
            except (TypeError, ValueError):
                pass

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=proximity,
            metadata={
                "stock_price": stock_price,
                "conversion_price": conversion_price,
                "price_ratio": round(price_ratio, 4),
                "trigger_ratio": trigger,
                "raw_proximity": round(1.0 - price_ratio / trigger, 4),
            },
        )

    def _invalid_result(self, data: FactorData, reason: str) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={"invalid": True, "reason": reason},
        )


def putback_optionality_score(
    stock_price: float,
    conversion_price: float,
    cb_price: float,
    remaining_years: float,
    trigger_ratio: float = 0.85,
    *,
    has_putback_clause: bool = True,
    issue_size: float = 0.0,
    large_issue_threshold: float = 50000.0,
) -> float:
    """综合下修期权价值评分

    综合考虑：
    1. 下修距离（正股/转股价 vs 触发线）
    2. 下修空间（转债价格 vs 100 面值的距离）
    3. 发行人动机（余额大 → 更有动力下修促转股）
    4. 时间因素（临近回售期 → 下修紧迫性高）

    Args:
        stock_price: 正股当前价格
        conversion_price: 当前转股价
        cb_price: 转债当前价格
        remaining_years: 剩余年限
        trigger_ratio: 下修触发比例（A股常见 0.80-0.90）
        has_putback_clause: 是否有回售条款
        issue_size: 发行规模/余额（万元）
        large_issue_threshold: 大规模阈值（万元）

    Returns:
        综合评分（值越大 → 下修期权价值越高），范围大约 [0, 1]
    """
    if conversion_price <= 0 or stock_price <= 0 or cb_price <= 0:
        return 0.0

    # 1. 下修距离分（越接近或已穿越触发线 → 分数越高）
    price_ratio = stock_price / conversion_price
    distance = 1.0 - price_ratio / trigger_ratio
    distance_score = max(0.0, min(1.0, 0.5 + distance * 2.0))

    # 2. 下修空间分（转债价格越低 → 下修后弹性越大）
    # 转债价格 < 100 → 修正后回到 100 附近的空间大
    space_score = max(0.0, min(1.0, (110.0 - cb_price) / 30.0))

    # 3. 时间紧迫性（临近回售期 → 发行人更急迫）
    if has_putback_clause and remaining_years <= 2.0:
        urgency = max(0.0, min(1.0, (2.0 - remaining_years) / 2.0))
    else:
        urgency = 0.1  # 距回售期远，紧迫性低

    # 4. 规模因素（余额大 → 发行人更有动力下修）
    if issue_size > large_issue_threshold:
        size_motive = 0.8
    elif issue_size > large_issue_threshold * 0.5:
        size_motive = 0.5
    else:
        size_motive = 0.3

    # 加权综合
    score = (
        0.40 * distance_score
        + 0.25 * space_score
        + 0.20 * urgency
        + 0.15 * size_motive
    )

    return round(max(0.0, min(1.0, score)), 4)
