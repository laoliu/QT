"""Config Runner — YAML 驱动的端到端回测流水线

唯一入口: run_from_config(yaml_path) → BacktestResult

流程:
  1. 解析 YAML → BacktestRunConfig
  2. 加载数据 (data_loader)
  3. 构造策略 (strategy registry)
  4. 构造风控 (risk constraints)
  5. 构造执行模型 (fill + slippage + fees)
  6. 运行回测引擎
  7. 存储结果 (result_store)
  8. 生成报告 (html_report) 
  9. 返回 BacktestResult
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from aipha.backtest.data_loader import load_bars_by_date
from aipha.backtest.engine import BacktestConfig, BacktestEngine, BacktestResult
from aipha.backtest.result_store import ResultStore
from aipha.config.config_models import BacktestRunConfig
from aipha.config.config_parser import load_config
from aipha.execution.fees import FeeModel
from aipha.execution.fill_models import create_fill_model
from aipha.execution.slippage import create_slippage_model
from aipha.risk.constraints import (
    CompositeRiskManager,
    DrawdownLimit,
    PositionLimit,
    SectorLimit,
    TurnoverLimit,
)
from aipha.strategy.base import Strategy
from aipha.strategy.registry import StrategyRegistry

logger = logging.getLogger(__name__)


def _build_strategy(cfg: BacktestRunConfig) -> Strategy:
    """从配置构造策略实例。"""
    # 先确保所有策略已注册
    try:
        import aipha.strategy.register_all  # noqa: F401
    except Exception:
        pass

    registry = StrategyRegistry()
    name = cfg.strategy.name
    params = cfg.strategy.params or {}

    strategy_cls = registry.get(name)
    if strategy_cls is None:
        available = registry.list_strategies()
        raise ValueError(f"策略 '{name}' 未注册。可用: {available}")

    try:
        return strategy_cls(**params)
    except TypeError:
        # 部分策略不接受参数
        return strategy_cls()


def _build_risk_manager(cfg: BacktestRunConfig) -> CompositeRiskManager | None:
    """从配置构造风控管理器。"""
    rc = cfg.risk
    if not rc.enabled:
        return None

    constraints = []

    if rc.max_position_weight > 0:
        constraints.append(PositionLimit(max_weight=rc.max_position_weight))

    if rc.max_drawdown > 0:
        constraints.append(DrawdownLimit(max_drawdown=rc.max_drawdown))

    if rc.max_sector_weight is not None:
        constraints.append(SectorLimit(max_weight=rc.max_sector_weight))

    if rc.max_daily_turnover is not None:
        constraints.append(TurnoverLimit(max_turnover=rc.max_daily_turnover))

    if not constraints:
        return None

    return CompositeRiskManager(constraints)


def _build_engine(cfg: BacktestRunConfig, strategy: Strategy) -> BacktestEngine:
    """从配置构造回测引擎。"""
    cc = cfg.cost

    engine_config = BacktestConfig(
        initial_capital=cfg.initial_capital,
        lot_size=cfg.lot_size,
        commission_rate=cc.commission_rate,
        commission_min=cc.commission_min,
        stamp_tax_rate=cc.stamp_tax_rate,
        transfer_fee_rate=cc.transfer_fee_rate,
        slippage_bps=cc.slippage_bps,
        t_plus_n=cfg.t_plus_n,
    )

    fill_model = create_fill_model(cfg.fill_model.name)

    slippage_model = create_slippage_model(
        cc.slippage_model,
        bps=cc.slippage_bps,
        **(cc.slippage_params or {}),
    )

    fee_model = FeeModel(
        commission_rate=cc.commission_rate,
        commission_min=cc.commission_min,
        stamp_tax_rate=cc.stamp_tax_rate,
        transfer_fee_rate=cc.transfer_fee_rate,
    )

    risk_manager = _build_risk_manager(cfg)

    return BacktestEngine(
        config=engine_config,
        strategy=strategy,
        fill_model=fill_model,
        slippage_model=slippage_model,
        fee_model=fee_model,
        risk_manager=risk_manager,
    )


def _get_symbols(cfg: BacktestRunConfig) -> list[str]:
    """获取股票列表。"""
    if cfg.data.symbols:
        return cfg.data.symbols

    # 如果配置了 base_index，尝试获取成分股
    if cfg.universe.base_index:
        try:
            from aipha.universe.policy import UniversePolicy, PolicyConfig, UniverseType
            ut = {
                "CSI300": UniverseType.CSI300,
                "CSI500": UniverseType.CSI500,
                "CSI1000": UniverseType.CSI1000,
            }.get(cfg.universe.base_index.upper(), UniverseType.ALL_A)
            logger.info("Universe: %s (需外部提供成分股列表)", ut.value)
        except Exception:
            pass

    # Fallback: 空列表（调用方需提供）
    logger.warning("未指定 symbols，需要外部提供股票列表")
    return []


def run_from_config(
    config_path: str | Path,
    symbols_override: list[str] | None = None,
    store_result: bool = True,
) -> BacktestResult:
    """从 YAML 配置运行完整回测。

    Parameters
    ----------
    config_path : str | Path
        YAML 配置文件路径
    symbols_override : list[str] | None
        覆盖配置中的股票列表
    store_result : bool
        是否存储结果到 SQLite

    Returns
    -------
    BacktestResult
    """
    logger.info("Loading config: %s", config_path)
    cfg = load_config(config_path)

    # 获取股票列表
    symbols = symbols_override or _get_symbols(cfg)
    if not symbols:
        raise ValueError("必须提供 data.symbols 或 symbols_override")

    # 加载数据
    logger.info("Loading data: %d symbols, %s ~ %s", len(symbols), cfg.data.start_date, cfg.data.end_date)
    bars_by_date = load_bars_by_date(
        symbols=symbols,
        start=cfg.data.start_date,
        end=cfg.data.end_date,
        source=cfg.data.source,
        frequency=cfg.data.frequency,
    )
    logger.info("Loaded %d trading days", len(bars_by_date))

    if not bars_by_date:
        raise ValueError("数据加载失败，无交易日数据")

    # 构造策略
    strategy = _build_strategy(cfg)
    logger.info("Strategy: %s", cfg.strategy.name)

    # 构造并运行引擎
    engine = _build_engine(cfg, strategy)
    logger.info("Running backtest...")
    result = engine.run(bars_by_date)

    # 存储结果
    if store_result:
        try:
            output_dir = Path(cfg.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            store = ResultStore(str(output_dir / "results.db"))
            store.save(cfg.name, result)
            logger.info("Result saved to %s", output_dir / "results.db")
        except Exception as e:
            logger.warning("Failed to save result: %s", e)

    # 生成 HTML 报告
    try:
        from aipha.backtest.html_report import generate_html_report
        output_dir = Path(cfg.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / f"{cfg.name}_report.html"
        generate_html_report(result, str(report_path))
        logger.info("HTML report: %s", report_path)
    except Exception as e:
        logger.warning("Failed to generate HTML report: %s", e)

    # 日志摘要
    if result.daily_snapshots:
        final = result.daily_snapshots[-1]
        logger.info(
            "Backtest done: NAV=%.2f, Return=%.2f%%, Trades=%d",
            result.final_nav,
            final.cumulative_return * 100,
            result.total_trades,
        )

    return result


def run_from_dict(
    config_dict: dict[str, Any],
    symbols_override: list[str] | None = None,
) -> BacktestResult:
    """从 dict 配置直接运行回测（便于 bridge 调用）。"""
    from aipha.config.config_parser import parse_config
    cfg = parse_config(config_dict)

    symbols = symbols_override or _get_symbols(cfg)
    if not symbols:
        raise ValueError("必须提供 symbols")

    bars_by_date = load_bars_by_date(
        symbols=symbols,
        start=cfg.data.start_date,
        end=cfg.data.end_date,
        source=cfg.data.source,
    )

    strategy = _build_strategy(cfg)
    engine = _build_engine(cfg, strategy)
    return engine.run(bars_by_date)
