"""Position 模块边界测试 — Phase 39

覆盖 position.py 中 10 个未覆盖的分支/边界场景。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from aipha.portfolio.position import Position, PositionSide


# ============================================================
# 1. __post_init__: frozen_qty < 0
# ============================================================
class TestPostInitFrozenNeg:
    def test_frozen_qty_negative(self):
        with pytest.raises(ValueError, match="frozen_qty不能为负"):
            Position(symbol="000001.SZ", side=PositionSide.LONG,
                     total_qty=100, frozen_qty=-1)

    def test_today_bought_qty_negative(self):
        with pytest.raises(ValueError, match="today_bought_qty不能为负"):
            Position(symbol="000001.SZ", side=PositionSide.LONG,
                     total_qty=100, today_bought_qty=-1)


# ============================================================
# 2. buy_filled: qty <= 0
# ============================================================
class TestBuyFilledInvalid:
    def test_buy_zero(self):
        pos = Position(symbol="000001.SZ", side=PositionSide.LONG)
        with pytest.raises(ValueError, match="买入数量必须为正"):
            pos.buy_filled(0, Decimal("10"), date(2023, 1, 3))

    def test_buy_negative(self):
        pos = Position(symbol="000001.SZ", side=PositionSide.LONG)
        with pytest.raises(ValueError, match="买入数量必须为正"):
            pos.buy_filled(-100, Decimal("10"), date(2023, 1, 3))


# ============================================================
# 3. freeze_for_sell / unfreeze / sell_filled: qty <= 0
# ============================================================
class TestZeroQtyMethods:
    @pytest.fixture
    def pos(self):
        p = Position(symbol="000001.SZ", side=PositionSide.LONG)
        p.buy_filled(100, Decimal("10"), date(2023, 1, 2))
        p.day_end_unlock(date(2023, 1, 3))
        return p

    def test_freeze_zero(self, pos):
        with pytest.raises(ValueError, match="冻结数量必须为正"):
            pos.freeze_for_sell(0)

    def test_unfreeze_zero(self, pos):
        pos.freeze_for_sell(50)
        with pytest.raises(ValueError, match="解冻数量必须为正"):
            pos.unfreeze(0)

    def test_sell_filled_zero(self, pos):
        pos.freeze_for_sell(50)
        with pytest.raises(ValueError, match="卖出数量必须为正"):
            pos.sell_filled(0, Decimal("11"))


# ============================================================
# 4. day_end_unlock: 同一天日期 → no-op
# ============================================================
class TestDayEndUnlockEdge:
    def test_same_day_noop(self):
        """传入与 last_trade_date 相同的日期 → 不触发 unlock"""
        pos = Position(symbol="000001.SZ", side=PositionSide.LONG)
        pos.buy_filled(100, Decimal("10"), date(2023, 1, 3))
        assert pos.today_bought_qty == 100
        # 同一天 unlock → no-op
        pos.day_end_unlock(date(2023, 1, 3))
        assert pos.today_bought_qty == 100  # 不变

    def test_earlier_day_noop(self):
        """传入更早的日期 → 不触发 unlock"""
        pos = Position(symbol="000001.SZ", side=PositionSide.LONG)
        pos.buy_filled(100, Decimal("10"), date(2023, 1, 5))
        assert pos.today_bought_qty == 100
        pos.day_end_unlock(date(2023, 1, 3))
        assert pos.today_bought_qty == 100  # 不变

    def test_last_trade_date_none(self):
        """last_trade_date=None 且 total_qty>0 → 应触发 unlock"""
        pos = Position(symbol="000001.SZ", side=PositionSide.LONG,
                       total_qty=200, today_bought_qty=200)
        assert pos.last_trade_date is None
        pos.day_end_unlock(date(2023, 1, 4))
        assert pos.today_bought_qty == 0
        assert pos.last_trade_date == date(2023, 1, 4)


# ============================================================
# 5. to_dict: last_trade_date=None
# ============================================================
class TestToDictEdge:
    def test_last_trade_date_none(self):
        pos = Position(symbol="600000.SH", side=PositionSide.LONG)
        d = pos.to_dict()
        assert d["last_trade_date"] is None
        assert d["symbol"] == "600000.SH"

    def test_last_trade_date_set(self):
        pos = Position(symbol="600000.SH", side=PositionSide.LONG)
        pos.buy_filled(100, Decimal("5"), date(2023, 3, 1))
        d = pos.to_dict()
        assert d["last_trade_date"] == "2023-03-01"


# ============================================================
# 6. sell_filled 后 avg_cost 不变
# ============================================================
class TestSellAvgCostUnchanged:
    def test_avg_cost_preserved(self):
        pos = Position(symbol="000001.SZ", side=PositionSide.LONG)
        pos.buy_filled(100, Decimal("10"), date(2023, 1, 3))
        cost_before = pos.avg_cost
        pos.day_end_unlock(date(2023, 1, 4))
        pos.freeze_for_sell(50)
        pos.sell_filled(50, Decimal("12"))
        assert pos.avg_cost == cost_before


# ============================================================
# 7. available_to_sell: max(0,...) 下溢兜底
# ============================================================
class TestAvailableToSellFloor:
    def test_t1_negative_floor(self):
        """T+1: total=100, frozen=50, today_bought=80 → 100-50-80=-30 → max(0,...)=0"""
        pos = Position(symbol="000001.SZ", side=PositionSide.LONG,
                       total_qty=100, frozen_qty=50, today_bought_qty=80,
                       last_trade_date=date(2023, 1, 3))
        # 虽然算/计结果是负数，但 max(0,...) 兜底
        assert pos.available_to_sell == 0

    def test_t0_negative_floor(self):
        """T+0: total=10, frozen=20 → 不可能(post_init校验), 但 total=10, frozen=10 → 0"""
        pos = Position(symbol="123456.SZ", side=PositionSide.LONG,
                       total_qty=10, frozen_qty=10, t_plus_n=0)
        assert pos.available_to_sell == 0


# ============================================================
# 8. buy_filled: 新交易日 vs 同一交易日 today_bought_qty 逻辑
# ============================================================
class TestBuyFilledDateLogic:
    def test_same_day_accumulate(self):
        """同一天两次买入 → today_bought_qty 累加"""
        pos = Position(symbol="000001.SZ", side=PositionSide.LONG)
        pos.buy_filled(100, Decimal("10"), date(2023, 1, 3))
        pos.buy_filled(200, Decimal("11"), date(2023, 1, 3))
        assert pos.today_bought_qty == 300
        assert pos.total_qty == 300

    def test_new_day_reset(self):
        """不同天买入 → today_bought_qty 重置"""
        pos = Position(symbol="000001.SZ", side=PositionSide.LONG)
        pos.buy_filled(100, Decimal("10"), date(2023, 1, 3))
        pos.buy_filled(200, Decimal("11"), date(2023, 1, 4))
        assert pos.today_bought_qty == 200
        assert pos.total_qty == 300


# ============================================================
# 9. buy_filled: 首次买入 vs 加权平均成本
# ============================================================
class TestBuyFilledCostCalc:
    def test_first_buy_sets_cost(self):
        pos = Position(symbol="000001.SZ", side=PositionSide.LONG)
        pos.buy_filled(100, Decimal("10.50"), date(2023, 1, 3))
        assert pos.avg_cost == Decimal("10.50")

    def test_weighted_avg_cost(self):
        pos = Position(symbol="000001.SZ", side=PositionSide.LONG)
        pos.buy_filled(100, Decimal("10"), date(2023, 1, 3))
        pos.buy_filled(100, Decimal("12"), date(2023, 1, 3))
        # (10*100 + 12*100) / 200 = 11
        assert pos.avg_cost == Decimal("11")


# ============================================================
# 10. Position logs: various operations
# ============================================================
class TestPositionLogs:
    def test_log_count_after_cycle(self):
        """buy→unlock→freeze→sell 应该产生 4 条日志"""
        pos = Position(symbol="000001.SZ", side=PositionSide.LONG)
        pos.buy_filled(100, Decimal("10"), date(2023, 1, 3))
        pos.day_end_unlock(date(2023, 1, 4))
        pos.freeze_for_sell(100)
        pos.sell_filled(100, Decimal("11"))
        assert len(pos.get_logs()) == 4

    def test_clear_logs(self):
        pos = Position(symbol="000001.SZ", side=PositionSide.LONG)
        pos.buy_filled(100, Decimal("10"), date(2023, 1, 3))
        assert len(pos.get_logs()) == 1
        pos.clear_logs()
        assert len(pos.get_logs()) == 0
