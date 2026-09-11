"""基准相对指标（IR/TE/Active Return）测试

覆盖：
- compute_benchmark_relative() 函数
- BenchmarkRelativeMetrics 数据结构
- generate_report() 集成基准指标
- BacktestReport.summary() 输出基准指标
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from aipha.backtest.report import (
    BenchmarkRelativeMetrics,
    compute_benchmark_relative,
    generate_report,
)
from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

# 年化因子走 §13.8 的唯一标准值 sqrt(244)（A 股实际交易日，见
# aipha/market/trading_calendar_constants.py:20），不是美股惯例的 252。
# 本文件原先手算时写死 252，于是 tracking_error / active_return 两条
# 公式测试恒偏 sqrt(252/244) 倍 —— 偏差来自测试，不是 report.py。


# ============================================================================
# compute_benchmark_relative 核心逻辑
# ============================================================================


class TestComputeBenchmarkRelative:
    """compute_benchmark_relative 函数测试"""

    def test_returns_none_if_too_short(self) -> None:
        """数据不足 5 日时返回 None"""
        result = compute_benchmark_relative([0.01, 0.02], [0.005, 0.01])
        assert result is None

    def test_basic_outperformance(self) -> None:
        """策略持续跑赢基准 → 正 active_return 和正 IR"""
        import random
        random.seed(42)
        strat = [0.01 + random.gauss(0, 0.005) for _ in range(50)]
        bench = [0.005 + random.gauss(0, 0.003) for _ in range(50)]
        result = compute_benchmark_relative(strat, bench, "沪深300")
        assert result is not None
        assert result.active_return > 0
        assert result.information_ratio > 0
        assert result.tracking_error > 0
        assert result.benchmark_name == "沪深300"

    def test_zero_active_return(self) -> None:
        """策略与基准完全一致 → IR = 0"""
        rets = [0.01, -0.005, 0.003, 0.002, -0.001, 0.008]
        result = compute_benchmark_relative(rets, rets)
        assert result is not None
        assert abs(result.active_return) < 1e-10
        assert abs(result.information_ratio) < 1e-10
        assert result.tracking_error < 1e-10

    def test_underperformance(self) -> None:
        """策略持续跑输基准 → 负 IR"""
        import random
        random.seed(99)
        strat = [0.001 + random.gauss(0, 0.003) for _ in range(30)]
        bench = [0.01 + random.gauss(0, 0.003) for _ in range(30)]
        result = compute_benchmark_relative(strat, bench)
        assert result is not None
        assert result.active_return < 0
        assert result.information_ratio < 0

    def test_tracking_error_formula(self) -> None:
        """跟踪误差 = std(active_rets) * sqrt(TRADING_DAYS_PER_YEAR)"""
        strat = [0.02, -0.01, 0.015, -0.005, 0.01]
        bench = [0.01, 0.005, -0.003, 0.002, 0.008]
        result = compute_benchmark_relative(strat, bench)
        assert result is not None

        # 手动计算
        active = [s - b for s, b in zip(strat, bench)]
        mean_a = sum(active) / len(active)
        var_a = sum((r - mean_a) ** 2 for r in active) / (len(active) - 1)
        expected_te = var_a ** 0.5 * TRADING_DAYS_PER_YEAR ** 0.5

        assert abs(result.tracking_error - expected_te) < 1e-10

    def test_active_max_drawdown(self) -> None:
        """超额收益最大回撤测试"""
        # 先赢后输：超额 +1%, +1%, -3%, -2% → peak=2%, then drops
        strat = [0.02, 0.02, -0.02, -0.01, 0.00]
        bench = [0.01, 0.01, 0.01, 0.01, 0.00]
        result = compute_benchmark_relative(strat, bench)
        assert result is not None
        assert result.active_max_drawdown > 0

    def test_mismatched_lengths_uses_min(self) -> None:
        """长度不一致时使用较短的"""
        strat = [0.01] * 10
        bench = [0.005] * 7
        result = compute_benchmark_relative(strat, bench)
        assert result is not None
        # 应该使用 7 天数据
        active = [0.005] * 7
        mean_a = sum(active) / 7
        expected_ar = mean_a * TRADING_DAYS_PER_YEAR
        assert abs(result.active_return - expected_ar) < 1e-10


# ============================================================================
# BenchmarkRelativeMetrics 数据结构
# ============================================================================


class TestBenchmarkRelativeMetrics:
    """BenchmarkRelativeMetrics 结构测试"""

    def test_to_dict(self) -> None:
        bm = BenchmarkRelativeMetrics(
            active_return=0.123456,
            tracking_error=0.05678,
            information_ratio=2.1234,
            active_max_drawdown=0.08765,
            benchmark_name="中证500",
        )
        d = bm.to_dict()
        assert d["benchmark_name"] == "中证500"
        assert isinstance(d["active_return"], float)
        assert isinstance(d["information_ratio"], float)

    def test_frozen(self) -> None:
        """frozen=True → 不可修改"""
        bm = BenchmarkRelativeMetrics(
            active_return=0.1, tracking_error=0.05,
            information_ratio=2.0, active_max_drawdown=0.03,
        )
        with pytest.raises(AttributeError):
            bm.active_return = 0.2  # type: ignore[misc]


# ============================================================================
# generate_report 集成
# ============================================================================


class TestGenerateReportBenchmark:
    """generate_report() 集成基准指标测试"""

    @pytest.fixture
    def _mock_result(self):
        """构造最小 BacktestResult"""
        from aipha.backtest.engine import BacktestResult, DailySnapshot
        from aipha.execution.fees import FeeBreakdown

        base = date(2024, 1, 2)
        snapshots = []
        nav = 1_000_000.0
        for i in range(60):
            d = base + timedelta(days=i)
            daily_ret = 0.005 if i > 0 else 0.0
            nav *= (1 + daily_ret)
            snapshots.append(DailySnapshot(
                trade_date=d,
                nav=nav,
                cash=nav * 0.1,
                positions={"000001.SZ": 1000},
                position_values={"000001.SZ": nav * 0.9},
                daily_return=daily_ret,
                cumulative_return=nav / 1_000_000 - 1,
            ))

        return BacktestResult(
            strategy_name="test_bench",
            start_date=base,
            end_date=base + timedelta(days=59),
            initial_capital=1_000_000.0,
            final_nav=nav,
            final_cash=nav * 0.1,
            final_positions={"000001.SZ": 1000},
            daily_snapshots=snapshots,
            trades=[],
            total_trades=0,
            total_fees=FeeBreakdown(0, 0, 0),
            exclusion_stats={},
        )

    def test_without_benchmark(self, _mock_result) -> None:
        """不传基准 → benchmark_relative 为 None"""
        report = generate_report(_mock_result)
        assert report.benchmark_relative is None

    def test_with_benchmark(self, _mock_result) -> None:
        """传入基准收益率 → benchmark_relative 有值"""
        bench_rets = [0.002] * 59  # 基准每日涨 0.2%
        report = generate_report(
            _mock_result,
            benchmark_daily_returns=bench_rets,
            benchmark_name="沪深300",
        )
        assert report.benchmark_relative is not None
        assert report.benchmark_relative.benchmark_name == "沪深300"
        assert report.benchmark_relative.active_return > 0  # 策略 0.5% > 基准 0.2%

    def test_summary_contains_benchmark(self, _mock_result) -> None:
        """summary() 输出包含基准指标"""
        bench_rets = [0.002] * 59
        report = generate_report(
            _mock_result,
            benchmark_daily_returns=bench_rets,
            benchmark_name="沪深300",
        )
        text = report.summary()
        assert "基准相对指标" in text
        assert "沪深300" in text
        assert "信息比率" in text
        assert "跟踪误差" in text

    def test_summary_no_benchmark_section(self, _mock_result) -> None:
        """无基准时 summary() 不含基准段"""
        report = generate_report(_mock_result)
        text = report.summary()
        assert "基准相对指标" not in text
