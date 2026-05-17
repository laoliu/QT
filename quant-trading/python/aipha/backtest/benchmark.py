"""
基准对比模块

提供策略与基准的对比分析功能：
- 支持多种基准：沪深300、中证500、中证1000、等权基准
- 超额收益分析
- 相对指标：Alpha、Beta、信息比率
- 滚动分析

使用示例：
    benchmark = BenchmarkComparison(strategy_nav, benchmark_nav)
    metrics = benchmark.compute_metrics()
    print(benchmark.summary())
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from scipy import stats

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR


@dataclass
class BenchmarkMetrics:
    """基准对比指标"""

    # 策略指标
    strategy_return: float  # 策略总收益
    strategy_annual_return: float  # 策略年化收益
    strategy_volatility: float  # 策略波动率
    strategy_sharpe: float  # 策略夏普比率
    strategy_max_drawdown: float  # 策略最大回撤

    # 基准指标
    benchmark_return: float  # 基准总收益
    benchmark_annual_return: float  # 基准年化收益
    benchmark_volatility: float  # 基准波动率
    benchmark_sharpe: float  # 基准夏普比率
    benchmark_max_drawdown: float  # 基准最大回撤

    # 相对指标
    excess_return: float  # 超额收益
    excess_annual_return: float  # 年化超额收益
    alpha: float  # Jensen's Alpha
    beta: float  # Beta系数
    tracking_error: float  # 跟踪误差
    information_ratio: float  # 信息比率
    correlation: float  # 相关系数
    win_rate_vs_benchmark: float  # 跑赢基准天数比例


@dataclass
class RollingMetrics:
    """滚动指标"""

    dates: list[date]
    rolling_alpha: list[float]
    rolling_beta: list[float]
    rolling_sharpe: list[float]
    rolling_excess_return: list[float]


@dataclass
class BenchmarkComparisonReport:
    """基准对比报告"""

    strategy_name: str
    benchmark_name: str
    metrics: BenchmarkMetrics
    rolling_metrics: RollingMetrics | None
    analysis_period: tuple[date, date]
    trading_days: int

    def summary(self) -> str:
        """生成摘要报告"""
        m = self.metrics
        lines = [
            "=" * 60,
            "基准对比报告",
            f"策略: {self.strategy_name} vs 基准: {self.benchmark_name}",
            "=" * 60,
            "",
            f"分析期间: {self.analysis_period[0]} ~ {self.analysis_period[1]}",
            f"交易天数: {self.trading_days}",
            "",
            "【策略表现】",
            f"  总收益: {m.strategy_return:.2%}",
            f"  年化收益: {m.strategy_annual_return:.2%}",
            f"  波动率: {m.strategy_volatility:.2%}",
            f"  夏普比率: {m.strategy_sharpe:.2f}",
            f"  最大回撤: {m.strategy_max_drawdown:.2%}",
            "",
            "【基准表现】",
            f"  总收益: {m.benchmark_return:.2%}",
            f"  年化收益: {m.benchmark_annual_return:.2%}",
            f"  波动率: {m.benchmark_volatility:.2%}",
            f"  夏普比率: {m.benchmark_sharpe:.2f}",
            f"  最大回撤: {m.benchmark_max_drawdown:.2%}",
            "",
            "【相对表现】",
            f"  超额收益: {m.excess_return:.2%}",
            f"  年化超额: {m.excess_annual_return:.2%}",
            f"  Alpha: {m.alpha:.4f}",
            f"  Beta: {m.beta:.4f}",
            f"  跟踪误差: {m.tracking_error:.2%}",
            f"  信息比率: {m.information_ratio:.2f}",
            f"  相关系数: {m.correlation:.4f}",
            f"  跑赢基准比例: {m.win_rate_vs_benchmark:.2%}",
            "",
            "=" * 60,
        ]
        return "\n".join(lines)


class BenchmarkComparison:
    """基准对比分析器"""

    def __init__(
        self,
        strategy_nav: dict[date, float],
        benchmark_nav: dict[date, float],
        strategy_name: str = "Strategy",
        benchmark_name: str = "Benchmark",
        risk_free_rate: float = 0.02,
        trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
    ):
        """初始化基准对比分析器

        Args:
            strategy_nav: 策略净值 {date: nav}
            benchmark_nav: 基准净值 {date: nav}
            strategy_name: 策略名称
            benchmark_name: 基准名称
            risk_free_rate: 年化无风险利率
            trading_days_per_year: 年交易日数
        """
        self.strategy_name = strategy_name
        self.benchmark_name = benchmark_name
        self.risk_free_rate = risk_free_rate
        self.trading_days = trading_days_per_year

        # 对齐日期
        common_dates = sorted(set(strategy_nav.keys()) & set(benchmark_nav.keys()))
        self.dates = common_dates
        self.strategy_nav = {d: strategy_nav[d] for d in common_dates}
        self.benchmark_nav = {d: benchmark_nav[d] for d in common_dates}

        # 计算收益率序列
        self.strategy_returns = self._compute_returns(self.strategy_nav)
        self.benchmark_returns = self._compute_returns(self.benchmark_nav)

    def _compute_returns(self, nav: dict[date, float]) -> dict[date, float]:
        """计算日收益率"""
        returns = {}
        dates = sorted(nav.keys())
        for i in range(1, len(dates)):
            prev_date = dates[i - 1]
            curr_date = dates[i]
            returns[curr_date] = nav[curr_date] / nav[prev_date] - 1
        return returns

    def _compute_max_drawdown(self, nav: dict[date, float]) -> float:
        """计算最大回撤"""
        if not nav:
            return 0.0

        nav_values = np.array([nav[d] for d in sorted(nav.keys())])
        cummax = np.maximum.accumulate(nav_values)
        drawdown = (cummax - nav_values) / cummax
        return float(np.max(drawdown))

    def _compute_sharpe(self, returns: np.ndarray) -> float:
        """计算夏普比率"""
        if len(returns) < 2:
            return 0.0

        daily_rf = self.risk_free_rate / self.trading_days
        excess_returns = returns - daily_rf
        mean_excess = np.mean(excess_returns)
        std_returns = np.std(returns, ddof=1)

        if std_returns == 0:
            return 0.0

        return float(mean_excess * np.sqrt(self.trading_days) / std_returns)

    def compute_metrics(self) -> BenchmarkMetrics:
        """计算基准对比指标

        Returns:
            基准对比指标
        """
        if len(self.dates) < 2:
            return BenchmarkMetrics(
                strategy_return=0,
                strategy_annual_return=0,
                strategy_volatility=0,
                strategy_sharpe=0,
                strategy_max_drawdown=0,
                benchmark_return=0,
                benchmark_annual_return=0,
                benchmark_volatility=0,
                benchmark_sharpe=0,
                benchmark_max_drawdown=0,
                excess_return=0,
                excess_annual_return=0,
                alpha=0,
                beta=0,
                tracking_error=0,
                information_ratio=0,
                correlation=0,
                win_rate_vs_benchmark=0,
            )

        # 对齐收益率序列
        common_dates = sorted(set(self.strategy_returns.keys()) & set(self.benchmark_returns.keys()))
        strategy_rets = np.array([self.strategy_returns[d] for d in common_dates])
        benchmark_rets = np.array([self.benchmark_returns[d] for d in common_dates])

        n_days = len(common_dates)
        years = n_days / self.trading_days

        # 策略指标
        strategy_nav_list = [self.strategy_nav[d] for d in sorted(self.strategy_nav.keys())]
        strategy_return = strategy_nav_list[-1] / strategy_nav_list[0] - 1
        strategy_annual_return = (1 + strategy_return) ** (1 / years) - 1 if years > 0 else 0
        strategy_volatility = float(np.std(strategy_rets, ddof=1) * np.sqrt(self.trading_days)) if len(strategy_rets) >= 2 else 0.0
        strategy_sharpe = self._compute_sharpe(strategy_rets)
        strategy_max_dd = self._compute_max_drawdown(self.strategy_nav)

        # 基准指标
        benchmark_nav_list = [self.benchmark_nav[d] for d in sorted(self.benchmark_nav.keys())]
        benchmark_return = benchmark_nav_list[-1] / benchmark_nav_list[0] - 1
        benchmark_annual_return = (1 + benchmark_return) ** (1 / years) - 1 if years > 0 else 0
        benchmark_volatility = float(np.std(benchmark_rets, ddof=1) * np.sqrt(self.trading_days)) if len(benchmark_rets) >= 2 else 0.0
        benchmark_sharpe = self._compute_sharpe(benchmark_rets)
        benchmark_max_dd = self._compute_max_drawdown(self.benchmark_nav)

        # 相对指标
        excess_return = strategy_return - benchmark_return
        excess_annual_return = strategy_annual_return - benchmark_annual_return

        # Alpha和Beta (CAPM回归)
        if len(benchmark_rets) > 1 and np.std(benchmark_rets) > 0:
            slope, intercept, r_value, _, _ = stats.linregress(benchmark_rets, strategy_rets)
            beta = float(slope)
            alpha = float(intercept * self.trading_days)  # 年化
            correlation = float(r_value)
        else:
            beta = 1.0
            alpha = 0.0
            correlation = 0.0

        # 跟踪误差
        excess_daily_rets = strategy_rets - benchmark_rets
        tracking_error = float(np.std(excess_daily_rets, ddof=1) * np.sqrt(self.trading_days)) if len(excess_daily_rets) >= 2 else 0.0

        # 信息比率
        if tracking_error > 0:
            information_ratio = excess_annual_return / tracking_error
        else:
            information_ratio = 0.0

        # 跑赢基准比例
        win_rate = np.mean(strategy_rets > benchmark_rets)

        return BenchmarkMetrics(
            strategy_return=strategy_return,
            strategy_annual_return=strategy_annual_return,
            strategy_volatility=strategy_volatility,
            strategy_sharpe=strategy_sharpe,
            strategy_max_drawdown=strategy_max_dd,
            benchmark_return=benchmark_return,
            benchmark_annual_return=benchmark_annual_return,
            benchmark_volatility=benchmark_volatility,
            benchmark_sharpe=benchmark_sharpe,
            benchmark_max_drawdown=benchmark_max_dd,
            excess_return=excess_return,
            excess_annual_return=excess_annual_return,
            alpha=alpha,
            beta=beta,
            tracking_error=tracking_error,
            information_ratio=information_ratio,
            correlation=correlation,
            win_rate_vs_benchmark=float(win_rate),
        )

    def compute_rolling_metrics(
        self,
        window: int = 60,
    ) -> RollingMetrics:
        """计算滚动指标

        Args:
            window: 滚动窗口大小（天）

        Returns:
            滚动指标
        """
        common_dates = sorted(set(self.strategy_returns.keys()) & set(self.benchmark_returns.keys()))
        strategy_rets = np.array([self.strategy_returns[d] for d in common_dates])
        benchmark_rets = np.array([self.benchmark_returns[d] for d in common_dates])

        dates_out = []
        rolling_alpha = []
        rolling_beta = []
        rolling_sharpe = []
        rolling_excess = []

        for i in range(window, len(common_dates)):
            window_strategy = strategy_rets[i - window:i]
            window_benchmark = benchmark_rets[i - window:i]

            # Beta和Alpha
            if np.std(window_benchmark) > 0:
                slope, intercept, _, _, _ = stats.linregress(window_benchmark, window_strategy)
                rolling_beta.append(float(slope))
                rolling_alpha.append(float(intercept * self.trading_days))
            else:
                rolling_beta.append(1.0)
                rolling_alpha.append(0.0)

            # Sharpe
            rolling_sharpe.append(self._compute_sharpe(window_strategy))

            # 超额收益
            excess = np.mean(window_strategy - window_benchmark) * self.trading_days
            rolling_excess.append(float(excess))

            dates_out.append(common_dates[i])

        return RollingMetrics(
            dates=dates_out,
            rolling_alpha=rolling_alpha,
            rolling_beta=rolling_beta,
            rolling_sharpe=rolling_sharpe,
            rolling_excess_return=rolling_excess,
        )

    def generate_report(
        self,
        include_rolling: bool = True,
        rolling_window: int = 60,
    ) -> BenchmarkComparisonReport:
        """生成基准对比报告

        Args:
            include_rolling: 是否包含滚动分析
            rolling_window: 滚动窗口

        Returns:
            基准对比报告
        """
        metrics = self.compute_metrics()

        rolling_metrics = None
        if include_rolling and len(self.dates) > rolling_window:
            rolling_metrics = self.compute_rolling_metrics(rolling_window)

        return BenchmarkComparisonReport(
            strategy_name=self.strategy_name,
            benchmark_name=self.benchmark_name,
            metrics=metrics,
            rolling_metrics=rolling_metrics,
            analysis_period=(self.dates[0], self.dates[-1]) if self.dates else (date.today(), date.today()),
            trading_days=len(self.dates),
        )


def compare_with_benchmark(
    strategy_nav: dict[date, float],
    benchmark_nav: dict[date, float],
    strategy_name: str = "Strategy",
    benchmark_name: str = "Benchmark",
) -> BenchmarkComparisonReport:
    """便捷函数：基准对比

    Args:
        strategy_nav: 策略净值
        benchmark_nav: 基准净值
        strategy_name: 策略名称
        benchmark_name: 基准名称

    Returns:
        基准对比报告
    """
    comparison = BenchmarkComparison(strategy_nav, benchmark_nav, strategy_name, benchmark_name)
    return comparison.generate_report()
