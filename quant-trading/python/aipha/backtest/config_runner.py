"""Config Runner — YAML 驱动的端到端回测流水线

唯一入口: run_from_config(yaml_path) → BacktestResult

流程:
  1. 解析 YAML → BacktestRunConfig
  2. 加载数据 (data_loader / tick_data_loader)
  3. 构造策略 (strategy registry)
  4. 构造风控 (risk constraints)
  5. 构造执行模型 (fill + slippage + fees)
  6. 运行回测引擎（bar 或 tick 级别）
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


def _build_tick_strategy(cfg: BacktestRunConfig):
    """从配置构造 Tick 策略实例。"""
    from aipha.strategy.base import TickStrategy
    from aipha.strategy.registry import TickStrategyRegistry

    try:
        import aipha.strategy.register_all  # noqa: F401
    except Exception:
        pass

    registry = TickStrategyRegistry()
    name = cfg.strategy.name
    params = cfg.strategy.params or {}

    # 先在 tick 策略注册表中查找
    strategy_cls = registry.get(name)
    if strategy_cls is not None:
        try:
            return strategy_cls(**params)
        except TypeError:
            return strategy_cls()

    # 回退：普通策略注册表
    bar_registry = StrategyRegistry()
    bar_cls = bar_registry.get(name)
    if bar_cls is not None:
        raise ValueError(
            f"策略 '{name}' 是 bar 级别策略，不支持 tick 回测。"
            f"请实现 TickStrategy 子类或使用 frequency='1d'"
        )

    available = registry.list_strategies()
    raise ValueError(f"Tick策略 '{name}' 未注册。可用: {available}")


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
                "HSI": UniverseType.HSI,
                "HSCEI": UniverseType.HSCEI,
                "HSTECH": UniverseType.HSTECH,
                "ALL_HK": UniverseType.ALL_HK,
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

    # 判断是否为 tick 模式
    is_tick = cfg.data.frequency == "tick" or cfg.tick.enabled

    if is_tick:
        return _run_tick_backtest(cfg, symbols, store_result)

    # ==================== Bar 回测 ====================

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


def _run_tick_backtest(
    cfg: BacktestRunConfig,
    symbols: list[str],
    store_result: bool = True,
) -> BacktestResult:
    """运行 tick 级别回测。

    Returns BacktestResult（兼容 bar 回测的结果格式）。
    """
    from aipha.backtest.tick_data_loader import load_ticks_by_date
    from aipha.backtest.tick_engine import (
        TickBacktestConfig,
        TickBacktestEngine,
        TickBacktestResult,
    )
    from aipha.backtest.engine import (
        BacktestResult,
        DailySnapshot,
        TotalFees,
        TradeRecord,
    )

    logger.info("[Tick] 加载 tick 数据: %d 只, %s ~ %s", len(symbols), cfg.data.start_date, cfg.data.end_date)

    ticks_by_date = load_ticks_by_date(
        symbols=symbols,
        start=cfg.data.start_date,
        end=cfg.data.end_date,
        source=cfg.tick.tick_source,
        cache_dir=cfg.tick.tick_cache_dir,
        bridge_url=cfg.tick.bridge_url,
    )
    logger.info("[Tick] 加载完成: %d 交易日", len(ticks_by_date))

    if not ticks_by_date:
        raise ValueError("Tick 数据加载失败，无交易日数据")

    # 构造 tick 策略
    tick_strategy = _build_tick_strategy(cfg)
    logger.info("[Tick] Strategy: %s", cfg.strategy.name)

    # 构造 tick 引擎
    cc = cfg.cost
    tick_config = TickBacktestConfig(
        initial_capital=cfg.initial_capital,
        lot_size=cfg.lot_size,
        commission_rate=cc.commission_rate,
        commission_min=cc.commission_min,
        stamp_tax_rate=cc.stamp_tax_rate,
        transfer_fee_rate=cc.transfer_fee_rate,
        slippage_bps=cc.slippage_bps,
        t_plus_n=cfg.t_plus_n,
        fill_mode=cfg.tick.fill_mode,
        aggregate_interval=cfg.tick.aggregate_interval,
    )

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

    tick_engine = TickBacktestEngine(
        config=tick_config,
        strategy=tick_strategy,
        slippage_model=slippage_model,
        fee_model=fee_model,
        risk_manager=risk_manager,
    )

    logger.info("[Tick] Running tick backtest...")
    tick_result = tick_engine.run(ticks_by_date)

    # 将 TickBacktestResult 转为兼容的 BacktestResult
    result = _convert_tick_result(tick_result)

    # 存储结果
    if store_result:
        try:
            output_dir = Path(cfg.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            store = ResultStore(str(output_dir / "results.db"))
            store.save(cfg.name, result)
            logger.info("[Tick] Result saved to %s", output_dir / "results.db")
        except Exception as e:
            logger.warning("[Tick] Failed to save result: %s", e)

    # 日志摘要
    if tick_result.daily_snapshots:
        final = tick_result.daily_snapshots[-1]
        logger.info(
            "[Tick] Backtest done: NAV=%.2f, Return=%.2f%%, Trades=%d, Ticks=%d",
            tick_result.final_nav,
            final.cumulative_return * 100,
            tick_result.total_trades,
            tick_result.total_ticks_processed,
        )

    return result


def _convert_tick_result(tick_result) -> BacktestResult:
    """将 TickBacktestResult 转换为标准 BacktestResult（兼容报告/存储）。"""
    from aipha.backtest.engine import (
        BacktestResult,
        DailySnapshot,
        TotalFees,
        TradeRecord,
    )
    from aipha.execution.fees import TradeFees

    snapshots = []
    for ts in tick_result.daily_snapshots:
        snapshots.append(DailySnapshot(
            trade_date=ts.trade_date,
            nav=ts.nav,
            cash=ts.cash,
            frozen_cash=0.0,
            daily_return=ts.daily_return,
            cumulative_return=ts.cumulative_return,
            positions=ts.positions,
            position_values=ts.position_values,
        ))

    trades = []
    for tt in tick_result.trades:
        trades.append(TradeRecord(
            trade_id=tt.trade_id,
            order_id=tt.order_id,
            trade_date=tt.trade_date,
            trade_time=tt.trade_time,
            symbol=tt.symbol,
            side=tt.side,
            qty=tt.qty,
            price=tt.price,
            notional=tt.notional,
            fees=tt.fees,
            reason=tt.reason,
        ))

    return BacktestResult(
        strategy_name=tick_result.strategy_name,
        start_date=tick_result.start_date,
        end_date=tick_result.end_date,
        initial_capital=tick_result.initial_capital,
        final_nav=tick_result.final_nav,
        final_cash=tick_result.final_cash,
        final_positions=tick_result.final_positions,
        daily_snapshots=snapshots,
        trades=trades,
        total_trades=tick_result.total_trades,
        total_fees=TotalFees(
            commission=tick_result.total_fees_commission,
            stamp_tax=tick_result.total_fees_stamp_tax,
            transfer_fee=tick_result.total_fees_transfer_fee,
        ),
    )


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

    # 判断是否为 tick 模式
    is_tick = cfg.data.frequency == "tick" or cfg.tick.enabled
    if is_tick:
        return _run_tick_backtest(cfg, symbols, store_result=False)

    bars_by_date = load_bars_by_date(
        symbols=symbols,
        start=cfg.data.start_date,
        end=cfg.data.end_date,
        source=cfg.data.source,
    )

    strategy = _build_strategy(cfg)
    engine = _build_engine(cfg, strategy)
    return engine.run(bars_by_date)
