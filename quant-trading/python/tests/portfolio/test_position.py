"""
Position 单元测试 - T+1可卖数量状态机

测试覆盖：
1. 基本属性与初始化
2. T日买入不可卖规则
3. T+1日终解锁
4. 冻结/解冻流程
5. 部分成交场景
6. 边界条件与异常
"""

from datetime import date
from decimal import Decimal

import pytest

from aipha.portfolio.position import (
    Position,
    PositionSide,
    PositionUpdateReason,
)


class TestPositionBasic:
    """Position 基础功能测试"""

    def test_init_default(self):
        """测试：默认初始化"""
        pos = Position(symbol="000001.SZ")

        assert pos.symbol == "000001.SZ"
        assert pos.side == PositionSide.LONG
        assert pos.total_qty == 0
        assert pos.frozen_qty == 0
        assert pos.today_bought_qty == 0
        assert pos.available_to_sell == 0
        assert pos.avg_cost == Decimal("0")

    def test_init_with_existing_position(self):
        """测试：带初始持仓初始化"""
        pos = Position(
            symbol="000001.SZ",
            total_qty=1000,
            frozen_qty=0,
            today_bought_qty=0,
            avg_cost=Decimal("10.50"),
            last_trade_date=date(2024, 1, 2),
        )

        assert pos.total_qty == 1000
        assert pos.available_to_sell == 1000
        assert pos.avg_cost == Decimal("10.50")

    def test_init_validation_negative_total(self):
        """测试：初始化校验 - 负数总持仓"""
        with pytest.raises(ValueError, match="total_qty不能为负"):
            Position(symbol="000001.SZ", total_qty=-100)

    def test_init_validation_frozen_exceeds_total(self):
        """测试：初始化校验 - 冻结超过总持仓"""
        with pytest.raises(ValueError, match="frozen_qty.*不能大于total_qty"):
            Position(symbol="000001.SZ", total_qty=100, frozen_qty=200)

    def test_to_dict(self):
        """测试：导出为字典"""
        pos = Position(
            symbol="000001.SZ",
            total_qty=1000,
            avg_cost=Decimal("10.50"),
            last_trade_date=date(2024, 1, 2),
        )
        d = pos.to_dict()

        assert d["symbol"] == "000001.SZ"
        assert d["total_qty"] == 1000
        assert d["available_to_sell"] == 1000
        assert d["avg_cost"] == "10.50"
        assert d["last_trade_date"] == "2024-01-02"


class TestPositionT1Rule:
    """T+1 规则测试"""

    def test_t_day_buy_not_sellable(self):
        """测试：T日买入不可卖"""
        pos = Position(symbol="000001.SZ")
        trade_date = date(2024, 1, 2)

        # T日买入1000股
        pos.buy_filled(qty=1000, price=Decimal("10.00"), trade_date=trade_date)

        assert pos.total_qty == 1000
        assert pos.today_bought_qty == 1000
        assert pos.available_to_sell == 0  # T日买入不可卖

    def test_t_day_multiple_buys(self):
        """测试：T日多次买入累计不可卖"""
        pos = Position(symbol="000001.SZ")
        trade_date = date(2024, 1, 2)

        pos.buy_filled(qty=500, price=Decimal("10.00"), trade_date=trade_date)
        pos.buy_filled(qty=300, price=Decimal("10.10"), trade_date=trade_date)
        pos.buy_filled(qty=200, price=Decimal("10.20"), trade_date=trade_date)

        assert pos.total_qty == 1000
        assert pos.today_bought_qty == 1000
        assert pos.available_to_sell == 0

    def test_t_plus_1_unlock(self):
        """测试：T+1日终解锁后可卖"""
        pos = Position(symbol="000001.SZ")
        t_day = date(2024, 1, 2)
        t_plus_1 = date(2024, 1, 3)

        # T日买入
        pos.buy_filled(qty=1000, price=Decimal("10.00"), trade_date=t_day)
        assert pos.available_to_sell == 0

        # T+1日终解锁
        pos.day_end_unlock(new_trade_date=t_plus_1)

        assert pos.total_qty == 1000
        assert pos.today_bought_qty == 0
        assert pos.available_to_sell == 1000  # 解锁后可卖

    def test_mixed_old_and_new_position(self):
        """测试：混合持仓（昨日持仓+今日买入）"""
        pos = Position(
            symbol="000001.SZ",
            total_qty=500,  # 昨日持仓
            today_bought_qty=0,
            last_trade_date=date(2024, 1, 2),
        )
        trade_date = date(2024, 1, 3)

        # 今日买入300股
        pos.buy_filled(qty=300, price=Decimal("10.00"), trade_date=trade_date)

        assert pos.total_qty == 800  # 500 + 300
        assert pos.today_bought_qty == 300  # 今日买入
        assert pos.available_to_sell == 500  # 只有昨日持仓可卖

    def test_cross_day_resets_today_bought(self):
        """测试：跨日买入重置today_bought_qty"""
        pos = Position(symbol="000001.SZ")
        day1 = date(2024, 1, 2)
        day2 = date(2024, 1, 3)

        # Day1 买入
        pos.buy_filled(qty=500, price=Decimal("10.00"), trade_date=day1)
        assert pos.today_bought_qty == 500

        # Day2 买入（新交易日）
        pos.buy_filled(qty=300, price=Decimal("10.00"), trade_date=day2)

        assert pos.total_qty == 800
        assert pos.today_bought_qty == 300  # 只有Day2买入
        assert pos.available_to_sell == 500  # Day1买入已可卖


