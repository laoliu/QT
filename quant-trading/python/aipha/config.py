"""环境配置加载模块

从 .env 文件和环境变量加载配置。

使用示例：
    from aipha.config import get_tushare_token, get_config

    # 获取 TuShare Token
    token = get_tushare_token()

    # 获取任意配置
    cache_dir = get_config("DATA_CACHE_DIR", default="data/cache")
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TypeVar

# 尝试加载 python-dotenv（如果安装了）
try:
    from dotenv import load_dotenv

    # 查找 .env 文件（从当前目录向上查找）
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        # 尝试从工作目录加载
        load_dotenv()
except ImportError:
    # python-dotenv 未安装，仅使用环境变量
    pass


T = TypeVar("T")


def get_config(key: str, default: T | None = None) -> str | T:
    """获取配置值

    Args:
        key: 配置键名
        default: 默认值

    Returns:
        配置值或默认值
    """
    return os.environ.get(key, default)


def get_tushare_token() -> str:
    """获取 TuShare Pro API Token

    Returns:
        API Token

    Raises:
        ValueError: 未配置 TUSHARE_TOKEN
    """
    token = get_config("TUSHARE_TOKEN")
    if not token:
        raise ValueError(
            "TUSHARE_TOKEN 未配置。"
            "请在 .env 文件或环境变量中设置 TUSHARE_TOKEN。"
            "获取方式：https://tushare.pro/ -> 个人中心 -> 接口TOKEN"
        )
    return token


def get_initial_capital() -> float:
    """获取默认初始资金"""
    return float(get_config("DEFAULT_INITIAL_CAPITAL", "1000000"))


def get_risk_free_rate() -> float:
    """获取默认无风险利率"""
    return float(get_config("DEFAULT_RISK_FREE_RATE", "0.02"))


def get_cache_dir() -> Path:
    """获取数据缓存目录

    优先级:
      1. DATA_CACHE_DIR 环境变量（显式指定）
      2. QTDATA_DIR/cache（与 local_parquet_provider 共享）
      3. 回退到 data/cache（旧版兼容）
    """
    explicit = os.environ.get("DATA_CACHE_DIR")
    if explicit:
        return Path(explicit)
    qtdata = os.environ.get("QTDATA_DIR")
    if qtdata:
        return Path(qtdata) / "cache"
    # 默认指向 G:/qtdata/cache 与 local_parquet_provider 统一
    default_qtdata = Path("G:/qtdata/cache")
    if default_qtdata.exists():
        return default_qtdata
    return Path("data/cache")


def get_log_level() -> str:
    """获取日志级别"""
    return get_config("LOG_LEVEL", "INFO")


def get_log_dir() -> Path:
    """获取日志目录"""
    return Path(get_config("LOG_DIR", "logs"))
