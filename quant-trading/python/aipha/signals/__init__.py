"""
信号模块

提供信号生成、聚合、过滤与评估功能：
- evaluator: 信号质量评估
- aggregator: 多策略信号聚合（voting/weighted/priority/rank_avg）
- signal_filter: 信号后处理过滤器
"""

from aipha.signals.aggregator import AggregationMethod, SignalAggregator
from aipha.signals.evaluator import (
    DecayStats,
    ReturnStats,
    Signal,
    SignalEvaluationReport,
    SignalEvaluator,
    SignalStats,
    SignalType,
    evaluate_signals,
)
from aipha.signals.signal_filter import SignalFilter

__all__ = [
    "AggregationMethod",
    "Signal",
    "SignalAggregator",
    "SignalFilter",
    "SignalType",
    "SignalStats",
    "ReturnStats",
    "DecayStats",
    "SignalEvaluationReport",
    "SignalEvaluator",
    "evaluate_signals",
]
