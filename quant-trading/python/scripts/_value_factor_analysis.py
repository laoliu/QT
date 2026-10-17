"""价值因子系统性分析 — 单因子回测 + 多因子组合优化."""
import pandas as pd
import numpy as np
import sys, os, time, importlib.util
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Import from eval_value_factors & train_ml_factor
_spec_ev = importlib.util.spec_from_file_location(
    "evf", os.path.join(ROOT, "scripts", "eval_value_factors.py"))
_evf = importlib.util.module_from_spec(_spec_ev)
_spec_ev.loader.exec_module(_evf)

_spec_tmf = importlib.util.spec_from_file_location(
    "tmf", os.path.join(ROOT, "scripts", "train_ml_factor.py"))
_tmf = importlib.util.module_from_spec(_spec_tmf)
_spec_tmf.loader.exec_module(_tmf)

TOP_K = 30
REBALANCE_DAYS = 20
SLIPPAGE_BPS = 15.0
COMM = 0.0000854
STAMP = 0.0005


def backtest_signal(signal_df, prices_pivot, top_k=30, rebal=20):
    """Run equal-weight monthly rebalance backtest given signal & prices.
    
    signal_df: columns [datetime, symbol, score]
    prices_pivot: index=datetime, columns=symbol, values=close
    Returns: pd.Series of daily portfolio returns.
    """
    daily_ret = prices_pivot.pct_change(fill_method=None)
    trade_dates = sorted(signal_df['datetime'].unique())
    rebal_dates = trade_dates[::rebal]
    cost = SLIPPAGE_BPS / 10000 * 2 + COMM * 2 + STAMP
    
    parts = []
    for i, rd in enumerate(rebal_dates):
        day = signal_df[signal_df['datetime'] == rd]
        sel = day.nlargest(top_k, 'score')['symbol'].tolist()
        if not sel:
            continue
        end_d = rebal_dates[i + 1] if i + 1 < len(rebal_dates) else trade_dates[-1]
        mask = (daily_ret.index > rd) & (daily_ret.index <= end_d)
        valid = [s for s in sel if s in daily_ret.columns]
        if not valid:
            continue
        h = daily_ret.loc[mask, valid].mean(axis=1)
        if len(h) > 0:
            h.iloc[0] -= cost
            parts.append(h)
    
    if not parts:
        return pd.Series(dtype=float)
    ret = pd.concat(parts).sort_index()
    return ret.groupby(ret.index).first()


def compute_metrics(port_ret, label=""):
    """Compute standard metrics from portfolio return series."""
    if len(port_ret) < 20:
        return None
    nav = (1 + port_ret).cumprod()
    yrs = len(port_ret) / 252
    ar = float(nav.iloc[-1] ** (1 / yrs) - 1)
    vol = float(port_ret.std() * np.sqrt(252))
    sr = ar / vol if vol > 0 else 0
    dd = float(((nav - nav.cummax()) / nav.cummax()).min())
    calmar = ar / abs(dd) if dd != 0 else 0

    # Yearly returns
    yearly = {}
    for y in range(port_ret.index[0].year, port_ret.index[-1].year + 1):
        m = port_ret.index.year == y
        if m.sum() > 0:
            yearly[y] = float((1 + port_ret[m]).prod() - 1)

    return {
        'label': label, 'AR': ar, 'vol': vol, 'SR': sr,
        'MDD': dd, 'calmar': calmar, 'years': yrs, 'yearly': yearly,
    }


