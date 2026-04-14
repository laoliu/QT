"""分析师因子模块（接口预留）

预留分析师一致预期和盈利修正因子接口。
当前 TuShare Pro 不提供分析师预期数据，后续可接入
Wind / 朝阳永续 / 聚宽等数据源实现。

包含：
- AnalystConsensusFactor: 分析师一致预期评级因子（接口预留）
- EarningsRevisionFactor: 盈利修正方向因子（接口预留）

设计说明：
- 当前状态：定义完整接口，``compute()`` 返回 NaN + 提示无数据源
- 后续实现：接入数据源后，填充 ``_get_consensus`` / ``_get_revision`` 逻辑
- 因子方向：值越大 = 越看好 = 越优
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from aipha.factors.base import Factor, FactorData, FactorResult


@dataclass
class AnalystConsensusFactor(Factor):
    """分析师一致预期评级因子（接口预留）

    将分析师的买入/增持/中性/减持/卖出评级映射为数值：
    - 买入 = 5, 增持 = 4, 中性 = 3, 减持 = 2, 卖出 = 1

    使用加权平均分（按评级机构数量加权）。

    **当前状态：未实现，返回 NaN。**

    所需 metadata 字段（未来）：
        - ``analyst_rating``: 一致评级分数 (1-5)
        - ``analyst_count``: 覆盖分析师数量
        - ``target_price``: 一致目标价

    Attributes:
        min_coverage: 最少分析师覆盖数
    """

    min_coverage: int = 3

    @property
    def name(self) -> str:
        """因子名称: ``ANALYST_CONSENSUS``。"""
        return "ANALYST_CONSENSUS"

    def compute(self, data: FactorData) -> FactorResult:
        """计算分析师一致预期评级因子。

        **当前接口预留，返回 NaN。**

        Args:
            data: 因子输入数据

        Returns:
            FactorResult，value 为评级分数（1-5）；当前始终返回 NaN。
        """
        m = data.metadata

        # 如果外部已注入数据，则使用
        rating = m.get("analyst_rating")
        count = m.get("analyst_count")

        if rating is not None and count is not None:
            try:
                r = float(rating)
                c = int(count)
                if 1 <= r <= 5 and c >= self.min_coverage:
                    return FactorResult(
                        symbol=data.symbol,
                        trade_date=data.trade_date,
                        factor_name=self.name,
                        value=r,
                        metadata={"rating": r, "coverage": c},
                    )
            except (TypeError, ValueError):
                pass

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={
                "invalid": True,
                "reason": "分析师数据源未接入，接口预留",
            },
        )


@dataclass
class EarningsRevisionFactor(Factor):
    """盈利修正方向因子（接口预留）

    衡量分析师对未来盈利预期的调整方向和幅度。
    上调次数越多、幅度越大，因子值越高。

    **当前状态：未实现，返回 NaN。**

    所需 metadata 字段（未来）：
        - ``earnings_revision``: 盈利修正幅度（%，正=上调）
        - ``revision_ratio``: 上调/下调比率
        - ``revision_breadth``: (上调数-下调数) / 总覆盖数

    Attributes:
        revision_field: 使用的修正字段名
    """

    revision_field: str = "earnings_revision"

    @property
    def name(self) -> str:
        """因子名称: ``EARNINGS_REVISION``。"""
        return "EARNINGS_REVISION"

    def compute(self, data: FactorData) -> FactorResult:
        """计算盈利修正因子。

        **当前接口预留，返回 NaN。**

        Args:
            data: 因子输入数据

        Returns:
            FactorResult，value 为修正幅度/方向；当前始终返回 NaN。
        """
        m = data.metadata

        # 如果外部已注入数据，则使用
        revision = m.get(self.revision_field)
        if revision is not None:
            try:
                r = float(revision)
                if math.isfinite(r):
                    return FactorResult(
                        symbol=data.symbol,
                        trade_date=data.trade_date,
                        factor_name=self.name,
                        value=r,
                        metadata={"revision": r},
                    )
            except (TypeError, ValueError):
                pass

        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float("nan"),
            metadata={
                "invalid": True,
                "reason": "盈利修正数据源未接入，接口预留",
            },
        )
