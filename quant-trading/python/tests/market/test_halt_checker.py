"""
HaltChecker 单元测试 - 涨跌停与停牌检测

测试覆盖：
1. 板块类型识别
2. 涨跌停价格计算
3. 涨跌停状态判断
4. 停牌状态检测
5. 订单价格校验
"""

from datetime import datetime
from decimal import Decimal

import pytest

from aipha.market.halt_checker import (
    BoardType,
    HaltChecker,
    HaltStatus,
    LimitStatus,
    MarketStatus,
)


class TestBoardTypeDetection:
    """板块类型识别测试"""

    def setup_method(self):
        """每个测试前初始化"""
        self.checker = HaltChecker()

    def test_main_board_sh(self):
        """测试：主板（上海）"""
        assert self.checker.get_board_type("600000.SH") == BoardType.MAIN
        assert self.checker.get_board_type("601318.SH") == BoardType.MAIN

    def test_main_board_sz(self):
        """测试：主板（深圳）"""
        assert self.checker.get_board_type("000001.SZ") == BoardType.MAIN
        assert self.checker.get_board_type("000002.SZ") == BoardType.MAIN

    def test_gem_board(self):
        """测试：创业板"""
        assert self.checker.get_board_type("300001.SZ") == BoardType.GEM
        assert self.checker.get_board_type("301001.SZ") == BoardType.GEM

    def test_star_board(self):
        """测试：科创板"""
        assert self.checker.get_board_type("688001.SH") == BoardType.STAR
        assert self.checker.get_board_type("688688.SH") == BoardType.STAR

    def test_bse_board(self):
        """测试：北交所"""
        assert self.checker.get_board_type("830001.BJ") == BoardType.BSE
        assert self.checker.get_board_type("430001.BJ") == BoardType.BSE

    def test_st_stock(self):
        """测试：ST股票（覆盖板块类型）"""
        # ST股票的涨跌幅限制为5%，优先于板块限制
        assert self.checker.get_board_type("000001.SZ", is_st=True) == BoardType.ST
        assert self.checker.get_board_type("300001.SZ", is_st=True) == BoardType.ST


class TestLimitPriceCalculation:
    """涨跌停价格计算测试"""

    def setup_method(self):
        self.checker = HaltChecker()

    def test_main_board_limit_10_percent(self):
        """测试：主板10%涨跌幅限制"""
        prev_close = Decimal("10.00")
        limit_up, limit_down = self.checker.calculate_limit_prices(
            prev_close, BoardType.MAIN
        )

        assert limit_up == Decimal("11.00")
        assert limit_down == Decimal("9.00")

    def test_gem_board_limit_20_percent(self):
        """测试：创业板20%涨跌幅限制"""
        prev_close = Decimal("10.00")
        limit_up, limit_down = self.checker.calculate_limit_prices(
            prev_close, BoardType.GEM
        )

        assert limit_up == Decimal("12.00")
        assert limit_down == Decimal("8.00")

    def test_star_board_limit_20_percent(self):
        """测试：科创板20%涨跌幅限制"""
        prev_close = Decimal("10.00")
        limit_up, limit_down = self.checker.calculate_limit_prices(
            prev_close, BoardType.STAR
        )

        assert limit_up == Decimal("12.00")
        assert limit_down == Decimal("8.00")

    def test_st_stock_limit_5_percent(self):
        """测试：ST股票5%涨跌幅限制"""
        prev_close = Decimal("10.00")
        limit_up, limit_down = self.checker.calculate_limit_prices(
            prev_close, BoardType.ST
        )

        assert limit_up == Decimal("10.50")
        assert limit_down == Decimal("9.50")

    def test_bse_limit_30_percent(self):
        """测试：北交所30%涨跌幅限制"""
        prev_close = Decimal("10.00")
        limit_up, limit_down = self.checker.calculate_limit_prices(
            prev_close, BoardType.BSE
        )

        assert limit_up == Decimal("13.00")
        assert limit_down == Decimal("7.00")

    def test_limit_price_rounding(self):
        """测试：涨跌停价格四舍五入到分"""
        prev_close = Decimal("10.33")
        limit_up, limit_down = self.checker.calculate_limit_prices(
            prev_close, BoardType.MAIN
        )

        # 10.33 * 1.10 = 11.363 -> 11.36 (ROUND_DOWN)
        assert limit_up == Decimal("11.36")
        # 10.33 * 0.90 = 9.297 -> 9.30 (ROUND_UP)
        assert limit_down == Decimal("9.30")


