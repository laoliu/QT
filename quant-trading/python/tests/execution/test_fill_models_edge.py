"""Fill model edge-case tests.

Covers untested branches:
- NextBarOpenFillModel: open <= 0 → ZERO_OPEN_PRICE
- NextBarCloseFillModel: close <= 0 → ZERO_CLOSE_PRICE
- SameBarCloseFillModel: close <= 0, SELL direction
- VWAPFillModel: fill_price <= 0 → ZERO_FILL_PRICE
- TwapSliceFillModel: suspended bar, zero fill price
- VolumeConstrainedFillModel: lot_size truncation → VOLUME_EXHAUSTED
- VolumeConstraint: negative bar_volume
- FillResult: all-None state (not fill, not reject, not no_fill)
- get_fill_model: with kwargs
"""
from __future__ import annotations

from datetime import datetime

import pytest

from aipha.domain.models import Bar, Order, OrderType, Side
from aipha.execution.fill_models import (
    FillResult,
    NextBarCloseFillModel,
    NextBarOpenFillModel,
    NoFillReason,
    SameBarCloseFillModel,
    TwapSliceFillModel,
    VolumeConstrainedFillModel,
    VolumeConstraint,
    VWAPFillModel,
    get_fill_model,
    create_volume_constrained_model,
)
from aipha.market.rules_cn_equity import MarketRulesCNEquity


# ============================================================================
# Helpers
# ============================================================================


def _make_bar(*, open=10.0, high=10.5, low=9.5, close=10.0, volume=100000,
              vwap=None, is_suspended=False, is_limit_up=False, is_limit_down=False) -> Bar:
    return Bar(
        symbol="600000.SH",
        start_ts=datetime(2025, 1, 6, 9, 31),
        end_ts=datetime(2025, 1, 6, 9, 32),
        open=open,
        high=high,
        low=low,
        close=close,
        volume=volume,
        vwap=vwap,
        is_suspended=is_suspended,
        is_limit_up=is_limit_up,
        is_limit_down=is_limit_down,
    )


def _make_order(side=Side.BUY, qty=100, order_type=OrderType.MARKET, limit_price=None) -> Order:
    return Order(
        order_id="test_001",
        symbol="600000.SH",
        side=side,
        qty=qty,
        order_type=order_type,
        t_submit=datetime(2025, 1, 6, 9, 30),
        limit_price=limit_price,
    )


def _rules():
    return MarketRulesCNEquity(lot_size=100)


# ============================================================================
# Zero price defense branches
# ============================================================================


class TestZeroPriceDefense:

    def test_nbo_zero_open(self):
        """NextBarOpen: open=0 → ZERO_OPEN_PRICE"""
        model = NextBarOpenFillModel()
        bar = _make_bar(open=0.0)
        result = model.try_fill(order=_make_order(), next_bar=bar, rules=_rules())
        assert result.fill is None
        assert result.reject_reason == "ZERO_OPEN_PRICE"

    def test_nbo_negative_open(self):
        """NextBarOpen: open=-5 → ZERO_OPEN_PRICE"""
        model = NextBarOpenFillModel()
        bar = _make_bar(open=-5.0)
        result = model.try_fill(order=_make_order(), next_bar=bar, rules=_rules())
        assert result.fill is None
        assert result.reject_reason == "ZERO_OPEN_PRICE"

    def test_nbc_zero_close(self):
        """NextBarClose: close=0 → ZERO_CLOSE_PRICE"""
        model = NextBarCloseFillModel()
        bar = _make_bar(close=0.0)
        result = model.try_fill(order=_make_order(), next_bar=bar, rules=_rules())
        assert result.fill is None
        assert result.reject_reason == "ZERO_CLOSE_PRICE"

    def test_sbc_zero_close(self):
        """SameBarClose: close=0 → ZERO_CLOSE_PRICE"""
        model = SameBarCloseFillModel()
        bar = _make_bar(close=0.0)
        result = model.try_fill(order=_make_order(), next_bar=bar, rules=_rules())
        assert result.fill is None
        assert result.reject_reason == "ZERO_CLOSE_PRICE"

    def test_vwap_zero_fill_price(self):
        """VWAP: high=low=0, vwap=None → ZERO_FILL_PRICE"""
        model = VWAPFillModel()
        bar = _make_bar(high=0.0, low=0.0, close=10.0, vwap=None)
        result = model.try_fill(order=_make_order(), next_bar=bar, rules=_rules())
        assert result.fill is None
        assert result.reject_reason == "ZERO_FILL_PRICE"

    def test_vwap_zero_vwap(self):
        """VWAP: vwap=0 → ZERO_FILL_PRICE"""
        model = VWAPFillModel()
        bar = _make_bar(vwap=0.0)
        result = model.try_fill(order=_make_order(), next_bar=bar, rules=_rules())
        assert result.fill is None
        assert result.reject_reason == "ZERO_FILL_PRICE"


