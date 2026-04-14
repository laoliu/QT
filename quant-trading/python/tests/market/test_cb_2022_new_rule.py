"""2022 可转债新规涨跌停分段测试

验证 2022-08-01 新规：
- 非首日 ±20% 涨跌停
- 新规前无涨跌停（首日除外）
- get_price_limits() 返回值正确
- 买单涨停拒绝 / 卖单跌停拒绝
- 边界日期（2022-07-31 vs 2022-08-01）
"""
from datetime import date, datetime
from decimal import Decimal

import pytest

from aipha.domain.models import Bar, Order, OrderType, Side
from aipha.market.rules_convertible_bond import (
    CBRejectReason,
    MarketRulesConvertibleBond,
)


def _order(side=Side.BUY, qty=100):
    return Order(
        order_id="T001",
        symbol="127001.SZ",
        side=side,
        qty=qty,
        order_type=OrderType.MARKET,
        t_submit=datetime(2024, 1, 15, 10, 0),
    )


def _bar(close=100.0, high=None, low=None, volume=10000):
    return Bar(
        symbol="127001.SZ",
        start_ts=datetime(2024, 1, 15, 9, 30),
        end_ts=datetime(2024, 1, 15, 15, 0),
        open=close * 0.99,
        high=high or close * 1.01,
        low=low or close * 0.99,
        close=close,
        volume=volume,
    )


class TestCB2022NewRulePriceLimit:
    """2022 新规非首日涨跌停测试（≥8 用例）"""

    @pytest.fixture
    def rules(self):
        return MarketRulesConvertibleBond()

    # --- 涨停拒绝 ---

    def test_buy_rejected_at_limit_up(self, rules):
        """新规后非首日，close=涨停价 → 买单被拒"""
        prev_close = 100.0
        limit_up = prev_close * 1.20  # 120
        bar = _bar(close=120.0, high=120.0)
        result = rules.precheck_order(
            order=_order(Side.BUY),
            bar=bar,
            trade_date=date(2024, 1, 15),
            prev_close=prev_close,
        )
        assert result.allowed is False
        assert result.reason == CBRejectReason.PRICE_LIMIT

    def test_sell_allowed_at_limit_up(self, rules):
        """涨停时卖单不受限"""
        bar = _bar(close=120.0, high=120.0)
        result = rules.precheck_order(
            order=_order(Side.SELL),
            bar=bar,
            trade_date=date(2024, 1, 15),
            prev_close=100.0,
        )
        assert result.allowed is True

    # --- 跌停拒绝 ---

    def test_sell_rejected_at_limit_down(self, rules):
        """新规后非首日，close=跌停价 → 卖单被拒"""
        prev_close = 100.0
        bar = _bar(close=80.0, low=80.0)
        result = rules.precheck_order(
            order=_order(Side.SELL),
            bar=bar,
            trade_date=date(2024, 1, 15),
            prev_close=prev_close,
        )
        assert result.allowed is False
        assert result.reason == CBRejectReason.PRICE_LIMIT

    def test_buy_allowed_at_limit_down(self, rules):
        """跌停时买单不受限"""
        bar = _bar(close=80.0, low=80.0)
        result = rules.precheck_order(
            order=_order(Side.BUY),
            bar=bar,
            trade_date=date(2024, 1, 15),
            prev_close=100.0,
        )
        assert result.allowed is True

    # --- 正常价位 ---

    def test_normal_price_passes(self, rules):
        """正常价位（涨10%）→ 通过"""
        bar = _bar(close=110.0)
        result = rules.precheck_order(
            order=_order(Side.BUY),
            bar=bar,
            trade_date=date(2024, 1, 15),
            prev_close=100.0,
        )
        assert result.allowed is True

    # --- 2022 新规前后分段 ---

    def test_before_new_rule_no_limit(self, rules):
        """2022-07-31（新规前）→ 无涨跌停限制，涨停位仍通过"""
        bar = _bar(close=120.0, high=120.0)
        result = rules.precheck_order(
            order=_order(Side.BUY),
            bar=bar,
            trade_date=date(2022, 7, 31),
            prev_close=100.0,
        )
        assert result.allowed is True

    def test_new_rule_exact_date(self, rules):
        """2022-08-01（新规生效日）→ 涨停位买单被拒"""
        bar = _bar(close=120.0, high=120.0)
        result = rules.precheck_order(
            order=_order(Side.BUY),
            bar=bar,
            trade_date=date(2022, 8, 1),
            prev_close=100.0,
        )
        assert result.allowed is False

    # --- 无 prev_close 时不检查 ---

    def test_no_prev_close_skips_check(self, rules):
        """未提供 prev_close → 不检查涨跌停"""
        bar = _bar(close=200.0)
        result = rules.precheck_order(
            order=_order(Side.BUY),
            bar=bar,
            trade_date=date(2024, 1, 15),
            prev_close=None,
        )
        assert result.allowed is True

    # --- 首日不受新规涨跌停影响 ---

    def test_first_day_skips_new_rule(self, rules):
        """首日走首日逻辑，不走新规涨跌停"""
        bar = _bar(close=150.0, high=150.0, low=100.0)
        # open=99, high=150 → intraday_up=(150-99)/99=51.5% → 超首日临停
        bar_high = Bar(
            symbol="127001.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=100.0,
            high=125.0,  # +25% > 20%
            low=95.0,
            close=120.0,
            volume=10000,
        )
        result = rules.precheck_order(
            order=_order(Side.BUY),
            bar=bar_high,
            is_first_day=True,
            trade_date=date(2024, 1, 15),
            prev_close=100.0,
        )
        # 被首日规则拦截
        assert result.allowed is False
        assert result.reason == CBRejectReason.FIRST_DAY_LIMIT


class TestGetPriceLimits:
    """get_price_limits() 测试"""

    @pytest.fixture
    def rules(self):
        return MarketRulesConvertibleBond()

    def test_after_new_rule(self, rules):
        """新规后返回涨跌停价"""
        up, down = rules.get_price_limits(
            prev_close=100.0, trade_date=date(2024, 1, 1)
        )
        assert up == pytest.approx(120.0)
        assert down == pytest.approx(80.0)

    def test_before_new_rule(self, rules):
        """新规前返回 None, None"""
        up, down = rules.get_price_limits(
            prev_close=100.0, trade_date=date(2022, 7, 1)
        )
        assert up is None
        assert down is None

    def test_first_day(self, rules):
        """首日返回 None, None"""
        up, down = rules.get_price_limits(
            prev_close=100.0, trade_date=date(2024, 1, 1), is_first_day=True
        )
        assert up is None
        assert down is None

    def test_no_trade_date(self, rules):
        """无日期 → 无限制"""
        up, down = rules.get_price_limits(prev_close=100.0)
        assert up is None
        assert down is None

    def test_price_values_precision(self, rules):
        """非整数前收的涨跌停价精度"""
        up, down = rules.get_price_limits(
            prev_close=123.45, trade_date=date(2024, 1, 1)
        )
        assert up == pytest.approx(148.14)
        assert down == pytest.approx(98.76)


class TestCBNewRuleEnumValues:
    """新增枚举值测试"""

    def test_price_limit_enum(self):
        assert CBRejectReason.PRICE_LIMIT == "PRICE_LIMIT"

    def test_temporary_halt_enum(self):
        assert CBRejectReason.TEMPORARY_HALT == "TEMPORARY_HALT"
