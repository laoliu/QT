"""cache_utils 模块测试

覆盖: is_cache_fresh, safe_read_parquet, read_cache, CacheTTLPolicy
"""

from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from aipha.data.cache_utils import (
    DEFAULT_TTL_POLICY,
    TTL_1_DAY,
    TTL_7_DAYS,
    TTL_30_DAYS,
    TTL_NEVER,
    CacheTTLPolicy,
    is_cache_fresh,
    read_cache,
    safe_read_parquet,
)


# ============================================================================
# is_cache_fresh
# ============================================================================


class TestIsCacheFresh:
    """is_cache_fresh 工具函数测试。"""

    def test_ttl_zero_always_fresh(self, tmp_path: Path):
        """TTL=0（永不过期）始终返回 True"""
        p = tmp_path / "data.parquet"
        p.write_bytes(b"dummy")
        assert is_cache_fresh(p, ttl_seconds=0) is True

    def test_fresh_file(self, tmp_path: Path):
        """刚创建的文件应在 TTL 内"""
        p = tmp_path / "data.parquet"
        p.write_bytes(b"dummy")
        assert is_cache_fresh(p, ttl_seconds=3600) is True

    def test_expired_file(self, tmp_path: Path):
        """超过 TTL 的文件应返回 False"""
        p = tmp_path / "data.parquet"
        p.write_bytes(b"dummy")
        # 伪造 mtime 为 2 小时前
        old_time = time.time() - 7200
        import os
        os.utime(p, (old_time, old_time))
        assert is_cache_fresh(p, ttl_seconds=3600) is False

    def test_nonexistent_file(self, tmp_path: Path):
        """不存在的文件返回 False"""
        p = tmp_path / "missing.parquet"
        assert is_cache_fresh(p, ttl_seconds=3600) is False

    def test_negative_ttl_always_fresh(self, tmp_path: Path):
        """负 TTL 等同永不过期"""
        p = tmp_path / "data.parquet"
        p.write_bytes(b"dummy")
        assert is_cache_fresh(p, ttl_seconds=-1) is True


# ============================================================================
# safe_read_parquet
# ============================================================================


class TestSafeReadParquet:
    """safe_read_parquet 安全读取测试。"""

    def test_valid_parquet(self, tmp_path: Path):
        """正常 Parquet 文件可读取"""
        p = tmp_path / "good.parquet"
        df = pd.DataFrame({"a": [1, 2, 3]})
        df.to_parquet(p, index=False)

        result = safe_read_parquet(p)
        assert result is not None
        assert len(result) == 3

    def test_corrupt_file_returns_none(self, tmp_path: Path):
        """损坏文件返回 None 并删除"""
        p = tmp_path / "bad.parquet"
        p.write_bytes(b"not a parquet file")

        result = safe_read_parquet(p)
        assert result is None
        assert not p.exists()  # 文件应被删除

    def test_nonexistent_returns_none(self, tmp_path: Path):
        """不存在的文件读取时返回 None（异常捕获）"""
        p = tmp_path / "missing.parquet"
        result = safe_read_parquet(p)
        assert result is None


# ============================================================================
# read_cache
# ============================================================================


class TestReadCache:
    """read_cache 统一缓存入口测试。"""

    def test_cache_hit(self, tmp_path: Path):
        """缓存命中：存在 + TTL 有效 → 返回 DataFrame"""
        p = tmp_path / "data.parquet"
        df = pd.DataFrame({"x": [10]})
        df.to_parquet(p, index=False)

        result = read_cache(p, ttl_seconds=TTL_1_DAY)
        assert result is not None
        assert result["x"].iloc[0] == 10

    def test_cache_miss_use_cache_false(self, tmp_path: Path):
        """use_cache=False 跳过缓存"""
        p = tmp_path / "data.parquet"
        pd.DataFrame({"x": [1]}).to_parquet(p, index=False)

        result = read_cache(p, use_cache=False)
        assert result is None

    def test_cache_miss_not_exist(self, tmp_path: Path):
        """文件不存在 → None"""
        p = tmp_path / "nope.parquet"
        result = read_cache(p, ttl_seconds=TTL_1_DAY)
        assert result is None

    def test_cache_miss_expired(self, tmp_path: Path):
        """TTL 过期 → None"""
        import os
        p = tmp_path / "old.parquet"
        pd.DataFrame({"x": [1]}).to_parquet(p, index=False)
        old_time = time.time() - 100_000
        os.utime(p, (old_time, old_time))

        result = read_cache(p, ttl_seconds=3600)
        assert result is None

    def test_cache_ttl_zero_always_hit(self, tmp_path: Path):
        """TTL=0 永不过期"""
        import os
        p = tmp_path / "ancient.parquet"
        pd.DataFrame({"x": [1]}).to_parquet(p, index=False)
        old_time = time.time() - 10_000_000
        os.utime(p, (old_time, old_time))

        result = read_cache(p, ttl_seconds=TTL_NEVER)
        assert result is not None


# ============================================================================
# CacheTTLPolicy
# ============================================================================


class TestCacheTTLPolicy:
    """CacheTTLPolicy 配置测试。"""

    def test_defaults(self):
        """默认值符合预期"""
        p = CacheTTLPolicy()
        assert p.historical_bar == 0
        assert p.metadata == TTL_7_DAYS
        assert p.financial == TTL_30_DAYS
        assert p.daily_snapshot == TTL_1_DAY

    def test_from_dict(self):
        """从字典构造"""
        d = {"metadata": 1000, "financial": 2000}
        p = CacheTTLPolicy.from_dict(d)
        assert p.metadata == 1000
        assert p.financial == 2000
        # 未指定的字段保持默认
        assert p.historical_bar == 0

    def test_from_dict_ignores_unknown_keys(self):
        """未知 key 被忽略"""
        d = {"metadata": 1000, "unknown_key": 999}
        p = CacheTTLPolicy.from_dict(d)
        assert p.metadata == 1000

    def test_frozen(self):
        """frozen dataclass 不可修改"""
        p = CacheTTLPolicy()
        with pytest.raises(AttributeError):
            p.metadata = 123  # type: ignore[misc]

    def test_default_instance(self):
        """DEFAULT_TTL_POLICY 是全局默认"""
        assert DEFAULT_TTL_POLICY.metadata == TTL_7_DAYS
