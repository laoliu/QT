"""缓存工具 — Parquet / JSON 持久化缓存。"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TTL_NEVER = 0
TTL_1_DAY = 86_400
TTL_7_DAYS = 604_800
TTL_30_DAYS = 2_592_000


@dataclass
class CacheTTLPolicy:
    daily: int = TTL_1_DAY
    weekly: int = TTL_7_DAYS
    monthly: int = TTL_30_DAYS
    financial: int = TTL_30_DAYS
    default: int = TTL_1_DAY


DEFAULT_TTL_POLICY = CacheTTLPolicy()


def is_cache_fresh(path: Path, ttl_seconds: int = 0) -> bool:
    """检测缓存文件是否仍在有效期内。"""
    if ttl_seconds <= 0:
        return True
    if not path.exists():
        return False
    mtime = path.stat().st_mtime
    age = time.time() - mtime
    if age > ttl_seconds:
        logger.debug("缓存过期: %s (age=%.0fs, ttl=%ds)", path.name, age, ttl_seconds)
        return False
    return True


def cache_key(provider: str, symbol: str, start: date, end: date, frequency: str, adjust: str) -> str:
    """生成缓存 key（确定性 hash）。"""
    raw = f"{provider}|{symbol}|{start.isoformat()}|{end.isoformat()}|{frequency}|{adjust}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def cache_path(cache_dir: str | Path, key: str, fmt: str = "parquet") -> Path:
    """返回缓存文件路径。"""
    d = Path(cache_dir)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{key}.{fmt}"


def save_json_cache(path: Path, data: Any) -> None:
    """保存 JSON 缓存。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, default=str)
    logger.debug("缓存已保存: %s", path)


def load_json_cache(path: Path) -> Any | None:
    """加载 JSON 缓存，不存在返回 None。"""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("读取缓存失败 %s: %s", path, e)
        return None


def save_parquet_cache(path: Path, df: Any) -> None:
    """保存 Parquet 缓存（需要 pandas）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)
    logger.debug("Parquet 缓存已保存: %s", path)


def load_parquet_cache(path: Path) -> Any | None:
    """加载 Parquet 缓存。"""
    if not path.exists():
        return None
    try:
        import pandas as pd
        return pd.read_parquet(path)
    except Exception as e:
        logger.warning("读取 Parquet 缓存失败 %s: %s", path, e)
        return None


def safe_read_parquet(path: Path):
    """安全读取 Parquet 文件，损坏文件自动删除并返回 None。"""
    try:
        import pandas as pd
        return pd.read_parquet(path)
    except Exception as exc:
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
):
    """统一缓存读取入口：存在性 → TTL → 安全反序列化。"""
    if not use_cache:
        return None
    if not path.exists():
        return None
    if not is_cache_fresh(path, ttl_seconds):
        return None
    return safe_read_parquet(path)
