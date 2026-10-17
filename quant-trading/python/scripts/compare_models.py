"""A/B 模型比较脚本：基线 vs Mined Factors 模型.

用法:
    python scripts/compare_models.py \
        --baseline models/ml_csi2000_lgb_20d_r40 \
        --candidate models/ml_csi2000_lgb_20d_r40_mined
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="A/B Model Comparison")
    p.add_argument("--baseline", required=True)
    p.add_argument("--candidate", required=True)
    return p.parse_args()


def load_report(model_dir: str) -> dict:
    path = Path(model_dir) / "evaluation_report.json"
    if not path.exists():
        raise FileNotFoundError(f"evaluation_report.json not found in {model_dir}")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def extract_metrics(r: dict) -> dict:
    """Extract key metrics from evaluation report."""
    wf = r.get("wf_summary", {})
    oos = r.get("oos_metrics", {})
    port = r.get("portfolio_simulation", {})
    n_features = len(wf.get("top_features", {}))
    return {
        "n_folds": wf.get("n_folds"),
        "n_features": n_features,
        "oos_ic_mean": oos.get("mean_ic"),
        "oos_ic_std": oos.get("std_ic"),
        "oos_icir": oos.get("ic_ir"),
        "oos_ic_positive": oos.get("ic_positive_ratio"),
        "top_ann_ret": oos.get("top_annual_ret"),
        "long_short_ann": oos.get("long_short_annual"),
        "is_oos_decay": wf.get("is_oos_mean_decay"),
        "port_ar": port.get("annual_return"),
        "port_sr": port.get("sharpe_ratio"),
        "port_mdd": port.get("max_drawdown"),
        "port_calmar": port.get("calmar_ratio"),
        "port_winrate": port.get("win_rate"),
        "elapsed_sec": r.get("elapsed_seconds"),
    }


def compare_top_features(base_r: dict, cand_r: dict, top_n: int = 30) -> dict:
    """Compare top features between two models."""
    base_feats = base_r.get("wf_summary", {}).get("top_features", {})
    cand_feats = cand_r.get("wf_summary", {}).get("top_features", {})

    base_top = set(list(base_feats.keys())[:top_n])
    cand_top = set(list(cand_feats.keys())[:top_n])

    # Identify mined factor contribution
    mined_in_top = [f for f in list(cand_feats.keys())[:top_n] if f.startswith("mut_")]

    return {
        "overlap": len(base_top & cand_top),
        "new_in_candidate": sorted(cand_top - base_top),
        "dropped_from_baseline": sorted(base_top - cand_top),
        "mined_factors_in_top": mined_in_top,
        "mined_factors_count": len(mined_in_top),
    }


def main() -> None:
    args = parse_args()

    base_r = load_report(args.baseline)
    cand_r = load_report(args.candidate)

    base_m = extract_metrics(base_r)
    cand_m = extract_metrics(cand_r)

    feat_cmp = compare_top_features(base_r, cand_r)

    # Print comparison
    print("\n" + "=" * 75)
    print("A/B Model Comparison")
    print(f"  Baseline:  {args.baseline}")
    print(f"  Candidate: {args.candidate}")
    print("=" * 75)

    header = f"{'Metric':<30} {'Baseline':>12} {'Candidate':>12} {'Delta':>12}"
    print(f"\n{header}")
    print("-" * 75)

    for key in [
        "n_features", "n_folds",
        "oos_ic_mean", "oos_icir", "oos_ic_positive",
        "top_ann_ret", "long_short_ann", "is_oos_decay",
        "port_ar", "port_sr", "port_mdd", "port_calmar", "port_winrate",
        "elapsed_sec",
    ]:
        bv = base_m.get(key)
        cv = cand_m.get(key)
        if bv is None or cv is None:
            continue
        delta = cv - bv
        # Format based on type
        if key in ("n_features", "n_folds"):
            print(f"{key:<30} {bv:>12d} {cv:>12d} {delta:>+12d}")
        elif key == "elapsed_sec":
            print(f"{key:<30} {bv:>12.0f} {cv:>12.0f} {delta:>+12.0f}")
        elif key in ("port_ar", "port_mdd", "port_winrate", "oos_ic_positive",
                      "top_ann_ret", "long_short_ann", "is_oos_decay"):
            print(f"{key:<30} {bv:>11.1%} {cv:>11.1%} {delta:>+11.1%}")
        else:
            print(f"{key:<30} {bv:>12.4f} {cv:>12.4f} {delta:>+12.4f}")

    print(f"\n--- Feature Analysis (Top 30) ---")
    print(f"  Overlap with baseline: {feat_cmp['overlap']}/30")
    print(f"  Mined factors in top 30: {feat_cmp['mined_factors_count']}")
    if feat_cmp["mined_factors_in_top"]:
        for mf in feat_cmp["mined_factors_in_top"][:10]:
            imp = cand_r["wf_summary"]["top_features"].get(mf, 0)
            print(f"    {mf}: importance={imp:.1f}")
    if feat_cmp["new_in_candidate"]:
        print(f"  New features: {feat_cmp['new_in_candidate'][:10]}")
    if feat_cmp["dropped_from_baseline"]:
        print(f"  Dropped: {feat_cmp['dropped_from_baseline'][:10]}")

    # Verdict
    improved = (
        (cand_m.get("oos_ic_mean", 0) > base_m.get("oos_ic_mean", 0))
        and (cand_m.get("port_sr", 0) >= base_m.get("port_sr", 0) * 0.95)
    )
    print(f"\n{'=' * 75}")
    if improved:
        print("VERDICT: Candidate IMPROVES on baseline (higher IC, SR maintained)")
    else:
        ic_delta = (cand_m.get("oos_ic_mean", 0) - base_m.get("oos_ic_mean", 0))
        sr_delta = (cand_m.get("port_sr", 0) - base_m.get("port_sr", 0))
        print(f"VERDICT: Candidate {'improves' if ic_delta > 0 else 'degrades'} IC "
              f"({ic_delta:+.4f}), {'improves' if sr_delta > 0 else 'degrades'} SR "
              f"({sr_delta:+.4f})")
    print("=" * 75)


if __name__ == "__main__":
    main()
