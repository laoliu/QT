"""2025 OOS Holdout 验证脚本 (P2)

验证策略在 2025 年最新数据上的表现:
1. 使用已训练的 WF 模型产出的 oos_predictions
2. 筛选 2025 年数据窗口
3. 计算 IC/ICIR/Top-Bottom return
4. 与全量期 OOS 对比

用法:
    python scripts/oos_2025_validation.py \
        --model-dir models/ml_csi2000_lgb_20d_r40_mined \
        --holdout-start 2025-01-01 \
        --holdout-end 2025-12-31
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("oos_2025")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="2025 OOS Holdout 验证")
    p.add_argument("--model-dir", required=True)
    p.add_argument("--holdout-start", default="2025-01-01")
    p.add_argument("--holdout-end", default="2025-12-31")
    p.add_argument("--output", default=None,
                   help="输出 JSON 路径 (默认: model_dir/oos_2025_report.json)")
    return p.parse_args()


def load_oos_predictions(model_dir: str) -> pd.DataFrame:
    """加载 WF 训练产出的 oos_predictions.parquet."""
    path = Path(model_dir) / "oos_predictions.parquet"
    if not path.exists():
        raise FileNotFoundError(f"OOS predictions not found: {path}")
    df = pd.read_parquet(path)
    logger.info("Loaded OOS predictions: %d rows, columns: %s",
                len(df), list(df.columns))
    # 统一列名
    if "trade_date" in df.columns and "datetime" not in df.columns:
        df = df.rename(columns={"trade_date": "datetime"})
    if "ts_code" in df.columns and "symbol" not in df.columns:
        df = df.rename(columns={"ts_code": "symbol"})
    # 确保 datetime 是 datetime 类型
    df["datetime"] = pd.to_datetime(df["datetime"])
    # 统一预测/标签列名
    col_map = {}
    if "pred_score" in df.columns and "prediction" not in df.columns:
        col_map["pred_score"] = "prediction"
    if "fwd_return" in df.columns and "label" not in df.columns:
        col_map["fwd_return"] = "label"
    if col_map:
        df = df.rename(columns=col_map)
    return df


def compute_ic_series(df: pd.DataFrame,
                      pred_col: str = "prediction",
                      label_col: str = "label") -> pd.Series:
    """逐日横截面 IC (Spearman rank correlation)."""
    from scipy.stats import spearmanr

    def _daily_ic(g: pd.DataFrame) -> float:
        if len(g) < 10:
            return np.nan
        valid = g[[pred_col, label_col]].dropna()
        if len(valid) < 10:
            return np.nan
        corr, _ = spearmanr(valid[pred_col], valid[label_col])
        return corr

    ic = df.groupby("datetime").apply(_daily_ic, include_groups=False)
    return ic


def compute_top_bottom_return(df: pd.DataFrame,
                              pred_col: str = "prediction",
                              label_col: str = "label",
                              quantile: float = 0.1) -> dict:
    """逐日 Top/Bottom 分组收益."""

    def _daily_tb(g: pd.DataFrame) -> dict:
        valid = g[[pred_col, label_col]].dropna()
        if len(valid) < 20:
            return {"top": np.nan, "bottom": np.nan}
        q_hi = valid[pred_col].quantile(1 - quantile)
        q_lo = valid[pred_col].quantile(quantile)
        top_ret = valid.loc[valid[pred_col] >= q_hi, label_col].mean()
        bot_ret = valid.loc[valid[pred_col] <= q_lo, label_col].mean()
        return {"top": top_ret, "bottom": bot_ret}

    tb = df.groupby("datetime").apply(_daily_tb, include_groups=False).apply(pd.Series)
    return {
        "top_mean_daily": float(tb["top"].mean()),
        "bottom_mean_daily": float(tb["bottom"].mean()),
        "spread_daily": float(tb["top"].mean() - tb["bottom"].mean()),
        "top_ann_ret": float(tb["top"].mean() * 243),
        "bottom_ann_ret": float(tb["bottom"].mean() * 243),
        "spread_ann": float((tb["top"].mean() - tb["bottom"].mean()) * 243),
    }


def main() -> None:
    args = parse_args()
    model_dir = Path(args.model_dir)

    # 1. Load OOS predictions
    df = load_oos_predictions(str(model_dir))

    # 2. Load baseline evaluation report
    eval_path = model_dir / "evaluation_report.json"
    baseline_metrics = {}
    if eval_path.exists():
        with open(eval_path, "r", encoding="utf-8") as f:
            baseline_metrics = json.load(f)
        logger.info("Loaded baseline evaluation report")

    # 3. Split holdout window
    start = pd.Timestamp(args.holdout_start)
    end = pd.Timestamp(args.holdout_end)
    holdout = df[(df["datetime"] >= start) & (df["datetime"] <= end)].copy()
    pre_holdout = df[df["datetime"] < start].copy()

    logger.info("Holdout period: %s ~ %s, %d rows, %d days",
                start.date(), end.date(), len(holdout),
                holdout["datetime"].nunique())
    logger.info("Pre-holdout: %d rows, %d days",
                len(pre_holdout), pre_holdout["datetime"].nunique())

    if len(holdout) < 100:
        logger.error("Holdout data too small (%d rows). Check date range.", len(holdout))
        sys.exit(1)

    # 4. Compute metrics
    # 4a. Full OOS IC
    ic_full = compute_ic_series(df)
    ic_holdout = compute_ic_series(holdout)
    ic_pre = compute_ic_series(pre_holdout)

    # 4b. Top-Bottom
    tb_holdout = compute_top_bottom_return(holdout)
    tb_pre = compute_top_bottom_return(pre_holdout)
    tb_full = compute_top_bottom_return(df)

    # 5. Build report
    report = {
        "model_dir": str(model_dir),
        "holdout_start": str(start.date()),
        "holdout_end": str(end.date()),
        "holdout_rows": int(len(holdout)),
        "holdout_days": int(holdout["datetime"].nunique()),
        "holdout_stocks_avg": float(holdout.groupby("datetime").size().mean()),
        "metrics": {
            "full_oos": {
                "ic_mean": float(ic_full.mean()),
                "ic_std": float(ic_full.std()),
                "icir": float(ic_full.mean() / ic_full.std()) if ic_full.std() > 0 else 0,
                "ic_positive_ratio": float((ic_full > 0).mean()),
                "top_bottom": tb_full,
            },
            "pre_holdout": {
                "ic_mean": float(ic_pre.mean()),
                "ic_std": float(ic_pre.std()),
                "icir": float(ic_pre.mean() / ic_pre.std()) if ic_pre.std() > 0 else 0,
                "ic_positive_ratio": float((ic_pre > 0).mean()),
                "top_bottom": tb_pre,
            },
            "holdout_2025": {
                "ic_mean": float(ic_holdout.mean()),
                "ic_std": float(ic_holdout.std()),
                "icir": float(ic_holdout.mean() / ic_holdout.std()) if ic_holdout.std() > 0 else 0,
                "ic_positive_ratio": float((ic_holdout > 0).mean()),
                "top_bottom": tb_holdout,
            },
        },
        "decay_analysis": {
            "ic_decay_ratio": float(ic_holdout.mean() / ic_pre.mean()) if ic_pre.mean() != 0 else None,
            "spread_decay_ratio": (
                float(tb_holdout["spread_daily"] / tb_pre["spread_daily"])
                if tb_pre["spread_daily"] != 0 else None
            ),
        },
    }

    # 6. Print summary
    print("\n" + "=" * 70)
    print("2025 OOS Holdout Validation Report")
    print("=" * 70)
    for period, key in [("Full OOS", "full_oos"),
                        ("Pre-Holdout", "pre_holdout"),
                        ("2025 Holdout", "holdout_2025")]:
        m = report["metrics"][key]
        print(f"\n--- {period} ---")
        print(f"  IC mean:  {m['ic_mean']:.4f}")
        print(f"  IC std:   {m['ic_std']:.4f}")
        print(f"  ICIR:     {m['icir']:.4f}")
        print(f"  IC>0:     {m['ic_positive_ratio']:.1%}")
        print(f"  Spread(ann): {m['top_bottom']['spread_ann']:.2%}")

    decay = report["decay_analysis"]
    print(f"\n--- Decay Analysis ---")
    if decay["ic_decay_ratio"] is not None:
        print(f"  IC decay (2025/pre): {decay['ic_decay_ratio']:.2%}")
    if decay["spread_decay_ratio"] is not None:
        print(f"  Spread decay (2025/pre): {decay['spread_decay_ratio']:.2%}")
    print("=" * 70)

    # 7. Save report
    out_path = Path(args.output) if args.output else model_dir / "oos_2025_report.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    logger.info("Report saved to %s", out_path)


if __name__ == "__main__":
    main()
