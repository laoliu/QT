"""
端到端集成测试

验证完整交易流程：
数据 → 策略信号 → 订单意图 → 验证 → 成交 → 持仓更新 → 账户结算

场景：
1. 单次买入完整流程
2. 买入后卖出完整流程（T+1）
3. 多股票组合调仓
4. 边界条件处理
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

from aipha.domain.models import Fill, Side
from aipha.execution.fees import (
    CommissionModel,
    FeeBreakdown,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.portfolio.account import Account
from aipha.portfolio.position import Position
from aipha.portfolio.position_manager import PositionManager
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext


# ============================================================================
# Test Strategy Implementation
# ============================================================================


class SimpleBuyStrategy(Strategy):
    """简单买入策略：第一根bar买入指定股票"""

    name = "SimpleBuyStrategy"

    def __init__(self, symbol: str, qty: int, price: float):
        self.symbol = symbol
        self.qty = qty
        self.price = price
        self.bought = False

    def on_bar(self, bars: dict, context: StrategyContext) -> list[OrderIntent]:
        if not self.bought:
            self.bought = True
            return [
                OrderIntent(
                    symbol=self.symbol,
                    side=Side.BUY,
                    qty=self.qty,
                    limit_price=self.price,
                )
            ]
        return []


class BuyThenSellStrategy(Strategy):
    """买入后卖出策略：模拟T+1限制"""

    name = "BuyThenSellStrategy"

    def __init__(self, symbol: str, qty: int):
        self.symbol = symbol
        self.qty = qty
        self.day = 0

    def on_bar(self, bars: dict, context: StrategyContext) -> list[OrderIntent]:
        self.day += 1
        if self.day == 1:
            # Day 1: Buy
            return [
                OrderIntent(
                    symbol=self.symbol,
                    side=Side.BUY,
                    qty=self.qty,
                    limit_price=10.00,
                )
            ]
        elif self.day == 2:
            # Day 2: Sell (after T+1 unlock)
            pos_qty = context.get_position(self.symbol)
            if pos_qty > 0:
                return [
                    OrderIntent(
                        symbol=self.symbol,
                        side=Side.SELL,
                        qty=self.qty,
                        limit_price=10.50,
                    )
                ]
        return []


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def account() -> Account:
    """测试账户：100万初始资金"""
    return Account(initial_cash=Decimal("1000000.00"))


@pytest.fixture
def position_manager() -> PositionManager:
    """空持仓管理器"""
    return PositionManager()


@pytest.fixture
def fee_model() -> FeeModelCNEquity:
    """标准A股费用模型"""
    return FeeModelCNEquity(
        commission=CommissionModel(rate=0.0003, min_fee=5.0),
        stamp_tax=StampTaxModel(sell_rate=0.001),
        transfer_fee=TransferFeeModel(rate=0.00002),
    )


# ============================================================================
# Helper Functions
# ============================================================================


def calculate_total_fee(fee_breakdown: FeeBreakdown) -> Decimal:
    """计算费用总额"""
    return Decimal(str(fee_breakdown.total))


def process_buy_intent(
    intent: OrderIntent,
    account: Account,
    position_manager: PositionManager,
    fee_model: FeeModelCNEquity,
    trade_date: date,
    fill_time: datetime,
) -> Fill:
    """
    处理买入意图的完整流程

    1. 冻结资金
    2. 生成成交
    3. 结算账户
    4. 更新持仓
    """
    price = intent.limit_price or 10.0  # Default price
    estimated_cost = Decimal(str(price)) * intent.qty
    estimated_fee = estimated_cost * Decimal("0.001")
    freeze_amount = estimated_cost + estimated_fee

    order_id = f"ORD_{intent.symbol}_{trade_date.isoformat()}"

    # 1. 冻结资金
    account.freeze_cash(freeze_amount, order_id, "买入冻结")

    # 2. 生成成交
    fill = Fill(
        order_id=order_id,
        symbol=intent.symbol,
        side=Side.BUY,
        qty=intent.qty,
        price=price,
        t_fill=fill_time,
        model="next_bar_open",
    )

    # 3. 计算实际费用
    fee_breakdown = fee_model.calc(fill)
    total_fee = calculate_total_fee(fee_breakdown)

    # 4. 结算账户
    actual_cost = Decimal(str(fill.price)) * fill.qty
    account.settle_buy(order_id, actual_cost, total_fee, trade_date)

    # 5. 更新持仓
    position_manager.process_fill(fill, trade_date)

    return fill


def process_sell_intent(
    intent: OrderIntent,
    account: Account,
    position_manager: PositionManager,
    fee_model: FeeModelCNEquity,
    trade_date: date,
    fill_time: datetime,
) -> Fill:
    """
    处理卖出意图的完整流程

    1. 冻结持仓
    2. 生成成交
    3. 结算账户
    4. 更新持仓
    """
    order_id = f"ORD_{intent.symbol}_{trade_date.isoformat()}_SELL"
    price = intent.limit_price or 10.0

    # 1. 冻结持仓
    position_manager.freeze_for_sell(intent.symbol, intent.qty, order_id)

    # 2. 生成成交
    fill = Fill(
        order_id=order_id,
        symbol=intent.symbol,
        side=Side.SELL,
        qty=intent.qty,
        price=price,
        t_fill=fill_time,
        model="next_bar_open",
    )

    # 3. 计算实际费用
    fee_breakdown = fee_model.calc(fill)
    total_fee = calculate_total_fee(fee_breakdown)

    # 4. 结算账户
    proceeds = Decimal(str(fill.price)) * fill.qty
    account.settle_sell(order_id, proceeds, total_fee, trade_date)

    # 5. 更新持仓
    position_manager.process_fill(fill, trade_date)

    return fill


def make_context(
    position_manager: PositionManager,
    account: Account,
    current_time: datetime,
) -> StrategyContext:
    """创建策略上下文"""
    positions = position_manager.get_positions_dict()
    return StrategyContext(
        current_time=current_time,
        positions=positions,
        cash=float(account.available_cash),
        nav=float(account.cash),
    )


# ============================================================================
# Integration Tests
# ============================================================================


class TestBuyFlow:
    """买入完整流程测试"""

    def test_single_buy_flow(
        self,
        account: Account,
        position_manager: PositionManager,
        fee_model: FeeModelCNEquity,
    ):
        """单次买入完整流程"""
        # 准备策略
        strategy = SimpleBuyStrategy("600000.SH", qty=1000, price=10.0)

        # Day 1: 生成买入意图
        trade_date = date(2025, 1, 6)
        fill_time = datetime(2025, 1, 6, 9, 31)

        context = make_context(position_manager, account, fill_time)
        intents = strategy.on_bar({}, context)

        assert len(intents) == 1
        assert intents[0].side == Side.BUY

        # 执行买入
        fill = process_buy_intent(
            intents[0], account, position_manager, fee_model, trade_date, fill_time
        )

        # 验证持仓
        assert position_manager.get_qty("600000.SH") == 1000
        assert position_manager.get_available_to_sell("600000.SH") == 0  # T+1未解锁

        # 验证账户
        cost = Decimal("10000.00")  # 1000 * 10
        fee_breakdown = fee_model.calc(fill)
        total_fee = calculate_total_fee(fee_breakdown)
        expected_cash = Decimal("1000000.00") - cost - total_fee

        assert account.cash == expected_cash
        assert account.frozen_cash == Decimal("0.00")

    def test_buy_updates_position_correctly(
        self,
        account: Account,
        position_manager: PositionManager,
        fee_model: FeeModelCNEquity,
    ):
        """买入正确更新持仓状态"""
        trade_date = date(2025, 1, 6)
        fill_time = datetime(2025, 1, 6, 9, 31)

        intent = OrderIntent(
            symbol="600000.SH",
            side=Side.BUY,
            qty=500,
            limit_price=20.00,
        )

        process_buy_intent(intent, account, position_manager, fee_model, trade_date, fill_time)

        # 持仓快照
        snapshot = position_manager.get_positions_snapshot()
        assert "600000.SH" in snapshot
        pos = snapshot["600000.SH"]
        assert pos.total_qty == 500
        assert pos.today_bought_qty == 500  # T日买入
        assert pos.available_to_sell == 0


class TestBuySellFlow:
    """买入后卖出完整流程测试（T+1）"""

    def test_buy_then_sell_with_t1(
        self,
        account: Account,
        position_manager: PositionManager,
        fee_model: FeeModelCNEquity,
    ):
        """买入后次日卖出（遵守T+1）"""
        # Day 1: 买入
        day1 = date(2025, 1, 6)
        buy_intent = OrderIntent(
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            limit_price=10.00,
        )
        process_buy_intent(
            buy_intent,
            account,
            position_manager,
            fee_model,
            day1,
            datetime(2025, 1, 6, 9, 31),
        )

        # Day 1 结束：T+1解锁 (传入下一个交易日)
        position_manager.day_end_process(trade_date=date(2025, 1, 7))

        # 验证可卖
        assert position_manager.get_available_to_sell("600000.SH") == 1000

        # Day 2: 卖出
        day2 = date(2025, 1, 7)
        sell_intent = OrderIntent(
            symbol="600000.SH",
            side=Side.SELL,
            qty=1000,
            limit_price=10.50,
        )
        process_sell_intent(
            sell_intent,
            account,
            position_manager,
            fee_model,
            day2,
            datetime(2025, 1, 7, 9, 31),
        )

        # 验证持仓清空
        assert position_manager.get_qty("600000.SH") == 0

        # 验证盈利（粗略检查：卖出收入 > 买入成本）
        assert account.cash > Decimal("1000400.00")

    def test_cannot_sell_same_day(
        self,
        account: Account,
        position_manager: PositionManager,
        fee_model: FeeModelCNEquity,
    ):
        """当日买入不可当日卖出"""
        trade_date = date(2025, 1, 6)

        # 买入
        buy_intent = OrderIntent(
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            limit_price=10.00,
        )
        process_buy_intent(
            buy_intent,
            account,
            position_manager,
            fee_model,
            trade_date,
            datetime(2025, 1, 6, 9, 31),
        )

        # 尝试当日卖出 - 冻结应失败
        result = position_manager.freeze_for_sell("600000.SH", qty=1000, order_id="SELL001")
        assert result is False  # T+1限制


class TestMultiStockPortfolio:
    """多股票组合测试"""

    def test_portfolio_rebalance(
        self,
        account: Account,
        position_manager: PositionManager,
        fee_model: FeeModelCNEquity,
    ):
        """组合调仓：买入多只股票"""
        day1 = date(2025, 1, 6)

        symbols = ["600000.SH", "000001.SZ", "600519.SH"]
        quantities = [1000, 500, 100]
        prices = [10.00, 15.00, 2000.00]

        for symbol, qty, price in zip(symbols, quantities, prices):
            intent = OrderIntent(symbol=symbol, side=Side.BUY, qty=qty, limit_price=price)
            process_buy_intent(
                intent,
                account,
                position_manager,
                fee_model,
                day1,
                datetime(2025, 1, 6, 9, 31),
            )

        # 验证所有持仓
        assert position_manager.get_qty("600000.SH") == 1000
        assert position_manager.get_qty("000001.SZ") == 500
        assert position_manager.get_qty("600519.SH") == 100

        # 计算总市值
        prices_for_mv = {"600000.SH": 10.0, "000001.SZ": 15.0, "600519.SH": 2000.0}
        total_mv = position_manager.get_total_market_value(prices_for_mv)
        expected_mv = 10.0 * 1000 + 15.0 * 500 + 2000.0 * 100
        assert total_mv == pytest.approx(expected_mv)

    def test_nav_calculation_with_positions(
        self,
        account: Account,
        position_manager: PositionManager,
        fee_model: FeeModelCNEquity,
    ):
        """NAV计算：现金+持仓市值"""
        day1 = date(2025, 1, 6)

        intent = OrderIntent(
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            limit_price=10.00,
        )
        process_buy_intent(
            intent,
            account,
            position_manager,
            fee_model,
            day1,
            datetime(2025, 1, 6, 9, 31),
        )

        # 假设股价涨到 11.00
        current_price = {"600000.SH": 11.0}
        mv = Decimal(str(position_manager.get_total_market_value(current_price)))
        nav = account.calculate_nav(mv)

        # NAV应接近初始资金（考虑交易费用和价格变动）
        assert nav > Decimal("1000000.00")


class TestEdgeCases:
    """边界条件测试"""

    def test_insufficient_funds(self, account: Account):
        """资金不足无法买入"""
        result = account.freeze_cash(
            Decimal("2000000.00"), "ORD001", "买入冻结"
        )
        assert result is False
        assert account.frozen_cash == Decimal("0.00")

    def test_partial_position_sell(
        self,
        account: Account,
        position_manager: PositionManager,
        fee_model: FeeModelCNEquity,
    ):
        """部分卖出"""
        # 买入 1000 股
        buy_intent = OrderIntent(
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            limit_price=10.00,
        )
        process_buy_intent(
            buy_intent,
            account,
            position_manager,
            fee_model,
            date(2025, 1, 6),
            datetime(2025, 1, 6, 9, 31),
        )

        # T+1 解锁
        position_manager.day_end_process(trade_date=date(2025, 1, 7))

        # 只卖 500 股
        sell_intent = OrderIntent(
            symbol="600000.SH",
            side=Side.SELL,
            qty=500,
            limit_price=10.50,
        )
        process_sell_intent(
            sell_intent,
            account,
            position_manager,
            fee_model,
            date(2025, 1, 7),
            datetime(2025, 1, 7, 9, 31),
        )

        # 还剩 500 股
        assert position_manager.get_qty("600000.SH") == 500

    def test_strategy_context_isolation(self, position_manager: PositionManager, account: Account):
        """策略上下文是只读快照"""
        # 创建一个持仓
        pos = Position(symbol="600000.SH")
        pos.buy_filled(qty=1000, price=Decimal("10.00"), trade_date=date(2025, 1, 6))
        position_manager.positions["600000.SH"] = pos

        # 创建策略上下文
        context = make_context(position_manager, account, datetime(2025, 1, 6, 9, 31))

        # 上下文的持仓信息正确
        ctx_qty = context.get_position("600000.SH")
        assert ctx_qty == 1000

        # 修改原始持仓不影响上下文
        pos.buy_filled(qty=500, price=Decimal("10.00"), trade_date=date(2025, 1, 6))
        assert context.get_position("600000.SH") == 1000  # 仍是1000