class TestLimitStatusCheck:
    """涨跌停状态判断测试"""

    def setup_method(self):
        self.checker = HaltChecker()

    def test_normal_status(self):
        """测试：正常交易状态"""
        status = self.checker.check_limit_status(
            current_price=Decimal("10.50"),
            prev_close=Decimal("10.00"),
            board_type=BoardType.MAIN,
        )
        assert status == LimitStatus.NORMAL

    def test_limit_up_status(self):
        """测试：涨停状态"""
        status = self.checker.check_limit_status(
            current_price=Decimal("11.00"),
            prev_close=Decimal("10.00"),
            board_type=BoardType.MAIN,
        )
        assert status == LimitStatus.LIMIT_UP

    def test_limit_down_status(self):
        """测试：跌停状态"""
        status = self.checker.check_limit_status(
            current_price=Decimal("9.00"),
            prev_close=Decimal("10.00"),
            board_type=BoardType.MAIN,
        )
        assert status == LimitStatus.LIMIT_DOWN

    def test_approaching_limit_up(self):
        """测试：接近涨停（<1%）"""
        # 涨停价11.00，当前10.95（距离0.5%）
        status = self.checker.check_limit_status(
            current_price=Decimal("10.95"),
            prev_close=Decimal("10.00"),
            board_type=BoardType.MAIN,
        )
        assert status == LimitStatus.APPROACHING_UP

    def test_approaching_limit_down(self):
        """测试：接近跌停（<1%）"""
        # 跌停价9.00，当前9.05（距离0.5%）
        status = self.checker.check_limit_status(
            current_price=Decimal("9.05"),
            prev_close=Decimal("10.00"),
            board_type=BoardType.MAIN,
        )
        assert status == LimitStatus.APPROACHING_DOWN


class TestHaltStatusCheck:
    """停牌状态检测测试"""

    def setup_method(self):
        self.checker = HaltChecker()

    def test_trading_status(self):
        """测试：正常交易"""
        status = self.checker.check_halt_status()
        assert status == HaltStatus.TRADING

    def test_halted_status(self):
        """测试：停牌"""
        status = self.checker.check_halt_status(is_halted=True)
        assert status == HaltStatus.HALTED

    def test_suspended_status(self):
        """测试：临时停牌"""
        status = self.checker.check_halt_status(is_suspended=True)
        assert status == HaltStatus.SUSPENDED

    def test_delisting_status(self):
        """测试：退市整理"""
        status = self.checker.check_halt_status(is_delisting=True)
        assert status == HaltStatus.DELISTING

    def test_delisting_priority(self):
        """测试：退市优先级最高"""
        status = self.checker.check_halt_status(
            is_halted=True, is_suspended=True, is_delisting=True
        )
        assert status == HaltStatus.DELISTING


class TestMarketStatus:
    """MarketStatus 综合测试"""

    def setup_method(self):
        self.checker = HaltChecker()

    def test_get_market_status_normal(self):
        """测试：获取正常交易状态"""
        status = self.checker.get_market_status(
            symbol="000001.SZ",
            current_price=Decimal("10.50"),
            prev_close=Decimal("10.00"),
        )

        assert status.symbol == "000001.SZ"
        assert status.halt_status == HaltStatus.TRADING
        assert status.limit_status == LimitStatus.NORMAL
        assert status.limit_up_price == Decimal("11.00")
        assert status.limit_down_price == Decimal("9.00")
        assert status.can_buy is True
        assert status.can_sell is True

    def test_get_market_status_limit_up(self):
        """测试：涨停状态不可买入"""
        status = self.checker.get_market_status(
            symbol="000001.SZ",
            current_price=Decimal("11.00"),
            prev_close=Decimal("10.00"),
        )

        assert status.limit_status == LimitStatus.LIMIT_UP
        assert status.can_buy is False  # 涨停不可买
        assert status.can_sell is True

    def test_get_market_status_limit_down(self):
        """测试：跌停状态不可卖出"""
        status = self.checker.get_market_status(
            symbol="000001.SZ",
            current_price=Decimal("9.00"),
            prev_close=Decimal("10.00"),
        )

        assert status.limit_status == LimitStatus.LIMIT_DOWN
        assert status.can_buy is True
        assert status.can_sell is False  # 跌停不可卖

    def test_get_market_status_halted(self):
        """测试：停牌状态"""
        status = self.checker.get_market_status(
            symbol="000001.SZ",
            current_price=None,
            prev_close=Decimal("10.00"),
            is_halted=True,
            halt_reason="重大事项停牌",
        )

        assert status.halt_status == HaltStatus.HALTED
        assert status.halt_reason == "重大事项停牌"
        assert status.can_buy is False
        assert status.can_sell is False

    def test_get_market_status_st_stock(self):
        """测试：ST股票"""
        status = self.checker.get_market_status(
            symbol="000001.SZ",
            current_price=Decimal("10.00"),
            prev_close=Decimal("10.00"),
            is_st=True,
        )

        assert status.board_type == BoardType.ST
        assert status.is_st is True
        assert status.limit_up_price == Decimal("10.50")  # 5%限制
        assert status.limit_down_price == Decimal("9.50")


