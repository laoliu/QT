"""速率限制器模块

提供线程安全的速率限制功能，用于控制数据源 API 调用频率，
防止因请求过快被数据源封禁。

支持从 .env 文件读取配置，方便动态调整。

使用示例：
    from aipha.data.rate_limiter import RateLimiter, get_akshare_limiter

    # 使用预配置的 AKShare 限制器
    limiter = get_akshare_limiter()
    limiter.acquire()  # 阻塞直到可以发起请求
    # ... 发起 API 请求 ...

    # 或者自定义限制器
    custom_limiter = RateLimiter(
        requests_per_second=2.0,
        min_interval=0.3,
    )
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _load_env_file() -> dict[str, str]:
    """加载 .env 文件配置"""
    env_vars: dict[str, str] = {}
    
    # 查找 .env 文件（从当前目录向上查找）
    current = Path.cwd()
    env_path: Optional[Path] = None
    
    for _ in range(5):  # 最多向上查找 5 层
        candidate = current / ".env"
        if candidate.exists():
            env_path = candidate
            break
        if current.parent == current:
            break
        current = current.parent
    
    if not env_path:
        return env_vars
    
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    key = key.strip()
                    value = value.strip()
                    # 去掉可能的引号
                    if value.startswith('"') and value.endswith('"'):
                        value = value[1:-1]
                    elif value.startswith("'") and value.endswith("'"):
                        value = value[1:-1]
                    env_vars[key] = value
    except Exception as e:
        logger.warning(f"加载 .env 文件失败: {e}")
    
    return env_vars


def _get_env(key: str, default: str = "") -> str:
    """获取环境变量，优先从 .env 文件读取"""
    # 先检查系统环境变量
    value = os.environ.get(key)
    if value is not None:
        return value
    
    # 再从 .env 文件读取
    env_vars = _load_env_file()
    return env_vars.get(key, default)


def _get_env_float(key: str, default: float) -> float:
    """获取浮点数环境变量"""
    value = _get_env(key, str(default))
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def _get_env_int(key: str, default: int) -> int:
    """获取整数环境变量"""
    value = _get_env(key, str(default))
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


@dataclass
class RateLimitConfig:
    """速率限制配置

    Attributes:
        requests_per_second: 每秒最大请求数
        min_interval: 请求间最小间隔（秒）
        batch_interval: 批量请求间隔（秒）
        max_retries: 最大重试次数
        backoff_factor: 重试退避因子
    """

    requests_per_second: float = 1.5
    min_interval: float = 0.5
    batch_interval: float = 1.0
    max_retries: int = 3
    backoff_factor: float = 2.0

    @classmethod
    def from_env(cls, prefix: str = "AKSHARE") -> "RateLimitConfig":
        """从环境变量加载配置

        Args:
            prefix: 环境变量前缀（如 AKSHARE）

        Returns:
            配置对象
        """
        return cls(
            requests_per_second=_get_env_float(
                f"{prefix}_RATE_LIMIT_REQUESTS_PER_SECOND", 1.5
            ),
            min_interval=_get_env_float(
                f"{prefix}_RATE_LIMIT_MIN_INTERVAL", 0.5
            ),
            batch_interval=_get_env_float(
                f"{prefix}_RATE_LIMIT_BATCH_INTERVAL", 1.0
            ),
            max_retries=_get_env_int(f"{prefix}_MAX_RETRIES", 3),
            backoff_factor=_get_env_float(f"{prefix}_BACKOFF_FACTOR", 2.0),
        )


class RateLimiter:
    """线程安全的速率限制器

    使用令牌桶算法控制请求速率，支持：
    - 每秒请求数限制
    - 最小请求间隔
    - 批量请求间隔

    线程安全，可在多线程环境中使用。

    Args:
        requests_per_second: 每秒最大请求数
        min_interval: 请求间最小间隔（秒）
        batch_interval: 批量请求间隔（秒，用于标记批量请求后的额外等待）
        name: 限制器名称（用于日志）
    """

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

        # 令牌桶参数
        self._tokens = requests_per_second  # 当前可用令牌
        self._max_tokens = requests_per_second * 2  # 最大令牌数（允许短暂突发）
        self._last_refill = time.monotonic()
        self._last_request = 0.0  # 上次请求时间

        # 统计
        self._total_requests = 0
        self._total_wait_time = 0.0

        # 线程锁
        self._lock = threading.Lock()

        logger.debug(
            f"初始化速率限制器 [{name}]: "
            f"requests_per_second={requests_per_second}, "
            f"min_interval={min_interval}s"
        )

    def acquire(self, timeout: Optional[float] = None) -> bool:
        """获取一个请求令牌

        阻塞直到可以发起请求，或超时返回 False。

        Args:
            timeout: 超时时间（秒），None 表示无限等待。
                     为防止意外死锁，内部上限 120 秒。

        Returns:
            是否成功获取令牌
        """
        # 安全上限：即使调用者传 None，也不超过 120 秒
        effective_timeout = timeout if timeout is not None else 120.0
        deadline = time.monotonic() + effective_timeout

        while True:
            self._lock.acquire()
            try:
                self._refill_tokens()

                # 计算需要等待的时间
                wait_time = 0.0

                # 检查最小间隔
                since_last = time.monotonic() - self._last_request
                if since_last < self.min_interval:
                    wait_time = max(wait_time, self.min_interval - since_last)

                # 检查令牌
                if self._tokens < 1:
                    # 需要等待令牌恢复
                    tokens_needed = 1 - self._tokens
                    token_wait = tokens_needed / self.requests_per_second
                    wait_time = max(wait_time, token_wait)

                if wait_time <= 0:
                    # 可以立即请求
                    self._tokens -= 1
                    self._last_request = time.monotonic()
                    self._total_requests += 1
                    return True

                # 检查超时
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                wait_time = min(wait_time, remaining)

                # 记录等待时间
                self._total_wait_time += wait_time
                logger.debug(
                    f"速率限制器 [{self.name}] 等待 {wait_time:.3f}s"
                )
            finally:
                self._lock.release()

            # 在锁外等待，让其他线程有机会执行
            time.sleep(wait_time)

    def acquire_batch(self, count: int = 1) -> None:
        """标记即将进行批量请求

        在批量请求开始前调用，会在完成后添加额外的等待时间。

        Args:
            count: 计划请求数量
        """
        with self._lock:
            # 预留一些额外等待时间
            if count > 1:
                logger.debug(
                    f"速率限制器 [{self.name}] 标记批量请求 count={count}"
                )

    def wait_after_batch(self) -> None:
        """批量请求完成后等待

        在批量请求完成后调用，添加额外的冷却时间。
        """
        with self._lock:
            logger.debug(
                f"速率限制器 [{self.name}] 批量请求后等待 {self.batch_interval}s"
            )
        time.sleep(self.batch_interval)

    def _refill_tokens(self) -> None:
        """补充令牌"""
        now = time.monotonic()
        elapsed = now - self._last_refill
        new_tokens = elapsed * self.requests_per_second
        self._tokens = min(self._max_tokens, self._tokens + new_tokens)
        self._last_refill = now

    def stats(self) -> dict:
        """获取统计信息"""
        with self._lock:
            return {
                "name": self.name,
                "total_requests": self._total_requests,
                "total_wait_time": self._total_wait_time,
                "avg_wait_time": (
                    self._total_wait_time / self._total_requests
                    if self._total_requests > 0
                    else 0
                ),
                "current_tokens": self._tokens,
            }

    def reset_stats(self) -> None:
        """重置统计信息"""
        with self._lock:
            self._total_requests = 0
            self._total_wait_time = 0.0

    @classmethod
    def from_config(cls, config: RateLimitConfig, name: str = "default") -> "RateLimiter":
        """从配置创建限制器"""
        return cls(
            requests_per_second=config.requests_per_second,
            min_interval=config.min_interval,
            batch_interval=config.batch_interval,
            name=name,
        )


# 全局限制器实例（单例）
_akshare_limiter: Optional[RateLimiter] = None
_limiter_lock = threading.Lock()


def get_akshare_limiter() -> RateLimiter:
    """获取 AKShare 全局速率限制器（单例）

    从 .env 文件读取配置，所有 AKShare 请求共享同一个限制器。

    Returns:
        速率限制器实例
    """
    global _akshare_limiter

    if _akshare_limiter is not None:
        return _akshare_limiter

    with _limiter_lock:
        if _akshare_limiter is None:
            config = RateLimitConfig.from_env("AKSHARE")
            _akshare_limiter = RateLimiter.from_config(config, name="akshare")
            logger.info(
                f"初始化 AKShare 速率限制器: "
                f"requests_per_second={config.requests_per_second}, "
                f"min_interval={config.min_interval}s, "
                f"batch_interval={config.batch_interval}s"
            )
        return _akshare_limiter


def reset_akshare_limiter() -> None:
    """重置 AKShare 全局限制器（主要用于测试）"""
    global _akshare_limiter
    with _limiter_lock:
        _akshare_limiter = None


def get_rate_limit_config(prefix: str = "AKSHARE") -> RateLimitConfig:
    """获取速率限制配置

    Args:
        prefix: 环境变量前缀

    Returns:
        配置对象
    """
    return RateLimitConfig.from_env(prefix)


# ------------------------------------------------------------------
# TuShare 全局限制器（单例）
# ------------------------------------------------------------------
_tushare_limiter: Optional[RateLimiter] = None
_tushare_lock = threading.Lock()


def get_tushare_limiter() -> RateLimiter:
    """获取 TuShare Pro 全局速率限制器（单例）

    TuShare Pro 免费用户限制：
    - 每分钟最多 200 次请求
    - 同时最多 2 个并发连接（IP 维度）

    可通过 .env 覆盖默认值::

        TUSHARE_RATE_LIMIT_REQUESTS_PER_SECOND=3.0
        TUSHARE_RATE_LIMIT_MIN_INTERVAL=0.35
        TUSHARE_RATE_LIMIT_BATCH_INTERVAL=1.0

    Returns:
        速率限制器实例
    """
    global _tushare_limiter

    if _tushare_limiter is not None:
        return _tushare_limiter

    with _tushare_lock:
        if _tushare_limiter is None:
            config = RateLimitConfig.from_env("TUSHARE")
            # TuShare 限制同时 2 并发，保守设为每秒 2 次，最小间隔 0.5s
            if config.requests_per_second == 1.5:  # 未自定义
                config.requests_per_second = 2.0
            if config.min_interval == 0.5:  # 未自定义
                config.min_interval = 0.5
            _tushare_limiter = RateLimiter.from_config(config, name="tushare")
            logger.info(
                f"初始化 TuShare 速率限制器: "
                f"requests_per_second={config.requests_per_second}, "
                f"min_interval={config.min_interval}s, "
                f"batch_interval={config.batch_interval}s"
            )
        return _tushare_limiter


def reset_tushare_limiter() -> None:
    """重置 TuShare 全局限制器（主要用于测试）"""
    global _tushare_limiter
    with _tushare_lock:
        _tushare_limiter = None


# ------------------------------------------------------------------
# Sina 全局限制器（单例）
# ------------------------------------------------------------------
_sina_limiter: Optional[RateLimiter] = None
_sina_lock = threading.Lock()


def get_sina_limiter() -> RateLimiter:
    """获取 Sina Finance 全局速率限制器（单例）

    Sina hq 接口较宽松，但批量查询需控制频率。
    默认 5 req/s, min_interval 0.2s。

    可通过 .env 覆盖::

        SINA_RATE_LIMIT_REQUESTS_PER_SECOND=5.0
        SINA_RATE_LIMIT_MIN_INTERVAL=0.2
    """
    global _sina_limiter
    if _sina_limiter is not None:
        return _sina_limiter
    with _sina_lock:
        if _sina_limiter is None:
            config = RateLimitConfig.from_env("SINA")
            if config.requests_per_second == 1.5:
                config.requests_per_second = 5.0
            if config.min_interval == 0.5:
                config.min_interval = 0.2
            _sina_limiter = RateLimiter.from_config(config, name="sina")
        return _sina_limiter


# ------------------------------------------------------------------
# Eastmoney 全局限制器（单例）
# ------------------------------------------------------------------
_eastmoney_limiter: Optional[RateLimiter] = None
_eastmoney_lock = threading.Lock()


def get_eastmoney_limiter() -> RateLimiter:
    """获取 Eastmoney 全局速率限制器（单例）

    Eastmoney push2 接口频率适中，默认 3 req/s。

    可通过 .env 覆盖::

        EASTMONEY_RATE_LIMIT_REQUESTS_PER_SECOND=3.0
    """
    global _eastmoney_limiter
    if _eastmoney_limiter is not None:
        return _eastmoney_limiter
    with _eastmoney_lock:
        if _eastmoney_limiter is None:
            config = RateLimitConfig.from_env("EASTMONEY")
            if config.requests_per_second == 1.5:
                config.requests_per_second = 3.0
            if config.min_interval == 0.5:
                config.min_interval = 0.3
            _eastmoney_limiter = RateLimiter.from_config(
                config, name="eastmoney"
            )
        return _eastmoney_limiter


# ------------------------------------------------------------------
# Xueqiu 全局限制器（单例）
# ------------------------------------------------------------------
_xueqiu_limiter: Optional[RateLimiter] = None
_xueqiu_lock = threading.Lock()


def get_xueqiu_limiter() -> RateLimiter:
    """获取 Xueqiu 全局速率限制器（单例）

    雪球反爬较严，保守设为 1 req/s。

    可通过 .env 覆盖::

        XUEQIU_RATE_LIMIT_REQUESTS_PER_SECOND=1.0
    """
    global _xueqiu_limiter
    if _xueqiu_limiter is not None:
        return _xueqiu_limiter
    with _xueqiu_lock:
        if _xueqiu_limiter is None:
            config = RateLimitConfig.from_env("XUEQIU")
            if config.requests_per_second == 1.5:
                config.requests_per_second = 1.0
            if config.min_interval == 0.5:
                config.min_interval = 1.0
            _xueqiu_limiter = RateLimiter.from_config(
                config, name="xueqiu"
            )
        return _xueqiu_limiter
