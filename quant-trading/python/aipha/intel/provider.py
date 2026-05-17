"""Intel Provider 协议与注册表。

定义 IntelProvider Protocol + ProviderRegistry（优先级路由）。
遵循 docs/design/20_specs/intel_advisory_qmt_architecture.md §2.3。
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from aipha.intel.models import (
    IntelProviderPriority,
    NewsItem,
    ResearchReport,
)

logger = logging.getLogger(__name__)


@runtime_checkable
class IntelProvider(Protocol):
    """情报源协议 — 所有 provider 必须实现。"""

    @property
    def name(self) -> str: ...

    @property
    def priority(self) -> IntelProviderPriority: ...

    async def fetch_news(
        self,
        symbols: list[str] | None = None,
        since: str | None = None,
        limit: int = 50,
    ) -> list[NewsItem]: ...

    async def fetch_reports(
        self,
        symbols: list[str] | None = None,
        since: str | None = None,
        limit: int = 20,
    ) -> list[ResearchReport]: ...

    async def health_check(self) -> bool: ...


class ProviderRegistry:
    """Provider 注册表 — 按优先级管理多个情报源。"""

    def __init__(self) -> None:
        self._providers: list[IntelProvider] = []

    def register(self, provider: IntelProvider) -> None:
        self._providers.append(provider)
        self._providers.sort(key=lambda p: p.priority)
        logger.info("Intel provider registered: %s (P%d)",
                     provider.name, provider.priority)

    def unregister(self, name: str) -> None:
        self._providers = [p for p in self._providers if p.name != name]

    @property
    def providers(self) -> list[IntelProvider]:
        return list(self._providers)

    def get_by_name(self, name: str) -> IntelProvider | None:
        for p in self._providers:
            if p.name == name:
                return p
        return None

    async def fetch_news_cascade(
        self,
        symbols: list[str] | None = None,
        limit: int = 50,
    ) -> list[NewsItem]:
        """按优先级逐个尝试获取新闻，首个成功即返回。"""
        for provider in self._providers:
            try:
                if not await provider.health_check():
                    continue
                items = await provider.fetch_news(
                    symbols=symbols, limit=limit,
                )
                if items:
                    return items
            except Exception:
                logger.warning("Provider %s fetch_news failed",
                               provider.name, exc_info=True)
        return []

    async def fetch_all_news(
        self,
        symbols: list[str] | None = None,
        limit: int = 50,
    ) -> list[NewsItem]:
        """从所有健康 provider 聚合新闻（去重由上层处理）。"""
        all_items: list[NewsItem] = []
        for provider in self._providers:
            try:
                if not await provider.health_check():
                    continue
                items = await provider.fetch_news(
                    symbols=symbols, limit=limit,
                )
                all_items.extend(items)
            except Exception:
                logger.warning("Provider %s failed", provider.name,
                               exc_info=True)
        return all_items
