"""TuShare Pro 统一数据网关

所有 TuShare API 调用必须通过此网关，不得直接创建 ``ts.pro_api()``
实例或绕过限流/重试机制。

核心职责
--------
1. **单例 API 实例**：全局共享一个 ``ts.pro_api()`` 连接
2. **并发控制**：TuShare 限制同时 2 个并发请求（IP 维度），
   通过 ``threading.Semaphore(max_concurrent)`` 硬性保证
3. **速率限制**：复用 ``get_tushare_limiter()`` 令牌桶算法
4. **自动重试**：指数退避，可配置重试次数与退避因子
5. **统一 Token 管理**：从 ``config.get_tushare_token()`` 获取
6. **调用统计**：按 endpoint 记录成功/失败/重试次数

使用示例::

    from aipha.data.tushare_gateway import get_tushare_gateway

    gw = get_tushare_gateway()
    df = gw.call("daily", ts_code="000001.SZ", start_date="20240101")
    df = gw.call("stock_basic", list_status="L")

    # pro_bar 特殊接口（需要传 api 对象）
    df = gw.pro_bar(ts_code="000001.SZ", adj="qfq", asset="E")

设计原则（对齐 copilot-instructions.md）
------------------------------------------
- 作为可插拔组件，不绑定策略逻辑
- 禁止在代码/日志中打印完整 Token
- 线程安全，支持多线程回测场景
"""

from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import tushare as ts

from aipha.config import get_tushare_token
from aipha.data.rate_limiter import get_tushare_limiter

logger = logging.getLogger(__name__)

# 默认配置
_DEFAULT_MAX_CONCURRENT = 2  # TuShare 同时最多 2 个并发连接
_DEFAULT_MAX_RETRIES = 60    # 默认重试 60 次 (≈1 分钟)
_DEFAULT_RETRY_INTERVAL = 1.0  # 固定 1 秒间隔，不指数退避
_THROTTLE_RETRY_INTERVAL = 5.0  # 限流错误 5 秒间隔（等待其他客户端释放）

# TuShare 限流/并发相关错误关键词
_THROTTLE_KEYWORDS = (
    "IP数量超限",
    "每分钟最多访问",
    "抱歉，您每天最多访问",
    "最多访问",
    "频率",
    "too many",
    "rate limit",
    "超限",
)


class OfflineModeError(RuntimeError):
    """Raised when API call is attempted in strict offline mode."""


class _CrossProcessSemaphore:
    """跨进程信号量 — 基于文件锁，确保多 cron 进程共享同一限流。

    在 Linux (Docker) 上使用 fcntl.flock 实现跨进程互斥，
    在 Windows (开发) 上降级为 threading.Semaphore (单进程足够)。
    """

    def __init__(self, max_concurrent: int = 2) -> None:
        self._max = max_concurrent
        self._fallback = threading.Semaphore(max_concurrent)
        self._use_flock = False
        self._lock_dir: Path | None = None
        self._held_fd: Any = None

        try:
            import fcntl  # noqa: F401
            self._use_flock = True
            self._lock_dir = Path("/tmp/aipha_tushare_locks")
            self._lock_dir.mkdir(parents=True, exist_ok=True)
        except ImportError:
            pass  # Windows — 降级到 threading.Semaphore

    def acquire(self) -> None:
        if not self._use_flock:
            self._fallback.acquire()
            return
        import fcntl
        # 尝试非阻塞获取任一槽位
        for i in range(self._max):
            lock_path = self._lock_dir / f"slot_{i}.lock"  # type: ignore[union-attr]
            try:
                fd = open(lock_path, "w")
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fd.write(str(os.getpid()))
                fd.flush()
                self._held_fd = fd
                return
            except OSError:
                fd.close()
        # 所有槽位忙 — 阻塞等待槽位 0
        lock_path = self._lock_dir / "slot_0.lock"  # type: ignore[union-attr]
        fd = open(lock_path, "w")
        fcntl.flock(fd, fcntl.LOCK_EX)
        fd.write(str(os.getpid()))
        fd.flush()
        self._held_fd = fd

    def release(self) -> None:
        if not self._use_flock:
            self._fallback.release()
            return
        if self._held_fd is not None:
            try:
                self._held_fd.close()
            except Exception:
                pass
            self._held_fd = None


