"""速率限制器单元测试"""

import os
import tempfile
import threading
import time
from pathlib import Path

import pytest

from aipha.data.rate_limiter import (
    RateLimiter,
    RateLimitConfig,
    get_akshare_limiter,
    get_rate_limit_config,
    reset_akshare_limiter,
)


class TestRateLimitConfig:
    """RateLimitConfig 测试"""

    def test_default_values(self):
        """测试默认值"""
        config = RateLimitConfig()
        assert config.requests_per_second == 1.5
        assert config.min_interval == 0.5
        assert config.batch_interval == 1.0
        assert config.max_retries == 3
        assert config.backoff_factor == 2.0

    def test_custom_values(self):
        """测试自定义值"""
        config = RateLimitConfig(
            requests_per_second=2.0,
            min_interval=0.3,
            batch_interval=0.5,
            max_retries=5,
            backoff_factor=1.5,
        )
        assert config.requests_per_second == 2.0
        assert config.min_interval == 0.3
        assert config.batch_interval == 0.5
        assert config.max_retries == 5
        assert config.backoff_factor == 1.5

    def test_from_env_with_defaults(self):
        """测试从环境变量加载（使用默认值）"""
        # 确保没有设置相关环境变量
        for key in list(os.environ.keys()):
            if key.startswith("TEST_RATE_"):
                del os.environ[key]

        config = RateLimitConfig.from_env("TEST_RATE")
        # 应该使用默认值
        assert config.requests_per_second == 1.5
        assert config.max_retries == 3


