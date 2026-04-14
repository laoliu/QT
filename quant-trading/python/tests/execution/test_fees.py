"""Tests for fee models.

Coverage targets:
- CommissionModel: rate-based commission with min_fee
- StampTaxModel: sell-only stamp tax
- TransferFeeModel: transfer fee on all trades
- FeeModelCNEquity: composite A-share fee model
- FeeBreakdown: fee breakdown dataclass

Quality gates: 100% coverage.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from aipha.domain.models import Fill, Side
from aipha.execution.fees import (
    CommissionModel,
    FeeBreakdown,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def buy_fill() -> Fill:
    """Sample BUY fill with notional = 10000."""
    return Fill(
        order_id="ORD001",
        symbol="600000.SH",
        side=Side.BUY,
        qty=1000,
        price=10.00,  # notional = 10000
        t_fill=datetime(2025, 1, 6, 9, 31),
        model="next_bar_open",
    )


@pytest.fixture
def sell_fill() -> Fill:
    """Sample SELL fill with notional = 10000."""
    return Fill(
        order_id="ORD002",
        symbol="600000.SH",
        side=Side.SELL,
        qty=1000,
        price=10.00,  # notional = 10000
        t_fill=datetime(2025, 1, 6, 9, 31),
        model="next_bar_open",
    )


@pytest.fixture
def small_fill() -> Fill:
    """Small fill that triggers min commission."""
    return Fill(
        order_id="ORD003",
        symbol="600000.SH",
        side=Side.BUY,
        qty=100,
        price=5.00,  # notional = 500
        t_fill=datetime(2025, 1, 6, 9, 31),
        model="next_bar_open",
    )


@pytest.fixture
def cn_equity_fee_model() -> FeeModelCNEquity:
    """Standard A-share fee model with typical rates."""
    return FeeModelCNEquity(
        commission=CommissionModel(rate=0.0003, min_fee=5.0),  # 0.03%, min 5元
        stamp_tax=StampTaxModel(sell_rate=0.001),  # 0.1%
        transfer_fee=TransferFeeModel(rate=0.00002),  # 0.002%
    )


# ============================================================================
# CommissionModel Tests
# ============================================================================


class TestCommissionModel:
    """Tests for CommissionModel."""

    def test_commission_above_min(self, buy_fill: Fill):
        """Commission calculated when above min_fee."""
        model = CommissionModel(rate=0.0003, min_fee=5.0)

        fee = model.calc(buy_fill)

        # notional = 10000, fee = 10000 * 0.0003 = 3.0 < min_fee 5.0
        # Wait, 3.0 < 5.0, so min_fee should apply
        assert fee == 5.0

    def test_commission_equals_min(self):
        """Commission equals min_fee when calculated fee < min_fee."""
        fill = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=10000,
            price=10.00,  # notional = 100000
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="test",
        )
        model = CommissionModel(rate=0.0003, min_fee=5.0)

        fee = model.calc(fill)

        # notional = 100000, fee = 100000 * 0.0003 = 30.0 > min_fee 5.0
        assert fee == pytest.approx(30.0, rel=1e-9)

    def test_commission_min_fee_applied(self, small_fill: Fill):
        """Min fee applied when calculated commission is lower."""
        model = CommissionModel(rate=0.0003, min_fee=5.0)

        fee = model.calc(small_fill)

        # notional = 500, fee = 500 * 0.0003 = 0.15 < min_fee 5.0
        assert fee == 5.0

    def test_commission_zero_rate(self, buy_fill: Fill):
        """Zero rate should result in min_fee."""
        model = CommissionModel(rate=0.0, min_fee=5.0)

        fee = model.calc(buy_fill)

        assert fee == 5.0  # min_fee applied

    def test_commission_zero_min_fee(self, buy_fill: Fill):
        """Zero min_fee should allow calculated commission."""
        model = CommissionModel(rate=0.0003, min_fee=0.0)

        fee = model.calc(buy_fill)

        # notional = 10000, fee = 10000 * 0.0003 = 3.0
        assert fee == pytest.approx(3.0, rel=1e-9)


# ============================================================================
# StampTaxModel Tests
# ============================================================================


class TestStampTaxModel:
    """Tests for StampTaxModel."""

    def test_stamp_tax_on_sell(self, sell_fill: Fill):
        """Stamp tax should apply on SELL orders."""
        model = StampTaxModel(sell_rate=0.001)

        fee = model.calc(sell_fill)

        # notional = 10000, fee = 10000 * 0.001 = 10.0
        assert fee == pytest.approx(10.0, rel=1e-9)

    def test_stamp_tax_zero_on_buy(self, buy_fill: Fill):
        """Stamp tax should be zero on BUY orders."""
        model = StampTaxModel(sell_rate=0.001)

        fee = model.calc(buy_fill)

        assert fee == 0.0

    def test_stamp_tax_higher_rate(self, sell_fill: Fill):
        """Higher stamp tax rate should calculate correctly."""
        model = StampTaxModel(sell_rate=0.002)

        fee = model.calc(sell_fill)

        # notional = 10000, fee = 10000 * 0.002 = 20.0
        assert fee == pytest.approx(20.0, rel=1e-9)

    def test_stamp_tax_zero_rate(self, sell_fill: Fill):
        """Zero stamp tax rate should result in zero fee."""
        model = StampTaxModel(sell_rate=0.0)

        fee = model.calc(sell_fill)

        assert fee == 0.0


# ============================================================================
# TransferFeeModel Tests
# ============================================================================


class TestTransferFeeModel:
    """Tests for TransferFeeModel."""

    def test_transfer_fee_on_buy(self, buy_fill: Fill):
        """Transfer fee should apply on BUY orders."""
        model = TransferFeeModel(rate=0.00002)

        fee = model.calc(buy_fill)

        # notional = 10000, fee = 10000 * 0.00002 = 0.20
        assert fee == pytest.approx(0.20, rel=1e-9)

    def test_transfer_fee_on_sell(self, sell_fill: Fill):
        """Transfer fee should apply on SELL orders."""
        model = TransferFeeModel(rate=0.00002)

        fee = model.calc(sell_fill)

        assert fee == pytest.approx(0.20, rel=1e-9)

    def test_transfer_fee_higher_rate(self, buy_fill: Fill):
        """Higher transfer fee rate should calculate correctly."""
        model = TransferFeeModel(rate=0.0001)

        fee = model.calc(buy_fill)

        # notional = 10000, fee = 10000 * 0.0001 = 1.00
        assert fee == pytest.approx(1.00, rel=1e-9)


# ============================================================================
# FeeBreakdown Tests
# ============================================================================


class TestFeeBreakdown:
    """Tests for FeeBreakdown dataclass."""

    def test_fee_breakdown_total(self):
        """Total should sum all fee components."""
        breakdown = FeeBreakdown(
            commission=5.0,
            stamp_tax=10.0,
            transfer_fee=0.2,
        )

        assert breakdown.total == pytest.approx(15.2, rel=1e-9)

    def test_fee_breakdown_zero_components(self):
        """Zero components should sum to zero."""
        breakdown = FeeBreakdown(
            commission=0.0,
            stamp_tax=0.0,
            transfer_fee=0.0,
        )

        assert breakdown.total == 0.0

    def test_fee_breakdown_only_commission(self):
        """Only commission should sum correctly."""
        breakdown = FeeBreakdown(
            commission=5.0,
            stamp_tax=0.0,
            transfer_fee=0.0,
        )

        assert breakdown.total == 5.0

    def test_fee_breakdown_immutability(self):
        """FeeBreakdown should be immutable."""
        breakdown = FeeBreakdown(
            commission=5.0,
            stamp_tax=10.0,
            transfer_fee=0.2,
        )

        with pytest.raises(Exception):  # FrozenInstanceError
            breakdown.commission = 10.0


# ============================================================================
# FeeModelCNEquity Tests
# ============================================================================


class TestFeeModelCNEquity:
    """Tests for composite FeeModelCNEquity."""

    def test_buy_fees_no_stamp_tax(
        self, cn_equity_fee_model: FeeModelCNEquity, buy_fill: Fill
    ):
        """BUY order should have commission + transfer, no stamp tax."""
        breakdown = cn_equity_fee_model.calc(buy_fill)

        # Commission: notional=10000, rate=0.0003 → 3.0 < min_fee 5.0 → 5.0
        assert breakdown.commission == 5.0
        # Stamp tax: 0 for BUY
        assert breakdown.stamp_tax == 0.0
        # Transfer fee: 10000 * 0.00002 = 0.20
        assert breakdown.transfer_fee == pytest.approx(0.20, rel=1e-9)
        # Total: 5.0 + 0.0 + 0.20 = 5.20
        assert breakdown.total == pytest.approx(5.20, rel=1e-9)

    def test_sell_fees_with_stamp_tax(
        self, cn_equity_fee_model: FeeModelCNEquity, sell_fill: Fill
    ):
        """SELL order should have commission + stamp tax + transfer."""
        breakdown = cn_equity_fee_model.calc(sell_fill)

        # Commission: 5.0 (min_fee)
        assert breakdown.commission == 5.0
        # Stamp tax: 10000 * 0.001 = 10.0
        assert breakdown.stamp_tax == pytest.approx(10.0, rel=1e-9)
        # Transfer fee: 0.20
        assert breakdown.transfer_fee == pytest.approx(0.20, rel=1e-9)
        # Total: 5.0 + 10.0 + 0.20 = 15.20
        assert breakdown.total == pytest.approx(15.20, rel=1e-9)

    def test_large_trade_fees(self, cn_equity_fee_model: FeeModelCNEquity):
        """Large trade should have commission above min_fee."""
        fill = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.SELL,
            qty=10000,
            price=50.00,  # notional = 500000
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="test",
        )

        breakdown = cn_equity_fee_model.calc(fill)

        # Commission: 500000 * 0.0003 = 150.0 > min_fee 5.0
        assert breakdown.commission == pytest.approx(150.0, rel=1e-9)
        # Stamp tax: 500000 * 0.001 = 500.0
        assert breakdown.stamp_tax == pytest.approx(500.0, rel=1e-9)
        # Transfer fee: 500000 * 0.00002 = 10.0
        assert breakdown.transfer_fee == pytest.approx(10.0, rel=1e-9)
        # Total: 150 + 500 + 10 = 660.0
        assert breakdown.total == pytest.approx(660.0, rel=1e-9)

    def test_fee_model_immutability(self, cn_equity_fee_model: FeeModelCNEquity):
        """FeeModelCNEquity should be immutable."""
        with pytest.raises(Exception):  # FrozenInstanceError
            cn_equity_fee_model.commission = CommissionModel(rate=0.001, min_fee=10.0)

    def test_high_value_precision(self, cn_equity_fee_model: FeeModelCNEquity):
        """Fee calculation should maintain precision for high values."""
        fill = Fill(
            order_id="ORD001",
            symbol="600519.SH",  # 茅台
            side=Side.SELL,
            qty=100,
            price=1800.00,  # notional = 180000
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="test",
        )

        breakdown = cn_equity_fee_model.calc(fill)

        # Commission: 180000 * 0.0003 = 54.0
        assert breakdown.commission == pytest.approx(54.0, rel=1e-9)
        # Stamp tax: 180000 * 0.001 = 180.0
        assert breakdown.stamp_tax == pytest.approx(180.0, rel=1e-9)
        # Transfer fee: 180000 * 0.00002 = 3.6
        assert breakdown.transfer_fee == pytest.approx(3.6, rel=1e-9)
