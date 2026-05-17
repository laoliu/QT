"""数据 Provider 健康检查与容错链

提供：
- ``ProviderHealth`` — Provider 健康状态枚举
- ``HealthAwareProvider`` — 带健康探针的 Provider Protocol 扩展
- ``FallbackProviderChain`` — 主备链式切换的 BarProvider 实现

设计原则：
- 对上层完全透明（实现 ``BarProvider`` Protocol）
- 主 Provider 不可用时自动切换到备用
- 支持健康恢复后自动切回主 Provider
- 所有切换事件可审计（日志）

使用示例::

    chain = FallbackProviderChain(
        providers=[tushare_provider, akshare_provider],
        health_timeout_seconds=5.0,
    )
    bars = chain.load_bars(symbol="000001.SZ", ...)
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable

from aipha.data.provider_base import BarProvider, DataFingerprint
from aipha.domain.models import Bar

logger = logging.getLogger(__name__)


# ============================================================================
# 健康状态
# ============================================================================


class ProviderHealth(enum.Enum):
    """Provider 健康状态"""

    HEALTHY = "healthy"
    DEGRADED = "degraded"  # 可用但延迟高/数据缺失
    UNAVAILABLE = "unavailable"


# ============================================================================
# 健康感知 Provider Protocol
# ============================================================================


@runtime_checkable
class HealthAwareProvider(Protocol):
    """带健康检查能力的 Provider 接口

    在 ``BarProvider`` 基础上增加 ``is_healthy()`` 方法。
    """

    def fingerprint(self) -> DataFingerprint:
        """返回数据源指纹信息。"""
        ...
    def load_bars(
        self, *, symbol: str, start: date, end: date, frequency: str,
    ) -> list[Bar]:
        """加载指定标的与时间范围的K线数据。"""
        ...
    def is_healthy(self) -> ProviderHealth:
        """检查数据源健康状态。"""
        ...


# ============================================================================
# 容错配置
# ============================================================================


@dataclass(frozen=True, slots=True)
class FallbackConfig:
    """容错配置

    Attributes:
        max_retries: 单个 Provider 最大重试次数
        retry_delay_seconds: 重试间隔（秒）
        recovery_check_interval: 恢复检测间隔（秒），主 Provider 恢复后切回
        auto_recover: 是否在主 Provider 恢复后自动切回
    """

    max_retries: int = 2
    retry_delay_seconds: float = 1.0
    recovery_check_interval: float = 300.0  # 5 分钟
    auto_recover: bool = True


# ============================================================================
# 容错链
# ============================================================================


class FallbackProviderChain:
    """主备链式 BarProvider

    按注册顺序尝试各 Provider，第一个成功返回数据的 Provider 为当前活跃源。
    支持自动重试与健康恢复检测。

    实现 ``BarProvider`` Protocol（``fingerprint`` + ``load_bars``）。
    """

    def __init__(
        self,
        providers: list[BarProvider],
        config: FallbackConfig | None = None,
    ) -> None:
        if not providers:
            raise ValueError("至少需要 1 个 Provider")
        self._providers = list(providers)
        self._config = config or FallbackConfig()
        self._active_index: int = 0
        self._last_recovery_check: float = 0.0
        self._switch_log: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # BarProvider Protocol
    # ------------------------------------------------------------------

    def fingerprint(self) -> DataFingerprint:
        """返回当前活跃 Provider 的 fingerprint"""
        return self._providers[self._active_index].fingerprint()

    def load_bars(
        self, *, symbol: str, start: date, end: date, frequency: str,
    ) -> list[Bar]:
        """加载数据，失败时自动切换到下一个 Provider"""
        # 自动恢复检测
        if (
            self._config.auto_recover
            and self._active_index > 0
            and self._should_check_recovery()
        ):
            self._try_recover(symbol=symbol, start=start, end=end, frequency=frequency)

        # 从当前 active 开始依次尝试
        errors: list[tuple[int, Exception]] = []
        order = self._get_try_order()

        for idx in order:
            provider = self._providers[idx]
            for attempt in range(self._config.max_retries + 1):
                try:
                    bars = provider.load_bars(
                        symbol=symbol, start=start, end=end, frequency=frequency,
                    )
                    if idx != self._active_index:
                        self._switch_to(idx, reason="failover")
                    return bars
                except Exception as e:
                    if attempt < self._config.max_retries:
                        time.sleep(self._config.retry_delay_seconds)
                    else:
                        errors.append((idx, e))
                        logger.warning(
                            "Provider #%d (%s) 加载失败: %s",
                            idx, self._provider_name(idx), e,
                        )

        # 全部失败
        msg_parts = [
            f"Provider #{i} ({self._provider_name(i)}): {e}"
            for i, e in errors
        ]
        raise RuntimeError(
            f"所有 Provider ({len(self._providers)}) 均不可用。"
            f"详情:\n" + "\n".join(msg_parts)
        )

    # ------------------------------------------------------------------
    # 状态查询
    # ------------------------------------------------------------------

    @property
    def active_index(self) -> int:
        """当前活跃 Provider 索引"""
        return self._active_index

    @property
    def active_provider(self) -> BarProvider:
        """当前活跃 Provider"""
        return self._providers[self._active_index]

    @property
    def provider_count(self) -> int:
        """Provider 总数"""
        return len(self._providers)

    @property
    def switch_log(self) -> list[dict[str, Any]]:
        """切换历史记录"""
        return list(self._switch_log)

    def provider_health(self, index: int) -> ProviderHealth:
        """查询指定 Provider 的健康状态

        如果 Provider 实现了 ``HealthAwareProvider``，调用其
        ``is_healthy()``；否则返回 ``HEALTHY``（乐观假设）。
        """
        if index < 0 or index >= len(self._providers):
            raise IndexError(f"Provider 索引超出范围: {index}")
        provider = self._providers[index]
        if isinstance(provider, HealthAwareProvider):
            return provider.is_healthy()
        return ProviderHealth.HEALTHY

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _get_try_order(self) -> list[int]:
        """获取尝试顺序：active 优先，然后按注册顺序"""
        order = [self._active_index]
        for i in range(len(self._providers)):
            if i != self._active_index:
                order.append(i)
        return order

    def _switch_to(self, index: int, *, reason: str) -> None:
        old = self._active_index
        self._active_index = index
        record = {
            "timestamp": time.time(),
            "from_index": old,
            "to_index": index,
            "from_provider": self._provider_name(old),
            "to_provider": self._provider_name(index),
            "reason": reason,
        }
        self._switch_log.append(record)
        logger.info(
            "Provider 切换: #%d (%s) → #%d (%s) [%s]",
            old, self._provider_name(old),
            index, self._provider_name(index),
            reason,
        )

    def _should_check_recovery(self) -> bool:
        now = time.time()
        if now - self._last_recovery_check >= self._config.recovery_check_interval:
            self._last_recovery_check = now
            return True
        return False

    def _try_recover(
        self, *, symbol: str, start: date, end: date, frequency: str,
    ) -> None:
        """尝试恢复到主 Provider（索引 0）"""
        primary = self._providers[0]
        # 先检查健康探针
        if isinstance(primary, HealthAwareProvider):
            health = primary.is_healthy()
            if health == ProviderHealth.UNAVAILABLE:
                return

        try:
            primary.load_bars(
                symbol=symbol, start=start, end=end, frequency=frequency,
            )
            self._switch_to(0, reason="recovery")
        except Exception:
            pass  # 主 Provider 仍不可用

    def _provider_name(self, index: int) -> str:
        p = self._providers[index]
        return type(p).__name__
