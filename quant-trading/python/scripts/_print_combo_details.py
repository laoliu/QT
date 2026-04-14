"""Print detailed combo results for expert review."""
import json

with open("output/combo_r123_full/summary.json") as f:
    s = json.load(f)

for name in ["R123", "R116", "R123_oldmodel"]:
    c = s["combos"][name]
    seg = c.get("segmented", {})
    m = c["metrics"]
    print(f"=== {name} ===")
    ar = m["annualized_return"] * 100
    sr = m["sharpe_ratio"]
    mdd = m["max_drawdown"] * 100
    cal = m["calmar_ratio"]
    print(f"  Full: AR={ar:.1f}%, SR={sr}, MDD={mdd:.1f}%, Calmar={cal}")
    print(f"  Yearly: {m['yearly_returns']}")
    print(f"  Win: {m['win_years']}, Median: {m['median_yearly_return']*100:.1f}%")
    if "excl_best_year" in seg:
        print(f"  ExclBest: {seg['excl_best_year']}")
    if "post_2022aug" in seg:
        p = seg["post_2022aug"]
        print(f"  Post2022.08: AR={p['annualized_return']*100:.1f}%, SR={p['sharpe_ratio']}, MDD={p['max_drawdown']*100:.1f}%")
        print(f"  Post yearly: {p['yearly_returns']}")
    st = c.get("statistical", {})
    print(f"  DSR={st.get('dsr_statistic',0):.3f}, p={st.get('dsr_pvalue',0):.4f}, HLZ_t={st.get('hlz_t_adj',0):.3f}")
    print()
