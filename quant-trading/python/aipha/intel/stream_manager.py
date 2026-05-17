"""Intel 流管理器 — 异步任务管理 + 去重 + 事件广播。

管理所有 IntelProvider 的异步拉取/推送任务，
通过 SimHash 去重避免重复新闻，
通过 asyncio.Queue 广播事件给多个消费者。

遵循 docs/design/20_specs/intel_advisory_qmt_architecture.md §2.4。
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import deque
from datetime import datetime
from typing import Any, Callable

from aipha.intel.models import NewsItem
from aipha.intel.provider import IntelProvider

logger = logging.getLogger(__name__)


def _simhash_text(text: str, bits: int = 64) -> int:
    """简易 SimHash 实现（用于新闻去重）。"""
    v = [0] * bits
    tokens = text.split()
    for token in tokens:
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)  # noqa: S324
        for i in range(bits):
            if h & (1 << i):
                v[i] += 1
            else:
                v[i] -= 1
    fingerprint = 0
    for i in range(bits):
        if v[i] > 0:
            fingerprint |= (1 << i)
    return fingerprint


def _hamming_distance(a: int, b: int) -> int:
    """计算两个整数的汉明距离。"""
    return bin(a ^ b).count("1")


class DedupFilter:
    """SimHash 去重过滤器（Hamming distance ≤ 3 判定为重复）。"""

    def __init__(self, threshold: int = 3, max_window: int = 5000) -> None:
        self._threshold = threshold
        self._hashes: deque[int] = deque(maxlen=max_window)

    def is_duplicate(self, text: str) -> bool:
        h = _simhash_text(text)
        for existing in self._hashes:
            if _hamming_distance(h, existing) <= self._threshold:
                return True
        self._hashes.append(h)
        return False

    def clear(self) -> None:
        self._hashes.clear()


class StreamManager:
    """Intel 流管理器 — 管理 Provider 轮询 + 事件广播。"""

    def __init__(
        self,
        providers: list[IntelProvider] | None = None,
        poll_interval: float = 30.0,
    ) -> None:
        self._providers: list[IntelProvider] = providers or []
        self._poll_interval = poll_interval
        self._dedup = DedupFilter()
        self._subscribers: list[asyncio.Queue[NewsItem]] = []
        self._running = False
        self._tasks: list[asyncio.Task[None]] = []

    def add_provider(self, provider: IntelProvider) -> None:
        self._providers.append(provider)

    def subscribe(self) -> asyncio.Queue[NewsItem]:
        """注册一个消费者队列。"""
        q: asyncio.Queue[NewsItem] = asyncio.Queue(maxsize=1000)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[NewsItem]) -> None:
        self._subscribers = [s for s in self._subscribers if s is not q]

    async def start(self) -> None:
        """启动所有 Provider 的轮询任务。"""
        self._running = True
        for provider in self._providers:
            task = asyncio.create_task(
                self._poll_provider(provider),
                name=f"intel-poll-{provider.name}",
            )
            self._tasks.append(task)
        logger.info("StreamManager started with %d providers",
                     len(self._providers))

    async def stop(self) -> None:
        """停止所有轮询。"""
        self._running = False
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        logger.info("StreamManager stopped")

    async def _poll_provider(self, provider: IntelProvider) -> None:
        """单个 Provider 的轮询循环。"""
        while self._running:
            try:
                if not await provider.health_check():
                    await asyncio.sleep(self._poll_interval * 2)
                    continue
                items = await provider.fetch_news(limit=50)
                for item in items:
                    if self._dedup.is_duplicate(item.title):
                        continue
                    await self._broadcast(item)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("Poll %s failed", provider.name,
                               exc_info=True)
            await asyncio.sleep(self._poll_interval)

    async def _broadcast(self, item: NewsItem) -> None:
        """广播一条新闻到所有消费者。"""
        for q in self._subscribers:
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                logger.debug("Subscriber queue full, dropping item")
