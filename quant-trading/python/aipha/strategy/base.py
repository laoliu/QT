"""Strategy base class and interfaces.

Strategy layer outputs ONLY abstract intentions (target_position or order_intent).
Strategy MUST NOT directly modify positions or cash.
All market rules (T+1, lot size, limits) are handled by execution layer.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aipha.domain.models import Bar, Side, Tick


class IntentType(str, Enum):
    TARGET_POSITION = "TARGET_POSITION"
    ORDER = "ORDER"
    EXIT = "EXIT"
    ENTRY = "ENTRY"


class OrderIntent:
    """Immutable order intention from strategy."""
    __slots__ = ("symbol", "side", "qty", "intent_type", "limit_price", "reason", "signal_strength", "metadata")

    def __init__(
        self,
        symbol: str,
        side: "Side",
        qty: int = 0,
        intent_type: IntentType = IntentType.ORDER,
        limit_price: float | None = None,
        reason: str | None = None,
        signal_strength: float | None = None,
        metadata: dict | None = None,
        *,
        quantity: int | None = None,
    ):
        if quantity is not None and qty == 0:
            qty = quantity
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "side", side)
        object.__setattr__(self, "qty", qty)
        object.__setattr__(self, "intent_type", intent_type)
        object.__setattr__(self, "limit_price", limit_price)
        object.__setattr__(self, "reason", reason)
        object.__setattr__(self, "signal_strength", signal_strength)
        object.__setattr__(self, "metadata", metadata)

    def __setattr__(self, _name, _value):
        raise AttributeError("OrderIntent is frozen")

    def __eq__(self, other):
        if not isinstance(other, OrderIntent):
            return NotImplemented
        return all(
            getattr(self, f) == getattr(other, f)
            for f in self.__slots__
        )

    def __hash__(self):
        return hash(tuple(getattr(self, f) for f in self.__slots__))


@dataclass
class StrategyContext:
    """Context passed to strategy on each bar."""

    current_time: datetime
    positions: dict[str, int]
    cash: float
    nav: float
    metadata: dict = field(default_factory=dict)

    def get_position(self, symbol: str) -> int:
        return self.positions.get(symbol, 0)

    @property
    def portfolio(self) -> dict[str, int]:
        """Alias for positions (backward compat)."""
        return self.positions


class Strategy(ABC):
    """Abstract base class for trading strategies."""

    name: str = "BaseStrategy"

    @classmethod
    def param_space(cls):
        """Declare parameter space for sensitivity analysis. Override to enable."""
        return None

    @abstractmethod
    def on_bar(
        self,
        bars: dict[str, "Bar"],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """Process new bar data and generate order intents."""
        raise NotImplementedError

    def on_start(self) -> None:
        pass

    def on_fill(self, symbol: str, side: "Side", qty: int, price: float) -> None:
        pass

    def on_end(self) -> None:
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"


# ============================================================================
# Tick Features — Python 侧计算的 tick 特征
# ============================================================================


@dataclass
class TickFeatures:
    """Tick 特征（对应 TS 侧 TickFeatures）。"""

    symbol: str
    timestamp: datetime
    last_price: float
    micro_vwap: float = 0.0
    short_momentum_pct: float = 0.0   # 5 tick 动量
    medium_momentum_pct: float = 0.0  # 20 tick 动量
    spread_bps: float = 0.0
    order_book_imbalance: float = 0.0
    realized_vol_bps: float = 0.0
    volume_delta: int = 0
    amount_delta: float = 0.0


@dataclass
class TickContext:
    """Context passed to tick strategy on each tick."""

    current_time: datetime
    positions: dict[str, int]
    cash: float
    nav: float
    tick: "Tick"
    features: TickFeatures
    recent_ticks: list["Tick"] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def get_position(self, symbol: str) -> int:
        return self.positions.get(symbol, 0)


class TickStrategy(ABC):
    """Tick 级别策略基类。

    同时支持实时交易和 tick 回测。策略通过 ``on_tick()`` 接收
    逐笔数据和计算好的特征，返回 ``OrderIntent`` 列表。
    """

    name: str = "BaseTickStrategy"

    @abstractmethod
    def on_tick(self, context: TickContext) -> list[OrderIntent]:
        """处理新 tick 数据，返回交易意图列表。"""
        raise NotImplementedError

    def on_start(self) -> None:
        pass

    def on_fill(self, symbol: str, side: "Side", qty: int, price: float) -> None:
        pass

    def on_end(self) -> None:
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
