"""Tests for fill models.

Coverage targets:
- NextBarOpenFillModel: market order fill at next bar open
- NextBarCloseFillModel: market order fill at next bar close  
- BarLimitTouchFillModel: limit order touch detection

Quality gates: >90% coverage, all edge cases covered.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from aipha.domain.models import Bar, Order, OrderType, Side
from aipha.execution.fill_models import (
    BarLimitTouchFillModel,
    FillResult,
    NextBarCloseFillModel,
    NextBarOpenFillModel,
    NoFillReason,
)
from aipha.market.rules_cn_equity import MarketRulesCNEquity, RejectReason


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def rules() -> MarketRulesCNEquity:
    """Standard A-share rules."""
    return MarketRulesCNEquity(lot_size=100)


@pytest.fixture
def normal_bar() -> Bar:
    """Normal trading bar without halt/limit states."""
    return Bar(
        symbol="600000.SH",
        start_ts=datetime(2025, 1, 6, 9, 31),
        end_ts=datetime(2025, 1, 6, 9, 32),
        open=10.00,
        high=10.05,
        low=9.95,
        close=10.02,
        volume=100000,
        is_suspended=False,
        is_limit_up=False,
        is_limit_down=False,
    )


@pytest.fixture
def suspended_bar() -> Bar:
    """Suspended bar."""
    return Bar(
        symbol="600000.SH",
        start_ts=datetime(2025, 1, 6, 9, 31),
        end_ts=datetime(2025, 1, 6, 9, 32),
        open=10.00,
        high=10.00,
        low=10.00,
        close=10.00,
        volume=0,
        is_suspended=True,
        is_limit_up=False,
        is_limit_down=False,
    )


@pytest.fixture
def limit_up_bar() -> Bar:
    """Limit up bar."""
    return Bar(
        symbol="600000.SH",
        start_ts=datetime(2025, 1, 6, 9, 31),
        end_ts=datetime(2025, 1, 6, 9, 32),
        open=11.00,
        high=11.00,
        low=11.00,
        close=11.00,
        volume=50000,
        is_suspended=False,
        is_limit_up=True,
        is_limit_down=False,
    )


@pytest.fixture
def limit_down_bar() -> Bar:
    """Limit down bar."""
    return Bar(
        symbol="600000.SH",
        start_ts=datetime(2025, 1, 6, 9, 31),
        end_ts=datetime(2025, 1, 6, 9, 32),
        open=9.00,
        high=9.00,
        low=9.00,
        close=9.00,
        volume=50000,
        is_suspended=False,
        is_limit_up=False,
        is_limit_down=True,
    )


def make_order(
    side: Side = Side.BUY,
    qty: int = 100,
    order_type: OrderType = OrderType.MARKET,
    limit_price: float | None = None,
) -> Order:
    """Helper to create orders."""
    return Order(
        order_id="ORD001",
        symbol="600000.SH",
        side=side,
        qty=qty,
        order_type=order_type,
        t_submit=datetime(2025, 1, 6, 9, 30),
        limit_price=limit_price,
    )


# ============================================================================
# NextBarOpenFillModel Tests
# ============================================================================


class TestNextBarOpenFillModel:
    """Tests for NextBarOpenFillModel."""

    def test_model_name(self):
        """Model should have correct name."""
        model = NextBarOpenFillModel()
        assert model.name == "next_bar_open"

    def test_buy_order_fills_at_open(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """BUY market order should fill at next bar open price."""
        model = NextBarOpenFillModel()
        order = make_order(side=Side.BUY, qty=100)

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is not None
        assert result.fill.price == normal_bar.open  # 10.00
        assert result.fill.qty == 100
        assert result.fill.side == Side.BUY
        assert result.fill.t_fill == normal_bar.start_ts
        assert result.fill.model == "next_bar_open"
        assert result.reject_reason is None
        assert result.no_fill_reason is None

    def test_sell_order_fills_at_open(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """SELL market order should fill at next bar open price."""
        model = NextBarOpenFillModel()
        order = make_order(side=Side.SELL, qty=200)

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is not None
        assert result.fill.price == normal_bar.open  # 10.00
        assert result.fill.qty == 200
        assert result.fill.side == Side.SELL

    def test_suspended_bar_rejects(self, rules: MarketRulesCNEquity, suspended_bar: Bar):
        """Order on suspended bar should be rejected."""
        model = NextBarOpenFillModel()
        order = make_order(side=Side.BUY, qty=100)

        result = model.try_fill(order=order, next_bar=suspended_bar, rules=rules)

        assert result.fill is None
        assert result.reject_reason == RejectReason.SUSPENDED

    def test_limit_up_rejects_buy(self, rules: MarketRulesCNEquity, limit_up_bar: Bar):
        """BUY on limit-up bar should be rejected (conservative)."""
        model = NextBarOpenFillModel()
        order = make_order(side=Side.BUY, qty=100)

        result = model.try_fill(order=order, next_bar=limit_up_bar, rules=rules)

        assert result.fill is None
        assert result.reject_reason == RejectReason.LIMIT_UP

    def test_limit_up_allows_sell(self, rules: MarketRulesCNEquity, limit_up_bar: Bar):
        """SELL on limit-up bar should be allowed."""
        model = NextBarOpenFillModel()
        order = make_order(side=Side.SELL, qty=100)

        result = model.try_fill(order=order, next_bar=limit_up_bar, rules=rules)

        assert result.fill is not None
        assert result.fill.price == limit_up_bar.open

    def test_limit_down_rejects_sell(self, rules: MarketRulesCNEquity, limit_down_bar: Bar):
        """SELL on limit-down bar should be rejected (conservative)."""
        model = NextBarOpenFillModel()
        order = make_order(side=Side.SELL, qty=100)

        result = model.try_fill(order=order, next_bar=limit_down_bar, rules=rules)

        assert result.fill is None
        assert result.reject_reason == RejectReason.LIMIT_DOWN

    def test_limit_down_allows_buy(self, rules: MarketRulesCNEquity, limit_down_bar: Bar):
        """BUY on limit-down bar should be allowed."""
        model = NextBarOpenFillModel()
        order = make_order(side=Side.BUY, qty=100)

        result = model.try_fill(order=order, next_bar=limit_down_bar, rules=rules)

        assert result.fill is not None
        assert result.fill.price == limit_down_bar.open

    def test_lot_size_violation_rejects(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """Order quantity not divisible by lot_size should be rejected."""
        model = NextBarOpenFillModel()
        order = make_order(side=Side.BUY, qty=150)  # Not divisible by 100

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is None
        assert result.reject_reason == RejectReason.LOT_SIZE_ROUNDING


# ============================================================================
# NextBarCloseFillModel Tests
# ============================================================================


class TestNextBarCloseFillModel:
    """Tests for NextBarCloseFillModel."""

    def test_model_name(self):
        """Model should have correct name."""
        model = NextBarCloseFillModel()
        assert model.name == "next_bar_close"

    def test_buy_order_fills_at_close(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """BUY order should fill at next bar close price."""
        model = NextBarCloseFillModel()
        order = make_order(side=Side.BUY, qty=100)

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is not None
        assert result.fill.price == normal_bar.close  # 10.02
        assert result.fill.t_fill == normal_bar.end_ts
        assert result.fill.model == "next_bar_close"

    def test_sell_order_fills_at_close(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """SELL order should fill at next bar close price."""
        model = NextBarCloseFillModel()
        order = make_order(side=Side.SELL, qty=300)

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is not None
        assert result.fill.price == normal_bar.close

    def test_suspended_bar_rejects(self, rules: MarketRulesCNEquity, suspended_bar: Bar):
        """Order on suspended bar should be rejected."""
        model = NextBarCloseFillModel()
        order = make_order(side=Side.BUY, qty=100)

        result = model.try_fill(order=order, next_bar=suspended_bar, rules=rules)

        assert result.fill is None
        assert result.reject_reason == RejectReason.SUSPENDED


# ============================================================================
# BarLimitTouchFillModel Tests
# ============================================================================


class TestBarLimitTouchFillModel:
    """Tests for BarLimitTouchFillModel."""

    def test_model_name(self):
        """Model should have correct name."""
        model = BarLimitTouchFillModel()
        assert model.name == "bar_limit_touch"

    def test_buy_limit_touched_fills(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """BUY limit order touched (price >= bar.low) should fill at limit price."""
        model = BarLimitTouchFillModel()
        # Bar low = 9.95, so limit_price >= 9.95 is touched
        order = make_order(
            side=Side.BUY, qty=100, order_type=OrderType.LIMIT, limit_price=9.98
        )

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is not None
        assert result.fill.price == 9.98  # Fill at limit price
        assert result.fill.model == "bar_limit_touch"
        assert result.no_fill_reason is None

    def test_buy_limit_exactly_at_low_fills(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """BUY limit exactly at bar.low should fill."""
        model = BarLimitTouchFillModel()
        order = make_order(
            side=Side.BUY, qty=100, order_type=OrderType.LIMIT, limit_price=9.95
        )

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is not None
        assert result.fill.price == 9.95

    def test_buy_limit_not_touched_no_fill(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """BUY limit below bar.low should not fill."""
        model = BarLimitTouchFillModel()
        # Bar low = 9.95, limit_price = 9.90 < 9.95 → not touched
        order = make_order(
            side=Side.BUY, qty=100, order_type=OrderType.LIMIT, limit_price=9.90
        )

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is None
        assert result.no_fill_reason == NoFillReason.LIMIT_NOT_TOUCHED
        assert result.reject_reason is None

    def test_sell_limit_touched_fills(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """SELL limit order touched (price <= bar.high) should fill at limit price."""
        model = BarLimitTouchFillModel()
        # Bar high = 10.05, so limit_price <= 10.05 is touched
        order = make_order(
            side=Side.SELL, qty=100, order_type=OrderType.LIMIT, limit_price=10.02
        )

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is not None
        assert result.fill.price == 10.02

    def test_sell_limit_exactly_at_high_fills(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """SELL limit exactly at bar.high should fill."""
        model = BarLimitTouchFillModel()
        order = make_order(
            side=Side.SELL, qty=100, order_type=OrderType.LIMIT, limit_price=10.05
        )

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is not None
        assert result.fill.price == 10.05

    def test_sell_limit_not_touched_no_fill(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """SELL limit above bar.high should not fill."""
        model = BarLimitTouchFillModel()
        # Bar high = 10.05, limit_price = 10.10 > 10.05 → not touched
        order = make_order(
            side=Side.SELL, qty=100, order_type=OrderType.LIMIT, limit_price=10.10
        )

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is None
        assert result.no_fill_reason == NoFillReason.LIMIT_NOT_TOUCHED

    def test_limit_order_without_price_raises(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """Limit order without limit_price should raise ValueError."""
        model = BarLimitTouchFillModel()
        order = make_order(side=Side.BUY, qty=100, order_type=OrderType.LIMIT, limit_price=None)

        with pytest.raises(ValueError, match="limit_price"):
            model.try_fill(order=order, next_bar=normal_bar, rules=rules)

    def test_suspended_bar_rejects(self, rules: MarketRulesCNEquity, suspended_bar: Bar):
        """Limit order on suspended bar should be rejected."""
        model = BarLimitTouchFillModel()
        order = make_order(
            side=Side.BUY, qty=100, order_type=OrderType.LIMIT, limit_price=10.00
        )

        result = model.try_fill(order=order, next_bar=suspended_bar, rules=rules)

        assert result.fill is None
        assert result.reject_reason == RejectReason.SUSPENDED


# ============================================================================
# FillResult Tests
# ============================================================================


class TestFillResult:
    """Tests for FillResult dataclass."""

    def test_fill_result_with_fill(self):
        """FillResult with successful fill."""
        from aipha.domain.models import Fill

        fill = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=100,
            price=10.00,
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="test",
        )
        result = FillResult(fill=fill)

        assert result.fill == fill
        assert result.reject_reason is None
        assert result.no_fill_reason is None

    def test_fill_result_with_rejection(self):
        """FillResult with rejection reason."""
        result = FillResult(fill=None, reject_reason=RejectReason.SUSPENDED)

        assert result.fill is None
        assert result.reject_reason == RejectReason.SUSPENDED
        assert result.no_fill_reason is None

    def test_fill_result_with_no_fill_reason(self):
        """FillResult with no-fill reason (e.g., limit not touched)."""
        result = FillResult(fill=None, no_fill_reason=NoFillReason.LIMIT_NOT_TOUCHED)

        assert result.fill is None
        assert result.reject_reason is None
        assert result.no_fill_reason == NoFillReason.LIMIT_NOT_TOUCHED

    def test_fill_result_properties_full_fill(self):
        """FillResult properties for full fill."""
        from aipha.domain.models import Fill

        fill = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=100,
            price=10.00,
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="test",
        )
        result = FillResult(fill=fill, remaining_qty=0)

        assert result.is_full is True
        assert result.is_partial is False
        assert result.is_rejected is False

    def test_fill_result_properties_partial_fill(self):
        """FillResult properties for partial fill."""
        from aipha.domain.models import Fill

        fill = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=100,
            price=10.00,
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="test",
        )
        result = FillResult(fill=fill, remaining_qty=200)

        assert result.is_full is False
        assert result.is_partial is True
        assert result.is_rejected is False

    def test_fill_result_properties_rejected(self):
        """FillResult properties for rejected order."""
        result = FillResult(fill=None, reject_reason=RejectReason.SUSPENDED)

        assert result.is_full is False
        assert result.is_partial is False
        assert result.is_rejected is True


# ============================================================================
# VWAPFillModel Tests
# ============================================================================


class TestVWAPFillModel:
    """Tests for VWAPFillModel."""

    def test_model_name(self):
        """Model should have correct name."""
        from aipha.execution.fill_models import VWAPFillModel

        model = VWAPFillModel()
        assert model.name == "vwap"

    def test_fill_at_vwap_when_available(self, rules: MarketRulesCNEquity):
        """Should fill at VWAP when available."""
        from aipha.execution.fill_models import VWAPFillModel

        bar_with_vwap = Bar(
            symbol="600000.SH",
            start_ts=datetime(2025, 1, 6, 9, 31),
            end_ts=datetime(2025, 1, 6, 9, 32),
            open=10.00,
            high=10.10,
            low=9.90,
            close=10.05,
            volume=100000,
            vwap=10.03,
            is_suspended=False,
        )
        model = VWAPFillModel()
        order = make_order(side=Side.BUY, qty=100)

        result = model.try_fill(order=order, next_bar=bar_with_vwap, rules=rules)

        assert result.fill is not None
        assert result.fill.price == 10.03  # VWAP
        assert result.fill.t_fill == bar_with_vwap.end_ts

    def test_fill_at_midpoint_when_no_vwap(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """Should fill at midpoint when VWAP not available."""
        from aipha.execution.fill_models import VWAPFillModel

        model = VWAPFillModel()
        order = make_order(side=Side.BUY, qty=100)

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is not None
        expected_midpoint = (normal_bar.high + normal_bar.low) / 2  # (10.05 + 9.95) / 2 = 10.00
        assert result.fill.price == expected_midpoint

    def test_suspended_bar_rejects(self, rules: MarketRulesCNEquity, suspended_bar: Bar):
        """VWAP order on suspended bar should be rejected."""
        from aipha.execution.fill_models import VWAPFillModel

        model = VWAPFillModel()
        order = make_order(side=Side.BUY, qty=100)

        result = model.try_fill(order=order, next_bar=suspended_bar, rules=rules)

        assert result.fill is None
        assert result.reject_reason == RejectReason.SUSPENDED


# ============================================================================
# VolumeConstraint Tests
# ============================================================================


class TestVolumeConstraint:
    """Tests for VolumeConstraint."""

    def test_default_values(self):
        """Default constraint should have 10% participation, 100 lot size, 0.15 fraction."""
        from aipha.execution.fill_models import VolumeConstraint

        constraint = VolumeConstraint()
        assert constraint.max_participation_rate == 0.10
        assert constraint.lot_size == 100
        assert constraint.opening_volume_fraction == 0.15

    def test_compute_max_qty(self):
        """Max qty should be participation rate * volume * fraction, rounded to lot."""
        from aipha.execution.fill_models import VolumeConstraint

        # Explicit full volume to test basic calculation
        constraint = VolumeConstraint(
            max_participation_rate=0.10, lot_size=100,
            opening_volume_fraction=1.0,
        )

        # 100000 * 1.0 * 0.10 = 10000
        assert constraint.compute_max_qty(100000) == 10000

        # 5000 * 0.10 = 500
        assert constraint.compute_max_qty(5000) == 500

        # 550 * 0.10 = 55 → rounded to 0 (less than lot size)
        assert constraint.compute_max_qty(550) == 0

        # 1050 * 0.10 = 105 → rounded to 100
        assert constraint.compute_max_qty(1050) == 100

    def test_invalid_participation_rate_raises(self):
        """Participation rate must be in (0, 1]."""
        from aipha.execution.fill_models import VolumeConstraint

        with pytest.raises(ValueError, match="participation_rate"):
            VolumeConstraint(max_participation_rate=0.0)

        with pytest.raises(ValueError, match="participation_rate"):
            VolumeConstraint(max_participation_rate=1.5)

        with pytest.raises(ValueError, match="participation_rate"):
            VolumeConstraint(max_participation_rate=-0.1)

    def test_100_percent_participation(self):
        """100% participation should be allowed."""
        from aipha.execution.fill_models import VolumeConstraint

        constraint = VolumeConstraint(max_participation_rate=1.0, opening_volume_fraction=1.0)
        assert constraint.compute_max_qty(1000) == 1000


# ============================================================================
# VolumeConstrainedFillModel Tests
# ============================================================================


class TestVolumeConstrainedFillModel:
    """Tests for VolumeConstrainedFillModel."""

    def test_model_name(self):
        """Model name should include inner model name."""
        from aipha.execution.fill_models import (
            NextBarOpenFillModel,
            VolumeConstrainedFillModel,
        )

        inner = NextBarOpenFillModel()
        model = VolumeConstrainedFillModel(inner_model=inner)
        assert model.name == "next_bar_open_volume_constrained"

    def test_full_fill_when_under_volume_limit(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """Order under volume limit should fully fill."""
        from aipha.execution.fill_models import (
            NextBarOpenFillModel,
            VolumeConstraint,
            VolumeConstrainedFillModel,
        )

        inner = NextBarOpenFillModel()
        constraint = VolumeConstraint(max_participation_rate=0.10)  # 100000 * 0.1 = 10000
        model = VolumeConstrainedFillModel(inner_model=inner, constraint=constraint)
        order = make_order(side=Side.BUY, qty=100)  # 100 < 10000

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is not None
        assert result.fill.qty == 100
        assert result.remaining_qty == 0
        assert result.is_full is True

    def test_partial_fill_when_over_volume_limit(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """Order over volume limit should partially fill."""
        from aipha.execution.fill_models import (
            NextBarOpenFillModel,
            VolumeConstraint,
            VolumeConstrainedFillModel,
        )

        inner = NextBarOpenFillModel()
        constraint = VolumeConstraint(max_participation_rate=0.10, opening_volume_fraction=1.0)  # max 10000
        model = VolumeConstrainedFillModel(inner_model=inner, constraint=constraint)
        # Order for 50000 shares, but max allowed is 10000
        order = make_order(side=Side.BUY, qty=50000)

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is not None
        assert result.fill.qty == 10000
        assert result.remaining_qty == 40000
        assert result.is_partial is True

    def test_zero_volume_bar_rejects(self, rules: MarketRulesCNEquity):
        """Order on zero-volume bar should not fill."""
        from aipha.execution.fill_models import (
            NextBarOpenFillModel,
            VolumeConstrainedFillModel,
            NoFillReason,
        )

        zero_vol_bar = Bar(
            symbol="600000.SH",
            start_ts=datetime(2025, 1, 6, 9, 31),
            end_ts=datetime(2025, 1, 6, 9, 32),
            open=10.00,
            high=10.00,
            low=10.00,
            close=10.00,
            volume=0,
            is_suspended=False,
        )

        inner = NextBarOpenFillModel()
        model = VolumeConstrainedFillModel(inner_model=inner)
        order = make_order(side=Side.BUY, qty=100)

        result = model.try_fill(order=order, next_bar=zero_vol_bar, rules=rules)

        assert result.fill is None
        assert result.no_fill_reason == NoFillReason.ZERO_VOLUME

    def test_volume_exhausted_when_max_qty_too_small(self, rules: MarketRulesCNEquity):
        """When max qty rounds to 0, should return VOLUME_EXHAUSTED."""
        from aipha.execution.fill_models import (
            NextBarOpenFillModel,
            VolumeConstraint,
            VolumeConstrainedFillModel,
            NoFillReason,
        )

        low_vol_bar = Bar(
            symbol="600000.SH",
            start_ts=datetime(2025, 1, 6, 9, 31),
            end_ts=datetime(2025, 1, 6, 9, 32),
            open=10.00,
            high=10.05,
            low=9.95,
            close=10.02,
            volume=500,  # 500 * 0.10 = 50 < 100 lot size
            is_suspended=False,
        )

        inner = NextBarOpenFillModel()
        constraint = VolumeConstraint(max_participation_rate=0.10)
        model = VolumeConstrainedFillModel(inner_model=inner, constraint=constraint)
        order = make_order(side=Side.BUY, qty=100)

        result = model.try_fill(order=order, next_bar=low_vol_bar, rules=rules)

        assert result.fill is None
        assert result.no_fill_reason == NoFillReason.VOLUME_EXHAUSTED

    def test_inner_model_rejection_propagates(self, rules: MarketRulesCNEquity, suspended_bar: Bar):
        """Inner model rejection should propagate through."""
        from aipha.execution.fill_models import (
            NextBarOpenFillModel,
            VolumeConstrainedFillModel,
        )

        # Create a suspended bar with positive volume for the constraint check to pass
        suspended_with_vol = Bar(
            symbol="600000.SH",
            start_ts=datetime(2025, 1, 6, 9, 31),
            end_ts=datetime(2025, 1, 6, 9, 32),
            open=10.00,
            high=10.00,
            low=10.00,
            close=10.00,
            volume=100000,
            is_suspended=True,
        )

        inner = NextBarOpenFillModel()
        model = VolumeConstrainedFillModel(inner_model=inner)
        order = make_order(side=Side.BUY, qty=100)

        result = model.try_fill(order=order, next_bar=suspended_with_vol, rules=rules)

        assert result.fill is None
        assert result.reject_reason == RejectReason.SUSPENDED


# ============================================================================
# TwapSliceFillModel Tests
# ============================================================================


class TestTwapSliceFillModel:
    """Tests for TwapSliceFillModel."""

    def test_model_name(self):
        """Model should have correct name."""
        from aipha.execution.fill_models import TwapSliceFillModel

        model = TwapSliceFillModel(n_slices=10)
        assert model.name == "twap_slice"

    def test_slices_order_uniformly(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """Should slice order into uniform parts."""
        from aipha.execution.fill_models import TwapSliceFillModel

        model = TwapSliceFillModel(n_slices=10)
        order = make_order(side=Side.BUY, qty=1000)

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is not None
        assert result.fill.qty == 100  # 1000 / 10
        assert result.remaining_qty == 900

    def test_fills_at_vwap(self, rules: MarketRulesCNEquity):
        """Should fill at VWAP when available."""
        from aipha.execution.fill_models import TwapSliceFillModel

        bar_with_vwap = Bar(
            symbol="600000.SH",
            start_ts=datetime(2025, 1, 6, 9, 31),
            end_ts=datetime(2025, 1, 6, 9, 32),
            open=10.00,
            high=10.10,
            low=9.90,
            close=10.05,
            volume=100000,
            vwap=10.03,
            is_suspended=False,
        )
        model = TwapSliceFillModel(n_slices=5)
        order = make_order(side=Side.BUY, qty=500)

        result = model.try_fill(order=order, next_bar=bar_with_vwap, rules=rules)

        assert result.fill is not None
        assert result.fill.price == 10.03

    def test_fills_at_midpoint_when_no_vwap(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """Should fill at midpoint when VWAP not available."""
        from aipha.execution.fill_models import TwapSliceFillModel

        model = TwapSliceFillModel(n_slices=5)
        order = make_order(side=Side.BUY, qty=500)

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is not None
        expected_midpoint = (normal_bar.high + normal_bar.low) / 2
        assert result.fill.price == expected_midpoint

    def test_small_order_returns_volume_exhausted(self, rules: MarketRulesCNEquity, normal_bar: Bar):
        """Very small order relative to slices returns volume exhausted."""
        from aipha.execution.fill_models import TwapSliceFillModel, NoFillReason

        model = TwapSliceFillModel(n_slices=10)
        # 500 / 10 = 50, rounds to 0 (< 100 lot size)
        order = make_order(side=Side.BUY, qty=500)

        result = model.try_fill(order=order, next_bar=normal_bar, rules=rules)

        assert result.fill is None
        assert result.no_fill_reason == NoFillReason.VOLUME_EXHAUSTED

    def test_invalid_n_slices_raises(self):
        """n_slices must be >= 1."""
        from aipha.execution.fill_models import TwapSliceFillModel

        with pytest.raises(ValueError, match="n_slices"):
            TwapSliceFillModel(n_slices=0)


# ============================================================================
# Factory Tests
# ============================================================================


class TestFillModelFactory:
    """Tests for fill model factory functions."""

    def test_get_fill_model_next_bar_open(self):
        """get_fill_model should return NextBarOpenFillModel."""
        from aipha.execution.fill_models import get_fill_model, NextBarOpenFillModel

        model = get_fill_model("next_bar_open")
        assert isinstance(model, NextBarOpenFillModel)

    def test_get_fill_model_next_bar_close(self):
        """get_fill_model should return NextBarCloseFillModel."""
        from aipha.execution.fill_models import get_fill_model, NextBarCloseFillModel

        model = get_fill_model("next_bar_close")
        assert isinstance(model, NextBarCloseFillModel)

    def test_get_fill_model_vwap(self):
        """get_fill_model should return VWAPFillModel."""
        from aipha.execution.fill_models import get_fill_model, VWAPFillModel

        model = get_fill_model("vwap")
        assert isinstance(model, VWAPFillModel)

    def test_get_fill_model_unknown_raises(self):
        """get_fill_model should raise KeyError for unknown model."""
        from aipha.execution.fill_models import get_fill_model

        with pytest.raises(KeyError, match="unknown_model"):
            get_fill_model("unknown_model")

    def test_create_volume_constrained_model(self):
        """create_volume_constrained_model should create wrapped model."""
        from aipha.execution.fill_models import (
            create_volume_constrained_model,
            VolumeConstrainedFillModel,
        )

        model = create_volume_constrained_model(
            base_name="next_bar_open",
            max_participation_rate=0.05,
            lot_size=100,
        )

        assert isinstance(model, VolumeConstrainedFillModel)
        assert model.name == "next_bar_open_volume_constrained"
        assert model._constraint.max_participation_rate == 0.05

