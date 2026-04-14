"""
测试 frozen_qty 锁定 BUG 修复（C1）

BUG 描述：
  engine._process_pending_orders 中，卖单在以下情况不释放 frozen_qty，
  导致持仓永久锁定：
  1. 标的无行情（order.symbol not in bars）
  2. 标的有行情但 fill_model.try_fill 返回 None（涨停/无效价格等）

修复：
  - 卖单无行情时调用 position_manager.unfreeze_for_sell()
  - 卖单成交失败时调用 position_manager.unfreeze_for_sell()
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

import pytest

from aipha.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
)
from aipha.domain.models import Bar, Fill, Side
from aipha.execution.fill_models import FillModel, FillResult, NextBarOpenFillModel
from aipha.execution.fees import (
    CommissionModel,
    FeeBreakdown,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.execution.fill_models import FillModel, NextBarOpenFillModel
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.market.base import MarketRules
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.portfolio.position_manager import PositionManager
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext


# ============================================================================
# 测试辅助
# ============================================================================


def _make_bar(
    symbol: str,
    trade_date: date,
    price: float = 10.0,
    volume: float = 1_000_000.0,
) -> Bar:
    """创建测试用 Bar"""
    ts = datetime.combine(trade_date, datetime.min.time().replace(hour=15))
    return Bar(
        symbol=symbol,
        start_ts=ts.replace(hour=9, minute=30),
        end_ts=ts,
        open=price * 0.99,
        high=price * 1.01,
        low=price * 0.98,
        close=price,
        volume=volume,
    )


@dataclass
class BuyThenSellStrategy(Strategy):
    """Day 1 买入，Day N 卖出的策略"""

    name: str = "BuyThenSell"
    target_symbol: str = "A"
    buy_day: int = 0  # 第几个交易日买入（0-indexed）
    sell_day: int = 2  # 第几个交易日卖出
    _day_count: int = field(default=0, repr=False)

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        intents = []
        current_day = self._day_count
        self._day_count += 1

        if current_day == self.buy_day and self.target_symbol in bars:
            bar = bars[self.target_symbol]
            qty = int(context.cash * 0.5 / bar.close / 100) * 100
            if qty >= 100:
                intents.append(
                    OrderIntent(
                        symbol=self.target_symbol,
                        side=Side.BUY,
                        qty=qty,
                        intent_type=IntentType.ORDER,
                        reason="test_buy",
                    )
                )
        elif current_day == self.sell_day:
            pos_qty = context.positions.get(self.target_symbol, 0)
            if pos_qty > 0:
                intents.append(
                    OrderIntent(
                        symbol=self.target_symbol,
                        side=Side.SELL,
                        qty=pos_qty,
                        intent_type=IntentType.ORDER,
                        reason="test_sell",
                    )
                )

        return intents


@dataclass
class AlwaysFailFillModel(FillModel):
    """永远不成交的 FillModel（模拟涨停/跌停等极端情况）"""

    name: str = "always_fail"
    is_same_bar: bool = False

    def try_fill(
        self,
        order,
        next_bar: Bar,
        rules: MarketRules,
    ) -> FillResult:
        return FillResult(fill=None, no_fill_reason="simulated_fail")


@dataclass
class SelectiveFillModel(FillModel):
    """选择性成交：buy 总是成交，sell 在 fail_sell_symbols 中的不成交"""

    name: str = "selective"
    is_same_bar: bool = False
    fail_sell_symbols: list[str] = field(default_factory=list)
    _delegate: FillModel = field(default_factory=NextBarOpenFillModel)

    def try_fill(
        self,
        order,
        next_bar: Bar,
        rules: MarketRules,
    ) -> FillResult:
        if order.side == Side.SELL and order.symbol in self.fail_sell_symbols:
            return FillResult(fill=None, no_fill_reason="simulated_sell_fail")
        return self._delegate.try_fill(order=order, next_bar=next_bar, rules=rules)


def _default_fee_model(config: BacktestConfig) -> FeeModelCNEquity:
    return FeeModelCNEquity(
        commission=CommissionModel(rate=config.commission_rate, min_fee=config.commission_min),
        stamp_tax=StampTaxModel(sell_rate=config.stamp_tax_rate),
        transfer_fee=TransferFeeModel(rate=config.transfer_fee_rate),
    )


def _build_bars(
    symbols: list[str],
    dates: list[date],
    price: float = 10.0,
    missing: Optional[dict[date, set[str]]] = None,
) -> dict[date, dict[str, Bar]]:
    """构建 bars_by_date，支持指定缺失标的

    Args:
        symbols: 标的列表
        dates: 交易日列表
        price: 基础价格
        missing: {trade_date: {symbols_to_exclude}} 缺失映射
    """
    missing = missing or {}
    result = {}
    for d in dates:
        bars = {}
        excluded = missing.get(d, set())
        for s in symbols:
            if s not in excluded:
                bars[s] = _make_bar(s, d, price=price)
        result[d] = bars
    return result


# ============================================================================
# C1-a: 卖单无行情释放 frozen_qty
# ============================================================================


class TestSellOrderNoBarReleaseFrozenQty:
    """验证卖单目标无行情时 frozen_qty 被正确释放"""

    def _make_engine(
        self,
        strategy: Strategy,
        fill_model: Optional[FillModel] = None,
    ) -> BacktestEngine:
        config = BacktestConfig(initial_capital=100_000.0)
        return BacktestEngine(
            config=config,
            strategy=strategy,
            fill_model=fill_model or NextBarOpenFillModel(),
            slippage=FixedBpsSlippageModel(bps=0.0),
            fee_model=_default_fee_model(config),
            rules=MarketRulesCNEquity(),
        )

    def test_sell_no_bar_releases_frozen_qty(self):
        """卖单目标标的在 T+sell 日无行情，frozen_qty 应被释放"""
        # Day 0: 策略发出买入信号
        # Day 1: 买入成交 (NBO)
        # Day 2: 策略发出卖出信号 → freeze_for_sell → pending_order
        # Day 3: A 无行情 → sell 订单取消，frozen_qty 释放
        # Day 4: A 有行情 → 持仓应可卖（available_to_sell > 0）
        dates = [
            date(2025, 1, 6),   # Day 0
            date(2025, 1, 7),   # Day 1
            date(2025, 1, 8),   # Day 2
            date(2025, 1, 9),   # Day 3 (A 无行情)
            date(2025, 1, 10),  # Day 4
        ]
        # Day 3 移除 A 的行情
        bars = _build_bars(["A"], dates, price=10.0, missing={date(2025, 1, 9): {"A"}})

        strategy = BuyThenSellStrategy(
            target_symbol="A",
            buy_day=0,
            sell_day=2,
        )
        engine = self._make_engine(strategy)
        result = engine.run(bars)

        # 关键断言：position 不应有 frozen_qty 锁定
        # 买入后又尝试卖出但失败，持仓应仍存在且可卖
        if result.final_positions.get("A", 0) > 0:
            # 如果持仓还在，frozen_qty 应为 0
            pos = engine._position_manager.positions["A"]
            assert pos.frozen_qty == 0, (
                f"frozen_qty 应为 0（卖单无行情已释放），实际={pos.frozen_qty}"
            )
            assert pos.available_to_sell > 0, (
                "持仓 available_to_sell 应 > 0"
            )

    def test_sell_no_bar_position_still_sellable_next_day(self):
        """卖单因无行情释放后，下一交易日持仓可再次卖出"""
        dates = [
            date(2025, 1, 6),   # Day 0: buy signal
            date(2025, 1, 7),   # Day 1: buy fill (NBO)
            date(2025, 1, 8),   # Day 2: sell signal
            date(2025, 1, 9),   # Day 3: A 无行情 → sell cancelled
            date(2025, 1, 10),  # Day 4: A 有行情
        ]
        bars = _build_bars(["A"], dates, price=10.0, missing={date(2025, 1, 9): {"A"}})

        @dataclass
        class BuySellRetrySell(Strategy):
            """Day0 买，Day2 卖（失败后 Day4 再卖）"""

            name: str = "BuySellRetry"
            _day: int = field(default=0, repr=False)
            _bought_qty: int = field(default=0, repr=False)

            def on_bar(self, bars, context):
                intents = []
                d = self._day
                self._day += 1

                if d == 0 and "A" in bars:
                    qty = int(context.cash * 0.5 / bars["A"].close / 100) * 100
                    if qty >= 100:
                        self._bought_qty = qty
                        intents.append(OrderIntent(
                            symbol="A", side=Side.BUY, qty=qty,
                            intent_type=IntentType.ORDER,
                        ))
                elif d == 2:  # sell signal
                    pos_qty = context.positions.get("A", 0)
                    if pos_qty > 0:
                        intents.append(OrderIntent(
                            symbol="A", side=Side.SELL, qty=pos_qty,
                            intent_type=IntentType.ORDER,
                        ))
                elif d == 4:  # retry sell
                    pos_qty = context.positions.get("A", 0)
                    if pos_qty > 0:
                        intents.append(OrderIntent(
                            symbol="A", side=Side.SELL, qty=pos_qty,
                            intent_type=IntentType.ORDER,
                        ))

                return intents

        # 添加 Day5 来让 Day4 的 NBO 卖单成交
        dates.append(date(2025, 1, 13))
        bars[date(2025, 1, 13)] = {"A": _make_bar("A", date(2025, 1, 13), 10.0)}

        strategy = BuySellRetrySell()
        engine = self._make_engine(strategy)
        result = engine.run(bars)

        # Day2 的 sell 在 Day3 因无行情取消
        # Day4 的 sell 在 Day5 应成功成交
        sell_trades = [t for t in result.trades if t.side == Side.SELL]
        assert len(sell_trades) >= 1, "重试的卖单应成功成交"
        assert result.final_positions.get("A", 0) == 0, "持仓应已清空"

    def test_buy_no_bar_still_works(self):
        """确认买单无行情时冻结资金释放（回归测试）"""
        dates = [
            date(2025, 1, 6),   # Day 0: buy signal
            date(2025, 1, 7),   # Day 1: A 无行情
            date(2025, 1, 8),   # Day 2
        ]
        bars = _build_bars(["A"], dates, price=10.0, missing={date(2025, 1, 7): {"A"}})

        strategy = BuyThenSellStrategy(target_symbol="A", buy_day=0, sell_day=99)
        config = BacktestConfig(initial_capital=100_000.0)
        engine = BacktestEngine(
            config=config,
            strategy=strategy,
            fill_model=NextBarOpenFillModel(),
            slippage=FixedBpsSlippageModel(bps=0.0),
            fee_model=_default_fee_model(config),
            rules=MarketRulesCNEquity(),
        )
        result = engine.run(bars)

        # 无行情买单应释放冻结资金
        assert engine._account.frozen_cash == Decimal("0"), (
            f"冻结资金应为 0，实际={engine._account.frozen_cash}"
        )


# ============================================================================
# C1-b: 卖单成交失败（fill=None）释放 frozen_qty
# ============================================================================


class TestSellOrderFillFailReleaseFrozenQty:
    """验证卖单成交失败时 frozen_qty 被正确释放"""

    def _make_engine(
        self,
        strategy: Strategy,
        fill_model: FillModel,
    ) -> BacktestEngine:
        config = BacktestConfig(initial_capital=100_000.0)
        return BacktestEngine(
            config=config,
            strategy=strategy,
            fill_model=fill_model,
            slippage=FixedBpsSlippageModel(bps=0.0),
            fee_model=_default_fee_model(config),
            rules=MarketRulesCNEquity(),
        )

    def test_sell_fill_fail_releases_frozen_qty(self):
        """卖单成交失败（涨停等），frozen_qty 应被释放"""
        dates = [
            date(2025, 1, 6),   # Day 0: buy signal
            date(2025, 1, 7),   # Day 1: buy fill (NBO)
            date(2025, 1, 8),   # Day 2: sell signal
            date(2025, 1, 9),   # Day 3: sell fill fails (always_fail)
            date(2025, 1, 10),  # Day 4
        ]
        bars = _build_bars(["A"], dates, price=10.0)

        # 使用 SelectiveFillModel：buy 正常成交，sell A 总是失败
        fill_model = SelectiveFillModel(fail_sell_symbols=["A"])
        strategy = BuyThenSellStrategy(target_symbol="A", buy_day=0, sell_day=2)
        engine = self._make_engine(strategy, fill_model)
        result = engine.run(bars)

        # 持仓应存在且 frozen_qty == 0
        if "A" in engine._position_manager.positions:
            pos = engine._position_manager.positions["A"]
            assert pos.frozen_qty == 0, (
                f"frozen_qty 应为 0（fill 失败已释放），实际={pos.frozen_qty}"
            )
            assert pos.available_to_sell > 0, (
                "持仓 available_to_sell 应 > 0"
            )

    def test_sell_fill_fail_then_retry_succeeds(self):
        """卖单成交失败后，下一次卖出尝试应成功"""
        dates = [
            date(2025, 1, 6),
            date(2025, 1, 7),
            date(2025, 1, 8),
            date(2025, 1, 9),
            date(2025, 1, 10),
            date(2025, 1, 13),  # Day 5: retry sell
            date(2025, 1, 14),  # Day 6: sell fills
        ]
        bars = _build_bars(["A"], dates, price=10.0)

        @dataclass
        class BuySellFailRetry(Strategy):
            """Day0 买，Day2 卖（fill fail），Day5 重试卖"""

            name: str = "BuySellFailRetry"
            _day: int = field(default=0, repr=False)

            def on_bar(self, bars, context):
                d = self._day
                self._day += 1
                intents = []

                if d == 0 and "A" in bars:
                    qty = int(context.cash * 0.5 / bars["A"].close / 100) * 100
                    if qty >= 100:
                        intents.append(OrderIntent(
                            symbol="A", side=Side.BUY, qty=qty,
                            intent_type=IntentType.ORDER,
                        ))
                elif d in (2, 5):
                    pos_qty = context.positions.get("A", 0)
                    if pos_qty > 0:
                        intents.append(OrderIntent(
                            symbol="A", side=Side.SELL, qty=pos_qty,
                            intent_type=IntentType.ORDER,
                        ))

                return intents

        @dataclass
        class FailOnceSelectiveFill(FillModel):
            """第一次 sell 失败，第二次 sell 成功"""

            name: str = "fail_once"
            is_same_bar: bool = False
            _sell_count: int = field(default=0, repr=False)
            _delegate: FillModel = field(default_factory=NextBarOpenFillModel)

            def try_fill(self, order, next_bar, rules):
                if order.side == Side.SELL:
                    self._sell_count += 1
                    if self._sell_count == 1:
                        return FillResult(fill=None, no_fill_reason="first_sell_fail")
                return self._delegate.try_fill(order=order, next_bar=next_bar, rules=rules)

        fill_model = FailOnceSelectiveFill()
        strategy = BuySellFailRetry()
        engine = self._make_engine(strategy, fill_model)
        result = engine.run(bars)

        # Day2 sell 在 Day3 fail → frozen_qty 释放
        # Day5 sell 在 Day6 成功
        sell_trades = [t for t in result.trades if t.side == Side.SELL]
        assert len(sell_trades) >= 1, "重试的卖单应成功成交"
        assert result.final_positions.get("A", 0) == 0, "持仓应已清空"


# ============================================================================
# H1: Account settle_buy 负现金防护
# ============================================================================


class TestAccountNegativeCashGuard:
    """验证 Account settle_buy 负现金保护"""

    def test_settle_buy_negative_cash_raises(self):
        """settle_buy 导致现金为负时抛出 ValueError"""
        from aipha.portfolio.account import Account

        account = Account(initial_cash=Decimal("1000.00"))
        account.freeze_cash(Decimal("1000.00"), "ORD001", "买入冻结")

        # 结算时 cost + fee > cash → 应抛异常
        with pytest.raises(ValueError, match="现金为负"):
            account.settle_buy(
                order_id="ORD001",
                cost=Decimal("1100.00"),
                fee=Decimal("5.00"),
                trade_date=date(2025, 1, 6),
            )

    def test_settle_buy_normal_succeeds(self):
        """正常结算不触发负现金保护"""
        from aipha.portfolio.account import Account

        account = Account(initial_cash=Decimal("100000.00"))
        account.freeze_cash(Decimal("50000.00"), "ORD001", "买入冻结")

        # cost + fee < cash → 正常结算
        account.settle_buy(
            order_id="ORD001",
            cost=Decimal("50000.00"),
            fee=Decimal("25.00"),
            trade_date=date(2025, 1, 6),
        )
        assert account.cash == Decimal("49975.00")

    def test_settle_buy_tiny_rounding_allowed(self):
        """允许极小浮点误差（< 0.01）"""
        from aipha.portfolio.account import Account

        account = Account(initial_cash=Decimal("10000.00"))
        account.freeze_cash(Decimal("10000.00"), "ORD001", "买入冻结")

        # cost = exactly cash → should succeed (cash = 0)
        account.settle_buy(
            order_id="ORD001",
            cost=Decimal("10000.00"),
            fee=Decimal("0.00"),
            trade_date=date(2025, 1, 6),
        )
        assert account.cash == Decimal("0.00")

    def test_settle_buy_marginal_negative_allowed(self):
        """现金仅略低于 -0.01 时仍通过（浮点精度容忍）"""
        from aipha.portfolio.account import Account

        account = Account(initial_cash=Decimal("10000.00"))
        account.freeze_cash(Decimal("10000.00"), "ORD001", "买入冻结")

        # cost + fee = 10000.005 → cash = -0.005, > -0.01 → 应通过
        account.settle_buy(
            order_id="ORD001",
            cost=Decimal("10000.00"),
            fee=Decimal("0.005"),
            trade_date=date(2025, 1, 6),
        )
        assert account.cash == Decimal("-0.005")
