"""Tests for backtest runner.

Coverage targets:
- simulate_single_order_next_bar: complete order → fill → slippage → fee pipeline
- Rejection handling
- No-fill handling (limit not touched)

Quality gates: 100% coverage.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from aipha.backtest.runner import BacktestOutcome, simulate_single_order_next_bar
from aipha.domain.models import Bar, Order, OrderType, Side
from aipha.execution.fees import (
    CommissionModel,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.execution.fill_models import (
    BarLimitTouchFillModel,
    NextBarCloseFillModel,
    NextBarOpenFillModel,
)
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.market.rules_cn_equity import MarketRulesCNEquity, RejectReason


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def rules() -> MarketRulesCNEquity:
    """Standard A-share rules."""
    return MarketRulesCNEquity(lot_size=100)


@pytest.fixture
def slippage() -> FixedBpsSlippageModel:
    """Standard 10 bps slippage."""
    return FixedBpsSlippageModel(bps=10.0)


@pytest.fixture
def fee_model() -> FeeModelCNEquity:
    """Standard A-share fee model."""
    return FeeModelCNEquity(
        commission=CommissionModel(rate=0.0003, min_fee=5.0),
        stamp_tax=StampTaxModel(sell_rate=0.001),
        transfer_fee=TransferFeeModel(rate=0.00002),
    )


@pytest.fixture
def normal_bar() -> Bar:
    """Normal trading bar."""
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


def make_order(
    side: Side = Side.BUY,
    qty: int = 1000,
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
# BacktestOutcome Tests
# ============================================================================


class TestBacktestOutcome:
    """Tests for BacktestOutcome dataclass."""

    def test_outcome_with_fill(self):
        """Outcome with successful fill should have all components."""
        from aipha.domain.models import Fill
        from aipha.execution.fees import FeeBreakdown

        fill = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            price=10.01,
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="next_bar_open",
        )
        fees = FeeBreakdown(commission=5.0, stamp_tax=0.0, transfer_fee=0.2)

        outcome = BacktestOutcome(
            fill=fill, fees=fees, reject_reason=None, no_fill_reason=None
        )

        assert outcome.fill == fill
        assert outcome.fees == fees
        assert outcome.reject_reason is None
        assert outcome.no_fill_reason is None

    def test_outcome_with_rejection(self):
        """Outcome with rejection should have no fill."""
        outcome = BacktestOutcome(
            fill=None, fees=None, reject_reason="SUSPENDED", no_fill_reason=None
        )

        assert outcome.fill is None
        assert outcome.fees is None
        assert outcome.reject_reason == "SUSPENDED"

    def test_outcome_with_no_fill(self):
        """Outcome with no-fill reason should have no fill."""
        outcome = BacktestOutcome(
            fill=None, fees=None, reject_reason=None, no_fill_reason="LIMIT_NOT_TOUCHED"
        )

        assert outcome.fill is None
        assert outcome.no_fill_reason == "LIMIT_NOT_TOUCHED"


# ============================================================================
# simulate_single_order_next_bar Tests
# ============================================================================


class TestSimulateSingleOrderNextBar:
    """Tests for simulate_single_order_next_bar function."""

    def test_buy_market_order_fills(
        self,
        rules: MarketRulesCNEquity,
        slippage: FixedBpsSlippageModel,
        fee_model: FeeModelCNEquity,
        normal_bar: Bar,
    ):
        """BUY market order should fill with slippage and fees."""
        order = make_order(side=Side.BUY, qty=1000)
        fill_model = NextBarOpenFillModel()

        outcome = simulate_single_order_next_bar(
            order=order,
            next_bar=normal_bar,
            fill_model=fill_model,
            slippage=slippage,
            fee_model=fee_model,
            rules=rules,
        )

        # Fill at open 10.00 with 10 bps slippage → 10.01
        assert outcome.fill is not None
        assert outcome.fill.price == pytest.approx(10.01, rel=1e-9)
        assert outcome.fill.qty == 1000
        assert outcome.fill.side == Side.BUY

        # Fees: commission=5.0 (min), stamp_tax=0.0, transfer=10010*0.00002=0.2002
        assert outcome.fees is not None
        assert outcome.fees.commission == 5.0
        assert outcome.fees.stamp_tax == 0.0
        assert outcome.fees.transfer_fee == pytest.approx(0.2002, rel=1e-3)

        assert outcome.reject_reason is None
        assert outcome.no_fill_reason is None

    def test_sell_market_order_fills(
        self,
        rules: MarketRulesCNEquity,
        slippage: FixedBpsSlippageModel,
        fee_model: FeeModelCNEquity,
        normal_bar: Bar,
    ):
        """SELL market order should fill with slippage and fees including stamp tax."""
        order = make_order(side=Side.SELL, qty=1000)
        fill_model = NextBarOpenFillModel()

        outcome = simulate_single_order_next_bar(
            order=order,
            next_bar=normal_bar,
            fill_model=fill_model,
            slippage=slippage,
            fee_model=fee_model,
            rules=rules,
        )

        # Fill at open 10.00 with 10 bps slippage (sell decreases) → 9.99
        assert outcome.fill is not None
        assert outcome.fill.price == pytest.approx(9.99, rel=1e-9)

        # Fees: commission=5.0, stamp_tax=9990*0.001=9.99, transfer=9990*0.00002=0.1998
        assert outcome.fees is not None
        assert outcome.fees.commission == 5.0
        assert outcome.fees.stamp_tax == pytest.approx(9.99, rel=1e-3)
        assert outcome.fees.transfer_fee == pytest.approx(0.1998, rel=1e-3)

    def test_close_fill_model(
        self,
        rules: MarketRulesCNEquity,
        slippage: FixedBpsSlippageModel,
        fee_model: FeeModelCNEquity,
        normal_bar: Bar,
    ):
        """NextBarCloseFillModel should fill at close price."""
        order = make_order(side=Side.BUY, qty=1000)
        fill_model = NextBarCloseFillModel()

        outcome = simulate_single_order_next_bar(
            order=order,
            next_bar=normal_bar,
            fill_model=fill_model,
            slippage=slippage,
            fee_model=fee_model,
            rules=rules,
        )

        # Fill at close 10.02 with 10 bps slippage → 10.02 * 1.001 = 10.03002
        assert outcome.fill is not None
        assert outcome.fill.price == pytest.approx(10.02 * 1.001, rel=1e-9)

    def test_suspended_bar_rejects(
        self,
        rules: MarketRulesCNEquity,
        slippage: FixedBpsSlippageModel,
        fee_model: FeeModelCNEquity,
        suspended_bar: Bar,
    ):
        """Order on suspended bar should be rejected."""
        order = make_order(side=Side.BUY, qty=1000)
        fill_model = NextBarOpenFillModel()

        outcome = simulate_single_order_next_bar(
            order=order,
            next_bar=suspended_bar,
            fill_model=fill_model,
            slippage=slippage,
            fee_model=fee_model,
            rules=rules,
        )

        assert outcome.fill is None
        assert outcome.fees is None
        assert outcome.reject_reason == str(RejectReason.SUSPENDED)

    def test_lot_size_violation_rejects(
        self,
        rules: MarketRulesCNEquity,
        slippage: FixedBpsSlippageModel,
        fee_model: FeeModelCNEquity,
        normal_bar: Bar,
    ):
        """Order with invalid lot size should be rejected."""
        order = make_order(side=Side.BUY, qty=150)  # Not divisible by 100
        fill_model = NextBarOpenFillModel()

        outcome = simulate_single_order_next_bar(
            order=order,
            next_bar=normal_bar,
            fill_model=fill_model,
            slippage=slippage,
            fee_model=fee_model,
            rules=rules,
        )

        assert outcome.fill is None
        assert outcome.reject_reason == str(RejectReason.LOT_SIZE_ROUNDING)

    def test_limit_order_touched_fills(
        self,
        rules: MarketRulesCNEquity,
        slippage: FixedBpsSlippageModel,
        fee_model: FeeModelCNEquity,
        normal_bar: Bar,
    ):
        """Limit order that touches should fill."""
        order = make_order(
            side=Side.BUY, qty=1000, order_type=OrderType.LIMIT, limit_price=9.98
        )
        fill_model = BarLimitTouchFillModel()

        outcome = simulate_single_order_next_bar(
            order=order,
            next_bar=normal_bar,
            fill_model=fill_model,
            slippage=slippage,
            fee_model=fee_model,
            rules=rules,
        )

        # Fill at limit 9.98 with 10 bps slippage → 9.98 * 1.001 = 9.98998
        assert outcome.fill is not None
        assert outcome.fill.price == pytest.approx(9.98 * 1.001, rel=1e-9)

    def test_limit_order_not_touched_no_fill(
        self,
        rules: MarketRulesCNEquity,
        slippage: FixedBpsSlippageModel,
        fee_model: FeeModelCNEquity,
        normal_bar: Bar,
    ):
        """Limit order that doesn't touch should not fill."""
        order = make_order(
            side=Side.BUY, qty=1000, order_type=OrderType.LIMIT, limit_price=9.90
        )
        fill_model = BarLimitTouchFillModel()

        outcome = simulate_single_order_next_bar(
            order=order,
            next_bar=normal_bar,
            fill_model=fill_model,
            slippage=slippage,
            fee_model=fee_model,
            rules=rules,
        )

        assert outcome.fill is None
        assert outcome.fees is None
        assert outcome.reject_reason is None
        assert outcome.no_fill_reason == "LIMIT_NOT_TOUCHED"

    def test_zero_slippage(
        self,
        rules: MarketRulesCNEquity,
        fee_model: FeeModelCNEquity,
        normal_bar: Bar,
    ):
        """Zero slippage should not change fill price."""
        order = make_order(side=Side.BUY, qty=1000)
        fill_model = NextBarOpenFillModel()
        zero_slippage = FixedBpsSlippageModel(bps=0.0)

        outcome = simulate_single_order_next_bar(
            order=order,
            next_bar=normal_bar,
            fill_model=fill_model,
            slippage=zero_slippage,
            fee_model=fee_model,
            rules=rules,
        )

        assert outcome.fill is not None
        assert outcome.fill.price == 10.00  # Exact open price

    def test_large_order_fees(
        self,
        rules: MarketRulesCNEquity,
        slippage: FixedBpsSlippageModel,
        fee_model: FeeModelCNEquity,
        normal_bar: Bar,
    ):
        """Large order should have commission above min_fee."""
        order = make_order(side=Side.SELL, qty=10000)  # qty=10000
        fill_model = NextBarOpenFillModel()

        outcome = simulate_single_order_next_bar(
            order=order,
            next_bar=normal_bar,
            fill_model=fill_model,
            slippage=slippage,
            fee_model=fee_model,
            rules=rules,
        )

        # Fill at open 10.00 with sell slippage → 9.99
        # Notional = 10000 * 9.99 = 99900
        assert outcome.fill is not None

        # Commission = 99900 * 0.0003 = 29.97 > min_fee 5.0
        assert outcome.fees is not None
        assert outcome.fees.commission == pytest.approx(29.97, rel=1e-3)

        # Stamp tax = 99900 * 0.001 = 99.9
        assert outcome.fees.stamp_tax == pytest.approx(99.9, rel=1e-3)