class TestRateLimiter:
    """RateLimiter 测试"""

    def test_basic_acquire(self):
        """测试基本获取令牌"""
        limiter = RateLimiter(
            requests_per_second=10.0,  # 高速率以快速测试
            min_interval=0.0,
            name="test",
        )

        # 应该能立即获取
        start = time.monotonic()
        result = limiter.acquire()
        elapsed = time.monotonic() - start

        assert result is True
        assert elapsed < 0.1  # 应该很快

    def test_min_interval_enforcement(self):
        """测试最小间隔强制"""
        min_interval = 0.1  # 100ms，确保在 Windows 定时器精度下可靠
        limiter = RateLimiter(
            requests_per_second=100.0,  # 高速率
            min_interval=min_interval,
            name="test_interval",
        )

        # 第一次请求
        limiter.acquire()
        start = time.monotonic()

        # 第二次请求应该等待
        limiter.acquire()
        elapsed = time.monotonic() - start

        # 应该至少等待了 min_interval
        assert elapsed >= min_interval * 0.8  # 允许 20% 误差

    def test_rate_limiting(self):
        """测试速率限制"""
        limiter = RateLimiter(
            requests_per_second=5.0,  # 每秒 5 个请求
            min_interval=0.0,
            name="test_rate",
        )

        # 快速发起多个请求
        start = time.monotonic()
        for _ in range(6):  # 超过初始令牌数
            limiter.acquire()
        elapsed = time.monotonic() - start

        # 6 个请求，每秒 5 个，应该至少需要约 0.2 秒
        # 因为初始有 5 个令牌，第 6 个需要等待
        assert elapsed >= 0.1

    def test_timeout(self):
        """测试超时"""
        limiter = RateLimiter(
            requests_per_second=1.0,  # 初始令牌=1.0，够第一次 acquire
            min_interval=5.0,  # 很长间隔，迫使第二次必须等待
            name="test_timeout",
        )

        # 第一次请求（初始令牌足够，立即返回）
        limiter.acquire()

        # 第二次请求应该超时（min_interval=5s 远大于 timeout=0.2s）
        start = time.monotonic()
        result = limiter.acquire(timeout=0.2)
        elapsed = time.monotonic() - start

        assert result is False
        assert elapsed < 0.5  # 允许 Windows 定时器精度误差

    def test_stats(self):
        """测试统计信息"""
        limiter = RateLimiter(
            requests_per_second=100.0,
            min_interval=0.0,
            name="test_stats",
        )

        # 发起几个请求
        for _ in range(5):
            limiter.acquire()

        stats = limiter.stats()
        assert stats["name"] == "test_stats"
        assert stats["total_requests"] == 5

    def test_reset_stats(self):
        """测试重置统计"""
        limiter = RateLimiter(
            requests_per_second=100.0,
            min_interval=0.0,
            name="test_reset",
        )

        limiter.acquire()
        limiter.reset_stats()

        stats = limiter.stats()
        assert stats["total_requests"] == 0

    def test_from_config(self):
        """测试从配置创建"""
        config = RateLimitConfig(
            requests_per_second=2.0,
            min_interval=0.5,
            batch_interval=1.0,
        )
        limiter = RateLimiter.from_config(config, name="test_config")

        assert limiter.requests_per_second == 2.0
        assert limiter.min_interval == 0.5
        assert limiter.batch_interval == 1.0
        assert limiter.name == "test_config"

    def test_thread_safety(self):
        """测试线程安全"""
        limiter = RateLimiter(
            requests_per_second=1000.0,  # 非常高速率以加速测试
            min_interval=0.0,  # 无最小间隔，避免 Windows 定时器精度问题
            name="test_thread",
        )

        results = []
        errors = []

        def worker():
            try:
                for _ in range(5):  # 减少每个线程的请求数
                    ok = limiter.acquire(timeout=2.0)  # 避免无限阻塞
                    if ok:
                        results.append(1)
            except Exception as e:
                errors.append(e)

        # 并发 4 个线程
        threads = [threading.Thread(target=worker, daemon=True) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # 检查无挂起线程
        alive = [t for t in threads if t.is_alive()]
        assert len(alive) == 0, f"{len(alive)} threads still alive"

        # 应该没有错误
        assert len(errors) == 0
        # 应该有 20 个成功的请求
        assert len(results) == 20


class TestGlobalLimiter:
    """全局限制器测试"""

    def setup_method(self):
        """每个测试前重置全局限制器"""
        reset_akshare_limiter()

    def teardown_method(self):
        """每个测试后重置"""
        reset_akshare_limiter()

    def test_singleton(self):
        """测试单例模式"""
        limiter1 = get_akshare_limiter()
        limiter2 = get_akshare_limiter()
        assert limiter1 is limiter2

    def test_reset(self):
        """测试重置全局限制器"""
        limiter1 = get_akshare_limiter()
        reset_akshare_limiter()
        limiter2 = get_akshare_limiter()
        assert limiter1 is not limiter2


class TestEnvFileLoading:
    """测试 .env 文件加载"""

    def test_get_rate_limit_config(self):
        """测试获取速率限制配置"""
        config = get_rate_limit_config("AKSHARE")
        # 应该返回有效的配置
        assert config.requests_per_second > 0
        assert config.min_interval >= 0


class TestTuShareLimiter:
    """TuShare 全局限制器测试"""

    def setup_method(self):
        from aipha.data.rate_limiter import reset_tushare_limiter
        reset_tushare_limiter()

    def teardown_method(self):
        from aipha.data.rate_limiter import reset_tushare_limiter
        reset_tushare_limiter()

    def test_singleton(self):
        """TuShare 限制器单例模式"""
        from aipha.data.rate_limiter import get_tushare_limiter
        l1 = get_tushare_limiter()
        l2 = get_tushare_limiter()
        assert l1 is l2

    def test_reset(self):
        """TuShare 限制器重置"""
        from aipha.data.rate_limiter import get_tushare_limiter, reset_tushare_limiter
        l1 = get_tushare_limiter()
        reset_tushare_limiter()
        l2 = get_tushare_limiter()
        assert l1 is not l2

    def test_default_config(self):
        """TuShare 限制器默认配置"""
        from aipha.data.rate_limiter import get_tushare_limiter
        limiter = get_tushare_limiter()
        assert limiter.name == "tushare"
        assert limiter.requests_per_second >= 2.0  # 至少 2 rps
        assert limiter.min_interval <= 0.5

    def test_acquire_works(self):
        """TuShare 限制器可正常获取令牌"""
        from aipha.data.rate_limiter import get_tushare_limiter
        limiter = get_tushare_limiter()
        assert limiter.acquire(timeout=5.0) is True

    def test_independent_from_akshare(self):
        """TuShare 和 AKShare 限制器互相独立"""
        from aipha.data.rate_limiter import get_tushare_limiter
        ts_limiter = get_tushare_limiter()
        ak_limiter = get_akshare_limiter()
        assert ts_limiter is not ak_limiter
        assert ts_limiter.name != ak_limiter.name
