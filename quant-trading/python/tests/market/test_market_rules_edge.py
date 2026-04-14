"""市场规则边界条件测试

覆盖 rules_cn_equity.py / halt_checker.py 中未测试的分支。
"""

from datetime import datetime
from decimal import Decimal

import pytest

from aipha.domain.models import Bar, Order, OrderType, Side
from aipha.market.base import PrecheckDecision
from aipha.market.halt_checker import BoardType, HaltChecker
from aipha.market.rules_cn_equity import MarketRulesCNEquity, RejectReason


# ── helpers ──────────────────────────────────────────────────

_T = datetime(2024, 6, 3, 10, 0, 0)


def _order(
    symbol="000001.SZ", side=Side.BUY, qty=100,
    order_type=OrderType.MARKET, limit_price=None,
) -> Order:
    return Order(
        order_id="test",
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=order_type,
        t_submit=_T,
        limit_price=limit_price,
    )


def _bar(
    close=10.0, prev_close=10.0, is_suspended=False,
    is_st=False, is_limit_up=None, is_limit_down=None,
) -> Bar:
    return Bar(
        symbol="000001.SZ",
        start_ts=_T,
        end_ts=_T,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100000,
        prev_close=prev_close,
        is_suspended=is_suspended,
        is_st=is_st,
        is_limit_up=is_limit_up,
        is_limit_down=is_limit_down,
    )


# ══════════════════════════════════════════════════════════════
# 1. MarketRulesCNEquity 边界
# ══════════════════════════════════════════════════════════════


class TestPrevCloseZeroSkipsCheck:
    """prev_close=0 时价格校验被跳过"""

    def test_limit_order_with_prev_close_zero_passes(self):
        """prev_close=0 → 条件为 False → 跳过价格范围校验 → 允许任意限价"""
        rules = MarketRulesCNEquity()
        o = _order(
            order_type=OrderType.LIMIT, limit_price=999.99,
        )
        b = _bar(prev_close=0.0)
        result = rules.precheck_order(order=o, bar=b)
        assert result.allowed is True

    def test_limit_order_with_prev_close_none_passes(self):
        """prev_close=None → 也跳过价格校验"""
        rules = MarketRulesCNEquity()
        o = _order(order_type=OrderType.LIMIT, limit_price=1.0)
        b = _bar(prev_close=None)
        result = rules.precheck_order(order=o, bar=b)
        assert result.allowed is True


class TestStarBoardLimitPrice:
    """科创板(688xxx) LIMIT 单价格范围 ±20%"""

    def test_star_board_within_range(self):
        """688xxx + 限价在 ±20% 内 → 允许"""
        rules = MarketRulesCNEquity()
        o = _order(
            symbol="688001.SH", order_type=OrderType.LIMIT,
            limit_price=11.90, qty=200,  # +19%, qty=200 (科创板最小单位)
        )
        b = _bar(prev_close=10.0)
        result = rules.precheck_order(order=o, bar=b)
        assert result.allowed is True

    def test_star_board_exceeds_limit_up(self):
        """688xxx + 限价超过 20% 涨停 → 拒绝"""
        rules = MarketRulesCNEquity()
        o = _order(
            symbol="688001.SH", order_type=OrderType.LIMIT,
            limit_price=12.50, qty=200,  # +25% > ±20%, qty=200 (科创板最小单位)
        )
        b = _bar(prev_close=10.0)
        result = rules.precheck_order(order=o, bar=b)
        assert result.allowed is False
        assert result.reason == RejectReason.PRICE_OUT_OF_RANGE


class TestBseBoardLimitPrice:
    """北交所(8xxxxx/43xxxx/87xxxx) LIMIT 单价格范围 ±30%"""

    def test_bse_board_within_range(self):
        """830001 + 限价在 ±30% 内 → 允许"""
        rules = MarketRulesCNEquity()
        o = _order(
            symbol="830001.BJ", order_type=OrderType.LIMIT,
            limit_price=12.90,  # +29%
        )
        b = _bar(prev_close=10.0)
        result = rules.precheck_order(order=o, bar=b)
        assert result.allowed is True

    def test_bse_board_exceeds_limit_down(self):
        """430001 + 限价低于 30% 跌停 → 拒绝"""
        rules = MarketRulesCNEquity()
        o = _order(
            symbol="430001.BJ", side=Side.SELL,
            order_type=OrderType.LIMIT, limit_price=6.50,  # -35%
        )
        b = _bar(prev_close=10.0)
        result = rules.precheck_order(order=o, bar=b)
        assert result.allowed is False
        assert result.reason == RejectReason.PRICE_OUT_OF_RANGE


