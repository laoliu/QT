"""
OrderValidator 单元测试 - 统一订单校验与拒单日志

测试覆盖：
1. 交易时段校验
2. 数量校验
3. 价格校验
4. 停牌/涨跌停校验
5. T+1持仓校验
6. 资金校验
7. 综合校验流程
8. 拒单日志记录
"""

from datetime import datetime
from decimal import Decimal

import pytest

from aipha.market.order_validator import (
    OrderSide,
    OrderValidator,
    RejectReason,
    ValidationResult,
)
from aipha.portfolio.position import Position


class TestTradingSessionValidation:
    """交易时段校验测试"""

    def setup_method(self):
        self.validator = OrderValidator()

    def test_trading_day_trading_hour(self):
        """测试：交易日交易时间"""
        # 2024-01-02 10:00 是交易日交易时间
        check_time = datetime(2024, 1, 2, 10, 0, 0)
        result = self.validator.validate_trading_session(check_time)

        assert result.is_valid is True

    def test_non_trading_day(self):
        """测试：非交易日"""
        # 2024-01-01 元旦
        check_time = datetime(2024, 1, 1, 10, 0, 0)
        result = self.validator.validate_trading_session(check_time)

        assert result.is_valid is False
        assert result.reject_reason == RejectReason.NON_TRADING_DAY

    def test_weekend(self):
        """测试：周末"""
        # 2024-01-06 是周六
        check_time = datetime(2024, 1, 6, 10, 0, 0)
        result = self.validator.validate_trading_session(check_time)

        assert result.is_valid is False
        assert result.reject_reason == RejectReason.NON_TRADING_DAY

    def test_lunch_break(self):
        """测试：午间休市"""
        check_time = datetime(2024, 1, 2, 12, 0, 0)
        result = self.validator.validate_trading_session(check_time)

        assert result.is_valid is False
        assert result.reject_reason == RejectReason.MARKET_CLOSED
        assert "午间" in result.reject_detail

    def test_after_market_close(self):
        """测试：收盘后"""
        check_time = datetime(2024, 1, 2, 16, 0, 0)
        result = self.validator.validate_trading_session(check_time)

        assert result.is_valid is False
        assert result.reject_reason == RejectReason.MARKET_CLOSED

    def test_call_auction_warning(self):
        """测试：集合竞价时段（有警告）"""
        # 9:20 早盘集合竞价
        check_time = datetime(2024, 1, 2, 9, 20, 0)
        result = self.validator.validate_trading_session(check_time)

        assert result.is_valid is True
        assert len(result.warnings) > 0
        assert "集合竞价" in result.warnings[0]


class TestQuantityValidation:
    """数量校验测试"""

    def setup_method(self):
        self.validator = OrderValidator()

    def test_valid_quantity(self):
        """测试：有效数量"""
        result = self.validator.validate_quantity(1000)
        assert result.is_valid is True

    def test_zero_quantity(self):
        """测试：零数量"""
        result = self.validator.validate_quantity(0)
        assert result.is_valid is False
        assert result.reject_reason == RejectReason.INVALID_QUANTITY

    def test_negative_quantity(self):
        """测试：负数量"""
        result = self.validator.validate_quantity(-100)
        assert result.is_valid is False
        assert result.reject_reason == RejectReason.INVALID_QUANTITY

    def test_not_round_lot(self):
        """测试：非整手"""
        result = self.validator.validate_quantity(150)
        assert result.is_valid is False
        assert result.reject_reason == RejectReason.NOT_ROUND_LOT

    def test_exceeds_max_qty(self):
        """测试：超过单笔上限"""
        result = self.validator.validate_quantity(2_000_000)
        assert result.is_valid is False
        assert result.reject_reason == RejectReason.EXCEEDS_MAX_QTY

    def test_cb_lot_size_10_valid(self):
        """测试：可转债 lot_size=10 有效数量"""
        cb_validator = OrderValidator(lot_size=10)
        result = cb_validator.validate_quantity(10)
        assert result.is_valid is True

        result = cb_validator.validate_quantity(30)
        assert result.is_valid is True

    def test_cb_lot_size_10_invalid(self):
        """测试：可转债 lot_size=10 非整手"""
        cb_validator = OrderValidator(lot_size=10)
        result = cb_validator.validate_quantity(15)
        assert result.is_valid is False
        assert result.reject_reason == RejectReason.NOT_ROUND_LOT
        assert "10" in result.reject_detail

    def test_cb_lot_size_10_rejects_odd(self):
        """测试：可转债 lot_size=10 拒绝 A 股整手但非 CB 整手"""
        cb_validator = OrderValidator(lot_size=10)
        # 100 是 10 的整数倍，应通过
        result = cb_validator.validate_quantity(100)
        assert result.is_valid is True

    def test_stock_lot_size_default(self):
        """测试：默认 lot_size=100"""
        validator = OrderValidator()
        # 10 不是 100 的整数倍
        result = validator.validate_quantity(10)
        assert result.is_valid is False
        assert result.reject_reason == RejectReason.NOT_ROUND_LOT


