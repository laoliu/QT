"""回测引擎构建器 — 从配置文件快速组装回测组件"""
from __future__ import annotations

import logging
from typing import Any

from aipha.backtest.config_runner import BacktestRunConfig

logger = logging.getLogger(__name__)


def build_strategy(cfg: BacktestRunConfig):
    """根据配置构建策略实例"""
    from aipha.strategy.registry import StrategyRegistry
    import aipha.strategy.register_all  # noqa: F401  — 触发注册

    name = cfg.strategy_name
    params = cfg.strategy_params or {}
    klass = StrategyRegistry.get(name)
    if klass is None:
        raise ValueError(f"未找到策略: {name}, 已注册: {StrategyRegistry.list()}")
    return klass(**params)


def build_slippage(cfg: BacktestRunConfig):
    """根据配置构建滑点模型"""
    from aipha.execution.slippage import SlippageModel
    params = cfg.slippage or {}
    model_type = params.get("model", "fixed")
    rate = params.get("rate", 0.001)
    return SlippageModel(model=model_type, rate=rate)


def build_fee_model(cfg: BacktestRunConfig):
    """根据配置构建手续费模型"""
    from aipha.execution.fees import FeeModel
    fee_cfg = cfg.fees or {}
    return FeeModel(
        commission_rate=fee_cfg.get("commission", 0.0003),
        stamp_tax=fee_cfg.get("stamp_tax", 0.001),
        min_commission=fee_cfg.get("min_commission", 5.0),
    )


def build_risk_manager(cfg: BacktestRunConfig):
    """根据配置构建风控管理器 (可选)"""
    risk_cfg = cfg.risk or {}
    if not risk_cfg:
        return None
    try:
        from aipha.risk.constraints import PositionConstraints
        return PositionConstraints(
            max_position_pct=risk_cfg.get("max_position_pct", 0.1),
            max_sector_pct=risk_cfg.get("max_sector_pct", 0.3),
            max_total_positions=risk_cfg.get("max_total_positions", 50),
        )
    except ImportError:
        logger.warning("风控模块未安装, 跳过")
        return None


def build_data_loader(cfg: BacktestRunConfig):
    """根据配置构建数据加载器"""
    from aipha.backtest.data_loader import DataLoaderConfig, DataLoader

    start, end = cfg.date_range
    symbols = cfg.symbols or []
    dl_cfg = DataLoaderConfig(
        symbols=symbols,
        start_date=start,
        end_date=end,
        freq=cfg.freq or "daily",
    )
    return DataLoader(dl_cfg)


def build_engine(cfg: BacktestRunConfig, **overrides):
    """一站式构建完整回测引擎

    Parameters
    ----------
    cfg : BacktestRunConfig
        回测运行配置
    overrides : dict
        覆盖默认组件 (strategy=, slippage=, fee_model=, risk=, data_loader=)

    Returns
    -------
    BacktestEngine
        已配置好的回测引擎实例
    """
    from aipha.backtest.engine import BacktestEngine, BacktestConfig

    strategy = overrides.get("strategy") or build_strategy(cfg)
    slippage = overrides.get("slippage") or build_slippage(cfg)
    fee_model = overrides.get("fee_model") or build_fee_model(cfg)
    risk = overrides.get("risk") or build_risk_manager(cfg)

    start, end = cfg.date_range
    engine_cfg = BacktestConfig(
        initial_cash=cfg.initial_cash,
        start_date=start,
        end_date=end,
    )
    engine = BacktestEngine(engine_cfg)
    engine.strategy = strategy
    engine.slippage = slippage
    engine.fee_model = fee_model
    if risk is not None:
        engine.risk_manager = risk

    return engine


def build_and_run(cfg: BacktestRunConfig, **overrides) -> Any:
    """构建并直接运行回测, 返回 BacktestResult"""
    engine = build_engine(cfg, **overrides)
    data_loader = overrides.get("data_loader") or build_data_loader(cfg)
    return engine.run(data_loader)
