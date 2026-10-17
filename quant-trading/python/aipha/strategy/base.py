"""Strategy base class and interfaces.

This module defines the core abstractions for trading strategies:
- OrderIntent: immutable order intention from strategy (not yet validated)
- Strategy: abstract base class for all trading strategies

Design principles (per copilot-instructions.md):
- Strategy layer outputs ONLY abstract intentions (target_position or order_intent)
- Strategy MUST NOT directly modify positions or cash
- All market rules (T+1, lot size, limits) are handled by execution layer
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

# §13.2: OrderIntent / IntentType 的唯一定义在 domain/models.py
# 此处 re-export 保持向后兼容
from aipha.domain.models import IntentType, OrderIntent, Tick  # noqa: F401

if TYPE_CHECKING:
    from aipha.domain.models import Bar, Side
    from aipha.strategy.param_space import ParamSpace


@dataclass
class StrategyContext:
    """Context passed to strategy on each bar.

    Contains everything the strategy needs to make decisions,
    but does NOT allow direct modification of state.
    """

    current_time: datetime
    positions: dict[str, int]  # symbol -> current qty (read-only view)
    cash: float
    nav: float  # Net Asset Value
    metadata: dict = field(default_factory=dict)

    def get_position(self, symbol: str) -> int:
        """Get current position for symbol (0 if not held)."""
        return self.positions.get(symbol, 0)


@dataclass
class TickFeatures:
    symbol: str
    timestamp: datetime
    last_price: float
    micro_vwap: float = 0.0
    short_momentum_pct: float = 0.0
    medium_momentum_pct: float = 0.0
    spread_bps: float = 0.0
    order_book_imbalance: float = 0.0
    realized_vol_bps: float = 0.0
    volume_delta: int = 0
    amount_delta: float = 0.0


@dataclass(kw_only=True)
class TickContext(StrategyContext):
    tick: Tick
    features: TickFeatures
    recent_ticks: list[Tick] = field(default_factory=list)


class TickStrategy(ABC):
    """Tick-only strategy lifecycle, independent of bar registration."""

    name = "TickStrategy"

    @abstractmethod
    def on_tick(self, context: TickContext) -> list[OrderIntent]:
        raise NotImplementedError

    def on_start(self) -> None:
        pass

    def on_fill(self, symbol: str, side: "Side", qty: int, price: float) -> None:
        pass

    def on_end(self) -> None:
        pass


class Strategy(ABC):
    """Abstract base class for trading strategies.

    All strategies must implement:
    - on_bar(): Generate order intents based on bar data

    Optional:
    - param_space(): Declare parameter space for sensitivity analysis and grid search

    Strategy is stateless by design - all state should be managed
    by the backtest engine (positions, cash, etc.).
    """

    name: str = "BaseStrategy"

    @classmethod
    def param_space(cls) -> "ParamSpace | None":
        """Declare parameter space for this strategy.

        Override to enable automated parameter sweep, sensitivity analysis,
        and robustness checking. Returns None by default (no param space declared).

        Returns:
            ParamSpace or None
        """
        return None

    @abstractmethod
    def on_bar(
        self,
        bars: dict[str, "Bar"],  # symbol -> Bar
        context: StrategyContext,
    ) -> list[OrderIntent]:
        """Process new bar data and generate order intents.

        Args:
            bars: Dictionary of symbol -> Bar for current timestamp
            context: Read-only context with positions, cash, etc.

        Returns:
            List of OrderIntent objects (can be empty)
        """
        raise NotImplementedError

    def on_start(self) -> None:
        """Called once before backtest starts. Override if needed."""
        pass

    def on_fill(self, symbol: str, side: "Side", qty: int, price: float) -> None:
        """Called after an order is filled.

        Override to track actual fill prices (e.g. for stop-loss).

        Args:
            symbol: Filled instrument symbol
            side: BUY or SELL
            qty: Filled quantity
            price: Actual fill price (after slippage)
        """
        pass

    def on_end(self) -> None:
        """Called once after backtest ends. Override if needed."""
        pass

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
