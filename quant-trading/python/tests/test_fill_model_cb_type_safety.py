"""CB FillModel 类型安全测试

验证 FillModel 能正确配合 MarketRulesConvertibleBond 工作：
- lot_size=10 检查
- 2022 新规 ±20% 涨跌停（非首日）
- 停牌拒绝
- SameBarClose（T+0 模型）
- VolumeConstrained + CB lot_size
- TwapSlice 使用 rules.lot_size 而非硬编码
- 强赎警告透传

注意：所有 Bar 都必须给 prev_close。§13.3 规定 prev_close 缺失时
涨跌停无法校验 —— 不得静默放行，一律拒单（DATA_MISSING）。
不给 prev_close 的话 precheck 会在 fill model 之前就短路，
测试看起来在测撮合，实际什么都没测到。
（本文件早期版本正是如此，且 docstring 还写着「无涨跌停限制」。）
"""

from datetime import datetime
from decimal import Decimal

import pytest

from aipha.domain.models import Bar, Fill, Order, OrderType, Side
from aipha.execution.fill_models import (
    FillModel,
    FillResult,
    NextBarOpenFillModel,
    NextBarCloseFillModel,
    SameBarCloseFillModel,
    BarLimitTouchFillModel,
    VWAPFillModel,
    VolumeConstrainedFillModel,
    VolumeConstraint,
    TwapSliceFillModel,
    get_fill_model,
    create_volume_constrained_model,
)
from aipha.market.base import MarketRules, PrecheckDecision
from aipha.market.rules_cn_equity import MarketRulesCNEquity, RejectReason
from aipha.market.rules_convertible_bond import (
    CBRejectReason,
    MarketRulesConvertibleBond,
    RedemptionStatus,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def cb_rules() -> MarketRulesConvertibleBond:
    """可转债默认规则"""
    return MarketRulesConvertibleBond(
        lot_size=10,
        t_plus=0,
        commission_rate=Decimal("0.0000854"),
    )


@pytest.fixture
def eq_rules() -> MarketRulesCNEquity:
    """A 股默认规则"""
    return MarketRulesCNEquity(lot_size=100)


@pytest.fixture
def cb_bar() -> Bar:
    """正常可转债行情

    prev_close=120 → 2022 新规 ±20% 区间 [96, 144]，close=122 在区间内。
    """
    return Bar(
        symbol="127075.SZ",
        start_ts=datetime(2024, 1, 15, 9, 30),
        end_ts=datetime(2024, 1, 15, 15, 0),
        open=120.0,
        high=125.0,
        low=118.0,
        close=122.0,
        volume=100000,
        is_suspended=False,
        prev_close=120.0,
    )


@pytest.fixture
def suspended_bar() -> Bar:
    """停牌行情"""
    return Bar(
        symbol="127075.SZ",
        start_ts=datetime(2024, 1, 15, 9, 30),
        end_ts=datetime(2024, 1, 15, 15, 0),
        open=120.0,
        high=120.0,
        low=120.0,
        close=120.0,
        volume=0,
        is_suspended=True,
        prev_close=120.0,
    )


@pytest.fixture
def limit_up_bar() -> Bar:
    """涨停行情（A 股场景，CB 不适用）

    prev_close=10 → close=11 正好是 A 股 +10% 涨停价。
    """
    return Bar(
        symbol="600000.SH",
        start_ts=datetime(2024, 1, 15, 9, 30),
        end_ts=datetime(2024, 1, 15, 15, 0),
        open=10.0,
        high=11.0,
        low=9.9,
        close=11.0,
        volume=50000,
        is_suspended=False,
        is_limit_up=True,
        prev_close=10.0,
    )


def _make_order(
    qty: int,
    side: Side = Side.BUY,
    order_type: OrderType = OrderType.MARKET,
    limit_price: float | None = None,
) -> Order:
    return Order(
        order_id="test_001",
        symbol="127075.SZ",
        side=side,
        qty=qty,
        order_type=order_type,
        limit_price=limit_price,
        t_submit=datetime(2024, 1, 15, 10, 0),
    )


# ============================================================================
# Protocol 一致性
# ============================================================================


class TestMarketRulesProtocol:
    """验证 MarketRules Protocol 一致性"""

    def test_cn_equity_is_market_rules(self, eq_rules):
        assert isinstance(eq_rules, MarketRules)

    def test_convertible_bond_is_market_rules(self, cb_rules):
        assert isinstance(cb_rules, MarketRules)

    def test_lot_size_attribute(self, cb_rules, eq_rules):
        assert cb_rules.lot_size == 10
        assert eq_rules.lot_size == 100

    def test_precheck_unified_return_type(self, cb_rules, eq_rules, cb_bar):
        """两种规则返回统一的 PrecheckDecision"""
        order = _make_order(qty=100)  # 同时满足 10 和 100 的整数倍
        eq_bar = Bar(
            symbol="600000.SH",
            start_ts=cb_bar.start_ts,
            end_ts=cb_bar.end_ts,
            open=10.0, high=11.0, low=9.9, close=10.5,
            volume=50000, is_suspended=False,
            prev_close=10.0,
        )

        r_eq = eq_rules.precheck_order(order=order, bar=eq_bar)
        r_cb = cb_rules.precheck_order(order=order, bar=cb_bar)

        assert isinstance(r_eq, PrecheckDecision)
        assert isinstance(r_cb, PrecheckDecision)
        assert r_eq.allowed is True
        assert r_cb.allowed is True


# ============================================================================
# CB + FillModel 交互测试
# ============================================================================


class TestFillModelWithCBRules:
    """验证所有 FillModel 在 CB 规则下正确工作"""

    def test_next_bar_open_cb_valid(self, cb_rules, cb_bar):
        """NextBarOpen + CB 有效订单"""
        fm = NextBarOpenFillModel()
        order = _make_order(qty=10)  # 1 手 CB

        result = fm.try_fill(order=order, next_bar=cb_bar, rules=cb_rules)

        assert result.fill is not None
        assert result.fill.price == 120.0  # open
        assert result.fill.qty == 10
        assert result.reject_reason is None

    def test_next_bar_open_cb_lot_reject(self, cb_rules, cb_bar):
        """NextBarOpen + CB 非整手拒绝"""
        fm = NextBarOpenFillModel()
        order = _make_order(qty=15)

        result = fm.try_fill(order=order, next_bar=cb_bar, rules=cb_rules)

        assert result.fill is None
        assert result.is_rejected
        assert result.reject_reason == CBRejectReason.LOT_SIZE_ROUNDING

    def test_next_bar_close_cb(self, cb_rules, cb_bar):
        """NextBarClose + CB"""
        fm = NextBarCloseFillModel()
        order = _make_order(qty=20)

        result = fm.try_fill(order=order, next_bar=cb_bar, rules=cb_rules)

        assert result.fill is not None
        assert result.fill.price == 122.0  # close

    def test_same_bar_close_cb_t0(self, cb_rules, cb_bar):
        """SameBarClose — CB T+0 核心场景"""
        fm = SameBarCloseFillModel()
        assert fm.is_same_bar is True

        order = _make_order(qty=10)
        result = fm.try_fill(order=order, next_bar=cb_bar, rules=cb_rules)

        assert result.fill is not None
        assert result.fill.price == 122.0
        assert result.fill.model == "same_bar_close"

    def test_bar_limit_touch_cb(self, cb_rules, cb_bar):
        """BarLimitTouch + CB 限价触发"""
        fm = BarLimitTouchFillModel()
        order = _make_order(
            qty=10,
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            limit_price=119.0,
        )

        result = fm.try_fill(order=order, next_bar=cb_bar, rules=cb_rules)

        assert result.fill is not None
        assert result.fill.price == 119.0  # 限价
        assert result.fill.qty == 10

    def test_bar_limit_touch_cb_not_touched(self, cb_rules, cb_bar):
        """BarLimitTouch + CB 限价未触及"""
        fm = BarLimitTouchFillModel()
        order = _make_order(
            qty=10,
            side=Side.BUY,
            order_type=OrderType.LIMIT,
            limit_price=117.0,  # 低于 low=118
        )

        result = fm.try_fill(order=order, next_bar=cb_bar, rules=cb_rules)

        assert result.fill is None
        assert result.no_fill_reason == "LIMIT_NOT_TOUCHED"

    def test_vwap_cb(self, cb_rules, cb_bar):
        """VWAP + CB"""
        fm = VWAPFillModel()
        order = _make_order(qty=30)

        result = fm.try_fill(order=order, next_bar=cb_bar, rules=cb_rules)

        assert result.fill is not None
        # 无 vwap 字段时使用 midpoint
        expected_price = (125.0 + 118.0) / 2
        assert result.fill.price == expected_price

    def test_suspended_cb_reject(self, cb_rules, suspended_bar):
        """停牌拒绝（所有 FillModel 通用）"""
        fm = NextBarOpenFillModel()
        order = _make_order(qty=10)

        result = fm.try_fill(order=order, next_bar=suspended_bar, rules=cb_rules)

        assert result.fill is None
        assert result.is_rejected
        assert result.reject_reason == CBRejectReason.SUSPENDED


class TestCBNoLimitUpDown:
    """验证 CB 规则不检查涨跌停（与 A 股差异）"""

    def test_cb_allows_buy_on_limit_up_bar(self, cb_rules, limit_up_bar):
        """CB 规则在 is_limit_up=True 的 bar 上不拒绝买入"""
        fm = NextBarOpenFillModel()
        order = _make_order(qty=10, side=Side.BUY)

        result = fm.try_fill(order=order, next_bar=limit_up_bar, rules=cb_rules)

        # CB 无涨跌停限制 —— 应该允许
        assert result.fill is not None
        assert result.reject_reason is None

    def test_equity_rejects_buy_on_limit_up(self, eq_rules, limit_up_bar):
        """A 股规则在涨停 bar 上拒绝买入（对比用例）"""
        fm = NextBarOpenFillModel()
        order = Order(
            order_id="eq_001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=100,
            order_type=OrderType.MARKET,
            t_submit=datetime(2024, 1, 15, 10, 0),
        )

        result = fm.try_fill(order=order, next_bar=limit_up_bar, rules=eq_rules)

        assert result.fill is None
        assert result.reject_reason == RejectReason.LIMIT_UP


# ============================================================================
# VolumeConstrained + CB lot_size
# ============================================================================


class TestVolumeConstrainedCB:
    """VolumeConstrained 在 CB 场景下的行为"""

    def test_volume_constrained_cb_lot_size(self, cb_rules, cb_bar):
        """VolumeConstraint 使用 CB lot_size=10"""
        inner = NextBarOpenFillModel()
        constraint = VolumeConstraint(max_participation_rate=0.10, lot_size=10)
        fm = VolumeConstrainedFillModel(inner_model=inner, constraint=constraint)

        # volume=100000, 10% = 10000, 远超 qty=50
        order = _make_order(qty=50)
        result = fm.try_fill(order=order, next_bar=cb_bar, rules=cb_rules)

        assert result.fill is not None
        assert result.fill.qty == 50  # 全部成交

    def test_volume_constrained_cb_partial(self, cb_rules):
        """CB 订单量超过可用量时部分成交"""
        low_vol_bar = Bar(
            symbol="127075.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=120.0, high=125.0, low=118.0, close=122.0,
            volume=200,  # 低成交量
            is_suspended=False,
            prev_close=120.0,
        )

        inner = NextBarOpenFillModel()
        constraint = VolumeConstraint(
            max_participation_rate=0.10, lot_size=10, opening_volume_fraction=1.0,
        )
        fm = VolumeConstrainedFillModel(inner_model=inner, constraint=constraint)

        # 10% of 200 = 20 张，round to lot=10 → 20 张
        order = _make_order(qty=50)
        result = fm.try_fill(order=order, next_bar=low_vol_bar, rules=cb_rules)

        assert result.fill is not None
        assert result.fill.qty == 20
        assert result.remaining_qty == 30
        assert result.is_partial


# ============================================================================
# TwapSlice 使用 rules.lot_size
# ============================================================================


class TestTwapSliceCB:
    """TwapSlice 不再硬编码 A 股 lot_size=100"""

    def test_twap_uses_rules_lot_size(self, cb_rules, cb_bar):
        """TWAP slice 使用 rules.lot_size（10）而非硬编码 100"""
        fm = TwapSliceFillModel(n_slices=5)
        # 100 张 / 5 slices = 20 张/slice, round_to_10 = 20
        order = _make_order(qty=100)

        result = fm.try_fill(order=order, next_bar=cb_bar, rules=cb_rules)

        assert result.fill is not None
        assert result.fill.qty == 20
        assert result.remaining_qty == 80

    def test_twap_with_equity_lot_size(self, eq_rules):
        """A 股 TWAP 仍使用 lot_size=100"""
        fm = TwapSliceFillModel(n_slices=5)
        bar = Bar(
            symbol="600000.SH",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0, high=11.0, low=9.9, close=10.5,
            volume=50000, is_suspended=False,
            prev_close=10.0,
        )
        # 500 shares / 5 slices = 100, round_to_100 = 100
        order = Order(
            order_id="twap_eq",
            symbol="600000.SH",
            side=Side.BUY,
            qty=500,
            order_type=OrderType.MARKET,
            t_submit=datetime(2024, 1, 15, 10, 0),
        )

        result = fm.try_fill(order=order, next_bar=bar, rules=eq_rules)

        assert result.fill is not None
        assert result.fill.qty == 100
        assert result.remaining_qty == 400

    def test_twap_cb_small_order_not_exhausted(self, cb_rules, cb_bar):
        """CB 小订单按 lot_size=10 切片不会因 100 整除被 VOLUME_EXHAUSTED"""
        fm = TwapSliceFillModel(n_slices=2)
        # 20 张 / 2 slices = 10, round_to_10 = 10 ✓
        order = _make_order(qty=20)

        result = fm.try_fill(order=order, next_bar=cb_bar, rules=cb_rules)

        assert result.fill is not None
        assert result.fill.qty == 10

    def test_twap_cb_tiny_order_exhausted(self, cb_rules, cb_bar):
        """CB 极小订单按 lot_size=10 切片 → qty/slices < lot_size → EXHAUSTED"""
        fm = TwapSliceFillModel(n_slices=5)
        # 10 张 / 5 slices = 2, round_to_10 = 0 → exhausted
        order = _make_order(qty=10)

        result = fm.try_fill(order=order, next_bar=cb_bar, rules=cb_rules)

        assert result.fill is None
        assert result.no_fill_reason == "VOLUME_EXHAUSTED"


# ============================================================================
# FillResult 兼容性
# ============================================================================


class TestFillResultRejectReasonType:
    """验证 FillResult.reject_reason 可以存储 str 值"""

    def test_reject_reason_is_str_compatible(self, cb_rules, cb_bar):
        """RejectReason / CBRejectReason 作为 str 存储"""
        fm = NextBarOpenFillModel()
        order = _make_order(qty=15)
        result = fm.try_fill(order=order, next_bar=cb_bar, rules=cb_rules)

        assert result.reject_reason is not None
        assert isinstance(result.reject_reason, str)
        assert "LOT_SIZE_ROUNDING" in str(result.reject_reason)

    def test_reject_reason_str_conversion(self, eq_rules):
        """A 股拒绝原因也可以 str() 转换"""
        fm = NextBarOpenFillModel()
        bar = Bar(
            symbol="600000.SH",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0, high=10.0, low=10.0, close=10.0,
            volume=0, is_suspended=True,
            prev_close=10.0,
        )
        order = Order(
            order_id="eq_001", symbol="600000.SH", side=Side.BUY,
            qty=100, order_type=OrderType.MARKET,
            t_submit=datetime(2024, 1, 15, 10, 0),
        )

        result = fm.try_fill(order=order, next_bar=bar, rules=eq_rules)

        assert result.is_rejected
        assert str(result.reject_reason) == "RejectReason.SUSPENDED"


# ============================================================================
# PrecheckDecision warning 字段
# ============================================================================


class TestPrecheckDecisionWarning:
    """验证统一 PrecheckDecision 的 warning 字段"""

    def test_equity_precheck_no_warning(self, eq_rules):
        """A 股预检无 warning 字段"""
        bar = Bar(
            symbol="600000.SH",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0, high=11.0, low=9.9, close=10.5,
            volume=50000, is_suspended=False,
            prev_close=10.0,
        )
        order = Order(
            order_id="eq_001", symbol="600000.SH", side=Side.BUY,
            qty=100, order_type=OrderType.MARKET,
            t_submit=datetime(2024, 1, 15, 10, 0),
        )

        result = eq_rules.precheck_order(order=order, bar=bar)

        assert result.allowed is True
        assert result.warning is None

    def test_cb_precheck_with_redemption_warning(self, cb_rules, cb_bar):
        """CB 预检含强赎警告"""
        redemption = RedemptionStatus(
            ts_code="127075.SZ",
            is_announced=True,
            announce_date=datetime(2024, 1, 10).date(),
            redemption_date=datetime(2024, 1, 20).date(),
        )
        order = _make_order(qty=10)

        result = cb_rules.precheck_order(
            order=order, bar=cb_bar, redemption_status=redemption,
        )

        assert result.allowed is True
        assert result.warning is not None
        assert "强赎已公告" in result.warning


# ============================================================================
# create_volume_constrained_model 默认用 A 股 lot_size
# ============================================================================


class TestFactoryFunctions:
    """验证工厂函数行为"""

    def test_get_fill_model_all(self):
        """所有注册模型可通过名称获取"""
        for name in ["next_bar_open", "next_bar_close", "same_bar_close",
                      "bar_limit_touch", "vwap"]:
            fm = get_fill_model(name)
            assert fm.name == name

    def test_create_volume_constrained_default(self):
        """默认 lot_size=100（A 股）"""
        fm = create_volume_constrained_model()
        assert fm.name == "next_bar_open_volume_constrained"
