"""Tests for backtest report module.

Coverage targets:
- PerformanceMetrics computation
- DrawdownAnalysis computation
- CostAttribution computation
- TradingStats computation
- PositionStats computation
- BacktestReport generation
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

import numpy as np
import pytest

from aipha.backtest.engine import BacktestResult, DailySnapshot, TradeRecord
from aipha.backtest.report import (
    TRADING_DAYS_PER_YEAR,
    BacktestReport,
    CostAttribution,
    DrawdownAnalysis,
    PerformanceMetrics,
    PositionStats,
    TradingStats,
    compute_cost_attribution,
    compute_drawdown_analysis,
    compute_performance_metrics,
    compute_position_stats,
    compute_trading_stats,
    extract_metrics_dict,
    generate_report,
)
from aipha.domain.models import Side
from aipha.execution.fees import FeeBreakdown


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def simple_fee_breakdown() -> FeeBreakdown:
    """简单费用分解"""
    return FeeBreakdown(
        commission=100.0,
        stamp_tax=50.0,
        transfer_fee=10.0,
    )


@pytest.fixture
def sample_snapshots() -> list[DailySnapshot]:
    """示例日快照 - 10天，模拟涨跌"""
    # 模拟一个先涨后跌再涨的 NAV 曲线
    navs = [100000, 101000, 103000, 102000, 100000, 98000, 99000, 101000, 103000, 105000]
    snapshots = []
    prev_nav = 100000

    for i, nav in enumerate(navs):
        daily_return = (nav - prev_nav) / prev_nav if prev_nav > 0 else 0
        cumulative_return = (nav - 100000) / 100000
        snapshots.append(
            DailySnapshot(
                trade_date=date(2025, 1, 6 + i),
                nav=float(nav),
                cash=50000.0,
                positions={"600000.SH": 100, "000001.SZ": 200} if i % 2 == 0 else {"600000.SH": 100},
                position_values={"600000.SH": 25000.0, "000001.SZ": 25000.0}
                if i % 2 == 0
                else {"600000.SH": 50000.0},
                daily_return=daily_return,
                cumulative_return=cumulative_return,
            )
        )
        prev_nav = nav

    return snapshots


@pytest.fixture
def sample_trades(simple_fee_breakdown: FeeBreakdown) -> list[TradeRecord]:
    """示例交易记录"""
    return [
        TradeRecord(
            trade_id="T001",
            order_id="O001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=100,
            price=10.0,
            notional=1000.0,
            fees=simple_fee_breakdown,
            trade_time=datetime(2025, 1, 6, 9, 31),
            trade_date=date(2025, 1, 6),
        ),
        TradeRecord(
            trade_id="T002",
            order_id="O002",
            symbol="600000.SH",
            side=Side.SELL,
            qty=100,
            price=10.5,
            notional=1050.0,
            fees=simple_fee_breakdown,
            trade_time=datetime(2025, 1, 10, 9, 31),
            trade_date=date(2025, 1, 10),
        ),
        TradeRecord(
            trade_id="T003",
            order_id="O003",
            symbol="000001.SZ",
            side=Side.BUY,
            qty=200,
            price=5.0,
            notional=1000.0,
            fees=simple_fee_breakdown,
            trade_time=datetime(2025, 1, 8, 14, 0),
            trade_date=date(2025, 1, 8),
        ),
    ]


@pytest.fixture
def sample_result(
    sample_snapshots: list[DailySnapshot],
    sample_trades: list[TradeRecord],
    simple_fee_breakdown: FeeBreakdown,
) -> BacktestResult:
    """示例回测结果"""
    total_fees = FeeBreakdown(
        commission=300.0,  # 3 trades * 100
        stamp_tax=50.0,  # only sell
        transfer_fee=30.0,  # 3 trades * 10
    )
    return BacktestResult(
        strategy_name="TestStrategy",
        start_date=date(2025, 1, 6),
        end_date=date(2025, 1, 15),
        initial_capital=100000.0,
        final_nav=105000.0,
        final_cash=50000.0,
        final_positions={"600000.SH": 100},
        daily_snapshots=sample_snapshots,
        trades=sample_trades,
        total_trades=3,
        total_fees=total_fees,
        exclusion_stats={},
        config={"test": True},
    )


@pytest.fixture
def empty_result() -> BacktestResult:
    """空回测结果（无交易）"""
    return BacktestResult(
        strategy_name="EmptyStrategy",
        start_date=date(2025, 1, 6),
        end_date=date(2025, 1, 6),
        initial_capital=100000.0,
        final_nav=100000.0,
        final_cash=100000.0,
        final_positions={},
        daily_snapshots=[],
        trades=[],
        total_trades=0,
        total_fees=FeeBreakdown(commission=0, stamp_tax=0, transfer_fee=0),
        exclusion_stats={},
    )


# ============================================================================
# PerformanceMetrics Tests
# ============================================================================


class TestPerformanceMetrics:
    """Tests for compute_performance_metrics"""

    def test_total_return(self, sample_result: BacktestResult):
        """总收益率计算"""
        metrics = compute_performance_metrics(sample_result)

        expected = (105000 - 100000) / 100000  # 5%
        assert metrics.total_return == pytest.approx(expected, rel=1e-6)

    def test_trading_days(self, sample_result: BacktestResult):
        """交易日数"""
        metrics = compute_performance_metrics(sample_result)

        assert metrics.trading_days == 10

    def test_annualized_return(self, sample_result: BacktestResult):
        """年化收益率计算"""
        metrics = compute_performance_metrics(sample_result)

        # 10天，5%收益，年化
        years = 10 / TRADING_DAYS_PER_YEAR
        expected = (1 + 0.05) ** (1 / years) - 1
        assert metrics.annualized_return == pytest.approx(expected, rel=1e-6)

    def test_volatility(self, sample_result: BacktestResult):
        """波动率计算"""
        metrics = compute_performance_metrics(sample_result)

        # 计算日收益标准差 * sqrt(252)
        daily_returns = [s.daily_return for s in sample_result.daily_snapshots]
        expected_vol = np.std(daily_returns, ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR)
        assert metrics.volatility == pytest.approx(expected_vol, rel=1e-6)

    def test_sharpe_ratio_positive(self, sample_result: BacktestResult):
        """夏普比率应为正数"""
        metrics = compute_performance_metrics(sample_result)

        # 有正收益，应该有正夏普
        assert metrics.sharpe_ratio > 0

    def test_calmar_ratio(self, sample_result: BacktestResult):
        """卡玛比率计算"""
        metrics = compute_performance_metrics(sample_result)

        # calmar = 年化收益 / 最大回撤
        assert metrics.calmar_ratio > 0

    def test_empty_result(self, empty_result: BacktestResult):
        """空结果应返回零值"""
        metrics = compute_performance_metrics(empty_result)

        assert metrics.total_return == 0.0
        assert metrics.annualized_return == 0.0
        assert metrics.volatility == 0.0
        assert metrics.sharpe_ratio == 0.0
        assert metrics.trading_days == 0


# ============================================================================
# DrawdownAnalysis Tests
# ============================================================================


class TestDrawdownAnalysis:
    """Tests for compute_drawdown_analysis"""

    def test_max_drawdown_pct(self, sample_result: BacktestResult):
        """最大回撤百分比"""
        dd = compute_drawdown_analysis(sample_result)

        # NAV: 100k -> 103k -> 98k，从 103k 回撤到 98k
        # max_dd_pct = (103000 - 98000) / 103000 ≈ 4.85%
        expected_max_dd = (103000 - 98000) / 103000
        assert dd.max_drawdown_pct == pytest.approx(expected_max_dd, rel=1e-6)

    def test_max_drawdown_value(self, sample_result: BacktestResult):
        """最大回撤金额"""
        dd = compute_drawdown_analysis(sample_result)

        expected_max_dd = 103000 - 98000  # 5000
        assert dd.max_drawdown == pytest.approx(expected_max_dd, rel=1e-6)

    def test_drawdown_dates(self, sample_result: BacktestResult):
        """回撤日期"""
        dd = compute_drawdown_analysis(sample_result)

        # 高点是第3天（index 2），低点是第6天（index 5）
        assert dd.max_drawdown_start == date(2025, 1, 8)  # index 2
        assert dd.max_drawdown_end == date(2025, 1, 11)  # index 5

    def test_drawdown_recovery(self, sample_result: BacktestResult):
        """回撤恢复日期"""
        dd = compute_drawdown_analysis(sample_result)

        # NAV 恢复到 103k 是第9天（index 8）
        assert dd.max_drawdown_recovery == date(2025, 1, 14)

    def test_avg_drawdown(self, sample_result: BacktestResult):
        """平均回撤"""
        dd = compute_drawdown_analysis(sample_result)

        # 平均回撤应该是正数（回撤用正数表示）
        assert dd.avg_drawdown >= 0

    def test_drawdown_series_length(self, sample_result: BacktestResult):
        """回撤序列长度应等于交易日数"""
        dd = compute_drawdown_analysis(sample_result)

        assert len(dd.drawdown_series) == 10

    def test_empty_result(self, empty_result: BacktestResult):
        """空结果"""
        dd = compute_drawdown_analysis(empty_result)

        assert dd.max_drawdown == 0.0
        assert dd.max_drawdown_pct == 0.0
        assert dd.max_drawdown_start is None
        assert dd.max_drawdown_end is None
        assert dd.drawdown_series == []


# ============================================================================
# CostAttribution Tests
# ============================================================================


class TestCostAttribution:
    """Tests for compute_cost_attribution"""

    def test_total_fees(self, sample_result: BacktestResult):
        """总费用"""
        costs = compute_cost_attribution(sample_result)

        expected = 300 + 50 + 30  # 380
        assert costs.total_fees == pytest.approx(expected, rel=1e-6)

    def test_fee_breakdown(self, sample_result: BacktestResult):
        """费用分解"""
        costs = compute_cost_attribution(sample_result)

        assert costs.commission_total == pytest.approx(300.0, rel=1e-6)
        assert costs.stamp_tax_total == pytest.approx(50.0, rel=1e-6)
        assert costs.transfer_fee_total == pytest.approx(30.0, rel=1e-6)

    def test_cost_vs_nav_pct(self, sample_result: BacktestResult):
        """费用占 NAV 百分比"""
        costs = compute_cost_attribution(sample_result)

        expected = 380 / 105000
        assert costs.cost_vs_nav_pct == pytest.approx(expected, rel=1e-6)

    def test_cost_drag_annualized(self, sample_result: BacktestResult):
        """年化费用拖累"""
        costs = compute_cost_attribution(sample_result)

        # 10天，费用 380
        years = 10 / TRADING_DAYS_PER_YEAR
        expected = 380 / 100000 / years
        assert costs.cost_drag_annualized == pytest.approx(expected, rel=1e-6)


# ============================================================================
# TradingStats Tests
# ============================================================================


class TestTradingStats:
    """Tests for compute_trading_stats"""

    def test_trade_counts(self, sample_result: BacktestResult):
        """交易次数统计"""
        stats = compute_trading_stats(sample_result)

        assert stats.total_trades == 3
        assert stats.buy_trades == 2
        assert stats.sell_trades == 1

    def test_avg_trade_size(self, sample_result: BacktestResult):
        """平均交易金额"""
        stats = compute_trading_stats(sample_result)

        # total_notional = 1000 + 1050 + 1000 = 3050
        expected = 3050 / 3
        assert stats.avg_trade_size == pytest.approx(expected, rel=1e-6)

    def test_turnover_rate(self, sample_result: BacktestResult):
        """换手率"""
        stats = compute_trading_stats(sample_result)

        # 应该有一个正的换手率
        assert stats.turnover_rate > 0

    def test_empty_result(self, empty_result: BacktestResult):
        """空结果"""
        stats = compute_trading_stats(empty_result)

        assert stats.total_trades == 0
        assert stats.buy_trades == 0
        assert stats.sell_trades == 0
        assert stats.avg_trade_size == 0.0


# ============================================================================
# PositionStats Tests
# ============================================================================


class TestPositionStats:
    """Tests for compute_position_stats"""

    def test_avg_positions(self, sample_result: BacktestResult):
        """平均持仓数量"""
        stats = compute_position_stats(sample_result)

        # 偶数天 2 个持仓，奇数天 1 个持仓
        # [2, 1, 2, 1, 2, 1, 2, 1, 2, 1] -> avg = 1.5
        assert stats.avg_positions == pytest.approx(1.5, rel=1e-6)

    def test_max_positions(self, sample_result: BacktestResult):
        """最大持仓数量"""
        stats = compute_position_stats(sample_result)

        assert stats.max_positions == 2

    def test_min_positions(self, sample_result: BacktestResult):
        """最小持仓数量（非零）"""
        stats = compute_position_stats(sample_result)

        assert stats.min_positions == 1

    def test_concentration(self, sample_result: BacktestResult):
        """集中度"""
        stats = compute_position_stats(sample_result)

        # 应该有正的集中度
        assert stats.avg_concentration > 0
        assert stats.max_concentration > 0
        assert stats.max_concentration <= 1.0

    def test_empty_result(self, empty_result: BacktestResult):
        """空结果"""
        stats = compute_position_stats(empty_result)

        assert stats.avg_positions == 0.0
        assert stats.max_positions == 0
        assert stats.min_positions == 0

    def test_idle_ratio_no_idle(self, sample_result: BacktestResult):
        """sample_result 所有天都有持仓，idle_ratio == 0"""
        stats = compute_position_stats(sample_result)
        assert stats.idle_ratio == pytest.approx(0.0)

    def test_idle_ratio_empty(self, empty_result: BacktestResult):
        """empty_result 无快照，idle_ratio == 1"""
        stats = compute_position_stats(empty_result)
        assert stats.idle_ratio == pytest.approx(1.0)

    def test_idle_ratio_partial(self):
        """部分天空仓的场景"""
        snapshots = []
        for i in range(10):
            # 前3天空仓, 后7天有持仓
            pos = {} if i < 3 else {"600000.SH": 100}
            pv = {} if i < 3 else {"600000.SH": 50000.0}
            snapshots.append(
                DailySnapshot(
                    trade_date=date(2025, 1, 6 + i),
                    nav=100000.0,
                    cash=50000.0,
                    positions=pos,
                    position_values=pv,
                    daily_return=0.0,
                    cumulative_return=0.0,
                )
            )
        result = BacktestResult(
            strategy_name="IdleTest",
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 15),
            initial_capital=100000.0,
            final_nav=100000.0,
            final_cash=50000.0,
            final_positions={},
            daily_snapshots=snapshots,
            trades=[],
            total_trades=0,
            total_fees=FeeBreakdown(commission=0, stamp_tax=0, transfer_fee=0),
            exclusion_stats={},
        )
        stats = compute_position_stats(result)
        # 3/10 = 0.3 idle
        assert stats.idle_ratio == pytest.approx(0.3)


# ============================================================================
# BacktestReport Tests
# ============================================================================


class TestBacktestReport:
    """Tests for BacktestReport and generate_report"""

    def test_generate_report(self, sample_result: BacktestResult):
        """生成完整报告"""
        report = generate_report(sample_result)

        assert report.strategy_name == "TestStrategy"
        assert report.start_date == date(2025, 1, 6)
        assert report.end_date == date(2025, 1, 15)
        assert report.initial_capital == 100000.0
        assert report.final_nav == 105000.0

    def test_report_has_all_sections(self, sample_result: BacktestResult):
        """报告包含所有部分"""
        report = generate_report(sample_result)

        assert isinstance(report.performance, PerformanceMetrics)
        assert isinstance(report.drawdown, DrawdownAnalysis)
        assert isinstance(report.costs, CostAttribution)
        assert isinstance(report.trading, TradingStats)
        assert isinstance(report.positions, PositionStats)

    def test_report_summary(self, sample_result: BacktestResult):
        """报告摘要生成"""
        report = generate_report(sample_result)
        summary = report.summary()

        # 应该包含关键信息
        assert "TestStrategy" in summary
        assert "累计收益" in summary
        assert "年化收益" in summary
        assert "最大回撤" in summary
        assert "夏普比率" in summary
        assert "总交易数" in summary

    def test_report_generated_at(self, sample_result: BacktestResult):
        """报告包含生成时间"""
        report = generate_report(sample_result)

        assert report.generated_at != ""
        # 应该是 ISO 格式
        assert "2025" in report.generated_at or "T" in report.generated_at

    def test_report_config_preserved(self, sample_result: BacktestResult):
        """报告保留配置"""
        report = generate_report(sample_result)

        assert report.config == {"test": True}


# ============================================================================
# Edge Cases
# ============================================================================


class TestEdgeCases:
    """边界情况测试"""

    def test_single_day_result(self):
        """单日结果"""
        result = BacktestResult(
            strategy_name="SingleDay",
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 6),
            initial_capital=100000.0,
            final_nav=101000.0,
            final_cash=50000.0,
            final_positions={"600000.SH": 100},
            daily_snapshots=[
                DailySnapshot(
                    trade_date=date(2025, 1, 6),
                    nav=101000.0,
                    cash=50000.0,
                    positions={"600000.SH": 100},
                    position_values={"600000.SH": 51000.0},
                    daily_return=0.01,
                    cumulative_return=0.01,
                )
            ],
            trades=[],
            total_trades=0,
            total_fees=FeeBreakdown(commission=0, stamp_tax=0, transfer_fee=0),
            exclusion_stats={},
        )

        report = generate_report(result)

        assert report.performance.total_return == pytest.approx(0.01, rel=1e-6)
        assert report.performance.trading_days == 1

    def test_no_drawdown(self):
        """无回撤（一路上涨）"""
        navs = [100000, 101000, 102000, 103000, 104000]
        snapshots = []
        prev_nav = 100000
        for i, nav in enumerate(navs):
            dr = (nav - prev_nav) / prev_nav
            snapshots.append(
                DailySnapshot(
                    trade_date=date(2025, 1, 6 + i),
                    nav=float(nav),
                    cash=50000.0,
                    positions={},
                    position_values={},
                    daily_return=dr,
                    cumulative_return=(nav - 100000) / 100000,
                )
            )
            prev_nav = nav

        result = BacktestResult(
            strategy_name="NoDrawdown",
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 10),
            initial_capital=100000.0,
            final_nav=104000.0,
            final_cash=104000.0,
            final_positions={},
            daily_snapshots=snapshots,
            trades=[],
            total_trades=0,
            total_fees=FeeBreakdown(commission=0, stamp_tax=0, transfer_fee=0),
            exclusion_stats={},
        )

        dd = compute_drawdown_analysis(result)

        assert dd.max_drawdown_pct == 0.0

    def test_all_down(self):
        """全程下跌"""
        navs = [100000, 99000, 98000, 97000, 96000]
        snapshots = []
        prev_nav = 100000
        for i, nav in enumerate(navs):
            dr = (nav - prev_nav) / prev_nav
            snapshots.append(
                DailySnapshot(
                    trade_date=date(2025, 1, 6 + i),
                    nav=float(nav),
                    cash=50000.0,
                    positions={},
                    position_values={},
                    daily_return=dr,
                    cumulative_return=(nav - 100000) / 100000,
                )
            )
            prev_nav = nav

        result = BacktestResult(
            strategy_name="AllDown",
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 10),
            initial_capital=100000.0,
            final_nav=96000.0,
            final_cash=96000.0,
            final_positions={},
            daily_snapshots=snapshots,
            trades=[],
            total_trades=0,
            total_fees=FeeBreakdown(commission=0, stamp_tax=0, transfer_fee=0),
            exclusion_stats={},
        )

        metrics = compute_performance_metrics(result)
        dd = compute_drawdown_analysis(result)

        assert metrics.total_return < 0
        assert dd.max_drawdown_pct == pytest.approx(0.04, rel=1e-6)  # 4%
        assert dd.max_drawdown_recovery is None  # Not recovered


# ============================================================================
# extract_metrics_dict Tests
# ============================================================================


class TestExtractMetricsDict:
    """Tests for extract_metrics_dict"""

    def test_returns_all_expected_keys(self, sample_result: BacktestResult):
        """应返回所有预期的指标键（可能含额外的 yearly_returns）"""
        metrics = extract_metrics_dict(sample_result)

        expected_keys = {
            "total_return",
            "annualized_return",
            "annual_return",  # 别名，兼容 reporting/report.py 命名
            "volatility",
            "sharpe_ratio",
            "sortino_ratio",
            "calmar_ratio",
            "trading_days",
            "max_drawdown_pct",
            "avg_drawdown",
            "total_trades",
            "win_rate",
            "profit_factor",
            "turnover_rate",
            "avg_positions",
            "max_positions",
            "max_concentration",
        }
        # 允许额外的 yearly_returns 等键（长周期数据会添加）
        assert expected_keys.issubset(set(metrics.keys())), (
            f"缺少预期键: {expected_keys - set(metrics.keys())}"
        )

    def test_values_are_numeric(self, sample_result: BacktestResult):
        """标量指标值应为数值类型（yearly_returns 为字典，跳过）"""
        metrics = extract_metrics_dict(sample_result)

        for key, value in metrics.items():
            if key == "yearly_returns":
                assert isinstance(value, dict), f"{key} 应为 dict"
            else:
                assert isinstance(value, (int, float)), f"{key} is {type(value)}"

    def test_consistent_with_compute_functions(self, sample_result: BacktestResult):
        """指标值应与直接计算函数一致"""
        metrics = extract_metrics_dict(sample_result)
        perf = compute_performance_metrics(sample_result)
        dd = compute_drawdown_analysis(sample_result)
        trading = compute_trading_stats(sample_result)
        pos = compute_position_stats(sample_result)

        assert metrics["total_return"] == pytest.approx(perf.total_return)
        assert metrics["sharpe_ratio"] == pytest.approx(perf.sharpe_ratio)
        assert metrics["max_drawdown_pct"] == pytest.approx(dd.max_drawdown_pct)
        assert metrics["win_rate"] == pytest.approx(trading.win_rate)
        assert metrics["avg_positions"] == pytest.approx(pos.avg_positions)

    def test_empty_result(self, empty_result: BacktestResult):
        """空回测结果应返回零值字典"""
        metrics = extract_metrics_dict(empty_result)

        assert metrics["total_return"] == 0.0
        assert metrics["sharpe_ratio"] == 0.0
        assert metrics["trading_days"] == 0.0
        assert metrics["total_trades"] == 0.0
        # 空结果不应有 yearly_returns
        assert "yearly_returns" not in metrics

    def test_yearly_returns_in_metrics_for_long_data(self):
        """超过252日的回测结果应包含 yearly_returns 字段。"""
        from datetime import date, timedelta
        from aipha.backtest.engine import DailySnapshot
        from aipha.backtest.report import extract_metrics_dict
        from aipha.backtest.engine import BacktestResult
        from aipha.execution.fees import FeeBreakdown

        start = date(2020, 1, 2)
        n = 504  # 约2年
        nav = 100000.0
        snapshots = []
        for i in range(n):
            d = start + timedelta(days=i)
            nav *= 1.00080  # 每日0.08% ≈ 年化22%
            snapshots.append(DailySnapshot(
                trade_date=d, nav=nav, cash=nav * 0.1,
                positions={}, position_values={},
                daily_return=0.0008, cumulative_return=(nav - 100000) / 100000,
            ))

        result = BacktestResult(
            strategy_name="LongTest",
            start_date=start,
            end_date=start + timedelta(days=n - 1),
            initial_capital=100000.0,
            final_nav=nav,
            final_cash=nav * 0.1,
            final_positions={},
            daily_snapshots=snapshots,
            trades=[],
            total_trades=0,
            total_fees=FeeBreakdown(commission=0, stamp_tax=0, transfer_fee=0),
            exclusion_stats={},
        )

        metrics = extract_metrics_dict(result)
        assert "yearly_returns" in metrics, "长周期回测应包含 yearly_returns"
        yr = metrics["yearly_returns"]
        assert isinstance(yr, dict), "yearly_returns 应为字典"
        assert len(yr) >= 2, "504天数据应覆盖至少2个日历年"
        for year, ret in yr.items():
            assert isinstance(year, str), "年份键应为字符串"
            assert isinstance(ret, float), "年化收益应为 float"

    def test_compatible_with_quality_checker(self, sample_result: BacktestResult):
        """提取的指标应能直接传入 QualityChecker.check()"""
        from aipha.backtest.quality_gates import QualityChecker

        metrics = extract_metrics_dict(sample_result)
        checker = QualityChecker.default()
        report = checker.check(metrics, strategy_name="test")

        # 必需门禁的指标必须全部存在（否则会因缺指标而误判失败）。
        # required=False 的过拟合门禁（DSR/PBO/衰减）需要回测之外的输入，
        # 缺失时记为「未检验」，不在此列。
        for r in report.results:
            if not r.gate.required:
                continue
            assert r.actual_value is not None, (
                f"指标 '{r.gate.metric}' 在 extract_metrics_dict 中缺失"
            )

    def test_optional_overfitting_gates_skipped_not_failed(
        self, sample_result: BacktestResult,
    ):
        """不提供试验次数时过拟合门禁记为未检验，而非判失败"""
        from aipha.backtest.quality_gates import QualityChecker

        report = QualityChecker.default().check(
            extract_metrics_dict(sample_result), strategy_name="test",
        )

        assert {gr.gate.metric for gr in report.skipped} == {
            "dsr", "dsr_p_value", "pbo", "oos_sharpe_decay",
        }
        for gr in report.errors:
            assert gr.gate.required
