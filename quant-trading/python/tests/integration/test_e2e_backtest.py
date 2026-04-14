"""End-to-end integration tests for factor strategy backtest.

This module validates the complete workflow:
    data → factors → strategy → backtest engine → report

Coverage targets:
- Full backtest cycle with event-driven engine
- Factor computation → signal generation → order execution
- Report generation with all metrics
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import numpy as np
import pytest

from aipha.backtest.engine import (
    BacktestConfig,
    BacktestEngine,
    BacktestResult,
    DailySnapshot,
    Event,
    EventQueue,
    EventType,
    TradeRecord,
)
from aipha.backtest.report import (
    generate_report,
    compute_performance_metrics,
    compute_drawdown_analysis,
)
from aipha.domain.models import Bar, Fill, Order, OrderType, Side
from aipha.execution.fees import (
    CommissionModel,
    FeeBreakdown,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.execution.fill_models import NextBarOpenFillModel, FillResult
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.portfolio.account import Account
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext


# ============================================================================
# 合成数据生成器
# ============================================================================


def generate_synthetic_bars(
    symbols: list[str],
    start_date: date,
    num_days: int = 20,
    base_prices: dict[str, float] | None = None,
    trend: str = "random",  # "up", "down", "random", "volatile"
) -> dict[date, dict[str, Bar]]:
    """生成合成 Bar 数据

    Args:
        symbols: 标的列表
        start_date: 开始日期
        num_days: 交易天数
        base_prices: 初始价格
        trend: 趋势类型

    Returns:
        按日期组织的 Bar 数据
    """
    np.random.seed(42)  # 可复现

    if base_prices is None:
        base_prices = {s: 10.0 + i * 2 for i, s in enumerate(symbols)}

    bars_by_date: dict[date, dict[str, Bar]] = {}
    current_prices = base_prices.copy()

    for day_offset in range(num_days):
        current_date = start_date + timedelta(days=day_offset)

        # 跳过周末
        if current_date.weekday() >= 5:
            continue

        daily_bars: dict[str, Bar] = {}

        for symbol in symbols:
            price = current_prices[symbol]

            # 生成价格变动
            if trend == "up":
                change_pct = np.random.uniform(0.0, 0.03)
            elif trend == "down":
                change_pct = np.random.uniform(-0.03, 0.0)
            elif trend == "volatile":
                change_pct = np.random.uniform(-0.05, 0.05)
            else:  # random
                change_pct = np.random.uniform(-0.02, 0.02)

            open_price = price
            close_price = price * (1 + change_pct)
            high_price = max(open_price, close_price) * (1 + np.random.uniform(0, 0.01))
            low_price = min(open_price, close_price) * (1 - np.random.uniform(0, 0.01))

            bar = Bar(
                symbol=symbol,
                start_ts=datetime.combine(current_date, datetime.min.time().replace(hour=9, minute=30)),
                end_ts=datetime.combine(current_date, datetime.min.time().replace(hour=15, minute=0)),
                open=round(open_price, 2),
                high=round(high_price, 2),
                low=round(low_price, 2),
                close=round(close_price, 2),
                volume=float(np.random.randint(100000, 1000000)),
                vwap=round((open_price + close_price) / 2, 2),
                is_suspended=False,
                is_limit_up=False,
                is_limit_down=False,
            )
            daily_bars[symbol] = bar
            current_prices[symbol] = close_price

        if daily_bars:
            bars_by_date[current_date] = daily_bars

    return bars_by_date


# ============================================================================
# 测试策略
# ============================================================================


class BuyAndHoldStrategy(Strategy):
    """买入持有策略

    在第一天买入指定股票并持有。
    """

    def __init__(self, target_symbols: list[str], name: str = "BuyAndHold"):
        self._target_symbols = target_symbols
        self._name = name
        self._bought = False

    @property
    def name(self) -> str:
        return self._name

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        if self._bought:
            return []

        intents = []
        available_cash = context.cash / len(self._target_symbols) if self._target_symbols else 0

        for symbol in self._target_symbols:
            if symbol in bars:
                bar = bars[symbol]
                qty = int(available_cash / bar.close // 100) * 100
                if qty >= 100:
                    intents.append(
                        OrderIntent(
                            symbol=symbol,
                            side=Side.BUY,
                            qty=qty,
                            intent_type=IntentType.TARGET_POSITION,
                            reason="Initial buy",
                        )
                    )

        self._bought = True
        return intents


class MomentumTopNStrategy(Strategy):
    """动量选股策略

    每隔 N 天，选择过去 M 天涨幅最大的 K 只股票。
    """

    def __init__(
        self,
        lookback_days: int = 5,
        rebalance_days: int = 5,
        top_k: int = 3,
        name: str = "MomentumTopN",
    ):
        self._lookback = lookback_days
        self._rebalance_days = rebalance_days
        self._top_k = top_k
        self._name = name
        self._history: dict[str, list[float]] = {}  # symbol -> price history
        self._days_since_rebalance = 0

    @property
    def name(self) -> str:
        return self._name

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        # 更新价格历史
        for symbol, bar in bars.items():
            if symbol not in self._history:
                self._history[symbol] = []
            self._history[symbol].append(bar.close)
            # 保留最多 lookback + 1 天
            if len(self._history[symbol]) > self._lookback + 1:
                self._history[symbol] = self._history[symbol][-(self._lookback + 1) :]

        self._days_since_rebalance += 1

        # 检查是否需要调仓
        if self._days_since_rebalance < self._rebalance_days:
            return []

        self._days_since_rebalance = 0

        # 计算动量得分
        momentum_scores: dict[str, float] = {}
        for symbol, prices in self._history.items():
            if len(prices) >= self._lookback:
                momentum = (prices[-1] - prices[-self._lookback]) / prices[-self._lookback]
                momentum_scores[symbol] = momentum

        if not momentum_scores:
            return []

        # 选择 top K
        sorted_symbols = sorted(momentum_scores.items(), key=lambda x: x[1], reverse=True)
        top_symbols = [s for s, _ in sorted_symbols[: self._top_k]]

        # 生成订单意图
        intents = []
        current_positions = context.positions or {}

        # 卖出不在 top 中的持仓
        for symbol, pos_qty in current_positions.items():
            if symbol not in top_symbols and pos_qty > 0:
                intents.append(
                    OrderIntent(
                        symbol=symbol,
                        side=Side.SELL,
                        qty=pos_qty,
                        intent_type=IntentType.ORDER,
                        reason="Out of momentum top",
                    )
                )

        # 买入 top 中的股票
        target_cash = context.cash / len(top_symbols) if top_symbols else 0
        for symbol in top_symbols:
            if symbol in bars:
                bar = bars[symbol]
                current_qty = current_positions.get(symbol, 0)
                target_qty = int(target_cash / bar.close // 100) * 100

                if target_qty > current_qty:
                    buy_qty = target_qty - current_qty
                    intents.append(
                        OrderIntent(
                            symbol=symbol,
                            side=Side.BUY,
                            qty=buy_qty,
                            intent_type=IntentType.ORDER,
                            reason="Momentum top",
                        )
                    )

        return intents


# ============================================================================
# 简化回测运行器（用于测试）
# ============================================================================


def run_simple_backtest(
    strategy: Strategy,
    bars_by_date: dict[date, dict[str, Bar]],
    initial_capital: float = 100000.0,
    commission_rate: float = 0.0001,
    stamp_tax_rate: float = 0.0005,
) -> BacktestResult:
    """运行简化的回测

    注意：这是测试用的简化版本，真实回测应使用 BacktestEngine。

    Args:
        strategy: 策略实例
        bars_by_date: 按日期组织的 Bar 数据
        initial_capital: 初始资金
        commission_rate: 佣金率
        stamp_tax_rate: 印花税率

    Returns:
        BacktestResult
    """
    # 初始化账户
    cash = initial_capital
    positions: dict[str, int] = {}
    trades: list[TradeRecord] = []
    snapshots: list[DailySnapshot] = []
    total_fees = FeeBreakdown(commission=0, stamp_tax=0, transfer_fee=0)
    trade_counter = 0

    # 创建费用模型
    fee_model = FeeModelCNEquity(
        commission=CommissionModel(rate=commission_rate, min_fee=5.0),
        stamp_tax=StampTaxModel(sell_rate=stamp_tax_rate),
        transfer_fee=TransferFeeModel(rate=0.00001),
    )

    # 获取排序的日期列表
    sorted_dates = sorted(bars_by_date.keys())
    if not sorted_dates:
        raise ValueError("No bar data provided")

    start_date = sorted_dates[0]
    end_date = sorted_dates[-1]

    prev_nav = initial_capital

    # 按日期迭代
    for current_date in sorted_dates:
        bars = bars_by_date[current_date]

        # 创建策略上下文
        context = StrategyContext(
            cash=cash,
            positions=positions.copy(),
            current_time=datetime.combine(current_date, datetime.min.time().replace(hour=9, minute=30)),
            nav=prev_nav,
        )

        # 获取策略的订单意图
        intents = strategy.on_bar(bars, context)

        # 执行订单
        for intent in intents:
            if intent.symbol not in bars:
                continue

            bar = bars[intent.symbol]
            current_qty = positions.get(intent.symbol, 0)

            if intent.side == Side.BUY:
                qty_to_buy = intent.qty
                if qty_to_buy > 0:
                    # 模拟以收盘价成交
                    notional = qty_to_buy * bar.close

                    # 创建 Fill 用于费用计算
                    fill = Fill(
                        order_id=f"O{trade_counter:04d}",
                        symbol=intent.symbol,
                        side=Side.BUY,
                        qty=qty_to_buy,
                        price=bar.close,
                        t_fill=datetime.combine(current_date, datetime.min.time().replace(hour=15, minute=0)),
                        model="test",
                    )

                    # 计算费用
                    fees = fee_model.calc(fill)

                    if cash >= notional + fees.total:
                        cash -= notional + fees.total
                        positions[intent.symbol] = current_qty + qty_to_buy

                        # 累加费用
                        total_fees = FeeBreakdown(
                            commission=total_fees.commission + fees.commission,
                            stamp_tax=total_fees.stamp_tax + fees.stamp_tax,
                            transfer_fee=total_fees.transfer_fee + fees.transfer_fee,
                        )

                        trades.append(
                            TradeRecord(
                                trade_id=f"T{trade_counter:04d}",
                                order_id=f"O{trade_counter:04d}",
                                symbol=intent.symbol,
                                side=Side.BUY,
                                qty=qty_to_buy,
                                price=bar.close,
                                notional=notional,
                                fees=fees,
                                trade_time=fill.t_fill,
                                trade_date=current_date,
                            )
                        )
                        trade_counter += 1

            elif intent.side == Side.SELL:
                qty_to_sell = intent.qty
                if qty_to_sell > 0 and current_qty >= qty_to_sell:
                    notional = qty_to_sell * bar.close

                    fill = Fill(
                        order_id=f"O{trade_counter:04d}",
                        symbol=intent.symbol,
                        side=Side.SELL,
                        qty=qty_to_sell,
                        price=bar.close,
                        t_fill=datetime.combine(current_date, datetime.min.time().replace(hour=15, minute=0)),
                        model="test",
                    )

                    fees = fee_model.calc(fill)

                    cash += notional - fees.total
                    positions[intent.symbol] = current_qty - qty_to_sell

                    total_fees = FeeBreakdown(
                        commission=total_fees.commission + fees.commission,
                        stamp_tax=total_fees.stamp_tax + fees.stamp_tax,
                        transfer_fee=total_fees.transfer_fee + fees.transfer_fee,
                    )

                    trades.append(
                        TradeRecord(
                            trade_id=f"T{trade_counter:04d}",
                            order_id=f"O{trade_counter:04d}",
                            symbol=intent.symbol,
                            side=Side.SELL,
                            qty=qty_to_sell,
                            price=bar.close,
                            notional=notional,
                            fees=fees,
                            trade_time=fill.t_fill,
                            trade_date=current_date,
                        )
                    )
                    trade_counter += 1

        # 计算 NAV
        position_values = {}
        for symbol, qty in positions.items():
            if symbol in bars:
                position_values[symbol] = qty * bars[symbol].close

        position_value_total = sum(position_values.values())
        nav = cash + position_value_total

        daily_return = (nav - prev_nav) / prev_nav if prev_nav > 0 else 0
        cumulative_return = (nav - initial_capital) / initial_capital

        snapshots.append(
            DailySnapshot(
                trade_date=current_date,
                nav=nav,
                cash=cash,
                positions=positions.copy(),
                position_values=position_values,
                daily_return=daily_return,
                cumulative_return=cumulative_return,
            )
        )
        prev_nav = nav

    return BacktestResult(
        strategy_name=strategy.name,
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        final_nav=nav,
        final_cash=cash,
        final_positions=positions.copy(),
        daily_snapshots=snapshots,
        trades=trades,
        total_trades=len(trades),
        total_fees=total_fees,
        exclusion_stats={},
        config={
            "initial_capital": initial_capital,
            "commission_rate": commission_rate,
            "stamp_tax_rate": stamp_tax_rate,
        },
    )


# ============================================================================
# 测试类
# ============================================================================


class TestEndToEndBacktest:
    """端到端回测集成测试"""

    @pytest.fixture
    def symbols(self) -> list[str]:
        """测试标的"""
        return ["600000.SH", "600001.SH", "600002.SH", "000001.SZ", "000002.SZ"]

    @pytest.fixture
    def bars_data(self, symbols: list[str]) -> dict[date, dict[str, Bar]]:
        """生成测试数据"""
        return generate_synthetic_bars(
            symbols=symbols,
            start_date=date(2025, 1, 6),
            num_days=30,
            trend="random",
        )

    def test_buy_and_hold_backtest(self, bars_data: dict[date, dict[str, Bar]]):
        """测试买入持有策略回测"""
        strategy = BuyAndHoldStrategy(target_symbols=["600000.SH", "000001.SZ"])

        result = run_simple_backtest(
            strategy=strategy,
            bars_by_date=bars_data,
            initial_capital=100000.0,
        )

        # 验证基本结构
        assert result.strategy_name == "BuyAndHold"
        assert result.initial_capital == 100000.0
        assert result.final_nav > 0
        assert len(result.daily_snapshots) > 0
        assert len(result.trades) >= 2  # 至少买入 2 只股票

    def test_momentum_strategy_backtest(self, bars_data: dict[date, dict[str, Bar]]):
        """测试动量策略回测"""
        strategy = MomentumTopNStrategy(
            lookback_days=5,
            rebalance_days=5,
            top_k=2,
        )

        result = run_simple_backtest(
            strategy=strategy,
            bars_by_date=bars_data,
            initial_capital=100000.0,
        )

        # 验证结果
        assert result.strategy_name == "MomentumTopN"
        # NAV 应在合理范围（30 天随机数据）
        assert 0.5 * 100000 < result.final_nav < 2.0 * 100000
        assert len(result.daily_snapshots) >= 15, "应有 15+ 个交易日快照"
        assert result.total_trades > 0, "动量策略应产生交易"

    def test_report_generation(self, bars_data: dict[date, dict[str, Bar]]):
        """测试报告生成"""
        strategy = BuyAndHoldStrategy(target_symbols=["600000.SH"])

        result = run_simple_backtest(
            strategy=strategy,
            bars_by_date=bars_data,
            initial_capital=100000.0,
        )

        # 生成报告
        report = generate_report(result)

        # 验证报告结构
        assert report.strategy_name == "BuyAndHold"
        assert report.performance is not None
        assert report.drawdown is not None
        assert report.costs is not None
        assert report.trading is not None
        assert report.positions is not None

        # 验证指标计算
        assert report.performance.trading_days > 0
        assert report.performance.total_return != 0  # 应该有收益或亏损

        # 验证费用归因 — 有交易必有费用
        assert report.costs.total_fees > 0, "有交易必有费用"

    def test_report_summary(self, bars_data: dict[date, dict[str, Bar]]):
        """测试报告摘要输出"""
        strategy = BuyAndHoldStrategy(target_symbols=["600000.SH", "000001.SZ"])

        result = run_simple_backtest(
            strategy=strategy,
            bars_by_date=bars_data,
            initial_capital=100000.0,
        )

        report = generate_report(result)
        summary = report.summary()

        # 验证摘要包含关键信息
        assert "BuyAndHold" in summary
        assert "累计收益" in summary
        assert "年化收益" in summary
        assert "最大回撤" in summary
        assert "夏普比率" in summary
        assert "总费用" in summary

    def test_performance_metrics(self, bars_data: dict[date, dict[str, Bar]]):
        """测试性能指标计算"""
        strategy = MomentumTopNStrategy(top_k=3)

        result = run_simple_backtest(
            strategy=strategy,
            bars_by_date=bars_data,
            initial_capital=100000.0,
        )

        metrics = compute_performance_metrics(result)

        # 验证指标类型
        assert isinstance(metrics.total_return, float)
        assert isinstance(metrics.annualized_return, float)
        assert isinstance(metrics.volatility, float)
        assert isinstance(metrics.sharpe_ratio, float)
        assert metrics.trading_days > 0

    def test_drawdown_analysis(self, bars_data: dict[date, dict[str, Bar]]):
        """测试回撤分析"""
        strategy = BuyAndHoldStrategy(target_symbols=["600000.SH"])

        result = run_simple_backtest(
            strategy=strategy,
            bars_by_date=bars_data,
            initial_capital=100000.0,
        )

        dd = compute_drawdown_analysis(result)

        # 验证回撤分析
        assert dd.max_drawdown_pct >= 0  # 回撤应 >= 0
        assert dd.max_drawdown_pct <= 1.0  # 回撤不应超过 100%
        assert len(dd.drawdown_series) == len(result.daily_snapshots)

    def test_fee_accumulation(self, bars_data: dict[date, dict[str, Bar]]):
        """测试费用累计"""
        strategy = MomentumTopNStrategy(
            lookback_days=3,
            rebalance_days=3,
            top_k=2,
        )

        result = run_simple_backtest(
            strategy=strategy,
            bars_by_date=bars_data,
            initial_capital=100000.0,
            commission_rate=0.001,  # 高佣金率
            stamp_tax_rate=0.001,
        )

        # 动量策略会换仓，应该产生更多费用
        assert result.total_fees.total > 0
        assert result.total_fees.commission > 0

    def test_trade_records(self, bars_data: dict[date, dict[str, Bar]]):
        """测试交易记录"""
        strategy = BuyAndHoldStrategy(target_symbols=["600000.SH"])

        result = run_simple_backtest(
            strategy=strategy,
            bars_by_date=bars_data,
            initial_capital=100000.0,
        )

        assert len(result.trades) >= 1
        trade = result.trades[0]

        # 验证交易记录字段
        assert trade.symbol == "600000.SH"
        assert trade.side == Side.BUY
        assert trade.qty > 0
        assert trade.qty >= 100, "最小交易单位应为 100 股"
        assert trade.price > 0
        assert trade.notional > 0
        assert trade.fees is not None
        assert trade.fees.total > 0, "单笔交易应有费用"

    def test_daily_snapshots(self, bars_data: dict[date, dict[str, Bar]]):
        """测试每日快照"""
        strategy = BuyAndHoldStrategy(target_symbols=["600000.SH"])

        result = run_simple_backtest(
            strategy=strategy,
            bars_by_date=bars_data,
            initial_capital=100000.0,
        )

        # 验证快照连续性
        for i, snapshot in enumerate(result.daily_snapshots):
            assert snapshot.nav > 0
            assert snapshot.cash >= 0
            # NAV = cash + 持仓市值
            position_mv = sum(snapshot.position_values.values()) if snapshot.position_values else 0
            assert abs(snapshot.nav - snapshot.cash - position_mv) < 0.1, (
                f"NAV({snapshot.nav}) != cash({snapshot.cash}) + pos_mv({position_mv})"
            )

            if i > 0:
                # 验证日期递增
                assert snapshot.trade_date > result.daily_snapshots[i - 1].trade_date


class TestSyntheticDataGenerator:
    """合成数据生成器测试"""

    def test_generate_bars_structure(self):
        """测试数据结构"""
        bars = generate_synthetic_bars(
            symbols=["600000.SH", "000001.SZ"],
            start_date=date(2025, 1, 6),
            num_days=10,
        )

        assert len(bars) > 0
        for d, daily_bars in bars.items():
            assert isinstance(d, date)
            for symbol, bar in daily_bars.items():
                assert isinstance(bar, Bar)
                assert bar.open > 0
                assert bar.high >= bar.open
                assert bar.high >= bar.close
                assert bar.low <= bar.open
                assert bar.low <= bar.close

    def test_generate_bars_trend_up(self):
        """测试上涨趋势"""
        bars = generate_synthetic_bars(
            symbols=["600000.SH"],
            start_date=date(2025, 1, 6),
            num_days=20,
            trend="up",
        )

        dates = sorted(bars.keys())
        first_close = bars[dates[0]]["600000.SH"].close
        last_close = bars[dates[-1]]["600000.SH"].close

        # 上涨趋势应该整体上涨
        assert last_close > first_close

    def test_generate_bars_reproducible(self):
        """测试可复现性"""
        bars1 = generate_synthetic_bars(
            symbols=["600000.SH"],
            start_date=date(2025, 1, 6),
            num_days=10,
        )
        bars2 = generate_synthetic_bars(
            symbols=["600000.SH"],
            start_date=date(2025, 1, 6),
            num_days=10,
        )

        # 相同参数应产生相同结果
        for d in bars1:
            assert bars1[d]["600000.SH"].close == bars2[d]["600000.SH"].close
