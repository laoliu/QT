#!/usr/bin/env python3
"""多资金量级回测扫描.

用实际回测引擎对 V3.1 各组件在不同资金量下运行，
收集真实 AR/SR/MDD 数据，量化资金量对策略绩效的影响。

用法:
    python scripts/multi_capital_backtest.py
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[1]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

from aipha.utils.paths import project_path

logging.basicConfig(
    level=logging.WARNING,
    stream=sys.stdout,
    format="%(asctime)s %(levelname)s %(message)s",
    force=True,
    encoding="utf-8",
)
logger = logging.getLogger("multi_capital")
logger.setLevel(logging.INFO)

from aipha.backtest.config_runner import run_from_config  # noqa: E402

# V3.1 各组件配置文件 + 权重
COMPONENTS = {
    "CSI2000": {
        "config": "configs/backtest/ml_csi2000_ensemble_v3b_master_v2.yml",
        "weight": 0.35,
    },
    "CB": {
        "config": "configs/cb_strategies/cb_stock_quality_v4b_balanced_8y.yml",
        "weight": 0.15,
    },
    "CSI300": {
        "config": "configs/backtest/ml_csi300_ge200b_ensemble_v1.yml",
        "weight": 0.15,
    },
    "ETF": {
        "config": "configs/backtest/etf_cross_v5_regime_11y.yml",
        "weight": 0.35,
    },
}

# 组合总资金量级 (元)
TOTAL_AUM_LEVELS = [
    200_000,     # 20万
    500_000,     # 50万
    1_000_000,   # 100万
    2_000_000,   # 200万
    3_000_000,   # 300万
    5_000_000,   # 500万
    10_000_000,  # 1000万
]


def run_component_at_capital(
    comp_name: str, config_path: str, capital: float
) -> dict | None:
    """对单个组件在指定资金量下运行回测."""
    cap_tag = f"{capital/10000:.0f}w"
    out_dir = str(project_path("output", "_cap_sweep", f"{comp_name}_{cap_tag}"))
    summary_path = Path(out_dir) / "summary.json"

    # 已有结果时直接读取
    if summary_path.exists():
        logger.info(f"  [{comp_name}] capital={capital:,.0f} -> CACHED")
        with open(summary_path) as f:
            data = json.load(f)
        metrics = data.get("metrics", data)
        return {
            "component": comp_name,
            "capital": capital,
            "ar": metrics.get("annualized_return", metrics.get("annual_return", 0)),
            "sr": metrics.get("sharpe_ratio", 0),
            "mdd": metrics.get("max_drawdown_pct", metrics.get("max_drawdown", 0)),
            "calmar": metrics.get("calmar_ratio", 0),
            "turnover": metrics.get("turnover_rate", 0),
            "total_trades": metrics.get("total_trades", 0),
            "avg_positions": metrics.get("avg_positions", 0),
            "final_nav": data.get("final_nav", 0),
        }

    logger.info(f"  [{comp_name}] capital={capital:,.0f} -> {out_dir}")
    try:
        result = run_from_config(
            config_path,
            output_dir=out_dir,
            param_overrides={"initial_capital": capital},
        )
        # 读取 summary
        summary_path = Path(out_dir) / "summary.json"
        if summary_path.exists():
            with open(summary_path) as f:
                data = json.load(f)
            metrics = data.get("metrics", data)
            return {
                "component": comp_name,
                "capital": capital,
                "ar": metrics.get("annualized_return", metrics.get("annual_return", 0)),
                "sr": metrics.get("sharpe_ratio", 0),
                "mdd": metrics.get("max_drawdown_pct", metrics.get("max_drawdown", 0)),
                "calmar": metrics.get("calmar_ratio", 0),
                "turnover": metrics.get("turnover_rate", 0),
                "total_trades": metrics.get("total_trades", 0),
                "avg_positions": metrics.get("avg_positions", 0),
                "final_nav": data.get("final_nav", 0),
            }
    except Exception as e:
        logger.error(f"  [{comp_name}] ERROR: {e}")
    return None


def run_all() -> list[dict]:
    """扫描所有资金量级."""
    all_results = []
    total_runs = len(TOTAL_AUM_LEVELS) * len(COMPONENTS)
    done = 0

    for aum in TOTAL_AUM_LEVELS:
        aum_str = f"{aum/10000:.0f}万"
        logger.info(f"\n{'='*60}")
        logger.info(f"总AUM = {aum_str} ({aum:,.0f}元)")
        logger.info(f"{'='*60}")

        for comp_name, comp_info in COMPONENTS.items():
            comp_capital = aum * comp_info["weight"]
            t0 = time.time()
            result = run_component_at_capital(
                comp_name, comp_info["config"], comp_capital
            )
            elapsed = time.time() - t0
            done += 1
            if result:
                result["total_aum"] = aum
                all_results.append(result)
                logger.info(
                    f"  [{comp_name}] AR={result['ar']:.2%} SR={result['sr']:.3f} "
                    f"MDD={result['mdd']:.2%} ({elapsed:.0f}s) "
                    f"[{done}/{total_runs}]"
                )
            else:
                logger.warning(f"  [{comp_name}] FAILED ({elapsed:.0f}s)")

    return all_results


def print_summary(results: list[dict]) -> None:
    """打印汇总表."""
    # 按 AUM 分组
    from collections import defaultdict
    by_aum: dict[float, dict] = defaultdict(dict)
    for r in results:
        by_aum[r["total_aum"]][r["component"]] = r

    # ── 各组件 AR 表 ──
    print("\n" + "=" * 90)
    print("V3.1 各组件在不同资金量下的实际回测年化收益 (AR)")
    print("=" * 90)
    comp_names = list(COMPONENTS.keys())
    header = f"{'总AUM':>10} |"
    for c in comp_names:
        w = COMPONENTS[c]["weight"]
        header += f" {c}({w:.0%}):>12 |"
    header += f" {'加权组合AR':>12}"
    print(header)
    print("-" * 90)

    for aum in sorted(by_aum.keys()):
        aum_s = f"{aum/10000:.0f}万"
        row = f"{aum_s:>10} |"
        combo_ar = 0.0
        all_ok = True
        for c in comp_names:
            if c in by_aum[aum]:
                ar = by_aum[aum][c]["ar"]
                row += f" {ar:>11.2%} |"
                combo_ar += ar * COMPONENTS[c]["weight"]
            else:
                row += f" {'N/A':>11} |"
                all_ok = False
        if all_ok:
            row += f" {combo_ar:>11.2%}"
        else:
            row += f" {'N/A':>11}"
        print(row)

    # ── 各组件 SR 表 ──
    print("\n" + "-" * 90)
    print("Sharpe Ratio")
    print("-" * 90)
    for aum in sorted(by_aum.keys()):
        aum_s = f"{aum/10000:.0f}万"
        row = f"{aum_s:>10} |"
        for c in comp_names:
            if c in by_aum[aum]:
                sr = by_aum[aum][c]["sr"]
                row += f" {sr:>11.3f} |"
            else:
                row += f" {'N/A':>11} |"
        print(row)

    # ── 各组件 MDD 表 ──
    print("\n" + "-" * 90)
    print("Max Drawdown")
    print("-" * 90)
    for aum in sorted(by_aum.keys()):
        aum_s = f"{aum/10000:.0f}万"
        row = f"{aum_s:>10} |"
        for c in comp_names:
            if c in by_aum[aum]:
                mdd = by_aum[aum][c]["mdd"]
                row += f" {mdd:>11.2%} |"
            else:
                row += f" {'N/A':>11} |"
        print(row)

    # ── 持仓数表 ──
    print("\n" + "-" * 90)
    print("平均持仓数")
    print("-" * 90)
    for aum in sorted(by_aum.keys()):
        aum_s = f"{aum/10000:.0f}万"
        row = f"{aum_s:>10} |"
        for c in comp_names:
            if c in by_aum[aum]:
                pos = by_aum[aum][c].get("avg_positions")
                if pos is not None:
                    row += f" {pos:>11.1f} |"
                else:
                    row += f" {'N/A':>11} |"
            else:
                row += f" {'N/A':>11} |"
        print(row)

    # ── 综合分析 ──
    print("\n" + "=" * 90)
    print("综合分析: 加权组合 AR vs 资金量")
    print("=" * 90)
    print(f"{'总AUM':>10} | {'组合AR':>10} | {'vs 1000万':>10} | {'AR衰减':>10} | 评级")
    print("-" * 65)
    baseline_ar = None
    for aum in sorted(by_aum.keys()):
        aum_s = f"{aum/10000:.0f}万"
        combo_ar = 0.0
        all_ok = True
        for c in comp_names:
            if c in by_aum[aum]:
                combo_ar += by_aum[aum][c]["ar"] * COMPONENTS[c]["weight"]
            else:
                all_ok = False
        if not all_ok:
            continue
        if aum == 10_000_000:
            baseline_ar = combo_ar
        decay = (combo_ar - baseline_ar) if baseline_ar else 0
        decay_s = f"{decay:+.2%}" if baseline_ar else "基准"
        grade = (
            "A" if combo_ar >= 0.18 else
            "B" if combo_ar >= 0.14 else
            "C" if combo_ar >= 0.10 else
            "D" if combo_ar >= 0.05 else "F"
        )
        print(f"{aum_s:>10} | {combo_ar:>10.2%} | {decay_s:>10} | "
              f"{abs(decay) if baseline_ar else 0:>10.2%} | {grade}")

    # 保存 JSON
    out_path = project_path("output/_cap_sweep/summary.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\n结果已保存: {out_path}")


def main() -> None:
    results = run_all()
    if results:
        print_summary(results)
    else:
        logger.error("无回测结果!")


if __name__ == "__main__":
    main()
