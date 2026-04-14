"""
BarBuffer 多标的聚合测试 — Sprint 3.2

测试覆盖：
- BarBuffer 基本聚合
- 窗口齐全触发 flush
- 旧窗口自动 flush
- flush_all / check_timeout
- group_bars_by_end_ts 工具函数
- LiveRunner.on_bars 多标的集成
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from aipha.domain.models import Bar, Side
from aipha.runner.bar_buffer import BarBuffer, group_bars_by_end_ts
from aipha.runner.live_runner import LiveRunner, RunnerConfig, RunnerMode
from aipha.strategy.base import OrderIntent, Strategy, StrategyContext


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def make_bar(
    symbol: str = "000001.SZ",
    price: float = 10.0,
    end_ts: datetime | None = None,
) -> Bar:
    dt = end_ts or datetime(2024, 1, 15, 9, 31)
    return Bar(
        symbol=symbol,
        start_ts=dt - timedelta(minutes=1),
        end_ts=dt,
        open=price, high=price + 0.5, low=price - 0.5, close=price,
        volume=100000.0,
    )


class MultiSymbolStrategy(Strategy):
    """多标的策略：记录收到的 bars_dict"""

    name = "multi_symbol_test"

    def __init__(self):
        self.received_bars: list[dict[str, Bar]] = []

    def on_bar(
        self,
        bars: dict[str, Bar],
        context: StrategyContext,
    ) -> list[OrderIntent]:
        self.received_bars.append(bars.copy())
        return []


class HoldStrategy(Strategy):
    name = "hold"

    def on_bar(self, bars, context):
        return []


# ─────────────────────────────────────────────────────────────────────────────
# BarBuffer 核心测试
# ─────────────────────────────────────────────────────────────────────────────

class TestBarBuffer:

    def test_single_symbol_immediate_flush(self):
        """无 expected_symbols 时，新窗口把旧的刷出"""
        buf = BarBuffer()  # 自动模式
        ts1 = datetime(2024, 1, 15, 9, 31)
        ts2 = datetime(2024, 1, 15, 9, 32)

        flushed = buf.add(make_bar("A", end_ts=ts1))
        assert len(flushed) == 0  # 没有旧窗口

        flushed = buf.add(make_bar("A", end_ts=ts2))
        assert len(flushed) == 1  # ts1 窗口被刷出
        assert "A" in flushed[0]

    def test_multi_symbol_complete(self):
        """期望标的齐全时自动 flush"""
        buf = BarBuffer(symbols=["A", "B"])
        ts = datetime(2024, 1, 15, 9, 31)

        flushed = buf.add(make_bar("A", end_ts=ts))
        assert len(flushed) == 0  # 等 B

        flushed = buf.add(make_bar("B", end_ts=ts))
        assert len(flushed) == 1
        assert set(flushed[0].keys()) == {"A", "B"}

    def test_older_window_auto_flush(self):
        """新窗口到达时旧窗口（不齐全）自动 flush"""
        buf = BarBuffer(symbols=["A", "B"])
        ts1 = datetime(2024, 1, 15, 9, 31)
        ts2 = datetime(2024, 1, 15, 9, 32)

        buf.add(make_bar("A", end_ts=ts1))  # ts1: 只有 A
        flushed = buf.add(make_bar("A", end_ts=ts2))  # 新窗口→ts1 被刷出

        assert len(flushed) == 1
        assert "A" in flushed[0]
        assert flushed[0]["A"].end_ts == ts1

    def test_flush_all(self):
        """强制 flush 全部"""
        buf = BarBuffer(symbols=["A", "B"])
        ts = datetime(2024, 1, 15, 9, 31)

        buf.add(make_bar("A", end_ts=ts))
        # 只有 A，不齐全，仍在 pending

        result = buf.flush_all()
        assert len(result) == 1  # 强制刷出
        assert "A" in result[0]
        assert buf.pending_count == 0

    def test_callback(self):
        """flush 时触发回调"""
        received = []
        buf = BarBuffer(symbols=["A"])
        buf.on_flush(lambda bars: received.append(bars))

        buf.add(make_bar("A"))
        assert len(received) == 1

    def test_clear(self):
        """清空缓冲区"""
        buf = BarBuffer()
        buf.add(make_bar("A"))
        assert buf.pending_count == 1
        buf.clear()
        assert buf.pending_count == 0

    def test_update_symbols(self):
        """动态更新期望标的"""
        buf = BarBuffer(symbols=["A"])
        ts = datetime(2024, 1, 15, 9, 31)

        flushed = buf.add(make_bar("A", end_ts=ts))
        assert len(flushed) == 1  # A 齐全

        buf.update_symbols(["A", "B"])
        flushed = buf.add(make_bar("A", end_ts=datetime(2024, 1, 15, 9, 32)))
        assert len(flushed) == 0  # 需要等 B


# ─────────────────────────────────────────────────────────────────────────────
# group_bars_by_end_ts 工具函数测试
# ─────────────────────────────────────────────────────────────────────────────

class TestGroupBarsByEndTs:

    def test_basic_grouping(self):
        """基本分组"""
        ts1 = datetime(2024, 1, 15, 9, 31)
        ts2 = datetime(2024, 1, 15, 9, 32)

        bars = [
            make_bar("A", end_ts=ts1),
            make_bar("B", end_ts=ts1),
            make_bar("A", end_ts=ts2),
            make_bar("B", end_ts=ts2),
        ]

        grouped = group_bars_by_end_ts(bars)
        assert len(grouped) == 2
        assert set(grouped[0].keys()) == {"A", "B"}
        assert grouped[0]["A"].end_ts == ts1
        assert grouped[1]["A"].end_ts == ts2

    def test_sorted_by_ts(self):
        """按 end_ts 排序"""
        ts1 = datetime(2024, 1, 15, 9, 32)
        ts2 = datetime(2024, 1, 15, 9, 31)  # 早于 ts1

        bars = [
            make_bar("A", end_ts=ts1),
            make_bar("A", end_ts=ts2),
        ]

        grouped = group_bars_by_end_ts(bars)
        assert grouped[0]["A"].end_ts == ts2  # 更早的排前面

    def test_single_symbol(self):
        """单标的"""
        bars = [make_bar("A", end_ts=datetime(2024, 1, 15, 9, 31))]
        grouped = group_bars_by_end_ts(bars)
        assert len(grouped) == 1
        assert "A" in grouped[0]


# ─────────────────────────────────────────────────────────────────────────────
# LiveRunner 多标的集成测试
# ─────────────────────────────────────────────────────────────────────────────

class TestLiveRunnerMultiSymbol:

    @pytest.fixture
    def config(self):
        return RunnerConfig(
            mode=RunnerMode.PAPER,
            initial_capital=Decimal("1000000"),
            commission_rate=Decimal("0.0001"),
            commission_min=Decimal("0"),
            stamp_tax_rate=Decimal("0"),
            audit_log_enabled=False,
            stop_loss_enabled=False,
            risk_constraints_enabled=False,
            monitoring_enabled=False,
        )

    def test_on_bars_multi_symbol(self, config):
        """on_bars 传入多标的 dict"""
        strategy = MultiSymbolStrategy()
        runner = LiveRunner.from_paper_mode(config, strategy)
        runner.start()

        ts = datetime(2024, 1, 15, 9, 31)
        bars_dict = {
            "A": make_bar("A", price=10.0, end_ts=ts),
            "B": make_bar("B", price=20.0, end_ts=ts),
        }
        runner.on_bars(bars_dict)

        assert len(strategy.received_bars) == 1
        assert set(strategy.received_bars[0].keys()) == {"A", "B"}
        runner.stop()

    def test_replay_groups_by_end_ts(self, config):
        """run_replay_sync 按 end_ts 聚合"""
        strategy = MultiSymbolStrategy()
        runner = LiveRunner.from_paper_mode(config, strategy)

        ts1 = datetime(2024, 1, 15, 9, 31)
        ts2 = datetime(2024, 1, 15, 9, 32)

        bars_by_date = {
            date(2024, 1, 15): [
                make_bar("A", price=10.0, end_ts=ts1),
                make_bar("B", price=20.0, end_ts=ts1),
                make_bar("A", price=11.0, end_ts=ts2),
                make_bar("B", price=21.0, end_ts=ts2),
            ]
        }

        runner.run_replay_sync(bars_by_date)

        # 应收到 2 组 bars（每组 2 个标的）
        assert len(strategy.received_bars) == 2
        assert set(strategy.received_bars[0].keys()) == {"A", "B"}
        assert set(strategy.received_bars[1].keys()) == {"A", "B"}

    def test_on_bar_single_still_works(self, config):
        """on_bar 单标的向后兼容"""
        strategy = MultiSymbolStrategy()
        runner = LiveRunner.from_paper_mode(config, strategy)
        runner.start()

        bar = make_bar("A", price=10.0)
        runner.on_bar(bar)

        assert len(strategy.received_bars) == 1
        assert "A" in strategy.received_bars[0]
        runner.stop()

    def test_bars_processed_count(self, config):
        """处理计数反映标的数而非组数"""
        strategy = MultiSymbolStrategy()
        runner = LiveRunner.from_paper_mode(config, strategy)
        runner.start()

        ts = datetime(2024, 1, 15, 9, 31)
        bars_dict = {
            "A": make_bar("A", end_ts=ts),
            "B": make_bar("B", end_ts=ts),
            "C": make_bar("C", end_ts=ts),
        }
        runner.on_bars(bars_dict)

        assert runner._state.total_bars_processed == 3
        runner.stop()
