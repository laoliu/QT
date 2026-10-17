"""Sweep 参数扫描 DSR 校正工具

对参数扫描（Sweep）结果执行 Deflated Sharpe Ratio (DSR) 校正，
评估最优参数组合是否具备经多重检验校正后的统计显著性。

实现原则 A（反数据挖掘）：参数扫描选出的最优配置必须通过 DSR 校正。

用法:
    python scripts/sweep_dsr_correction.py output/sweep_double_low_plus_7y
    python scripts/sweep_dsr_correction.py output/sweep_cb_comprehensive_7y --significance 0.05

输出:
    - 控制台：DSR 校正报告
    - {sweep_dir}/dsr_correction.json：结构化结果
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

# 确保 src 在 path 中

from aipha.backtest.advanced_validation import DeflatedSharpeRatio, DSRResult

logger = logging.getLogger(__name__)


def load_sweep_results(sweep_dir: Path) -> list[dict[str, Any]]:
    """加载 sweep 结果 JSON。"""
    json_path = sweep_dir / "sweep_results.json"
    if not json_path.exists():
        raise FileNotFoundError(f"未找到 sweep 结果: {json_path}")
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # sweep_results.json 可能是 list 或 dict with 'results' key
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    raise ValueError(f"无法解析 sweep 结果格式: {type(data)}")


def check_boundary_params(
    results: list[dict[str, Any]],
    best_result: dict[str, Any],
    sweep_config_path: Path | None = None,
) -> list[dict[str, str]]:
    """检查最优参数是否触达扫描边界（原则 A 红旗检测）。

    Returns:
        list of boundary warnings, each with 'param', 'value', 'range', 'warning'
    """
    warnings: list[dict[str, str]] = []

    # 从所有结果中提取每个参数的范围
    param_keys = set()
    for r in results:
        params = r.get("params", r.get("param_label", {}))
        if isinstance(params, dict):
            param_keys.update(params.keys())

    best_params = best_result.get("params", best_result.get("param_label", {}))
    if not isinstance(best_params, dict):
        return warnings

    # 收集每个参数的观察值范围
    param_values: dict[str, list[float]] = {k: [] for k in param_keys}
    for r in results:
        params = r.get("params", r.get("param_label", {}))
        if isinstance(params, dict):
            for k in param_keys:
                if k in params:
                    try:
                        param_values[k].append(float(params[k]))
                    except (ValueError, TypeError):
                        pass

    for k in param_keys:
        if k not in best_params or not param_values[k]:
            continue
        try:
            best_val = float(best_params[k])
        except (ValueError, TypeError):
            continue

        vals = sorted(set(param_values[k]))
        if len(vals) < 2:
            continue

        range_min, range_max = vals[0], vals[-1]

        if abs(best_val - range_max) < 1e-10:
            warnings.append({
                "param": k,
                "value": str(best_val),
                "range": f"[{range_min}, {range_max}]",
                "warning": f"🔴 参数 {k}={best_val} 触达扫描上界！"
                           f"建议扩展搜索范围至 {range_max * 1.5:.2f}+",
            })
        elif abs(best_val - range_min) < 1e-10:
            warnings.append({
                "param": k,
                "value": str(best_val),
                "range": f"[{range_min}, {range_max}]",
                "warning": f"🔴 参数 {k}={best_val} 触达扫描下界！"
                           f"建议扩展搜索范围至 {range_min * 0.5:.2f}-",
            })

    return warnings


def run_dsr_correction(
    sweep_dir: str | Path,
    significance_level: float = 0.05,
) -> dict[str, Any]:
    """对 sweep 结果执行 DSR 校正。

    Args:
        sweep_dir: sweep 输出目录
        significance_level: DSR 显著性水平

    Returns:
        DSR 校正结果字典
    """
    sweep_dir = Path(sweep_dir)
    results = load_sweep_results(sweep_dir)
    n_trials = len(results)

    if n_trials == 0:
        raise ValueError("sweep 结果为空")

    # 提取 Sharpe 排名，找到最优
    sharpe_values = []
    for r in results:
        metrics = r.get("metrics", {})
        sharpe = metrics.get("sharpe_ratio", metrics.get("sharpe", 0.0))
        sharpe_values.append(sharpe)

    best_idx = max(range(n_trials), key=lambda i: sharpe_values[i])
    best_result = results[best_idx]
    best_sharpe = sharpe_values[best_idx]

    # 提取交易日数
    best_metrics = best_result.get("metrics", {})
    trading_days = best_metrics.get("trading_days", best_metrics.get("n_days", 1700))

    # 尝试获取日收益率（如果有 daily_snapshots）
    daily_returns = None
    best_dir_name = best_result.get("task_id", best_result.get("param_label", ""))
    if isinstance(best_dir_name, str):
        # 尝试从 task 子目录读取 daily_snapshots
        for sub in sweep_dir.iterdir():
            if sub.is_dir() and best_dir_name in sub.name:
                snap_path = sub / "daily_snapshots.csv"
                if snap_path.exists():
                    daily_returns = _load_daily_returns(snap_path)
                break

    # 执行 DSR
    dsr_calc = DeflatedSharpeRatio(significance_level=significance_level)
    dsr_result = dsr_calc.compute(
        observed_sharpe=best_sharpe,
        n_trials=n_trials,
        trading_days=trading_days,
        daily_returns=daily_returns,
    )

    # 边界参数检查
    boundary_warnings = check_boundary_params(results, best_result)

    # 整理 Sharpe 分布统计
    sharpe_stats = {
        "count": n_trials,
        "min": min(sharpe_values),
        "max": max(sharpe_values),
        "mean": sum(sharpe_values) / n_trials,
        "median": sorted(sharpe_values)[n_trials // 2],
    }

    output = {
        "sweep_dir": str(sweep_dir),
        "n_trials": n_trials,
        "best_params": best_result.get("params", best_result.get("param_label", {})),
        "best_sharpe": best_sharpe,
        "dsr": dsr_result.to_dict(),
        "sharpe_distribution": sharpe_stats,
        "boundary_warnings": boundary_warnings,
        "verdict": _make_verdict(dsr_result, boundary_warnings),
    }

    # 保存结果
    out_path = sweep_dir / "dsr_correction.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    logger.info(f"DSR 校正结果已保存: {out_path}")

    return output


def _load_daily_returns(csv_path: Path) -> list[float] | None:
    """从 daily_snapshots.csv 加载日收益率序列。"""
    try:
        returns: list[float] = []
        prev_equity = None
        with open(csv_path, "r", encoding="utf-8") as f:
            header = f.readline().strip().split(",")
            eq_idx = None
            for i, col in enumerate(header):
                if col.strip().lower() in (
                    "equity", "total_equity", "portfolio_value", "nav",
                ):
                    eq_idx = i
                    break
            if eq_idx is None:
                return None
            for line in f:
                parts = line.strip().split(",")
                if len(parts) > eq_idx:
                    try:
                        equity = float(parts[eq_idx])
                        if prev_equity and prev_equity > 0:
                            returns.append(equity / prev_equity - 1)
                        prev_equity = equity
                    except ValueError:
                        continue
        return returns if len(returns) >= 20 else None
    except Exception:
        return None


def _make_verdict(
    dsr: DSRResult,
    boundary_warnings: list[dict[str, str]],
) -> dict[str, Any]:
    """生成综合判定。"""
    issues = []

    if not dsr.is_significant:
        issues.append(
            f"DSR 不显著 (p={dsr.p_value:.4f} > {dsr.significance_level})"
        )

    if dsr.haircut_sharpe < 0.8:
        issues.append(
            f"Haircut Sharpe={dsr.haircut_sharpe:.3f} < 0.8 硬性门槛"
        )

    for w in boundary_warnings:
        issues.append(w["warning"])

    passed = dsr.is_significant and len(boundary_warnings) == 0
    return {
        "passed": passed,
        "issues": issues,
        "recommendation": (
            "✅ 通过 DSR 校正，可进入下一阶段验证"
            if passed else
            "❌ 未通过 DSR 校正，" + "; ".join(issues)
        ),
    }


def print_report(result: dict[str, Any]) -> None:
    """打印人类可读的 DSR 校正报告。"""
    print("\n" + "=" * 70)
    print("  Sweep DSR 校正报告（原则 A：反数据挖掘）")
    print("=" * 70)

    print(f"\n📁 Sweep 目录: {result['sweep_dir']}")
    print(f"📊 参数组合数 (n_trials): {result['n_trials']}")
    print(f"🏆 最优参数: {result['best_params']}")
    print(f"📈 最优 Sharpe: {result['best_sharpe']:.4f}")

    dsr = result["dsr"]
    print(f"\n--- DSR 校正结果 ---")
    print(f"  E[max(SR)] (随机最优): {dsr['expected_max_sharpe']:.4f}")
    print(f"  Haircut Sharpe:        {dsr['haircut_sharpe']:.4f}")
    print(f"  p-value:               {dsr['p_value']:.6f}")
    print(f"  显著 (p < {dsr['significance_level']}): "
          f"{'✅ 是' if dsr['is_significant'] else '❌ 否'}")

    dist = result["sharpe_distribution"]
    print(f"\n--- Sharpe 分布 ---")
    print(f"  {dist['count']} 组: "
          f"min={dist['min']:.3f}, median={dist['median']:.3f}, "
          f"max={dist['max']:.3f}, mean={dist['mean']:.3f}")

    if result["boundary_warnings"]:
        print(f"\n--- 边界触达检查 ---")
        for w in result["boundary_warnings"]:
            print(f"  {w['warning']}")
    else:
        print(f"\n--- 边界触达检查: ✅ 无边界触达 ---")

    verdict = result["verdict"]
    print(f"\n{'=' * 70}")
    print(f"  综合判定: {verdict['recommendation']}")
    print(f"{'=' * 70}\n")


def main():
    """CLI 入口。"""
    parser = argparse.ArgumentParser(
        description="Sweep 参数扫描 DSR 校正（原则 A）"
    )
    parser.add_argument(
        "sweep_dir",
        help="sweep 输出目录路径 (e.g., output/sweep_double_low_plus_7y)",
    )
    parser.add_argument(
        "--significance", "-s",
        type=float,
        default=0.05,
        help="DSR 显著性水平 (默认 0.05)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    result = run_dsr_correction(args.sweep_dir, args.significance)
    print_report(result)


if __name__ == "__main__":
    main()