class TestPositionFreezeUnfreeze:
    """冻结/解冻测试"""

    def test_freeze_for_sell_success(self):
        """测试：成功冻结卖出"""
        pos = Position(
            symbol="000001.SZ",
            total_qty=1000,
            today_bought_qty=0,
        )

        result = pos.freeze_for_sell(qty=500)

        assert result is True
        assert pos.frozen_qty == 500
        assert pos.available_to_sell == 500

    def test_freeze_exceeds_available(self):
        """测试：冻结超过可卖数量"""
        pos = Position(
            symbol="000001.SZ",
            total_qty=1000,
            today_bought_qty=300,  # 300不可卖
        )

        assert pos.available_to_sell == 700

        with pytest.raises(ValueError, match="冻结数量.*超过可卖数量"):
            pos.freeze_for_sell(qty=800)

    def test_freeze_t_day_position_rejected(self):
        """测试：T日买入的持仓无法冻结"""
        pos = Position(symbol="000001.SZ")
        pos.buy_filled(qty=1000, price=Decimal("10.00"), trade_date=date(2024, 1, 2))

        assert pos.available_to_sell == 0

        with pytest.raises(ValueError, match="冻结数量.*超过可卖数量"):
            pos.freeze_for_sell(qty=100)

    def test_unfreeze_on_cancel(self):
        """测试：撤单时解冻"""
        pos = Position(symbol="000001.SZ", total_qty=1000)

        pos.freeze_for_sell(qty=500)
        assert pos.frozen_qty == 500
        assert pos.available_to_sell == 500

        pos.unfreeze(qty=500)
        assert pos.frozen_qty == 0
        assert pos.available_to_sell == 1000

    def test_unfreeze_partial(self):
        """测试：部分解冻"""
        pos = Position(symbol="000001.SZ", total_qty=1000)

        pos.freeze_for_sell(qty=500)
        pos.unfreeze(qty=200)

        assert pos.frozen_qty == 300
        assert pos.available_to_sell == 700

    def test_unfreeze_exceeds_frozen(self):
        """测试：解冻超过冻结数量"""
        pos = Position(symbol="000001.SZ", total_qty=1000)
        pos.freeze_for_sell(qty=300)

        with pytest.raises(ValueError, match="解冻数量.*超过冻结数量"):
            pos.unfreeze(qty=500)


