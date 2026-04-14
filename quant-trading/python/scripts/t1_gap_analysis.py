"""T+1 Gap Analysis: Close-to-Close vs Open-to-Open returns."""
import pandas as pd
import numpy as np
import glob
import os

# Load OOS predictions
oos = pd.read_parquet('models/ml_csi300_lgb_p3/oos_predictions.parquet')
oos['date'] = pd.to_datetime(oos['datetime'])
oos['date_str'] = oos['date'].dt.strftime('%Y-%m-%d')
dates = sorted(oos['date_str'].unique())
print(f'OOS: {len(oos)} rows, {len(dates)} dates')

# Load monthly bulk data VECTORIZED
eq_cache = 'data/cache/equity_tushare'
frames = []
for f in sorted(glob.glob(os.path.join(eq_cache, 'daily_2*.parquet'))):
    stem = os.path.basename(f).replace('.parquet', '')
    if 'basic' in stem:
        continue
    ym = stem.replace('daily_', '')
    try:
        ym_int = int(ym)
    except ValueError:
        continue
    if ym_int < 201801 or ym_int > 202512:
        continue
    df = pd.read_parquet(f, columns=['ts_code', 'trade_date', 'open', 'close'])
    frames.append(df)

prices = pd.concat(frames, ignore_index=True)
prices['trade_date'] = prices['trade_date'].astype(str)
print(f'Loaded {len(prices)} price records')

prices = prices.set_index(['ts_code', 'trade_date'])
print('Index built')

rebal_dates = dates[4::5]
print(f'Rebalance dates: {len(rebal_dates)}')

c2c, o2o = [], []
for rd in rebal_dates:
    day = oos[oos['date_str'] == rd].nlargest(30, 'pred_score')
    c2c.append(day['fwd_return'].mean())

    rd_idx = dates.index(rd)
    buy_idx, sell_idx = rd_idx + 1, rd_idx + 6
    if sell_idx >= len(dates):
        continue
    buy_d = dates[buy_idx].replace('-', '')
    sell_d = dates[sell_idx].replace('-', '')

    rets = []
    for sym in day['symbol'].values:
        try:
            bp = prices.loc[(sym, buy_d), 'open']
            sp = prices.loc[(sym, sell_d), 'open']
            if isinstance(bp, pd.Series):
                bp = bp.iloc[0]
            if isinstance(sp, pd.Series):
                sp = sp.iloc[0]
            if bp > 0:
                rets.append(sp / bp - 1)
        except Exception:
            pass
    if rets:
        o2o.append(np.mean(rets))

c = np.array(c2c[:len(o2o)])
o = np.array(o2o)
n = len(o)
ann = 252 / 5

print(f'\n=== T+1 Gap Analysis ({n} periods) ===')
print(f'Close-to-Close: {c.mean()*100:.3f}%/period, AR={c.mean()*ann*100:.1f}%, SR={c.mean()/c.std()*np.sqrt(ann):.2f}')
print(f'Open-to-Open:   {o.mean()*100:.3f}%/period, AR={o.mean()*ann*100:.1f}%, SR={o.mean()/o.std()*np.sqrt(ann):.2f}')
print(f'Gap:            {(c.mean()-o.mean())*100:.3f}%/period, {(c.mean()-o.mean())*ann*100:.1f}%/year')
print(f'O2O captures {o.mean()/c.mean()*100:.1f}% of C2C alpha')

# Yearly breakdown
valid_dates = []
for rd in rebal_dates[:n]:
    idx = dates.index(rd) + 1
    if idx < len(dates):
        valid_dates.append(dates[idx])
    else:
        valid_dates.append(rd)

oos_yr = pd.DataFrame({
    'date': pd.to_datetime(valid_dates[:n]),
    'c2c': c,
    'o2o': o,
})
oos_yr['year'] = oos_yr['date'].dt.year

print('\nYearly breakdown:')
for yr, g in oos_yr.groupby('year'):
    c_ar = g['c2c'].mean() * ann * 100
    o_ar = g['o2o'].mean() * ann * 100
    gap = c_ar - o_ar
    print(f'  {yr}: C2C={c_ar:+.1f}%  O2O={o_ar:+.1f}%  Gap={gap:+.1f}%')
