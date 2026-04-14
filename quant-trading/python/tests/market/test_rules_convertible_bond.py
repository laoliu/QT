"""可转债市场规则测试"""
from datetime import datetime
from decimal import Decimal

import pytest

from aipha.domain.models import Bar, Order, OrderType, Side
from aipha.market.rules_convertible_bond import (
    CBPrecheckDecision,
    CBRejectReason,
    MarketRulesConvertibleBond,
    PutBackChecker,
    RedemptionChecker,
    RedemptionStatus,
)


class TestMarketRulesConvertibleBond:
    """可转债市场规则测试"""

    @pytest.fixture
    def rules(self):
        """默认市场规则"""
        return MarketRulesConvertibleBond(
            lot_size=10,
            t_plus=0,
            commission_rate=Decimal("0.0000854"),
        )

    @pytest.fixture
    def normal_bar(self):
        """正常交易行情"""
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
        )

    def test_valid_order(self, rules, normal_bar):
        """测试有效订单"""
        order = Order(
            order_id="001",
            symbol="127075.SZ",
            side=Side.BUY,
            qty=100,  # 10手
            order_type=OrderType.MARKET,
            t_submit=datetime(2024, 1, 15, 10, 0),
        )

        result = rules.precheck_order(order=order, bar=normal_bar)

        assert result.allowed is True
        assert result.reason is None

    def test_lot_size_reject(self, rules, normal_bar):
        """测试手数不正确拒绝"""
        order = Order(
            order_id="001",
            symbol="127075.SZ",
            side=Side.BUY,
            qty=15,  # 1.5手，不是整数手
            order_type=OrderType.MARKET,
            t_submit=datetime(2024, 1, 15, 10, 0),
        )

        result = rules.precheck_order(order=order, bar=normal_bar)

        assert result.allowed is False
        assert result.reason == CBRejectReason.LOT_SIZE_ROUNDING

    def test_suspended_reject(self, rules):
        """测试停牌拒绝"""
        suspended_bar = Bar(
            symbol="127075.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=120.0,
            high=120.0,
            low=120.0,
            close=120.0,
            volume=0,
            is_suspended=True,
        )

        order = Order(
            order_id="001",
            symbol="127075.SZ",
            side=Side.BUY,
            qty=100,
            order_type=OrderType.MARKET,
            t_submit=datetime(2024, 1, 15, 10, 0),
        )

        result = rules.precheck_order(order=order, bar=suspended_bar)

        assert result.allowed is False
        assert result.reason == CBRejectReason.SUSPENDED

    def test_redemption_warning(self, rules, normal_bar):
        """测试强赎警告"""
        redemption_status = RedemptionStatus(
            ts_code="127075.SZ",
            is_announced=True,
            announce_date=datetime(2024, 1, 10).date(),
            redemption_date=datetime(2024, 1, 20).date(),
        )

        order = Order(
            order_id="001",
            symbol="127075.SZ",
            side=Side.BUY,
            qty=100,
            order_type=OrderType.MARKET,
            t_submit=datetime(2024, 1, 15, 10, 0),
        )

        result = rules.precheck_order(
            order=order,
            bar=normal_bar,
            redemption_status=redemption_status,
        )

        # 强赎已公告，但仍允许交易（带警告）
        assert result.allowed is True
        assert result.warning is not None
        assert "强赎已公告" in result.warning

    def test_calculate_fee(self, rules):
        """测试费用计算"""
        # 成交价格 120 元，数量 100 张
        # 成交额 = 120 × 100 = 12000 元
        # 佣金 = 12000 × 0.0000854 = 1.0248 元 → 低于最低佣金5元，取5
        fee_small = rules.calculate_fee(
            price=Decimal("120.0"),
            qty=100,
            side=Side.BUY,
        )
        # 小额交易触发最低佣金
        assert fee_small == rules.commission_min  # 5 元

        # 大额交易: 120 × 10000 = 1,200,000 元
        # 佣金 = 1,200,000 × 0.0000854 = 102.48 元 > 5 元
        fee_large = rules.calculate_fee(
            price=Decimal("120.0"),
            qty=10000,
            side=Side.BUY,
        )
        assert fee_large == Decimal("120.0") * 10000 * Decimal("0.0000854")

    def test_no_stamp_tax(self, rules):
        """测试可转债免印花税"""
        # 用大额成交确保超过最低佣金，便于精确对比
        fee_buy = rules.calculate_fee(
            price=Decimal("120.0"),
            qty=10000,
            side=Side.BUY,
        )

        fee_sell = rules.calculate_fee(
            price=Decimal("120.0"),
            qty=10000,
            side=Side.SELL,
        )

        # 买卖佣金相同（可转债无印花税）
        assert fee_buy == fee_sell