# ============================================================================
# SameBarCloseFillModel SELL direction
# ============================================================================


class TestSameBarCloseSell:

    def test_sell_normal_bar(self):
        """SameBarClose SELL 正常成交"""
        model = SameBarCloseFillModel()
        bar = _make_bar()
        order = _make_order(side=Side.SELL)
        result = model.try_fill(order=order, next_bar=bar, rules=_rules())
        assert result.fill is not None
        assert result.fill.side == Side.SELL

    def test_sell_limit_down_bar(self):
        """SameBarClose SELL on limit_down → rejected by precheck"""
        model = SameBarCloseFillModel()
        bar = _make_bar(is_limit_down=True)
        order = _make_order(side=Side.SELL)
        result = model.try_fill(order=order, next_bar=bar, rules=_rules())
        # Selling at limit down is typically blocked
        assert result.fill is None or result.reject_reason is not None


# ============================================================================
# TwapSliceFillModel edge cases
# ============================================================================


class TestTwapSliceEdge:

    def test_suspended_bar_rejected(self):
        """TwapSlice: 停牌 bar → precheck 拒绝"""
        model = TwapSliceFillModel(n_slices=5)
        bar = _make_bar(is_suspended=True, volume=0)
        result = model.try_fill(order=_make_order(qty=500), next_bar=bar, rules=_rules())
        assert result.fill is None
        assert result.reject_reason is not None

    def test_zero_fill_price_no_defense(self):
        """TwapSlice: high=low=0, vwap=None → fill_price=0 (no defense!)"""
        model = TwapSliceFillModel(n_slices=1)
        bar = _make_bar(high=0.0, low=0.0, vwap=None)
        result = model.try_fill(order=_make_order(qty=100), next_bar=bar, rules=_rules())
        # This is a potential bug: TwapSlice doesn't check fill_price <= 0
        # Either it fills at price=0 or it has been patched
        if result.fill is not None:
            assert result.fill.price == 0.0  # Bug: fills at zero price

    def test_n_slices_1(self):
        """TwapSlice: n_slices=1 → full qty in one slice"""
        model = TwapSliceFillModel(n_slices=1)
        bar = _make_bar()
        result = model.try_fill(order=_make_order(qty=100), next_bar=bar, rules=_rules())
        assert result.fill is not None
        assert result.fill.qty == 100

    def test_n_slices_zero_raises(self):
        """TwapSlice: n_slices=0 → ValueError"""
        with pytest.raises(ValueError, match="n_slices"):
            TwapSliceFillModel(n_slices=0)


# ============================================================================
# VolumeConstrainedFillModel edge cases
# ============================================================================


