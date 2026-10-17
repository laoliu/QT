"""核心领域模型定义。

包含交易系统的基础数据结构：Bar（K线）、Order（订单）、Fill（成交）、
Side（交易方向）、OrderType（订单类型）等。这些模型贯穿数据层、策略层、
执行层和回测引擎，是整个系统的统一语言。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Side(str, Enum):
    """交易方向枚举。"""

    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True, slots=True)
class DepthLevel:
    """One order-book level; volume is in shares or contracts."""

    price: float
    volume: int


@dataclass(frozen=True, slots=True)
class Tick:
    """Quote snapshot with cumulative session volume and turnover."""

    symbol: str
    timestamp: datetime
    price: float
    volume: int = 0
    amount: float = 0.0
    open: float = 0.0
    high: float = 0.0
    low: float = 0.0
    prev_close: float = 0.0
    bid_levels: list[DepthLevel] = field(default_factory=list)
    ask_levels: list[DepthLevel] = field(default_factory=list)
    direction: str = ""
    trade_count: int = 0


class OrderState(str, Enum):
    """订单生命周期状态枚举。

    状态转移:
        PENDING → FILLED        (全部成交)
        PENDING → PARTIAL_FILLED (部分成交)
        PENDING → CANCELLED     (未成交取消: 无行情/现金不足/撮合失败)
        PARTIAL_FILLED → FILLED (后续全部成交, 预留)
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
    # 以下用于实盘/仿真对接
    FOK = "FOK"              # Fill-or-Kill：全部成交或全部撤销
    IOC = "IOC"              # Immediate-or-Cancel：立即成交可成交部分，剩余撤销
    BEST_FIVE = "BEST_FIVE"  # 最优五档即时成交剩余撤销（深交所特有）
    BEST_FIVE_THEN_LIMIT = "BEST_FIVE_THEN_LIMIT"  # 最优五档即时成交剩余转限价


@dataclass(frozen=True, slots=True)
class Bar:
    """OHLCV bar.

    Notes:
        - `end_ts` is the time at which the bar is considered complete.
        - `metadata` carries instrument-specific data (e.g. CB premium rate).
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
    """订单模型。

    Attributes:
        order_id: 唯一订单标识。
        symbol: 标的代码。
        side: 买卖方向。
        qty: 委托数量（股/张）。
        order_type: 订单类型。
        t_submit: 提交时间。
        limit_price: 限价（仅限价单有效）。
    """

    order_id: str
    symbol: str
    side: Side
    qty: int
    order_type: OrderType
    t_submit: datetime
    limit_price: float | None = None


@dataclass(frozen=True, slots=True)
class Fill:
    """成交回报模型。

    Attributes:
        order_id: 关联的订单标识。
        symbol: 标的代码。
        side: 买卖方向。
        qty: 成交数量。
        price: 成交价格。
        t_fill: 成交时间。
        model: 成交模型名称（如 'next_bar_open'）。
    """

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


# =====================================================================
# OrderIntent — 策略层统一订单意图 (§13.2 单一实现原则)
# =====================================================================


class IntentType(str, Enum):
    """Type of order intent."""

    TARGET_POSITION = "TARGET_POSITION"  # Target qty (rebalance to this)
    ORDER = "ORDER"  # Explicit order (buy/sell qty)


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """策略层订单意图 — 策略决策的不可变输出。

    表示策略"想做什么"，而非"实际发生什么"（后者取决于
    市场规则、可用资金、持仓等）。

    Attributes:
        symbol: 标的代码 (e.g., "600000.SH")
        side: 买卖方向 (Side.BUY / Side.SELL)
        qty: 数量（股/张）。TARGET_POSITION 时为目标持仓量。
        intent_type: ORDER（显式交易）或 TARGET_POSITION（调仓到目标）
        limit_price: 可选限价 (None 表示市价单)
        reason: 可选人类可读原因
        signal_strength: 可选信号强度 (e.g., 0.0-1.0)
        metadata: 可选附加数据（日志/分析用）
    """

    symbol: str
    side: Side
    qty: int
    intent_type: IntentType = IntentType.ORDER
    limit_price: float | None = None
    reason: str | None = None
    signal_strength: float | None = None
    metadata: dict | None = None

    def __post_init__(self) -> None:
        """Validate intent."""
        if self.qty <= 0:
            raise ValueError(f"qty must be positive, got {self.qty}")