class TestPositionSellFilled:
    """卖出成交测试"""

    def test_sell_filled_reduces_position(self):
        """测试：卖出成交减少持仓"""
        pos = Position(symbol="000001.SZ", total_qty=1000)

        pos.freeze_for_sell(qty=500)
        pos.sell_filled(qty=500, price=Decimal("11.00"))

        assert pos.total_qty == 500
        assert pos.frozen_qty == 0
        assert pos.available_to_sell == 500

    def test_sell_filled_partial(self):
        """测试：部分成交"""
        pos = Position(symbol="000001.SZ", total_qty=1000)

        pos.freeze_for_sell(qty=500)
        pos.sell_filled(qty=200, price=Decimal("11.00"))

        assert pos.total_qty == 800
        assert pos.frozen_qty == 300  # 500 - 200
        assert pos.available_to_sell == 500

    def test_sell_filled_exceeds_frozen(self):
        """测试：成交超过冻结"""
        pos = Position(symbol="000001.SZ", total_qty=1000)
        pos.freeze_for_sell(qty=300)

        with pytest.raises(ValueError, match="卖出成交数量.*超过冻结数量"):
            pos.sell_filled(qty=500, price=Decimal("11.00"))

    def test_complete_round_trip(self):
        """测试：完整交易流程（买入→解锁→冻结→卖出）"""
        pos = Position(symbol="000001.SZ")

        # T日买入
        t_day = date(2024, 1, 2)
        pos.buy_filled(qty=1000, price=Decimal("10.00"), trade_date=t_day)
        assert pos.available_to_sell == 0

        # T+1解锁
        t_plus_1 = date(2024, 1, 3)
        pos.day_end_unlock(new_trade_date=t_plus_1)
        assert pos.available_to_sell == 1000

        # 冻结卖出
        pos.freeze_for_sell(qty=500)
        assert pos.available_to_sell == 500

        # 卖出成交
        pos.sell_filled(qty=500, price=Decimal("11.00"))
        assert pos.total_qty == 500
        assert pos.available_to_sell == 500


class TestPositionLogs:
    """持仓日志测试"""

    def test_logs_recorded(self):
        """测试：操作日志被记录"""
        pos = Position(symbol="000001.SZ")

        pos.buy_filled(qty=1000, price=Decimal("10.00"), trade_date=date(2024, 1, 2))
        pos.day_end_unlock(new_trade_date=date(2024, 1, 3))
        pos.freeze_for_sell(qty=500, order_id="ORD001")
        pos.sell_filled(qty=500, price=Decimal("11.00"), order_id="ORD001")

        logs = pos.get_logs()
        assert len(logs) == 4

        assert logs[0].reason == PositionUpdateReason.BUY_FILLED
        assert logs[1].reason == PositionUpdateReason.DAY_END_UNLOCK
        assert logs[2].reason == PositionUpdateReason.SELL_FROZEN
        assert logs[3].reason == PositionUpdateReason.SELL_FILLED

    def test_clear_logs(self):
        """测试：清空日志"""
        pos = Position(symbol="000001.SZ")
        pos.buy_filled(qty=100, price=Decimal("10.00"), trade_date=date(2024, 1, 2))

        assert len(pos.get_logs()) == 1

        pos.clear_logs()
        assert len(pos.get_logs()) == 0


class TestPositionAvgCost:
    """平均成本计算测试"""

    def test_avg_cost_single_buy(self):
        """测试：单次买入成本"""
        pos = Position(symbol="000001.SZ")
        pos.buy_filled(qty=1000, price=Decimal("10.00"), trade_date=date(2024, 1, 2))

        assert pos.avg_cost == Decimal("10.00")

    def test_avg_cost_multiple_buys(self):
        """测试：多次买入加权平均成本"""
        pos = Position(symbol="000001.SZ")

        pos.buy_filled(qty=1000, price=Decimal("10.00"), trade_date=date(2024, 1, 2))
        pos.buy_filled(qty=1000, price=Decimal("12.00"), trade_date=date(2024, 1, 2))

        # 加权平均：(1000*10 + 1000*12) / 2000 = 11.00
        assert pos.avg_cost == Decimal("11.00")

    def test_market_value(self):
        """测试：市值计算"""
        pos = Position(
            symbol="000001.SZ",
            total_qty=1000,
            avg_cost=Decimal("10.50"),
        )

        assert pos.market_value == Decimal("10500")
