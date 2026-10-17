#!/usr/bin/env python3
"""容量敏感度扫描脚本.

对 V3.1 各组件在不同参与率下评估容量，
找到容量瓶颈并生成报告。

用法:
    python scripts/capacity_sweep.py [--output output/capacity_sweep.json]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[1]
if str(_PROJ) not in sys.path:
    sys.path.insert(0, str(_PROJ))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
logger = logging.getLogger("capacity_sweep")

# V3.1 组件定义
V31_COMPONENTS = {
    "CSI2000": {
        "weight": 0.35,
        "avg_daily_volume_cny": 50_000_000,  # 保守估计: 50M 平均日成交额
        "top_k": 30,
        "description": "CSI2000 微盘 ML",
    },
    "CB": {
        "weight": 0.15,
        "avg_daily_volume_cny": 10_000_000,  # CB 流动性更低
        "top_k": 20,
        "description": "可转债质量因子",
    },
    "CSI300": {
        "weight": 0.15,
        "avg_daily_volume_cny": 500_000_000,  # 大盘股流动性好
        "top_k": 15,
        "description": "CSI300≥200B ML",
    },
    "ETF": {
        "weight": 0.35,
        "avg_daily_volume_cny": 1_000_000_000,  # ETF 高流动性
        "top_k": 5,
        "description": "ETF 轮动",
    },
}

PARTICIPATION_RATES = [0.02, 0.05, 0.10, 0.15, 0.20]


def estimate_component_capacity(
    comp: dict, participation_rate: float
) -> dict:
    """估算单组件在给定参与率下的容量."""
    per_stock = comp["avg_daily_volume_cny"] * participation_rate
    total = per_stock * comp["top_k"]
    return {
        "per_stock_capacity": per_stock,
        "total_capacity": total,
        "participation_rate": participation_rate,
    }


def sweep() -> list[dict]:
    """扫描所有组件在不同参与率下的容量."""
    results = []
    for rate in PARTICIPATION_RATES:
        row = {"participation_rate": rate, "components": {}}
        portfolio_capacity = float("inf")
        for name, comp in V31_COMPONENTS.items():
            est = estimate_component_capacity(comp, rate)
            # 组件的可支撑资金 = 容量 / 权重
            if comp["weight"] > 0:
                est["portfolio_max_aum"] = est["total_capacity"] / comp["weight"]
            else:
                est["portfolio_max_aum"] = float("inf")
            row["components"][name] = est
            portfolio_capacity = min(portfolio_capacity, est["portfolio_max_aum"])
        row["portfolio_max_aum"] = portfolio_capacity
        row["bottleneck"] = min(
            row["components"],
            key=lambda k: row["components"][k]["portfolio_max_aum"],
        )
        results.append(row)
    return results


def print_report(results: list[dict]) -> None:
    """打印容量扫描报告."""
    print("\n=== V3.1 容量敏感度扫描 ===\n")
    print(f"{'参与率':>8} {'组合最大AUM':>15} {'瓶颈':>10}")
    print("-" * 40)
    for r in results:
        aum = r["portfolio_max_aum"]
        aum_str = f"{aum/1e6:.1f}M" if aum < 1e9 else f"{aum/1e9:.2f}B"
        print(f"{r['participation_rate']:>8.0%} {aum_str:>15} {r['bottleneck']:>10}")

    print("\n--- 各组件详情 (参与率=10%) ---")
    target = [r for r in results if r["participation_rate"] == 0.10]
    if target:
        for name, est in target[0]["components"].items():
            cap = est["total_capacity"]
            cap_str = f"{cap/1e6:.1f}M" if cap < 1e9 else f"{cap/1e9:.2f}B"
            print(f"  {name:>10}: 容量={cap_str}, "
                  f"单票={est['per_stock_capacity']/1e6:.1f}M, "
                  f"可支撑AUM={est['portfolio_max_aum']/1e6:.0f}M")


def main() -> None:
    parser = argparse.ArgumentParser(description="容量敏感度扫描")
    parser.add_argument("--output", default="output/capacity_sweep.json")
    args = parser.parse_args()

    results = sweep()
    print_report(results)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    logger.info("结果已保存至 %s", out_path)


if __name__ == "__main__":
    main()
