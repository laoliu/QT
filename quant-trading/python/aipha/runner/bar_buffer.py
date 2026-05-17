"""
多标的 Bar 聚合缓冲区

策略通常需要同一时间点的多标的 Bar（dict[str, Bar]）作为输入。
但实时/回放场景中，单标的 Bar 逐条到达。

BarBuffer 按 end_ts 聚合 Bar，在以下条件之一满足时触发 flush：
1. 所有已订阅标的都已收到当前窗口的 bar
2. 超过 deadline（实时模式下的最大等待时间）

设计原则：
- 与 LiveRunner 解耦，可独立测试
- 支持实时（带 deadline）和回放（无 deadline）两种模式
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Callable

from aipha.domain.models import Bar


logger = logging.getLogger(__name__)

# 当 flush 发生时的回调签名
FlushCallback = Callable[[dict[str, Bar]], None]


class BarBuffer:
    """多标的 Bar 聚合缓冲区

    Usage::

        buffer = BarBuffer(symbols=["000001.SZ", "600000.SH"])
        buffer.on_flush(lambda bars: strategy.on_bar(bars, ctx))

        # 逐条喂入
        buffer.add(bar_000001)  # 缓冲
        buffer.add(bar_600000)  # 两个都到齐 → 自动 flush

    Args:
        symbols: 订阅的标的列表。None 表示自动模式（不等待齐全）
        max_wait_sec: 最大等待时间（秒），超时后即使不齐也 flush。
                      0 = 仅靠齐全触发（回放模式）。
    """

    def __init__(
        self,
        symbols: list[str] | None = None,
        max_wait_sec: float = 0.0,
    ):
        self._expected_symbols: set[str] | None = (
            set(symbols) if symbols else None
        )
        self._max_wait = timedelta(seconds=max_wait_sec) if max_wait_sec > 0 else None

        # end_ts → {symbol: Bar}
        self._pending: dict[datetime, dict[str, Bar]] = defaultdict(dict)
        self._pending_start: dict[datetime, datetime] = {}  # 第一条 bar 到达的真实时间

        self._flush_callbacks: list[FlushCallback] = []

    # ── 公共 API ──

    def add(self, bar: Bar) -> list[dict[str, Bar]]:
        """添加一根 Bar

        如果这导致某个窗口"齐全"或前一个窗口过期，自动 flush。

        Returns:
            本次 add 触发 flush 的 bars dict 列表（0 或多个）
        """
        key = bar.end_ts
        self._pending[key][bar.symbol] = bar

        if key not in self._pending_start:
            self._pending_start[key] = datetime.now()

        flushed: list[dict[str, Bar]] = []

        # 1. 检查是否有前序窗口需要 flush（新窗口的到来意味着旧窗口应结束）
        flushed.extend(self._flush_older_windows(key))

        # 2. 检查当前窗口是否齐全
        if self._is_window_complete(key):
            bars_dict = self._pending.pop(key)
            self._pending_start.pop(key, None)
            flushed.append(bars_dict)

        for bars in flushed:
            self._dispatch(bars)

        return flushed

    def flush_all(self) -> list[dict[str, Bar]]:
        """强制 flush 所有待处理窗口（按 end_ts 排序）

        用于回放结束或断线时。

        Returns:
            所有窗口的 bars dict 列表
        """
        result: list[dict[str, Bar]] = []
        for key in sorted(self._pending.keys()):
            bars_dict = self._pending[key]
            result.append(bars_dict)
            self._dispatch(bars_dict)
        self._pending.clear()
        self._pending_start.clear()
        return result

    def check_timeout(self) -> list[dict[str, Bar]]:
        """检查超时窗口（实时模式下定期调用）

        Returns:
            已超时被 flush 的窗口列表
        """
        if self._max_wait is None:
            return []

        now = datetime.now()
        expired: list[datetime] = []

        for key, start_time in self._pending_start.items():
            if now - start_time >= self._max_wait:
                expired.append(key)

        result: list[dict[str, Bar]] = []
        for key in sorted(expired):
            bars_dict = self._pending.pop(key)
            self._pending_start.pop(key, None)
            logger.debug(
                f"BarBuffer timeout flush: end_ts={key}, "
                f"symbols={list(bars_dict.keys())}"
            )
            result.append(bars_dict)
            self._dispatch(bars_dict)

        return result

    def on_flush(self, callback: FlushCallback) -> None:
        """注册 flush 回调"""
        self._flush_callbacks.append(callback)

    def update_symbols(self, symbols: list[str]) -> None:
        """更新期望标的列表"""
        self._expected_symbols = set(symbols)

    @property
    def pending_count(self) -> int:
        """待处理窗口数"""
        return len(self._pending)

    def clear(self) -> None:
        """清空缓冲区"""
        self._pending.clear()
        self._pending_start.clear()

    # ── 内部方法 ──

    def _is_window_complete(self, key: datetime) -> bool:
        """检查窗口是否齐全"""
        if self._expected_symbols is None:
            # 自动模式：无法判断齐全，不自动 flush
            return False
        return set(self._pending[key].keys()) >= self._expected_symbols

    def _flush_older_windows(self, current_key: datetime) -> list[dict[str, Bar]]:
        """flush 比 current_key 更早的窗口"""
        older = sorted(k for k in self._pending if k < current_key)
        result: list[dict[str, Bar]] = []
        for key in older:
            bars_dict = self._pending.pop(key)
            self._pending_start.pop(key, None)
            result.append(bars_dict)
        return result

    def _dispatch(self, bars_dict: dict[str, Bar]) -> None:
        """分发 flush 事件"""
        for callback in self._flush_callbacks:
            try:
                callback(bars_dict)
            except Exception as e:
                logger.error(f"BarBuffer flush callback error: {e}")


def group_bars_by_end_ts(bars: list[Bar]) -> list[dict[str, Bar]]:
    """将 Bar 列表按 end_ts 分组

    回放模式下的便捷函数。

    Args:
        bars: 混合标的的 Bar 列表

    Returns:
        按 end_ts 排序的 dict[str, Bar] 列表
    """
    groups: dict[datetime, dict[str, Bar]] = defaultdict(dict)
    for bar in bars:
        groups[bar.end_ts][bar.symbol] = bar

    return [groups[ts] for ts in sorted(groups.keys())]
