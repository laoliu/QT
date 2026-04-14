"""持仓 T+0 模式测试（可转债）"""
from datetime import date
from decimal import Decimal

import pytest

from aipha.portfolio.position import Position, PositionSide


class TestPositionT0Mode:
    """T+0 模式测试（可转债）"""

    def test_t0_buy_and_sell_same_day(self):
        """测试 T+0 模式下当日买入可当日卖出"""
        pos = Position(symbol="127075.SZ", t_plus_n=0)  # T+0 模式
        trade_date = date(2024, 1, 15)

        # 买入 100 张
        pos.buy_filled(qty=100, price=Decimal("120.00"), trade_date=trade_date)

        assert pos.total_qty == 100
        assert pos.today_bought_qty == 100
        # T+0 模式：今日买入可卖
        assert pos.available_to_sell == 100

    def test_t0_multiple_trades_same_day(self):
        """测试 T+0 模式下当日多次交易"""
        pos = Position(symbol="127075.SZ", t_plus_n=0)
        trade_date = date(2024, 1, 15)

        # 第一次买入
        pos.buy_filled(qty=100, price=Decimal("120.00"), trade_date=trade_date)
        assert pos.available_to_sell == 100

        # 卖出 50 张
        pos.freeze_for_sell(qty=50)
        pos.sell_filled(qty=50, price=Decimal("122.00"))
        assert pos.total_qty == 50
        assert pos.available_to_sell == 50

        # 再买入 30 张
        pos.buy_filled(qty=30, price=Decimal("121.00"), trade_date=trade_date)
        assert pos.total_qty == 80
        assert pos.available_to_sell == 80  # T+0 全部可卖

    def test_t1_vs_t0_comparison(self):
        """T+1 与 T+0 对比测试"""
        trade_date = date(2024, 1, 15)

        # T+1 模式（A股）
        pos_t1 = Position(symbol="000001.SZ", t_plus_n=1)
        pos_t1.buy_filled(qty=1000, price=Decimal("10.00"), trade_date=trade_date)

        assert pos_t1.total_qty == 1000
        assert pos_t1.today_bought_qty == 1000
        assert pos_t1.available_to_sell == 0  # T+1 当日不可卖

        # T+0 模式（可转债）
        pos_t0 = Position(symbol="127075.SZ", t_plus_n=0)
        pos_t0.buy_filled(qty=100, price=Decimal("120.00"), trade_date=trade_date)

        assert pos_t0.total_qty == 100
        assert pos_t0.today_bought_qty == 100
        assert pos_t0.available_to_sell == 100  # T+0 当日可卖

    def test_t0_frozen_deduction(self):
        """测试 T+0 模式下冻结正确扣减"""
        pos = Position(symbol="127075.SZ", t_plus_n=0)
        trade_date = date(2024, 1, 15)

        pos.buy_filled(qty=100, price=Decimal("120.00"), trade_date=trade_date)

        # 冻结 30 张准备卖出
        success = pos.freeze_for_sell(qty=30)
        assert success is True
        assert pos.frozen_qty == 30
        assert pos.available_to_sell == 70  # 100 - 30 = 70

    def test_t0_day_end_unlock(self):
        """测试 T+0 模式的日终处理"""
        pos = Position(symbol="127075.SZ", t_plus_n=0)
        t_day = date(2024, 1, 15)
        t_plus_1 = date(2024, 1, 16)

        # T 日买入
        pos.buy_filled(qty=100, price=Decimal("120.00"), trade_date=t_day)
        assert pos.today_bought_qty == 100
        assert pos.available_to_sell == 100  # T+0 当日可卖

        # T+1 日终解锁
        pos.day_end_unlock(new_trade_date=t_plus_1)

        assert pos.today_bought_qty == 0  # 重置
        assert pos.available_to_sell == 100  # 仍然全部可卖

    def test_default_is_t1(self):
        """测试默认 T+1 模式"""
        pos = Position(symbol="000001.SZ")  # 不指定 t_plus_n

        assert pos.t_plus_n == 1  # 默认 T+1
