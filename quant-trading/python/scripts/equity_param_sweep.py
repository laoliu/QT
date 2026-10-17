#!/usr/bin/env python3
"""A 股权益策略参数稳定性分析

对 multifactor_enhance / smallcap_rotation / pead_event 的关键参数
做邻域扰动 ±20%，检查策略是否存在参数悬崖效应。

扰动方式：对每个参数分别做 -20%, -10%, 0, +10%, +20% 五点扫描。
稳定性判定：
  - 邻域平均 Sharpe ≥ 0.3
  - 邻域 Sharpe 标准差 < 0.5
  - 邻域全部年化收益 > 0

用法:
    python scripts/equity_param_sweep.py --strategy multifactor_enhance
    python scripts/equity_param_sweep.py --strategy smallcap_rotation
    python scripts/equity_param_sweep.py --all
    python scripts/equity_param_sweep.py --strategy multifactor_enhance --synthetic

环境变量:
    TUSHARE_TOKEN: TuShare Pro API Token（真实数据必需）
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any


from aipha.backtest.config_runner import (
    BacktestRunConfig,
    load_config,
    validate_config,
)

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ============================================================================
# 策略参数定义
# ============================================================================

# 每个策略的扰动参数: {param_key: param_type}
STRATEGY_PARAMS: dict[str, dict] = {
    "multifactor_enhance": {
        "base_config": "configs/backtest/multifactor_enhance_5y.yml",
        "perturb_keys": {
            "top_k": "int",
            "rebalance_days": "int",
            "weight_value": "float",
            "weight_quality": "float",
            "weight_momentum": "float",
            "weight_volatility": "float",
            "weight_size": "float",
            "stop_loss_pct": "float",
        },
    },
    "smallcap_rotation": {
        "base_config": "configs/backtest/smallcap_rotation_5y.yml",
        "perturb_keys": {
            "top_k": "int",
            "rebalance_days": "int",
            "weight_small_cap": "float",
            "weight_momentum": "float",
            "weight_turnover": "float",
            "weight_volatility": "float",
            "stop_loss_pct": "float",
            "max_mv": "float",
        },
    },
    "pead_event": {
        "base_config": "configs/backtest/pead_event_5y.yml",
        "perturb_keys": {
            "top_k": "int",
            "rebalance_days": "int",
            "weight_sue": "float",
            "weight_earnings_growth": "float",
            "weight_quality": "float",
            "min_roe": "float",
            "stop_loss_pct": "float",
        },
    },
    "sector_momentum": {
        "base_config": "configs/backtest/sector_momentum_5y.yml",
        "perturb_keys": {
            "top_k": "int",
            "rebalance_days": "int",
            "top_industries": "int",
            "weight_momentum": "float",
            "weight_volatility": "float",
            "weight_value": "float",
            "weight_quality": "float",
            "weight_size": "float",
            "stop_loss_pct": "float",
        },
    },
    "value_quality": {
        "base_config": "configs/backtest/value_quality_5y.yml",
        "perturb_keys": {
            "top_k": "int",
            "rebalance_days": "int",
            "weight_value": "float",
            "weight_quality": "float",
            "weight_momentum": "float",
            "weight_volatility": "float",
            "weight_size": "float",
            "min_roe": "float",
            "max_pe": "float",
            "stop_loss_pct": "float",
        },
    },
    "earnings_momentum": {
        "base_config": "configs/backtest/earnings_momentum_5y.yml",
        "perturb_keys": {
            "top_k": "int",
            "rebalance_days": "int",
            "weight_earnings_accel": "float",
            "weight_revenue_growth": "float",
            "weight_sue": "float",
            "weight_momentum": "float",
            "weight_quality": "float",
            "weight_volatility": "float",
            "min_earnings_growth": "float",
            "min_revenue_growth": "float",
            "stop_loss_pct": "float",
        },
    },
}

PERTURBATIONS = [-0.20, -0.10, 0.0, 0.10, 0.20]

# 稳定性阈值
SHARPE_AVG_THRESHOLD = 0.3
SHARPE_STD_THRESHOLD = 0.5


# ============================================================================
# 工具函数
# ============================================================================


def perturb_value(base_val: float, delta: float, param_type: str) -> Any:
    """对参数值做扰动"""
    new_val = base_val * (1 + delta)
    if param_type == "int":
        new_val = max(1, round(new_val))
    else:
        new_val = round(new_val, 4)
    return new_val


def run_single_backtest(
    config_path: str,
    param_overrides: dict[str, Any],
    synthetic: bool = False,
) -> dict:
    """运行单次回测并返回指标

    通过 run_from_config 驱动，自动处理数据加载与引擎组装。
    """
    import tempfile
    import yaml
    from aipha.backtest.config_runner import run_from_config

    # 加载基础配置
    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    # 应用参数覆盖
    strategy_params = cfg.get("strategy", {}).get("parameters", {})
    if not strategy_params:
        strategy_params = cfg.get("strategy", {}).get("params", {})
    strategy_params.update(param_overrides)

    if "parameters" in cfg.get("strategy", {}):
        cfg["strategy"]["parameters"] = strategy_params
    else:
        cfg["strategy"]["params"] = strategy_params

    # 合成数据模式
    if synthetic:
        cfg["data"]["provider"] = "synthetic"
        cfg["data"]["source"] = "synthetic"
        cfg["run"]["start_date"] = "2023-01-01"
        cfg["run"]["end_date"] = "2023-12-31"

    # 写入临时配置
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yml", delete=False, dir=".",
    ) as tmp:
        yaml.dump(cfg, tmp, allow_unicode=True)
        tmp_path = tmp.name

    try:
        result = run_from_config(tmp_path, output_dir=tempfile.mkdtemp())

        # 提取指标
        metrics = compute_metrics_from_result(result.result)
        return metrics
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def compute_metrics_from_result(result) -> dict:
    """从 BacktestResult 计算核心指标"""
    initial = result.initial_capital
    final = result.final_nav
    total_return = (final - initial) / initial
    snapshots = result.daily_snapshots
    n_days = len(snapshots)
    n_years = n_days / 252.0

    annual_return = (
        (1 + total_return) ** (1 / n_years) - 1
        if n_years > 0 and total_return > -1
        else total_return
    )

    peak = initial
    max_dd = 0.0
    for snap in snapshots:
        if snap.nav > peak:
            peak = snap.nav
        dd = (peak - snap.nav) / peak if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd

    returns = [s.daily_return for s in snapshots]
    if len(returns) > 1:
        avg_ret = sum(returns) / len(returns)
        std_ret = (sum((r - avg_ret) ** 2 for r in returns) / (len(returns) - 1)) ** 0.5  # ddof=1 §13.2
        sharpe = (
            (avg_ret * 252 - 0.02) / (std_ret * 252**0.5)
            if std_ret > 0
            else 0
        )
    else:
        sharpe = 0.0

    calmar = annual_return / max_dd if max_dd > 0 else 0.0

    return {
        "annual_return_pct": round(annual_return * 100, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 3),
        "calmar": round(calmar, 3),
        "n_trades": result.total_trades,
    }


def extract_base_params(config_path: str, perturb_keys: dict) -> dict:
    """从配置文件提取基准参数值"""
    import yaml

    with open(config_path) as f:
        cfg = yaml.safe_load(f)

    params = cfg.get("strategy", {}).get("parameters", {})
    if not params:
        params = cfg.get("strategy", {}).get("params", {})

    base = {}
    for key in perturb_keys:
        if key in params:
            base[key] = params[key]
        else:
            print(f"  警告: 参数 {key} 不在配置中，跳过")
    return base


def run_stability_analysis(
    strategy_name: str,
    synthetic: bool = False,
) -> dict:
    """对单个策略进行参数稳定性分析"""
    spec = STRATEGY_PARAMS[strategy_name]
    config_path = spec["base_config"]
    perturb_keys = spec["perturb_keys"]

    base_params = extract_base_params(config_path, perturb_keys)

    print(f"\n{'=' * 78}")
    print(f"A 股参数稳定性分析: {strategy_name}")
    print(f"{'=' * 78}")
    print(f"基准参数: {base_params}")
    print(f"数据模式: {'合成' if synthetic else '真实(TuShare)'}")

    all_results: list[dict] = []

    for param_key, param_type in perturb_keys.items():
        if param_key not in base_params:
            continue

        base_val = base_params[param_key]
        print(f"\n  扰动 {param_key} (基准={base_val}):")

        for delta in PERTURBATIONS:
            new_val = perturb_value(base_val, delta, param_type)
            overrides = {param_key: new_val}

            t0 = time.time()
            try:
                metrics = run_single_backtest(
                    config_path, overrides, synthetic=synthetic,
                )
                elapsed = time.time() - t0
                marker = " *" if delta == 0 else ""
                print(
                    f"    {param_key}={new_val:>10} (Δ={delta:>+5.0%})  "
                    f"年化={metrics['annual_return_pct']:>7.2f}%  "
                    f"Sharpe={metrics['sharpe']:>6.3f}  "
                    f"DD={metrics['max_drawdown_pct']:>6.2f}%  "
                    f"({elapsed:.1f}s){marker}"
                )
                all_results.append({
                    "param": param_key,
                    "delta": delta,
                    "value": new_val,
                    **metrics,
                })
            except Exception as e:
                elapsed = time.time() - t0
                print(
                    f"    {param_key}={new_val} (Δ={delta:>+5.0%})  "
                    f"ERROR: {e} ({elapsed:.1f}s)"
                )
                all_results.append({
                    "param": param_key,
                    "delta": delta,
                    "value": new_val,
                    "annual_return_pct": None,
                    "sharpe": None,
                    "error": str(e),
                })

    # 汇总
    summary = compute_stability_summary(strategy_name, all_results, perturb_keys)
    return {"summary": summary, "details": all_results}


def compute_stability_summary(
    strategy_name: str,
    all_results: list[dict],
    perturb_keys: dict,
) -> dict:
    """计算稳定性汇总"""
    valid = [r for r in all_results if r.get("sharpe") is not None]
    sharpes = [r["sharpe"] for r in valid]
    returns = [r["annual_return_pct"] for r in valid]

    if sharpes:
        avg_sharpe = sum(sharpes) / len(sharpes)
        std_sharpe = (
            sum((s - avg_sharpe) ** 2 for s in sharpes) / (len(sharpes) - 1)  # ddof=1 §13.2
        ) ** 0.5
        min_sharpe = min(sharpes)
        max_sharpe = max(sharpes)
        avg_return = sum(returns) / len(returns)
        all_positive = all(r > 0 for r in returns)
    else:
        avg_sharpe = std_sharpe = min_sharpe = max_sharpe = avg_return = 0
        all_positive = False

    # 逐参数统计
    per_param: dict = {}
    print(f"\n  {'=' * 74}")
    print(f"  逐参数邻域 Sharpe 均值:")
    for param_key in perturb_keys:
        param_sharpes = [
            r["sharpe"] for r in valid if r["param"] == param_key
        ]
        if param_sharpes:
            pavg = sum(param_sharpes) / len(param_sharpes)
            pstd = (
                sum((s - pavg) ** 2 for s in param_sharpes) / len(param_sharpes)
            ) ** 0.5
            pmin = min(param_sharpes)
            per_param[param_key] = {
                "avg": round(pavg, 3),
                "std": round(pstd, 3),
                "min": round(pmin, 3),
            }
            print(
                f"    {param_key:25s}  avg={pavg:.3f}  "
                f"std={pstd:.3f}  min={pmin:.3f}"
            )

    stable = (
        avg_sharpe >= SHARPE_AVG_THRESHOLD
        and std_sharpe < SHARPE_STD_THRESHOLD
        and all_positive
    )
    verdict = "STABLE" if stable else "UNSTABLE"

    print(
        f"\n  汇总: Sharpe avg={avg_sharpe:.3f} std={std_sharpe:.3f} "
        f"min={min_sharpe:.3f} max={max_sharpe:.3f}"
    )
    print(f"  全部年化>0: {'YES' if all_positive else 'NO'}")
    print(f"  判定: [{verdict}]")

    return {
        "strategy": strategy_name,
        "n_tests": len(valid),
        "avg_sharpe": round(avg_sharpe, 3),
        "std_sharpe": round(std_sharpe, 3),
        "min_sharpe": round(min_sharpe, 3),
        "max_sharpe": round(max_sharpe, 3),
        "avg_return_pct": round(avg_return, 2),
        "all_positive_return": all_positive,
        "per_param": per_param,
        "verdict": verdict,
    }


# ============================================================================
# 滑点敏感性分析
# ============================================================================


def run_slippage_sensitivity(
    strategy_name: str,
    bps_levels: list[float] | None = None,
    synthetic: bool = False,
) -> dict:
    """对策略做多级滑点敏感性分析

    Args:
        strategy_name: 策略名称
        bps_levels: 滑点水平列表（bps）
        synthetic: 是否使用合成数据

    Returns:
        结果字典
    """
    import tempfile
    import yaml
    from aipha.backtest.config_runner import run_from_config

    if bps_levels is None:
        bps_levels = [0, 3, 5, 10, 15, 20, 30]

    spec = STRATEGY_PARAMS[strategy_name]
    config_path = spec["base_config"]

    print(f"\n{'=' * 78}")
    print(f"滑点敏感性分析: {strategy_name}")
    print(f"{'=' * 78}")
    print(f"滑点水平(bps): {bps_levels}")

    results = []
    for bps in bps_levels:
        with open(config_path) as f:
            cfg = yaml.safe_load(f)

        # 修改滑点
        if "slippage_model" in cfg:
            cfg["slippage_model"]["bps"] = bps
        elif "slippage" in cfg:
            cfg["slippage"]["bps"] = bps
        elif "cost" in cfg:
            cfg["cost"]["slippage_bps"] = bps

        if synthetic:
            cfg["data"]["provider"] = "synthetic"
            cfg["data"]["source"] = "synthetic"
            cfg["run"]["start_date"] = "2023-01-01"
            cfg["run"]["end_date"] = "2023-12-31"

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yml", delete=False, dir=".",
        ) as tmp:
            yaml.dump(cfg, tmp, allow_unicode=True)
            tmp_path = tmp.name

        t0 = time.time()
        try:
            result = run_from_config(tmp_path, output_dir=tempfile.mkdtemp())
            metrics = compute_metrics_from_result(result.result)
            elapsed = time.time() - t0
            print(
                f"  {bps:>3.0f} bps  "
                f"年化={metrics['annual_return_pct']:>7.2f}%  "
                f"Sharpe={metrics['sharpe']:>6.3f}  "
                f"DD={metrics['max_drawdown_pct']:>6.2f}%  "
                f"({elapsed:.1f}s)"
            )
            results.append({"bps": bps, **metrics})
        except Exception as e:
            print(f"  {bps:>3.0f} bps  ERROR: {e}")
            results.append({"bps": bps, "error": str(e)})
        finally:
            Path(tmp_path).unlink(missing_ok=True)

    return {"strategy": strategy_name, "slippage_results": results}


# ============================================================================
# 主函数
# ============================================================================


def main():
    parser = argparse.ArgumentParser(
        description="A 股权益策略参数稳定性分析",
    )
    parser.add_argument(
        "--strategy", "-s",
        choices=list(STRATEGY_PARAMS.keys()),
        help="分析指定策略",
    )
    parser.add_argument(
        "--all", "-a",
        action="store_true",
        help="分析所有 A 股策略",
    )
    parser.add_argument(
        "--slippage",
        action="store_true",
        help="运行滑点敏感性分析",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="使用合成数据",
    )
    parser.add_argument(
        "--output", "-o",
        default="results/equity_param_stability.json",
        help="输出文件路径",
    )

    args = parser.parse_args()

    strategies = (
        list(STRATEGY_PARAMS.keys()) if args.all
        else [args.strategy] if args.strategy
        else []
    )

    if not strategies:
        parser.print_help()
        sys.exit(1)

    results = {}

    # 参数稳定性
    for name in strategies:
        results[name] = run_stability_analysis(
            name, synthetic=args.synthetic,
        )

    # 滑点敏感性（可选）
    if args.slippage:
        for name in strategies:
            slip_result = run_slippage_sensitivity(
                name, synthetic=args.synthetic,
            )
            results[f"{name}_slippage"] = slip_result

    # 总结
    print(f"\n{'=' * 78}")
    print("A 股参数稳定性总结")
    print(f"{'=' * 78}")
    for name in strategies:
        if name in results and "summary" in results[name]:
            s = results[name]["summary"]
            print(
                f"  {name:25s}  [{s['verdict']:8s}]  "
                f"Sharpe avg={s['avg_sharpe']:.3f} "
                f"std={s['std_sharpe']:.3f}  "
                f"全正={'Y' if s['all_positive_return'] else 'N'}"
            )

    # 保存
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n结果已保存: {output_path}")

    all_stable = all(
        results.get(n, {}).get("summary", {}).get("verdict") == "STABLE"
        for n in strategies
    )
    return results, all_stable


if __name__ == "__main__":
    results, all_stable = main()
    sys.exit(0 if all_stable else 1)
