"""基准对比模块 — 策略 vs 基准 (Alpha/Beta/IR/滚动分析)"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional

import numpy as np
from scipy import stats


@dataclass
class BenchmarkMetrics:
    strategy_return: float
    strategy_annual_return: float
    strategy_volatility: float
    strategy_sharpe: float
    strategy_max_drawdown: float
    benchmark_return: float
    benchmark_annual_return: float
    benchmark_volatility: float
    benchmark_sharpe: float
    benchmark_max_drawdown: float
    excess_return: float
    excess_annual_return: float
    alpha: float
    beta: float
    tracking_error: float
    information_ratio: float
    correlation: float
    win_rate_vs_benchmark: float


@dataclass
class RollingMetrics:
    dates: list[date]
    rolling_alpha: list[float]
    rolling_beta: list[float]
    rolling_sharpe: list[float]
    rolling_excess_return: list[float]


@dataclass
class BenchmarkComparisonReport:
    strategy_name: str
    benchmark_name: str
    metrics: BenchmarkMetrics
    rolling_metrics: Optional[RollingMetrics]
    analysis_period: tuple[date, date]
    trading_days: int

    def summary(self) -> str:
        m = self.metrics
        lines = [
            "=" * 60,
            f"基准对比: {self.strategy_name} vs {self.benchmark_name}",
            f"期间: {self.analysis_period[0]} ~ {self.analysis_period[1]} ({self.trading_days} 天)",
            "=" * 60,
            f"策略: 年化{m.strategy_annual_return:.2%} | 波动{m.strategy_volatility:.2%} | "
            f"Sharpe={m.strategy_sharpe:.2f} | MDD={m.strategy_max_drawdown:.2%}",
            f"基准: 年化{m.benchmark_annual_return:.2%} | 波动{m.benchmark_volatility:.2%} | "
            f"Sharpe={m.benchmark_sharpe:.2f} | MDD={m.benchmark_max_drawdown:.2%}",
            f"超额: {m.excess_annual_return:.2%} | Alpha={m.alpha:.4f} | Beta={m.beta:.4f} | "
            f"TE={m.tracking_error:.2%} | IR={m.information_ratio:.2f} | 跑赢率={m.win_rate_vs_benchmark:.1%}",
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
        trading_days_per_year: int = 252,
    ):
        self.strategy_name = strategy_name
        self.benchmark_name = benchmark_name
        self.risk_free_rate = risk_free_rate
        self.trading_days = trading_days_per_year

        common_dates = sorted(set(strategy_nav.keys()) & set(benchmark_nav.keys()))
        self.dates = common_dates
        self.strategy_nav = {d: strategy_nav[d] for d in common_dates}
        self.benchmark_nav = {d: benchmark_nav[d] for d in common_dates}
        self.strategy_returns = self._compute_returns(self.strategy_nav)
        self.benchmark_returns = self._compute_returns(self.benchmark_nav)

    def _compute_returns(self, nav: dict[date, float]) -> dict[date, float]:
        returns = {}
        dates = sorted(nav.keys())
        for i in range(1, len(dates)):
            returns[dates[i]] = nav[dates[i]] / nav[dates[i - 1]] - 1
        return returns

    def _compute_max_drawdown(self, nav: dict[date, float]) -> float:
        if not nav:
            return 0.0
        vals = np.array([nav[d] for d in sorted(nav.keys())])
        cummax = np.maximum.accumulate(vals)
        return float(np.max((cummax - vals) / cummax))

    def _compute_sharpe(self, returns: np.ndarray) -> float:
        if len(returns) < 2:
            return 0.0
        daily_rf = self.risk_free_rate / self.trading_days
        excess = returns - daily_rf
        std = np.std(returns, ddof=1)
        return float(np.mean(excess) * np.sqrt(self.trading_days) / std) if std > 0 else 0.0

    def compute_metrics(self) -> BenchmarkMetrics:
        if len(self.dates) < 2:
            return BenchmarkMetrics(**{f: 0 for f in BenchmarkMetrics.__dataclass_fields__})

        common = sorted(set(self.strategy_returns) & set(self.benchmark_returns))
        sr = np.array([self.strategy_returns[d] for d in common])
        br = np.array([self.benchmark_returns[d] for d in common])
        n = len(common)
        years = n / self.trading_days

        # Strategy
        s_nav = [self.strategy_nav[d] for d in sorted(self.strategy_nav)]
        s_ret = s_nav[-1] / s_nav[0] - 1
        s_ann = (1 + s_ret) ** (1 / years) - 1 if years > 0 else 0
        s_vol = float(np.std(sr, ddof=1) * np.sqrt(self.trading_days))
        s_sharpe = self._compute_sharpe(sr)
        s_mdd = self._compute_max_drawdown(self.strategy_nav)

        # Benchmark
        b_nav = [self.benchmark_nav[d] for d in sorted(self.benchmark_nav)]
        b_ret = b_nav[-1] / b_nav[0] - 1
        b_ann = (1 + b_ret) ** (1 / years) - 1 if years > 0 else 0
        b_vol = float(np.std(br, ddof=1) * np.sqrt(self.trading_days))
        b_sharpe = self._compute_sharpe(br)
        b_mdd = self._compute_max_drawdown(self.benchmark_nav)

        # Relative
        if len(br) > 1 and np.std(br) > 0:
            slope, intercept, r_val, _, _ = stats.linregress(br, sr)
            beta, alpha, corr = float(slope), float(intercept * self.trading_days), float(r_val)
        else:
            beta, alpha, corr = 1.0, 0.0, 0.0

        excess_daily = sr - br
        te = float(np.std(excess_daily, ddof=1) * np.sqrt(self.trading_days))
        ir = (s_ann - b_ann) / te if te > 0 else 0.0

        return BenchmarkMetrics(
            strategy_return=s_ret, strategy_annual_return=s_ann,
            strategy_volatility=s_vol, strategy_sharpe=s_sharpe, strategy_max_drawdown=s_mdd,
            benchmark_return=b_ret, benchmark_annual_return=b_ann,
            benchmark_volatility=b_vol, benchmark_sharpe=b_sharpe, benchmark_max_drawdown=b_mdd,
            excess_return=s_ret - b_ret, excess_annual_return=s_ann - b_ann,
            alpha=alpha, beta=beta, tracking_error=te,
            information_ratio=ir, correlation=corr,
            win_rate_vs_benchmark=float(np.mean(sr > br)),
        )

    def compute_rolling_metrics(self, window: int = 60) -> RollingMetrics:
        common = sorted(set(self.strategy_returns) & set(self.benchmark_returns))
        sr = np.array([self.strategy_returns[d] for d in common])
        br = np.array([self.benchmark_returns[d] for d in common])
        dates_out, r_alpha, r_beta, r_sharpe, r_excess = [], [], [], [], []

        for i in range(window, len(common)):
            ws, wb = sr[i - window:i], br[i - window:i]
            if np.std(wb) > 0:
                slope, intercept, _, _, _ = stats.linregress(wb, ws)
                r_beta.append(float(slope))
                r_alpha.append(float(intercept * self.trading_days))
            else:
                r_beta.append(1.0)
                r_alpha.append(0.0)
            r_sharpe.append(self._compute_sharpe(ws))
            r_excess.append(float(np.mean(ws - wb) * self.trading_days))
            dates_out.append(common[i])

        return RollingMetrics(dates=dates_out, rolling_alpha=r_alpha,
                              rolling_beta=r_beta, rolling_sharpe=r_sharpe,
                              rolling_excess_return=r_excess)

    def generate_report(self, include_rolling: bool = True, window: int = 60) -> BenchmarkComparisonReport:
        metrics = self.compute_metrics()
        rolling = self.compute_rolling_metrics(window) if include_rolling and len(self.dates) > window else None
        period = (self.dates[0], self.dates[-1]) if self.dates else (date.today(), date.today())
        return BenchmarkComparisonReport(
            strategy_name=self.strategy_name, benchmark_name=self.benchmark_name,
            metrics=metrics, rolling_metrics=rolling,
            analysis_period=period, trading_days=len(self.dates))


def compare_with_benchmark(strategy_nav, benchmark_nav, strategy_name="Strategy", benchmark_name="Benchmark"):
    return BenchmarkComparison(strategy_nav, benchmark_nav, strategy_name, benchmark_name).generate_report()
