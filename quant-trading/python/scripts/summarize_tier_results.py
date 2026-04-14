"""Quick summary of all tier experiment results."""
import json, pathlib

results = {}
for d in pathlib.Path("output").iterdir():
    if not d.is_dir():
        continue
    sf = d / "summary.json"
    if not sf.exists():
        continue
    try:
        s = json.loads(sf.read_text(encoding="utf-8"))
    except Exception:
        try:
            s = json.loads(sf.read_text(encoding="gbk"))
        except Exception:
            continue
    results[d.name] = s

# Print CSI500 results
print("=" * 90)
print("CSI500 Tier Results (trained on CSI500 universe, 240 features, P3)")
print("=" * 90)
for k in sorted(results):
    if "csi500" not in k:
        continue
    s = results[k]
    ar = s.get("annualized_return", 0)
    sr = s.get("sharpe_ratio", 0)
    mdd = s.get("max_drawdown", 0)
    cal = s.get("calmar_ratio", 0)
    mv_min = s.get("min_circ_mv_yi", 0)
    mv_max = s.get("max_circ_mv_yi", 0)
    mv_tag = f"[{mv_min}-{mv_max}B]" if mv_max else f"[>={mv_min}B]"
    print(f"  {k:42s} {mv_tag:>12s}  AR={ar:6.1%}  SR={sr:5.2f}  MDD={mdd:6.1%}  Cal={cal:4.2f}")

# Print CSI2000 P3 tier results for comparison
print()
print("=" * 90)
print("CSI2000 P3 Tier Results (trained on CSI2000 universe, comparison)")
print("=" * 90)
for k in sorted(results):
    if "p3_tier" not in k and "p3_mv" not in k:
        continue
    if "csi500" in k or "csi1000" in k:
        continue
    s = results[k]
    ar = s.get("annualized_return", 0)
    sr = s.get("sharpe_ratio", 0)
    mdd = s.get("max_drawdown", 0)
    cal = s.get("calmar_ratio", 0)
    mv_min = s.get("min_circ_mv_yi", 0)
    mv_max = s.get("max_circ_mv_yi", 0)
    mv_tag = f"[{mv_min}-{mv_max}B]" if mv_max else f"[>={mv_min}B]"
    print(f"  {k:42s} {mv_tag:>12s}  AR={ar:6.1%}  SR={sr:5.2f}  MDD={mdd:6.1%}  Cal={cal:4.2f}")

# Print CSI1000 results
print()
print("=" * 90)
print("CSI1000 Tier Results (trained on CSI1000 universe, comparison)")
print("=" * 90)
for k in sorted(results):
    if "csi1000" not in k:
        continue
    s = results[k]
    ar = s.get("annualized_return", 0)
    sr = s.get("sharpe_ratio", 0)
    mdd = s.get("max_drawdown", 0)
    cal = s.get("calmar_ratio", 0)
    mv_min = s.get("min_circ_mv_yi", 0)
    mv_max = s.get("max_circ_mv_yi", 0)
    mv_tag = f"[{mv_min}-{mv_max}B]" if mv_max else f"[>={mv_min}B]"
    print(f"  {k:42s} {mv_tag:>12s}  AR={ar:6.1%}  SR={sr:5.2f}  MDD={mdd:6.1%}  Cal={cal:4.2f}")
