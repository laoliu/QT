"""交易网关抽象层 — 统一接口 (回测/模拟/实盘)"""
from __future__ import annotations

import enum
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


class TradingMode(enum.Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


class GatewayError(Exception):
    """网关错误"""
    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class GatewayNotConnectedError(GatewayError):
    """网关未连接"""
    pass


class OrderValidationError(GatewayError):
    """订单验证错误"""
    pass


class InsufficientFundsError(GatewayError):
    """资金不足"""
    pass


class InsufficientPositionError(GatewayError):
    """持仓不足"""
    pass


class OrderStatus(enum.Enum):
    PENDING = "pending"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    EXPIRED = "expired"


class OrderSide(enum.Enum):
    BUY = "buy"
    SELL = "sell"


@dataclass
class OrderStatusChange:
    """订单状态变更事件"""
    order_id: str = ""
    old_status: OrderStatus = OrderStatus.PENDING
    new_status: OrderStatus = OrderStatus.PENDING
    timestamp: datetime | None = None
    reason: str | None = None


class OrderType(enum.Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"


@dataclass
class GatewayOrder:
    symbol: str
    side: OrderSide
    quantity: int
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    order_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    status: OrderStatus = OrderStatus.PENDING
    created_at: Optional[datetime] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()


@dataclass
class ExecutionReport:
    order_id: str
    symbol: str
    side: OrderSide
    status: OrderStatus
    filled_quantity: int = 0
    filled_price: float = 0.0
    commission: float = 0.0
    slippage: float = 0.0
    executed_at: Optional[datetime] = None
    message: str = ""

    @property
    def is_terminal(self) -> bool:
        return self.status in (OrderStatus.FILLED, OrderStatus.CANCELLED,
                               OrderStatus.REJECTED, OrderStatus.EXPIRED)


@dataclass
class AccountSnapshot:
    cash: float = 0.0
    market_value: float = 0.0
    positions: dict[str, int] = field(default_factory=dict)
    frozen_cash: float = 0.0
    timestamp: Optional[datetime] = None

    @property
    def total_equity(self) -> float:
        return self.cash + self.market_value


class TradingGateway(ABC):
    """交易网关抽象基类"""

    def __init__(self, mode: TradingMode = TradingMode.PAPER):
        self.mode = mode
        self._connected = False

    @abstractmethod
    def connect(self) -> bool:
        ...

    @abstractmethod
    def disconnect(self) -> None:
        ...

    @abstractmethod
    def submit_order(self, order: GatewayOrder) -> ExecutionReport:
        ...

    @abstractmethod
    def cancel_order(self, order_id: str) -> bool:
        ...

    @abstractmethod
    def query_order(self, order_id: str) -> Optional[ExecutionReport]:
        ...

    @abstractmethod
    def query_account(self) -> AccountSnapshot:
        ...

    @abstractmethod
    def query_positions(self) -> dict[str, int]:
        ...

    @property
    def is_connected(self) -> bool:
        return self._connected

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *exc):
        self.disconnect()
