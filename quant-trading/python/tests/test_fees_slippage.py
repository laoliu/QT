from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from aipha.domain.models import Bar, Fill, Side
from aipha.execution.fees import (
    CommissionModel,
    FeeBreakdown,
    FeeModel,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.execution.slippage import (
    FixedBpsSlippageModel,
    LinearImpactSlippageModel,
    SlippageModel,
    SquareRootImpactSlippageModel,
    _estimate_bar_amount,
)


def _ts() -> datetime:
    return datetime(2020, 1, 2, 9, 31, tzinfo=timezone.utc)


def _fill(side: Side = Side.BUY, qty: int = 100, price: float = 10.0) -> Fill:
    return Fill(order_id="o1", symbol="000001.SZ", side=side, qty=qty, price=price, t_fill=_ts(), model="x")


def _bar(volume: float = 100_000, close: float = 10.0, metadata: dict | None = None,
         high: float | None = None, low: float | None = None) -> Bar:
    return Bar(
        symbol="000001.SZ",
        start_ts=_ts(),
        end_ts=_ts(),
        open=close,
        high=high if high is not None else close * 1.05,
        low=low if low is not None else close * 0.95,
        close=close,
        volume=volume,
        metadata=metadata,
    )


def _fee_model() -> FeeModelCNEquity:
    return FeeModelCNEquity(
        commission=CommissionModel(rate=0.0003, min_fee=5.0),
        stamp_tax=StampTaxModel(sell_rate=0.001),
        transfer_fee=TransferFeeModel(rate=0.00001),
    )


# =========================================================================
# FeeBreakdown
# =========================================================================

class TestFeeBreakdown:
    def test_total_sums_all(self) -> None:
        fb = FeeBreakdown(commission=3.0, stamp_tax=2.0, transfer_fee=0.5)
        assert fb.total == pytest.approx(5.5)

    def test_total_zero(self) -> None:
        fb = FeeBreakdown(commission=0.0, stamp_tax=0.0, transfer_fee=0.0)
        assert fb.total == 0.0


# =========================================================================
# CommissionModel
# =========================================================================

class TestCommissionModel:
    def test_normal_rate(self) -> None:
        """成交额足够大时，按比例计费。"""
        m = CommissionModel(rate=0.0003, min_fee=5.0)
        fill = _fill(qty=10000, price=10.0)  # notional=100000, fee=30
        assert m.calc(fill) == pytest.approx(30.0)

    def test_min_fee_triggered(self) -> None:
        """成交额太小时，触发最低佣金。"""
        m = CommissionModel(rate=0.0003, min_fee=5.0)
        fill = _fill(qty=100, price=10.0)  # notional=1000, fee=0.3 < 5
        assert m.calc(fill) == pytest.approx(5.0)

    def test_zero_rate(self) -> None:
        m = CommissionModel(rate=0.0, min_fee=0.0)
        assert m.calc(_fill()) == 0.0

    def test_boundary_exactly_min(self) -> None:
        """成交额恰好使佣金等于最低收费。"""
        # min_fee=5, rate=0.0005 → 需 notional=10000
        m = CommissionModel(rate=0.0005, min_fee=5.0)
        fill = _fill(qty=1000, price=10.0)  # notional=10000
        assert m.calc(fill) == pytest.approx(5.0)


# =========================================================================
# StampTaxModel
# =========================================================================

class TestStampTaxModel:
    def test_sell_charged(self) -> None:
        m = StampTaxModel(sell_rate=0.001)
        fill = _fill(side=Side.SELL, qty=1000, price=10.0)
        assert m.calc(fill) == pytest.approx(10.0)

    def test_buy_exempt(self) -> None:
        m = StampTaxModel(sell_rate=0.001)
        assert m.calc(_fill(side=Side.BUY)) == 0.0

    def test_zero_rate(self) -> None:
        m = StampTaxModel(sell_rate=0.0)
        assert m.calc(_fill(side=Side.SELL)) == 0.0


# =========================================================================
# TransferFeeModel
# =========================================================================

class TestTransferFeeModel:
    def test_buy_charged(self) -> None:
        m = TransferFeeModel(rate=0.00001)
        fill = _fill(qty=10000, price=10.0)  # notional=100000
        assert m.calc(fill) == pytest.approx(1.0)

    def test_sell_charged(self) -> None:
        m = TransferFeeModel(rate=0.00001)
        fill = _fill(side=Side.SELL, qty=10000, price=10.0)
        assert m.calc(fill) == pytest.approx(1.0)


# =========================================================================
# FeeModelCNEquity (integrated)
# =========================================================================

class TestFeeModelCNEquity:
    def test_buy_no_stamp_tax(self) -> None:
        fm = _fee_model()
        fb = fm.calc(_fill(side=Side.BUY, qty=10000, price=10.0))
        assert fb.stamp_tax == 0.0
        assert fb.commission > 0.0
        assert fb.transfer_fee > 0.0
        assert fb.total == pytest.approx(fb.commission + fb.transfer_fee)

    def test_sell_has_stamp_tax(self) -> None:
        fm = _fee_model()
        fb = fm.calc(_fill(side=Side.SELL, qty=10000, price=10.0))
        assert fb.stamp_tax == pytest.approx(100.0)  # 100000 * 0.001
        assert fb.total > fb.commission

    def test_min_commission_buy(self) -> None:
        fm = _fee_model()
        fb = fm.calc(_fill(side=Side.BUY, qty=100, price=10.0))
        assert fb.commission == pytest.approx(5.0)

    def test_protocol_compliance(self) -> None:
        fm = _fee_model()
        assert isinstance(fm, FeeModel)

    def test_multi_trade_accumulation(self) -> None:
        """多笔交易费用可累积。"""
        fm = _fee_model()
        fills = [_fill(qty=1000, price=10.0 + i) for i in range(5)]
        total = sum(fm.calc(f).total for f in fills)
        assert total > 0.0

    def test_large_notional(self) -> None:
        """大额交易的费用比例正确。"""
        fm = _fee_model()
        fill = _fill(qty=100000, price=50.0)  # notional = 5,000,000
        fb = fm.calc(fill)
        assert fb.commission == pytest.approx(5_000_000 * 0.0003)
        assert fb.transfer_fee == pytest.approx(5_000_000 * 0.00001)


# =========================================================================
# FixedBpsSlippageModel
# =========================================================================

class TestFixedBpsSlippage:
    def test_buy_price_increases(self) -> None:
        slip = FixedBpsSlippageModel(bps=2)
        result = slip.apply(_fill(side=Side.BUY, price=10.0))
        assert result.price > 10.0
        assert result.price == pytest.approx(10.0 * (1 + 2 / 10000))

    def test_sell_price_decreases(self) -> None:
        slip = FixedBpsSlippageModel(bps=2)
        result = slip.apply(_fill(side=Side.SELL, price=10.0))
        assert result.price < 10.0
        assert result.price == pytest.approx(10.0 * (1 - 2 / 10000))

    def test_zero_bps(self) -> None:
        slip = FixedBpsSlippageModel(bps=0)
        result = slip.apply(_fill(price=10.0))
        assert result.price == pytest.approx(10.0)

    def test_preserves_other_fields(self) -> None:
        slip = FixedBpsSlippageModel(bps=5)
        original = _fill()
        result = slip.apply(original)
        assert result.order_id == original.order_id
        assert result.symbol == original.symbol
        assert result.qty == original.qty
        assert result.side == original.side

    def test_protocol_compliance(self) -> None:
        assert isinstance(FixedBpsSlippageModel(bps=1), SlippageModel)

    def test_bar_argument_ignored(self) -> None:
        slip = FixedBpsSlippageModel(bps=3)
        r1 = slip.apply(_fill(price=10.0))
        r2 = slip.apply(_fill(price=10.0), bar=_bar())
        assert r1.price == pytest.approx(r2.price)


# =========================================================================
# LinearImpactSlippageModel
# =========================================================================

class TestLinearImpactSlippage:
    def test_with_bar_larger_than_fixed(self) -> None:
        slip = LinearImpactSlippageModel(base_bps=3.0, impact_coeff=50.0)
        bar = _bar(volume=100_000, close=10.0, metadata={"amount": 1_000_000})
        fill = _fill(qty=1000, price=10.0)  # notional=10000, participation=0.01
        result = slip.apply(fill, bar=bar)
        expected_bps = 3.0 + 50.0 * (10000 / 1_000_000)
        assert result.price == pytest.approx(10.0 * (1 + expected_bps / 10000))

    def test_fallback_when_bar_none(self) -> None:
        slip = LinearImpactSlippageModel(fallback_bps=10.0)
        result = slip.apply(_fill(price=10.0), bar=None)
        assert result.price == pytest.approx(10.0 * (1 + 10.0 / 10000))

    def test_fallback_when_volume_zero(self) -> None:
        slip = LinearImpactSlippageModel(fallback_bps=10.0)
        bar = _bar(volume=0)
        result = slip.apply(_fill(price=10.0), bar=bar)
        assert result.price == pytest.approx(10.0 * (1 + 10.0 / 10000))

    def test_max_bps_cap(self) -> None:
        slip = LinearImpactSlippageModel(base_bps=0, impact_coeff=10000, max_bps=50.0)
        bar = _bar(volume=100_000, close=10.0, metadata={"amount": 100})
        fill = _fill(qty=10000, price=10.0)  # huge participation
        result = slip.apply(fill, bar=bar)
        assert result.price == pytest.approx(10.0 * (1 + 50.0 / 10000))

    def test_sell_direction(self) -> None:
        slip = LinearImpactSlippageModel(base_bps=5.0, impact_coeff=0.0)
        result = slip.apply(_fill(side=Side.SELL, price=10.0), bar=_bar())
        assert result.price < 10.0

    def test_protocol_compliance(self) -> None:
        assert isinstance(LinearImpactSlippageModel(), SlippageModel)


# =========================================================================
# SquareRootImpactSlippageModel
# =========================================================================

class TestSquareRootImpactSlippage:
    def test_with_bar(self) -> None:
        slip = SquareRootImpactSlippageModel(
            impact_coeff=0.5, sigma_daily=0.02, use_bar_volatility=False,
        )
        bar = _bar(volume=100_000, close=10.0)
        fill = _fill(qty=1000, price=10.0)  # participation = 0.01
        result = slip.apply(fill, bar=bar)
        expected_bps = 0.02 * 0.5 * math.sqrt(1000 / 100_000) * 10000
        assert result.price == pytest.approx(10.0 * (1 + expected_bps / 10000))

    def test_fallback_no_bar(self) -> None:
        slip = SquareRootImpactSlippageModel(fallback_bps=15.0)
        result = slip.apply(_fill(price=10.0), bar=None)
        assert result.price == pytest.approx(10.0 * (1 + 15.0 / 10000))

    def test_bar_volatility_override(self) -> None:
        """当 bar 的 intraday range 大于 sigma_daily 时使用更大值。"""
        slip = SquareRootImpactSlippageModel(
            impact_coeff=0.5, sigma_daily=0.01, use_bar_volatility=True,
        )
        # intraday_range = (12-8)/10 = 0.4 > 0.01
        bar = _bar(volume=100_000, close=10.0, high=12.0, low=8.0)
        fill = _fill(qty=1000, price=10.0)
        result = slip.apply(fill, bar=bar)
        # sigma = max(0.01, 0.4) = 0.4
        expected_bps = 0.4 * 0.5 * math.sqrt(1000 / 100_000) * 10000
        assert result.price == pytest.approx(10.0 * (1 + expected_bps / 10000))

    def test_max_bps_cap(self) -> None:
        slip = SquareRootImpactSlippageModel(
            impact_coeff=100.0, sigma_daily=1.0, max_bps=50.0, use_bar_volatility=False,
        )
        bar = _bar(volume=100, close=10.0)
        fill = _fill(qty=100, price=10.0)  # participation = 1.0
        result = slip.apply(fill, bar=bar)
        assert result.price == pytest.approx(10.0 * (1 + 50.0 / 10000))

    def test_sell_direction(self) -> None:
        slip = SquareRootImpactSlippageModel(fallback_bps=10.0)
        result = slip.apply(_fill(side=Side.SELL, price=10.0))
        assert result.price < 10.0

    def test_protocol_compliance(self) -> None:
        assert isinstance(SquareRootImpactSlippageModel(), SlippageModel)


# =========================================================================
# _estimate_bar_amount helper
# =========================================================================

class TestEstimateBarAmount:
    def test_metadata_amount(self) -> None:
        bar = _bar(volume=100, close=10.0, metadata={"amount": 5000.0})
        assert _estimate_bar_amount(bar) == pytest.approx(5000.0)

    def test_fallback_volume_times_close(self) -> None:
        bar = _bar(volume=1000, close=10.0)
        assert _estimate_bar_amount(bar) == pytest.approx(1000 * 10.0)

    def test_zero_volume_zero_close(self) -> None:
        bar = _bar(volume=0, close=0.0)
        assert _estimate_bar_amount(bar) == 0.0


# =========================================================================
# Original integration tests (preserved)
# =========================================================================

def test_fixed_bps_slippage_buy_sell() -> None:
    slip = FixedBpsSlippageModel(bps=2)

    buy = Fill(order_id="o1", symbol="000001.SZ", side=Side.BUY, qty=100, price=10.0, t_fill=_ts(), model="x")
    sell = Fill(order_id="o2", symbol="000001.SZ", side=Side.SELL, qty=100, price=10.0, t_fill=_ts(), model="x")

    buy2 = slip.apply(buy)
    sell2 = slip.apply(sell)

    assert buy2.price > buy.price
    assert sell2.price < sell.price


def test_fee_model_breakdown() -> None:
    fee_model = FeeModelCNEquity(
        commission=CommissionModel(rate=0.0003, min_fee=5.0),
        stamp_tax=StampTaxModel(sell_rate=0.001),
        transfer_fee=TransferFeeModel(rate=0.00001),
    )

    fill_buy = Fill(
        order_id="o1",
        symbol="000001.SZ",
        side=Side.BUY,
        qty=100,
        price=10.0,
        t_fill=_ts(),
        model="x",
    )
    fill_sell = Fill(
        order_id="o2",
        symbol="000001.SZ",
        side=Side.SELL,
        qty=100,
        price=10.0,
        t_fill=_ts(),
        model="x",
    )

    fees_buy = fee_model.calc(fill_buy)
    fees_sell = fee_model.calc(fill_sell)

    assert fees_buy.commission >= 5.0
    assert fees_buy.stamp_tax == 0.0
    assert fees_sell.stamp_tax > 0.0
    assert fees_buy.total > 0.0


# ============================================================================
# A 股滑点参考常量测试
# ============================================================================


def test_slippage_constants_defined():
    """A 股滑点参考常量存在且合理。"""
    from aipha.execution.slippage import (
        SLIPPAGE_BPS_LARGECAP,
        SLIPPAGE_BPS_MIDCAP,
        SLIPPAGE_BPS_SMALLCAP,
        SLIPPAGE_BPS_MICROCAP,
        SLIPPAGE_BPS_MIN_REALISTIC,
    )
    assert SLIPPAGE_BPS_LARGECAP == 10.0
    assert SLIPPAGE_BPS_MIDCAP == 15.0
    assert SLIPPAGE_BPS_SMALLCAP == 25.0
    assert SLIPPAGE_BPS_MICROCAP == 40.0
    assert SLIPPAGE_BPS_MIN_REALISTIC == 5.0
    # 层级递增
    assert SLIPPAGE_BPS_LARGECAP < SLIPPAGE_BPS_MIDCAP < SLIPPAGE_BPS_SMALLCAP < SLIPPAGE_BPS_MICROCAP


def test_low_slippage_warning(caplog):
    """滑点低于合理值时应发出警告。"""
    import logging
    from aipha.backtest.engine_builder import _warn_low_slippage
    with caplog.at_level(logging.WARNING):
        _warn_low_slippage(3.0)
    assert "滑点" in caplog.text
    assert "低于" in caplog.text


def test_normal_slippage_no_warning(caplog):
    """合理滑点不应发出警告。"""
    import logging
    from aipha.backtest.engine_builder import _warn_low_slippage
    with caplog.at_level(logging.WARNING):
        _warn_low_slippage(15.0)
    assert "滑点" not in caplog.text
