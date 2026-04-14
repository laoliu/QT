"""Quick aggressive CB strategy sweep — targeted parameter exploration."""
import os
import re
import sys
import time
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from aipha.backtest.config_runner import run_from_config

OUTPUT_BASE = "output/quick_sweep"
os.makedirs(OUTPUT_BASE, exist_ok=True)

BASE_CONFIG = {
    "data": {
        "source": "tushare_cb",
        "start_date": "2018-01-01",
        "end_date": "2024-12-31",
        "frequency": "1d",
        "adjust": "none",
        "cache_dir": "data/cache/cb_tushare",
    },
    "fill_model": {
        "name": "next_bar_open",
        "volume_constraint": {"max_participation_rate": 0.10, "lot_size": 10},
    },
    "cost": {
        "commission_rate": 0.0001,
        "commission_min": 5.0,
        "stamp_tax_rate": 0.0,
        "transfer_fee_rate": 0.0,
        "slippage_bps": 5.0,
    },
    "initial_capital": 1000000.0,
    "lot_size": 10,
    "t_plus_n": 0,
    "random_seed": 42,
}


def extract_metrics(html_path):
    if not os.path.exists(html_path):
        return None
    with open(html_path, 'r', encoding='utf-8') as f:
        html = f.read()
    pairs = re.findall(
        r'metric-label[^>]*>([^<]+)</span>\s*<span\s+class=.metric-value[^>]*>([^<]+)</span>',
        html
    )
    m = {l.strip(): v.strip() for l, v in pairs}
    try:
        return {
            'annual': float(m.get('年化收益率', '0').replace('%', '')),
            'mdd': float(m.get('最大回撤', '0').replace('%', '')),
            'sharpe': float(m.get('夏普比率', '0')),
            'turnover': float(m.get('年化换手率', '0').replace('%', '')),
        }
    except Exception:
        return None


sweeps = [
    # --- cb_multi_alpha: size-dominant with minor dynamic factors ---
    ("ma_s80_k5_p150_i20", "cb_multi_alpha", {
        "top_k": 5, "rebalance_days": 1, "buffer_k": 2,
        "max_price": 150.0, "max_issue_size": 20000.0,
        "min_turnover": 10.0, "min_days_to_maturity": 60,
        "max_premium": 80.0,
        "momentum_lookback": 10, "surge_lookback": 20, "warmup_bars": 25,
        "w_size": 0.80, "w_momentum": 0.05, "w_surge": 0.05,
        "w_premium": 0.05, "w_price": 0.05,
        "stop_loss_pct": 0.50, "min_positions": 3,
        "exclude_called": True, "exclude_st": True, "lot_size": 10,
    }),
    ("ma_s70_k5_p200_i20", "cb_multi_alpha", {
        "top_k": 5, "rebalance_days": 1, "buffer_k": 2,
        "max_price": 200.0, "max_issue_size": 20000.0,
        "min_turnover": 10.0, "min_days_to_maturity": 60,
        "max_premium": 100.0,
        "momentum_lookback": 10, "surge_lookback": 20, "warmup_bars": 25,
        "w_size": 0.70, "w_momentum": 0.10, "w_surge": 0.10,
        "w_premium": 0.05, "w_price": 0.05,
        "stop_loss_pct": 0.50, "min_positions": 3,
        "exclude_called": True, "exclude_st": True, "lot_size": 10,
    }),
    ("ma_s80_k5_p150_i15", "cb_multi_alpha", {
        "top_k": 5, "rebalance_days": 1, "buffer_k": 2,
        "max_price": 150.0, "max_issue_size": 15000.0,
        "min_turnover": 5.0, "min_days_to_maturity": 60,
        "max_premium": 80.0,
        "momentum_lookback": 10, "surge_lookback": 20, "warmup_bars": 25,
        "w_size": 0.80, "w_momentum": 0.05, "w_surge": 0.05,
        "w_premium": 0.05, "w_price": 0.05,
        "stop_loss_pct": 0.50, "min_positions": 3,
        "exclude_called": True, "exclude_st": True, "lot_size": 10,
    }),
    ("ma_s80_k8_p200_i20", "cb_multi_alpha", {
        "top_k": 8, "rebalance_days": 1, "buffer_k": 3,
        "max_price": 200.0, "max_issue_size": 20000.0,
        "min_turnover": 5.0, "min_days_to_maturity": 60,
        "max_premium": 100.0,
        "momentum_lookback": 10, "surge_lookback": 20, "warmup_bars": 25,
        "w_size": 0.80, "w_momentum": 0.05, "w_surge": 0.05,
        "w_premium": 0.05, "w_price": 0.05,
        "stop_loss_pct": 0.50, "min_positions": 3,
        "exclude_called": True, "exclude_st": True, "lot_size": 10,
    }),
    # --- cb_multi_alpha: momentum-enhanced ---
    ("ma_s40_m30_k5_p150", "cb_multi_alpha", {
        "top_k": 5, "rebalance_days": 1, "buffer_k": 2,
        "max_price": 150.0, "max_issue_size": 20000.0,
        "min_turnover": 10.0, "min_days_to_maturity": 60,
        "max_premium": 80.0,
        "momentum_lookback": 5, "surge_lookback": 20, "warmup_bars": 25,
        "w_size": 0.40, "w_momentum": 0.30, "w_surge": 0.10,
        "w_premium": 0.10, "w_price": 0.10,
        "stop_loss_pct": 0.50, "min_positions": 3,
        "exclude_called": True, "exclude_st": True, "lot_size": 10,
    }),
    # --- cb_reversal_small: size-dominant reversal ---
    ("rs_k5_r3_s70", "cb_reversal_small", {
        "top_k": 5, "rebalance_days": 1, "buffer_k": 2,
        "max_price": 150.0, "max_issue_size": 20000.0,
        "min_turnover": 5.0, "min_days_to_maturity": 60,
        "max_premium": 80.0,
        "reversal_lookback": 3, "warmup_bars": 5,
        "reversal_weight": 0.30, "size_weight": 0.70,
        "stop_loss_pct": 0.50, "min_positions": 3,
        "max_single_drop": -0.25,
        "exclude_called": True, "exclude_st": True, "lot_size": 10,
    }),
    ("rs_k5_r5_pure", "cb_reversal_small", {
        "top_k": 5, "rebalance_days": 1, "buffer_k": 2,
        "max_price": 200.0, "max_issue_size": 20000.0,
        "min_turnover": 5.0, "min_days_to_maturity": 60,
        "max_premium": 100.0,
        "reversal_lookback": 5, "warmup_bars": 8,
        "reversal_weight": 1.0, "size_weight": 0.0,
        "stop_loss_pct": 0.50, "min_positions": 3,
        "max_single_drop": -0.30,
        "exclude_called": True, "exclude_st": True, "lot_size": 10,
    }),
    # --- cb_regime_rotation variants ---
    ("rr_k5_rl10", "cb_regime_rotation", {
        "top_k": 5, "rebalance_days": 1, "buffer_k": 2,
        "max_price": 150.0, "max_issue_size": 20000.0,
        "min_turnover": 10.0, "min_days_to_maturity": 60,
        "max_premium": 80.0,
        "regime_lookback": 10, "bull_threshold": 0.02, "bear_threshold": -0.01,
        "momentum_lookback": 10, "surge_lookback": 20,
        "reversal_lookback": 5, "warmup_bars": 25,
        "bull_w_size": 0.10, "bull_w_momentum": 0.35,
        "bull_w_surge": 0.30, "bull_w_premium": 0.10,
        "bull_w_price": 0.05, "bull_w_reversal": 0.10,
        "neutral_w_size": 0.25, "neutral_w_momentum": 0.15,
        "neutral_w_surge": 0.10, "neutral_w_premium": 0.20,
        "neutral_w_price": 0.15, "neutral_w_reversal": 0.15,
        "bear_w_size": 0.15, "bear_w_momentum": 0.00,
        "bear_w_surge": 0.05, "bear_w_premium": 0.15,
        "bear_w_price": 0.25, "bear_w_reversal": 0.40,
        "stop_loss_pct": 0.50, "min_positions": 3,
        "exclude_called": True, "exclude_st": True, "lot_size": 10,
    }),
]

