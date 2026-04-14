"""Diagnose engine alpha loss: theoretical O2O vs engine actual.

Compare:
1. Theoretical: OOS top-30 ranked by pred_score, O2O returns, no friction
2. Engine-filtered: Same ranking but with engine's universe filters applied
3. With costs: Engine-filtered + slippage + commission
"""
import pandas as pd
import numpy as np
import glob
import os
import sys

sys.path.insert(0, 'src')


def load_prices():
    """Load all equity price data into a fast lookup."""
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
        df = pd.read_parquet(f, columns=['ts_code', 'trade_date', 'open', 'close', 'vol'])
        frames.append(df)
    prices = pd.concat(frames, ignore_index=True)
    prices['trade_date'] = prices['trade_date'].astype(str)
    return prices.set_index(['ts_code', 'trade_date'])


def load_basic():
    """Load daily basic data for market cap filtering."""
    eq_cache = 'data/cache/equity_tushare'
    frames = []
    for f in sorted(glob.glob(os.path.join(eq_cache, 'daily_basic_2*.parquet'))):
        stem = os.path.basename(f).replace('.parquet', '')
        ym = stem.replace('daily_basic_', '')
        try:
            ym_int = int(ym)
        except ValueError:
            continue
        if ym_int < 201801 or ym_int > 202512:
            continue
        cols_needed = ['ts_code', 'trade_date', 'total_mv', 'turnover_rate']
        try:
            df = pd.read_parquet(f, columns=cols_needed)
        except Exception:
            df = pd.read_parquet(f)
            for c in cols_needed:
                if c not in df.columns:
                    df[c] = np.nan
            df = df[cols_needed]
        frames.append(df)
    basic = pd.concat(frames, ignore_index=True)
    basic['trade_date'] = basic['trade_date'].astype(str)
    return basic.set_index(['ts_code', 'trade_date'])


def main():
    # Load data
    oos = pd.read_parquet('models/ml_csi300_lgb_p3/oos_predictions.parquet')
    oos['date'] = pd.to_datetime(oos['datetime'])
    oos['date_str'] = oos['date'].dt.strftime('%Y-%m-%d')
    dates = sorted(oos['date_str'].unique())
    print(f'OOS: {len(oos)} rows, {len(dates)} dates')

    print('Loading prices...')
    prices = load_prices()
    print(f'Prices: {len(prices)} records')

    print('Loading basic...')
    basic = load_basic()
    print(f'Basic: {len(basic)} records')

    rebal_dates = dates[4::5]
    print(f'Rebalance dates: {len(rebal_dates)}')

    # Engine filter parameters (from equity_rotation_base)
    MIN_PRICE = 3.0
    MAX_PRICE = 50.0
    MIN_MV = 100000  # 10万 (万元)
    MIN_TURNOVER = 500  # 万元
    MIN_LISTED = 60  # days
    TOP_K = 30
    SLIPPAGE_BPS = 10
    COMMISSION_RATE = 0.0000854
    COMMISSION_MIN = 5.0
    STAMP_TAX = 0.0005  # sell only

    ret_theoretical = []  # OOS top-30, O2O, no filter
    ret_filtered = []     # After engine-like filters, O2O
    ret_with_cost = []    # Filtered + costs
    filter_stats = []

    for rd in rebal_dates:
        rd_idx = dates.index(rd)
        buy_idx, sell_idx = rd_idx + 1, rd_idx + 6
        if sell_idx >= len(dates):
            continue
        buy_d = dates[buy_idx].replace('-', '')
        sell_d = dates[sell_idx].replace('-', '')

        day = oos[oos['date_str'] == rd].copy()
        day = day.sort_values('pred_score', ascending=False)

        # --- Layer 1: Theoretical (no filter) ---
        top30 = day.head(TOP_K)
        theo_rets = _compute_o2o(top30, prices, buy_d, sell_d)
        if theo_rets:
            ret_theoretical.append(np.mean(theo_rets))

        # --- Layer 2: With engine filters ---
        filtered = _apply_filters(
            day, prices, basic, buy_d,
            MIN_PRICE, MAX_PRICE, MIN_MV, MIN_TURNOVER
        )
        n_before = len(day)
        n_after = len(filtered)
        ftop30 = filtered.head(TOP_K)
        filt_rets = _compute_o2o(ftop30, prices, buy_d, sell_d)
        if filt_rets:
            ret_filtered.append(np.mean(filt_rets))
            # Layer 3: with costs
            cost_rets = [r - SLIPPAGE_BPS * 2 / 10000 for r in filt_rets]
            ret_with_cost.append(np.mean(cost_rets))
        filter_stats.append((n_before, n_after))

    ann = 252 / 5
    n = min(len(ret_theoretical), len(ret_filtered), len(ret_with_cost))
    t = np.array(ret_theoretical[:n])
    f = np.array(ret_filtered[:n])
    w = np.array(ret_with_cost[:n])

    print(f'\n=== Engine Gap Diagnosis ({n} periods) ===')
    for label, arr in [('Theoretical O2O', t), ('Filtered O2O', f), ('With costs', w)]:
        ar = arr.mean() * ann * 100
        sr = arr.mean() / arr.std() * np.sqrt(ann) if arr.std() > 0 else 0
        print(f'{label:20s}: {arr.mean()*100:.3f}%/period  AR={ar:+.1f}%  SR={sr:.2f}')

    avg_before = np.mean([s[0] for s in filter_stats])
    avg_after = np.mean([s[1] for s in filter_stats])
    print(f'\nFilter funnel: {avg_before:.0f} -> {avg_after:.0f} ({avg_after/avg_before*100:.1f}%)')

    # Overlap analysis
    overlaps = []
    for i, rd in enumerate(rebal_dates[:n]):
        day = oos[oos['date_str'] == rd].sort_values('pred_score', ascending=False)
        theo_syms = set(day.head(TOP_K)['symbol'])
        rd_idx = dates.index(rd)
        buy_d = dates[rd_idx + 1].replace('-', '')
        filtered = _apply_filters(
            day, prices, basic, buy_d,
            MIN_PRICE, MAX_PRICE, MIN_MV, MIN_TURNOVER
        )
        filt_syms = set(filtered.head(TOP_K)['symbol'])
        overlaps.append(len(theo_syms & filt_syms))
    print(f'Avg overlap (theo vs filtered top-30): {np.mean(overlaps):.1f}/30')


def _compute_o2o(picks, prices, buy_d, sell_d):
    """Compute open-to-open returns for selected stocks."""
    rets = []
    for sym in picks['symbol'].values:
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
    return rets


def _apply_filters(day_df, prices, basic, trade_date,
                   min_price, max_price, min_mv, min_turnover):
    """Apply engine-like universe filters."""
    valid = []
    for _, row in day_df.iterrows():
        sym = row['symbol']
        try:
            p = prices.loc[(sym, trade_date)]
            close = p['close'] if not isinstance(p, pd.DataFrame) else p['close'].iloc[0]
        except Exception:
            continue
        if close < min_price or close > max_price:
            continue
        try:
            b = basic.loc[(sym, trade_date)]
            mv = b['total_mv'] if not isinstance(b, pd.DataFrame) else b['total_mv'].iloc[0]
            tv = b['turnover_rate'] if not isinstance(b, pd.DataFrame) else b['turnover_rate'].iloc[0]
        except Exception:
            mv = 999999
            tv = 999
        if mv < min_mv:
            continue
        valid.append(row)
    if not valid:
        return pd.DataFrame()
    return pd.DataFrame(valid)


if __name__ == '__main__':
    main()
