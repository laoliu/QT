"""
数据缓存工具模块

提供统一的缓存新鲜度检测（TTL）和安全读写工具，供所有 Provider 复用。

设计原则
--------
- **历史行情**（已过去的月份）：永不过期（不可变数据）。
- **元数据** (stock_basic, cb_basic)：7 天 TTL。
- **当月/最新快照** (daily_basic 当月, index_members_latest)：1 天 TTL。
- **财务报表** (fina_indicator, income, balancesheet, cashflow)：30 天 TTL。
- Provider 可通过 ``ttl_seconds`` 参数覆盖默认值。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# ============================================================================
# 默认 TTL 常量（单位：秒）
# ============================================================================

TTL_NEVER = 0          # 0 表示永不过期
TTL_1_DAY = 86_400
TTL_7_DAYS = 604_800
TTL_30_DAYS = 2_592_000


@dataclass(frozen=True)
class CacheTTLPolicy:
    """缓存 TTL 策略配置。

    Attributes:
        historical_bar: 历史月度行情缓存 TTL（默认永不过期）。
        current_month_bar: 当月行情缓存 TTL（默认 1 天）。
        metadata: 元数据 (stock_basic 等) TTL（默认 7 天）。
        daily_snapshot: 当月 daily_basic 等快照 TTL（默认 1 天）。
        financial: 财务报表 TTL（默认 30 天）。
        index_members: 指数成分最新 TTL（默认 1 天）。
    """

    historical_bar: int = TTL_NEVER
    current_month_bar: int = TTL_1_DAY
    metadata: int = TTL_7_DAYS
    daily_snapshot: int = TTL_1_DAY
    financial: int = TTL_30_DAYS
    index_members: int = TTL_1_DAY

    @classmethod
    def from_dict(cls, d: dict) -> CacheTTLPolicy:
        """从配置字典构建。"""
        return cls(**{k: int(v) for k, v in d.items() if k in cls.__dataclass_fields__})


# 全局默认策略实例
DEFAULT_TTL_POLICY = CacheTTLPolicy()


# ============================================================================
# 核心工具函数
# ============================================================================


def is_cache_fresh(path: Path, ttl_seconds: int = 0) -> bool:
    """检测缓存文件是否仍在有效期内。

    Args:
        path: 缓存文件路径（必须已存在）。
        ttl_seconds: 有效期（秒）。``0`` 表示永不过期。

    Returns:
        ``True`` 表示缓存仍可使用，``False`` 表示已过期需刷新。
    """
    if ttl_seconds <= 0:
        return True  # 永不过期

    if not path.exists():
        return False

    mtime = path.stat().st_mtime
    age = time.time() - mtime
    if age > ttl_seconds:
        logger.debug(
            "缓存过期: %s (age=%.0fs, ttl=%ds)",
            path.name, age, ttl_seconds,
        )
        return False
    return True


def safe_read_parquet(path: Path) -> pd.DataFrame | None:
    """安全读取 Parquet 文件，损坏文件自动删除并返回 None。

    在写入过程中断电/崩溃的情况下 Parquet 文件可能损坏，
    此方法捕获所有反序列化异常，删除损坏文件并记录警告，
    让调用方回退到 API 重新下载。
    """
    try:
        return pd.read_parquet(path)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Parquet 缓存损坏，已删除待重新下载: %s (%s: %s)",
            path, type(exc).__name__, exc,
        )
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def read_cache(
    path: Path,
    ttl_seconds: int = 0,
    *,
    use_cache: bool = True,
) -> pd.DataFrame | None:
    """统一的缓存读取入口：存在性 → TTL → 安全反序列化。

    Args:
        path: 缓存文件路径。
        ttl_seconds: 有效期秒数（0 = 永不过期）。
        use_cache: False 时直接返回 None（跳过缓存）。

    Returns:
        缓存的 DataFrame 或 None（需要重新拉取）。
    """
    if not use_cache:
        return None

    if not path.exists():
        return None

    if not is_cache_fresh(path, ttl_seconds):
        return None

    return safe_read_parquet(path)
