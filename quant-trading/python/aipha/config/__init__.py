"""配置层 — YAML 配置驱动体系。"""
import os as _os
from pathlib import Path as _Path


def get_config(key: str, default: str | None = None) -> str | None:
    """获取配置值（从环境变量）。"""
    return _os.environ.get(key, default)


def get_cache_dir() -> _Path:
    """获取数据缓存目录（统一入口）

    优先级:
      1. DATA_CACHE_DIR 环境变量（显式指定）
      2. QTDATA_DIR/cache（与 local_parquet_provider 共享）
      3. G:/qtdata/cache（默认，如果存在）
      4. 回退到 data/cache（旧版兼容）
    """
    explicit = _os.environ.get("DATA_CACHE_DIR")
    if explicit:
        return _Path(explicit)
    qtdata = _os.environ.get("QTDATA_DIR")
    if qtdata:
        return _Path(qtdata) / "cache"
    default_qtdata = _Path("G:/qtdata/cache")
    if default_qtdata.exists():
        return default_qtdata
    return _Path("data/cache")


def get_tushare_token() -> str:
    """获取 TuShare Pro API Token。"""
    token = get_config("TUSHARE_TOKEN")
    if not token:
        raise ValueError(
            "TUSHARE_TOKEN 未配置。"
            "请在 .env 文件或环境变量中设置 TUSHARE_TOKEN。"
        )
    return token


from aipha.config.config_models import (
    BacktestRunConfig,
    CostConfig,
    DataConfig,
    FillModelConfig,
    PortfolioConfig,
    RiskConfig,
    StrategyConfig,
    TickBacktestConfig,
    UniverseConfig,
)

__all__ = [
    "get_config",
    "get_cache_dir",
    "get_tushare_token",
    "DataConfig",
    "UniverseConfig",
    "StrategyConfig",
    "CostConfig",
    "FillModelConfig",
    "RiskConfig",
    "PortfolioConfig",
    "TickBacktestConfig",
    "BacktestRunConfig",
]
