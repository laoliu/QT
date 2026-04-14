"""并行回测 — 参数扫描 / 批量回测"""
from __future__ import annotations

import itertools
import logging
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ParallelTask:
    task_id: str
    strategy_name: str
    params: dict[str, Any]
    date_range: tuple[str, str]
    initial_cash: float = 1_000_000
    symbols: list[str] = field(default_factory=list)


@dataclass
class ParallelResult:
    task_id: str
    params: dict[str, Any]
    success: bool
    sharpe: float = 0.0
    total_return: float = 0.0
    max_drawdown: float = 0.0
    annual_return: float = 0.0
    elapsed_seconds: float = 0.0
    error: Optional[str] = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SweepSummary:
    total_tasks: int
    completed: int
    failed: int
    best_result: Optional[ParallelResult]
    all_results: list[ParallelResult]
    total_elapsed: float

    def report(self) -> str:
        lines = [
            "=" * 60,
            f"参数扫描: {self.total_tasks} 任务, 成功 {self.completed}, 失败 {self.failed}",
            f"总耗时: {self.total_elapsed:.1f}s",
        ]
        if self.best_result:
            b = self.best_result
            lines.append(f"最优: {b.params} → Sharpe={b.sharpe:.3f} 年化={b.annual_return:.2%} MDD={b.max_drawdown:.2%}")
        lines.append("=" * 60)
        return "\n".join(lines)


def generate_param_grid(param_space: dict[str, list]) -> list[dict[str, Any]]:
    """生成参数网格

    >>> generate_param_grid({"window": [5, 10], "threshold": [0.01, 0.02]})
    [{'window': 5, 'threshold': 0.01}, {'window': 5, 'threshold': 0.02},
     {'window': 10, 'threshold': 0.01}, {'window': 10, 'threshold': 0.02}]
    """
    keys = sorted(param_space.keys())
    combos = list(itertools.product(*(param_space[k] for k in keys)))
    return [dict(zip(keys, vals)) for vals in combos]


def create_sweep_tasks(
    strategy_name: str,
    param_space: dict[str, list],
    date_range: tuple[str, str],
    initial_cash: float = 1_000_000,
    symbols: list[str] | None = None,
) -> list[ParallelTask]:
    """从参数空间创建扫描任务列表"""
    grid = generate_param_grid(param_space)
    tasks = []
    for i, params in enumerate(grid):
        tasks.append(ParallelTask(
            task_id=f"sweep_{i:04d}",
            strategy_name=strategy_name,
            params=params,
            date_range=date_range,
            initial_cash=initial_cash,
            symbols=symbols or [],
        ))
    return tasks


def _run_single_task(task: ParallelTask) -> ParallelResult:
    """在子进程中执行单个回测任务"""
    t0 = time.time()
    try:
        from aipha.backtest.config_runner import BacktestRunConfig
        from aipha.backtest.engine_builder import build_and_run

        cfg = BacktestRunConfig(
            strategy_name=task.strategy_name,
            strategy_params=task.params,
            date_range=task.date_range,
            initial_cash=task.initial_cash,
            symbols=task.symbols,
        )
        result = build_and_run(cfg)
        metrics = result.metrics if hasattr(result, "metrics") else {}
        if isinstance(metrics, dict):
            sharpe = metrics.get("sharpe_ratio", 0)
            total_ret = metrics.get("total_return", 0)
            mdd = metrics.get("max_drawdown", 0)
            ann = metrics.get("annual_return", 0)
        else:
            sharpe = getattr(metrics, "sharpe_ratio", 0)
            total_ret = getattr(metrics, "total_return", 0)
            mdd = getattr(metrics, "max_drawdown", 0)
            ann = getattr(metrics, "annual_return", 0)

        return ParallelResult(
            task_id=task.task_id, params=task.params, success=True,
            sharpe=sharpe, total_return=total_ret,
            max_drawdown=mdd, annual_return=ann,
            elapsed_seconds=time.time() - t0,
        )
    except Exception as e:
        return ParallelResult(
            task_id=task.task_id, params=task.params, success=False,
            error=str(e), elapsed_seconds=time.time() - t0,
        )


def run_parallel_backtests(
    tasks: list[ParallelTask],
    max_workers: int | None = None,
    rank_by: str = "sharpe",
) -> SweepSummary:
    """并行执行回测任务列表"""
    t0 = time.time()
    results: list[ParallelResult] = []

    n_workers = max_workers or min(len(tasks), 4)
    logger.info("启动参数扫描: %d 任务, %d 进程", len(tasks), n_workers)

    with ProcessPoolExecutor(max_workers=n_workers) as pool:
        futures = {pool.submit(_run_single_task, t): t for t in tasks}
        for future in as_completed(futures):
            res = future.result()
            results.append(res)
            status = "OK" if res.success else f"FAIL: {res.error}"
            logger.info("[%s] %s (%.1fs)", res.task_id, status, res.elapsed_seconds)

    succeeded = [r for r in results if r.success]
    best = max(succeeded, key=lambda r: getattr(r, rank_by, 0)) if succeeded else None

    return SweepSummary(
        total_tasks=len(tasks), completed=len(succeeded),
        failed=len(tasks) - len(succeeded),
        best_result=best, all_results=results,
        total_elapsed=time.time() - t0,
    )