class TestOrderValidation:
    """订单价格校验测试"""

    def setup_method(self):
        self.checker = HaltChecker()

    def test_buy_order_valid_price(self):
        """测试：买单价格有效"""
        can_place, reason = self.checker.can_place_buy_order(
            symbol="000001.SZ",
            order_price=Decimal("10.50"),
            prev_close=Decimal("10.00"),
        )

        assert can_place is True
        assert reason == ""

    def test_buy_order_exceeds_limit_up(self):
        """测试：买单价格超过涨停价"""
        can_place, reason = self.checker.can_place_buy_order(
            symbol="000001.SZ",
            order_price=Decimal("11.50"),
            prev_close=Decimal("10.00"),
        )

        assert can_place is False
        assert "超过涨停价" in reason

    def test_buy_order_below_limit_down(self):
        """测试：买单价格低于跌停价"""
        can_place, reason = self.checker.can_place_buy_order(
            symbol="000001.SZ",
            order_price=Decimal("8.50"),
            prev_close=Decimal("10.00"),
        )

        assert can_place is False
        assert "低于跌停价" in reason

    def test_buy_order_halted_stock(self):
        """测试：买单 - 停牌股票"""
        can_place, reason = self.checker.can_place_buy_order(
            symbol="000001.SZ",
            order_price=Decimal("10.50"),
            prev_close=Decimal("10.00"),
            is_halted=True,
        )

        assert can_place is False
        assert "停牌" in reason

    def test_sell_order_valid_price(self):
        """测试：卖单价格有效"""
        can_place, reason = self.checker.can_place_sell_order(
            symbol="000001.SZ",
            order_price=Decimal("10.50"),
            prev_close=Decimal("10.00"),
        )

        assert can_place is True
        assert reason == ""

    def test_sell_order_exceeds_limit_up(self):
        """测试：卖单价格超过涨停价"""
        can_place, reason = self.checker.can_place_sell_order(
            symbol="000001.SZ",
            order_price=Decimal("11.50"),
            prev_close=Decimal("10.00"),
        )

        assert can_place is False
        assert "超过涨停价" in reason

    def test_sell_order_st_stock(self):
        """测试：卖单 - ST股票价格限制"""
        can_place, reason = self.checker.can_place_sell_order(
            symbol="000001.SZ",
            order_price=Decimal("10.60"),  # 超过5%
            prev_close=Decimal("10.00"),
            is_st=True,
        )

        assert can_place is False
        assert "超过涨停价" in reason

    def test_order_at_limit_price(self):
        """测试：以涨跌停价下单（边界值）"""
        # 涨停价下单
        can_place, _ = self.checker.can_place_buy_order(
            symbol="000001.SZ",
            order_price=Decimal("11.00"),  # 刚好涨停价
            prev_close=Decimal("10.00"),
        )
        assert can_place is True

        # 跌停价下单
        can_place, _ = self.checker.can_place_sell_order(
            symbol="000001.SZ",
            order_price=Decimal("9.00"),  # 刚好跌停价
            prev_close=Decimal("10.00"),
        )
        assert can_place is True
