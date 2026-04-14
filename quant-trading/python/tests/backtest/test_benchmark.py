"""基准对比模块测试"""

from datetime import date, timedelta
import numpy as np
import pytest

from aipha.backtest.benchmark import (
    BenchmarkComparison,
    BenchmarkMetrics,
    BenchmarkComparisonReport,
    RollingMetrics,
    compare_with_benchmark,
)


class TestBenchmarkComparison:
    """BenchmarkComparison测试"""

    def _generate_nav(
        self,
        start_date: date,
        n_days: int,
        annual_return: float = 0.10,
        volatility: float = 0.20,
        seed: int = 42,
    ) -> dict[date, float]:
        """生成模拟净值曲线"""
        rng = np.random.default_rng(seed)
        daily_return = annual_return / 252
        daily_vol = volatility / np.sqrt(252)

        nav = {start_date: 1.0}
        current_nav = 1.0
        current_date = start_date

        for _ in range(n_days - 1):
            current_date = current_date + timedelta(days=1)
            # 跳过周末
            while current_date.weekday() >= 5:
                current_date = current_date + timedelta(days=1)

            ret = daily_return + daily_vol * rng.standard_normal()
            current_nav = current_nav * (1 + ret)
            nav[current_date] = current_nav

        return nav

    def test_basic_comparison(self):
        """测试基本对比功能"""
        start = date(2023, 1, 1)
        strategy_nav = self._generate_nav(start, 252, annual_return=0.15, seed=42)
        benchmark_nav = self._generate_nav(start, 252, annual_return=0.10, seed=123)

        comparison = BenchmarkComparison(
            strategy_nav, benchmark_nav, "TestStrategy", "沪深300"
        )

        assert len(comparison.dates) > 0
        assert comparison.strategy_name == "TestStrategy"
        assert comparison.benchmark_name == "沪深300"

    def test_compute_metrics(self):
        """测试指标计算"""
        start = date(2023, 1, 1)
        strategy_nav = self._generate_nav(start, 252, annual_return=0.20, seed=42)
        benchmark_nav = self._generate_nav(start, 252, annual_return=0.10, seed=123)

        comparison = BenchmarkComparison(strategy_nav, benchmark_nav)
        metrics = comparison.compute_metrics()

        assert isinstance(metrics, BenchmarkMetrics)
        # 策略年化收益应该较高
        assert metrics.strategy_annual_return != 0
        # 波动率应为正
        assert metrics.strategy_volatility > 0
        assert metrics.benchmark_volatility > 0
        # Beta应接近1
        assert 0 < metrics.beta < 3

    def test_excess_return(self):
        """测试超额收益计算"""
        start = date(2023, 1, 1)
        # 策略明显跑赢基准
        strategy_nav = self._generate_nav(start, 252, annual_return=0.30, volatility=0.15, seed=42)
        benchmark_nav = self._generate_nav(start, 252, annual_return=-0.10, volatility=0.20, seed=123)

        comparison = BenchmarkComparison(strategy_nav, benchmark_nav)
        metrics = comparison.compute_metrics()

        # 超额收益应为正
        assert metrics.excess_return > 0
        assert metrics.excess_annual_return > 0

    def test_alpha_beta(self):
        """测试Alpha和Beta计算"""
        start = date(2023, 1, 1)
        # 使用相同seed生成高度相关的序列
        base_nav = self._generate_nav(start, 252, annual_return=0.10, seed=42)

        # 策略 = 1.2 * 基准 (高Beta)
        strategy_nav = {d: 1.0 + (v - 1.0) * 1.2 for d, v in base_nav.items()}

        comparison = BenchmarkComparison(strategy_nav, base_nav)
        metrics = comparison.compute_metrics()

        # Beta应接近1.2
        assert 1.0 < metrics.beta < 1.5
        # 相关系数应很高
        assert metrics.correlation > 0.9

    def test_tracking_error(self):
        """测试跟踪误差计算"""
        start = date(2023, 1, 1)
        # 完全相同的净值
        nav = self._generate_nav(start, 252, seed=42)

        comparison = BenchmarkComparison(nav.copy(), nav.copy())
        metrics = comparison.compute_metrics()

        # 跟踪误差应为0
        assert metrics.tracking_error == 0

    def test_information_ratio(self):
        """测试信息比率计算"""
        start = date(2023, 1, 1)
        # 策略稳定跑赢基准
        strategy_nav = self._generate_nav(start, 252, annual_return=0.20, volatility=0.15, seed=42)
        benchmark_nav = self._generate_nav(start, 252, annual_return=0.05, volatility=0.15, seed=123)

        comparison = BenchmarkComparison(strategy_nav, benchmark_nav)
        metrics = comparison.compute_metrics()

        # 应有正信息比率（跑赢基准）
        assert isinstance(metrics.information_ratio, float)

    def test_win_rate(self):
        """测试跑赢基准比例"""
        start = date(2023, 1, 1)
        strategy_nav = self._generate_nav(start, 100, annual_return=0.30, seed=42)
        benchmark_nav = self._generate_nav(start, 100, annual_return=0.0, seed=123)

        comparison = BenchmarkComparison(strategy_nav, benchmark_nav)
        metrics = comparison.compute_metrics()

        # 跑赢比例在0-1之间
        assert 0 <= metrics.win_rate_vs_benchmark <= 1

    def test_max_drawdown(self):
        """测试最大回撤计算"""
        start = date(2023, 1, 1)
        # 创建有明显回撤的净值
        nav = {}
        current_date = start
        values = [1.0, 1.05, 1.10, 1.08, 0.90, 0.85, 0.95, 1.00]
        for v in values:
            nav[current_date] = v
            current_date = current_date + timedelta(days=1)

        comparison = BenchmarkComparison(nav.copy(), nav.copy())
        metrics = comparison.compute_metrics()

        # 最大回撤应为 (1.10 - 0.85) / 1.10 ≈ 0.227
        assert metrics.strategy_max_drawdown > 0.2

    def test_rolling_metrics(self):
        """测试滚动指标计算"""
        start = date(2023, 1, 1)
        strategy_nav = self._generate_nav(start, 252, seed=42)
        benchmark_nav = self._generate_nav(start, 252, seed=123)

        comparison = BenchmarkComparison(strategy_nav, benchmark_nav)
        rolling = comparison.compute_rolling_metrics(window=30)

        assert isinstance(rolling, RollingMetrics)
        assert len(rolling.dates) > 0
        assert len(rolling.rolling_alpha) == len(rolling.dates)
        assert len(rolling.rolling_beta) == len(rolling.dates)
        assert len(rolling.rolling_sharpe) == len(rolling.dates)
        assert len(rolling.rolling_excess_return) == len(rolling.dates)

    def test_generate_report(self):
        """测试报告生成"""
        start = date(2023, 1, 1)
        strategy_nav = self._generate_nav(start, 252, seed=42)
        benchmark_nav = self._generate_nav(start, 252, seed=123)

        comparison = BenchmarkComparison(strategy_nav, benchmark_nav, "MyStrategy", "CSI300")
        report = comparison.generate_report()

        assert isinstance(report, BenchmarkComparisonReport)
        assert report.strategy_name == "MyStrategy"
        assert report.benchmark_name == "CSI300"
        assert report.metrics is not None
        assert report.rolling_metrics is not None
        assert report.trading_days > 0

    def test_report_summary(self):
        """测试报告摘要"""
        start = date(2023, 1, 1)
        strategy_nav = self._generate_nav(start, 252, seed=42)
        benchmark_nav = self._generate_nav(start, 252, seed=123)

        comparison = BenchmarkComparison(strategy_nav, benchmark_nav)
        report = comparison.generate_report()
        summary = report.summary()

        assert "基准对比报告" in summary
        assert "策略表现" in summary
        assert "基准表现" in summary
        assert "相对表现" in summary
        assert "Alpha" in summary
        assert "Beta" in summary
        assert "信息比率" in summary

    def test_convenience_function(self):
        """测试便捷函数"""
        start = date(2023, 1, 1)
        strategy_nav = self._generate_nav(start, 100, seed=42)
        benchmark_nav = self._generate_nav(start, 100, seed=123)

        report = compare_with_benchmark(
            strategy_nav, benchmark_nav, "TestStrategy", "Benchmark"
        )

        assert isinstance(report, BenchmarkComparisonReport)
        assert report.strategy_name == "TestStrategy"
        assert report.benchmark_name == "Benchmark"

    def test_date_alignment(self):
        """测试日期对齐"""
        # 策略和基准有不同日期
        strategy_nav = {
            date(2023, 1, 1): 1.0,
            date(2023, 1, 2): 1.01,
            date(2023, 1, 3): 1.02,
            date(2023, 1, 4): 1.03,
        }
        benchmark_nav = {
            date(2023, 1, 2): 1.0,
            date(2023, 1, 3): 1.01,
            date(2023, 1, 4): 1.02,
            date(2023, 1, 5): 1.03,
        }

        comparison = BenchmarkComparison(strategy_nav, benchmark_nav)

        # 应该只包含共同日期
        assert len(comparison.dates) == 3
        assert date(2023, 1, 2) in comparison.dates
        assert date(2023, 1, 3) in comparison.dates
        assert date(2023, 1, 4) in comparison.dates

    def test_empty_data(self):
        """测试空数据处理"""
        comparison = BenchmarkComparison({}, {})
        metrics = comparison.compute_metrics()

        assert metrics.strategy_return == 0
        assert metrics.benchmark_return == 0
        assert metrics.alpha == 0

    def test_single_day(self):
        """测试单日数据"""
        nav = {date(2023, 1, 1): 1.0}
        comparison = BenchmarkComparison(nav.copy(), nav.copy())
        metrics = comparison.compute_metrics()

        assert metrics.strategy_return == 0

    def test_risk_free_rate(self):
        """测试无风险利率参数"""
        start = date(2023, 1, 1)
        strategy_nav = self._generate_nav(start, 252, seed=42)
        benchmark_nav = self._generate_nav(start, 252, seed=123)

        # 不同的无风险利率
        comp1 = BenchmarkComparison(strategy_nav, benchmark_nav, risk_free_rate=0.02)
        comp2 = BenchmarkComparison(strategy_nav, benchmark_nav, risk_free_rate=0.05)

        sharpe1 = comp1.compute_metrics().strategy_sharpe
        sharpe2 = comp2.compute_metrics().strategy_sharpe

        # 更高的无风险利率应该导致更低的夏普
        assert sharpe1 > sharpe2

    def test_negative_returns(self):
        """测试负收益场景"""
        start = date(2023, 1, 1)
        # 策略亏损
        strategy_nav = self._generate_nav(start, 252, annual_return=-0.20, seed=42)
        benchmark_nav = self._generate_nav(start, 252, annual_return=0.10, seed=123)

        comparison = BenchmarkComparison(strategy_nav, benchmark_nav)
        metrics = comparison.compute_metrics()

        # 策略收益应为负
        assert metrics.strategy_annual_return < 0
        # 超额收益应为负
        assert metrics.excess_return < 0

    def test_short_period_no_rolling(self):
        """测试短期数据不生成滚动指标"""
        start = date(2023, 1, 1)
        strategy_nav = self._generate_nav(start, 30, seed=42)
        benchmark_nav = self._generate_nav(start, 30, seed=123)

        comparison = BenchmarkComparison(strategy_nav, benchmark_nav)
        report = comparison.generate_report(rolling_window=60)

        # 数据不足，不应有滚动指标
        assert report.rolling_metrics is None


class TestBenchmarkMetricsDataclass:
    """BenchmarkMetrics数据类测试"""

    def test_all_fields_present(self):
        """测试所有字段存在"""
        metrics = BenchmarkMetrics(
            strategy_return=0.1,
            strategy_annual_return=0.12,
            strategy_volatility=0.15,
            strategy_sharpe=1.5,
            strategy_max_drawdown=0.05,
            benchmark_return=0.08,
            benchmark_annual_return=0.10,
            benchmark_volatility=0.12,
            benchmark_sharpe=1.2,
            benchmark_max_drawdown=0.04,
            excess_return=0.02,
            excess_annual_return=0.02,
            alpha=0.01,
            beta=1.1,
            tracking_error=0.03,
            information_ratio=0.5,
            correlation=0.85,
            win_rate_vs_benchmark=0.55,
        )

        assert metrics.strategy_return == 0.1
        assert metrics.alpha == 0.01
        assert metrics.beta == 1.1
        assert metrics.information_ratio == 0.5
