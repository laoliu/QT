"""
报告模块 — 轻量级性能指标与报告数据结构

本模块提供独立于回测引擎的性能指标计算，适用于：
- 从 NAV 序列直接计算指标（无需 BacktestResult）
- 脚本/归档/对比等场景

注意：本模块的 PerformanceMetrics 是轻量版本，
详细分析（含 sortino、持仓统计、成本归因等）请使用 aipha.backtest.report 模块。

提供：
- PerformanceMetrics: 轻量性能指标数据类（从 NAV 序列计算）
- ExperimentFingerprint: 实验指纹数据类
- BacktestReport: 轻量回测报告数据类
- calculate_metrics: 从 NAV 序列计算性能指标
"""

import math
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

import numpy as np

from aipha.utils.metrics import (
    compute_sharpe_ratio as _compute_sharpe,
    compute_sortino_ratio as _compute_sortino,
    compute_max_drawdown as _compute_mdd,
    compute_annualized_return as _compute_ar,
    compute_calmar_ratio as _compute_calmar,
)


@dataclass(frozen=True)
class PerformanceMetrics:
    """
    性能指标

    Attributes:
        total_return: 总收益率
        annual_return: 年化收益率
        max_drawdown: 最大回撤
        sharpe_ratio: 夏普比率
        calmar_ratio: 卡尔玛比率（年化收益/最大回撤）
        volatility: 年化波动率
        sortino_ratio: 索提诺比率（仅计算下行波动）
        win_rate: 胜率（可选）
        turnover: 换手率（可选）
        # 基准相对指标（需提供 benchmark NAV 时才有值）
        excess_return: 超额总收益率（可选）
        excess_annual_return: 年化超额收益率（可选）
        alpha: Jensen's Alpha（可选）
        beta: Beta 系数（可选）
        tracking_error: 跟踪误差（可选）
        information_ratio: 信息比率（可选）
        win_rate_vs_benchmark: 跑赢基准日比例（可选）
    """

    total_return: float
    annual_return: float
    max_drawdown: float
    sharpe_ratio: float
    calmar_ratio: float
    volatility: float
    sortino_ratio: float = 0.0
    win_rate: Optional[float] = None
    turnover: Optional[float] = None
    # 基准相对指标
    excess_return: Optional[float] = None
    excess_annual_return: Optional[float] = None
    alpha: Optional[float] = None
    beta: Optional[float] = None
    tracking_error: Optional[float] = None
    information_ratio: Optional[float] = None
    win_rate_vs_benchmark: Optional[float] = None

    def to_dict(self) -> dict:
        """转换为字典"""
        result = {
            "total_return": self.total_return,
            "annual_return": self.annual_return,
            "max_drawdown": self.max_drawdown,
            "sharpe_ratio": self.sharpe_ratio,
            "calmar_ratio": self.calmar_ratio,
            "volatility": self.volatility,
            "sortino_ratio": self.sortino_ratio,
        }
        if self.win_rate is not None:
            result["win_rate"] = self.win_rate
        if self.turnover is not None:
            result["turnover"] = self.turnover
        # 基准相对指标
        for field in (
            "excess_return", "excess_annual_return", "alpha",
            "beta", "tracking_error", "information_ratio",
            "win_rate_vs_benchmark",
        ):
            val = getattr(self, field)
            if val is not None:
                result[field] = val
        return result


@dataclass(frozen=True)
class ExperimentFingerprint:
    """
    实验指纹

    用于确保实验可复现，记录关键环境信息。

    Attributes:
        git_commit: Git提交哈希
        config_hash: 配置文件哈希
        data_fingerprint: 数据指纹
        random_seed: 随机种子
        run_time: 运行时间
    """

    git_commit: str
    config_hash: str
    data_fingerprint: str
    random_seed: int
    run_time: datetime

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "git_commit": self.git_commit,
            "config_hash": self.config_hash,
            "data_fingerprint": self.data_fingerprint,
            "random_seed": self.random_seed,
            "run_time": self.run_time.isoformat(),
        }


