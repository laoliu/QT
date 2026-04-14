"""统一市场规则接口

为 A 股、可转债等不同资产类型提供统一的市场规则 Protocol，
使 FillModel / BacktestEngine 可以对不同资产使用同一套调用接口。

设计原则：
- MarketRules Protocol 定义最小公共接口（order + bar 两参数）
- 各资产规则类可以有额外参数（带默认值），不影响 Protocol 兼容性
- PrecheckDecision 统一包含 warning 字段，A 股场景默认 None
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from aipha.domain.models import Bar, Order


@dataclass(frozen=True, slots=True)
class PrecheckDecision:
    """统一订单预检结果

    适用于所有资产类型（A 股、可转债等）。

    Attributes:
        allowed: 是否允许下单
        reason: 拒绝原因（str enum 值或 None）
        warning: 非阻塞警告信息（如可转债强赎预警），A 股场景为 None
    """

    allowed: bool
    reason: str | None = None
    warning: str | None = None


@runtime_checkable
class MarketRules(Protocol):
    """市场规则 Protocol

    所有资产类型（A 股、可转债等）的市场规则必须实现此接口。
    FillModel / BacktestEngine 通过此 Protocol 调用，不依赖具体实现类。

    实现要求：
    - lot_size: 最小交易单位
    - precheck_order: 至少接受 order + bar 两个关键字参数
      （可转债等资产可以有额外的可选参数）

    示例::

        class MarketRulesCNEquity:
            lot_size: int = 100
            def precheck_order(self, *, order, bar) -> PrecheckDecision: ...

        class MarketRulesConvertibleBond:
            lot_size: int = 10
            def precheck_order(self, *, order, bar,
                               is_first_day=False, ...) -> PrecheckDecision: ...
    """

    @property
    def lot_size(self) -> int:
        """最小交易单位（手数），股票通常为 100，可转债为 10。"""
        ...

    def precheck_order(self, *, order: Order, bar: Bar, **kwargs: Any) -> PrecheckDecision:
        """订单预检：检查涨跌停、停牌、T+1 等交易制度约束。"""
        ...
