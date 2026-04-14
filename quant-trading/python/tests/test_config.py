"""配置模块测试"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from aipha.config import (
    get_cache_dir,
    get_config,
    get_initial_capital,
    get_log_dir,
    get_log_level,
    get_risk_free_rate,
    get_tushare_token,
)


class TestGetConfig:
    """get_config 测试"""

    def test_returns_env_var(self) -> None:
        """返回环境变量值"""
        with patch.dict(os.environ, {"TEST_KEY": "test_value"}):
            assert get_config("TEST_KEY") == "test_value"

    def test_returns_default_if_not_set(self) -> None:
        """未设置时返回默认值"""
        # 确保环境变量不存在
        os.environ.pop("NONEXISTENT_KEY", None)
        assert get_config("NONEXISTENT_KEY", "default") == "default"

    def test_returns_none_if_no_default(self) -> None:
        """未设置且无默认值时返回 None"""
        os.environ.pop("NONEXISTENT_KEY", None)
        assert get_config("NONEXISTENT_KEY") is None


class TestGetTushareToken:
    """get_tushare_token 测试"""

    def test_returns_token(self) -> None:
        """返回 Token"""
        with patch.dict(os.environ, {"TUSHARE_TOKEN": "test_token_123"}):
            assert get_tushare_token() == "test_token_123"

    def test_raises_if_not_configured(self) -> None:
        """未配置时抛出异常"""
        with patch.dict(os.environ, {}, clear=True):
            # 清除可能存在的 TUSHARE_TOKEN
            os.environ.pop("TUSHARE_TOKEN", None)
            with pytest.raises(ValueError, match="TUSHARE_TOKEN 未配置"):
                get_tushare_token()


class TestGetInitialCapital:
    """get_initial_capital 测试"""

    def test_returns_configured_value(self) -> None:
        """返回配置值"""
        with patch.dict(os.environ, {"DEFAULT_INITIAL_CAPITAL": "500000"}):
            assert get_initial_capital() == 500000.0

    def test_returns_default(self) -> None:
        """返回默认值"""
        os.environ.pop("DEFAULT_INITIAL_CAPITAL", None)
        assert get_initial_capital() == 1000000.0


class TestGetRiskFreeRate:
    """get_risk_free_rate 测试"""

    def test_returns_configured_value(self) -> None:
        """返回配置值"""
        with patch.dict(os.environ, {"DEFAULT_RISK_FREE_RATE": "0.03"}):
            assert get_risk_free_rate() == 0.03

    def test_returns_default(self) -> None:
        """返回默认值"""
        os.environ.pop("DEFAULT_RISK_FREE_RATE", None)
        assert get_risk_free_rate() == 0.02


class TestGetCacheDir:
    """get_cache_dir 测试"""

    def test_returns_configured_path(self) -> None:
        """返回配置路径"""
        with patch.dict(os.environ, {"DATA_CACHE_DIR": "/custom/cache"}):
            assert get_cache_dir() == Path("/custom/cache")

    def test_returns_default(self) -> None:
        """返回默认路径"""
        os.environ.pop("DATA_CACHE_DIR", None)
        assert get_cache_dir() == Path("data/cache")


class TestGetLogLevel:
    """get_log_level 测试"""

    def test_returns_configured_level(self) -> None:
        """返回配置级别"""
        with patch.dict(os.environ, {"LOG_LEVEL": "DEBUG"}):
            assert get_log_level() == "DEBUG"

    def test_returns_default(self) -> None:
        """返回默认级别"""
        os.environ.pop("LOG_LEVEL", None)
        assert get_log_level() == "INFO"


class TestGetLogDir:
    """get_log_dir 测试"""

    def test_returns_configured_path(self) -> None:
        """返回配置路径"""
        with patch.dict(os.environ, {"LOG_DIR": "/custom/logs"}):
            assert get_log_dir() == Path("/custom/logs")

    def test_returns_default(self) -> None:
        """返回默认路径"""
        os.environ.pop("LOG_DIR", None)
        assert get_log_dir() == Path("logs")