class TestStLimitPrice:
    """ST + LIMIT 单 ±5% 窄范围"""

    def test_st_within_5_pct(self):
        """is_st=True + 限价在 ±5% → 允许"""
        rules = MarketRulesCNEquity()
        o = _order(
            symbol="000001.SZ", order_type=OrderType.LIMIT,
            limit_price=10.45,  # +4.5%
        )
        b = _bar(prev_close=10.0, is_st=True)
        result = rules.precheck_order(order=o, bar=b)
        assert result.allowed is True

    def test_st_exceeds_5_pct(self):
        """is_st=True + 限价超 5% → 拒绝"""
        rules = MarketRulesCNEquity()
        o = _order(
            symbol="000001.SZ", order_type=OrderType.LIMIT,
            limit_price=10.60,  # +6% > ±5%
        )
        b = _bar(prev_close=10.0, is_st=True)
        result = rules.precheck_order(order=o, bar=b)
        assert result.allowed is False


# ══════════════════════════════════════════════════════════════
# 2. HaltChecker 边界
# ══════════════════════════════════════════════════════════════


class TestGetBoardTypeEdge:
    """get_board_type 边界分支"""

    def test_87xxxx_is_bse(self):
        """87xxxx 前缀 → BSE"""
        checker = HaltChecker()
        assert checker.get_board_type("870001.BJ") == BoardType.BSE

    def test_43xxxx_is_bse(self):
        """43xxxx 前缀 → BSE"""
        checker = HaltChecker()
        assert checker.get_board_type("430001.BJ") == BoardType.BSE

    def test_688xxx_is_star(self):
        """688xxx → STAR"""
        checker = HaltChecker()
        assert checker.get_board_type("688001.SH") == BoardType.STAR

    def test_st_overrides_board(self):
        """is_st=True 无论代码 → ST"""
        checker = HaltChecker()
        assert checker.get_board_type("688001.SH", is_st=True) == BoardType.ST


class TestCalculateLimitPricesEdge:
    """calculate_limit_prices 边界"""

    def test_new_listing_huge_range(self):
        """NEW_LISTING → ±1000% → 跌停价接近 0"""
        checker = HaltChecker()
        up, down = checker.calculate_limit_prices(
            Decimal("10.00"), BoardType.NEW_LISTING,
        )
        # 涨停 = 10*(1+10) = 110.00, 跌停 = 10*(1-10) = -90 → quantize to 0.01
        # 具体取决于实现是否 clip at 0
        assert up > Decimal("100.0")

    def test_star_board_limits(self):
        """STAR ±20% 精确计算"""
        checker = HaltChecker()
        up, down = checker.calculate_limit_prices(
            Decimal("10.00"), BoardType.STAR,
        )
        assert up == Decimal("12.00")
        assert down == Decimal("8.00")

    def test_st_narrow_range(self):
        """ST ±5%"""
        checker = HaltChecker()
        up, down = checker.calculate_limit_prices(
            Decimal("10.00"), BoardType.ST,
        )
        assert up == Decimal("10.50")
        assert down == Decimal("9.50")


class TestGetMarketStatusPrevCloseNone:
    """get_market_status 中 prev_close=None 路径"""

    def test_prev_close_none_normal_status(self):
        """prev_close=None → limit_up/limit_down 保持 None"""
        checker = HaltChecker()
        status = checker.get_market_status(
            symbol="000001.SZ",
            current_price=Decimal("10.00"),
            prev_close=None,
            is_suspended=False,
        )
        # 不抛异常，返回某种状态
        assert status is not None
        assert status.limit_up_price is None
        assert status.limit_down_price is None
