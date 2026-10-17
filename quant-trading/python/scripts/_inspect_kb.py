"""Inspect mutation_kb.json for analysis."""
import json, sys
from pathlib import Path

kb_path = Path(sys.argv[1]) if len(sys.argv) > 1 else (
    Path(__file__).resolve().parents[1] / "output/auto_mining_v2/mutation_kb.json"
)
if not kb_path.exists():
    print(f"Not found: {kb_path}")
    sys.exit(1)

data = json.load(open(kb_path, encoding="utf-8"))
total = len(data)
passed = sum(1 for e in data if e["passed"])
print(f"Total: {total}, Passed: {passed}, Rate: {passed/max(total,1)*100:.1f}%\n")

# Failure reason distribution
from collections import Counter
reasons = Counter()
for e in data:
    if not e["passed"]:
        r = e.get("failure_reason", "unknown")
        key = r.split("(")[0] if "(" in r else r
        reasons[key] += 1
print("Failure reasons:")
for r, c in reasons.most_common():
    print(f"  {r:30s} {c}")

print("\nAll entries:")
for e in data:
    status = "PASS" if e["passed"] else "FAIL"
    fr = e.get("failure_reason", "")[:55]
    fit = e.get("fitness", 0)
    expr = e["expression"][:50]
    print(f"  [{status}] ICIR={e['icir']:+.3f} fit={fit:.3f} {fr:55s} {expr}")
