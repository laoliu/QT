"""速率限制器 — 线程安全, 令牌桶算法"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    requests_per_second: float = 1.5
    min_interval: float = 0.5
    batch_interval: float = 1.0
    max_retries: int = 3
    backoff_factor: float = 2.0


class RateLimiter:
    """线程安全令牌桶速率限制器"""

    def __init__(
        self,
        requests_per_second: float = 1.5,
        min_interval: float = 0.5,
        batch_interval: float = 1.0,
        name: str = "default",
    ):
        self.requests_per_second = requests_per_second
        self.min_interval = min_interval
        self.batch_interval = batch_interval
        self.name = name
        self._tokens = requests_per_second
        self._max_tokens = requests_per_second * 2
        self._last_refill = time.monotonic()
        self._last_request = 0.0
        self._total_requests = 0
        self._total_wait = 0.0
        self._lock = threading.Lock()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._max_tokens, self._tokens + elapsed * self.requests_per_second)
        self._last_refill = now

    def acquire(self, timeout: Optional[float] = None) -> bool:
        deadline = time.monotonic() + (timeout if timeout is not None else 120.0)
        while True:
            with self._lock:
                self._refill()
                wait = 0.0
                since_last = time.monotonic() - self._last_request
                if since_last < self.min_interval:
                    wait = max(wait, self.min_interval - since_last)
                if self._tokens < 1:
                    wait = max(wait, (1 - self._tokens) / self.requests_per_second)
                if wait <= 0:
                    self._tokens -= 1
                    self._last_request = time.monotonic()
                    self._total_requests += 1
                    return True
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                wait = min(wait, remaining)
                self._total_wait += wait
            time.sleep(wait)

    def mark_batch(self):
        with self._lock:
            self._last_request = time.monotonic()
            self._tokens = max(0, self._tokens - 1)

    @property
    def stats(self) -> dict:
        return {"name": self.name, "total_requests": self._total_requests,
                "total_wait_seconds": round(self._total_wait, 2)}


# 预配置限制器工厂
def get_tushare_limiter() -> RateLimiter:
    return RateLimiter(requests_per_second=3.0, min_interval=0.2, name="tushare")


def get_akshare_limiter() -> RateLimiter:
    return RateLimiter(requests_per_second=1.5, min_interval=0.5, name="akshare")


def get_rate_limit_config(prefix: str = "AKSHARE") -> RateLimitConfig:
    """获取速率限制配置。"""
    import os
    rps = float(os.environ.get(f"{prefix}_RATE_RPS", "1.5"))
    interval = float(os.environ.get(f"{prefix}_RATE_INTERVAL", "0.5"))
    return RateLimitConfig(requests_per_second=rps, min_interval=interval)
