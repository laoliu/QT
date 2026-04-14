#!/usr/bin/env python
"""
验证回测脚本：运行关键策略回测，对比工程调整前后的结果。
用于确认工程重构（目录整理、代码移动等）未影响回测结果。
"""
import json
import sys
import traceback
from pathlib import Path

# 确保项目根目录在 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from aipha.backtest.config_runner import run_from_config


# ---- 待验证的策略配置列表 ----
VERIFY_CONFIGS = [
    {
        "name": "triple_low_v3_8y",
        "config": "configs/cb_strategies/triple_low_v3_8y.yml",
        "output": "output/verify_triple_low_v3_8y",
    },
    {
        "name": "double_low_pct_v2_8y",
        "config": "configs/cb_strategies/double_low_pct_v2_8y.yml",
        "output": "output/verify_double_low_pct_v2_8y",
    },
    {
        "name": "cb_stock_quality_v4b_balanced_8y",
        "config": "configs/cb_strategies/cb_stock_quality_v4b_balanced_8y.yml",
        "output": "output/verify_cb_stock_quality_v4b_balanced_8y",
    },
    {
        "name": "cb_adaptive_factor_8y",
        "config": "configs/backtest/cb_adaptive_factor_8y.yml",
        "output": "output/verify_cb_adaptive_factor_8y",
    },
]

METRIC_KEYS = [
    "annualized_return",
    "sharpe_ratio",
    "max_drawdown_pct",
    "calmar_ratio",
    "total_trades",
    "win_rate",
]


def run_single(entry):
    """运行单个策略并返回指标字典。"""
    name = entry["name"]
    config_path = str(ROOT / entry["config"])
    output_dir = str(ROOT / entry["output"])
    print(f"\n{'='*60}")
    print(f"[START] {name}")
    print(f"  config: {config_path}")
    print(f"  output: {output_dir}")
    print(f"{'='*60}")
    try:
        result = run_from_config(config_path, output_dir=output_dir)
        m = result.result.metrics
        metrics = {k: m.get(k) for k in METRIC_KEYS}
        print(f"[OK] {name}")
        for k, v in metrics.items():
            if isinstance(v, float):
                print(f"  {k}: {v:.6f}")
            else:
                print(f"  {k}: {v}")
        return {"status": "OK", "metrics": metrics}
    except Exception as e:
        print(f"[FAIL] {name}: {e}")
        traceback.print_exc()
        return {"status": "FAIL", "error": str(e)}


def main():
    results = {}
    for entry in VERIFY_CONFIGS:
        results[entry["name"]] = run_single(entry)

    # ---- 汇总输出 ----
    print(f"\n\n{'='*70}")
    print("验证结果汇总")
    print(f"{'='*70}")
    for name, r in results.items():
        if r["status"] == "OK":
            m = r["metrics"]
            ar = m.get("annualized_return", 0) or 0
            sr = m.get("sharpe_ratio", 0) or 0
            mdd = m.get("max_drawdown_pct", 0) or 0
            print(f"{name:45s} AR={ar:8.4f}  SR={sr:7.4f}  MDD={mdd:7.4f}")
        else:
            print(f"{name:45s} FAILED: {r['error'][:60]}")

    # 保存 JSON
    out_path = ROOT / "output" / "verify_summary.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n汇总已保存到 {out_path}")


if __name__ == "__main__":
    main()