@dataclass
class BacktestReport:
    """
    回测报告

    Attributes:
        strategy_name: 策略名称
        start_date: 开始日期
        end_date: 结束日期
        initial_capital: 初始资金
        final_capital: 最终资金
        metrics: 性能指标
        fingerprint: 实验指纹（可选）
        exclusion_stats: 剔除统计（可选）
        benchmark_name: 基准名称（可选）
        cost_breakdown: 成本分解（可选）
    """

    strategy_name: str
    start_date: date
    end_date: date
    initial_capital: Decimal
    final_capital: Decimal
    metrics: PerformanceMetrics
    fingerprint: Optional[ExperimentFingerprint] = None
    exclusion_stats: Optional[dict[str, int]] = None
    benchmark_name: Optional[str] = None
    cost_breakdown: Optional[dict[str, float]] = None

    def to_dict(self) -> dict:
        """转换为字典"""
        result = {
            "strategy_name": self.strategy_name,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "initial_capital": float(self.initial_capital),
            "final_capital": float(self.final_capital),
            "metrics": self.metrics.to_dict(),
        }
        if self.fingerprint is not None:
            result["fingerprint"] = self.fingerprint.to_dict()
        if self.exclusion_stats is not None:
            result["exclusion_stats"] = self.exclusion_stats
        if self.benchmark_name is not None:
            result["benchmark_name"] = self.benchmark_name
        if self.cost_breakdown is not None:
            result["cost_breakdown"] = self.cost_breakdown
        return result


def calculate_metrics(
    nav_series: list[tuple[date, float]],
    risk_free_rate: float = 0.02,
    trading_days_per_year: int = 252,
) -> PerformanceMetrics:
    """
    从NAV序列计算性能指标 — §13.2 委托 aipha.utils.metrics 共享模块

    Args:
        nav_series: NAV序列，每个元素为(日期, NAV值)
        risk_free_rate: 无风险利率（年化），默认0.02
        trading_days_per_year: 每年交易日数，默认252

    Returns:
        PerformanceMetrics: 计算得到的性能指标

    Raises:
        ValueError: NAV序列为空时抛出
    """
    if not nav_series:
        raise ValueError("NAV序列不能为空")

    # 按日期排序
    sorted_navs = sorted(nav_series, key=lambda x: x[0])
    navs = [nav for _, nav in sorted_navs]
    navs_arr = np.array(navs, dtype=np.float64)

    # 总收益率
    total_return = (navs[-1] - navs[0]) / navs[0]

    # 计算日收益率序列
    daily_returns: list[float] = []
    for i in range(1, len(navs)):
        daily_return = (navs[i] - navs[i - 1]) / navs[i - 1]
        daily_returns.append(daily_return)

    rets_arr = np.array(daily_returns, dtype=np.float64)

    # 波动率
    if len(daily_returns) < 2:
        volatility = 0.0
    else:
        volatility = float(np.std(rets_arr, ddof=1)) * math.sqrt(
            trading_days_per_year
        )

    # §13.2 委托共享模块计算 Sharpe / Sortino / MDD / AR / Calmar
    sharpe_ratio = _compute_sharpe(
        rets_arr, risk_free_rate=risk_free_rate,
        trading_days=trading_days_per_year,
    )
    sortino_ratio = _compute_sortino(
        rets_arr, risk_free_rate=risk_free_rate,
        trading_days=trading_days_per_year,
    )
    max_drawdown, _, _ = _compute_mdd(navs_arr)
    annual_return = _compute_ar(
        navs_arr, trading_days=trading_days_per_year,
    )
    calmar_ratio = _compute_calmar(annual_return, max_drawdown)

    return PerformanceMetrics(
        total_return=total_return,
        annual_return=annual_return,
        max_drawdown=max_drawdown,
        sharpe_ratio=sharpe_ratio,
        calmar_ratio=calmar_ratio,
        volatility=volatility,
        sortino_ratio=sortino_ratio,
    )


