"""并行回测执行器

支持在多进程中并发执行同一策略的不同参数组合，
适用于参数扫描（parameter sweep）、敏感性分析等场景。

设计要点:
- 使用 ProcessPoolExecutor（Windows 兼容，CPU 密集型）
- 每个 worker 独立加载数据（利用 Parquet 磁盘缓存，避免序列化大对象）
- 每个 worker 独立创建策略 + 引擎实例（策略含内部状态）
- 返回轻量 metrics dict（不含大型 daily_snapshots）

Classes:
    ParallelTask: 单个并行回测任务描述
    ParallelResult: 单个并行回测执行结果
    SweepSummary: 参数扫描汇总

Functions:
    generate_param_grid: 生成参数笛卡尔积
    create_sweep_tasks: 从基础配置 + 参数网格创建任务列表
    run_parallel_backtests: 并行执行回测任务
"""

from __future__ import annotations

import copy
import itertools
import logging
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ============================================================================
# 数据结构
# ============================================================================


@dataclass
class ParallelTask:
    """单个并行回测任务

    Attributes:
        task_id: 任务编号（唯一标识）
        config_dict: 完整回测配置字典（可被 load_config_from_dict 解析）
        param_label: 参数标签（便于识别，如 "top_k=5_rebal=3"）
        output_dir: 该任务的独立输出目录
    """

    task_id: int
    config_dict: dict[str, Any]
    param_label: str = ""
    output_dir: str = ""


@dataclass
class ParallelResult:
    """单个并行回测执行结果

    Attributes:
        task_id: 对应任务编号
        param_label: 参数标签
        params: 本次使用的策略参数
        metrics: 指标字典（annualized_return, max_drawdown_pct, sharpe_ratio 等）
        success: 是否成功
        error: 失败时的错误信息
        elapsed_seconds: 执行耗时（秒）
    """

    task_id: int
    param_label: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    success: bool = True
    error: str = ""
    elapsed_seconds: float = 0.0


@dataclass
class SweepSummary:
    """参数扫描汇总

    Attributes:
        total_tasks: 总任务数
        success_count: 成功数
        fail_count: 失败数
        total_elapsed: 总耗时（秒）
        results: 所有结果（按 annualized_return 降序排列）
        best_result: 最佳结果（按 annualized_return）
    """

    total_tasks: int
    success_count: int
    fail_count: int
    total_elapsed: float
    results: list[ParallelResult]
    best_result: ParallelResult | None = None


# ============================================================================
# 参数网格生成
# ============================================================================


def generate_param_grid(param_ranges: dict[str, list]) -> list[dict[str, Any]]:
    """生成参数笛卡尔积

    Args:
        param_ranges: 参数名 → 候选值列表
            示例: {"top_k": [3, 5, 8], "rebalance_days": [1, 3, 5]}
            生成 3 × 3 = 9 组参数

    Returns:
        参数字典列表，每个字典是一组参数组合
    """
    if not param_ranges:
        return [{}]

    keys = list(param_ranges.keys())
    values = [param_ranges[k] for k in keys]

    grid = []
    for combo in itertools.product(*values):
        grid.append(dict(zip(keys, combo)))
    return grid


def _make_param_label(params: dict[str, Any], base_params: dict[str, Any]) -> str:
    """生成参数标签（只包含与基础参数不同的部分）"""
    changed = {}
    for k, v in params.items():
        if k not in base_params or base_params[k] != v:
            changed[k] = v
    if not changed:
        return "baseline"
    parts = []
    for k, v in sorted(changed.items()):
        if isinstance(v, float):
            parts.append(f"{k}={v:.4g}")
        else:
            parts.append(f"{k}={v}")
    return "_".join(parts)


# ============================================================================
# 任务创建
# ============================================================================


