"""单因子逐个添加实验 — CF-018路线

验证 turnover_stability 因子单独加入 P3 基线后的效果。
严格遵循 CF-015 三条件验证:
  1. IC 提升
  2. Portfolio Sim AR 不低于基线
  3. 风控约束触发率无显著上升

用法:
    python scripts/single_factor_addition_test.py

依赖: models/ml_csi2000_lgb_p3/ (P3基线模型)
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logger = logging.getLogger(__name__)

# P3 基线配置 — 240 特征
BASELINE_CONFIG = "configs/backtest/ml_csi2000_lt20b.yml"
BASELINE_NAME = "P3_baseline_240feat"

# 候选单因子添加列表 (按 CF-018 优先级)
CANDIDATE_FACTORS = [
    {
        "name": "turnover_stability",
        "description": "CF-018: IC=2.80% ICIR=0.786 post-2022更强",
        "ic": 0.028,
        "icir": 0.786,
    },
]


def run_baseline() -> dict:
    """运行 P3 基线回测"""
    from aipha.backtest.config_runner import run_from_config

    logger.info("运行 P3 基线 (240 特征)...")
    result = run_from_config(
        BASELINE_CONFIG,
        output_dir="output/_factor_test/p3_baseline",
    )
    rpt = result.report
    if rpt is None:
        raise RuntimeError("基线报告生成失败")
    return {
        "name": BASELINE_NAME,
        "ar": rpt.performance.annualized_return,
        "sr": rpt.performance.sharpe_ratio,
        "mdd": rpt.drawdown.max_drawdown_pct,
        "calmar": rpt.performance.calmar_ratio,
    }


def check_factor_availability(factor_name: str) -> bool:
    """检查因子是否存在于模型特征中"""
    import pandas as pd
    model_dir = Path("models/ml_csi2000_lgb_p3")
    meta_path = model_dir / "meta.pkl"
    if not meta_path.exists():
        logger.warning("meta.pkl不存在: %s", meta_path)
        return False
    import pickle
    with open(meta_path, "rb") as f:
        meta = pickle.load(f)
    features = meta.get("feature_names", [])
    found = factor_name in features
    logger.info("因子 %s 在P3特征集中: %s (共%d特征)", factor_name, found, len(features))
    return found


def main() -> None:
    """执行单因子添加实验"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=" * 70)
    print("单因子逐个添加实验 (CF-018 路线)")
    print("=" * 70)

    # Step 1: 检查因子可用性
    for factor in CANDIDATE_FACTORS:
        available = check_factor_availability(factor["name"])
        if available:
            print(f"✅ {factor['name']}: 已在P3特征集中 → 需重训模型添加")
        else:
            print(f"📦 {factor['name']}: 不在P3特征集中 → 需新增特征+重训")

    # Step 2: 运行基线
    try:
        baseline = run_baseline()
    except Exception as e:
        logger.error("基线运行失败: %s", e)
        return

    print(f"\n基线绩效:")
    print(f"  AR: {baseline['ar']:.2%}")
    print(f"  SR: {baseline['sr']:.4f}")
    print(f"  MDD: {baseline['mdd']:.2%}")
    print(f"  Calmar: {baseline['calmar']:.4f}")

    # Step 3: 评估结论
    print("\n--- 评估结论 ---")
    print("当前P3基线(240特征)是最优已验证模型。")
    print("turnover_stability IC=2.80%已通过独立IC门禁。")
    print("下一步: 需重训模型(241特征)后对比CF-015三条件。")
    print("⚠️ 重训需约2-4小时GPU时间，建议单独安排。")

    # 保存结果
    out = {"baseline": baseline, "candidates": CANDIDATE_FACTORS,
           "status": "baseline_captured", "next_step": "retrain_with_241_features"}
    out_path = Path("output/_factor_test/single_factor_plan.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, default=str)
    print(f"\n实验计划已保存: {out_path}")


if __name__ == "__main__":
    main()
