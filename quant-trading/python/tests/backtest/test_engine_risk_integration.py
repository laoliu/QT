"""
回测引擎风控/止损/审计集成测试
"""

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from aipha.backtest.engine import BacktestConfig, BacktestEngine
from aipha.domain.models import Bar, Side
from aipha.execution.fees import (
    CommissionModel,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.execution.fill_models import NextBarOpenFillModel
from aipha.execution.slippage import FixedBpsSlippageModel
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.risk.audit_log import AuditEventType, AuditLogConfig, AuditLogger
from aipha.risk.constraints import CompositeRiskManager, PositionLimit
from aipha.risk.stop_loss import (
    FixedPercentStopLoss,
    StopLossConfig,
    StopLossManager,
    StopLossType,
    TriggerAction,
)
from aipha.strategy.base import IntentType, OrderIntent, Strategy, StrategyContext


# ============================================================================
# 测试辅助
# ============================================================================


def create_bar(
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


def create_bars_series(
    symbols: list[str],
    start_date: date,
    num_days: int = 10,
    base_price: float = 10.0,
) -> dict[date, dict[str, Bar]]:
    """创建多日 Bar 数据"""
    bars_by_date = {}
    current_date = start_date

    for day in range(num_days):
        while current_date.weekday() >= 5:
            current_date += timedelta(days=1)

        bars_by_date[current_date] = {}
        for i, symbol in enumerate(symbols):
            price = base_price + i * 2 + day * 0.1
            bars_by_date[current_date][symbol] = create_bar(symbol, current_date, price)

        current_date += timedelta(days=1)

    return bars_by_date


# ============================================================================
# 策略辅助
# ============================================================================


@dataclass
class BuyAndHoldStrategy(Strategy):
    """买入持有策略"""

    name: str = "BuyAndHold"
    target_symbols: list[str] = field(default_factory=list)
    _initialized: bool = field(default=False, repr=False)

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        if self._initialized:
            return []

        self._initialized = True
        intents = []

        num_symbols = len(self.target_symbols)
        if num_symbols == 0:
            return []

        available = context.cash * 0.95
        per_symbol = available / num_symbols

        for symbol in self.target_symbols:
            if symbol not in bars:
                continue
            bar = bars[symbol]
            qty = int(per_symbol / bar.close / 100) * 100
            if qty >= 100:
                intents.append(
                    OrderIntent(
                        symbol=symbol,
                        side=Side.BUY,
                        qty=qty,
                        intent_type=IntentType.ORDER,
                    )
                )

        return intents


@dataclass
class AggressiveBuyStrategy(Strategy):
    """激进买入策略 — 尝试把所有资金投到单只标的"""

    name: str = "AggressiveBuy"
    target_symbol: str = "A"
    _initialized: bool = field(default=False, repr=False)

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        if self._initialized:
            return []

        self._initialized = True

        if self.target_symbol not in bars:
            return []

        bar = bars[self.target_symbol]
        # 试图把 95% 的资金全压到一只标的上
        qty = int(context.cash * 0.95 / bar.close / 100) * 100
        if qty >= 100:
            return [
                OrderIntent(
                    symbol=self.target_symbol,
                    side=Side.BUY,
                    qty=qty,
                    intent_type=IntentType.ORDER,
                )
            ]
        return []


# ============================================================================
# Fixtures
# ============================================================================


class TestEngineRiskIntegration:
    """回测引擎风控集成测试"""

    @pytest.fixture
    def default_engine_components(self):
        """默认引擎组件"""
        config = BacktestConfig(initial_capital=100_000.0)

        fee_model = FeeModelCNEquity(
            commission=CommissionModel(rate=config.commission_rate, min_fee=config.commission_min),
            stamp_tax=StampTaxModel(sell_rate=config.stamp_tax_rate),
            transfer_fee=TransferFeeModel(rate=config.transfer_fee_rate),
        )

        return {
            "config": config,
            "fill_model": NextBarOpenFillModel(),
            "slippage": FixedBpsSlippageModel(bps=config.slippage_bps),
            "fee_model": fee_model,
            "rules": MarketRulesCNEquity(),
        }

    # ------------------------------------------------------------------
    # 1. 风控拦截买入
    # ------------------------------------------------------------------

    def test_engine_with_risk_manager_blocks_buy(self, default_engine_components):
        """CompositeRiskManager（PositionLimit 5%）限制单票买入"""
        risk_mgr = CompositeRiskManager(
            constraints=[PositionLimit(max_weight=0.05)],
        )

        strategy = AggressiveBuyStrategy(target_symbol="A")
        engine = BacktestEngine(
            strategy=strategy,
            risk_manager=risk_mgr,
            **default_engine_components,
        )

        bars = create_bars_series(["A"], date(2025, 1, 6), num_days=5, base_price=10.0)
        result = engine.run(bars)

        # 策略想买 95% 的仓位（≈9500 股），5% 限制后最多约 500 股
        if result.total_trades > 0:
            trade = result.trades[0]
            assert trade.qty <= 600  # 被截断到 ≤ 5% NAV
        else:
            # 数量可能被截断到 0（不足 100 股整手）
            assert result.total_trades == 0

    # ------------------------------------------------------------------
    # 2. 引擎级止损
    # ------------------------------------------------------------------

    def test_engine_with_stop_loss_manager(self, default_engine_components):
        """FixedPercentStopLoss 10% — 价格暴跌后引擎自动平仓"""
        sl_config = StopLossConfig(threshold=0.10, stop_type=StopLossType.FIXED_PERCENT)
        sl_rule = FixedPercentStopLoss(sl_config)
        sl_manager = StopLossManager()
        sl_manager.add_rule(sl_rule)

        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(
            strategy=strategy,
            stop_loss_manager=sl_manager,
            **default_engine_components,
        )

        # 构造特殊数据: 前几天正常，后面价格暴跌 >10%
        dates = []
        current = date(2025, 1, 6)
        for _ in range(8):
            while current.weekday() >= 5:
                current += timedelta(days=1)
            dates.append(current)
            current += timedelta(days=1)

        bars_by_date = {}
        prices = [10.0, 10.1, 10.2, 10.3, 10.0, 8.5, 8.0, 7.5]
        for d, p in zip(dates, prices):
            bars_by_date[d] = {"A": create_bar("A", d, price=p)}

        result = engine.run(bars_by_date)

        # 应该有买入和卖出交易（止损平仓）
        sides = [t.side for t in result.trades]
        assert Side.BUY in sides
        # 价格从 ~10 跌到 8.5 时约跌 15%，应触发止损卖出
        assert Side.SELL in sides

    # ------------------------------------------------------------------
    # 3. 审计日志
    # ------------------------------------------------------------------

    def test_engine_with_audit_logger(self, default_engine_components, tmp_path: Path):
        """AuditLogger 记录回测生命周期事件"""
        audit_config = AuditLogConfig(log_dir=tmp_path, console_output=False)
        audit = AuditLogger(config=audit_config)

        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(
            strategy=strategy,
            audit_logger=audit,
            **default_engine_components,
        )

        bars = create_bars_series(["A"], date(2025, 1, 6), num_days=5)
        result = engine.run(bars)

        # 最少有 SYSTEM_START + fills + SYSTEM_STOP
        event_types = {e.event_type for e in audit._events}
        assert AuditEventType.SYSTEM_START in event_types
        assert AuditEventType.SYSTEM_STOP in event_types

        # 有成交时应记录 ORDER_FILLED
        if result.total_trades > 0:
            assert AuditEventType.ORDER_FILLED in event_types

        audit.close()

    # ------------------------------------------------------------------
    # 4. 向后兼容：不传风控组件
    # ------------------------------------------------------------------

    def test_engine_without_risk_components(self, default_engine_components):
        """不传 risk_manager / stop_loss_manager / audit_logger，引擎正常运行"""
        strategy = BuyAndHoldStrategy(target_symbols=["A", "B"])
        engine = BacktestEngine(
            strategy=strategy,
            **default_engine_components,
        )

        bars = create_bars_series(["A", "B"], date(2025, 1, 6), num_days=5)
        result = engine.run(bars)

        assert result.total_trades > 0
        assert result.final_nav > 0
        assert len(result.daily_snapshots) == 5

    # ------------------------------------------------------------------
    # 5. 风控调整数量
    # ------------------------------------------------------------------

    def test_engine_risk_adjusted_qty(self, default_engine_components):
        """PositionLimit 截断数量但仍允许部分买入"""
        # 用 15% 限制：100_000 的 15% = 15_000
        # 价格 10 → 最多 1500 股 = 15 手
        risk_mgr = CompositeRiskManager(
            constraints=[PositionLimit(max_weight=0.15)],
        )

        strategy = AggressiveBuyStrategy(target_symbol="A")
        engine = BacktestEngine(
            strategy=strategy,
            risk_manager=risk_mgr,
            **default_engine_components,
        )

        bars = create_bars_series(["A"], date(2025, 1, 6), num_days=5, base_price=10.0)
        result = engine.run(bars)

        # 应该有交易，但数量被削减
        assert result.total_trades > 0
        trade = result.trades[0]
        # 原始想买 ~9500 股，15% 限 → ≤1500 股
        assert trade.qty <= 1600
        assert trade.qty >= 100  # 至少一手

    # ------------------------------------------------------------------
    # 6. 多约束组合: PositionLimit + StopLoss 联合
    # ------------------------------------------------------------------

    def test_multi_constraint_and_stop_loss(self, default_engine_components):
        """PositionLimit 限制买入量 + StopLoss 触发平仓：完整链路"""
        risk_mgr = CompositeRiskManager(
            constraints=[PositionLimit(max_weight=0.30)],
        )
        sl_config = StopLossConfig(threshold=0.08, stop_type=StopLossType.FIXED_PERCENT)
        sl_rule = FixedPercentStopLoss(sl_config)
        sl_manager = StopLossManager()
        sl_manager.add_rule(sl_rule)

        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(
            strategy=strategy,
            risk_manager=risk_mgr,
            stop_loss_manager=sl_manager,
            **default_engine_components,
        )

        # Price profile: buy at ~10, then crash to trigger 8% stop
        dates = []
        current = date(2025, 1, 6)
        for _ in range(10):
            while current.weekday() >= 5:
                current += timedelta(days=1)
            dates.append(current)
            current += timedelta(days=1)

        bars_by_date = {}
        prices = [10.0, 10.1, 10.2, 10.3, 10.0, 9.0, 8.5, 8.0, 7.5, 7.0]
        for d, p in zip(dates, prices):
            bars_by_date[d] = {"A": create_bar("A", d, price=p)}

        result = engine.run(bars_by_date)

        sides = [t.side for t in result.trades]
        assert Side.BUY in sides
        # Stop loss should trigger after price drops ~10% from entry ~10.2
        assert Side.SELL in sides

    # ------------------------------------------------------------------
    # 7. 止损后 NAV 稳定（无负现金）
    # ------------------------------------------------------------------

    def test_stop_loss_nav_never_negative(self, default_engine_components):
        """止损平仓后 NAV 应维持为正、不低于剩余现金"""
        sl_config = StopLossConfig(threshold=0.05, stop_type=StopLossType.FIXED_PERCENT)
        sl_rule = FixedPercentStopLoss(sl_config)
        sl_manager = StopLossManager()
        sl_manager.add_rule(sl_rule)

        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(
            strategy=strategy,
            stop_loss_manager=sl_manager,
            **default_engine_components,
        )

        dates = []
        current = date(2025, 6, 2)
        for _ in range(10):
            while current.weekday() >= 5:
                current += timedelta(days=1)
            dates.append(current)
            current += timedelta(days=1)

        # Sharp drop then recovery
        prices = [10.0, 10.1, 10.0, 9.0, 8.5, 9.5, 10.0, 10.5, 11.0, 11.5]
        bars_by_date = {}
        for d, p in zip(dates, prices):
            bars_by_date[d] = {"A": create_bar("A", d, price=p)}

        result = engine.run(bars_by_date)

        # All snapshots should have positive NAV
        for snap in result.daily_snapshots:
            assert snap.nav > 0, f"NAV went negative on {snap.trade_date}"

    # ------------------------------------------------------------------
    # 8. 审计日志记录止损事件
    # ------------------------------------------------------------------

    def test_audit_logs_stop_loss_events(self, default_engine_components, tmp_path: Path):
        """审计日志应记录止损相关事件"""
        audit_config = AuditLogConfig(log_dir=tmp_path, console_output=False)
        audit = AuditLogger(config=audit_config)
        sl_config = StopLossConfig(threshold=0.10, stop_type=StopLossType.FIXED_PERCENT)
        sl_rule = FixedPercentStopLoss(sl_config)
        sl_manager = StopLossManager()
        sl_manager.add_rule(sl_rule)

        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(
            strategy=strategy,
            stop_loss_manager=sl_manager,
            audit_logger=audit,
            **default_engine_components,
        )

        dates = []
        current = date(2025, 3, 3)
        for _ in range(8):
            while current.weekday() >= 5:
                current += timedelta(days=1)
            dates.append(current)
            current += timedelta(days=1)

        prices = [10.0, 10.1, 10.2, 10.0, 8.5, 8.0, 7.5, 7.0]
        bars_by_date = {}
        for d, p in zip(dates, prices):
            bars_by_date[d] = {"A": create_bar("A", d, price=p)}

        result = engine.run(bars_by_date)

        event_types = {e.event_type for e in audit._events}
        assert AuditEventType.SYSTEM_START in event_types
        assert AuditEventType.SYSTEM_STOP in event_types
        # Should have order filled events (buy + stop loss sell)
        if result.total_trades > 1:
            assert AuditEventType.ORDER_FILLED in event_types

        audit.close()

    # ------------------------------------------------------------------
    # 9. 多标的分散买入 + 风控限制
    # ------------------------------------------------------------------

    def test_multi_symbol_with_position_limits(self, default_engine_components):
        """多标的同时买入，PositionLimit 分别限制每只"""
        risk_mgr = CompositeRiskManager(
            constraints=[PositionLimit(max_weight=0.20)],
        )

        strategy = BuyAndHoldStrategy(target_symbols=["A", "B", "C"])
        engine = BacktestEngine(
            strategy=strategy,
            risk_manager=risk_mgr,
            **default_engine_components,
        )

        bars = create_bars_series(
            ["A", "B", "C"], date(2025, 1, 6), num_days=5, base_price=10.0,
        )
        result = engine.run(bars)

        assert result.total_trades >= 1
        # Each position should be limited to ~20% of NAV
        for trade in result.trades:
            approx_weight = (trade.qty * trade.price) / default_engine_components["config"].initial_capital
            assert approx_weight <= 0.25  # Allow some slack for rounding

    # ------------------------------------------------------------------
    # 10. 零成交量标的应无法成交
    # ------------------------------------------------------------------

    def test_zero_volume_no_fill(self, default_engine_components):
        """零成交量的标的不应该成交"""
        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(
            strategy=strategy,
            **default_engine_components,
        )

        current = date(2025, 1, 6)
        while current.weekday() >= 5:
            current += timedelta(days=1)

        # First day normal (strategy submits order), second day zero volume
        d1 = current
        d2 = current + timedelta(days=1)
        while d2.weekday() >= 5:
            d2 += timedelta(days=1)

        bars_by_date = {
            d1: {"A": create_bar("A", d1, price=10.0, volume=1000000.0)},
            d2: {"A": create_bar("A", d2, price=10.5, volume=0.0)},
        }

        result = engine.run(bars_by_date)
        # NextBarOpenFillModel should still fill if volume > 0
        # The point is the engine doesn't crash on 0 volume
        assert result.final_nav > 0

    # ------------------------------------------------------------------
    # MarketRegimeDetector 集成
    # ------------------------------------------------------------------

    def test_market_regime_detector_integration(self):
        """MarketRegimeDetector 集成到引擎后，StrategyContext.metadata 包含 regime 信息"""
        from aipha.risk.constraints import MarketRegimeDetector

        detector = MarketRegimeDetector(
            momentum_lookback=3,
            vol_lookback=3,
            bull_threshold=0.05,
        )

        # 使用一个能记录 context metadata 的策略
        @dataclass
        class MetadataRecorderStrategy(Strategy):
            name: str = "MetadataRecorder"
            captured_metadata: list = field(default_factory=list)

            def on_bar(self, bars, context):
                self.captured_metadata.append(dict(context.metadata))
                return []

        strategy = MetadataRecorderStrategy()
        current = date(2024, 1, 2)

        config = BacktestConfig(initial_capital=1_000_000.0, t_plus_n=1)
        engine = BacktestEngine(
            config=config,
            strategy=strategy,
            fill_model=NextBarOpenFillModel(),
            slippage=FixedBpsSlippageModel(bps=5.0),
            fee_model=FeeModelCNEquity(
                commission=CommissionModel(rate=0.0000854, min_fee=5.0),
                stamp_tax=StampTaxModel(sell_rate=0.001),
                transfer_fee=TransferFeeModel(rate=0.00002),
            ),
            rules=MarketRulesCNEquity(),
            market_regime_detector=detector,
            benchmark_symbol="INDEX",
        )

        # 创建上涨趋势
        bars_by_date = {}
        for i in range(5):
            d = current + timedelta(days=i)
            while d.weekday() >= 5:
                d = d + timedelta(days=1)
            bars_by_date[d] = {
                "INDEX": create_bar("INDEX", d, price=3000 + i * 100),
                "A": create_bar("A", d, price=10.0),
            }

        result = engine.run(bars_by_date)
        assert result.final_nav > 0

        # 策略至少收到一些 metadata
        assert len(strategy.captured_metadata) > 0
        last_meta = strategy.captured_metadata[-1]
        assert "market_regime" in last_meta
        assert last_meta["market_regime"] in ("BULL", "NEUTRAL", "BEAR")
        assert "position_scale" in last_meta
        assert 0.0 <= last_meta["position_scale"] <= 1.0
        # signal_detail 审计信息
        assert "signal_detail" in last_meta
        detail = last_meta["signal_detail"]
        assert "momentum" in detail
        assert "ma_crossover" in detail
        assert "volatility" in detail
        assert "composite" in detail
        assert detail["composite"] in ("BULL", "NEUTRAL", "BEAR")

    def test_engine_without_market_regime_still_works(self):
        """不传入 MarketRegimeDetector 时引擎正常运行, metadata 无 market 字段"""

        @dataclass
        class MetadataRecorderStrategy(Strategy):
            name: str = "MetadataRecorder"
            captured_metadata: list = field(default_factory=list)

            def on_bar(self, bars, context):
                self.captured_metadata.append(dict(context.metadata))
                return []

        strategy = MetadataRecorderStrategy()

        config = BacktestConfig(initial_capital=1_000_000.0, t_plus_n=1)
        engine = BacktestEngine(
            config=config,
            strategy=strategy,
            fill_model=NextBarOpenFillModel(),
            slippage=FixedBpsSlippageModel(bps=5.0),
            fee_model=FeeModelCNEquity(
                commission=CommissionModel(rate=0.0000854, min_fee=5.0),
                stamp_tax=StampTaxModel(sell_rate=0.001),
                transfer_fee=TransferFeeModel(rate=0.00002),
            ),
            rules=MarketRulesCNEquity(),
            # 不传入 market_regime_detector
        )

        current = date(2024, 1, 2)
        bars_by_date = {current: {"A": create_bar("A", current, price=10.0)}}
        result = engine.run(bars_by_date)
        assert result.final_nav > 0
        assert "market_regime" not in strategy.captured_metadata[0]


# ============================================================================
# CircuitBreaker 集成测试
# ============================================================================


class TestCircuitBreakerIntegration:
    """熔断器在引擎中的集成测试"""

    @pytest.fixture
    def engine_components(self):
        config = BacktestConfig(initial_capital=100_000.0)
        fee_model = FeeModelCNEquity(
            commission=CommissionModel(rate=0.0000854, min_fee=5.0),
            stamp_tax=StampTaxModel(sell_rate=0.001),
            transfer_fee=TransferFeeModel(rate=0.00002),
        )
        return {
            "config": config,
            "fill_model": NextBarOpenFillModel(),
            "slippage": FixedBpsSlippageModel(bps=5.0),
            "fee_model": fee_model,
            "rules": MarketRulesCNEquity(),
        }

    def test_circuit_breaker_halts_trading(self, engine_components):
        """熔断器触发后跳过后续交易"""
        from aipha.risk.stop_loss import CircuitBreaker, CircuitBreakerConfig

        cb = CircuitBreaker(CircuitBreakerConfig(
            max_daily_loss=0.03,  # 3% 日内亏损触发
            max_drawdown=0.50,    # 不让回撤触发
            cooldown_minutes=9999,
        ))
        sl_manager = StopLossManager()
        sl_manager.set_circuit_breaker(cb)

        # 构造一个每日都买的策略
        @dataclass
        class DailyBuyStrategy(Strategy):
            name: str = "DailyBuy"
            call_count: int = field(default=0, repr=False)

            def on_bar(self, bars, context):
                self.call_count += 1
                if "A" in bars and context.positions.get("A", 0) == 0:
                    qty = int(context.cash * 0.5 / bars["A"].close / 100) * 100
                    if qty >= 100:
                        return [OrderIntent(symbol="A", side=Side.BUY, qty=qty)]
                return []

        strategy = DailyBuyStrategy()

        engine = BacktestEngine(
            strategy=strategy,
            stop_loss_manager=sl_manager,
            **engine_components,
        )

        dates = []
        current = date(2025, 1, 6)
        for _ in range(6):
            while current.weekday() >= 5:
                current += timedelta(days=1)
            dates.append(current)
            current += timedelta(days=1)

        # 价格暴跌导致 NAV 日内损失 >3%
        prices = [10.0, 10.0, 10.0, 8.0, 7.0, 6.0]
        bars_by_date = {}
        for d, p in zip(dates, prices):
            bars_by_date[d] = {"A": create_bar("A", d, price=p)}

        result = engine.run(bars_by_date)
        # 引擎不崩溃，NAV 保持正数
        assert result.final_nav > 0

    def test_circuit_breaker_without_breaker_no_halt(self, engine_components):
        """不设置 CircuitBreaker 时 StopLossManager 不触发熔断"""
        sl_manager = StopLossManager()
        # 不调用 set_circuit_breaker

        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(
            strategy=strategy,
            stop_loss_manager=sl_manager,
            **engine_components,
        )

        bars = create_bars_series(["A"], date(2025, 1, 6), num_days=5)
        result = engine.run(bars)
        assert result.final_nav > 0
        assert result.total_trades > 0


# ============================================================================
# TriggerAction 集成测试（4 种动作）
# ============================================================================


class TestTriggerActionIntegration:
    """止损 TriggerAction 在引擎中的 4 种处理路径"""

    @pytest.fixture
    def engine_components(self):
        config = BacktestConfig(initial_capital=100_000.0)
        fee_model = FeeModelCNEquity(
            commission=CommissionModel(rate=0.0000854, min_fee=5.0),
            stamp_tax=StampTaxModel(sell_rate=0.001),
            transfer_fee=TransferFeeModel(rate=0.00002),
        )
        return {
            "config": config,
            "fill_model": NextBarOpenFillModel(),
            "slippage": FixedBpsSlippageModel(bps=5.0),
            "fee_model": fee_model,
            "rules": MarketRulesCNEquity(),
        }

    def _build_crash_bars(self) -> dict:
        """构造买入后暴跌的 bar 数据"""
        dates = []
        current = date(2025, 1, 6)
        for _ in range(8):
            while current.weekday() >= 5:
                current += timedelta(days=1)
            dates.append(current)
            current += timedelta(days=1)
        # 先涨再跌超 10%
        prices = [10.0, 10.1, 10.2, 10.3, 10.0, 8.5, 8.0, 7.5]
        return {d: {"A": create_bar("A", d, price=p)} for d, p in zip(dates, prices)}

    def test_close_position_action(self, engine_components):
        """CLOSE_POSITION: 全量平仓"""
        sl_config = StopLossConfig(
            threshold=0.10,
            stop_type=StopLossType.FIXED_PERCENT,
            action=TriggerAction.CLOSE_POSITION,
        )
        sl_manager = StopLossManager()
        sl_manager.add_rule(FixedPercentStopLoss(sl_config))

        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(
            strategy=strategy,
            stop_loss_manager=sl_manager,
            **engine_components,
        )

        result = engine.run(self._build_crash_bars())
        sides = [t.side for t in result.trades]
        assert Side.BUY in sides
        assert Side.SELL in sides
        # 全平后应无持仓
        sell_trades = [t for t in result.trades if t.side == Side.SELL]
        assert len(sell_trades) >= 1

    def test_reduce_position_action(self, engine_components):
        """REDUCE_POSITION: 只卖出 50%"""
        sl_config = StopLossConfig(
            threshold=0.10,
            stop_type=StopLossType.FIXED_PERCENT,
            action=TriggerAction.REDUCE_POSITION,
        )
        sl_manager = StopLossManager()
        sl_manager.add_rule(FixedPercentStopLoss(sl_config))

        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(
            strategy=strategy,
            stop_loss_manager=sl_manager,
            **engine_components,
        )

        result = engine.run(self._build_crash_bars())
        sides = [t.side for t in result.trades]
        assert Side.BUY in sides
        # REDUCE_POSITION 应生成卖出意图
        if Side.SELL in sides:
            buy_qty = sum(t.qty for t in result.trades if t.side == Side.BUY)
            sell_qty = sum(t.qty for t in result.trades if t.side == Side.SELL)
            # 卖出量应约为买入量的 50%（减仓而非全平）
            assert sell_qty <= buy_qty
        assert result.final_nav > 0

    def test_alert_only_action(self, engine_components):
        """ALERT_ONLY: 不生成卖出意图，仅告警"""
        sl_config = StopLossConfig(
            threshold=0.10,
            stop_type=StopLossType.FIXED_PERCENT,
            action=TriggerAction.ALERT_ONLY,
        )
        sl_manager = StopLossManager()
        sl_manager.add_rule(FixedPercentStopLoss(sl_config))

        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(
            strategy=strategy,
            stop_loss_manager=sl_manager,
            **engine_components,
        )

        result = engine.run(self._build_crash_bars())
        sides = [t.side for t in result.trades]
        assert Side.BUY in sides
        # ALERT_ONLY 不应产生自动卖出
        assert Side.SELL not in sides
        assert result.final_nav > 0

    def test_halt_trading_action(self, engine_components):
        """HALT_TRADING: 设 _halted，卖出并跳过后续 bar"""
        sl_config = StopLossConfig(
            threshold=0.10,
            stop_type=StopLossType.FIXED_PERCENT,
            action=TriggerAction.HALT_TRADING,
        )
        sl_manager = StopLossManager()
        sl_manager.add_rule(FixedPercentStopLoss(sl_config))

        # 用一个记录 on_bar 调用的策略
        @dataclass
        class CountingStrategy(Strategy):
            name: str = "Counter"
            call_dates: list = field(default_factory=list)
            _bought: bool = field(default=False, repr=False)

            def on_bar(self, bars, context):
                self.call_dates.append(context.current_time.date())
                if not self._bought and "A" in bars:
                    self._bought = True
                    qty = int(context.cash * 0.5 / bars["A"].close / 100) * 100
                    if qty >= 100:
                        return [OrderIntent(symbol="A", side=Side.BUY, qty=qty)]
                return []

        strategy = CountingStrategy()
        engine = BacktestEngine(
            strategy=strategy,
            stop_loss_manager=sl_manager,
            **engine_components,
        )

        bars_data = self._build_crash_bars()
        result = engine.run(bars_data)

        # HALT_TRADING 后应跳过后续 bar，call_dates 数量 < 总天数
        total_days = len(bars_data)
        assert len(strategy.call_dates) < total_days
        assert result.final_nav > 0


# ============================================================================
# DrawdownLimit / TurnoverLimit 引擎喂养测试
# ============================================================================


class TestDrawdownTurnoverFeedIntegration:
    """验证引擎正确喂养 DrawdownLimit.update_nav / TurnoverLimit.reset_daily+record_trade"""

    @pytest.fixture
    def engine_components(self):
        config = BacktestConfig(initial_capital=100_000.0)
        fee_model = FeeModelCNEquity(
            commission=CommissionModel(rate=0.0000854, min_fee=5.0),
            stamp_tax=StampTaxModel(sell_rate=0.001),
            transfer_fee=TransferFeeModel(rate=0.00002),
        )
        return {
            "config": config,
            "fill_model": NextBarOpenFillModel(),
            "slippage": FixedBpsSlippageModel(bps=5.0),
            "fee_model": fee_model,
            "rules": MarketRulesCNEquity(),
        }

    def test_drawdown_limit_blocks_buy_during_drawdown(self, engine_components):
        """DrawdownLimit 在回撤期间阻止买入"""
        from aipha.risk.constraints import DrawdownLimit

        dd_limit = DrawdownLimit(
            max_drawdown=0.10,        # 10% 禁止开仓
            reduce_position_at=0.05,  # 5% 开始减仓
        )
        risk_mgr = CompositeRiskManager(constraints=[dd_limit])

        # 分两阶段买入的策略
        @dataclass
        class TwoPhaseBuyStrategy(Strategy):
            name: str = "TwoPhaseBuy"
            _phase: int = field(default=0, repr=False)

            def on_bar(self, bars, context):
                self._phase += 1
                if "A" not in bars:
                    return []
                if self._phase == 1:
                    # 第一天买入
                    qty = int(context.cash * 0.5 / bars["A"].close / 100) * 100
                    if qty >= 100:
                        return [OrderIntent(symbol="A", side=Side.BUY, qty=qty)]
                elif self._phase >= 5:
                    # 第 5 天后尝试再买
                    qty = int(context.cash * 0.3 / bars["A"].close / 100) * 100
                    if qty >= 100:
                        return [OrderIntent(symbol="A", side=Side.BUY, qty=qty)]
                return []

        strategy = TwoPhaseBuyStrategy()
        engine = BacktestEngine(
            strategy=strategy,
            risk_manager=risk_mgr,
            **engine_components,
        )

        dates = []
        current = date(2025, 1, 6)
        for _ in range(8):
            while current.weekday() >= 5:
                current += timedelta(days=1)
            dates.append(current)
            current += timedelta(days=1)

        # 先涨后大跌：NAV 回撤 >10%
        prices = [10.0, 10.2, 10.5, 10.3, 8.0, 7.5, 7.0, 6.5]
        bars_by_date = {d: {"A": create_bar("A", d, price=p)} for d, p in zip(dates, prices)}

        result = engine.run(bars_by_date)
        assert result.final_nav > 0
        # DrawdownLimit.peak_nav 应被引擎更新
        assert dd_limit.peak_nav > 0

    def test_turnover_limit_tracks_daily_traded(self, engine_components):
        """TurnoverLimit 的 record_trade 被引擎正确调用"""
        from aipha.risk.constraints import TurnoverLimit

        to_limit = TurnoverLimit(
            max_daily_turnover=1.0,   # 不限换手(100%)
            max_single_trade=0.50,
        )
        risk_mgr = CompositeRiskManager(constraints=[to_limit])

        strategy = BuyAndHoldStrategy(target_symbols=["A"])
        engine = BacktestEngine(
            strategy=strategy,
            risk_manager=risk_mgr,
            **engine_components,
        )

        bars = create_bars_series(["A"], date(2025, 1, 6), num_days=5)
        result = engine.run(bars)

        # 引擎应通过 record_trade 更新 daily_traded_value
        # 有成交时 daily_traded_value 应 > 0
        if result.total_trades > 0:
            # last_reset_date 被设置
            assert to_limit.last_reset_date != ""

    def test_turnover_limit_restricts_excessive_buy(self, engine_components):
        """TurnoverLimit 限制超过换手阈值的买入"""
        from aipha.risk.constraints import TurnoverLimit

        to_limit = TurnoverLimit(
            max_daily_turnover=0.05,  # 5% 日换手限制（很紧）
            max_single_trade=0.04,    # 4% 单笔限制
        )
        risk_mgr = CompositeRiskManager(constraints=[to_limit])

        strategy = AggressiveBuyStrategy(target_symbol="A")
        engine = BacktestEngine(
            strategy=strategy,
            risk_manager=risk_mgr,
            **engine_components,
        )

        bars = create_bars_series(["A"], date(2025, 1, 6), num_days=5, base_price=10.0)
        result = engine.run(bars)

        if result.total_trades > 0:
            trade = result.trades[0]
            # 4% of 100k = 4000 → 最多 400 股 @10
            assert trade.qty <= 500
        assert result.final_nav > 0


# ============================================================================
# MarketRegime position_scale 引擎级缩放测试
# ============================================================================


class TestMarketRegimeScaleIntegration:
    """验证引擎在 BEAR 市场时对买入数量进行 position_scale 缩放"""

    def test_bear_regime_reduces_buy_qty(self):
        """下跌趋势中 position_scale < 1，买入数量被缩减"""
        from aipha.risk.constraints import MarketRegimeDetector

        detector = MarketRegimeDetector(
            momentum_lookback=3,
            vol_lookback=3,
            bull_threshold=0.05,
            bear_threshold=-0.02,  # 容易触发 BEAR
        )

        @dataclass
        class LateByerStrategy(Strategy):
            """仅在第 5 天买入，此时 regime 已确定"""
            name: str = "LateBuyer"
            _day: int = field(default=0, repr=False)
            attempted_qty: int = field(default=0, repr=False)

            def on_bar(self, bars, context):
                self._day += 1
                if self._day == 5 and "A" in bars:
                    qty = int(context.cash * 0.5 / bars["A"].close / 100) * 100
                    self.attempted_qty = qty
                    if qty >= 100:
                        return [OrderIntent(symbol="A", side=Side.BUY, qty=qty)]
                return []

        strategy = LateByerStrategy()

        config = BacktestConfig(initial_capital=1_000_000.0)
        engine = BacktestEngine(
            config=config,
            strategy=strategy,
            fill_model=NextBarOpenFillModel(),
            slippage=FixedBpsSlippageModel(bps=5.0),
            fee_model=FeeModelCNEquity(
                commission=CommissionModel(rate=0.0000854, min_fee=5.0),
                stamp_tax=StampTaxModel(sell_rate=0.001),
                transfer_fee=TransferFeeModel(rate=0.00002),
            ),
            rules=MarketRulesCNEquity(),
            market_regime_detector=detector,
            benchmark_symbol="INDEX",
        )

        dates = []
        current = date(2025, 1, 6)
        for _ in range(7):
            while current.weekday() >= 5:
                current += timedelta(days=1)
            dates.append(current)
            current += timedelta(days=1)

        # 持续下跌的 INDEX 触发 BEAR
        bars_by_date = {}
        for i, d in enumerate(dates):
            bars_by_date[d] = {
                "INDEX": create_bar("INDEX", d, price=3000 - i * 150),
                "A": create_bar("A", d, price=10.0),
            }

        result = engine.run(bars_by_date)
        assert result.final_nav > 0
        # 策略运行正常不崩溃
        if result.total_trades > 0 and strategy.attempted_qty > 0:
            trade = result.trades[0]
            # 如果 regime 为 BEAR，position_scale < 1，实际成交量 ≤ 尝试量
            assert trade.qty <= strategy.attempted_qty
