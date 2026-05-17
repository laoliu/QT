"""Configuration-driven backtest runner.

This module provides a configuration-driven approach to running backtests,
supporting YAML/JSON configuration files with explicit specification of:
- Data source and parameters
- Universe policy
- Strategy and parameters
- Fill model selection
- Backtest settings (fees, slippage, etc.)
- Output and reporting

Unified entry point:
    ``run_from_config(path)`` handles both A-stock and CB strategies,
    selecting data loader / strategy factory / engine components
    entirely from YAML config — no hardcoded parameters.

Design principles:
- All assumptions explicit in config (no hidden defaults)
- Reproducible via experiment fingerprint
- Can be run from CLI or programmatically

Module structure (post-refactor):
- config_models.py: All dataclass definitions
- config_parser.py: Parsing, serialization, validation, templates
- config_runner.py: Runner logic, factory functions, data loading
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import yaml

# --- Re-export config models for backward compatibility ---
from aipha.backtest.config_models import (  # noqa: F401
    BacktestRunConfig,
    BacktestRunResult,
    CostConfig,
    DataConfig,
    FillModelConfig,
    RiskConfig,
    StrategyConfig,
    UniverseConfig,
)

# --- Re-export config parser functions for backward compatibility ---
from aipha.backtest.config_parser import (  # noqa: F401
    config_to_dict,
    create_config_template,
    load_config,
    parse_config,
    save_config,
    save_run_result,
    to_backtest_config,
    validate_config,
)

# --- Imports used directly in this module ---
from aipha.backtest.engine import BacktestEngine, BacktestResult
from aipha.backtest.html_report import (
    extract_drawdown_series,
    extract_nav_series,
    generate_html_report,
)
from aipha.backtest.quality_gates import QualityChecker, QualityReport
from aipha.backtest.report import BacktestReport, extract_metrics_dict, generate_report
from aipha.risk.constraints import MarketRegimeDetector
from aipha.utils.reproducibility import (
    create_fingerprint,
    set_global_seed,
)

logger = logging.getLogger(__name__)

# NOTE: DataConfig, UniverseConfig, StrategyConfig, CostConfig,
# FillModelConfig, RiskConfig, BacktestRunConfig, BacktestRunResult
# are now defined in config_models.py and re-exported above.
#
# load_config, parse_config, _normalize_config, _parse_date,
# config_to_dict, save_config, to_backtest_config, validate_config,
# save_run_result, create_config_template
# are now defined in config_parser.py and re-exported above.

# Also expose _parse_date and _normalize_config for any internal users
from aipha.backtest.config_parser import _normalize_config, _parse_date  # noqa: F401


# ============================================================================
# Engine builder functions (extracted to engine_builder.py)
# ============================================================================
# Re-export all factory functions for backward compatibility
from aipha.backtest.engine_builder import (  # noqa: F401
    _SLIPPAGE_MODELS,
    _STRATEGY_MODULES,
    _build_audit_logger,
    _build_compliance_engine,
    _build_degradation_manager,
    _build_market_regime_detector,
    _build_position_sizing,
    _build_risk_manager,
    _build_stop_loss_manager,
    _build_universe_policy,
    _build_weight_optimizer,
    _create_engine_components,
    _create_slippage_model,
    _create_strategy,
    _ensure_strategies_registered,
    _strategies_registered,
)

def _build_data_summary(
    bars_by_date: dict[date, dict], config: BacktestRunConfig
) -> dict:
    """构建轻量级数据摘要，用于实验指纹的数据哈希。

    不对全部数据做 hash（大数据集太慢），而是提取"形状签名"：
    日期数、标的总数、首尾日期、每日平均标的数、数据源等。

    Args:
        bars_by_date: 按日期索引的行情字典
        config: 回测配置（提供数据源等元信息）

    Returns:
        用于传入 ``compute_data_fingerprint()`` 的摘要字典
    """
    if not bars_by_date:
        return {"n_dates": 0, "source": config.data.source}

    sorted_dates = sorted(bars_by_date.keys())
    symbols_per_day = [len(v) for v in bars_by_date.values()]

    return {
        "source": config.data.source,
        "n_dates": len(sorted_dates),
        "first_date": str(sorted_dates[0]),
        "last_date": str(sorted_dates[-1]),
        "total_bars": sum(symbols_per_day),
        "avg_symbols_per_day": round(sum(symbols_per_day) / len(symbols_per_day), 2),
        "min_symbols": min(symbols_per_day),
        "max_symbols": max(symbols_per_day),
        "start_date_cfg": str(config.data.start_date),
        "end_date_cfg": str(config.data.end_date),
    }


def _run_data_health_check(
    bars_by_date: dict[date, dict],
    *,
    block_on_error: bool = True,
) -> dict | None:
    """对回测数据执行轻量级健康检查并记录结果日志。

    使用 :func:`health_check.quick_check_bars_by_date` 检查日期排序、
    重复、每日标的数波动、价格跳变等。

    Args:
        bars_by_date: 回测数据
        block_on_error: 当检测到 error 级别问题时是否阻断回测（默认 True）

    Returns:
        健康检查摘要字典，嵌入 ``BacktestRunResult.data_health``

    Raises:
        RuntimeError: 当 block_on_error=True 且检测到 error 级别问题
    """
    try:
        from aipha.data.health_check import quick_check_bars_by_date

        summary = quick_check_bars_by_date(bars_by_date)
        status = summary.get("status", "unknown")

        if status == "pass":
            logger.info("数据健康检查: 全部通过")
        elif status == "warning":
            checks = summary.get("checks", [])
            warns = [c for c in checks if c.get("status") == "warning"]
            details = "; ".join(f"{c['name']}: {c['detail']}" for c in warns)
            logger.warning(f"数据健康检查: {len(warns)} 项警告 — {details}")
        else:
            error_msg = f"数据健康检查: 存在错误 — {summary}"
            logger.error(error_msg)
            if block_on_error:
                checks = summary.get("checks", [])
                errors = [c for c in checks if c.get("status") == "error"]
                detail = "; ".join(
                    f"{c['name']}: {c.get('detail', 'unknown')}"
                    for c in errors
                )
                raise RuntimeError(
                    f"数据健康检查发现 {len(errors)} 个 ERROR 级别问题，"
                    f"回测已阻断。详情: {detail}。"
                    f"如需强制运行，请在配置中设置 data_health_gate: false"
                )

        return summary
    except RuntimeError:
        raise  # 不吞没我们自己抛的 RuntimeError
    except Exception as e:
        logger.error("数据健康检查模块异常（回测将继续，但健康检查结果缺失）: %s", e, exc_info=True)
        return None


def _run_quality_gates(
    result: BacktestResult,
    config: BacktestRunConfig,
) -> QualityReport | None:
    """对回测结果执行质量门禁检查。

    从 BacktestResult 提取指标字典，用 QualityChecker 做阈值检查。
    如果 ``config.quality_gates_path`` 非空则加载对应 YAML；否则使用默认门禁。

    结果以日志形式输出，不中断回测流程。

    Args:
        result: 回测结果
        config: 回测运行配置

    Returns:
        QualityReport 或 None（检查失败时）
    """
    try:
        # 构建检查器
        if config.quality_gates_path:
            checker = QualityChecker.from_yaml(config.quality_gates_path)
            logger.info(f"质量门禁: 使用配置 {config.quality_gates_path}")
        else:
            checker = QualityChecker.default()
            logger.info("质量门禁: 使用默认配置")

        # 提取指标
        metrics = extract_metrics_dict(result)

        # 执行检查
        strategy_name = config.strategy.name
        report = checker.check(metrics, strategy_name=strategy_name)

        # 日志输出
        if report.all_passed:
            logger.info(
                f"质量门禁: 全部通过 ({len(report.results)} 项检查)"
            )
        else:
            for err in report.errors:
                logger.error(f"质量门禁 FAIL: {err.message}")
            for warn in report.warnings:
                logger.warning(f"质量门禁 WARN: {warn.message}")

        return report
    except Exception as e:
        logger.error("质量门禁检查模块异常（回测已完成，但质量报告缺失）: %s", e, exc_info=True)
        return None


# ============================================================================
# Data loading functions (extracted to data_loader.py)
# ============================================================================
# Re-export all data loading functions for backward compatibility
from aipha.backtest.data_loader import (  # noqa: F401
    _build_st_interval_index,
    _compute_sue_fields,
    _enrich_bar_metadata,
    _generate_synthetic_data,
    _inject_benchmark_bars,
    _is_st_by_namechange,
    _load_cb_data,
    _load_data,
    _load_fina_indicators_pit,
    _load_tushare_data,
    _pit_lookup,
    _safe_float,
    _tushare_cache_base_key,
    _tushare_data_cache,
    _tushare_data_cache_ranges,
    clear_tushare_data_cache,
)




def _apply_param_overrides(config: "BacktestRunConfig", overrides: dict) -> None:
    """将 dotted-notation overrides 应用到 config 对象（就地修改）。

    支持通用 dotted-notation 键路径，分为三个参数区：

    **自由区（无限制覆盖）**：
      strategy.params.*   → config.strategy.params[key] (任意策略参数)
      data.start_date     → config.data.start_date
      data.end_date       → config.data.end_date
      initial_capital     → config.initial_capital
      output_dir          → config.output_dir
      random_seed         → config.random_seed

    **受限区（现实模型参数，默认允许但会被 validate_config 严格检查）**：
      cost.*              → config.cost.{field} (佣金/滑点/印花税等)
      fill_model.*        → config.fill_model.{field}

    **禁止区（结构性字段，不可通过 overrides 修改）**：
      strategy.name       → 禁止修改（影响策略实例化）
      name                → 禁止修改（回测名称由配置决定）

    Args:
        config:    BacktestRunConfig 实例（就地修改）
        overrides: 覆盖键值对，例如 {"strategy.params.top_k": 5}

    Raises:
        ValueError: 包含禁止区键或路径无效
    """
    from aipha.backtest.config_parser import _parse_date

    _FORBIDDEN_KEYS = {"strategy.name", "name"}

    for key, val in overrides.items():
        if key in _FORBIDDEN_KEYS:
            raise ValueError(
                f"param_overrides 禁止覆盖结构性字段 '{key}'。"
            )

        # --- 固定映射（向后兼容 + 特殊类型转换） ---
        if key == "data.start_date":
            config.data.start_date = _parse_date(val)
        elif key == "data.end_date":
            config.data.end_date = _parse_date(val)
        elif key == "initial_capital":
            config.initial_capital = float(val)
        elif key == "output_dir":
            config.output_dir = str(val)
        elif key == "random_seed":
            config.random_seed = int(val)
        # --- strategy.params.* → 自由区 ---
        elif key.startswith("strategy.params."):
            param_name = key[len("strategy.params."):]
            if not param_name:
                raise ValueError("strategy.params. 后缺少参数名")
            config.strategy.params[param_name] = val
        # --- cost.* → 受限区 ---
        elif key.startswith("cost."):
            field_name = key[len("cost."):]
            if not hasattr(config.cost, field_name):
                raise ValueError(
                    f"param_overrides: cost.{field_name} 不是有效的 CostConfig 字段。"
                    f"有效字段: {[f.name for f in config.cost.__dataclass_fields__.values()]}"
                )
            _set_typed_field(config.cost, field_name, val)
        # --- fill_model.* → 受限区 ---
        elif key.startswith("fill_model."):
            field_name = key[len("fill_model."):]
            if not hasattr(config.fill_model, field_name):
                raise ValueError(
                    f"param_overrides: fill_model.{field_name} 不是有效的 FillModelConfig 字段。"
                )
            _set_typed_field(config.fill_model, field_name, val)
        # --- risk.* → 受限区 ---
        elif key.startswith("risk."):
            field_name = key[len("risk."):]
            if not hasattr(config.risk, field_name):
                raise ValueError(
                    f"param_overrides: risk.{field_name} 不是有效的 RiskConfig 字段。"
                )
            _set_typed_field(config.risk, field_name, val)
        else:
            raise ValueError(
                f"param_overrides 包含不支持的键 '{key}'。"
                "支持的前缀: strategy.params.*, cost.*, fill_model.*, risk.*, "
                "data.start_date, data.end_date, initial_capital, output_dir, random_seed"
            )


def _set_typed_field(obj: object, field_name: str, val: object) -> None:
    """Set a dataclass field with type coercion based on the current value's type."""
    current = getattr(obj, field_name)
    if current is None:
        setattr(obj, field_name, val)
    elif isinstance(current, float):
        setattr(obj, field_name, float(val))
    elif isinstance(current, int):
        setattr(obj, field_name, int(val))
    elif isinstance(current, bool):
        setattr(obj, field_name, bool(val))
    elif isinstance(current, str):
        setattr(obj, field_name, str(val))
    else:
        setattr(obj, field_name, val)


