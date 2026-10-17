"""Analyze v5 KB status for expert review."""
import json
import re
from collections import Counter
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
kb = json.loads(
    (_PROJECT_ROOT / "output" / "auto_mining_v5" / "mutation_kb.json")
    .read_text(encoding="utf-8")
)
passed = [e for e in kb if e.get("passed")]

# Field distribution
fields = Counter()
for e in passed:
    for f in re.findall(r"\$(\w+)", e["expression"]):
        fields[f] += 1

print("=== Passed factor field distribution ===")
for f, c in fields.most_common(20):
    pct = c / len(passed) * 100
    print(f"  ${f}: {c} ({pct:.0f}%)")

print(f"\nTotal passed: {len(passed)}, Total: {len(kb)}")

# ICIR stats
icirs = [abs(e["icir"]) for e in passed]
print(f"ICIR range: {min(icirs):.3f} - {max(icirs):.3f}")
print(f"ICIR median: {sorted(icirs)[len(icirs)//2]:.3f}")

# Rejected gems
gems = [e for e in kb if not e.get("passed")
        and "ic_corr" in e.get("fail_reason", "")
        and abs(e.get("icir", 0)) >= 1.0]
print(f"\nRejected gems (ICIR>=1.0, killed by ic_corr): {len(gems)}")
for g in gems[:8]:
    print(f"  ICIR={g['icir']:+.3f} {g['expression'][:70]}")

# Category breakdown
price_fields = {"close", "open", "high", "low", "vwap"}
vol_fields = {"volume", "amount", "turnover", "turnover_rate"}
fund_fields = {"pb", "pe", "ps", "total_mv", "circ_mv", "pe_ttm",
               "dv_ratio", "dv_ttm", "profit_dedt", "roe"}

cats = Counter()
for e in passed:
    flds = set(re.findall(r"\$(\w+)", e["expression"]))
    has_price = bool(flds & price_fields)
    has_vol = bool(flds & vol_fields)
    has_fund = bool(flds & fund_fields)
    if has_fund and has_price:
        cats["cross(fund+price)"] += 1
    elif has_fund:
        cats["pure_fundamental"] += 1
    elif has_price and has_vol:
        cats["price+volume"] += 1
    elif has_price:
        cats["pure_price"] += 1
    elif has_vol:
        cats["pure_volume"] += 1
    else:
        cats["other"] += 1

print("\n=== Category distribution ===")
for c, n in cats.most_common():
    print(f"  {c}: {n} ({n/len(passed)*100:.0f}%)")

# Method distribution
methods = Counter()
for e in kb:
    m = e.get("method", "unknown")
    methods[m] += 1
print("\n=== Generation method distribution ===")
for m, n in methods.most_common():
    p = sum(1 for e in kb if e.get("method") == m and e.get("passed"))
    print(f"  {m}: {n} total, {p} passed ({p/max(n,1)*100:.0f}%)")