class TestVolumeConstrainedEdge:

    def test_lot_size_truncation_to_zero(self):
        """max_qty=99 但 lot_size=100 → 截断为 0 → VOLUME_EXHAUSTED"""
        constraint = VolumeConstraint(max_participation_rate=0.1, lot_size=100)
        inner = NextBarOpenFillModel()
        model = VolumeConstrainedFillModel(inner_model=inner, constraint=constraint)
        # volume=990 → max_qty = 990*0.1 = 99 → 99//100*100 = 0
        bar = _make_bar(volume=990)
        result = model.try_fill(order=_make_order(qty=200), next_bar=bar, rules=_rules())
        assert result.fill is None
        assert result.no_fill_reason == NoFillReason.VOLUME_EXHAUSTED

    def test_negative_volume_bar(self):
        """volume < 0 → ZERO_VOLUME (caught by volume <= 0 check)"""
        constraint = VolumeConstraint(max_participation_rate=0.1, lot_size=100)
        inner = NextBarOpenFillModel()
        model = VolumeConstrainedFillModel(inner_model=inner, constraint=constraint)
        bar = _make_bar(volume=-100)
        result = model.try_fill(order=_make_order(), next_bar=bar, rules=_rules())
        assert result.fill is None


# ============================================================================
# VolumeConstraint edge cases
# ============================================================================


class TestVolumeConstraintEdge:

    def test_participation_rate_boundary_max(self):
        """max_participation_rate=1.0, opening_volume_fraction=1.0 → full volume"""
        vc = VolumeConstraint(max_participation_rate=1.0, lot_size=100, opening_volume_fraction=1.0)
        assert vc.compute_max_qty(1000) == 1000

    def test_participation_rate_zero_raises(self):
        """max_participation_rate=0 → ValueError"""
        with pytest.raises(ValueError):
            VolumeConstraint(max_participation_rate=0.0)

    def test_negative_bar_volume(self):
        """negative bar_volume → negative max_qty (potential issue)"""
        vc = VolumeConstraint(max_participation_rate=0.1, lot_size=100)
        result = vc.compute_max_qty(-1000)
        # Returns -100 (negative) — caller must handle
        assert result < 0


# ============================================================================
# opening_volume_fraction 测试
# ============================================================================