def main():
    t0 = time.time()
    
    # ---- 1. Load data & compute individual factors ----
    print("=" * 70)
    print("价值因子系统性回测分析")
    print("=" * 70)
    
    print("\n[1/4] 加载数据...")
    df = _tmf.load_daily_data("20150105", "20260227")
    symbols = df['symbol'].unique().tolist()
    print(f"  日线: {len(df)} 行, {len(symbols)} 只")
    
    print("  加载财务数据...")
    fina = _evf._load_fina_pit(symbols)
    cf = _evf._load_cashflow_pit(symbols)
    
    print("  合并...")
    merged = _evf._merge_fina_to_daily(df, fina)
    if cf is not None and len(cf) > 0:
        cf_cols = [c for c in cf.columns if c not in ['symbol', 'ann_date', 'end_date']]
        cf_daily = _evf._merge_fina_to_daily(df[['datetime', 'symbol']].copy(), cf)
        cb_need = [c for c in cf_cols if c in cf_daily.columns]
        for c in cb_need:
            merged[c] = cf_daily[c]
    
    print("\n[2/4] 计算因子...")
    merged, factor_names = _evf.compute_value_factors(merged)
    print(f"  因子: {factor_names}")
    
    # Price pivot for backtest
    prices_pivot = df.pivot_table(index='datetime', columns='symbol', values='close')
    
    # Filter to common symbols
    sig_symbols = set(merged['symbol'].unique()) & set(prices_pivot.columns)
    merged = merged[merged['symbol'].isin(sig_symbols)]
    print(f"  有效股票数: {len(sig_symbols)}")
    print(f"  数据加载耗时: {time.time()-t0:.0f}s")
    
    # ---- 2. Single-factor backtests ----
    print("\n[3/4] 单因子回测...")
    results = []
    
    for fname in factor_names:
        sig = merged[['datetime', 'symbol', fname]].dropna()
        sig = sig.rename(columns={fname: 'score'})
        pr = backtest_signal(sig, prices_pivot, top_k=TOP_K, rebal=REBALANCE_DAYS)
        m = compute_metrics(pr, label=fname)
        if m:
            results.append(m)
            yr_str = " ".join(f"{y}:{v*100:+.0f}" for y, v in m['yearly'].items())
            print(f"  {fname:25s}  AR={m['AR']*100:5.1f}%  SR={m['SR']:.2f}"
                  f"  MDD={m['MDD']*100:5.1f}%  | {yr_str}")
    
    # ---- 3. Top-N combos ----
    print("\n[4/4] 多因子组合回测...")
    # Sort factors by SR
    results.sort(key=lambda x: x['SR'], reverse=True)
    top_factors_list = [r['label'] for r in results]
    
    for n_top in [3, 5, 7]:
        chosen = top_factors_list[:n_top]
        combo = merged[['datetime', 'symbol']].copy()
        for f in chosen:
            combo[f] = merged[f]
        combo['score'] = combo[chosen].mean(axis=1)
        combo = combo.dropna(subset=['score'])
        sig = combo[['datetime', 'symbol', 'score']]
        pr = backtest_signal(sig, prices_pivot, top_k=TOP_K, rebal=REBALANCE_DAYS)
        m = compute_metrics(pr, label=f"top{n_top}_equal")
        if m:
            results.append(m)
            yr_str = " ".join(f"{y}:{v*100:+.0f}" for y, v in m['yearly'].items())
            print(f"  top{n_top}_equal ({','.join(chosen[:3])}..)"
                  f"  AR={m['AR']*100:5.1f}%  SR={m['SR']:.2f}"
                  f"  MDD={m['MDD']*100:5.1f}%  | {yr_str}")
    
    # Top-K sensitivity
    print("\n  Top-K 灵敏度 (best single factor: {})...".format(top_factors_list[0]))
    best_f = top_factors_list[0]
    sig_best = merged[['datetime', 'symbol', best_f]].dropna()
    sig_best = sig_best.rename(columns={best_f: 'score'})
    for k in [10, 20, 30, 50, 80]:
        pr = backtest_signal(sig_best, prices_pivot, top_k=k, rebal=REBALANCE_DAYS)
        m = compute_metrics(pr, label=f"{best_f}_k{k}")
        if m:
            print(f"    k={k:3d}: AR={m['AR']*100:5.1f}%  SR={m['SR']:.2f}"
                  f"  MDD={m['MDD']*100:5.1f}%")
    
    # ---- Show summary table ----
    print("\n" + "=" * 70)
    print("总结 (排除2015后):")
    print("=" * 70)
    for r in results:
        yr = r['yearly']
        post15 = {y: v for y, v in yr.items() if y >= 2016}
        if not post15:
            continue
        # recompute AR post-2015 approx
        cum = 1
        for v in post15.values():
            cum *= (1 + v)
        n_y = len(post15)
        ar_post = cum ** (1 / n_y) - 1 if n_y > 0 else 0
        print(f"  {r['label']:25s}  全量AR={r['AR']*100:5.1f}%"
              f"  2016+AR≈{ar_post*100:5.1f}%  SR={r['SR']:.2f}")
    
    print(f"\n总耗时: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
