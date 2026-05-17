"""
Reporting 模块

提供回测报告生成、性能指标计算、实验指纹记录、
实验归档与版本管理、多实验对比分析、亏损追踪归因等功能。

本模块包含两套 PerformanceMetrics：
- reporting.report.PerformanceMetrics: 轻量版，从 NAV 序列计算，
  适用于脚本/归档/对比（含 total_return, annual_return, max_drawdown 等）
- backtest.report.PerformanceMetrics: 详细版，从 BacktestResult 计算，
  含 sortino_ratio、trading_days 等完整分析
"""

from aipha.reporting.archiver import ExperimentArchiver
from aipha.reporting.comparator import ExperimentComparator
from aipha.reporting.loss_tracker import (
    DrawdownAttributor,
    LossAnalyzer,
    LossSummary,
    RoundTrip,
    RoundTripTracker,
    analyze_losses,
    format_loss_report,
)
from aipha.reporting.brinson_attribution import (
    BrinsonMultiPeriodResult,
    BrinsonSingleResult,
    brinson_multi_period,
    brinson_single_period,
)
from aipha.reporting.factor_exposure import (
    FactorExposureAnalyzer,
    FactorExposureSnapshot,
    FactorExposureSummary,
)
from aipha.reporting.factor_ic import (
    FactorICAnalyzer,
    FactorICResult,
    ICDecayResult,
)
from aipha.reporting.report import (
    BacktestReport,
    ExperimentFingerprint,
    PerformanceMetrics,
    calculate_benchmark_relative_metrics,
    calculate_metrics,
)

__all__ = [
    "BacktestReport",
    "PerformanceMetrics",
    "ExperimentFingerprint",
    "ExperimentArchiver",
    "ExperimentComparator",
    "calculate_metrics",
    "calculate_benchmark_relative_metrics",
    # 亏损追踪
    "RoundTrip",
    "RoundTripTracker",
    "LossAnalyzer",
    "LossSummary",
    "DrawdownAttributor",
    "analyze_losses",
    "format_loss_report",
    # Brinson 归因
    "BrinsonSingleResult",
    "BrinsonMultiPeriodResult",
    "brinson_single_period",
    "brinson_multi_period",
    # 因子暴露分析
    "FactorExposureAnalyzer",
    "FactorExposureSnapshot",
    "FactorExposureSummary",
    # 因子 IC 分析
    "FactorICAnalyzer",
    "FactorICResult",
    "ICDecayResult",
]
