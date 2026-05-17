"""选股筛选引擎 — 统一筛选管道。

5 步管道：构建 Universe → 财务数据 → K 线数据 → 执行筛选 → 格式化结果。
移植自 QT screener_engine.py，适配 AlphQ advisory 模块。
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from aipha.advisory.models import ScreenerResult

logger = logging.getLogger(__name__)


@runtime_checkable
class ScreenerStrategy(Protocol):
    """筛选策略协议 — 所有策略必须实现。"""

    @property
    def name(self) -> str: ...

    @property
    def requires_financials(self) -> bool: ...

    @property
    def requires_klines(self) -> bool: ...

    def screen(
        self,
        universe: list[dict[str, Any]],
        **kwargs: Any,
    ) -> list[ScreenerResult]: ...


class ScreenerEngine:
    """选股筛选引擎。"""

    def __init__(self) -> None:
        self._strategies: dict[str, ScreenerStrategy] = {}

    def register(self, strategy: ScreenerStrategy) -> None:
        self._strategies[strategy.name] = strategy
        logger.info("Screener registered: %s", strategy.name)

    def list_strategies(self) -> list[str]:
        return list(self._strategies.keys())

    def run(
        self,
        strategy_name: str,
        universe: list[dict[str, Any]],
        top_n: int = 20,
        **kwargs: Any,
    ) -> list[ScreenerResult]:
        """执行筛选。"""
        strategy = self._strategies.get(strategy_name)
        if strategy is None:
            raise ValueError(f"Unknown screener: {strategy_name}")
        results = strategy.screen(universe, **kwargs)
        results.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results):
            r.rank = i + 1
        return results[:top_n]
