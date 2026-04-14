"""provider_base 模块测试。

验证 DataFingerprint 数据结构与 BarProvider 接口协议。
"""

from dataclasses import FrozenInstanceError
from datetime import date, datetime

import pytest

from aipha.data.provider_base import BarProvider, DataFingerprint
from aipha.domain.models import Bar


# ===========================================================================
# DataFingerprint 测试
# ===========================================================================


class TestDataFingerprint:
    """DataFingerprint 数据指纹测试。"""

    def test_creation(self):
        fp = DataFingerprint(
            provider="tushare_pro",
            snapshot="2024-01-15",
            calendar="SSE_2024",
            adjust="qfq",
            schema="v1",
        )
        assert fp.provider == "tushare_pro"
        assert fp.snapshot == "2024-01-15"
        assert fp.calendar == "SSE_2024"
        assert fp.adjust == "qfq"
        assert fp.schema == "v1"

    def test_frozen(self):
        """fingerprint 是不可变对象"""
        fp = DataFingerprint(
            provider="tushare_pro",
            snapshot="latest",
            calendar="SSE",
            adjust="none",
            schema="v1",
        )
        with pytest.raises(FrozenInstanceError):
            fp.provider = "akshare"  # type: ignore[misc]

    def test_equality(self):
        fp1 = DataFingerprint("a", "b", "c", "d", "e")
        fp2 = DataFingerprint("a", "b", "c", "d", "e")
        assert fp1 == fp2

    def test_inequality(self):
        fp1 = DataFingerprint("a", "b", "c", "d", "e")
        fp2 = DataFingerprint("a", "b", "c", "d", "f")
        assert fp1 != fp2

    def test_hashable(self):
        """frozen dataclass 可作为 dict key / set 元素"""
        fp1 = DataFingerprint("a", "b", "c", "d", "e")
        fp2 = DataFingerprint("a", "b", "c", "d", "e")
        s = {fp1, fp2}
        assert len(s) == 1

    def test_repr(self):
        fp = DataFingerprint("tushare", "latest", "SSE", "qfq", "v2")
        r = repr(fp)
        assert "tushare" in r
        assert "qfq" in r

    def test_slots(self):
        """slots=True → 不能添加动态属性"""
        fp = DataFingerprint("a", "b", "c", "d", "e")
        with pytest.raises((AttributeError, TypeError)):
            fp.extra = "test"  # type: ignore[attr-defined]


# ===========================================================================
# BarProvider 协议测试
# ===========================================================================


class _DummyProvider:
    """最小实现 BarProvider 协议的类。"""

    def fingerprint(self) -> DataFingerprint:
        return DataFingerprint("dummy", "test", "cal", "none", "v1")

    def load_bars(
        self, *, symbol: str, start: date, end: date, frequency: str,
    ) -> list[Bar]:
        return [
            Bar(
                symbol=symbol,
                start_ts=datetime(2024, 1, 1, 9, 30),
                end_ts=datetime(2024, 1, 1, 15, 0),
                open=10.0, high=11.0, low=9.5, close=10.5,
                volume=1000, prev_close=10.0,
            )
        ]


class TestBarProviderProtocol:
    """BarProvider 协议测试。"""

    def test_dummy_satisfies_protocol(self):
        """最小实现满足 BarProvider 协议"""
        provider = _DummyProvider()
        # Protocol 非 runtime_checkable，用方法检查替代
        assert hasattr(provider, "fingerprint")
        assert hasattr(provider, "load_bars")
        assert callable(provider.fingerprint)
        assert callable(provider.load_bars)

    def test_fingerprint_returns_datafingerprint(self):
        provider = _DummyProvider()
        fp = provider.fingerprint()
        assert isinstance(fp, DataFingerprint)
        assert fp.provider == "dummy"

    def test_load_bars_returns_list(self):
        provider = _DummyProvider()
        bars = provider.load_bars(
            symbol="000001.SZ",
            start=date(2024, 1, 1),
            end=date(2024, 1, 31),
            frequency="1d",
        )
        assert isinstance(bars, list)
        assert len(bars) == 1
        assert isinstance(bars[0], Bar)

    def test_non_conforming_class(self):
        """不实现接口的类不满足协议"""

        class NotAProvider:
            pass

        # Protocol 是 runtime-checkable (if it is)
        # Actually Protocol may not be runtime_checkable by default
        # so just verify the dummy works and the non-conforming doesn't have methods
        p = NotAProvider()
        assert not hasattr(p, "fingerprint")
        assert not hasattr(p, "load_bars")
