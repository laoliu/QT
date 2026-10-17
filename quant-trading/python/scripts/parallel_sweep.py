#!/usr/bin/env python
"""并行参数扫描 CLI 脚本

用法:
    # 从 sweep 配置文件启动（推荐）
    python scripts/parallel_sweep.py sweep_config.yml

    # 快捷模式：指定基础配置 + 参数范围
    python scripts/parallel_sweep.py --base-config configs/cb_strategies/cb_comprehensive_opt_7y.yml \
        --param "top_k=[3,5,8,10]" \
        --param "rebalance_days=[1,3,5]" \
        --workers 4 \
        --output output/sweep_cb_comp

Sweep 配置文件格式 (YAML):
    base_config: configs/cb_strategies/cb_comprehensive_opt_7y.yml
    param_ranges:
      top_k: [3, 5, 8, 10]
      rebalance_days: [1, 3, 5]
      max_price: [130, 145, 160]
    max_workers: 4
    output_dir: output/sweep_cb_comp
    top_n: 10  # 结果表格显示前 N 条
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import logging
import sys
import time
from pathlib import Path

# 确保 src 在 sys.path 中
_project_root = Path(__file__).resolve().parent.parent
_src = _project_root
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from aipha.backtest.parallel_runner import (  # noqa: E402
    SweepSummary,
    sweep_strategy_params,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("parallel_sweep")


# ============================================================================
# 结果输出
# ============================================================================


def print_results_table(summary: SweepSummary, top_n: int = 20) -> None:
    """打印结果排行表"""
    print("\n" + "=" * 100)
    print(f"  参数扫描完成: {summary.success_count}/{summary.total_tasks} 成功, "
          f"{summary.fail_count} 失败, 耗时 {summary.total_elapsed:.1f}s")
    print("=" * 100)

    if not summary.results:
        print("  (无结果)")
        return

    # 表头
    print(f"{'Rank':>4} | {'Param Label':40s} | {'AR':>8} | {'MDD':>8} | "
          f"{'Sharpe':>7} | {'Calmar':>7} | {'WinRate':>7} | {'Time':>6}")
    print("-" * 100)

    shown = 0
    for r in summary.results:
        if not r.success:
            continue
        shown += 1
        if shown > top_n:
            break
        m = r.metrics
        print(
            f"{shown:4d} | {r.param_label:40s} | "
            f"{m.get('annualized_return', 0):+7.2%} | "
            f"{m.get('max_drawdown_pct', 0):7.2%} | "
            f"{m.get('sharpe_ratio', 0):7.2f} | "
            f"{m.get('calmar_ratio', 0):7.2f} | "
            f"{m.get('win_rate', 0):6.1%} | "
            f"{r.elapsed_seconds:5.0f}s"
        )

    if summary.fail_count > 0:
        print(f"\n  失败任务 ({summary.fail_count}):")
        for r in summary.results:
            if not r.success:
                print(f"    [{r.task_id}] {r.param_label}: {r.error[:120]}")

    print("=" * 100)


def save_results_csv(summary: SweepSummary, output_path: Path) -> Path:
    """保存结果到 CSV"""
    csv_path = output_path / "sweep_results.csv"
    output_path.mkdir(parents=True, exist_ok=True)

    success_results = [r for r in summary.results if r.success]
    if not success_results:
        logger.warning("无成功结果，跳过 CSV 保存")
        return csv_path

    # 收集所有参数键和指标键
    all_param_keys = set()
    all_metric_keys = set()
    for r in success_results:
        all_param_keys.update(r.params.keys())
        all_metric_keys.update(r.metrics.keys())

    param_keys = sorted(all_param_keys)
    metric_keys = sorted(all_metric_keys)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        header = ["rank", "task_id", "param_label"] + \
                 [f"p_{k}" for k in param_keys] + \
                 metric_keys + \
                 ["elapsed_seconds"]
        writer.writerow(header)

        for i, r in enumerate(success_results, 1):
            row = [i, r.task_id, r.param_label]
            row += [r.params.get(k, "") for k in param_keys]
            row += [r.metrics.get(k, "") for k in metric_keys]
            row += [f"{r.elapsed_seconds:.1f}"]
            writer.writerow(row)

    logger.info(f"CSV 结果已保存: {csv_path}")
    return csv_path


def save_results_json(summary: SweepSummary, output_path: Path) -> Path:
    """保存结果到 JSON"""
    json_path = output_path / "sweep_results.json"
    output_path.mkdir(parents=True, exist_ok=True)

    data = {
        "total_tasks": summary.total_tasks,
        "success_count": summary.success_count,
        "fail_count": summary.fail_count,
        "total_elapsed_seconds": round(summary.total_elapsed, 1),
        "results": [],
    }

    for r in summary.results:
        entry = {
            "task_id": r.task_id,
            "param_label": r.param_label,
            "params": r.params,
            "metrics": {k: round(v, 6) for k, v in r.metrics.items()},
            "success": r.success,
            "elapsed_seconds": round(r.elapsed_seconds, 1),
        }
        if not r.success:
            entry["error"] = r.error[:500]
        data["results"].append(entry)

    if summary.best_result:
        data["best"] = {
            "param_label": summary.best_result.param_label,
            "params": summary.best_result.params,
            "annualized_return": round(
                summary.best_result.metrics.get("annualized_return", 0), 4
            ),
            "max_drawdown_pct": round(
                summary.best_result.metrics.get("max_drawdown_pct", 0), 4
            ),
            "sharpe_ratio": round(
                summary.best_result.metrics.get("sharpe_ratio", 0), 4
            ),
        }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)

    logger.info(f"JSON 结果已保存: {json_path}")

    # 写入 SQLite 索引
    try:
        from aipha.backtest.result_store import ResultStore

        strategy_name = "unknown"
        best_sharpe = 0.0
        if summary.best_result:
            strategy_name = summary.best_result.metrics.get(
                "strategy_name",
                summary.best_result.params.get("strategy", "unknown"),
            )
            best_sharpe = summary.best_result.metrics.get("sharpe_ratio", 0)
        # 从配置推断策略名
        if strategy_name == "unknown" and data.get("results"):
            for r in data["results"]:
                sn = r.get("metrics", {}).get("strategy_name")
                if sn:
                    strategy_name = sn
                    break

        store = ResultStore()
        store.upsert_sweep_run({
            "sweep_id": output_path.name,
            "strategy_name": strategy_name,
            "total_tasks": summary.total_tasks,
            "best_params": data.get("best", {}).get("params"),
            "best_sharpe": best_sharpe,
            "sweep_config": None,
            "output_dir": str(output_path),
        })
        store.close()
        logger.info("Sweep 结果已索引到 SQLite")
    except Exception as _e:
        logger.debug("Sweep SQLite 索引写入失败（不影响结果）: %s", _e)

    return json_path


# ============================================================================
# CLI 解析
# ============================================================================


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="并行参数扫描 CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "sweep_config",
        nargs="?",
        help="Sweep 配置文件路径 (YAML)，包含 base_config + param_ranges",
    )

    # 快捷模式参数
    parser.add_argument(
        "--base-config", "-b",
        help="基础回测配置 YAML 路径（快捷模式）",
    )
    parser.add_argument(
        "--param", "-p",
        action="append",
        help='参数范围，格式: "name=[v1,v2,v3]"，可多次指定',
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=None,
        help="最大并行 worker 数（默认 CPU-1）",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="输出目录（默认 output/sweep_<timestamp>）",
    )
    parser.add_argument(
        "--top-n", "-n",
        type=int,
        default=20,
        help="结果表格显示前 N 条（默认 20）",
    )

    return parser.parse_args()


def parse_param_arg(param_str: str) -> tuple[str, list]:
    """解析 --param 参数字符串，如 'top_k=[3,5,8]'"""
    if "=" not in param_str:
        raise ValueError(f"参数格式错误: '{param_str}'，应为 'name=[v1,v2,...]'")
    name, values_str = param_str.split("=", 1)
    name = name.strip()
    try:
        values = ast.literal_eval(values_str.strip())
    except (ValueError, SyntaxError) as e:
        raise ValueError(f"参数值解析失败: '{values_str}' → {e}") from e
    if not isinstance(values, (list, tuple)):
        values = [values]
    return name, list(values)


def main():
    args = parse_args()

    import yaml

    # 确定配置来源
    if args.sweep_config:
        # 从 sweep 配置文件加载
        sweep_path = Path(args.sweep_config)
        with open(sweep_path, "r", encoding="utf-8") as f:
            sweep_cfg = yaml.safe_load(f)

        base_config_path = sweep_cfg["base_config"]
        param_ranges = sweep_cfg["param_ranges"]
        max_workers = sweep_cfg.get("max_workers", args.workers)
        output_dir = sweep_cfg.get("output_dir", args.output)
        top_n = sweep_cfg.get("top_n", args.top_n)

    elif args.base_config and args.param:
        # 快捷模式
        base_config_path = args.base_config
        param_ranges = {}
        for p in args.param:
            name, values = parse_param_arg(p)
            param_ranges[name] = values
        max_workers = args.workers
        output_dir = args.output
        top_n = args.top_n

    else:
        print("错误: 必须提供 sweep_config 文件或 --base-config + --param 参数")
        sys.exit(1)

    # 默认输出目录
    if not output_dir:
        ts = time.strftime("%Y%m%d_%H%M%S")
        output_dir = f"output/sweep_{ts}"

    output_path = Path(output_dir)

    # 计算网格大小
    grid_size = 1
    for v in param_ranges.values():
        grid_size *= len(v)

    logger.info(f"基础配置: {base_config_path}")
    logger.info(f"参数范围: {param_ranges}")
    logger.info(f"网格大小: {grid_size}")
    logger.info(f"Worker 数: {max_workers or 'auto'}")
    logger.info(f"输出目录: {output_path}")

    # 执行并行扫描
    summary = sweep_strategy_params(
        config_path=base_config_path,
        param_ranges=param_ranges,
        output_base_dir=str(output_path),
        max_workers=max_workers,
    )

    # 输出结果
    print_results_table(summary, top_n=top_n)
    save_results_csv(summary, output_path)
    save_results_json(summary, output_path)

    # 退出码
    if summary.fail_count > 0 and summary.success_count == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