class TestRedemptionChecker:
    """强赎条件检查器测试"""

    def test_trigger_condition(self):
        """测试触发强赎条件"""
        checker = RedemptionChecker(
            trigger_ratio=Decimal("1.3"),
            trigger_days=15,
        )

        # 转股价 10 元，触发价 13 元
        # 连续 20 天价格 >= 13 元
        stock_prices = [Decimal("14.0")] * 20

        is_triggered, consecutive = checker.check(
            conv_price=Decimal("10.0"),
            stock_prices=stock_prices,
        )

        assert is_triggered is True
        assert consecutive == 20

    def test_not_trigger_insufficient_days(self):
        """测试连续天数不足"""
        checker = RedemptionChecker(
            trigger_ratio=Decimal("1.3"),
            trigger_days=15,
        )

        # 只有 10 天高于触发价
        stock_prices = [Decimal("12.0")] * 5 + [Decimal("14.0")] * 10

        is_triggered, consecutive = checker.check(
            conv_price=Decimal("10.0"),
            stock_prices=stock_prices,
        )

        assert is_triggered is False
        assert consecutive == 10

    def test_break_in_middle(self):
        """测试中间有回落"""
        checker = RedemptionChecker(
            trigger_ratio=Decimal("1.3"),
            trigger_days=15,
        )

        # 前面高于触发价，中间回落，最后再高于
        stock_prices = [Decimal("14.0")] * 10 + [Decimal("12.0")] + [Decimal("14.0")] * 5

        is_triggered, consecutive = checker.check(
            conv_price=Decimal("10.0"),
            stock_prices=stock_prices,
        )

        assert is_triggered is False
        assert consecutive == 5  # 只有最后 5 天连续


class TestPutBackChecker:
    """回售条件检查器测试"""

    def test_trigger_put_back(self):
        """测试触发回售条件"""
        checker = PutBackChecker(
            trigger_ratio=Decimal("0.7"),
            trigger_days=30,
        )

        # 转股价 10 元，触发价 7 元
        # 连续 35 天价格 <= 7 元
        stock_prices = [Decimal("6.5")] * 35

        is_triggered, consecutive = checker.check(
            conv_price=Decimal("10.0"),
            stock_prices=stock_prices,
        )

        assert is_triggered is True
        assert consecutive == 35

    def test_not_trigger_put_back(self):
        """测试未触发回售条件"""
        checker = PutBackChecker(
            trigger_ratio=Decimal("0.7"),
            trigger_days=30,
        )

        # 只有 20 天低于触发价
        stock_prices = [Decimal("8.0")] * 10 + [Decimal("6.5")] * 20

        is_triggered, consecutive = checker.check(
            conv_price=Decimal("10.0"),
            stock_prices=stock_prices,
        )

        assert is_triggered is False
        assert consecutive == 20


class TestPrevCloseNoneHandling:
    """§13.3: prev_close=None 时不静默跳过，返回 warning"""

    @pytest.fixture
    def rules(self):
        return MarketRulesConvertibleBond(
            lot_size=10,
            t_plus=0,
            commission_rate=Decimal("0.0000854"),
        )

    def test_prev_close_none_returns_warning(self, rules):
        """prev_close=None 时订单放行但附带 warning"""
        from datetime import date as date_cls
        bar = Bar(
            symbol="127075.SZ",
            start_ts=datetime(2023, 1, 15, 9, 30),
            end_ts=datetime(2023, 1, 15, 15, 0),
            open=120.0, high=125.0, low=118.0,
            close=122.0, volume=100000,
        )
        order = Order(
            order_id="001", symbol="127075.SZ",
            side=Side.BUY, qty=100,
            order_type=OrderType.MARKET,
            t_submit=datetime(2023, 1, 15, 10, 0),
        )
        result = rules.precheck_order(
            order=order, bar=bar,
            trade_date=date_cls(2023, 1, 15),
            prev_close=None,
        )
        # 允许交易但有 warning
        assert result.allowed is True
        assert result.warning is not None
        assert "prev_close" in result.warning
        assert "未校验" in result.warning

    def test_prev_close_valid_no_warning(self, rules):
        """prev_close 有效时正常进行涨跌停检查"""
        from datetime import date as date_cls
        bar = Bar(
            symbol="127075.SZ",
            start_ts=datetime(2023, 1, 15, 9, 30),
            end_ts=datetime(2023, 1, 15, 15, 0),
            open=120.0, high=125.0, low=118.0,
            close=122.0, volume=100000,
        )
        order = Order(
            order_id="001", symbol="127075.SZ",
            side=Side.BUY, qty=100,
            order_type=OrderType.MARKET,
            t_submit=datetime(2023, 1, 15, 10, 0),
        )
        result = rules.precheck_order(
            order=order, bar=bar,
            trade_date=date_cls(2023, 1, 15),
            prev_close=120.0,
        )
        assert result.allowed is True
        assert result.warning is None
