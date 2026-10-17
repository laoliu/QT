"""Evaluate GRU model OOS predictions and compare with existing models.

Usage:
    python scripts/evaluate_gru_ensemble.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJ))


def load_oos(model_dir: str) -> pd.DataFrame:
    p = _PROJ / "models" / model_dir / "oos_predictions.parquet"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_parquet(p)
    df["date_str"] = pd.to_datetime(df["datetime"]).dt.strftime("%Y-%m-%d")
    return df


def topk_returns(oos: pd.DataFrame, k: int = 50, fwd_days: int = 5):
    """Per-period top-k average return series."""
    dates = sorted(oos["date_str"].unique())
    rebal_dates = dates[fwd_days - 1 :: fwd_days]
    returns = []
    for rd in rebal_dates:
        day = oos[oos["date_str"] == rd].sort_values("pred_score", ascending=False)
        if len(day) < k:
            continue
        topk = day.head(k)
        returns.append({"date": rd, "ret": topk["fwd_return"].mean()})
    return pd.DataFrame(returns)


def main():
    # Models to compare
    model_dirs = [
        "ml_csi2000_lgb_20d_r40",
        "ml_csi2000_de_10d",
        "ml_csi2000_lgb_b3_sizeneutral",
        "ml_csi2000_lgb_fund_t1aligned",
        "ml_csi2000_lgb_20d_neutral_p1",
        "ml_csi2000_gru_v1",
    ]
    names = ["lgb_20d", "de_10d", "lgb_b3", "lgb_fund", "lgb_neutral", "gru_v1"]

    print("=" * 70)
    print("OOS Prediction Quality Comparison")
    print("=" * 70)

    oos_dfs = {}
    for md, nm in zip(model_dirs, names):
        df = load_oos(md)
        if df.empty:
            print(f"  {nm}: NOT FOUND")
            continue
        oos_dfs[nm] = df
        dates = sorted(df["date_str"].unique())
        # Per-date IC
        ics = []
        for d in dates:
            day = df[df["date_str"] == d]
            if len(day) >= 50:
                ic = day["pred_score"].corr(day["fwd_return"], method="spearman")
                if np.isfinite(ic):
                    ics.append(ic)
        ic_arr = np.array(ics)
        ret_df = topk_returns(df)
        if len(ret_df) > 0:
            r = ret_df["ret"].values
            ar = r.mean() * 252 / 5 * 100
            sr = r.mean() / r.std() * np.sqrt(252 / 5) if r.std() > 0 else 0
        else:
            ar = sr = 0
        print(
            f"  {nm:20s}: IC={ic_arr.mean():.4f}±{ic_arr.std():.4f}  "
            f"ICIR={ic_arr.mean()/ic_arr.std():.2f}  "
            f"Top50-AR={ar:.1f}%  Top50-SR={sr:.2f}  "
            f"({len(dates)} dates)"
        )

    # Pairwise return correlation
    if len(oos_dfs) >= 2:
        print("\n" + "=" * 70)
        print("Pairwise Top-50 Return Correlation")
        print("=" * 70)
        ret_series = {}
        for nm, df in oos_dfs.items():
            rdf = topk_returns(df)
            if len(rdf) > 0:
                ret_series[nm] = rdf.set_index("date")["ret"]

        if len(ret_series) >= 2:
            combined = pd.DataFrame(ret_series)
            # Align on common dates
            combined = combined.dropna()
            corr = combined.corr()
            print(corr.to_string(float_format="%.3f"))
            print(f"\nCommon dates: {len(combined)}")

            # Highlight GRU correlation
            if "gru_v1" in corr.columns:
                gru_corrs = corr["gru_v1"].drop("gru_v1")
                print(f"\nGRU vs others: mean={gru_corrs.mean():.3f}, "
                      f"max={gru_corrs.max():.3f}, min={gru_corrs.min():.3f}")
                if gru_corrs.max() < 0.75:
                    print("✓ GRU passes diversification threshold (all < 0.75)")
                else:
                    print("✗ GRU too correlated with existing models")


if __name__ == "__main__":
    main()
