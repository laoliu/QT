"""配置层 — YAML 配置驱动体系。"""
import os as _os


def get_config(key: str, default: str | None = None) -> str | None:
    """获取配置值（从环境变量）。"""
    return _os.environ.get(key, default)


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
