"""降级数据源 — 多源自动切换 + 重试"""
from __future__ import annotations

import logging
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class FallbackProvider:
    """多数据源降级提供器

    按优先级尝试多个数据源, 自动切换到备用源。

    用法:
        provider = FallbackProvider()
        provider.add_source("tushare", tushare_fetch_fn, priority=1)
        provider.add_source("akshare", akshare_fetch_fn, priority=2)
        df = provider.fetch("000001.SZ", "2024-01-01", "2024-06-01")
    """

    def __init__(self, max_retries: int = 2):
        self._sources: list[tuple[int, str, Any]] = []  # (priority, name, fetch_fn)
        self._max_retries = max_retries
        self._stats: dict[str, dict] = {}

    def add_source(self, name: str, fetch_fn: Any, priority: int = 10):
        self._sources.append((priority, name, fetch_fn))
        self._sources.sort(key=lambda x: x[0])
        self._stats[name] = {"success": 0, "fail": 0}

    def fetch(self, *args, **kwargs) -> Optional[pd.DataFrame]:
        last_error = None
        for _, name, fn in self._sources:
            for attempt in range(self._max_retries):
                try:
                    result = fn(*args, **kwargs)
                    if result is not None and (not isinstance(result, pd.DataFrame) or not result.empty):
                        self._stats[name]["success"] += 1
                        return result
                except Exception as e:
                    last_error = e
                    logger.warning("数据源 %s 第%d次失败: %s", name, attempt + 1, e)
            self._stats[name]["fail"] += 1
            logger.info("数据源 %s 全部重试失败, 切换下一个", name)

        if last_error:
            logger.error("所有数据源失败, 最后错误: %s", last_error)
        return None

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    @property
    def source_names(self) -> list[str]:
        return [name for _, name, _ in self._sources]
