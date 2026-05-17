"""交易网关抽象层

定义统一的交易网关接口，支持：
- 回测模式 (Backtest)
- 仿真模式 (Paper Trading)
- 实盘模式 (Live Trading)

设计原则：
- 订单生命周期一致：下单 → 成交/拒绝 → 回报
- 回测/仿真/实盘复用同一套接口
- 资金/持仓状态同步
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Callable


class OrderStatus(str, Enum):
    """订单状态"""

    PENDING = "pending"  # 待提交
    SUBMITTED = "submitted"  # 已提交
    PARTIAL_FILLED = "partial_filled"  # 部分成交
    FILLED = "filled"  # 全部成交
    CANCELED = "canceled"  # 已撤销
    REJECTED = "rejected"  # 已拒绝
    EXPIRED = "expired"  # 已过期


class TradingMode(str, Enum):
    """交易模式"""

    BACKTEST = "backtest"  # 回测
    PAPER = "paper"  # 仿真
    LIVE = "live"  # 实盘


@dataclass
class GatewayOrder:
    """网关订单

    统一的订单表示，适用于回测/仿真/实盘。
    """

    order_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    qty: int
    order_type: str  # "MARKET" or "LIMIT"
    limit_price: float | None = None

    # 状态
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    avg_fill_price: Decimal = Decimal("0")

    # 时间戳
    created_at: datetime | None = None
    submitted_at: datetime | None = None
    filled_at: datetime | None = None

    # 拒绝/撤销原因
    reject_reason: str | None = None
    cancel_reason: str | None = None

    # 元数据
    metadata: dict = field(default_factory=dict)

    @property
    def remaining_qty(self) -> int:
        """剩余未成交数量"""
        return self.qty - self.filled_qty

    @property
    def is_done(self) -> bool:
        """订单是否已结束"""
        return self.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
        )

    @property
    def is_active(self) -> bool:
        """订单是否活跃"""
        return self.status in (
            OrderStatus.PENDING,
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIAL_FILLED,
        )


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    """成交回报

    每次成交（包括部分成交）生成一个回报。
    """

    order_id: str
    exec_id: str
    symbol: str
    side: str
    fill_qty: int
    fill_price: float
    fill_time: datetime
    commission: Decimal = Decimal("0")
    is_last: bool = False  # 是否最后一笔（全部成交）


@dataclass(frozen=True, slots=True)
class OrderStatusChange:
    """订单状态变更事件"""

    order_id: str
    old_status: OrderStatus
    new_status: OrderStatus
    timestamp: datetime
    reason: str | None = None


@dataclass
class AccountSnapshot:
    """账户快照"""

    timestamp: datetime
    cash: Decimal
    frozen_cash: Decimal
    total_equity: Decimal
    positions: dict[str, int]  # symbol -> qty
    positions_value: Decimal = Decimal("0")  # 持仓市值


# Tick 数据
@dataclass
class TickData:
    """逐笔/快照 Tick 数据。"""

    symbol: str
    price: float
    volume: int
    bid1: float = 0.0
    ask1: float = 0.0
    bid1_vol: int = 0
    ask1_vol: int = 0
    amount: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)


# 回调类型
OrderCallback = Callable[[GatewayOrder], None]
ExecutionCallback = Callable[[ExecutionReport], None]
StatusCallback = Callable[[OrderStatusChange], None]
TickCallback = Callable[[TickData], None]


class TradingGateway(ABC):
    """交易网关抽象接口

    所有交易模式（回测/仿真/实盘）都实现此接口。

    Example:
        gateway = PaperGateway.from_cb_mode(initial_capital=500_000)
        gateway.on_execution(my_execution_handler)
        gateway.submit_order(order)
    """

    @property
    @abstractmethod
    def mode(self) -> TradingMode:
        """交易模式"""
        ...

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """是否已连接"""
        ...

    @abstractmethod
    def connect(self) -> None:
        """连接网关"""
        ...

    @abstractmethod
    def disconnect(self) -> None:
        """断开连接"""
        ...

    @abstractmethod
    def submit_order(self, order: GatewayOrder) -> str:
        """提交订单

        Args:
            order: 订单

        Returns:
            订单ID

        Raises:
            GatewayError: 提交失败
        """
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        """撤销订单

        Args:
            order_id: 订单ID

        Returns:
            是否成功提交撤单请求
        """
        ...

    @abstractmethod
    def get_order(self, order_id: str) -> GatewayOrder | None:
        """获取订单

        Args:
            order_id: 订单ID

        Returns:
            订单对象，不存在返回 None
        """
        ...

    @abstractmethod
    def get_active_orders(self) -> list[GatewayOrder]:
        """获取所有活跃订单"""
        ...

    @abstractmethod
    def get_account_snapshot(self) -> AccountSnapshot:
        """获取账户快照"""
        ...

    def on_execution(self, callback: ExecutionCallback) -> None:  # noqa: B027
        """注册成交回调（子类可选覆盖）"""
        pass

    def on_status_change(self, callback: StatusCallback) -> None:  # noqa: B027
        """注册状态变更回调（子类可选覆盖）"""
        pass

    def on_tick(self, callback: TickCallback) -> None:  # noqa: B027
        """注册 Tick 数据推送回调（子类可选覆盖）"""
        pass


class GatewayError(Exception):
    """网关错误"""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class OrderValidationError(GatewayError):
    """订单验证错误"""

    pass


class InsufficientFundsError(GatewayError):
    """资金不足"""

    pass


class InsufficientPositionError(GatewayError):
    """持仓不足"""

    pass


class GatewayNotConnectedError(GatewayError):
    """网关未连接"""

    pass
