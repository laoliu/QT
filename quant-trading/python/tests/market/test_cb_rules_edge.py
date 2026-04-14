"""可转债市场规则边界条件测试

覆盖 rules_convertible_bond.py 中未测试的分支。
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

from aipha.domain.models import Bar, Order, OrderType, Side
from aipha.market.rules_convertible_bond import (
    CBRejectReason,
    MarketRulesConvertibleBond,
    PutBackChecker,
    RedemptionChecker,
    RedemptionStatus,
)

# ── helpers ──────────────────────────────────────────────────

_T = datetime(2024, 6, 3, 10, 0, 0)


def _order(side=Side.BUY, qty=10) -> Order:
    return Order(
        order_id="cb_001",
        symbol="123001.SZ",
        side=side,
        qty=qty,
        order_type=OrderType.MARKET,
        t_submit=_T,
    )


def _bar(open_=100.0, high=120.0, low=80.0, close=110.0, is_suspended=False) -> Bar:
    return Bar(
        symbol="123001.SZ",
        start_ts=_T,
        end_ts=_T,
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=10000,
        is_suspended=is_suspended,
    )


# ══════════════════════════════════════════════════════════════
# 1. MarketRulesConvertibleBond 首日边界
# ══════════════════════════════════════════════════════════════


class TestCBFirstDayEdge:
    """首日涨跌停边界"""

    def test_first_day_down_halt(self):
        """首日跌幅 ≥ 30% → FIRST_DAY_LIMIT"""
        rules = MarketRulesConvertibleBond()
        # open=100, low=65 → down=(100-65)/100=35% ≥ 30%
        b = _bar(open_=100.0, high=100.0, low=65.0, close=70.0)
        result = rules.precheck_order(order=_order(), bar=b, is_first_day=True)
        assert result.allowed is False
        assert result.reason == CBRejectReason.FIRST_DAY_LIMIT
        assert "跌幅" in result.warning

    def test_first_day_down_exactly_30pct(self):
        """首日跌幅恰好 30% → 触发"""
        rules = MarketRulesConvertibleBond()
        b = _bar(open_=100.0, high=100.0, low=70.0, close=70.0)
        result = rules.precheck_order(order=_order(), bar=b, is_first_day=True)
        assert result.allowed is False

    def test_first_day_open_zero_skip_check(self):
        """首日 open=0 → 跳过涨跌幅检查 → 允许"""
        rules = MarketRulesConvertibleBond()
        b = _bar(open_=0.0, high=0.0, low=0.0, close=0.0)
        result = rules.precheck_order(order=_order(), bar=b, is_first_day=True)
        assert result.allowed is True

    def test_first_day_within_range(self):
        """首日涨跌幅在范围内 → 允许"""
        rules = MarketRulesConvertibleBond()
        b = _bar(open_=100.0, high=115.0, low=90.0, close=108.0)
        result = rules.precheck_order(order=_order(), bar=b, is_first_day=True)
        assert result.allowed is True


# ══════════════════════════════════════════════════════════════
# 2. 强赎状态分支
# ══════════════════════════════════════════════════════════════


class TestCBRedemptionEdge:
    """强赎状态边界"""

    def test_triggered_not_announced_warning(self):
        """is_triggered=True, is_announced=False → 允许交易 + 警告"""
        rules = MarketRulesConvertibleBond()
        rs = RedemptionStatus(
            ts_code="123001.SZ",
            is_triggered=True,
            is_announced=False,
        )
        b = _bar()
        result = rules.precheck_order(
            order=_order(), bar=b, redemption_status=rs,
        )
        assert result.allowed is True
        assert result.warning == "强赎条件已触发，请关注公告"

    def test_announced_with_date(self):
        """已公告强赎 → 允许 + 包含赎回日警告"""
        rules = MarketRulesConvertibleBond()
        rs = RedemptionStatus(
            ts_code="123001.SZ",
            is_announced=True,
            redemption_date=date(2024, 7, 15),
        )
        b = _bar()
        result = rules.precheck_order(
            order=_order(), bar=b, redemption_status=rs,
        )
        assert result.allowed is True
        assert "2024-07-15" in result.warning

    def test_no_redemption_status_no_warning(self):
        """无强赎状态 → 无警告"""
        rules = MarketRulesConvertibleBond()
        result = rules.precheck_order(order=_order(), bar=_bar())
        assert result.allowed is True
        assert result.warning is None


# ══════════════════════════════════════════════════════════════
# 3. calculate_fee 边界
# ══════════════════════════════════════════════════════════════


class TestCBFeeEdge:
    """计算费用边界"""

    def test_commission_min_takes_effect(self):
        """佣金低于最低佣金 → 使用最低佣金"""
        rules = MarketRulesConvertibleBond(
            commission_rate=Decimal("0.0000854"),
            commission_min=Decimal("5.0"),
        )
        # trade_value = 100 * 10 = 1000 → commission = 0.0854 < 5.0
        fee = rules.calculate_fee(price=Decimal("100"), qty=10, side=Side.BUY)
        assert fee == Decimal("5.0")

    def test_commission_above_min(self):
        """正常佣金 > 最低佣金"""
        rules = MarketRulesConvertibleBond(
            commission_rate=Decimal("0.0000854"),
            commission_min=Decimal("0"),
        )
        fee = rules.calculate_fee(price=Decimal("100"), qty=10000, side=Side.BUY)
        # 100*10000*0.0000854 = 85.4
        assert fee == Decimal("85.4")

    def test_zero_qty_zero_fee(self):
        """qty=0 → fee=0"""
        rules = MarketRulesConvertibleBond()
        fee = rules.calculate_fee(price=Decimal("100"), qty=0, side=Side.SELL)
        assert fee == Decimal("0")


# ══════════════════════════════════════════════════════════════
# 4. RedemptionChecker 边界
# ══════════════════════════════════════════════════════════════


class TestRedemptionCheckerEdge:
    """RedemptionChecker 边界"""

    def test_conv_price_zero(self):
        """conv_price=0 → (False, 0)"""
        rc = RedemptionChecker()
        triggered, days = rc.check(
            conv_price=Decimal("0"), stock_prices=[Decimal("100")],
        )
        assert triggered is False
        assert days == 0

    def test_conv_price_negative(self):
        """conv_price 负数 → (False, 0)"""
        rc = RedemptionChecker()
        triggered, _ = rc.check(
            conv_price=Decimal("-10"), stock_prices=[Decimal("100")],
        )
        assert triggered is False

    def test_empty_stock_prices(self):
        """空价格序列 → (False, 0)"""
        rc = RedemptionChecker()
        triggered, days = rc.check(
            conv_price=Decimal("10"), stock_prices=[],
        )
        assert triggered is False
        assert days == 0

    def test_triggered_exactly_at_threshold(self):
        """恰好达到 trigger_days → True"""
        rc = RedemptionChecker(trigger_days=3, trigger_ratio=Decimal("1.3"))
        # trigger_price = 10 * 1.3 = 13
        prices = [Decimal("15")] * 3
        triggered, days = rc.check(conv_price=Decimal("10"), stock_prices=prices)
        assert triggered is True
        assert days == 3

    def test_one_day_short(self):
        """差一天 → False"""
        rc = RedemptionChecker(trigger_days=3)
        prices = [Decimal("15")] * 2
        triggered, days = rc.check(conv_price=Decimal("10"), stock_prices=prices)
        assert triggered is False
        assert days == 2

    def test_break_in_middle(self):
        """中间断裂 → 只计连续末尾"""
        rc = RedemptionChecker(trigger_days=3)
        # 13, 12(不满足), 15, 15
        prices = [Decimal("15"), Decimal("12"), Decimal("15"), Decimal("15")]
        triggered, days = rc.check(conv_price=Decimal("10"), stock_prices=prices)
        assert days == 2  # 最后 2 天满足


# ══════════════════════════════════════════════════════════════
# 5. PutBackChecker 边界
# ══════════════════════════════════════════════════════════════


class TestPutBackCheckerEdge:
    """PutBackChecker 边界"""

    def test_conv_price_zero(self):
        """conv_price=0 → (False, 0)"""
        pc = PutBackChecker()
        triggered, _ = pc.check(
            conv_price=Decimal("0"), stock_prices=[Decimal("1")],
        )
        assert triggered is False

    def test_empty_stock_prices(self):
        """空序列 → (False, 0)"""
        pc = PutBackChecker()
        triggered, days = pc.check(
            conv_price=Decimal("10"), stock_prices=[],
        )
        assert triggered is False
        assert days == 0

    def test_triggered_put_back(self):
        """触发回售条件"""
        pc = PutBackChecker(trigger_days=2, trigger_ratio=Decimal("0.7"))
        # trigger_price = 10 * 0.7 = 7, 都 <= 7
        prices = [Decimal("6"), Decimal("5")]
        triggered, days = pc.check(conv_price=Decimal("10"), stock_prices=prices)
        assert triggered is True
        assert days == 2

    def test_break_in_middle(self):
        """中间一天回升 → 只计末尾连续"""
        pc = PutBackChecker(trigger_days=3)
        # 6, 8(>7 不满足), 5, 6
        prices = [Decimal("6"), Decimal("8"), Decimal("5"), Decimal("6")]
        triggered, days = pc.check(conv_price=Decimal("10"), stock_prices=prices)
        assert days == 2
