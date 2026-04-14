from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aipha.domain.models import Bar, Order, OrderType, Side
from aipha.market.rules_cn_equity import MarketRulesCNEquity, RejectReason


def _ts(y: int = 2020, m: int = 1, d: int = 2, hh: int = 9, mm: int = 31) -> datetime:
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


def _order(
    side: Side = Side.BUY,
    qty: int = 100,
    order_type: OrderType = OrderType.MARKET,
    limit_price: float | None = None,
    symbol: str = "000001.SZ",
) -> Order:
    return Order(
        order_id="o1",
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=order_type,
        t_submit=_ts(),
        limit_price=limit_price,
    )


def _bar(
    close: float = 10.0,
    is_suspended: bool = False,
    is_limit_up: bool | None = None,
    is_limit_down: bool | None = None,
    prev_close: float | None = None,
    volume: float = 10000,
    symbol: str = "000001.SZ",
) -> Bar:
    return Bar(
        symbol=symbol,
        start_ts=_ts(hh=9, mm=32),
        end_ts=_ts(hh=9, mm=33),
        open=close,
        high=close * 1.02,
        low=close * 0.98,
        close=close,
        volume=volume,
        is_suspended=is_suspended,
        is_limit_up=is_limit_up,
        is_limit_down=is_limit_down,
        prev_close=prev_close,
    )


# =========================================================================
# Suspension checks
# =========================================================================

class TestSuspended:
    def test_reject_buy_suspended(self) -> None:
        rules = MarketRulesCNEquity()
        decision = rules.precheck_order(order=_order(), bar=_bar(is_suspended=True))
        assert not decision.allowed
        assert decision.reason == RejectReason.SUSPENDED

    def test_reject_sell_suspended(self) -> None:
        rules = MarketRulesCNEquity()
        decision = rules.precheck_order(
            order=_order(side=Side.SELL), bar=_bar(is_suspended=True),
        )
        assert not decision.allowed
        assert decision.reason == RejectReason.SUSPENDED


# =========================================================================
# Limit up / down
# =========================================================================

class TestLimitUpDown:
    def test_reject_buy_limit_up(self) -> None:
        rules = MarketRulesCNEquity()
        decision = rules.precheck_order(order=_order(), bar=_bar(is_limit_up=True))
        assert not decision.allowed
        assert decision.reason == RejectReason.LIMIT_UP

    def test_allow_sell_limit_up(self) -> None:
        """涨停时仍可卖出。"""
        rules = MarketRulesCNEquity()
        decision = rules.precheck_order(
            order=_order(side=Side.SELL), bar=_bar(is_limit_up=True),
        )
        assert decision.allowed

    def test_reject_sell_limit_down(self) -> None:
        rules = MarketRulesCNEquity()
        decision = rules.precheck_order(
            order=_order(side=Side.SELL), bar=_bar(is_limit_down=True),
        )
        assert not decision.allowed
        assert decision.reason == RejectReason.LIMIT_DOWN

    def test_allow_buy_limit_down(self) -> None:
        """跌停时仍可买入。"""
        rules = MarketRulesCNEquity()
        decision = rules.precheck_order(
            order=_order(side=Side.BUY), bar=_bar(is_limit_down=True),
        )
        assert decision.allowed


# =========================================================================
# Lot size
# =========================================================================

class TestLotSize:
    def test_reject_non_multiple(self) -> None:
        rules = MarketRulesCNEquity(lot_size=100)
        decision = rules.precheck_order(order=_order(qty=150), bar=_bar())
        assert not decision.allowed
        assert decision.reason == RejectReason.LOT_SIZE_ROUNDING

    def test_allow_exact_multiple(self) -> None:
        rules = MarketRulesCNEquity(lot_size=100)
        decision = rules.precheck_order(order=_order(qty=300), bar=_bar())
        assert decision.allowed

    def test_custom_lot_size(self) -> None:
        """可转债手数 = 10 张。"""
        rules = MarketRulesCNEquity(lot_size=10)
        assert rules.precheck_order(order=_order(qty=10), bar=_bar()).allowed
        assert not rules.precheck_order(order=_order(qty=15), bar=_bar()).allowed


# =========================================================================
# Max order qty
# =========================================================================

class TestMaxOrderQty:
    def test_reject_exceeds_max(self) -> None:
        rules = MarketRulesCNEquity(lot_size=100)
        decision = rules.precheck_order(
            order=_order(qty=2_000_000), bar=_bar(),
        )
        assert not decision.allowed
        assert decision.reason == RejectReason.EXCEEDS_MAX_ORDER_QTY

    def test_allow_at_max(self) -> None:
        rules = MarketRulesCNEquity(lot_size=100, max_order_qty=1_000_000)
        decision = rules.precheck_order(
            order=_order(qty=1_000_000), bar=_bar(),
        )
        assert decision.allowed


# =========================================================================
# Limit order price range
# =========================================================================

class TestLimitOrderPrice:
    def test_reject_price_above_limit_up(self) -> None:
        """限价单价格超出涨停价被拒绝。"""
        rules = MarketRulesCNEquity()
        # 主板涨跌幅 ±10%，prev_close=10 → 涨停=11
        decision = rules.precheck_order(
            order=_order(order_type=OrderType.LIMIT, limit_price=11.50),
            bar=_bar(prev_close=10.0),
        )
        assert not decision.allowed
        assert decision.reason == RejectReason.PRICE_OUT_OF_RANGE

    def test_reject_price_below_limit_down(self) -> None:
        rules = MarketRulesCNEquity()
        decision = rules.precheck_order(
            order=_order(order_type=OrderType.LIMIT, limit_price=8.0, side=Side.SELL),
            bar=_bar(prev_close=10.0),
        )
        assert not decision.allowed
        assert decision.reason == RejectReason.PRICE_OUT_OF_RANGE

    def test_allow_within_range(self) -> None:
        rules = MarketRulesCNEquity()
        decision = rules.precheck_order(
            order=_order(order_type=OrderType.LIMIT, limit_price=10.5),
            bar=_bar(prev_close=10.0),
        )
        assert decision.allowed

    def test_limit_no_price_raises(self) -> None:
        rules = MarketRulesCNEquity()
        with pytest.raises(ValueError, match="limit_price"):
            rules.precheck_order(
                order=_order(order_type=OrderType.LIMIT, limit_price=None),
                bar=_bar(prev_close=10.0),
            )


# =========================================================================
# Normal orders (happy path)
# =========================================================================

class TestNormalPath:
    def test_allow_normal_buy(self) -> None:
        rules = MarketRulesCNEquity()
        decision = rules.precheck_order(order=_order(), bar=_bar())
        assert decision.allowed
        assert decision.reason is None

    def test_allow_normal_sell(self) -> None:
        rules = MarketRulesCNEquity()
        decision = rules.precheck_order(order=_order(side=Side.SELL), bar=_bar())
        assert decision.allowed

    def test_no_prev_close_warns_and_marks_unverified(self) -> None:
        """prev_close 为 None 时放行但标记为未校验（§13.3）。"""
        rules = MarketRulesCNEquity()
        decision = rules.precheck_order(
            order=_order(order_type=OrderType.LIMIT, limit_price=999.0),
            bar=_bar(prev_close=None),
        )
        assert decision.allowed
        assert decision.warning is not None
        assert "prev_close 缺失" in decision.warning
        assert "未校验" in decision.warning