class TestPriceValidation:
    """价格校验测试"""

    def setup_method(self):
        self.validator = OrderValidator()

    def test_valid_price(self):
        """测试：有效价格"""
        result = self.validator.validate_price(
            symbol="000001.SZ",
            price=Decimal("10.50"),
            prev_close=Decimal("10.00"),
        )
        assert result.is_valid is True

    def test_price_exceeds_limit_up(self):
        """测试：价格超过涨停"""
        result = self.validator.validate_price(
            symbol="000001.SZ",
            price=Decimal("11.50"),
            prev_close=Decimal("10.00"),
        )
        assert result.is_valid is False
        assert result.reject_reason == RejectReason.PRICE_EXCEEDS_LIMIT
        assert "超过涨停价" in result.reject_detail

    def test_price_below_limit_down(self):
        """测试：价格低于跌停"""
        result = self.validator.validate_price(
            symbol="000001.SZ",
            price=Decimal("8.50"),
            prev_close=Decimal("10.00"),
        )
        assert result.is_valid is False
        assert result.reject_reason == RejectReason.PRICE_EXCEEDS_LIMIT
        assert "低于跌停价" in result.reject_detail

    def test_st_stock_price_limit(self):
        """测试：ST股票5%限制"""
        result = self.validator.validate_price(
            symbol="000001.SZ",
            price=Decimal("10.60"),  # 超过5%
            prev_close=Decimal("10.00"),
            is_st=True,
        )
        assert result.is_valid is False


class TestHaltStatusValidation:
    """停牌/涨跌停校验测试"""

    def setup_method(self):
        self.validator = OrderValidator()

    def test_normal_trading(self):
        """测试：正常交易"""
        result = self.validator.validate_halt_status(
            symbol="000001.SZ",
            side=OrderSide.BUY,
            current_price=Decimal("10.50"),
            prev_close=Decimal("10.00"),
        )
        assert result.is_valid is True

    def test_halted_stock(self):
        """测试：停牌股票"""
        result = self.validator.validate_halt_status(
            symbol="000001.SZ",
            side=OrderSide.BUY,
            current_price=None,
            prev_close=Decimal("10.00"),
            is_halted=True,
        )
        assert result.is_valid is False
        assert result.reject_reason == RejectReason.STOCK_HALTED

    def test_limit_up_no_buy(self):
        """测试：涨停无法买入"""
        result = self.validator.validate_halt_status(
            symbol="000001.SZ",
            side=OrderSide.BUY,
            current_price=Decimal("11.00"),  # 涨停价
            prev_close=Decimal("10.00"),
        )
        assert result.is_valid is False
        assert result.reject_reason == RejectReason.LIMIT_UP_NO_BUY

    def test_limit_up_can_sell(self):
        """测试：涨停可以卖出"""
        result = self.validator.validate_halt_status(
            symbol="000001.SZ",
            side=OrderSide.SELL,
            current_price=Decimal("11.00"),  # 涨停价
            prev_close=Decimal("10.00"),
        )
        assert result.is_valid is True

    def test_limit_down_no_sell(self):
        """测试：跌停无法卖出"""
        result = self.validator.validate_halt_status(
            symbol="000001.SZ",
            side=OrderSide.SELL,
            current_price=Decimal("9.00"),  # 跌停价
            prev_close=Decimal("10.00"),
        )
        assert result.is_valid is False
        assert result.reject_reason == RejectReason.LIMIT_DOWN_NO_SELL

    def test_limit_down_can_buy(self):
        """测试：跌停可以买入"""
        result = self.validator.validate_halt_status(
            symbol="000001.SZ",
            side=OrderSide.BUY,
            current_price=Decimal("9.00"),  # 跌停价
            prev_close=Decimal("10.00"),
        )
        assert result.is_valid is True


