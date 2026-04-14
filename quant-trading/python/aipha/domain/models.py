"""核心领域模型定义。

包含交易系统的基础数据结构：Bar（K线）、Order（订单）、Fill（成交）、
Side（交易方向）、OrderType（订单类型）等。这些模型贯穿数据层、策略层、
执行层和回测引擎，是整个系统的统一语言。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    """交易方向枚举。"""

    BUY = "BUY"
    SELL = "SELL"


class OrderState(str, Enum):
    """订单生命周期状态枚举。

    状态转移:
        PENDING → FILLED        (全部成交)
        PENDING → PARTIAL_FILLED (部分成交)
        PENDING → CANCELLED     (未成交取消)
        PARTIAL_FILLED → FILLED (后续全部成交)
        PARTIAL_FILLED → CANCELLED (剩余部分取消)
    """

    PENDING = "PENDING"
    PARTIAL_FILLED = "PARTIAL_FILLED"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


class OrderType(str, Enum):
    """订单类型枚举。

    MARKET/LIMIT 用于回测；FOK/IOC/BEST_FIVE 等用于实盘/仿真对接。
    """

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    FOK = "FOK"
    IOC = "IOC"
    BEST_FIVE = "BEST_FIVE"
    BEST_FIVE_THEN_LIMIT = "BEST_FIVE_THEN_LIMIT"


@dataclass(frozen=True, slots=True)
class Bar:
    """OHLCV bar。

    Notes:
        - ``end_ts`` 是 bar 完成的时间。
        - ``metadata`` 携带标的特有数据（如可转债溢价率）。
    """

    symbol: str
    start_ts: datetime
    end_ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: float | None = None
    prev_close: float | None = None
    is_suspended: bool = False
    is_st: bool = False
    is_limit_up: bool | None = None
    is_limit_down: bool | None = None
    metadata: dict | None = None


@dataclass(frozen=True, slots=True)
class Order:
    """订单模型。"""

    order_id: str
    symbol: str
    side: Side
    qty: int
    order_type: OrderType
    t_submit: datetime
    limit_price: float | None = None


@dataclass(frozen=True, slots=True)
class Fill:
    """成交回报模型。"""

    order_id: str
    symbol: str
    side: Side
    qty: int
    price: float
    t_fill: datetime
    model: str

    @property
    def notional(self) -> float:
        """返回成交名义金额（数量 × 价格）。"""
        return self.qty * self.price
