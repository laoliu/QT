"""停牌折价（suspension discount）功能测试。

验证 BacktestConfig.suspension_discount_* 参数在 NAV 计算中的行为。
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

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
from aipha.portfolio.position import Position
from aipha.strategy.base import OrderIntent, Strategy, StrategyContext


# ---------------------------------------------------------------------------
# 公共引擎工厂
# ---------------------------------------------------------------------------

def _make_engine(strategy=None, suspension_enabled=False,
                 start_days=30, rate=0.001, max_disc=0.30,
                 initial_capital=1_000_000, **extra_cfg):
    """构造带停牌折价配置的回测引擎。"""
    strategy = strategy or HoldStrategy()
    cfg = BacktestConfig(
        initial_capital=initial_capital,
        suspension_discount_enabled=suspension_enabled,
        suspension_discount_start_days=start_days,
        suspension_discount_rate=rate,
        suspension_discount_max=max_disc,
        **extra_cfg,
    )
    return BacktestEngine(
        config=cfg,
        strategy=strategy,
        fill_model=NextBarOpenFillModel(),
        slippage=FixedBpsSlippageModel(0),
        fee_model=FeeModelCNEquity(
            commission=CommissionModel(rate=0.0001, min_fee=5.0),
            stamp_tax=StampTaxModel(sell_rate=0.0005),
            transfer_fee=TransferFeeModel(rate=0.00001),
        ),
        rules=MarketRulesCNEquity(),
    )


def _inject_position(engine: BacktestEngine, symbol: str, qty: int) -> None:
    """向引擎注入持仓（测试辅助）。"""
    engine._position_manager.positions[symbol] = Position(
        symbol=symbol, total_qty=qty,
    )


# ---------------------------------------------------------------------------
# 辅助：最小策略 + Bar 工厂
# ---------------------------------------------------------------------------

class HoldStrategy(Strategy):
    """持有策略：首日买入，之后不做任何操作。"""

    name = "hold_strategy"

    def __init__(self, symbol: str = "000001.SZ", buy_qty: int = 1000):
        self._symbol = symbol
        self._buy_qty = buy_qty
        self._bought = False

    def on_bar(self, bars, context):
        if not self._bought:
            self._bought = True
            return [OrderIntent(
                symbol=self._symbol, side=Side.BUY, qty=self._buy_qty,
                reason="初始买入",
            )]
        return []

    def on_fill(self, symbol, side, qty, price):
        pass

    def on_start(self):
        self._bought = False


def _bar(symbol: str, trade_date: date, price: float,
         is_suspended: bool = False) -> Bar:
    ts = datetime.combine(trade_date, datetime.min.time().replace(hour=15))
    vol = 0 if is_suspended else 1_000_000
    return Bar(
        symbol=symbol,
        start_ts=ts.replace(hour=9, minute=30),
        end_ts=ts,
        open=price, high=price, low=price, close=price,
        volume=vol, is_suspended=is_suspended,
        metadata={"circ_mv": 500_000.0, "amount": 1000 if not is_suspended else 0},
    )


# ---------------------------------------------------------------------------
# 测试
# ---------------------------------------------------------------------------

class TestSuspensionDiscountConfig:
    """配置字段验证。"""

    def test_default_disabled(self):
        cfg = BacktestConfig()
        assert cfg.suspension_discount_enabled is False
        assert cfg.suspension_discount_start_days == 30
        assert cfg.suspension_discount_rate == 0.001
        assert cfg.suspension_discount_max == 0.30

    def test_custom_config(self):
        cfg = BacktestConfig(
            suspension_discount_enabled=True,
            suspension_discount_start_days=20,
            suspension_discount_rate=0.002,
            suspension_discount_max=0.50,
        )
        assert cfg.suspension_discount_enabled is True
        assert cfg.suspension_discount_start_days == 20


class TestSuspendDayCounter:
    """停牌天数计数器单元测试。"""

    def _make_engine(self, **kwargs) -> BacktestEngine:
        return _make_engine(
            suspension_enabled=True,
            start_days=5, rate=0.01, max_disc=0.30,
            **kwargs,
        )

    def test_counter_increments_on_suspend(self):
        """停牌日计数器递增。"""
        engine = self._make_engine()
        engine._initialize()

        # 模拟持仓
        _inject_position(engine, "000001.SZ", 1000)

        d = date(2024, 1, 2)
        bars = {"000001.SZ": _bar("000001.SZ", d, 10.0, is_suspended=True)}
        engine._update_suspend_counters(bars)
        assert engine._suspend_day_counts["000001.SZ"] == 1

        bars2 = {"000001.SZ": _bar("000001.SZ", d + timedelta(days=1), 10.0, is_suspended=True)}
        engine._update_suspend_counters(bars2)
        assert engine._suspend_day_counts["000001.SZ"] == 2

    def test_counter_resets_on_trade(self):
        """复牌后计数器清零。"""
        engine = self._make_engine()
        engine._initialize()
        _inject_position(engine, "000001.SZ", 1000)
        engine._suspend_day_counts["000001.SZ"] = 10

        d = date(2024, 1, 2)
        bars = {"000001.SZ": _bar("000001.SZ", d, 10.0, is_suspended=False)}
        engine._update_suspend_counters(bars)
        assert engine._suspend_day_counts["000001.SZ"] == 0

    def test_counter_removed_when_no_position(self):
        """不再持有的标的从计数器移除。"""
        engine = self._make_engine()
        engine._initialize()
        engine._suspend_day_counts["GONE.SZ"] = 50  # 遗留
        # 无持仓
        d = date(2024, 1, 2)
        bars = {}
        engine._update_suspend_counters(bars)
        assert "GONE.SZ" not in engine._suspend_day_counts


class TestSuspensionDiscountCalc:
    """折价率计算逻辑。"""

    def _make_engine(self, start_days=5, rate=0.01, max_disc=0.30):
        engine = _make_engine(
            suspension_enabled=True,
            start_days=start_days, rate=rate, max_disc=max_disc,
        )
        engine._initialize()
        return engine

    def test_no_discount_within_threshold(self):
        """停牌天数 <= start_days 时无折价。"""
        engine = self._make_engine(start_days=30)
        engine._suspend_day_counts["X.SZ"] = 30
        assert engine._suspension_discount("X.SZ") == 0.0

    def test_discount_after_threshold(self):
        """停牌天数 > start_days 时开始折价。"""
        engine = self._make_engine(start_days=5, rate=0.01)
        engine._suspend_day_counts["X.SZ"] = 10  # 超过 5 天
        expected = (10 - 5) * 0.01  # 5% discount
        assert abs(engine._suspension_discount("X.SZ") - expected) < 1e-10

    def test_discount_capped_at_max(self):
        """折价率不超过 max。"""
        engine = self._make_engine(start_days=5, rate=0.01, max_disc=0.10)
        engine._suspend_day_counts["X.SZ"] = 100  # 远超阈值
        assert engine._suspension_discount("X.SZ") == 0.10

    def test_discount_disabled_returns_zero(self):
        """未启用时折价为 0。"""
        engine = _make_engine(suspension_enabled=False)
        engine._initialize()
        engine._suspend_day_counts["X.SZ"] = 100
        assert engine._suspension_discount("X.SZ") == 0.0

    def test_unknown_symbol_zero_discount(self):
        """未在计数器中的标的无折价。"""
        engine = self._make_engine()
        assert engine._suspension_discount("UNKNOWN.SZ") == 0.0


class TestNAVWithSuspensionDiscount:
    """NAV 计算集成测试。"""

    def test_nav_discounted_for_long_suspend(self):
        """长停牌后 NAV 应低于无折价时的 NAV。"""
        engine = _make_engine(
            suspension_enabled=True,
            start_days=5, rate=0.02, max_disc=0.50,
        )
        engine._initialize()

        # 模拟：持有 1000 股，价格 10 元，连续停牌 15 天
        _inject_position(engine, "000001.SZ", 1000)
        engine._suspend_day_counts["000001.SZ"] = 15  # 超过 5 天
        engine._last_prices["000001.SZ"] = 10.0

        prices = {"000001.SZ": 10.0}
        nav = engine._calculate_nav(prices)

        # 折价率 = (15-5) * 0.02 = 0.20
        expected_position_value = 1000 * 10.0 * (1 - 0.20)
        expected_nav = 1_000_000 + expected_position_value  # cash + discounted position
        assert abs(nav - expected_nav) < 1.0

    def test_nav_no_discount_when_disabled(self):
        """未启用折价时 NAV 使用原始价格。"""
        engine = _make_engine(suspension_enabled=False)
        engine._initialize()

        _inject_position(engine, "000001.SZ", 1000)
        engine._suspend_day_counts["000001.SZ"] = 100
        engine._last_prices["000001.SZ"] = 10.0

        nav = engine._calculate_nav({"000001.SZ": 10.0})
        expected = 1_000_000 + 1000 * 10.0
        assert abs(nav - expected) < 0.01

    def test_apply_suspension_discounts_modifies_prices(self):
        """_apply_suspension_discounts 应修改价格字典。"""
        engine = _make_engine(
            suspension_enabled=True,
            start_days=5, rate=0.01, max_disc=0.30,
        )
        engine._initialize()
        _inject_position(engine, "A.SZ", 100)
        engine._suspend_day_counts["A.SZ"] = 10  # discount = 5 * 0.01 = 0.05

        prices = {"A.SZ": 100.0}
        engine._apply_suspension_discounts(prices)
        assert abs(prices["A.SZ"] - 95.0) < 0.01  # 100 * (1-0.05) = 95


class TestSuspensionEndToEnd:
    """端到端回测中停牌折价的效果。"""

    def test_e2e_suspension_discount_reduces_nav(self):
        """带停牌折价的回测 NAV 应低于无折价的回测 NAV。"""
        symbol = "000001.SZ"
        start = date(2024, 1, 2)
        
        # 构造数据：第1日正常（买入），第2日正常（成交），3-40日停牌
        bars_by_date = {}
        for i in range(40):
            d = start + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            if i <= 1:
                bars_by_date[d] = {symbol: _bar(symbol, d, 10.0, is_suspended=False)}
            else:
                bars_by_date[d] = {symbol: _bar(symbol, d, 10.0, is_suspended=True)}

        # 回测 1：无折价
        r1 = _make_engine(
            strategy=HoldStrategy(symbol, 1000),
            suspension_enabled=False,
        ).run(bars_by_date)

        # 回测 2：有折价
        r2 = _make_engine(
            strategy=HoldStrategy(symbol, 1000),
            suspension_enabled=True,
            start_days=5, rate=0.005, max_disc=0.30,
        ).run(bars_by_date)

        # 无折价的最终 NAV 应 >= 有折价的
        assert r1.final_nav >= r2.final_nav
        # 有折价的 NAV 应明显低于初始资金（因为持仓被折价了）
        # 长停牌超过 5 天后开始折价
        if len(r2.daily_snapshots) > 10:
            assert r2.final_nav < r1.final_nav
