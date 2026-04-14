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
    from aipha.domain.models import Bar, Side


class IntentType(str, Enum):
    TARGET_POSITION = "TARGET_POSITION"
    ORDER = "ORDER"


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """Immutable order intention from strategy."""

    symbol: str
    side: "Side"
    qty: int
    intent_type: IntentType = IntentType.ORDER
    limit_price: float | None = None
    reason: str | None = None
    signal_strength: float | None = None
    metadata: dict | None = None

    def __post_init__(self):
        if self.qty <= 0:
            raise ValueError(f"qty must be positive, got {self.qty}")


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
