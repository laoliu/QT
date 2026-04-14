"""PositionManager 模块边界测试 — Phase 42

覆盖 position_manager.py 中 10 个未覆盖的分支/边界场景。
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from aipha.portfolio.position_manager import PositionManager
from aipha.domain.models import Side, Fill


def _make_fill(symbol, side, qty, price, order_id="O1"):
    """构造 Fill 对象"""
    return Fill(
        order_id=order_id,
        symbol=symbol,
        side=side,
        qty=qty,
        price=price,
        t_fill=datetime(2023, 1, 3, 10, 0),
        model="test",
    )


# ============================================================
# 1. get_positions_snapshot(prices=None) → market_value all 0
# ============================================================
class TestSnapshotPricesNone:
    def test_prices_none(self):
        pm = PositionManager()
        pm.process_fill(_make_fill("000001.SZ", Side.BUY, 100, 10.0), date(2023, 1, 3))
        snap = pm.get_positions_snapshot(prices=None)
        assert "000001.SZ" in snap
        assert snap["000001.SZ"].market_value == 0.0

    def test_prices_empty_dict(self):
        pm = PositionManager()
        pm.process_fill(_make_fill("000001.SZ", Side.BUY, 100, 10.0), date(2023, 1, 3))
        snap = pm.get_positions_snapshot(prices={})
        assert snap["000001.SZ"].market_value == 0.0


# ============================================================
# 2. 快照中部分标的无价格
# ============================================================
class TestSnapshotPartialPrices:
    def test_partial_prices(self):
        pm = PositionManager()
        pm.process_fill(_make_fill("000001.SZ", Side.BUY, 100, 10.0), date(2023, 1, 3))
        pm.process_fill(_make_fill("600000.SH", Side.BUY, 200, 20.0, "O2"), date(2023, 1, 3))
        snap = pm.get_positions_snapshot(prices={"000001.SZ": 11.0})
        assert snap["000001.SZ"].market_value == pytest.approx(1100.0)
        assert snap["600000.SH"].market_value == 0.0  # 无价格 → 0


# ============================================================
# 3. default_t_plus_n=0 (T+0 模式)
# ============================================================
class TestTPlusZeroMode:
    def test_buy_and_sell_same_day(self):
        """T+0 模式：买入后当日可卖"""
        pm = PositionManager(default_t_plus_n=0)
        pm.process_fill(_make_fill("128000.SZ", Side.BUY, 10, 100.0), date(2023, 1, 3))
        pos = pm.get_position("128000.SZ")
        assert pos.available_to_sell == 10  # T+0 → 当日可卖
        # 冻结并卖出
        assert pm.freeze_for_sell("128000.SZ", 10, "O2")
        pm.process_fill(_make_fill("128000.SZ", Side.SELL, 10, 105.0, "O2"), date(2023, 1, 3))
        assert pm.get_position("128000.SZ") is None  # 清仓后删除


# ============================================================
# 4. SELL fill_history 验证
# ============================================================
class TestSellFillHistory:
    def test_sell_fill_recorded(self):
        pm = PositionManager()
        pm.process_fill(_make_fill("000001.SZ", Side.BUY, 100, 10.0), date(2023, 1, 3))
        pm.day_end_process(date(2023, 1, 4))
        pm.freeze_for_sell("000001.SZ", 100, "O2")
        sell_fill = _make_fill("000001.SZ", Side.SELL, 100, 11.0, "O2")
        pm.process_fill(sell_fill, date(2023, 1, 4))
        history = pm.get_fill_history()
        assert len(history) == 2
        assert history[1]["side"] == Side.SELL
        assert history[1]["price"] == 11.0


# ============================================================
# 5. get_total_market_value: 缺失/空
# ============================================================
class TestTotalMarketValueEdge:
    def test_missing_price(self):
        pm = PositionManager()
        pm.process_fill(_make_fill("000001.SZ", Side.BUY, 100, 10.0), date(2023, 1, 3))
        pm.process_fill(_make_fill("600000.SH", Side.BUY, 200, 20.0, "O2"), date(2023, 1, 3))
        val = pm.get_total_market_value({"000001.SZ": 11.0})
        # 000001: 100*11=1100, 600000: 200*0=0
        assert val == pytest.approx(1100.0)

    def test_empty_positions(self):
        pm = PositionManager()
        assert pm.get_total_market_value({"A": 10.0}) == 0.0


# ============================================================
# 6. freeze_for_sell: zero qty → ValueError → False
# ============================================================
class TestFreezeZeroQty:
    def test_freeze_zero(self):
        pm = PositionManager()
        pm.process_fill(_make_fill("000001.SZ", Side.BUY, 100, 10.0), date(2023, 1, 3))
        pm.day_end_process(date(2023, 1, 4))
        assert pm.freeze_for_sell("000001.SZ", 0, "O2") is False

    def test_freeze_nonexist(self):
        pm = PositionManager()
        assert pm.freeze_for_sell("NONEXIST", 100, "O2") is False


# ============================================================
# 7. unfreeze_for_sell: excess qty → ValueError → False
# ============================================================
class TestUnfreezeExcess:
    def test_unfreeze_excess(self):
        pm = PositionManager()
        pm.process_fill(_make_fill("000001.SZ", Side.BUY, 100, 10.0), date(2023, 1, 3))
        pm.day_end_process(date(2023, 1, 4))
        pm.freeze_for_sell("000001.SZ", 50, "O2")
        # 解冻超过冻结量
        assert pm.unfreeze_for_sell("000001.SZ", 100, "O3") is False

    def test_unfreeze_nonexist(self):
        pm = PositionManager()
        assert pm.unfreeze_for_sell("NONEXIST", 100, "O2") is False


# ============================================================
# 8. day_end_process: 空持仓
# ============================================================
class TestDayEndEmpty:
    def test_empty_positions(self):
        pm = PositionManager()
        pm.day_end_process(date(2023, 1, 4))  # 不崩溃
        assert len(pm) == 0


# ============================================================
# 9. get_fill_history: 防御性拷贝
# ============================================================
class TestFillHistoryCopy:
    def test_copy_isolation(self):
        pm = PositionManager()
        pm.process_fill(_make_fill("000001.SZ", Side.BUY, 100, 10.0), date(2023, 1, 3))
        h1 = pm.get_fill_history()
        h1.clear()
        h2 = pm.get_fill_history()
        assert len(h2) == 1  # 内部不受影响


# ============================================================
# 10. 多次买卖循环后的状态一致性
# ============================================================
class TestMultipleCycles:
    def test_buy_sell_buy_again(self):
        """买入→清仓→再买入 → position 重建"""
        pm = PositionManager()
        pm.process_fill(_make_fill("000001.SZ", Side.BUY, 100, 10.0), date(2023, 1, 3))
        pm.day_end_process(date(2023, 1, 4))
        pm.freeze_for_sell("000001.SZ", 100, "O2")
        pm.process_fill(_make_fill("000001.SZ", Side.SELL, 100, 11.0, "O2"), date(2023, 1, 4))
        assert pm.get_position("000001.SZ") is None  # 清仓

        # 重新买入
        pm.process_fill(_make_fill("000001.SZ", Side.BUY, 200, 12.0, "O3"), date(2023, 1, 5))
        pos = pm.get_position("000001.SZ")
        assert pos is not None
        assert pos.total_qty == 200
        assert pos.avg_cost == Decimal("12")