def calculate_benchmark_relative_metrics(
    strategy_nav: list[tuple[date, float]],
    benchmark_nav: list[tuple[date, float]],
    risk_free_rate: float = 0.02,
    trading_days_per_year: int = 252,
) -> PerformanceMetrics:
    """
    从策略 NAV 和基准 NAV 计算含基准相对指标的性能指标。

    先调用 calculate_metrics 计算绝对指标，然后补充基准相对指标：
    excess_return, excess_annual_return, alpha, beta, tracking_error,
    information_ratio, win_rate_vs_benchmark.

    Args:
        strategy_nav: 策略 NAV 序列，每个元素为 (日期, NAV值)
        benchmark_nav: 基准 NAV 序列，每个元素为 (日期, NAV值)
        risk_free_rate: 无风险利率（年化），默认 0.02
        trading_days_per_year: 每年交易日数，默认 252

    Returns:
        PerformanceMetrics: 含基准相对指标的完整性能指标

    Raises:
        ValueError: NAV 序列为空或对齐后日期不足
    """
    # 绝对指标
    base_metrics = calculate_metrics(
        strategy_nav, risk_free_rate, trading_days_per_year
    )

    # 对齐日期
    strat_map = dict(strategy_nav)
    bench_map = dict(benchmark_nav)
    common_dates = sorted(set(strat_map.keys()) & set(bench_map.keys()))

    if len(common_dates) < 2:
        return base_metrics  # 无法计算相对指标

    strat_vals = [strat_map[d] for d in common_dates]
    bench_vals = [bench_map[d] for d in common_dates]

    # 日收益率
    strat_rets = [
        (strat_vals[i] - strat_vals[i - 1]) / strat_vals[i - 1]
        for i in range(1, len(strat_vals))
    ]
    bench_rets = [
        (bench_vals[i] - bench_vals[i - 1]) / bench_vals[i - 1]
        for i in range(1, len(bench_vals))
    ]

    n = len(strat_rets)

    # 超额收益
    excess_total = strat_vals[-1] / strat_vals[0] - bench_vals[-1] / bench_vals[0]
    excess_daily = [s - b for s, b in zip(strat_rets, bench_rets)]

    # 年化超额
    strat_growth = strat_vals[-1] / strat_vals[0]
    bench_growth = bench_vals[-1] / bench_vals[0]
    period_years = n / trading_days_per_year
    if period_years > 0 and strat_growth > 0 and bench_growth > 0:
        excess_annual = (
            strat_growth ** (1 / period_years) - bench_growth ** (1 / period_years)
        )
    else:
        excess_annual = 0.0

    # Alpha, Beta (OLS: strat_ret = alpha + beta * bench_ret + epsilon)
    mean_s = sum(strat_rets) / n
    mean_b = sum(bench_rets) / n
    cov_sb = sum((s - mean_s) * (b - mean_b) for s, b in zip(strat_rets, bench_rets)) / n
    var_b = sum((b - mean_b) ** 2 for b in bench_rets) / n

    if var_b > 1e-15:
        beta_val = cov_sb / var_b
        daily_rf = risk_free_rate / trading_days_per_year
        alpha_daily = mean_s - daily_rf - beta_val * (mean_b - daily_rf)
        alpha_val = alpha_daily * trading_days_per_year
    else:
        beta_val = 0.0
        alpha_val = 0.0

    # Tracking Error
    mean_excess = sum(excess_daily) / n
    te_variance = sum((e - mean_excess) ** 2 for e in excess_daily) / max(n - 1, 1)
    te_daily = math.sqrt(te_variance)
    tracking_error = te_daily * math.sqrt(trading_days_per_year)

    # Information Ratio
    if tracking_error > 1e-15:
        ir = (mean_excess * trading_days_per_year) / tracking_error
    else:
        ir = 0.0

    # Win rate vs benchmark
    wins = sum(1 for e in excess_daily if e > 0)
    win_rate_bench = wins / n

    # 构造含基准指标的完整 PerformanceMetrics
    return PerformanceMetrics(
        total_return=base_metrics.total_return,
        annual_return=base_metrics.annual_return,
        max_drawdown=base_metrics.max_drawdown,
        sharpe_ratio=base_metrics.sharpe_ratio,
        calmar_ratio=base_metrics.calmar_ratio,
        volatility=base_metrics.volatility,
        sortino_ratio=base_metrics.sortino_ratio,
        win_rate=base_metrics.win_rate,
        turnover=base_metrics.turnover,
        excess_return=excess_total,
        excess_annual_return=excess_annual,
        alpha=alpha_val,
        beta=beta_val,
        tracking_error=tracking_error,
        information_ratio=ir,
        win_rate_vs_benchmark=win_rate_bench,
    )
