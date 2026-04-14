"""Quick check of individual component performance."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from eval_r116_expert import calc_metrics
import pandas as pd
import numpy as np

ROOT = os.path.join(os.path.dirname(__file__), '..')
navs = {
    'ml_csi2000': 'output/combo_r113b_enhanced/ml_csi2000_fund/daily_nav.csv',
    'ml_csi1000': 'output/lightweight_csi1000_oos/daily_nav.csv',
    'cb_quality': 'output/combo_r115_faa/cb_stock_quality/daily_snapshots.csv',
    'etf_faa':    'output/combo_r115_faa/etf_cross_v3/daily_snapshots.csv',
}

for name, path in navs.items():
    fp = os.path.join(ROOT, path)
    df = pd.read_csv(fp, parse_dates=['date'])
    df = df[(df['date'] >= '2018-01-17') & (df['date'] <= '2025-12-31')]
    col = 'nav' if 'nav' in df.columns else 'net_value'
    nav = df.set_index('date')[col]
    nav = nav / nav.iloc[0]
    total_ret = nav.iloc[-1] / nav.iloc[0] - 1
    n_years = len(nav) / 252
    m = calc_metrics(nav)
    # manual yearly
    rets = nav.pct_change().dropna()
    yearly = {}
    for y in range(2018, 2026):
        mask = rets.index.year == y
        if mask.any():
            yearly[y] = (1 + rets[mask]).prod() - 1
    recent3 = np.mean([yearly.get(y, 0) for y in [2023, 2024, 2025]])
    line1 = (f"{name:12s}: AR={m['ar']*100:5.1f}%  SR={m['sr']:.3f}  "
             f"MDD={m['mdd']*100:5.1f}%  TotalRet={total_ret*100:.0f}%  "
             f"Recent3y={recent3*100:.1f}%")
    print(line1)
    parts = [f"{y}:{yearly.get(y,0)*100:.0f}%" for y in range(2018, 2026)]
    print(f"  Yearly: {' '.join(parts)}")
