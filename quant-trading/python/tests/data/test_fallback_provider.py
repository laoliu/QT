"""FallbackProviderChain 测试

覆盖：
- 正常加载（单 Provider / 多 Provider）
- 主 Provider 失败 → 自动切换到备用
- 全部 Provider 失败 → RuntimeError
- 重试机制
- 恢复检测（auto_recover）
- 健康探针（HealthAwareProvider）
- switch_log 审计
- 边界条件
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import pytest

from aipha.data.fallback_provider import (
    FallbackConfig,
    FallbackProviderChain,
    HealthAwareProvider,
    ProviderHealth,
)
from aipha.data.provider_base import BarProvider, DataFingerprint
from aipha.domain.models import Bar


# ============================================================================
# Fake Providers
# ============================================================================


class FakeProvider:
    """可控的假 Provider（实现 BarProvider Protocol）"""

    def __init__(
        self,
        name: str = "FakeProvider",
        *,
        fail: bool = False,
        bars: list[Bar] | None = None,
    ) -> None:
        self._name = name
        self._fail = fail
        self._bars = bars or []
        self.call_count = 0

    def fingerprint(self) -> DataFingerprint:
        return DataFingerprint(
            provider=self._name,
            snapshot="v1",
            calendar="SSE",
            adjust="hfq",
            schema="ohlcv",
        )

    def load_bars(
        self, *, symbol: str, start: date, end: date, frequency: str,
    ) -> list[Bar]:
        self.call_count += 1
        if self._fail:
            raise ConnectionError(f"{self._name} 不可用")
        return list(self._bars)

    def set_fail(self, fail: bool) -> None:
        self._fail = fail


class FakeHealthProvider(FakeProvider):
    """支持 HealthAwareProvider 的假 Provider"""

    def __init__(self, name: str = "HealthProvider", **kwargs: Any) -> None:
        super().__init__(name, **kwargs)
        self._health = ProviderHealth.HEALTHY

    def is_healthy(self) -> ProviderHealth:
        return self._health

    def set_health(self, health: ProviderHealth) -> None:
        self._health = health


def _bar(symbol: str = "000001.SZ", d: str = "2024-01-15") -> Bar:
    """构造测试用 Bar"""
    dt = datetime.fromisoformat(f"{d}T09:30:00")
    return Bar(
        symbol=symbol,
        start_ts=dt,
        end_ts=dt.replace(hour=15),
        open=10.0,
        high=10.5,
        low=9.8,
        close=10.2,
        volume=1_000_000.0,
    )


LOAD_KWARGS = {
    "symbol": "000001.SZ",
    "start": date(2024, 1, 1),
    "end": date(2024, 1, 31),
    "frequency": "1d",
}


# ============================================================================
# TestFallbackProviderChain
# ============================================================================


class TestFallbackProviderChain:
    """FallbackProviderChain 核心测试"""

    def test_single_provider_success(self) -> None:
        p = FakeProvider("A", bars=[_bar()])
        chain = FallbackProviderChain([p])
        bars = chain.load_bars(**LOAD_KWARGS)
        assert len(bars) == 1
        assert chain.active_index == 0

    def test_primary_success_no_switch(self) -> None:
        p1 = FakeProvider("Primary", bars=[_bar()])
        p2 = FakeProvider("Backup", bars=[_bar()])
        chain = FallbackProviderChain([p1, p2])
        chain.load_bars(**LOAD_KWARGS)
        assert chain.active_index == 0
        assert p1.call_count == 1
        assert p2.call_count == 0

    def test_failover_to_backup(self) -> None:
        p1 = FakeProvider("Primary", fail=True)
        p2 = FakeProvider("Backup", bars=[_bar()])
        chain = FallbackProviderChain(
            [p1, p2],
            config=FallbackConfig(max_retries=0, retry_delay_seconds=0),
        )
        bars = chain.load_bars(**LOAD_KWARGS)
        assert len(bars) == 1
        assert chain.active_index == 1

    def test_failover_records_switch_log(self) -> None:
        p1 = FakeProvider("Primary", fail=True)
        p2 = FakeProvider("Backup", bars=[_bar()])
        chain = FallbackProviderChain(
            [p1, p2],
            config=FallbackConfig(max_retries=0, retry_delay_seconds=0),
        )
        chain.load_bars(**LOAD_KWARGS)
        log = chain.switch_log
        assert len(log) == 1
        assert log[0]["from_index"] == 0
        assert log[0]["to_index"] == 1
        assert log[0]["reason"] == "failover"

    def test_all_providers_fail_raises(self) -> None:
        p1 = FakeProvider("A", fail=True)
        p2 = FakeProvider("B", fail=True)
        chain = FallbackProviderChain(
            [p1, p2],
            config=FallbackConfig(max_retries=0, retry_delay_seconds=0),
        )
        with pytest.raises(RuntimeError, match="所有 Provider"):
            chain.load_bars(**LOAD_KWARGS)

    def test_retry_success(self) -> None:
        """第一次失败，重试成功"""

        class FailOnceThenSucceed(FakeProvider):
            def load_bars(self, **kwargs: Any) -> list[Bar]:
                self.call_count += 1
                if self.call_count == 1:
                    raise ConnectionError("first fail")
                return [_bar()]

        p = FailOnceThenSucceed("Flaky")
        chain = FallbackProviderChain(
            [p],
            config=FallbackConfig(max_retries=2, retry_delay_seconds=0),
        )
        bars = chain.load_bars(**LOAD_KWARGS)
        assert len(bars) == 1
        assert p.call_count == 2

    def test_fingerprint_returns_active(self) -> None:
        p1 = FakeProvider("A", fail=True)
        p2 = FakeProvider("B", bars=[_bar()])
        chain = FallbackProviderChain(
            [p1, p2],
            config=FallbackConfig(max_retries=0, retry_delay_seconds=0),
        )
        chain.load_bars(**LOAD_KWARGS)
        fp = chain.fingerprint()
        assert fp.provider == "B"

    def test_provider_count(self) -> None:
        chain = FallbackProviderChain([FakeProvider(), FakeProvider()])
        assert chain.provider_count == 2

    def test_active_provider(self) -> None:
        p1 = FakeProvider("A")
        chain = FallbackProviderChain([p1])
        assert chain.active_provider is p1

    def test_empty_providers_raises(self) -> None:
        with pytest.raises(ValueError, match="至少需要 1 个"):
            FallbackProviderChain([])


# ============================================================================
# TestHealthAwareProvider
# ============================================================================


class TestHealthAwareProvider:
    """健康探针相关测试"""

    def test_health_aware_protocol(self) -> None:
        p = FakeHealthProvider()
        assert isinstance(p, HealthAwareProvider)

    def test_non_health_aware_returns_healthy(self) -> None:
        p = FakeProvider()
        chain = FallbackProviderChain([p])
        assert chain.provider_health(0) == ProviderHealth.HEALTHY

    def test_health_aware_returns_actual_status(self) -> None:
        p = FakeHealthProvider()
        p.set_health(ProviderHealth.DEGRADED)
        chain = FallbackProviderChain([p])
        assert chain.provider_health(0) == ProviderHealth.DEGRADED

    def test_provider_health_index_error(self) -> None:
        chain = FallbackProviderChain([FakeProvider()])
        with pytest.raises(IndexError):
            chain.provider_health(5)


# ============================================================================
# TestAutoRecovery
# ============================================================================


class TestAutoRecovery:
    """自动恢复到主 Provider"""

    def test_recovery_switches_back(self) -> None:
        p1 = FakeProvider("Primary", fail=True)
        p2 = FakeProvider("Backup", bars=[_bar()])
        chain = FallbackProviderChain(
            [p1, p2],
            config=FallbackConfig(
                max_retries=0,
                retry_delay_seconds=0,
                recovery_check_interval=0,  # 立即检查
                auto_recover=True,
            ),
        )
        # 第一次：主失败 → 切到备
        chain.load_bars(**LOAD_KWARGS)
        assert chain.active_index == 1

        # 恢复主 Provider
        p1.set_fail(False)
        p1._bars = [_bar()]

        # 第二次：触发恢复检测 → 切回主
        chain.load_bars(**LOAD_KWARGS)
        assert chain.active_index == 0

    def test_no_recovery_when_disabled(self) -> None:
        p1 = FakeProvider("Primary", fail=True)
        p2 = FakeProvider("Backup", bars=[_bar()])
        chain = FallbackProviderChain(
            [p1, p2],
            config=FallbackConfig(
                max_retries=0,
                retry_delay_seconds=0,
                recovery_check_interval=0,
                auto_recover=False,
            ),
        )
        chain.load_bars(**LOAD_KWARGS)
        assert chain.active_index == 1

        p1.set_fail(False)
        p1._bars = [_bar()]
        chain.load_bars(**LOAD_KWARGS)
        # 不应切回
        assert chain.active_index == 1

    def test_recovery_skipped_if_health_unavailable(self) -> None:
        p1 = FakeHealthProvider("Primary", fail=True)
        p2 = FakeProvider("Backup", bars=[_bar()])
        chain = FallbackProviderChain(
            [p1, p2],
            config=FallbackConfig(
                max_retries=0,
                retry_delay_seconds=0,
                recovery_check_interval=0,
                auto_recover=True,
            ),
        )
        chain.load_bars(**LOAD_KWARGS)
        assert chain.active_index == 1

        # 主 Provider 健康探针仍报不可用
        p1.set_health(ProviderHealth.UNAVAILABLE)
        p1.set_fail(False)
        chain.load_bars(**LOAD_KWARGS)
        # 不切回（因为探针报 UNAVAILABLE）
        assert chain.active_index == 1


# ============================================================================
# TestFallbackConfig
# ============================================================================


class TestFallbackConfig:
    """FallbackConfig 配置"""

    def test_defaults(self) -> None:
        cfg = FallbackConfig()
        assert cfg.max_retries == 2
        assert cfg.retry_delay_seconds == 1.0
        assert cfg.recovery_check_interval == 300.0
        assert cfg.auto_recover is True

    def test_frozen(self) -> None:
        cfg = FallbackConfig()
        with pytest.raises(AttributeError):
            cfg.max_retries = 5  # type: ignore[misc]

    def test_custom(self) -> None:
        cfg = FallbackConfig(max_retries=5, retry_delay_seconds=0.5)
        assert cfg.max_retries == 5
        assert cfg.retry_delay_seconds == 0.5


# ============================================================================
# TestSwitchLog
# ============================================================================


class TestSwitchLog:
    """切换日志审计"""

    def test_multiple_switches(self) -> None:
        p1 = FakeProvider("A", fail=True)
        p2 = FakeProvider("B", fail=True)
        p3 = FakeProvider("C", bars=[_bar()])
        chain = FallbackProviderChain(
            [p1, p2, p3],
            config=FallbackConfig(max_retries=0, retry_delay_seconds=0),
        )
        chain.load_bars(**LOAD_KWARGS)
        # 应该切换到索引 2
        assert chain.active_index == 2
        log = chain.switch_log
        assert len(log) == 1
        assert log[0]["to_index"] == 2

    def test_switch_log_immutability(self) -> None:
        p1 = FakeProvider("A", fail=True)
        p2 = FakeProvider("B", bars=[_bar()])
        chain = FallbackProviderChain(
            [p1, p2],
            config=FallbackConfig(max_retries=0, retry_delay_seconds=0),
        )
        chain.load_bars(**LOAD_KWARGS)
        log = chain.switch_log
        log.clear()
        assert len(chain.switch_log) == 1  # 内部不受外部修改影响
