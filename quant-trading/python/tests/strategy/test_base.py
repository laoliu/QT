"""Tests for strategy base classes.

Coverage targets:
- OrderIntent: validation and immutability
- StrategyContext: position access
- Strategy: abstract interface and lifecycle

Quality gates: 100% coverage.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from aipha.domain.models import Bar, Side
from aipha.strategy.base import (
    IntentType,
    OrderIntent,
    Strategy,
    StrategyContext,
)


# ============================================================================
# OrderIntent Tests
# ============================================================================


class TestOrderIntent:
    """Tests for OrderIntent dataclass."""

    def test_basic_order_intent(self):
        """Create basic order intent."""
        intent = OrderIntent(
            symbol="600000.SH",
            side=Side.BUY,
            qty=100,
        )

        assert intent.symbol == "600000.SH"
        assert intent.side == Side.BUY
        assert intent.qty == 100
        assert intent.intent_type == IntentType.ORDER
        assert intent.limit_price is None
        assert intent.reason is None

    def test_order_intent_with_all_fields(self):
        """Create order intent with all optional fields."""
        intent = OrderIntent(
            symbol="600519.SH",
            side=Side.SELL,
            qty=200,
            intent_type=IntentType.TARGET_POSITION,
            limit_price=1800.00,
            reason="Profit taking",
            signal_strength=0.85,
            metadata={"indicator": "RSI", "value": 75},
        )

        assert intent.intent_type == IntentType.TARGET_POSITION
        assert intent.limit_price == 1800.00
        assert intent.reason == "Profit taking"
        assert intent.signal_strength == 0.85
        assert intent.metadata == {"indicator": "RSI", "value": 75}

    def test_order_intent_immutability(self):
        """OrderIntent should be immutable."""
        intent = OrderIntent(symbol="600000.SH", side=Side.BUY, qty=100)

        with pytest.raises(Exception):  # FrozenInstanceError
            intent.qty = 200

    def test_order_intent_zero_qty_raises(self):
        """Zero quantity should raise ValueError."""
        with pytest.raises(ValueError, match="qty must be positive"):
            OrderIntent(symbol="600000.SH", side=Side.BUY, qty=0)

    def test_order_intent_negative_qty_raises(self):
        """Negative quantity should raise ValueError."""
        with pytest.raises(ValueError, match="qty must be positive"):
            OrderIntent(symbol="600000.SH", side=Side.BUY, qty=-100)


# ============================================================================
# IntentType Tests
# ============================================================================


class TestIntentType:
    """Tests for IntentType enum."""

    def test_intent_types(self):
        """Verify all intent types exist."""
        assert IntentType.TARGET_POSITION == "TARGET_POSITION"
        assert IntentType.ORDER == "ORDER"

    def test_intent_type_is_string(self):
        """IntentType should be string enum."""
        assert isinstance(IntentType.ORDER, str)


# ============================================================================
# StrategyContext Tests
# ============================================================================


class TestStrategyContext:
    """Tests for StrategyContext dataclass."""

    def test_basic_context(self):
        """Create basic strategy context."""
        context = StrategyContext(
            current_time=datetime(2025, 1, 6, 9, 31),
            positions={"600000.SH": 1000, "600519.SH": 100},
            cash=100000.0,
            nav=200000.0,
        )

        assert context.current_time == datetime(2025, 1, 6, 9, 31)
        assert context.positions == {"600000.SH": 1000, "600519.SH": 100}
        assert context.cash == 100000.0
        assert context.nav == 200000.0

    def test_get_position_existing(self):
        """get_position should return qty for existing symbol."""
        context = StrategyContext(
            current_time=datetime(2025, 1, 6, 9, 31),
            positions={"600000.SH": 1000},
            cash=100000.0,
            nav=200000.0,
        )

        assert context.get_position("600000.SH") == 1000

    def test_get_position_non_existing(self):
        """get_position should return 0 for non-existing symbol."""
        context = StrategyContext(
            current_time=datetime(2025, 1, 6, 9, 31),
            positions={"600000.SH": 1000},
            cash=100000.0,
            nav=200000.0,
        )

        assert context.get_position("000001.SZ") == 0

    def test_context_with_metadata(self):
        """Context can include custom metadata."""
        context = StrategyContext(
            current_time=datetime(2025, 1, 6, 9, 31),
            positions={},
            cash=100000.0,
            nav=100000.0,
            metadata={"market_regime": "bull", "volatility": 0.15},
        )

        assert context.metadata["market_regime"] == "bull"


# ============================================================================
# Strategy Abstract Class Tests
# ============================================================================


class DummyStrategy(Strategy):
    """Concrete strategy for testing."""

    name = "DummyStrategy"

    def __init__(self, always_buy: bool = False):
        self.always_buy = always_buy
        self.started = False
        self.ended = False

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        if self.always_buy and bars:
            symbol = list(bars.keys())[0]
            return [OrderIntent(symbol=symbol, side=Side.BUY, qty=100)]
        return []

    def on_start(self) -> None:
        self.started = True

    def on_end(self) -> None:
        self.ended = True


class TestStrategy:
    """Tests for Strategy abstract class."""

    def test_strategy_repr(self):
        """Strategy should have readable repr."""
        strategy = DummyStrategy()
        assert "DummyStrategy" in repr(strategy)

    def test_strategy_on_bar_empty(self):
        """Strategy with no signals returns empty list."""
        strategy = DummyStrategy(always_buy=False)
        context = StrategyContext(
            current_time=datetime(2025, 1, 6, 9, 31),
            positions={},
            cash=100000.0,
            nav=100000.0,
        )

        intents = strategy.on_bar(bars={}, context=context)

        assert intents == []

    def test_strategy_on_bar_with_signal(self):
        """Strategy with signal returns order intent."""
        strategy = DummyStrategy(always_buy=True)
        bar = Bar(
            symbol="600000.SH",
            start_ts=datetime(2025, 1, 6, 9, 31),
            end_ts=datetime(2025, 1, 6, 9, 32),
            open=10.00,
            high=10.05,
            low=9.95,
            close=10.02,
            volume=100000,
        )
        context = StrategyContext(
            current_time=datetime(2025, 1, 6, 9, 31),
            positions={},
            cash=100000.0,
            nav=100000.0,
        )

        intents = strategy.on_bar(bars={"600000.SH": bar}, context=context)

        assert len(intents) == 1
        assert intents[0].symbol == "600000.SH"
        assert intents[0].side == Side.BUY
        assert intents[0].qty == 100

    def test_strategy_lifecycle(self):
        """Strategy lifecycle methods should be called."""
        strategy = DummyStrategy()

        assert not strategy.started
        assert not strategy.ended

        strategy.on_start()
        assert strategy.started

        strategy.on_end()
        assert strategy.ended

    def test_abstract_strategy_cannot_instantiate(self):
        """Cannot instantiate abstract Strategy directly."""
        with pytest.raises(TypeError):
            Strategy()  # type: ignore


# ============================================================================
# Integration Tests
# ============================================================================


class TestStrategyIntegration:
    """Integration tests for strategy components."""

    def test_strategy_generates_valid_intents(self):
        """Strategy should generate intents that can be processed."""
        strategy = DummyStrategy(always_buy=True)
        bar = Bar(
            symbol="600000.SH",
            start_ts=datetime(2025, 1, 6, 9, 31),
            end_ts=datetime(2025, 1, 6, 9, 32),
            open=10.00,
            high=10.05,
            low=9.95,
            close=10.02,
            volume=100000,
        )
        context = StrategyContext(
            current_time=datetime(2025, 1, 6, 9, 31),
            positions={},
            cash=100000.0,
            nav=100000.0,
        )

        intents = strategy.on_bar(bars={"600000.SH": bar}, context=context)

        # Verify intents can be used to create orders
        for intent in intents:
            assert intent.symbol is not None
            assert intent.side in (Side.BUY, Side.SELL)
            assert intent.qty > 0

    def test_context_read_only_design(self):
        """Context should encourage read-only access to positions."""
        context = StrategyContext(
            current_time=datetime(2025, 1, 6, 9, 31),
            positions={"600000.SH": 1000},
            cash=100000.0,
            nav=200000.0,
        )

        # Strategy reads position via get_position (preferred)
        pos = context.get_position("600000.SH")
        assert pos == 1000

        # Note: In Python, we can't truly prevent modification,
        # but the interface encourages read-only access
