"""
信号模块

提供信号生成与评估功能：
- evaluator: 信号质量评估
"""

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

__all__ = [
    "Signal",
    "SignalType",
    "SignalStats",
    "ReturnStats",
    "DecayStats",
    "SignalEvaluationReport",
    "SignalEvaluator",
    "evaluate_signals",
]
