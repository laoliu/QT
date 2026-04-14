"""Quick model comparison helper."""
import json, sys

def load_and_print(model_dir):
    try:
        with open(f"{model_dir}/evaluation_report.json") as f:
            d = json.load(f)
    except FileNotFoundError:
        print(f"NOT FOUND: {model_dir}")
        return

    oos = d.get("oos_metrics", {})
    p = d.get("portfolio_simulation", {})
    wf = d.get("wf_summary", {})

    name = model_dir.split("/")[-1]
    print(f"\n{'='*50}")
    print(f"Model: {name}")
    print(f"  mean_ic:     {oos.get('mean_ic', 0):.4f}")
    print(f"  ic_ir (WF):  {wf.get('ic_ir', 0):.4f}")
    print(f"  ic_pos:      {oos.get('ic_positive_ratio', 0):.1%}")
    print(f"  top50 AR:    {oos.get('top_annual_ret', 0):.1%}")
    print(f"  L-S AR:      {oos.get('long_short_annual', 0):.1%}")
    print(f"  Portfolio AR: {p.get('annual_return', 0):.1%}")
    print(f"  Portfolio SR: {p.get('sharpe_ratio', 0):.2f}")
    print(f"  Portfolio MDD:{p.get('max_drawdown', 0):.1%}")
    print(f"  IS-OOS decay: {wf.get('is_oos_mean_decay', 'N/A')}")

    folds = wf.get("folds", [])
    if folds:
        recent = folds[-8:]
        ics = [f["mean_ic"] for f in recent]
        print(f"  Recent 8 ICs: {[round(x,3) for x in ics]}")
        print(f"  Recent mean:  {sum(ics)/len(ics):.4f}")

models = [
    "models/ml_csi2000_lgb_fund",
    "models/ml_csi1000_lgb_regularized",
    "models/ml_csi1000_lgb_behavioral",
    "models/ml_csi1000_lgb_enhanced",
]
for m in models:
    load_and_print(m)