def run_from_config(
    config_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    param_overrides: dict | None = None,
    strict_mode: bool = True,
) -> BacktestRunResult:
    """统一配置驱动回测入口

    从 YAML 配置文件出发，自动完成:
    1. 配置加载与验证
    2. 实验指纹生成（含随机种子设定）
    3. 策略创建（支持 A-stock + CB）
    4. 引擎组件构建（fill_model / fee / slippage / rules 全部由配置驱动）
    5. 数据加载（tushare_cb / tushare / synthetic）
    6. 回测执行
    7. 结果封装与保存

    Args:
        config_path:     YAML/JSON 配置文件路径
        output_dir:      输出目录（覆盖配置中的 output_dir）
        param_overrides: 运行时覆盖参数，支持 dotted notation::

            {"data.start_date": "2018-01-01",
             "data.end_date":   "2018-12-31",
             "output_dir":      "results/stress/2018"}

        strict_mode:     是否启用严格配置验证（默认 True）。
                         压力测试/滑点扫描场景可设为 False。

    Returns:
        BacktestRunResult（含 config / result / fingerprint / output_dir）

    Raises:
        FileNotFoundError: 配置文件不存在
        ValueError: 配置验证失败
    """
    config_path = Path(config_path)

    # 1. 加载与验证
    logger.info(f"加载配置: {config_path}")
    config = load_config(config_path)

    # 1.1 应用运行时覆盖参数（压力测试/扫描场景使用）
    if param_overrides:
        _apply_param_overrides(config, param_overrides)

    errors = validate_config(config, strict_mode=strict_mode)
    if errors:
        raise ValueError(
            f"配置验证失败 ({config_path}):\n  " + "\n  ".join(errors)
        )

    # 2. 设置随机种子
    if config.random_seed is not None:
        set_global_seed(config.random_seed)

    # 3. 创建策略
    logger.info(f"创建策略: {config.strategy.name}")
    strategy = _create_strategy(config)

    # 4. 创建引擎组件（全部由配置驱动）
    engine_config = to_backtest_config(config)
    fill_model, slippage, fee_model, rules = _create_engine_components(config)
    universe_policy = _build_universe_policy(config)

    # 4.5 创建风控组件（由 risk 配置驱动）
    risk_manager = _build_risk_manager(config)
    compliance_engine = _build_compliance_engine(config)
    stop_loss_manager = _build_stop_loss_manager(config)
    audit_logger = _build_audit_logger(config)
    market_regime_detector = _build_market_regime_detector(config)
    position_sizing = _build_position_sizing(config)
    degradation_manager = _build_degradation_manager(config)
    weight_optimizer = _build_weight_optimizer(config)

    engine = BacktestEngine(
        config=engine_config,
        strategy=strategy,
        fill_model=fill_model,
        slippage=slippage,
        fee_model=fee_model,
        rules=rules,
        universe_policy=universe_policy,
        risk_manager=risk_manager,
        compliance_engine=compliance_engine,
        stop_loss_manager=stop_loss_manager,
        audit_logger=audit_logger,
        market_regime_detector=market_regime_detector,
        position_sizing=position_sizing,
        degradation_manager=degradation_manager,
        benchmark_symbol=config.risk.benchmark_symbol,
        weight_optimizer=weight_optimizer,
    )

    # 5. 加载数据
    logger.info(f"加载数据: source={config.data.source}")
    bars_by_date = _load_data(config)

    # 5.5 创建实验指纹（含数据指纹）
    data_summary = _build_data_summary(bars_by_date, config)
    fingerprint = create_fingerprint(
        config_path=str(config_path),
        data=data_summary,
        random_seed=config.random_seed,
        set_seed=False,  # 已在上方设置
    )
    logger.info(
        f"实验指纹: {fingerprint.git_short}_{fingerprint.config_hash[:8]}"
        f" data={fingerprint.data_fingerprint}"
    )

    # 5.6 数据健康检查（ERROR 级别问题阻断回测）
    health_summary = _run_data_health_check(bars_by_date)

    # 6. 运行回测
    logger.info(
        f"运行回测: {config.data.start_date} ~ {config.data.end_date}"
    )
    result = engine.run(bars_by_date)
    logger.info(f"回测完成: 最终净值 {result.final_nav:,.2f}")

    # 6.5 质量门禁检查
    quality_report = _run_quality_gates(result, config)

    # 6.6 生成分析报告
    report: BacktestReport | None = None
    try:
        report = generate_report(result)
        logger.info(
            f"报告已生成: 年化收益={report.performance.annualized_return:.2%}, "
            f"最大回撤={report.drawdown.max_drawdown_pct:.2%}, "
            f"Sharpe={report.performance.sharpe_ratio:.2f}"
        )
    except Exception as e:
        logger.warning(f"报告生成失败（不影响结果）: {e}")

    # 7. 封装结果
    effective_output_dir = Path(output_dir) if output_dir else Path(config.output_dir)

    # 7.1 生成 HTML 报告
    html_path: Path | None = None
    if report is not None:
        try:
            html_output = effective_output_dir / "backtest_report.html"
            nav_series = extract_nav_series(result.daily_snapshots)
            drawdown_series = extract_drawdown_series(result.daily_snapshots)
            html_path = generate_html_report(
                report=report,
                output_path=html_output,
                nav_series=nav_series,
                drawdown_series=drawdown_series,
                fingerprint=fingerprint.to_dict() if fingerprint else None,
            )
            logger.info(f"HTML 报告已生成: {html_path}")
        except Exception as e:
            logger.warning(f"HTML 报告生成失败（不影响结果）: {e}")

    run_result = BacktestRunResult(
        config=config,
        result=result,
        fingerprint=fingerprint,
        output_dir=effective_output_dir,
        report=report,
        html_report_path=html_path,
        data_health=health_summary,
        quality_report=quality_report,
    )

    # 7.2 自动保存指纹与运行摘要
    try:
        saved = save_run_result(run_result)
        logger.info(f"运行结果已保存: {list(saved.keys())}")
    except Exception as e:
        logger.warning(f"运行结果保存失败（不影响返回）: {e}")

    # 7.3 导出过滤漏斗日志（若策略支持）
    try:
        if hasattr(strategy, '_filter_funnel_log') and strategy._filter_funnel_log:
            import csv as _csv

            funnel_path = effective_output_dir / "filter_funnel.csv"
            with open(funnel_path, 'w', newline='', encoding='utf-8') as _f:
                writer = _csv.DictWriter(_f, fieldnames=strategy._filter_funnel_log[0].keys())
                writer.writeheader()
                writer.writerows(strategy._filter_funnel_log)
            logger.debug(f"过滤漏斗日志已保存: {funnel_path} ({len(strategy._filter_funnel_log)} 条)")
    except Exception as _e:
        logger.debug(f"过滤漏斗日志保存跳过: {_e}")

    # 7.4 自动标记策略目录为陈旧（下次平台加载时触发重建）
    #     通过触发 catalog staleness 检测实现，无需在这里直接重建
    #     （重建需要导入 strategy 注册模块，可能在 worker 进程中过重）
    try:
        from aipha.backtest.config_runner import _invalidate_catalog_cache
        _invalidate_catalog_cache()
    except Exception:
        pass  # 非关键路径

    return run_result


def _invalidate_catalog_cache() -> None:
    """标记策略目录缓存为陈旧。

    通过 touch 一个 sentinel 文件实现，使得下次
    ``load_strategy_catalog()`` 检测到陈旧并自动重建。
    实际上 staleness 由 summary.json mtime > catalog.json mtime 判断，
    而 save_run_result 已写入 summary.json，所以这里无需额外操作。
    此函数保留为显式语义标记，便于未来扩展。
    """
    pass
