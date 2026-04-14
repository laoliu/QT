"""Bar 聚合缓冲 — 多标的时序对齐 & 超时刷新"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class BarEvent:
    symbol: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: float = 0.0
    timestamp: Optional[datetime] = None
    extra: dict[str, Any] = field(default_factory=dict)


class BarBuffer:
    """多标的 Bar 聚合器

    功能:
    - 收集多标的同一时间窗口的 bar, 对齐后统一回调
    - 超时自动刷新 (处理缺失标的)
    - 支持自动模式 (期望标的数) 和手动 flush
    """

    def __init__(
        self,
        expected_symbols: list[str] | None = None,
        timeout_seconds: float = 5.0,
        on_bars: Callable[[dict[str, BarEvent]], None] | None = None,
    ):
        self._expected = set(expected_symbols) if expected_symbols else set()
        self._timeout = timeout_seconds
        self._callback = on_bars
        self._buffer: dict[str, BarEvent] = {}
        self._last_push_time: float = 0.0
        self._flush_count: int = 0

    @property
    def expected_count(self) -> int:
        return len(self._expected)

    @property
    def buffered_count(self) -> int:
        return len(self._buffer)

    @property
    def is_complete(self) -> bool:
        if not self._expected:
            return False
        return self._expected.issubset(self._buffer.keys())

    def set_expected(self, symbols: list[str]) -> None:
        self._expected = set(symbols)

    def push(self, bar: BarEvent) -> Optional[dict[str, BarEvent]]:
        """推入一根 bar, 若触发刷新则返回聚合结果"""
        self._buffer[bar.symbol] = bar
        self._last_push_time = time.monotonic()

        if self.is_complete:
            return self.flush()
        return None

    def push_dict(self, data: dict[str, Any]) -> Optional[dict[str, BarEvent]]:
        """从字典推入 bar"""
        bar = BarEvent(
            symbol=data["symbol"],
            open=data.get("open", 0),
            high=data.get("high", 0),
            low=data.get("low", 0),
            close=data.get("close", 0),
            volume=data.get("volume", 0),
            amount=data.get("amount", 0),
            timestamp=data.get("timestamp"),
        )
        return self.push(bar)

    def check_timeout(self) -> Optional[dict[str, BarEvent]]:
        """检查超时, 若超时则刷新"""
        if not self._buffer:
            return None
        if self._last_push_time > 0:
            elapsed = time.monotonic() - self._last_push_time
            if elapsed >= self._timeout:
                missing = self._expected - self._buffer.keys()
                if missing:
                    logger.warning("Bar 超时, 缺失: %s", missing)
                return self.flush()
        return None

    def flush(self) -> dict[str, BarEvent]:
        """强制刷新, 返回当前缓冲并清空"""
        result = dict(self._buffer)
        self._buffer.clear()
        self._flush_count += 1
        self._last_push_time = 0.0

        if self._callback and result:
            self._callback(result)

        return result

    def reset(self) -> None:
        self._buffer.clear()
        self._flush_count = 0
        self._last_push_time = 0.0

    def stats(self) -> dict[str, Any]:
        return {
            "expected": len(self._expected),
            "buffered": len(self._buffer),
            "flush_count": self._flush_count,
            "symbols": list(self._buffer.keys()),
        }
