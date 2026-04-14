"""Quick combo weight sensitivity scan."""
import pandas as pd, numpy as np, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

def load_nav(path):
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values("date").drop_duplicates("date").set_index("date")["nav"]

navs = {
    "csi2000": load_nav(ROOT / "output/combo_r115_faa/ml_csi2000_fund/daily_nav.csv"),
    "csi1000_old": load_nav(ROOT / "output/risk_controlled_csi1000_lgb_fund/daily_nav.csv"),
    "csi1000_enh": load_nav(ROOT / "output/risk_controlled_csi1000_lgb_enhanced/daily_nav.csv"),
    "cb": load_nav(ROOT / "output/combo_r115_faa/cb_stock_quality/daily_snapshots.csv"),
    "etf": load_nav(ROOT / "output/combo_r115_faa/etf_cross_v3/daily_snapshots.csv"),
}
common = sorted(set.intersection(*[set(v.index) for v in navs.values()]))
for k in navs:
    navs[k] = navs[k].loc[common]

cap = 10_000_000

schemes = {
    "R116 (baseline)":     {"csi2000": 0.35, "csi1000_old": 0.15, "cb": 0.25, "etf": 0.25},
    "R123 Enhanced":       {"csi2000": 0.40, "csi1000_enh": 0.10, "cb": 0.25, "etf": 0.25},
    "R123 OldModel":       {"csi2000": 0.40, "csi1000_old": 0.10, "cb": 0.25, "etf": 0.25},
    "R124a 40/15/25/20":   {"csi2000": 0.40, "csi1000_old": 0.15, "cb": 0.25, "etf": 0.20},
    "R124b 45/10/25/20":   {"csi2000": 0.45, "csi1000_old": 0.10, "cb": 0.25, "etf": 0.20},
    "R124c 40/15/20/25":   {"csi2000": 0.40, "csi1000_old": 0.15, "cb": 0.20, "etf": 0.25},
    "R124d 45/15/20/20":   {"csi2000": 0.45, "csi1000_old": 0.15, "cb": 0.20, "etf": 0.20},
}

print(f"Period: {common[0].date()} ~ {common[-1].date()}, {len(common)} days")
print()
hdr = f"{'Scheme':<22} {'AR':>6} {'SR':>5} {'MDD':>7} {'Calmar':>7} {'P22AR':>6}"
print(hdr)
print("-" * 60)

for name, w in schemes.items():
    combo = None
    for k, wt in w.items():
        n = navs[k] / navs[k].iloc[0] * wt * cap
        combo = n if combo is None else combo + n
    rets = combo.pct_change().dropna()
    n_yr = len(rets) / 244
    ar = (combo.iloc[-1] / combo.iloc[0]) ** (1 / n_yr) - 1
    vol = float(rets.std()) * np.sqrt(244)
    sr = (float(rets.mean()) * 244 - 0.02) / vol if vol > 0 else 0
    pk = combo.cummax()
    mdd = float(((pk - combo) / pk).max())
    calmar = float(ar) / mdd if mdd > 0 else 0
    # Post 2022-08
    post = combo[combo.index >= "2022-08-01"]
    r_post = post.pct_change().dropna()
    ny_p = len(r_post) / 244
    ar_p = (post.iloc[-1] / post.iloc[0]) ** (1 / ny_p) - 1
    print(f"{name:<22} {ar*100:>5.1f}% {sr:>5.2f} {mdd*100:>6.1f}% {calmar:>7.2f} {ar_p*100:>5.1f}%")