def create_sweep_tasks(
    base_config_dict: dict[str, Any],
    param_ranges: dict[str, list],
    output_base_dir: str = "output/sweep",
) -> list[ParallelTask]:
    """从基础配置 + 参数网格创建并行任务列表

    Args:
        base_config_dict: 基础回测配置字典（YAML 格式解析后的 dict）
        param_ranges: 参数扫描范围（key = 策略参数名, value = 候选值列表）
        output_base_dir: 输出基础目录（每个任务会在其下创建子目录）

    Returns:
        ParallelTask 列表
    """
    grid = generate_param_grid(param_ranges)
    # 归一化 config: / parameters: → params: (避免与 _normalize_config 不一致)
    _strat = base_config_dict.get("strategy", {})
    if isinstance(_strat, dict):
        if "params" not in _strat and "config" in _strat:
            _strat["params"] = _strat.pop("config")
        if "params" not in _strat and "parameters" in _strat:
            _strat["params"] = _strat.pop("parameters")

    base_params = base_config_dict.get("strategy", {}).get("params", {})
    tasks = []

    for i, param_override in enumerate(grid):
        cfg = copy.deepcopy(base_config_dict)

        # 覆盖策略参数
        if "strategy" not in cfg:
            cfg["strategy"] = {}
        if "params" not in cfg["strategy"]:
            cfg["strategy"]["params"] = {}
        cfg["strategy"]["params"].update(param_override)

        # 生成标签
        label = _make_param_label(param_override, base_params)

        # 独立输出目录
        out_dir = str(Path(output_base_dir) / f"task_{i:04d}_{label}")
        cfg["output_dir"] = out_dir

        # 更新配置名
        base_name = cfg.get("name", "sweep")
        cfg["name"] = f"{base_name}__{label}"

        tasks.append(ParallelTask(
            task_id=i,
            config_dict=cfg,
            param_label=label,
            output_dir=out_dir,
        ))

    return tasks


# ============================================================================
# Worker 进程函数
# ============================================================================


def _worker_run_single(task: ParallelTask) -> ParallelResult:
    """在子进程中执行单个回测任务

    此函数在 worker 进程中运行，独立完成:
    1. 从配置字典解析为 BacktestRunConfig
    2. 加载数据（利用磁盘 Parquet 缓存）
    3. 创建策略与引擎
    4. 执行回测
    5. 提取指标并返回 ParallelResult

    Args:
        task: 并行任务描述

    Returns:
        ParallelResult（包含指标或错误信息）
    """
    t0 = time.time()
    try:
        # 延迟导入，避免主进程载入时的副作用
        from aipha.backtest.config_parser import parse_config, to_backtest_config, validate_config
        from aipha.backtest.config_runner import (
            _build_audit_logger,
            _build_degradation_manager,
            _build_market_regime_detector,
            _build_position_sizing,
            _build_risk_manager,
            _build_stop_loss_manager,
            _build_universe_policy,
            _build_weight_optimizer,
            _create_engine_components,
            _create_strategy,
            _load_data,
        )
        from aipha.backtest.engine import BacktestEngine
        from aipha.backtest.report import extract_metrics_dict
        from aipha.utils.reproducibility import set_global_seed

        # 1. 解析配置
        config = parse_config(task.config_dict)

        # 1.1 验证配置合理性（Phase 1 加固，§4.5.1 parallel_runner 必须调用）
        _strict = task.config_dict.get("_strict_mode", True)
        config_errors = validate_config(config, strict_mode=_strict)
        if config_errors:
            elapsed = time.time() - t0
            return ParallelResult(
                task_id=task.task_id,
                param_label=task.param_label,
                params=dict(task.config_dict.get("strategy", {}).get("params", {})),
                success=False,
                error=f"ConfigValidationError: {'; '.join(config_errors)}",
                elapsed_seconds=elapsed,
            )

        # 2. 设置随机种子
        if config.random_seed is not None:
            set_global_seed(config.random_seed)

        # 3. 创建策略与引擎组件
        strategy = _create_strategy(config)
        engine_config = to_backtest_config(config)
        fill_model, slippage, fee_model, rules = _create_engine_components(config)
        universe_policy = _build_universe_policy(config)
        risk_manager = _build_risk_manager(config)
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
            stop_loss_manager=stop_loss_manager,
            audit_logger=audit_logger,
            market_regime_detector=market_regime_detector,
            position_sizing=position_sizing,
            degradation_manager=degradation_manager,
            weight_optimizer=weight_optimizer,
        )

        # 4. 加载数据
        bars_by_date = _load_data(config)

        # 5. 运行回测
        result = engine.run(bars_by_date)

        # 6. 提取指标
        metrics = extract_metrics_dict(result)

        elapsed = time.time() - t0
        return ParallelResult(
            task_id=task.task_id,
            param_label=task.param_label,
            params=dict(task.config_dict.get("strategy", {}).get("params", {})),
            metrics=metrics,
            success=True,
            elapsed_seconds=elapsed,
        )

    except Exception as e:
        elapsed = time.time() - t0
        return ParallelResult(
            task_id=task.task_id,
            param_label=task.param_label,
            params=dict(task.config_dict.get("strategy", {}).get("params", {})),
            success=False,
            error=f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            elapsed_seconds=elapsed,
        )


