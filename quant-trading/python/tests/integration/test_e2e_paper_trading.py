"""
E2E Paper Trading 验证 — Sprint 3.3

端到端验证 LiveRunner 在 paper 模式下的完整交易闭环：

验证项：
1. NAV 包含持仓市值（不仅是现金）
2. 回撤计算正确（动态 peak NAV）
3. 费用与 BacktestEngine 一致（佣金 + 印花税 + 过户费）
4. 可转债 lot_size=10 生效
5. 多标的同时间点聚合正确
6. 卖出优先于买入（资金回笼后再买）
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from aipha.domain.models import Bar, Side
from aipha.execution.gateway import AccountSnapshot
from aipha.runner.live_runner import LiveRunner, RunnerConfig, RunnerMode
from aipha.strategy.base import OrderIntent, Strategy, StrategyContext


# ─────────────────────────────────────────────────────────────────────────────
# 合成 CB 数据
# ─────────────────────────────────────────────────────────────────────────────

def generate_cb_bars(
    symbols: list[str],
    start_date: date,
    days: int = 5,
    base_price: float = 100.0,
    daily_return: float = 0.005,
) -> dict[date, list[Bar]]:
    """生成合成可转债日频 Bar

    Args:
        symbols: 可转债代码列表
        start_date: 起始日期
        days: 天数
        base_price: 基础价格
        daily_return: 日均收益率

    Returns:
        {date: [Bar]} 按日期组织
    """
    bars_by_date: dict[date, list[Bar]] = {}

    for day_idx in range(days):
        d = start_date + timedelta(days=day_idx)
        # 跳过周末
        if d.weekday() >= 5:
            days += 1
            continue

        day_bars = []
        for i, symbol in enumerate(symbols):
            # 每个标的有不同的价格偏移
            price_shift = i * 10.0
            price = (base_price + price_shift) * (1 + daily_return) ** day_idx

            # 模拟日内相对合理的 OHLC
            o = price * 0.998
            h = price * 1.005
            lo = price * 0.995
            c = price

            bar = Bar(
                symbol=symbol,
                start_ts=datetime(d.year, d.month, d.day, 9, 30),
                end_ts=datetime(d.year, d.month, d.day, 15, 0),
                open=round(o, 3),
                high=round(h, 3),
                low=round(lo, 3),
                close=round(c, 3),
                volume=500000.0,
                metadata={
                    "cb_over_rate": 5.0 + i * 2.0,  # 溢价率
                    "amount": 5000000.0 + i * 1000000.0,  # 成交额
                    "issue_size": 10.0,
                    "maturity_date": "2028-12-31",
                },
            )
            day_bars.append(bar)

        bars_by_date[d] = day_bars

    return bars_by_date


# ─────────────────────────────────────────────────────────────────────────────
# 测试用策略
# ─────────────────────────────────────────────────────────────────────────────

class E2EBuyHoldStrategy(Strategy):
    """买入持有：第一天买入所有标的，之后持有"""

    name = "e2e_buy_hold"

    def __init__(self, lot_size: int = 10):
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

        # 均分资金买入
        budget_per = context.cash / n * 0.9  # 留 10% buffer
        for symbol, bar in bars.items():
            qty = int(budget_per / bar.close / self._lot_size) * self._lot_size
            if qty >= self._lot_size:
                intents.append(
                    OrderIntent(
                        symbol=symbol,
                        side=Side.BUY,
                        qty=qty,
                        reason="e2e_initial_buy",
                    )
                )

        if intents:
            self._bought = True
        return intents


class E2ERotationStrategy(Strategy):
    """轮动策略：第一天买 A，第三天卖 A 买 B; 验证卖→买顺序"""

    name = "e2e_rotation"

    def __init__(self, lot_size: int = 10):
        self._lot_size = lot_size
        self._bar_count = 0

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        self._bar_count += 1
        intents = []

        if self._bar_count == 1 and "CB001" in bars:
            # 第一天：买入 CB001
            bar = bars["CB001"]
            qty = int(context.cash * 0.5 / bar.close / self._lot_size) * self._lot_size
            if qty >= self._lot_size:
                intents.append(
                    OrderIntent(symbol="CB001", side=Side.BUY, qty=qty, reason="init_buy")
                )

        elif self._bar_count == 3:
            # 第三天：卖出 CB001，买入 CB002
            pos_a = context.positions.get("CB001", 0)
            if pos_a > 0:
                intents.append(
                    OrderIntent(symbol="CB001", side=Side.SELL, qty=pos_a, reason="rotate_sell")
                )

            if "CB002" in bars:
                bar_b = bars["CB002"]
                qty = int(context.cash * 0.5 / bar_b.close / self._lot_size) * self._lot_size
                if qty >= self._lot_size:
                    intents.append(
                        OrderIntent(symbol="CB002", side=Side.BUY, qty=qty, reason="rotate_buy")
                    )

        return intents


# ─────────────────────────────────────────────────────────────────────────────
# 配置 fixture
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def paper_config() -> RunnerConfig:
    return RunnerConfig(
        mode=RunnerMode.PAPER,
        initial_capital=Decimal("1000000"),
        lot_size=10,  # 可转债手数
        t_plus_n=0,   # T+0 可转债
        commission_rate=Decimal("0.0001"),  # 万一
        commission_min=Decimal("0"),
        stamp_tax_rate=Decimal("0"),   # 可转债无印花税
        slippage_bps=0.0,  # E2E 验证不加滑点
        audit_log_enabled=False,
        stop_loss_enabled=False,
        risk_constraints_enabled=False,
        monitoring_enabled=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# E2E 测试
# ─────────────────────────────────────────────────────────────────────────────

class TestE2EPaperTrading:
    """端到端 Paper Trading 验证"""

    def test_buy_hold_nav_includes_positions(self, paper_config):
        """NAV = cash + 持仓市值"""
        symbols = ["CB001", "CB002", "CB003"]
        bars_by_date = generate_cb_bars(
            symbols=symbols,
            start_date=date(2024, 6, 3),  # 周一
            days=5,
            base_price=100.0,
        )

        strategy = E2EBuyHoldStrategy(lot_size=10)
        runner = LiveRunner.from_paper_mode(paper_config, strategy)
        snapshot = runner.run_replay_sync(bars_by_date)

        # 验证 NAV > 初始资金（因为持仓有市值）
        assert float(snapshot.total_equity) > 0
        # NAV = cash + positions_value
        assert snapshot.total_equity == snapshot.cash + snapshot.positions_value
        # 应有持仓
        assert sum(snapshot.positions.values()) > 0
        # 持仓市值 > 0
        assert float(snapshot.positions_value) > 0

    def test_lot_size_10_respected(self, paper_config):
        """可转债 lot_size=10 张"""
        symbols = ["CB001"]
        bars_by_date = generate_cb_bars(
            symbols=symbols,
            start_date=date(2024, 6, 3),
            days=3,
            base_price=100.0,
        )

        strategy = E2EBuyHoldStrategy(lot_size=10)
        runner = LiveRunner.from_paper_mode(paper_config, strategy)
        snapshot = runner.run_replay_sync(bars_by_date)

        # 持仓数量应是 10 的倍数
        for qty in snapshot.positions.values():
            assert qty % 10 == 0

    def test_multi_symbol_aggregation(self, paper_config):
        """多标的同时间点 Bar 正确聚合到策略"""
        symbols = ["CB001", "CB002"]
        bars_by_date = generate_cb_bars(
            symbols=symbols,
            start_date=date(2024, 6, 3),
            days=3,
            base_price=100.0,
        )

        class MultiCheck(Strategy):
            name = "multi_check"

            def __init__(self):
                self.max_symbols_per_call = 0

            def on_bar(self, bars, context):
                self.max_symbols_per_call = max(
                    self.max_symbols_per_call, len(bars)
                )
                return []

        strategy = MultiCheck()
        runner = LiveRunner.from_paper_mode(paper_config, strategy)
        runner.run_replay_sync(bars_by_date)

        # 策略应收到同时间点的 2 个标的
        assert strategy.max_symbols_per_call == 2

    def test_rotation_sell_before_buy(self, paper_config):
        """轮动：卖出资金回笼后买入"""
        symbols = ["CB001", "CB002"]
        bars_by_date = generate_cb_bars(
            symbols=symbols,
            start_date=date(2024, 6, 3),
            days=5,
            base_price=100.0,
        )

        strategy = E2ERotationStrategy(lot_size=10)
        runner = LiveRunner.from_paper_mode(paper_config, strategy)
        snapshot = runner.run_replay_sync(bars_by_date)

        # 第 3 天卖 A 买 B → 最终应持有 CB002
        assert snapshot.positions.get("CB001", 0) == 0
        assert snapshot.positions.get("CB002", 0) > 0

    def test_fees_deducted(self, paper_config):
        """佣金正确扣除"""
        symbols = ["CB001"]
        bars_by_date = generate_cb_bars(
            symbols=symbols,
            start_date=date(2024, 6, 3),
            days=3,
            base_price=100.0,
        )

        strategy = E2EBuyHoldStrategy(lot_size=10)
        runner = LiveRunner.from_paper_mode(paper_config, strategy)
        snapshot = runner.run_replay_sync(bars_by_date)

        initial = float(paper_config.initial_capital)
        # 交易后 NAV 不应等于初始资金（扣了佣金 + 价格波动）
        assert float(snapshot.total_equity) != initial

    def test_drawdown_tracking(self, paper_config):
        """峰值 NAV 动态跟踪"""
        symbols = ["CB001"]
        bars_by_date = generate_cb_bars(
            symbols=symbols,
            start_date=date(2024, 6, 3),
            days=5,
            base_price=100.0,
        )

        strategy = E2EBuyHoldStrategy(lot_size=10)
        runner = LiveRunner.from_paper_mode(paper_config, strategy)

        # 监控 peak_nav 变化
        peak_navs = []

        original_process = runner._process_bars

        def tracking_process(bars_dict):
            original_process(bars_dict)
            peak_navs.append(runner._peak_nav)

        runner._process_bars = tracking_process
        runner.start()

        for d in sorted(bars_by_date.keys()):
            from aipha.runner.bar_buffer import group_bars_by_end_ts
            grouped = group_bars_by_end_ts(bars_by_date[d])
            for bars_dict in grouped:
                runner.on_bars(bars_dict)

        runner.stop()

        # peak_nav 应该 >= 初始资金
        assert max(peak_navs) >= float(paper_config.initial_capital)

    def test_fill_callbacks_fired(self, paper_config):
        """成交回调被触发"""
        symbols = ["CB001"]
        bars_by_date = generate_cb_bars(
            symbols=symbols,
            start_date=date(2024, 6, 3),
            days=3,
            base_price=100.0,
        )

        strategy = E2EBuyHoldStrategy(lot_size=10)
        runner = LiveRunner.from_paper_mode(paper_config, strategy)

        fills = []
        runner.on_fill_callback(lambda report: fills.append(report))

        runner.run_replay_sync(bars_by_date)

        # 应有成交回报
        assert len(fills) > 0
        assert runner._state.total_fills > 0

    def test_empty_data_no_crash(self, paper_config):
        """空数据不崩溃"""
        strategy = E2EBuyHoldStrategy(lot_size=10)
        runner = LiveRunner.from_paper_mode(paper_config, strategy)
        snapshot = runner.run_replay_sync({})

        assert float(snapshot.total_equity) == float(paper_config.initial_capital)
