"""R133 Phase 1: T1-C 市值分层分析 — P3 预测按市值桶分析。

不需要重新训练，直接分析 P3 OOS 预测在不同市值区间的选股质量。
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_circ_mv_from_cache() -> pd.DataFrame:
    """Load circ_mv from daily_basic cache files."""
    cache_dir = ROOT / "data" / "cache" / "equity_tushare"
    parts = []
    for f in sorted(cache_dir.glob("daily_basic_*.parquet")):
        try:
            df = pd.read_parquet(f, columns=["trade_date", "ts_code", "circ_mv"])
            parts.append(df)
        except Exception:
            continue
    if not parts:
        raise FileNotFoundError("No daily_basic files found")
    mv = pd.concat(parts, ignore_index=True)
    mv["datetime"] = pd.to_datetime(mv["trade_date"], format="%Y%m%d")
    mv.rename(columns={"ts_code": "symbol"}, inplace=True)
    # circ_mv unit is 万元, convert to 亿元
    mv["circ_mv_yi"] = mv["circ_mv"] / 10000.0
    return mv[["datetime", "symbol", "circ_mv_yi"]]


def analyze_tier(df: pd.DataFrame, name: str, top_k: int = 50) -> dict:
    """Analyze prediction quality for a market-cap tier."""
    n_symbols = df["symbol"].nunique()
    # Per-date IC (rank correlation between pred_score and fwd_return)
    ic_list = []
    top_rets = []
    bot_rets = []
    dates = sorted(df["datetime"].unique())
    for dt in dates:
        day = df[df["datetime"] == dt]
        if len(day) < max(top_k * 2, 10):
            continue
        ic = day["pred_score"].corr(day["fwd_return"], method="spearman")
        if not np.isnan(ic):
            ic_list.append(ic)
        ranked = day.sort_values("pred_score", ascending=False)
        k = min(top_k, len(ranked) // 2)
        if k > 0:
            top_rets.append(ranked.head(k)["fwd_return"].mean())
            bot_rets.append(ranked.tail(k)["fwd_return"].mean())

    mean_ic = np.mean(ic_list) if ic_list else 0.0
    ic_pos_ratio = sum(1 for x in ic_list if x > 0) / max(len(ic_list), 1)

    # Annualize: fwd_return is 5-day return, ~50 periods/year
    periods_per_year = 250 / 5  # 50
    top_mean_per_period = np.mean(top_rets) if top_rets else 0.0
    bot_mean_per_period = np.mean(bot_rets) if bot_rets else 0.0
    top50_ar = (1 + top_mean_per_period) ** periods_per_year - 1
    bot50_ar = (1 + bot_mean_per_period) ** periods_per_year - 1
    ls_ar = top50_ar - bot50_ar

    return {
        "n_symbols": n_symbols,
        "n_dates": len(dates),
        "n_ic_dates": len(ic_list),
        "mean_ic": mean_ic,
        "ic_pos_ratio": ic_pos_ratio,
        "top50_ar": top50_ar,
        "bot50_ar": bot50_ar,
        "ls_ar": ls_ar,
        "top_mean_period": top_mean_per_period,
        "bot_mean_period": bot_mean_per_period,
    }


def main() -> None:
    """T1-C main analysis."""
    import sys
    # Accept --predictions <path> CLI arg (default: P3)
    pred_path = ROOT / "models/ml_csi2000_lgb_p3/oos_predictions.parquet"
    top_k = 50
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--predictions" and i < len(sys.argv):
            pred_path = Path(sys.argv[i + 1])
        if arg == "--top-k" and i < len(sys.argv):
            top_k = int(sys.argv[i + 1])

    # 1) Load OOS predictions
    oos = pd.read_parquet(pred_path)
    print(f"OOS: {len(oos):,} rows, {oos['symbol'].nunique()} symbols  [{pred_path.name}]")
    print(f"Date range: {oos['datetime'].min().date()} ~ {oos['datetime'].max().date()}")

    # 2) Load circ_mv
    print("\nLoading circ_mv from cache...")
    mv = load_circ_mv_from_cache()
    print(f"MV data: {len(mv):,} rows")

    # 3) Merge
    oos = oos.merge(mv, on=["datetime", "symbol"], how="left")
    n_miss = oos["circ_mv_yi"].isna().sum()
    print(f"After merge: {len(oos):,} rows, mv missing: {n_miss:,} ({100*n_miss/len(oos):.1f}%)")
    oos = oos.dropna(subset=["circ_mv_yi"])

    # 4) Define tiers
    tiers = {
        "Tier-3 (<20亿)":  (0, 20),
        "Tier-2b (20-30亿)": (20, 30),
        "Tier-2a (30-50亿)": (30, 50),
        "Tier-2 (50-100亿)": (50, 100),
        "Tier-1b (100-200亿)": (100, 200),
        "Tier-1a (≥200亿)": (200, 1e9),
        "ALL": (0, 1e9),
        "≥30亿": (30, 1e9),
        "≥50亿": (50, 1e9),
        "≥100亿": (100, 1e9),
    }

    # 5) Analyze each tier
    print("\n" + "=" * 90)
    print("R133 Phase 1 — T1-C: P3 模型按市值分桶选股质量分析")
    print("=" * 90)
    hdr = f"{'Tier':<22} {'N_stocks':>8} {'Top50 AR%':>10} {'Bot50 AR%':>10} {'L-S AR%':>10} {'IC':>8} {'IC>0%':>7}"
    print(hdr)
    print("-" * 90)

    results = {}
    for tier_name, (lo, hi) in tiers.items():
        tier_df = oos[(oos["circ_mv_yi"] >= lo) & (oos["circ_mv_yi"] < hi)]
        if len(tier_df) == 0:
            continue
        res = analyze_tier(tier_df, tier_name, top_k=top_k)
        results[tier_name] = res
        print(
            f"{tier_name:<22} {res['n_symbols']:>8} "
            f"{res['top50_ar']*100:>9.1f}% "
            f"{res['bot50_ar']*100:>9.1f}% "
            f"{res['ls_ar']*100:>9.1f}% "
            f"{res['mean_ic']:>8.4f} "
            f"{res['ic_pos_ratio']*100:>6.1f}%"
        )

    print("\n" + "=" * 90)
    print("关键结论:")
    all_r = results.get("ALL", {})
    ge30 = results.get("≥30亿", {})
    ge100 = results.get("≥100亿", {})
    t3 = results.get("Tier-3 (<20亿)", {})
    if all_r and ge30 and t3:
        print(f"  全市场 Top50 AR = {all_r.get('top50_ar',0)*100:.1f}%")
        print(f"  <20亿   Top50 AR = {t3.get('top50_ar',0)*100:.1f}%  ← 微盘")
        print(f"  ≥30亿   Top50 AR = {ge30.get('top50_ar',0)*100:.1f}%  ← 非微盘")
        if ge100:
            print(f"  ≥100亿  Top50 AR = {ge100.get('top50_ar',0)*100:.1f}%  ← 大盘")
    print("=" * 90)


if __name__ == "__main__":
    main()
