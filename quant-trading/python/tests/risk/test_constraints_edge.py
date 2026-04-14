"""Edge tests for risk/constraints.py — DrawdownLimit, TurnoverLimit, ConcentrationLimit.

覆盖:
- DrawdownLimit: 正常/超限/减仓区间/peak_nav≤0/卖出放行/reduce=max (除零边界)
- TurnoverLimit: 正常/超限/单笔超限/无价格/NAV≤0/reset_daily/remaining
- ConcentrationLimit: 正常/top_n超权重/SELL清仓低于min_positions/新持仓/NAV≤0
"""

from __future__ import annotations

import pytest

from aipha.risk.constraints import (
    ConcentrationLimit,
    DrawdownLimit,
    RiskViolationType,
    TurnoverLimit,
)


# =========================================================================
# DrawdownLimit
# =========================================================================


class TestDrawdownLimit:
    """DrawdownLimit 的边界场景。"""

    def test_no_drawdown_passes(self) -> None:
        dl = DrawdownLimit(max_drawdown=0.20, reduce_position_at=0.10)
        dl.update_nav(100.0)
        r = dl.check("A", 100, "BUY", {}, 100.0, {"A": 10.0})
        assert r.passed is True

    def test_sell_always_allowed(self) -> None:
        dl = DrawdownLimit(max_drawdown=0.20, reduce_position_at=0.10)
        dl.update_nav(100.0)
        dl.update_nav(70.0)  # 30% drawdown
        r = dl.check("A", 100, "SELL", {"A": 200}, 70.0, {"A": 10.0})
        assert r.passed is True

    def test_exceeds_max_drawdown_blocks(self) -> None:
        dl = DrawdownLimit(max_drawdown=0.20, reduce_position_at=0.10)
        dl.update_nav(100.0)
        r = dl.check("A", 100, "BUY", {}, 79.0, {"A": 10.0})
        assert r.passed is False
        assert r.violation_type == RiskViolationType.DRAWDOWN_LIMIT
        assert r.adjusted_qty == 0

    def test_in_reduction_zone(self) -> None:
        dl = DrawdownLimit(max_drawdown=0.20, reduce_position_at=0.10)
        dl.update_nav(100.0)
        # dd = 15% → between 10% and 20%
        r = dl.check("A", 1000, "BUY", {}, 85.0, {"A": 10.0})
        # Should adjust qty down
        assert r.adjusted_qty is not None
        assert r.adjusted_qty < 1000

    def test_peak_nav_zero(self) -> None:
        dl = DrawdownLimit()
        assert dl.get_current_drawdown() == 0.0

    def test_peak_nav_updates(self) -> None:
        dl = DrawdownLimit()
        dl.update_nav(100.0)
        dl.update_nav(110.0)
        dl.update_nav(105.0)
        assert dl.peak_nav == 110.0
        assert dl.current_nav == 105.0


# =========================================================================
# TurnoverLimit
# =========================================================================


