#!/usr/bin/env python
"""P1-3: Walk-Forward 增量训练 vs 全量训练对比

对比两种训练模式在同一数据上的 IC/ICIR 差异，
验证增量训练（warm-start）是否保持预测质量。

用法:
    # 快速验证（合成数据）
    python scripts/compare_incremental_vs_full.py --synthetic

    # 真实数据对比（需 1-3 小时）
    python scripts/compare_incremental_vs_full.py \
        --universe csi2000 --start 2017-01-03 --end 2026-02-28
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

_project_root = Path(__file__).resolve().parent.parent
_src = _project_root / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("compare_incremental")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="增量 vs 全量 WF 训练对比")
    p.add_argument("--synthetic", action="store_true",
                   help="使用合成数据快速验证")
    p.add_argument("--universe", default="csi2000")
    p.add_argument("--start", default="2017-01-03")
    p.add_argument("--end", default="2026-02-28")
    p.add_argument("--n-estimators", type=int, default=300)
    p.add_argument("--incr-n-estimators", type=int, default=100)
    p.add_argument("--output", default=None)
    return p.parse_args()


def make_synthetic_panel(
    n_symbols: int = 80,
    n_days: int = 900,
    n_features: int = 20,
    seed: int = 42,
) -> tuple[pd.DataFrame, list[str]]:
    """生成带有弱信号的合成面板数据。"""
    rng = np.random.RandomState(seed)
    dates = pd.bdate_range("2019-01-01", periods=n_days, freq="B")
    feat_names = [f"feat_{i}" for i in range(n_features)]
    rows = []
    for sym_idx in range(n_symbols):
        symbol = f"SH{600000 + sym_idx:06d}"
        price = 10.0 + rng.randn() * 3
        for dt in dates:
            feats = rng.randn(n_features)
            # 弱信号: close 与前几个特征微弱相关
            signal = 0.005 * feats[:3].mean()
            price *= 1 + rng.randn() * 0.02 + signal
            row = {
                "datetime": dt,
                "symbol": symbol,
                "close": round(max(price, 0.5), 2),
            }
            for j, fn in enumerate(feat_names):
                row[fn] = feats[j]
            rows.append(row)
    return pd.DataFrame(rows), feat_names


def compute_fold_ic(oos_df: pd.DataFrame) -> dict:
    """从 OOS 预测计算 IC 统计。"""
    if "fwd_return" not in oos_df.columns:
        # 需要原始 label 列
        return {"mean_ic": float("nan"), "ic_ir": float("nan"), "n_dates": 0}
    ic_by_date = oos_df.groupby("datetime").apply(
        lambda g: g["pred_score"].corr(g["fwd_return"]),
        include_groups=False,
    )
    ic_by_date = ic_by_date.dropna()
    mean_ic = ic_by_date.mean()
    std_ic = ic_by_date.std()
    ic_ir = mean_ic / std_ic if std_ic > 0 else 0.0
    return {
        "mean_ic": float(mean_ic),
        "std_ic": float(std_ic),
        "ic_ir": float(ic_ir),
        "n_dates": len(ic_by_date),
    }


def run_comparison(
    df: pd.DataFrame,
    feature_cols: list[str],
    n_estimators: int = 300,
    incr_n_estimators: int = 100,
) -> dict:
    """运行全量 vs 增量对比。"""
    from aipha.models.ml_stock_selector import MLSelectorConfig, MLStockSelector

    base_kw = dict(
        train_years=1,
        predict_months=2,
        purge_days=5,
        forward_days=5,
        n_estimators=n_estimators,
        objective="regression",
        feature_cols=feature_cols,
    )
    # --- Full training ---
    logger.info("--- 全量训练 (enable_incremental=False) ---")
    t0 = time.time()
    sel_full = MLStockSelector(MLSelectorConfig(**base_kw))
    oos_full = sel_full.walk_forward_train(df)
    time_full = time.time() - t0
    ic_full = compute_fold_ic(oos_full)

    # --- Incremental training ---
    logger.info("--- 增量训练 (enable_incremental=True) ---")
    t0 = time.time()
    sel_incr = MLStockSelector(MLSelectorConfig(
        **base_kw,
        enable_incremental=True,
        incremental_n_estimators=incr_n_estimators,
    ))
    oos_incr = sel_incr.walk_forward_train(df)
    time_incr = time.time() - t0
    ic_incr = compute_fold_ic(oos_incr)

    return {
        "full": {**ic_full, "time_sec": time_full, "n_folds": sel_full.n_folds},
        "incremental": {**ic_incr, "time_sec": time_incr, "n_folds": sel_incr.n_folds},
        "delta_ic": ic_incr["mean_ic"] - ic_full["mean_ic"],
        "delta_icir": ic_incr["ic_ir"] - ic_full["ic_ir"],
        "speedup": time_full / time_incr if time_incr > 0 else 0,
    }


def main() -> None:
    args = parse_args()
    if args.synthetic:
        logger.info("=== 合成数据快速对比 ===")
        df, feat_cols = make_synthetic_panel()
        results = run_comparison(
            df, feat_cols,
            n_estimators=50,
            incr_n_estimators=20,
        )
    else:
        logger.info("=== 真实数据对比（需较长时间）===")
        logger.warning("真实数据模式尚未实现，请使用 --synthetic")
        return

    _print_results(results)


def _print_results(results: dict) -> None:
    """打印对比结果。"""
    logger.info("=" * 70)
    logger.info("Walk-Forward 增量 vs 全量训练对比")
    logger.info("=" * 70)
    for mode in ("full", "incremental"):
        m = results[mode]
        logger.info(
            "  [%s] IC=%.4f  ICIR=%.4f  Folds=%d  Time=%.1fs",
            mode.upper(), m["mean_ic"], m["ic_ir"],
            m["n_folds"], m["time_sec"],
        )
    logger.info("-" * 70)
    logger.info(
        "  ΔIC=%.4f  ΔICIR=%.4f  Speedup=%.2fx",
        results["delta_ic"], results["delta_icir"], results["speedup"],
    )
    # 判定
    if abs(results["delta_ic"]) < 0.02:
        logger.info("  ✓ IC 差异 < 0.02，增量训练质量可接受")
    elif results["delta_ic"] < -0.02:
        logger.warning("  ✗ IC 下降 > 0.02，增量训练可能不适合当前场景")
    else:
        logger.info("  ✓ IC 提升 > 0.02，增量训练有正向贡献")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