class TestSellPositionValidation:
    """卖出持仓校验测试"""

    def setup_method(self):
        self.validator = OrderValidator()

    def test_sufficient_position(self):
        """测试：持仓充足"""
        position = Position(symbol="000001.SZ", total_qty=1000)
        result = self.validator.validate_sell_position(position, 500)
        assert result.is_valid is True

    def test_insufficient_position(self):
        """测试：持仓不足"""
        position = Position(symbol="000001.SZ", total_qty=300)
        result = self.validator.validate_sell_position(position, 500)
        assert result.is_valid is False
        assert result.reject_reason == RejectReason.INSUFFICIENT_POSITION

    def test_t_plus_1_restriction(self):
        """测试：T+1限制"""
        position = Position(
            symbol="000001.SZ",
            total_qty=1000,
            today_bought_qty=800,  # 今日买入不可卖
        )
        # 可卖 = 1000 - 800 = 200
        result = self.validator.validate_sell_position(position, 500)
        assert result.is_valid is False
        assert result.reject_reason == RejectReason.T_PLUS_1_RESTRICTION
        assert "今日买入" in result.reject_detail


class TestBuyFundsValidation:
    """买入资金校验测试"""

    def setup_method(self):
        self.validator = OrderValidator()

    def test_sufficient_funds(self):
        """测试：资金充足"""
        result = self.validator.validate_buy_funds(
            available_cash=Decimal("100000"),
            order_price=Decimal("10.00"),
            order_qty=1000,
        )
        assert result.is_valid is True

    def test_insufficient_funds(self):
        """测试：资金不足"""
        result = self.validator.validate_buy_funds(
            available_cash=Decimal("5000"),
            order_price=Decimal("10.00"),
            order_qty=1000,  # 需要10000+佣金
        )
        assert result.is_valid is False
        assert result.reject_reason == RejectReason.INSUFFICIENT_FUNDS

    def test_minimum_commission(self):
        """测试：最低佣金5元"""
        result = self.validator.validate_buy_funds(
            available_cash=Decimal("105"),
            order_price=Decimal("1.00"),
            order_qty=100,  # 100元 + 5元佣金 = 105元
        )
        assert result.is_valid is True


