#!/usr/bin/env python3
"""Test stock_recommend directly to see stderr output and timing."""
import os, sys, json, time

os.environ["WINDOWS_XTDATA_URL"] = "http://127.0.0.1:18888"

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

t0 = time.time()
from scripts.stock_recommend import recommend

t1 = time.time()
print(f"[TIMING] import took {t1-t0:.1f}s", file=sys.stderr)

result = recommend(
    capital=500000,
    strategy="momentum",
    risk_tolerance="medium",
    symbols=["002051", "600875", "603328", "600356", "601002", "000811", "002634"],
    start_date="2025-06-01",
    rebalance_days=5,
    exclude_star=True,
)
t2 = time.time()
print(f"\n[TIMING] recommend() took {t2-t1:.1f}s (total {t2-t0:.1f}s)", file=sys.stderr)
print(f"[RESULT] allocations: {len(result.get('allocations', []))}", file=sys.stderr)
if result.get("error"):
    print(f"[ERROR] {result['error']}", file=sys.stderr)
