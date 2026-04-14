"""MinuteBarProvider 接口与 MinuteDataCache 测试。"""

from __future__ import annotations

from datetime import date, datetime, time
from pathlib import Path

import pytest

from aipha.data.minute_provider import (
    MinuteBarProvider,
    MinuteDataCache,
    MinuteProviderConfig,
)
from aipha.data.stream import Bar, BarFrequency


# ------------------------------------------------------------------
# 辅助
# ------------------------------------------------------------------

def _make_bar(symbol: str, ts: datetime, close: float, vol: float = 100.0) -> Bar:
    return Bar(
        symbol=symbol,
        timestamp=ts,
        open=close,
        high=close + 0.01,
        low=close - 0.01,
        close=close,
        volume=vol,
        amount=close * vol,
        frequency=BarFrequency.MIN_1,
    )


# ------------------------------------------------------------------
# Config 测试
# ------------------------------------------------------------------

class TestMinuteProviderConfig:
    def test_defaults(self):
        cfg = MinuteProviderConfig()
        assert cfg.freq == BarFrequency.MIN_1
        assert cfg.market_open == time(9, 30)
        assert cfg.market_close == time(15, 0)
        assert cfg.cache_dir == "data/cache/minute"

    def test_custom(self):
        cfg = MinuteProviderConfig(freq=BarFrequency.MIN_5, cache_dir="/tmp/m")
        assert cfg.freq == BarFrequency.MIN_5
        assert cfg.cache_dir == "/tmp/m"


# ------------------------------------------------------------------
# Cache 测试
# ------------------------------------------------------------------

class TestMinuteDataCache:
    def test_put_get(self):
        cache = MinuteDataCache()
        td = date(2024, 1, 15)
        bars = [_make_bar("123456.SZ", datetime(2024, 1, 15, 9, 30), 100.0)]
        cache.put("123456.SZ", td, bars)
        result = cache.get("123456.SZ", td)
        assert result is not None
        assert len(result) == 1
        assert result[0].close == 100.0

    def test_get_miss(self):
        cache = MinuteDataCache()
        assert cache.get("NONE", date(2024, 1, 1)) is None

    def test_size(self):
        cache = MinuteDataCache()
        assert cache.size == 0
        td1 = date(2024, 1, 15)
        td2 = date(2024, 1, 16)
        cache.put("A", td1, [_make_bar("A", datetime(2024, 1, 15, 9, 30), 10)])
        cache.put("A", td2, [_make_bar("A", datetime(2024, 1, 16, 9, 30), 11)])
        assert cache.size == 2

    def test_clear(self):
        cache = MinuteDataCache()
        cache.put("A", date(2024, 1, 15), [])
        cache.clear()
        assert cache.size == 0

    def test_freq_separation(self):
        """不同频率分别存储。"""
        cache = MinuteDataCache()
        td = date(2024, 1, 15)
        b1 = [_make_bar("A", datetime(2024, 1, 15, 9, 30), 100)]
        b5 = [_make_bar("A", datetime(2024, 1, 15, 9, 35), 101)]
        cache.put("A", td, b1, BarFrequency.MIN_1)
        cache.put("A", td, b5, BarFrequency.MIN_5)
        assert cache.size == 2
        assert cache.get("A", td, BarFrequency.MIN_1)[0].close == 100
        assert cache.get("A", td, BarFrequency.MIN_5)[0].close == 101

    def test_parquet_roundtrip(self, tmp_path: Path):
        """Parquet 持久化 → 加载回来一致。"""
        cfg = MinuteProviderConfig(cache_dir=str(tmp_path))
        cache = MinuteDataCache(cfg)
        td = date(2024, 3, 1)
        bars = [
            _make_bar("128001.SZ", datetime(2024, 3, 1, 9, 30), 105.5, 200),
            _make_bar("128001.SZ", datetime(2024, 3, 1, 9, 31), 105.8, 150),
        ]
        cache.put("128001.SZ", td, bars)
        path = cache.save_to_parquet("128001.SZ", td, BarFrequency.MIN_1)
        assert path is not None
        assert path.exists()

        # 清空内存后从 Parquet 加载
        cache.clear()
        loaded = cache.load_from_parquet("128001.SZ", td, BarFrequency.MIN_1)
        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0].close == 105.5
        assert loaded[1].volume == 150.0

    def test_load_nonexistent_parquet(self, tmp_path: Path):
        cfg = MinuteProviderConfig(cache_dir=str(tmp_path))
        cache = MinuteDataCache(cfg)
        assert cache.load_from_parquet("NONE", date(2024, 1, 1), BarFrequency.MIN_1) is None
