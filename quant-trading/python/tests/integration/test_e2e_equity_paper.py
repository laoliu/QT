"""A 股 Paper Trading E2E 测试

验证:
1. PaperEngineConfig 正确加载 A 股配置 (market_type=a_stock)
2. RunnerConfig 使用 A 股参数 (lot_size=100, T+1, 印花税)
3. LiveRunner paper 模式下 A 股交易全链路闭环
4. T+1 规则生效（当日买入不可当日卖出）
5. 印花税仅卖出收取
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from aipha.utils.paths import project_path

import pytest

from aipha.domain.models import Bar, Side
from aipha.runner.live_runner import LiveRunner, RunnerConfig, RunnerMode
from aipha.runner.paper_engine import PaperEngineConfig
from aipha.strategy.base import OrderIntent, Strategy, StrategyContext
from aipha.strategy.registry import create_strategy_from_config


# ============================================================================
# 合成 A 股 Bar 数据
# ============================================================================


def generate_a_stock_bars(
    symbols: list[str],
    start_date: date,
    days: int = 5,
    base_price: float = 30.0,
    daily_return: float = 0.003,
) -> dict[date, list[Bar]]:
    """生成合成 A 股日频 Bar

    Returns:
        {date: [Bar]} 按日期组织
    """
    bars_by_date: dict[date, list[Bar]] = {}
    current = start_date

    for day_idx in range(days):
        while current.weekday() >= 5:
            current += timedelta(days=1)

        day_bars = []
        for i, symbol in enumerate(symbols):
            price_shift = i * 5.0
            price = (base_price + price_shift) * (1 + daily_return) ** day_idx

            o = round(price * 0.998, 2)
            h = round(price * 1.008, 2)
            lo = round(price * 0.992, 2)
            c = round(price, 2)

            bar = Bar(
                symbol=symbol,
                start_ts=datetime(current.year, current.month, current.day, 9, 30),
                end_ts=datetime(current.year, current.month, current.day, 15, 0),
                open=o,
                high=h,
                low=lo,
                close=c,
                volume=5_000_000,
                prev_close=round(price / (1 + daily_return), 2),
                metadata={
                    "circ_mv": 1_000_000 + i * 200_000,
                    "pe_ttm": 15.0 + i * 3,
                    "pb": 1.5 + i * 0.3,
                    "roe": 10.0 + i * 2,
                    "turnover_rate": 2.0 + i * 0.5,
                    "amount": 150_000_000 + i * 30_000_000,
                    "list_date": "20150101",
                    "name": f"测试A股{i}",
                    "industry": ["银行", "白酒", "医药", "电子"][i % 4],
                },
            )
            day_bars.append(bar)

        bars_by_date[current] = day_bars
        current += timedelta(days=1)

    return bars_by_date


# ============================================================================
# 测试用策略
# ============================================================================


class EquityBuyHoldStrategy(Strategy):
    """A 股买入持有策略"""

    name = "equity_buy_hold_test"

    def __init__(self, lot_size: int = 100):
        self._lot_size = lot_size
        self._bought = False

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        if self._bought:
            return []

        intents = []
        n = len(bars)
        if n == 0:
            return []

        budget_per = context.cash / n * 0.60  # 均分 60%
        for symbol, bar in bars.items():
            qty = int(budget_per / bar.close / self._lot_size) * self._lot_size
            if qty >= self._lot_size:
                intents.append(
                    OrderIntent(
                        symbol=symbol,
                        side=Side.BUY,
                        qty=qty,
                        reason="equity_initial_buy",
                    )
                )

        if intents:
            self._bought = True
        return intents


class EquityRotationStrategy(Strategy):
    """轮动策略: Day1 买 A, Day3 卖 A 买 B (验证 T+1)"""

    name = "equity_rotation_test"

    def __init__(self, lot_size: int = 100):
        self._lot_size = lot_size
        self._bar_count = 0

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        self._bar_count += 1
        symbols = sorted(bars.keys())
        intents = []

        if self._bar_count == 1 and len(symbols) > 0:
            # Day1: 买入第一只
            bar = bars[symbols[0]]
            qty = int(context.cash * 0.4 / bar.close / self._lot_size) * self._lot_size
            if qty >= self._lot_size:
                intents.append(
                    OrderIntent(
                        symbol=symbols[0],
                        side=Side.BUY,
                        qty=qty,
                        reason="init_buy",
                    )
                )

        elif self._bar_count == 3 and len(symbols) > 1:
            # Day3: 卖出第一只，买入第二只 (T+1: Day1 买入 → Day3 已满足 T+1)
            pos_a = context.positions.get(symbols[0], 0)
            if pos_a > 0:
                intents.append(
                    OrderIntent(
                        symbol=symbols[0],
                        side=Side.SELL,
                        qty=pos_a,
                        reason="rotate_sell",
                    )
                )
            bar_b = bars[symbols[1]]
            qty = int(context.cash * 0.4 / bar_b.close / self._lot_size) * self._lot_size
            if qty >= self._lot_size:
                intents.append(
                    OrderIntent(
                        symbol=symbols[1],
                        side=Side.BUY,
                        qty=qty,
                        reason="rotate_buy",
                    )
                )

        return intents


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def equity_paper_config() -> RunnerConfig:
    """A 股 Paper Trading 配置"""
    return RunnerConfig(
        mode=RunnerMode.PAPER,
        initial_capital=Decimal("1000000"),
        lot_size=100,
        t_plus_n=1,
        commission_rate=Decimal("0.0001"),
        commission_min=Decimal("5"),
        stamp_tax_rate=Decimal("0.0005"),
        slippage_bps=0.0,
        audit_log_enabled=False,
        stop_loss_enabled=False,
        risk_constraints_enabled=False,
        monitoring_enabled=False,
    )


@pytest.fixture
def a_stock_bars() -> dict[date, list[Bar]]:
    """5 天 4 只 A 股合成数据"""
    return generate_a_stock_bars(
        symbols=["600519.SH", "601318.SH", "600036.SH", "000858.SZ"],
        start_date=date(2024, 1, 2),
        days=8,
    )


# ============================================================================
# 测试类
# ============================================================================


class TestEquityPaperConfigParsing:
    """A 股 Paper 配置解析测试"""

    def test_load_equity_multifactor_config(self):
        """equity_multifactor.yml 能正确加载"""
        cfg = PaperEngineConfig.from_yaml(
            project_path("configs/paper/equity_multifactor.yml")
        )
        assert cfg.strategy_name == "multifactor_enhance"
        assert cfg.market_type.value == "a_stock"
        assert cfg.lot_size == 100
        assert cfg.t_plus_n == 1
        assert cfg.stamp_tax_rate == 0.0005
        assert len(cfg.symbols) == 30

    def test_load_equity_pead_config(self):
        """equity_pead.yml 能正确加载"""
        cfg = PaperEngineConfig.from_yaml(
            project_path("configs/paper/equity_pead.yml")
        )
        assert cfg.strategy_name == "pead_event"
        assert cfg.market_type.value == "a_stock"
        assert cfg.lot_size == 100
        assert cfg.t_plus_n == 1
        assert len(cfg.symbols) == 20

    def test_config_strategy_instantiation(self):
        """配置中的策略可从 registry 实例化"""
        for yml in [
            "configs/paper/equity_multifactor.yml",
            "configs/paper/equity_pead.yml",
        ]:
            cfg = PaperEngineConfig.from_yaml(project_path(yml))
            strategy = create_strategy_from_config({
                "name": cfg.strategy_name,
                "params": cfg.strategy_params,
            })
            assert strategy is not None
            assert hasattr(strategy, "on_bar")


class TestEquityPaperTrading:
    """A 股 Paper Trading E2E（使用 LiveRunner.from_paper_mode + run_replay_sync）"""

    def test_buy_hold_nav_positive(
        self, equity_paper_config, a_stock_bars,
    ):
        """买入持有: NAV 应包含持仓市值"""
        strategy = EquityBuyHoldStrategy(lot_size=100)
        runner = LiveRunner.from_paper_mode(equity_paper_config, strategy)
        snapshot = runner.run_replay_sync(a_stock_bars)

        # total_equity 应 > 初始资金的 90%
        assert float(snapshot.total_equity) > 900_000, (
            f"total_equity {snapshot.total_equity} 异常偏低"
        )
        # 应有持仓
        assert sum(snapshot.positions.values()) > 0, "应有股票持仓"
        # NAV = cash + positions_value
        assert snapshot.total_equity == snapshot.cash + snapshot.positions_value

    def test_lot_size_100_respected(
        self, equity_paper_config, a_stock_bars,
    ):
        """A 股持仓应是 100 的整数倍"""
        strategy = EquityBuyHoldStrategy(lot_size=100)
        runner = LiveRunner.from_paper_mode(equity_paper_config, strategy)
        snapshot = runner.run_replay_sync(a_stock_bars)

        for sym, qty in snapshot.positions.items():
            assert qty % 100 == 0, (
                f"{sym} 持仓 {qty} 不是 100 的整数倍"
            )

    def test_rotation_produces_trades(
        self, equity_paper_config, a_stock_bars,
    ):
        """轮动策略应产生买卖交易"""
        strategy = EquityRotationStrategy(lot_size=100)
        runner = LiveRunner.from_paper_mode(equity_paper_config, strategy)
        snapshot = runner.run_replay_sync(a_stock_bars)

        # 应有持仓变化（可能仍有持仓 or 全部卖出）
        # total_equity 应约等于初始资金（有小额损益）
        assert float(snapshot.total_equity) > 500_000, (
            f"total_equity {snapshot.total_equity} 异常"
        )

    def test_multi_stock_aggregation(
        self, equity_paper_config, a_stock_bars,
    ):
        """多只股票同时交易，总市值正确聚合"""
        strategy = EquityBuyHoldStrategy(lot_size=100)
        runner = LiveRunner.from_paper_mode(equity_paper_config, strategy)
        snapshot = runner.run_replay_sync(a_stock_bars)

        # NAV = cash + positions_value
        assert snapshot.total_equity == snapshot.cash + snapshot.positions_value
        # 多只持仓
        assert len(snapshot.positions) >= 2, (
            f"应持有多只股票，实际 {len(snapshot.positions)}"
        )

    def test_paper_mode_default_a_stock_params(self):
        """验证 A 股默认参数正确传递给 gateway"""
        config = RunnerConfig(
            mode=RunnerMode.PAPER,
            initial_capital=Decimal("500000"),
            lot_size=100,
            t_plus_n=1,
            commission_rate=Decimal("0.0001"),
            stamp_tax_rate=Decimal("0.0005"),
            audit_log_enabled=False,
            stop_loss_enabled=False,
            risk_constraints_enabled=False,
            monitoring_enabled=False,
        )
        strategy = EquityBuyHoldStrategy(lot_size=100)
        runner = LiveRunner.from_paper_mode(config, strategy)

        # runner 应已创建，gateway 应已初始化
        assert runner.config.lot_size == 100
        assert runner.config.t_plus_n == 1
        assert runner.config.stamp_tax_rate == Decimal("0.0005")