class TestComprehensiveValidation:
    """综合校验测试"""

    def setup_method(self):
        self.validator = OrderValidator()

    def test_valid_buy_order(self):
        """测试：有效买单"""
        result = self.validator.validate_order(
            order_id="ORD001",
            symbol="000001.SZ",
            side=OrderSide.BUY,
            price=Decimal("10.50"),
            quantity=1000,
            check_time=datetime(2024, 1, 2, 10, 0, 0),
            prev_close=Decimal("10.00"),
            current_price=Decimal("10.50"),
            available_cash=Decimal("100000"),
        )
        assert result.is_valid is True

    def test_valid_sell_order(self):
        """测试：有效卖单"""
        position = Position(symbol="000001.SZ", total_qty=2000)

        result = self.validator.validate_order(
            order_id="ORD002",
            symbol="000001.SZ",
            side=OrderSide.SELL,
            price=Decimal("10.50"),
            quantity=1000,
            check_time=datetime(2024, 1, 2, 10, 0, 0),
            prev_close=Decimal("10.00"),
            current_price=Decimal("10.50"),
            position=position,
        )
        assert result.is_valid is True

    def test_reject_logged(self):
        """测试：拒单被记录"""
        self.validator.clear_logs()

        result = self.validator.validate_order(
            order_id="ORD003",
            symbol="000001.SZ",
            side=OrderSide.BUY,
            price=Decimal("12.00"),  # 超过涨停
            quantity=1000,
            check_time=datetime(2024, 1, 2, 10, 0, 0),
            prev_close=Decimal("10.00"),
        )

        assert result.is_valid is False
        logs = self.validator.get_reject_logs()
        assert len(logs) == 1
        assert logs[0].order_id == "ORD003"
        assert logs[0].reason == RejectReason.PRICE_EXCEEDS_LIMIT

    def test_first_failure_stops_validation(self):
        """测试：首个失败即停止校验"""
        self.validator.clear_logs()

        # 非交易日 + 无效数量 + 价格超限
        result = self.validator.validate_order(
            order_id="ORD004",
            symbol="000001.SZ",
            side=OrderSide.BUY,
            price=Decimal("12.00"),
            quantity=0,  # 无效数量
            check_time=datetime(2024, 1, 1, 10, 0, 0),  # 非交易日
            prev_close=Decimal("10.00"),
        )

        assert result.is_valid is False
        # 只记录第一个失败原因（非交易日）
        assert result.reject_reason == RejectReason.NON_TRADING_DAY


class TestRejectLogs:
    """拒单日志测试"""

    def setup_method(self):
        self.validator = OrderValidator()

    def test_log_accumulation(self):
        """测试：日志累积"""
        self.validator.clear_logs()

        # 生成多个拒单
        for i in range(3):
            self.validator.validate_order(
                order_id=f"ORD{i}",
                symbol="000001.SZ",
                side=OrderSide.BUY,
                price=Decimal("12.00"),
                quantity=1000,
                check_time=datetime(2024, 1, 2, 10, 0, 0),
                prev_close=Decimal("10.00"),
            )

        logs = self.validator.get_reject_logs()
        assert len(logs) == 3

    def test_reject_summary(self):
        """测试：拒单统计摘要"""
        self.validator.clear_logs()

        # 价格超限
        self.validator.validate_order(
            order_id="ORD1",
            symbol="000001.SZ",
            side=OrderSide.BUY,
            price=Decimal("12.00"),
            quantity=1000,
            check_time=datetime(2024, 1, 2, 10, 0, 0),
            prev_close=Decimal("10.00"),
        )

        # 非交易日
        self.validator.validate_order(
            order_id="ORD2",
            symbol="000001.SZ",
            side=OrderSide.BUY,
            price=Decimal("10.50"),
            quantity=1000,
            check_time=datetime(2024, 1, 1, 10, 0, 0),
            prev_close=Decimal("10.00"),
        )

        summary = self.validator.get_reject_summary()
        assert summary["price_exceeds_limit"] == 1
        assert summary["non_trading_day"] == 1

    def test_export_logs(self):
        """测试：导出日志"""
        self.validator.clear_logs()

        self.validator.validate_order(
            order_id="ORD1",
            symbol="000001.SZ",
            side=OrderSide.BUY,
            price=Decimal("12.00"),
            quantity=1000,
            check_time=datetime(2024, 1, 2, 10, 0, 0),
            prev_close=Decimal("10.00"),
        )

        exported = self.validator.export_logs()
        assert len(exported) == 1
        assert exported[0]["order_id"] == "ORD1"
        assert exported[0]["reason"] == "price_exceeds_limit"
        assert "timestamp" in exported[0]

    def test_clear_logs(self):
        """测试：清空日志"""
        self.validator.validate_order(
            order_id="ORD1",
            symbol="000001.SZ",
            side=OrderSide.BUY,
            price=Decimal("12.00"),
            quantity=1000,
            check_time=datetime(2024, 1, 2, 10, 0, 0),
            prev_close=Decimal("10.00"),
        )

        assert len(self.validator.get_reject_logs()) > 0

        self.validator.clear_logs()
        assert len(self.validator.get_reject_logs()) == 0
