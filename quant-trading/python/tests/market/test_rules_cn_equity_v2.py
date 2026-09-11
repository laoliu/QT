"""
A 股市场规则 V2 增强功能测试

覆盖：
- lot_size 检验
- max_order_qty 超限拒绝（新增）
- 涨跌停拦截
- 停牌拦截
- 限价单价格范围校验（新增, 需 prev_close）
- 板块差异化涨跌幅（新增, 需 prev_close）
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

import pytest

from aipha.domain.models import Bar, Order, OrderType, Side
from aipha.market.rules_cn_equity import MarketRulesCNEquity, RejectReason


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _make_bar(
    symbol: str = "600000.SH",
    price: float = 10.0,
    volume: float = 1_000_000.0,
    is_suspended: bool = False,
    is_limit_up: bool | None = None,
    is_limit_down: bool | None = None,
) -> Bar:
    ts = datetime(2025, 1, 6, 15, 0, 0)
    return Bar(
        symbol=symbol,
        start_ts=ts.replace(hour=9, minute=30),
        end_ts=ts,
        open=price * 0.99,
        high=price * 1.01,
        low=price * 0.98,
        close=price,
        volume=volume,
        is_suspended=is_suspended,
        is_limit_up=is_limit_up,
        is_limit_down=is_limit_down,
    )


def _make_order(
    symbol: str = "600000.SH",
    side: Side = Side.BUY,
    qty: int = 100,
    order_type: OrderType = OrderType.MARKET,
    limit_price: float | None = None,
) -> Order:
    return Order(
        order_id="ORD-TEST",
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=order_type,
        t_submit=datetime(2025, 1, 6, 10, 0, 0),
        limit_price=limit_price,
    )


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

rules = MarketRulesCNEquity()


class TestLotSizeCheck:
    """整手检查"""

    def test_valid_lot_size(self):
        result = rules.precheck_order(order=_make_order(qty=100), bar=_make_bar())
        assert result.allowed is True

    def test_invalid_lot_size(self):
        result = rules.precheck_order(order=_make_order(qty=99), bar=_make_bar())
        assert result.allowed is False
        assert result.reason == RejectReason.LOT_SIZE_ROUNDING

    def test_large_lot(self):
        result = rules.precheck_order(order=_make_order(qty=10000), bar=_make_bar())
        assert result.allowed is True


class TestMaxOrderQty:
    """V2: 最大委托量检查（100 万股）"""

    def test_within_limit(self):
        result = rules.precheck_order(order=_make_order(qty=1_000_000), bar=_make_bar())
        assert result.allowed is True

    def test_exceeds_limit(self):
        result = rules.precheck_order(order=_make_order(qty=1_000_100), bar=_make_bar())
        assert result.allowed is False
        assert result.reason == RejectReason.EXCEEDS_MAX_ORDER_QTY

    def test_custom_max(self):
        custom = MarketRulesCNEquity(max_order_qty=500)
        result = custom.precheck_order(order=_make_order(qty=600), bar=_make_bar())
        assert result.allowed is False
        assert result.reason == RejectReason.EXCEEDS_MAX_ORDER_QTY


class TestSuspensionCheck:
    """停牌拦截"""

    def test_suspended(self):
        result = rules.precheck_order(
            order=_make_order(), bar=_make_bar(is_suspended=True)
        )
        assert result.allowed is False
        assert result.reason == RejectReason.SUSPENDED

    def test_not_suspended(self):
        result = rules.precheck_order(order=_make_order(), bar=_make_bar())
        assert result.allowed is True


class TestLimitUpDown:
    """涨跌停拦截"""

    def test_buy_at_limit_up(self):
        result = rules.precheck_order(
            order=_make_order(side=Side.BUY), bar=_make_bar(is_limit_up=True)
        )
        assert result.allowed is False
        assert result.reason == RejectReason.LIMIT_UP

    def test_sell_at_limit_down(self):
        result = rules.precheck_order(
            order=_make_order(side=Side.SELL, qty=100),
            bar=_make_bar(is_limit_down=True),
        )
        assert result.allowed is False
        assert result.reason == RejectReason.LIMIT_DOWN

    def test_buy_at_limit_down_ok(self):
        """跌停时可以买入"""
        result = rules.precheck_order(
            order=_make_order(side=Side.BUY), bar=_make_bar(is_limit_down=True)
        )
        assert result.allowed is True

    def test_sell_at_limit_up_ok(self):
        """涨停时可以卖出"""
        result = rules.precheck_order(
            order=_make_order(side=Side.SELL, qty=100),
            bar=_make_bar(is_limit_up=True),
        )
        assert result.allowed is True


class TestLimitPriceRange:
    """V2: 限价单价格范围校验（需要 bar 有 prev_close 属性）"""

    def _make_bar_with_prev_close(self, symbol="600000.SH", prev_close=10.0, is_st=False):
        """创建带 prev_close 属性的 bar"""
        ts = datetime(2025, 1, 6, 15, 0, 0)
        return Bar(
            symbol=symbol,
            start_ts=ts.replace(hour=9, minute=30),
            end_ts=ts,
            open=prev_close * 0.99,
            high=prev_close * 1.01,
            low=prev_close * 0.98,
            close=prev_close,
            volume=1_000_000.0,
            prev_close=prev_close,
            is_st=is_st,
        )

    def test_limit_price_within_range_main_board(self):
        """主板 ±10%: prev_close=10 → [9.00, 11.00]"""
        bar = self._make_bar_with_prev_close("600000.SH", 10.0)
        order = _make_order(
            symbol="600000.SH",
            order_type=OrderType.LIMIT,
            limit_price=10.5,
        )
        result = rules.precheck_order(order=order, bar=bar)
        assert result.allowed is True

    def test_limit_price_exceeds_up_main_board(self):
        """主板: 限价超过涨停价"""
        bar = self._make_bar_with_prev_close("600000.SH", 10.0)
        order = _make_order(
            symbol="600000.SH",
            order_type=OrderType.LIMIT,
            limit_price=11.50,  # 超过 +10%
        )
        result = rules.precheck_order(order=order, bar=bar)
        assert result.allowed is False
        assert result.reason == RejectReason.PRICE_OUT_OF_RANGE

    def test_limit_price_below_down_main_board(self):
        """主板: 限价低于跌停价"""
        bar = self._make_bar_with_prev_close("600000.SH", 10.0)
        order = _make_order(
            symbol="600000.SH",
            order_type=OrderType.LIMIT,
            limit_price=8.50,  # 低于 -10%
        )
        result = rules.precheck_order(order=order, bar=bar)
        assert result.allowed is False
        assert result.reason == RejectReason.PRICE_OUT_OF_RANGE

    def test_gem_board_wider_range(self):
        """创业板 ±20%: prev_close=10 → [8.00, 12.00]"""
        bar = self._make_bar_with_prev_close("300001.SZ", 10.0)
        # 在 ±20% 内
        order = _make_order(
            symbol="300001.SZ",
            order_type=OrderType.LIMIT,
            limit_price=11.5,  # 在 +20% 内
        )
        result = rules.precheck_order(order=order, bar=bar)
        assert result.allowed is True

    def test_gem_board_exceeds(self):
        """创业板超涨停"""
        bar = self._make_bar_with_prev_close("300001.SZ", 10.0)
        order = _make_order(
            symbol="300001.SZ",
            order_type=OrderType.LIMIT,
            limit_price=12.50,  # 超过 +20%
        )
        result = rules.precheck_order(order=order, bar=bar)
        assert result.allowed is False

    def test_market_order_no_price_check(self):
        """市价单不做价格范围检查"""
        bar = self._make_bar_with_prev_close("600000.SH", 10.0)
        order = _make_order(
            symbol="600000.SH",
            order_type=OrderType.MARKET,
        )
        result = rules.precheck_order(order=order, bar=bar)
        assert result.allowed is True

    def test_without_prev_close_rejects(self):
        """无 prev_close 时拒单，而不是跳过价格范围检查（§13.3）"""
        bar = _make_bar()  # 标准 bar, 没有 prev_close
        order = _make_order(
            symbol="600000.SH",
            order_type=OrderType.LIMIT,
            limit_price=999.0,  # 极端价格
        )
        result = rules.precheck_order(order=order, bar=bar)
        # 没有 prev_close 就算不出涨跌停价，放行等于盲下单
        assert result.allowed is False
        assert result.reason == RejectReason.DATA_MISSING