class TuShareGateway:
    """TuShare Pro 统一数据网关（线程安全）

    提供对所有 TuShare API 端点的统一访问入口，确保：
    - 全局只有一个 ``ts.pro_api()`` 连接
    - 并发请求不超过 ``max_concurrent``（默认 2）
    - 每次调用都经过速率限制器
    - 失败时自动重试（固定 1 秒间隔，限流时 5 秒间隔）
    - 按 endpoint 记录统计信息

    Args:
        token: TuShare Pro API Token（留空则从环境变量获取）
        max_concurrent: 最大并发请求数（默认 2）
        max_retries: 最大重试次数（默认 60，约 1 分钟）
        strict_offline: 若为 True，任何 API 调用抛 OfflineModeError
    """

    def __init__(
        self,
        token: str | None = None,
        max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        strict_offline: bool | None = None,
    ):
        import os as _os

        self._token = token or get_tushare_token()
        self._max_concurrent = max_concurrent
        self._max_retries = max_retries

        # strict_offline: 参数 > 环境变量 > 默认 False
        if strict_offline is not None:
            self._strict_offline = strict_offline
        else:
            self._strict_offline = _os.environ.get("TUSHARE_STRICT_OFFLINE", "").strip() in ("1", "true", "True")

        # 初始化 TuShare API（全局唯一实例）
        ts.set_token(self._token)
        self._api = ts.pro_api(self._token, timeout=15)  # 15秒超时，避免长时间阻塞

        # 并发信号量：使用文件锁实现跨进程安全 (CONC-2)
        self._semaphore = _CrossProcessSemaphore(max_concurrent)

        # 速率限制器（复用全局单例）
        self._limiter = get_tushare_limiter()

        # 调用统计（线程安全）
        self._stats_lock = threading.Lock()
        self._stats: dict[str, dict[str, int]] = {}

        # Token 脱敏日志
        masked = self._mask_token(self._token)
        logger.info(
            f"TuShareGateway 初始化: token={masked}, "
            f"max_concurrent={max_concurrent}, "
            f"max_retries={max_retries}"
        )

    @property
    def api(self) -> Any:
        """获取底层 TuShare API 对象（仅用于兼容过渡期）

        .. warning::
            不建议直接使用此属性。调用方应通过 ``call()`` 方法
            以获得限流、重试、统计等保护。仅在需要传递 api 对象
            给 ``ts.pro_bar()`` 等顶层函数时使用。
        """
        return self._api

    def call(
        self,
        endpoint: str,
        *,
        max_retries: int | None = None,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """调用 TuShare API 端点

        所有 TuShare 数据访问的统一入口。自动处理：
        - 并发控制（Semaphore）
        - 速率限制（令牌桶）
        - 失败重试（指数退避）
        - 调用统计

        Args:
            endpoint: API 端点名称（如 ``"daily"``, ``"stock_basic"``,
                ``"cb_daily"`` 等）
            max_retries: 本次调用的重试次数（留空使用默认值）
            **kwargs: 传递给 TuShare API 的参数

        Returns:
            API 返回的 DataFrame（空数据返回空 DataFrame）

        Raises:
            RuntimeError: 所有重试均失败
            AttributeError: endpoint 不存在
        """
        retries = max_retries if max_retries is not None else self._max_retries

        if self._strict_offline:
            raise OfflineModeError(
                f"TuShare API '{endpoint}' 被阻止：strict_offline 模式已启用。"
                f"确保所有所需数据已在本地缓存中。"
            )

        api_func = getattr(self._api, endpoint, None)
        if api_func is None:
            raise AttributeError(
                f"TuShare API 不存在端点 '{endpoint}'。"
                f"请检查端点名称是否正确。"
            )

        last_error: Exception | None = None

        for attempt in range(retries):
            # 1. 获取并发许可
            self._semaphore.acquire()
            try:
                # 2. 获取速率令牌
                self._limiter.acquire()

                # 3. 执行 API 调用
                result = api_func(**kwargs)

                # 4. 记录成功
                self._record_stat(endpoint, "success")

                if result is None:
                    return pd.DataFrame()
                return result

            except Exception as e:
                last_error = e
                self._record_stat(endpoint, "error")
                err_msg = str(e)
                is_throttle = any(kw in err_msg for kw in _THROTTLE_KEYWORDS)
                wait = _THROTTLE_RETRY_INTERVAL if is_throttle else _DEFAULT_RETRY_INTERVAL

                if attempt < retries - 1:
                    # 每 10 次打一条日志，避免刷屏
                    if attempt < 3 or attempt % 10 == 0:
                        tag = "限流" if is_throttle else "失败"
                        logger.warning(
                            f"TuShare API '{endpoint}' {tag} "
                            f"(尝试 {attempt + 1}/{retries}): {err_msg}, "
                            f"{wait:.0f}s 后重试"
                        )
                    time.sleep(wait)
                else:
                    logger.error(
                        f"TuShare API '{endpoint}' 最终失败 "
                        f"(共 {retries} 次尝试, 耗时约 {retries * wait:.0f}s): {err_msg}"
                    )
            finally:
                # 释放并发许可
                self._semaphore.release()

        raise RuntimeError(
            f"TuShare API '{endpoint}' 调用失败"
            f"（重试 {retries} 次）: {last_error}"
        ) from last_error

    def pro_bar(self, **kwargs: Any) -> pd.DataFrame:
        """调用 ``ts.pro_bar()`` 顶层函数

        ``ts.pro_bar()`` 与普通 API 端点不同，它是一个独立的顶层
        函数而非 ``api.xxx()`` 方法，且需要传入 ``api`` 对象。
        本方法封装了限流、并发控制和重试。

        Args:
            **kwargs: 传递给 ``ts.pro_bar()`` 的参数
                （``api`` 参数会被自动设置，无需手动传入）

        Returns:
            API 返回的 DataFrame

        Raises:
            RuntimeError: 所有重试均失败
        """
        last_error: Exception | None = None

        if self._strict_offline:
            raise OfflineModeError(
                "TuShare pro_bar 被阻止：strict_offline 模式已启用。"
                "确保所有所需数据已在本地缓存中。"
            )

        for attempt in range(self._max_retries):
            self._semaphore.acquire()
            try:
                self._limiter.acquire()

                # 抑制 tushare 内部 adj_factor 合并产生的 FutureWarning/KeyError 输出
                import warnings
                with warnings.catch_warnings():
                    warnings.filterwarnings("ignore", category=FutureWarning)
                    warnings.filterwarnings("ignore", message=".*adj_factor.*")
                    result = ts.pro_bar(api=self._api, **kwargs)

                self._record_stat("pro_bar", "success")

                if result is None:
                    return pd.DataFrame()
                return result

            except Exception as e:
                last_error = e
                self._record_stat("pro_bar", "error")
                err_msg = str(e)
                is_throttle = any(kw in err_msg for kw in _THROTTLE_KEYWORDS)
                wait = _THROTTLE_RETRY_INTERVAL if is_throttle else _DEFAULT_RETRY_INTERVAL

                if attempt < self._max_retries - 1:
                    if attempt < 3 or attempt % 10 == 0:
                        tag = "限流" if is_throttle else "失败"
                        logger.warning(
                            f"TuShare pro_bar {tag} "
                            f"(尝试 {attempt + 1}/{self._max_retries}): {err_msg}, "
                            f"{wait:.0f}s 后重试"
                        )
                    time.sleep(wait)
                else:
                    logger.error(
                        f"TuShare pro_bar 最终失败 "
                        f"(共 {self._max_retries} 次尝试): {err_msg}"
                    )
            finally:
                self._semaphore.release()

        raise RuntimeError(
            f"TuShare pro_bar 调用失败"
            f"（重试 {self._max_retries} 次）: {last_error}"
        ) from last_error

    def stats(self) -> dict[str, dict[str, int]]:
        """获取调用统计

        Returns:
            按 endpoint 分组的统计字典::

                {
                    "daily": {"success": 10, "error": 1, "total": 11},
                    "stock_basic": {"success": 1, "error": 0, "total": 1},
                }
        """
        with self._stats_lock:
            return {k: dict(v) for k, v in self._stats.items()}

    def reset_stats(self) -> None:
        """重置调用统计"""
        with self._stats_lock:
            self._stats.clear()

    def _record_stat(self, endpoint: str, result: str) -> None:
        """记录一次 API 调用

        Args:
            endpoint: API 端点名称
            result: 调用结果（``"success"`` 或 ``"error"``）
        """
        with self._stats_lock:
            if endpoint not in self._stats:
                self._stats[endpoint] = {"success": 0, "error": 0, "total": 0}
            self._stats[endpoint][result] += 1
            self._stats[endpoint]["total"] += 1

    @staticmethod
    def _mask_token(token: str) -> str:
        """Token 脱敏：只保留前后各 4 位

        Args:
            token: 原始 Token

        Returns:
            脱敏后的字符串（如 ``"abcd****wxyz"``）
        """
        if len(token) <= 8:
            return "****"
        return f"{token[:4]}****{token[-4:]}"


# ====================================================================
# 全局单例
# ====================================================================
_gateway_instance: TuShareGateway | None = None
_gateway_lock = threading.Lock()


def get_tushare_gateway(
    token: str | None = None,
    max_concurrent: int = _DEFAULT_MAX_CONCURRENT,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> TuShareGateway:
    """获取 TuShare 全局网关（单例）

    首次调用时创建实例，后续调用返回同一实例。
    若需重新初始化（如更换 Token），先调用 ``reset_tushare_gateway()``。

    Args:
        token: TuShare Pro API Token（留空则从环境变量获取）
        max_concurrent: 最大并发请求数（默认 2）
        max_retries: 最大重试次数（默认 60）

    Returns:
        全局 TuShareGateway 实例
    """
    global _gateway_instance

    if _gateway_instance is not None:
        return _gateway_instance

    with _gateway_lock:
        if _gateway_instance is None:
            _gateway_instance = TuShareGateway(
                token=token,
                max_concurrent=max_concurrent,
                max_retries=max_retries,
            )
        return _gateway_instance


def reset_tushare_gateway() -> None:
    """重置全局网关实例（主要用于测试）"""
    global _gateway_instance
    with _gateway_lock:
        _gateway_instance = None