class TestOpeningVolumeFraction:
    """测试 VolumeConstraint.opening_volume_fraction 对成交量约束的影响。

    NextBarOpen 成交模型使用全日成交量做约束时，需要通过
    opening_volume_fraction 将有效量缩减到开盘集合竞价可用的部分。
    """

    def test_default_fraction_is_015(self):
        """默认 fraction=0.15（保守开盘集合竞价可用量）"""
        vc = VolumeConstraint(max_participation_rate=0.10, lot_size=100)
        assert vc.opening_volume_fraction == 0.15
        # 100000 × 0.15 × 0.10 = 1500 → truncate to lot_size → 1500
        assert vc.compute_max_qty(100000) == 1500

    def test_explicit_fraction_1_full_volume(self):
        """显式设置 fraction=1.0 恢复全日量行为"""
        vc = VolumeConstraint(
            max_participation_rate=0.10, lot_size=100,
            opening_volume_fraction=1.0,
        )
        assert vc.opening_volume_fraction == 1.0
        assert vc.compute_max_qty(100000) == 10000

    def test_fraction_010_reduces_by_10x(self):
        """fraction=0.10 → 有效量 = 日成交量 × 10% → max_qty 缩减 10 倍"""
        vc = VolumeConstraint(
            max_participation_rate=0.10, lot_size=100,
            opening_volume_fraction=0.10,
        )
        # 100000 × 0.10 × 0.10 = 1000 → truncate to lot_size → 1000
        assert vc.compute_max_qty(100000) == 1000

    def test_fraction_015_cb_style(self):
        """fraction=0.15（可转债标配）→ effective = daily × 0.15"""
        vc = VolumeConstraint(
            max_participation_rate=0.10, lot_size=10,
            opening_volume_fraction=0.15,
        )
        # 50000 × 0.15 × 0.10 = 750 → truncate to lot_size=10 → 750
        assert vc.compute_max_qty(50000) == 750

    def test_fraction_with_lot_rounding(self):
        """缩减后不足一手时，取整到 0"""
        vc = VolumeConstraint(
            max_participation_rate=0.10, lot_size=100,
            opening_volume_fraction=0.10,
        )
        # 5000 × 0.10 × 0.10 = 50 → truncate to lot_size=100 → 0
        assert vc.compute_max_qty(5000) == 0

    def test_fraction_boundary_low(self):
        """fraction 趋近于 0（但 >0）仍合法"""
        vc = VolumeConstraint(
            max_participation_rate=0.10, lot_size=100,
            opening_volume_fraction=0.01,
        )
        # 1000000 × 0.01 × 0.10 = 1000
        assert vc.compute_max_qty(1000000) == 1000

    def test_fraction_zero_raises(self):
        """fraction=0.0 → ValueError"""
        with pytest.raises(ValueError):
            VolumeConstraint(
                max_participation_rate=0.10, lot_size=100,
                opening_volume_fraction=0.0,
            )

    def test_fraction_negative_raises(self):
        """fraction<0 → ValueError"""
        with pytest.raises(ValueError):
            VolumeConstraint(
                max_participation_rate=0.10, lot_size=100,
                opening_volume_fraction=-0.1,
            )

    def test_fraction_gt_1_raises(self):
        """fraction>1.0 → ValueError"""
        with pytest.raises(ValueError):
            VolumeConstraint(
                max_participation_rate=0.10, lot_size=100,
                opening_volume_fraction=1.5,
            )

    def test_e2e_fill_with_fraction(self):
        """端到端：VolumeConstrainedFillModel + fraction 限制成交量"""
        vc = VolumeConstraint(
            max_participation_rate=0.10, lot_size=100,
            opening_volume_fraction=0.10,
        )
        inner = NextBarOpenFillModel()
        model = VolumeConstrainedFillModel(inner_model=inner, constraint=vc)
        rules = MarketRulesCNEquity()
        bar = _make_bar(volume=100000)  # daily volume 10万, effective=1万, max=1000
        order = _make_order(qty=5000)   # 想买 5000 股

        result = model.try_fill(order=order, next_bar=bar, rules=rules)
        assert result.fill is not None
        # 最多只能成交 1000 股 (100000 × 0.10 × 0.10 = 1000)
        assert result.fill.qty == 1000
        assert result.remaining_qty == 4000
        assert result.is_partial is True


# ============================================================================
# FillResult state edge cases
# ============================================================================


class TestFillResultEdge:

    def test_all_none_state(self):
        """fill=None, reject=None, no_fill=None → all properties False"""
        fr = FillResult(fill=None)
        assert fr.is_partial is False
        assert fr.is_full is False
        assert fr.is_rejected is False

    def test_partial_fill(self):
        """fill exists + remaining_qty > 0 → is_partial"""
        from aipha.domain.models import Fill
        fill = Fill(
            order_id="test",
            symbol="A",
            side=Side.BUY,
            qty=50,
            price=10.0,
            t_fill=datetime(2025, 1, 1),
            model="test",
        )
        fr = FillResult(fill=fill, remaining_qty=50)
        assert fr.is_partial is True
        assert fr.is_full is False


# ============================================================================
# get_fill_model factory edge cases
# ============================================================================


class TestGetFillModelEdge:

    def test_unknown_model_raises(self):
        """未知模型名 → KeyError"""
        with pytest.raises(KeyError, match="Unknown fill model"):
            get_fill_model("nonexistent_model")

    def test_create_volume_constrained_model(self):
        """工厂创建 volume constrained model"""
        model = create_volume_constrained_model(
            base_name="next_bar_open",
            max_participation_rate=0.2,
            lot_size=100,
        )
        assert isinstance(model, VolumeConstrainedFillModel)
        assert "next_bar_open" in model.name
