"""Multi-tier portfolio comparison script."""
import pandas as pd
import numpy as np

# Load daily snapshots
csi300 = pd.read_csv('output/ml_csi300_ge200b_t1a/daily_snapshots.csv',
                      parse_dates=['date']).set_index('date')
csi2000 = pd.read_csv('output/ml_csi2000_ensemble_v2_neutral/daily_snapshots.csv',
                       parse_dates=['date']).set_index('date')
etf = pd.read_csv('output/etf_cross_v3_11y/daily_snapshots.csv',
                   parse_dates=['date']).set_index('date')

# Align to common dates
common = csi300.index.intersection(csi2000.index).intersection(etf.index)
common = common.sort_values()
print(f'Common period: {common[0].date()} ~ {common[-1].date()}, {len(common)} days')

r300 = csi300.loc[common, 'daily_return'].values
r2000 = csi2000.loc[common, 'daily_return'].values
retf = etf.loc[common, 'daily_return'].values

print(f'Corr CSI300 vs CSI2000: {np.corrcoef(r300, r2000)[0,1]:.3f}')
print(f'Corr CSI300 vs ETF:     {np.corrcoef(r300, retf)[0,1]:.3f}')
print(f'Corr CSI2000 vs ETF:    {np.corrcoef(r2000, retf)[0,1]:.3f}')

schemes = {
    'A: CSI2000(50)+CSI300(20)+ETF(30)': (0.50, 0.20, 0.30),
    'B: CSI2000(40)+CSI300(30)+ETF(30)': (0.40, 0.30, 0.30),
    'C: CSI2000(30)+CSI300(40)+ETF(30)': (0.30, 0.40, 0.30),
    'D: CSI300(45)+CSI2000(25)+ETF(30)': (0.25, 0.45, 0.30),
    'E: CSI2000(55)+CSI300(25)+ETF(20)': (0.55, 0.25, 0.20),
    'F: CSI2000(45)+CSI300(25)+ETF(30)': (0.45, 0.25, 0.30),
}

hdr = f"{'Scheme':42s} {'AR':>8s} {'SR':>8s} {'MDD':>8s} {'Calmar':>8s} {'Vol':>8s}"

# --- Part 2: Stability Analysis for Scheme F ---
print('\n=== Rolling 12-Month Sharpe (Scheme F: 45/25/30) ===')
dr_f = 0.45 * r2000 + 0.25 * r300 + 0.30 * retf
dates_list = list(common)
window = 252
for i in range(window, len(dr_f), 63):
    sub = dr_f[max(0, i - window):i]
    sr_val = np.mean(sub) / np.std(sub) * np.sqrt(252) if np.std(sub) > 0 else 0
    ar_val = np.prod(1 + sub) ** (252 / len(sub)) - 1
    dt_str = dates_list[i - 1].strftime('%Y-%m')
    print(f'  {dt_str}: SR={sr_val:.2f}  AR={ar_val:.1%}')

print('\n=== Weight Sensitivity (base: 45/25/30) ===')
def pf_metrics(w2k, w3h, wetf_w):
    dr = w2k * r2000 + w3h * r300 + wetf_w * retf
    nav_arr = np.cumprod(1 + dr)
    ar_v = nav_arr[-1] ** (252 / len(nav_arr)) - 1
    vol_v = np.std(dr) * np.sqrt(252)
    sr_v = ar_v / vol_v if vol_v > 0 else 0
    return sr_v, ar_v

perturbs = [
    ('Base 45/25/30', 0.45, 0.25, 0.30),
    ('CSI2000 +5pp', 0.50, 0.20, 0.30),
    ('CSI2000 -5pp', 0.40, 0.30, 0.30),
    ('ETF +5pp',     0.40, 0.25, 0.35),
    ('ETF -5pp',     0.50, 0.25, 0.25),
]
for lbl, w2k, w3h, wetf_w in perturbs:
    sr_v, ar_v = pf_metrics(w2k, w3h, wetf_w)
    print(f'  {lbl:20s} ({w2k:.0%}/{w3h:.0%}/{wetf_w:.0%}) SR={sr_v:.3f} AR={ar_v:.1%}')

print('\n=== IS/OOS Split Validation ===')
n_total = len(dr_f)
mid_idx = n_total // 2
is_sr = np.mean(dr_f[:mid_idx]) / np.std(dr_f[:mid_idx]) * np.sqrt(252)
oos_sr = np.mean(dr_f[mid_idx:]) / np.std(dr_f[mid_idx:]) * np.sqrt(252)
decay_pct = 1 - oos_sr / is_sr if is_sr > 0 else float('inf')
d_is_end = dates_list[mid_idx].strftime('%Y-%m-%d')
d_oos_end = dates_list[-1].strftime('%Y-%m-%d')
print(f'  IS  (first half):  SR={is_sr:.3f} (~ {d_is_end})')
print(f'  OOS (second half): SR={oos_sr:.3f} (~ {d_oos_end})')
print(f'  SR Decay: {decay_pct:.1%} (threshold <=50%)')
print('\n' + hdr)
print('-' * 82)

dates_list = list(common)

for name, (w2000, w300, wetf) in schemes.items():
    daily_r = w2000 * r2000 + w300 * r300 + wetf * retf
    nav = np.cumprod(1 + daily_r)
    ar = nav[-1] ** (252 / len(nav)) - 1
    vol = np.std(daily_r) * np.sqrt(252)
    sr = ar / vol if vol > 0 else 0
    peak = np.maximum.accumulate(nav)
    dd = (nav - peak) / peak
    mdd = -dd.min()
    calmar = ar / mdd if mdd > 0 else 0

    years = sorted(set(d.year for d in dates_list))
    yr_parts = []
    for y in years:
        mask = np.array([d.year == y for d in dates_list])
        yr_nav = np.cumprod(1 + daily_r[mask])
        yr_parts.append(f'{y}:{yr_nav[-1]-1:+.1%}')

    print(f'{name:42s} {ar:8.2%} {sr:8.3f} {mdd:8.2%} {calmar:8.3f} {vol:8.2%}')
    print(f'  Yearly: {"  ".join(yr_parts)}')

print('\n--- Single Legs (same period) ---')
for lname, rleg in [('CSI2000 V2 only', r2000),
                     ('CSI300>=200B only', r300),
                     ('ETF only', retf)]:
    nav = np.cumprod(1 + rleg)
    ar = nav[-1] ** (252 / len(nav)) - 1
    vol = np.std(rleg) * np.sqrt(252)
    sr = ar / vol if vol > 0 else 0
    peak = np.maximum.accumulate(nav)
    mdd = -(((nav - peak) / peak).min())
    calmar = ar / mdd if mdd > 0 else 0
    print(f'  {lname:40s} {ar:8.2%} {sr:8.3f} {mdd:8.2%} {calmar:8.3f} {vol:8.2%}')
