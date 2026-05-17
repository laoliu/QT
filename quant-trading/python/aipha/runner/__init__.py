"""
实时运行器模块

事件驱动的策略执行环境，支持：
- 回测模式 (Backtest)
- 仿真模式 (Paper Trading)
- 实盘模式 (Live Trading)
- 多策略组合
"""

from aipha.runner.live_runner import LiveRunner, RunnerConfig, RunnerMode
from aipha.runner.multi_strategy import (
    MultiStrategyRunner,
    PortfolioConfig,
    StrategyAllocation,
    StrategyStatus,
)

__all__ = [
    "LiveRunner",
    "RunnerConfig",
    "RunnerMode",
    "MultiStrategyRunner",
    "PortfolioConfig",
    "StrategyAllocation",
    "StrategyStatus",
]