# ============================================================================
# 并行编排
# ============================================================================


def run_parallel_backtests(
    tasks: list[ParallelTask],
    max_workers: int | None = None,
) -> SweepSummary:
    """并行执行回测任务

    使用 ProcessPoolExecutor 在多进程中并行运行回测。
    每个 worker 独立加载数据（利用磁盘缓存），互不干扰。

    Args:
        tasks: 任务列表
        max_workers: 最大并行 worker 数（默认 CPU 核心数 - 1，至少 1）

    Returns:
        SweepSummary（含所有结果，按 annualized_return 降序）
    """
    if not tasks:
        return SweepSummary(
            total_tasks=0,
            success_count=0,
            fail_count=0,
            total_elapsed=0.0,
            results=[],
        )

    import os
    if max_workers is None:
        max_workers = max(1, os.cpu_count() - 1) if os.cpu_count() else 1

    # 限制 worker 数不超过任务数
    max_workers = min(max_workers, len(tasks))

    logger.info(
        f"启动并行回测: {len(tasks)} 个任务, {max_workers} 个 worker"
    )

    t_start = time.time()
    results: list[ParallelResult] = []

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        future_to_task = {
            executor.submit(_worker_run_single, task): task
            for task in tasks
        }

        for future in as_completed(future_to_task):
            task = future_to_task[future]
            try:
                r = future.result()
                results.append(r)
                if r.success:
                    ar = r.metrics.get("annualized_return", float("nan"))
                    mdd = r.metrics.get("max_drawdown_pct", float("nan"))
                    sr = r.metrics.get("sharpe_ratio", float("nan"))
                    logger.info(
                        f"[{r.task_id:4d}] {r.param_label:40s} | "
                        f"AR={ar:+.2%} MDD={mdd:.2%} SR={sr:.2f} | "
                        f"{r.elapsed_seconds:.1f}s"
                    )
                else:
                    logger.warning(
                        f"[{r.task_id:4d}] {r.param_label:40s} | "
                        f"FAILED: {r.error[:100]}"
                    )
            except Exception as e:
                r = ParallelResult(
                    task_id=task.task_id,
                    param_label=task.param_label,
                    success=False,
                    error=f"Future error: {e}",
                )
                results.append(r)
                logger.error(f"[{task.task_id:4d}] Future 异常: {e}")

    total_elapsed = time.time() - t_start

    # 按 annualized_return 降序排列成功结果
    success_results = [r for r in results if r.success]
    fail_results = [r for r in results if not r.success]

    success_results.sort(
        key=lambda r: r.metrics.get("annualized_return", float("-inf")),
        reverse=True,
    )

    sorted_results = success_results + fail_results

    best = success_results[0] if success_results else None

    summary = SweepSummary(
        total_tasks=len(tasks),
        success_count=len(success_results),
        fail_count=len(fail_results),
        total_elapsed=total_elapsed,
        results=sorted_results,
        best_result=best,
    )

    logger.info(
        f"并行回测完成: {summary.success_count}/{summary.total_tasks} 成功, "
        f"耗时 {total_elapsed:.1f}s"
    )
    if best:
        logger.info(
            f"最佳参数: {best.param_label} | "
            f"AR={best.metrics.get('annualized_return', 0):.2%} "
            f"MDD={best.metrics.get('max_drawdown_pct', 0):.2%} "
            f"SR={best.metrics.get('sharpe_ratio', 0):.2f}"
        )

    return summary


# ============================================================================
# 便捷入口
# ============================================================================


def sweep_strategy_params(
    config_path: str | Path,
    param_ranges: dict[str, list],
    output_base_dir: str = "output/sweep",
    max_workers: int | None = None,
) -> SweepSummary:
    """一站式参数扫描入口

    加载基础配置 → 生成参数网格 → 并行回测 → 返回汇总

    Args:
        config_path: 基础回测配置 YAML 路径
        param_ranges: 参数扫描范围
        output_base_dir: 输出根目录
        max_workers: 最大并行 worker 数

    Returns:
        SweepSummary
    """
    import yaml

    config_path = Path(config_path)
    with open(config_path, "r", encoding="utf-8") as f:
        base_config = yaml.safe_load(f)

    tasks = create_sweep_tasks(base_config, param_ranges, output_base_dir)

    logger.info(
        f"参数扫描: 策略={base_config.get('strategy', {}).get('name', '?')}, "
        f"{len(tasks)} 组参数, 范围={list(param_ranges.keys())}"
    )

    return run_parallel_backtests(tasks, max_workers=max_workers)
