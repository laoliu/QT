"""配置层 — YAML 配置驱动体系 + 环境变量读取。

环境变量读取器（``get_config`` / ``get_tushare_token`` / ``get_cache_dir``
等）原本有两份：本包和同级的 ``aipha/config.py``。Python 的包优先于同名
模块，后者从来没被导入过 —— 里面的 ``get_initial_capital`` 等函数对外
表现为"不存在"。现已合并到这里，``aipha/config.py`` 删除。

注意：``.env`` 文件**不会**被自动加载（``python-dotenv`` 未安装，
且旧模块里的加载逻辑随包被遮蔽一起失效）。所有配置项都要是真的
环境变量，否则取到的是下面的默认值。
"""
import os as _os
from pathlib import Path as _Path
from aipha.utils.paths import project_path, resolve_path


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
    # 两个环境变量给绝对路径是常态（G:/qtdata、容器里的 /data）；
    # 万一给了相对路径，也锚到项目根而不是当前工作目录。
    explicit = _os.environ.get("DATA_CACHE_DIR")
    if explicit:
        return resolve_path(explicit)
    qtdata = _os.environ.get("QTDATA_DIR")
    if qtdata:
        return resolve_path(qtdata) / "cache"
    default_qtdata = _Path("G:/qtdata/cache")
    if default_qtdata.exists():
        return default_qtdata
    return project_path("data", "cache")


def get_tushare_token() -> str:
    """获取 TuShare Pro API Token。"""
    token = get_config("TUSHARE_TOKEN")
    if not token:
        raise ValueError(
            "TUSHARE_TOKEN 未配置。"
            "请在环境变量中设置 TUSHARE_TOKEN。"
            "获取方式：https://tushare.pro/ -> 个人中心 -> 接口TOKEN"
        )
    return token


def get_initial_capital() -> float:
    """获取默认初始资金。"""
    return float(get_config("DEFAULT_INITIAL_CAPITAL", "1000000"))


def get_risk_free_rate() -> float:
    """获取默认无风险利率。"""
    return float(get_config("DEFAULT_RISK_FREE_RATE", "0.02"))


def get_log_level() -> str:
    """获取日志级别。"""
    return get_config("LOG_LEVEL", "INFO")


def get_log_dir() -> _Path:
    """获取日志目录。

    ``LOG_DIR`` 给绝对路径就照用；给相对路径（或没给）一律锚到项目根，
    否则日志会跟着启动目录散到不同的树里 —— 排障时「日志哪去了」。
    """
    return resolve_path(get_config("LOG_DIR"), "logs")


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
    "get_initial_capital",
    "get_risk_free_rate",
    "get_log_level",
    "get_log_dir",
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