class TestEndToEndScenarios:
    """End-to-end integration scenarios."""

    def test_complete_round_trip(
        self,
        rules: MarketRulesCNEquity,
        slippage: FixedBpsSlippageModel,
        fee_model: FeeModelCNEquity,
        normal_bar: Bar,
    ):
        """Complete buy and sell round trip should calculate correct P&L components."""
        fill_model = NextBarOpenFillModel()

        # BUY 1000 shares
        buy_order = make_order(side=Side.BUY, qty=1000)
        buy_outcome = simulate_single_order_next_bar(
            order=buy_order,
            next_bar=normal_bar,
            fill_model=fill_model,
            slippage=slippage,
            fee_model=fee_model,
            rules=rules,
        )

        # SELL 1000 shares
        sell_order = make_order(side=Side.SELL, qty=1000)
        sell_outcome = simulate_single_order_next_bar(
            order=sell_order,
            next_bar=normal_bar,
            fill_model=fill_model,
            slippage=slippage,
            fee_model=fee_model,
            rules=rules,
        )

        # Validate both fills
        assert buy_outcome.fill is not None
        assert sell_outcome.fill is not None

        # Calculate total cost
        buy_cost = buy_outcome.fill.notional + buy_outcome.fees.total
        sell_proceeds = sell_outcome.fill.notional - sell_outcome.fees.total

        # Buy: 1000 * 10.01 + fees ≈ 10010 + 5.2
        # Sell: 1000 * 9.99 - fees ≈ 9990 - 15.2
        # Net P&L ≈ sell_proceeds - buy_cost < 0 (due to slippage and fees)
        net_pnl = sell_proceeds - buy_cost
        assert net_pnl < 0  # Should be negative due to transaction costs