print(f"Total: {len(sweeps)} configs")
results = []

for i, (name, strat, params) in enumerate(sweeps):
    out_dir = os.path.join(OUTPUT_BASE, name)
    report_path = os.path.join(out_dir, "backtest_report.html")

    if os.path.exists(report_path):
        metrics = extract_metrics(report_path)
        if metrics:
            results.append((name, metrics))
            print(f"[{i+1}] {name}: CACHED annual={metrics['annual']:.1f}%")
            continue

    cfg = dict(BASE_CONFIG)
    cfg["name"] = name
    cfg["strategy"] = {"name": strat, "params": params}
    cfg["output_dir"] = out_dir

    tmp_path = os.path.join(OUTPUT_BASE, f"_tmp_{name}.yml")
    with open(tmp_path, 'w', encoding='utf-8') as f:
        yaml.dump(cfg, f, allow_unicode=True)

    t0 = time.time()
    try:
        run_from_config(tmp_path, output_dir=out_dir)
        elapsed = time.time() - t0
        metrics = extract_metrics(report_path)
        if metrics:
            results.append((name, metrics))
            flag = " ***" if metrics['annual'] >= 25 else ""
            print(f"[{i+1}] {name}: annual={metrics['annual']:.1f}% mdd={metrics['mdd']:.1f}% sharpe={metrics['sharpe']:.2f} ({elapsed:.0f}s){flag}")
        else:
            print(f"[{i+1}] {name}: NO METRICS ({elapsed:.0f}s)")
    except Exception as e:
        print(f"[{i+1}] {name}: FAILED - {e}")
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

# Summary
print("\n" + "=" * 100)
print("RESULTS SORTED BY ANNUAL RETURN")
print("=" * 100)
results.sort(key=lambda x: x[1]['annual'], reverse=True)
print(f"{'Name':<40} {'Annual':>8} {'MDD':>8} {'Sharpe':>8} {'Turnover':>10}")
print("-" * 80)
for name, m in results:
    flag = " ***30%+" if m['annual'] >= 30 else ""
    print(f"{name:<40} {m['annual']:>7.2f}% {m['mdd']:>7.2f}% {m['sharpe']:>7.2f} {m['turnover']:>9.1f}%{flag}")
