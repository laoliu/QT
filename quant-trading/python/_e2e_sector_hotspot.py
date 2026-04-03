#!/usr/bin/env python3
"""E2E test: run sector_hotspot screener via screener_engine."""
import json
import os
import sys

# Set up path so screeners package is importable
_PYTHON_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _PYTHON_ROOT)

from engines.screener_engine import run_screener

config = {
    "action": "run_screener",
    "screenerId": "sector_hotspot",
    "market": "cn_stock",
    "limit": 10,
}

print("Running sector_hotspot screener E2E...\n", file=sys.stderr)
result = run_screener(config)

if result.get("error"):
    print(f"ERROR: {result['error']}", file=sys.stderr)
    # Still print the JSON for inspection
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    sys.exit(1)

count = result.get("count", 0)
results = result.get("results", [])
print(f"Screener: {result.get('screenerName', '?')}", file=sys.stderr)
print(f"Total screened: {result.get('totalScreened', '?')}", file=sys.stderr)
print(f"Results: {count}", file=sys.stderr)

if results:
    print(f"\n{'Rank':<5} {'Code':<8} {'Name':<12} {'Score':<7} {'Sector':<12} Signals", file=sys.stderr)
    print("-" * 80, file=sys.stderr)
    for r in results[:10]:
        fac = r.get("factors", {})
        sigs = r.get("signals", [])
        # Show first 2 signals
        sig_text = " | ".join(sigs[:2]) if sigs else ""
        print(
            f"{r.get('rank', '-'):<5} "
            f"{r.get('stock_id', ''):<8} "
            f"{r.get('name', ''):<12} "
            f"{r.get('score', 0):<7.1f} "
            f"{fac.get('sector', ''):<12} "
            f"{sig_text}",
            file=sys.stderr,
        )

    # Show exit hints from first result
    first_fac = results[0].get("factors", {})
    hints = first_fac.get("exitHints", [])
    if hints:
        print(f"\nExit strategy ({first_fac.get('exitMode', '?')}):", file=sys.stderr)
        for h in hints:
            print(f"  - {h}", file=sys.stderr)

    leaders = first_fac.get("sectorLeaders", [])
    if leaders:
        print(f"\nSector leaders (can't buy):", file=sys.stderr)
        for l in leaders:
            print(f"  - {l['name']} ({l.get('boards', 1)}板)", file=sys.stderr)

# Output full JSON
print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