class TestTurnoverLimit:
    """TurnoverLimit 的边界场景。"""

    def test_normal_trade_passes(self) -> None:
        tl = TurnoverLimit(max_daily_turnover=0.50, max_single_trade=0.10)
        r = tl.check("A", 100, "BUY", {}, 100000.0, {"A": 10.0})
        assert r.passed is True

    def test_single_trade_exceeds(self) -> None:
        tl = TurnoverLimit(max_daily_turnover=0.50, max_single_trade=0.10)
        # 1000 * 20 = 20000, nav=100000 → 20% > 10%
        r = tl.check("A", 1000, "BUY", {}, 100000.0, {"A": 20.0})
        assert r.passed is False
        assert r.violation_type == RiskViolationType.TURNOVER_LIMIT

    def test_daily_turnover_limit(self) -> None:
        tl = TurnoverLimit(max_daily_turnover=0.50, max_single_trade=0.30)
        tl.daily_traded_value = 45000.0  # already 45%
        # 10000 more → 55000 > 50000 limit
        r = tl.check("A", 1000, "BUY", {}, 100000.0, {"A": 10.0})
        assert r.passed is False

    def test_no_price(self) -> None:
        tl = TurnoverLimit()
        r = tl.check("A", 100, "BUY", {}, 100000.0, {})
        assert r.passed is False

    def test_nav_zero(self) -> None:
        tl = TurnoverLimit()
        r = tl.check("A", 100, "BUY", {}, 0.0, {"A": 10.0})
        assert r.passed is False

    def test_reset_daily(self) -> None:
        tl = TurnoverLimit()
        tl.record_trade(5000.0)
        assert tl.daily_traded_value == 5000.0
        tl.reset_daily("20240102")
        assert tl.daily_traded_value == 0.0
        assert tl.last_reset_date == "20240102"

    def test_reset_daily_same_date_noop(self) -> None:
        tl = TurnoverLimit()
        tl.record_trade(5000.0)
        tl.last_reset_date = "20240102"
        tl.reset_daily("20240102")
        assert tl.daily_traded_value == 5000.0  # not reset

    def test_remaining_capacity(self) -> None:
        tl = TurnoverLimit(max_daily_turnover=0.50)
        tl.daily_traded_value = 30000.0
        remaining = tl.get_remaining_capacity(100000.0)
        assert remaining == pytest.approx(20000.0)

    def test_remaining_capacity_exhausted(self) -> None:
        tl = TurnoverLimit(max_daily_turnover=0.50)
        tl.daily_traded_value = 60000.0
        assert tl.get_remaining_capacity(100000.0) == 0.0


# =========================================================================
# ConcentrationLimit
# =========================================================================


class TestConcentrationLimit:
    """ConcentrationLimit 的边界场景。"""

    def test_normal_buy_passes(self) -> None:
        cl = ConcentrationLimit(max_top_n_weight=0.50, top_n=3, min_positions=2)
        positions = {"A": 100, "B": 100, "C": 100}
        prices = {"A": 10.0, "B": 10.0, "C": 10.0, "D": 10.0}
        r = cl.check("D", 100, "BUY", positions, 10000.0, prices)
        assert r.passed is True

    def test_top_n_weight_exceeded(self) -> None:
        cl = ConcentrationLimit(max_top_n_weight=0.30, top_n=1, min_positions=1)
        prices = {"A": 100.0}
        # Buy 100 shares at 100 = 10000, nav = 10000 → top1 weight = 100%
        r = cl.check("A", 100, "BUY", {}, 10000.0, prices)
        assert r.passed is False

    def test_sell_blocks_below_min_positions(self) -> None:
        cl = ConcentrationLimit(min_positions=3)
        positions = {"A": 100, "B": 100, "C": 100}
        prices = {"A": 10.0, "B": 10.0, "C": 10.0}
        # Selling all of A → 2 remaining < 3
        r = cl.check("A", 100, "SELL", positions, 10000.0, prices)
        assert r.passed is False

    def test_sell_partial_allowed(self) -> None:
        cl = ConcentrationLimit(min_positions=2)
        positions = {"A": 200, "B": 100, "C": 100}
        prices = {"A": 10.0, "B": 10.0, "C": 10.0}
        # Selling 100 of A (still holds 100) → 3 positions >= 2
        r = cl.check("A", 100, "SELL", positions, 10000.0, prices)
        assert r.passed is True

    def test_nav_zero_blocks(self) -> None:
        cl = ConcentrationLimit()
        r = cl.check("A", 100, "BUY", {}, 0.0, {"A": 10.0})
        assert r.passed is False

    def test_new_position_added(self) -> None:
        """新标的买入被正确加入计算。"""
        cl = ConcentrationLimit(max_top_n_weight=0.80, top_n=2, min_positions=1)
        positions = {"A": 100}
        prices = {"A": 10.0, "B": 10.0}
        r = cl.check("B", 100, "BUY", positions, 10000.0, prices)
        # top2_weight = (1000+1000)/10000 = 20% <= 80%
        assert r.passed is True
