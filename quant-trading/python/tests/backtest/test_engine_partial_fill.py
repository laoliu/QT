"""
测试回测引擎对部分成交（partial fill）的正确处理。

验证: 当 FillModel 返回 remaining_qty > 0 时，引擎正确释放
未成交部分的冻结资源（持仓/资金），且不发生泄漏。
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from aipha.backtest.engine import BacktestConfig, BacktestEngine, TradeRecord
from aipha.domain.models import Bar, Fill, Order, Side
from aipha.execution.fees import FeeModelCNEquity, CommissionModel, StampTaxModel, TransferFeeModel
from aipha.execution.fill_models import FillModel, FillResult, NextBarOpenFillModel
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext


# ============================================================================
# 辅助
# ============================================================================

def _bar(symbol: str, td: date, price: float = 10.0, volume: float = 1_000_000) -> Bar:
    ts = datetime.combine(td, datetime.min.time().replace(hour=15))
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


class PartialFillModel(FillModel):
    """模拟部分成交的 FillModel: 只成交请求数量的 fill_ratio 比例。"""
    name: str = "PartialFill"

    def __init__(self, inner: FillModel, fill_ratio: float = 0.5):
        self._inner = inner
        self._fill_ratio = fill_ratio

    def try_fill(self, *, order: Order, next_bar: Bar, rules) -> FillResult:
        result = self._inner.try_fill(order=order, next_bar=next_bar, rules=rules)
        if result.fill is None:
            return result

        # 按 lot_size=100 对齐
        partial_qty = max(100, int(order.qty * self._fill_ratio / 100) * 100)
        if partial_qty >= order.qty:
            return result

        from dataclasses import replace as dc_replace
        partial_fill = dc_replace(result.fill, qty=partial_qty)
        remaining = order.qty - partial_qty
        return FillResult(fill=partial_fill, remaining_qty=remaining)


@dataclass
class BuyOnceStrategy(Strategy):
    """第一天买入指定数量"""
    name: str = "BuyOnce"
    symbol: str = "000001.SZ"
    qty: int = 1000
    _done: bool = field(default=False, repr=False)

    def on_bar(self, bars, context):
        if self._done:
            return []
        self._done = True
        if self.symbol not in bars:
            return []
        return [OrderIntent(
            symbol=self.symbol, side=Side.BUY, qty=self.qty,
            intent_type=IntentType.ORDER,
        )]


@dataclass
class SellOnDay2Strategy(Strategy):
    """第二天卖出全部持仓"""
    name: str = "SellOnDay2"
    symbol: str = "000001.SZ"
    _day_count: int = field(default=0, repr=False)

    def on_bar(self, bars, context):
        self._day_count += 1
        if self._day_count != 2:
            return []
        qty = context.positions.get(self.symbol, 0)
        if qty <= 0:
            return []
        return [OrderIntent(
            symbol=self.symbol, side=Side.SELL, qty=qty,
            intent_type=IntentType.ORDER,
        )]


@dataclass
class BuyThenSellStrategy(Strategy):
    """第一天买，第三天卖"""
    name: str = "BuyThenSell"
    symbol: str = "000001.SZ"
    buy_qty: int = 1000
    _day: int = field(default=0, repr=False)

    def on_bar(self, bars, context):
        self._day += 1
        if self.symbol not in bars:
            return []
        if self._day == 1:
            return [OrderIntent(
                symbol=self.symbol, side=Side.BUY, qty=self.buy_qty,
                intent_type=IntentType.ORDER,
            )]
        if self._day == 3:
            qty = context.positions.get(self.symbol, 0)
            if qty > 0:
                return [OrderIntent(
                    symbol=self.symbol, side=Side.SELL, qty=qty,
                    intent_type=IntentType.ORDER,
                )]
        return []


def _make_engine(strategy, fill_model=None, initial_capital=1_000_000):
    """构建引擎"""
    config = BacktestConfig(initial_capital=float(initial_capital))
    inner_fill = NextBarOpenFillModel()
    fm = fill_model or inner_fill
    fee = FeeModelCNEquity(
        commission=CommissionModel(rate=0.0003, min_fee=5.0),
        stamp_tax=StampTaxModel(sell_rate=0.001),
        transfer_fee=TransferFeeModel(rate=0.00002),
    )
    slippage = FixedBpsSlippageModel(bps=0.0)
    rules = MarketRulesCNEquity()

    return BacktestEngine(
        config=config,
        strategy=strategy,
        fill_model=fm,
        slippage=slippage,
        fee_model=fee,
        rules=rules,
    )


def _bars_3days(symbol="000001.SZ", price=10.0):
    """生成 3 天行情"""
    d1 = date(2024, 1, 2)
    d2 = date(2024, 1, 3)
    d3 = date(2024, 1, 4)
    return {
        d1: {symbol: _bar(symbol, d1, price)},
        d2: {symbol: _bar(symbol, d2, price)},
        d3: {symbol: _bar(symbol, d3, price)},
    }


# ============================================================================
# 测试
# ============================================================================


class TestPartialFillBuy:
    """买单部分成交测试"""

    def test_partial_buy_no_cash_leak(self):
        """部分成交的买单，资金总量守恒（cash + position_value ≈ initial）"""
        inner = NextBarOpenFillModel()
        partial_fm = PartialFillModel(inner, fill_ratio=0.5)

        strategy = BuyOnceStrategy(symbol="000001.SZ", qty=1000)
        engine = _make_engine(strategy, fill_model=partial_fm)

        result = engine.run(_bars_3days())

        # 应该只成交了约500股（50%）
        # 资金守恒：final_nav 应接近 initial
        final_nav = result.final_nav
        initial = 1_000_000.0

        # 允许费用导致的少量差异，但不应有大额泄漏
        assert final_nav > initial * 0.99, (
            f"资金泄漏: nav={final_nav}, initial={initial}"
        )

    def test_partial_buy_position_correct(self):
        """部分成交的买单，持仓数量 = 成交数量（非请求数量）"""
        inner = NextBarOpenFillModel()
        partial_fm = PartialFillModel(inner, fill_ratio=0.5)

        strategy = BuyOnceStrategy(symbol="000001.SZ", qty=1000)
        engine = _make_engine(strategy, fill_model=partial_fm)

        result = engine.run(_bars_3days())

        # 检查所有成交记录
        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        assert len(buy_trades) >= 1
        total_filled = sum(t.qty for t in buy_trades)
        # 应该是 ~500 而非 1000
        assert total_filled < 1000, f"Expected partial fill but got {total_filled}"
        assert total_filled >= 100, f"Expected at least 100 shares but got {total_filled}"

    def test_full_fill_unchanged(self):
        """非部分成交的买单，行为不变"""
        strategy = BuyOnceStrategy(symbol="000001.SZ", qty=1000)
        engine = _make_engine(strategy)

        result = engine.run(_bars_3days())

        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        total_filled = sum(t.qty for t in buy_trades)
        assert total_filled == 1000


class TestPartialFillSell:
    """卖单部分成交测试"""

    def test_partial_sell_position_retained(self):
        """部分成交的卖单后，应保留未卖出部分的持仓"""
        inner = NextBarOpenFillModel()
        # 先用完整成交买入，然后用部分成交卖出
        buy_strategy = BuyThenSellStrategy(symbol="000001.SZ", buy_qty=1000)

        # 自定义 FillModel：只对卖单做部分成交
        class SellOnlyPartialFM(FillModel):
            name = "SellOnlyPartial"
            def __init__(self):
                self._inner = NextBarOpenFillModel()

            def try_fill(self, *, order, next_bar, rules):
                result = self._inner.try_fill(order=order, next_bar=next_bar, rules=rules)
                if result.fill is None or order.side != Side.SELL:
                    return result
                partial_qty = max(100, order.qty // 2 // 100 * 100)
                if partial_qty >= order.qty:
                    return result
                from dataclasses import replace as dc_replace
                partial_fill = dc_replace(result.fill, qty=partial_qty)
                return FillResult(fill=partial_fill, remaining_qty=order.qty - partial_qty)

        engine = _make_engine(buy_strategy, fill_model=SellOnlyPartialFM())
        result = engine.run(_bars_3days())

        # 应有买入成交和部分卖出成交
        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        sell_trades = [t for t in result.trades if t.side == Side.SELL]

        assert len(buy_trades) >= 1
        total_bought = sum(t.qty for t in buy_trades)
        total_sold = sum(t.qty for t in sell_trades)

        # 卖出应少于买入（部分成交）
        if len(sell_trades) > 0:
            assert total_sold < total_bought, (
                f"Expected partial sell: sold={total_sold}, bought={total_bought}"
            )

    def test_partial_sell_no_frozen_qty_leak(self):
        """部分成交后，未成交部分的冻结持仓应被正确释放"""
        class SellOnlyPartialFM(FillModel):
            name = "SellOnlyPartial"
            def __init__(self):
                self._inner = NextBarOpenFillModel()

            def try_fill(self, *, order, next_bar, rules):
                result = self._inner.try_fill(order=order, next_bar=next_bar, rules=rules)
                if result.fill is None or order.side != Side.SELL:
                    return result
                partial_qty = max(100, order.qty // 2 // 100 * 100)
                if partial_qty >= order.qty:
                    return result
                from dataclasses import replace as dc_replace
                partial_fill = dc_replace(result.fill, qty=partial_qty)
                return FillResult(fill=partial_fill, remaining_qty=order.qty - partial_qty)

        strategy = BuyThenSellStrategy(symbol="000001.SZ", buy_qty=1000)
        engine = _make_engine(strategy, fill_model=SellOnlyPartialFM())
        result = engine.run(_bars_3days())

        # 最终持仓应非负（没有负持仓泄漏）
        final_pos = result.final_positions
        for sym, qty in final_pos.items():
            assert qty >= 0, f"{sym} has negative position: {qty}"


class TestPartialFillEdgeCases:
    """边界情况"""

    def test_zero_remaining(self):
        """remaining_qty=0 时行为等同全部成交"""
        strategy = BuyOnceStrategy(symbol="000001.SZ", qty=1000)
        engine = _make_engine(strategy)

        result = engine.run(_bars_3days())
        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        assert sum(t.qty for t in buy_trades) == 1000

    def test_very_small_partial(self):
        """只成交最小 lot（100股）时不崩溃"""
        inner = NextBarOpenFillModel()
        partial_fm = PartialFillModel(inner, fill_ratio=0.1)

        strategy = BuyOnceStrategy(symbol="000001.SZ", qty=1000)
        engine = _make_engine(strategy, fill_model=partial_fm)

        result = engine.run(_bars_3days())
        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        assert len(buy_trades) >= 1
        assert buy_trades[0].qty == 100  # 最小 lot

    def test_no_crash_with_partial_fill_model(self):
        """PartialFillModel 与引擎集成不崩溃"""
        inner = NextBarOpenFillModel()
        for ratio in [0.1, 0.3, 0.5, 0.7, 0.9]:
            partial_fm = PartialFillModel(inner, fill_ratio=ratio)
            strategy = BuyOnceStrategy(symbol="000001.SZ", qty=1000)
            engine = _make_engine(strategy, fill_model=partial_fm)
            result = engine.run(_bars_3days())
            assert result is not None
            assert len(result.daily_snapshots) > 0


# ============================================================================
# OrderState 枚举 + ORDER_CANCELLED 事件
# ============================================================================


class TestOrderStateEnum:
    """测试 OrderState 枚举定义正确"""

    def test_order_state_values(self):
        from aipha.domain.models import OrderState
        assert OrderState.PENDING == "PENDING"
        assert OrderState.PARTIAL_FILLED == "PARTIAL_FILLED"
        assert OrderState.FILLED == "FILLED"
        assert OrderState.CANCELLED == "CANCELLED"

    def test_order_state_transitions(self):
        """验证预期的状态值存在"""
        from aipha.domain.models import OrderState
        states = set(OrderState)
        assert len(states) == 4


class TestOrderCancelledEvent:
    """测试引擎在取消订单时发出 ORDER_CANCELLED 事件"""

    def test_buy_no_quotes_emits_cancel(self):
        """买单标的在 T+1 无行情时应发出 ORDER_CANCELLED 事件"""
        from aipha.backtest.engine import EventType

        # Day1: MISSING.SZ 有行情 → 策略发出买单 → pending order 创建
        # Day2: MISSING.SZ 无行情 → 买单取消
        d1 = date(2024, 1, 2)
        d2 = date(2024, 1, 3)
        d3 = date(2024, 1, 4)
        bars = {
            d1: {
                "MISSING.SZ": _bar("MISSING.SZ", d1, 10.0),
                "000001.SZ": _bar("000001.SZ", d1, 10.0),
            },
            d2: {
                "000001.SZ": _bar("000001.SZ", d2, 10.0),
                # MISSING.SZ 无行情
            },
            d3: {
                "000001.SZ": _bar("000001.SZ", d3, 10.0),
            },
        }
        strategy = BuyOnceStrategy(symbol="MISSING.SZ", qty=100)
        engine = _make_engine(strategy)
        result = engine.run(bars)

        # 检查事件队列中是否有 ORDER_CANCELLED
        events = []
        while True:
            evt = engine._event_queue.pop()
            if evt is None:
                break
            events.append(evt)
        cancel_events = [e for e in events if e.event_type == EventType.ORDER_CANCELLED]
        assert len(cancel_events) >= 1, f"应有至少 1 个 ORDER_CANCELLED 事件，实际事件: {[e.event_type for e in events]}"
        assert cancel_events[0].data["symbol"] == "MISSING.SZ"
        assert "无行情" in cancel_events[0].data["reason"]

    def test_fill_emits_order_filled(self):
        """正常成交时应发出 ORDER_FILLED 事件"""
        from aipha.backtest.engine import EventType

        strategy = BuyOnceStrategy(symbol="000001.SZ", qty=100)
        engine = _make_engine(strategy)
        result = engine.run(_bars_3days())

        events = []
        while True:
            evt = engine._event_queue.pop()
            if evt is None:
                break
            events.append(evt)
        fill_events = [e for e in events if e.event_type == EventType.ORDER_FILLED]
        assert len(fill_events) >= 1, "应有至少 1 个 ORDER_FILLED 事件"
        assert fill_events[0].data["symbol"] == "000001.SZ"


# ============================================================================
# 部分成交重新排队测试
# ============================================================================


def _bars_5days(symbol="000001.SZ", price=10.0):
    """生成 5 天行情，给部分成交留足够的 bar 来重新排队成交"""
    days = [date(2024, 1, d) for d in range(2, 7)]
    return {d: {symbol: _bar(symbol, d, price)} for d in days}


class TestPartialFillRequeue:
    """测试部分成交剩余数量重新排队机制"""

    def test_buy_partial_requeued_eventually_fills(self):
        """买单部分成交后，剩余数量在后续 bar 继续成交"""
        inner = NextBarOpenFillModel()
        # 只在第一次成交 50%，后续重新排队的订单会完整成交
        call_count = 0
        original_try_fill = inner.try_fill

        class OncePartialFM(FillModel):
            """仅首次买单做部分成交，后续全部成交"""
            name = "OncePartial"
            def __init__(self):
                self._inner = NextBarOpenFillModel()
                self._partial_done = False

            def try_fill(self, *, order, next_bar, rules):
                result = self._inner.try_fill(order=order, next_bar=next_bar, rules=rules)
                if result.fill is None or order.side != Side.BUY:
                    return result
                if self._partial_done:
                    return result  # 后续全部成交
                self._partial_done = True
                partial_qty = max(100, order.qty // 2 // 100 * 100)
                if partial_qty >= order.qty:
                    return result
                from dataclasses import replace as dc_replace
                partial_fill = dc_replace(result.fill, qty=partial_qty)
                return FillResult(fill=partial_fill, remaining_qty=order.qty - partial_qty)

        strategy = BuyOnceStrategy(symbol="000001.SZ", qty=1000)
        engine = _make_engine(strategy, fill_model=OncePartialFM())
        result = engine.run(_bars_5days())

        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        total_filled = sum(t.qty for t in buy_trades)
        # 500 首次 + 500 重新排队 = 1000
        assert total_filled == 1000, (
            f"Expected 1000 total filled after requeue, got {total_filled}"
        )
        assert len(buy_trades) == 2, (
            f"Expected 2 buy trades (initial + requeued), got {len(buy_trades)}"
        )

    def test_sell_partial_requeued_eventually_fills(self):
        """卖单部分成交后，剩余数量在后续 bar 继续成交"""

        class SellOncePartialFM(FillModel):
            """仅首次卖单做部分成交"""
            name = "SellOncePartial"
            def __init__(self):
                self._inner = NextBarOpenFillModel()
                self._sell_partial_done = False

            def try_fill(self, *, order, next_bar, rules):
                result = self._inner.try_fill(order=order, next_bar=next_bar, rules=rules)
                if result.fill is None or order.side != Side.SELL:
                    return result
                if self._sell_partial_done:
                    return result
                self._sell_partial_done = True
                partial_qty = max(100, order.qty // 2 // 100 * 100)
                if partial_qty >= order.qty:
                    return result
                from dataclasses import replace as dc_replace
                partial_fill = dc_replace(result.fill, qty=partial_qty)
                return FillResult(fill=partial_fill, remaining_qty=order.qty - partial_qty)

        # 第1天买入1000，第3天卖出（但只成交500），第4天剩余500继续排队成交
        strategy = BuyThenSellStrategy(symbol="000001.SZ", buy_qty=1000)
        engine = _make_engine(strategy, fill_model=SellOncePartialFM())
        result = engine.run(_bars_5days())

        sell_trades = [t for t in result.trades if t.side == Side.SELL]
        total_sold = sum(t.qty for t in sell_trades)
        # 应全部卖出：500 首次 + 500 重新排队
        assert total_sold == 1000, (
            f"Expected 1000 total sold after requeue, got {total_sold}"
        )
        assert len(sell_trades) == 2, (
            f"Expected 2 sell trades (initial + requeued), got {len(sell_trades)}"
        )

    def test_buy_partial_requeue_cash_conservation(self):
        """买单部分成交重新排队后资金总量守恒"""
        class OncePartialBuyFM(FillModel):
            name = "OncePartialBuy"
            def __init__(self):
                self._inner = NextBarOpenFillModel()
                self._done = False

            def try_fill(self, *, order, next_bar, rules):
                result = self._inner.try_fill(order=order, next_bar=next_bar, rules=rules)
                if result.fill is None or order.side != Side.BUY or self._done:
                    return result
                self._done = True
                partial_qty = max(100, order.qty // 2 // 100 * 100)
                if partial_qty >= order.qty:
                    return result
                from dataclasses import replace as dc_replace
                pf = dc_replace(result.fill, qty=partial_qty)
                return FillResult(fill=pf, remaining_qty=order.qty - partial_qty)

        strategy = BuyOnceStrategy(symbol="000001.SZ", qty=1000)
        engine = _make_engine(strategy, fill_model=OncePartialBuyFM(),
                              initial_capital=1_000_000)
        result = engine.run(_bars_5days())

        # NAV 应接近初始资金（价格不变，只有少量费用差异）
        assert result.final_nav > 1_000_000 * 0.995, (
            f"Cash leak after partial fill requeue: NAV={result.final_nav}"
        )

    def test_sell_partial_requeue_no_negative_position(self):
        """卖单部分成交重新排队后不出现负持仓"""
        class SellOncePartialFM(FillModel):
            name = "SellOncePartial"
            def __init__(self):
                self._inner = NextBarOpenFillModel()
                self._done = False

            def try_fill(self, *, order, next_bar, rules):
                result = self._inner.try_fill(order=order, next_bar=next_bar, rules=rules)
                if result.fill is None or order.side != Side.SELL or self._done:
                    return result
                self._done = True
                partial_qty = max(100, order.qty // 2 // 100 * 100)
                if partial_qty >= order.qty:
                    return result
                from dataclasses import replace as dc_replace
                pf = dc_replace(result.fill, qty=partial_qty)
                return FillResult(fill=pf, remaining_qty=order.qty - partial_qty)

        strategy = BuyThenSellStrategy(symbol="000001.SZ", buy_qty=1000)
        engine = _make_engine(strategy, fill_model=SellOncePartialFM())
        result = engine.run(_bars_5days())

        for sym, qty in result.final_positions.items():
            assert qty >= 0, f"{sym} negative position: {qty}"

    def test_multiple_partial_fills_accumulate(self):
        """连续多次部分成交（每次只成交1/3），最终应全部成交"""

        class AlwaysThirdFM(FillModel):
            """每次只成交 1/3 的请求数量"""
            name = "AlwaysThird"
            def __init__(self):
                self._inner = NextBarOpenFillModel()

            def try_fill(self, *, order, next_bar, rules):
                result = self._inner.try_fill(order=order, next_bar=next_bar, rules=rules)
                if result.fill is None or order.side != Side.BUY:
                    return result
                # 成交 1/3（向下取整到 100）
                partial_qty = max(100, order.qty // 3 // 100 * 100)
                if partial_qty >= order.qty:
                    return result
                from dataclasses import replace as dc_replace
                pf = dc_replace(result.fill, qty=partial_qty)
                return FillResult(fill=pf, remaining_qty=order.qty - partial_qty)

        # 需要足够多的 bar 让剩余数量逐步消化
        days = [date(2024, 1, d) for d in range(2, 15)]
        bars = {d: {"000001.SZ": _bar("000001.SZ", d, 10.0)} for d in days}

        strategy = BuyOnceStrategy(symbol="000001.SZ", qty=900)
        engine = _make_engine(strategy, fill_model=AlwaysThirdFM())
        result = engine.run(bars)

        buy_trades = [t for t in result.trades if t.side == Side.BUY]
        total_filled = sum(t.qty for t in buy_trades)
        # 900 → 300+300+300 （通过 3 次 bar 完成）
        # 或 900→300 rem 600→200 rem 400→100 rem 300→100 rem 200→100 rem 100
        # 取决于 1/3 取整，关键是最终应全部成交
        assert total_filled == 900, (
            f"Expected full 900 shares eventually filled, got {total_filled}"
        )
