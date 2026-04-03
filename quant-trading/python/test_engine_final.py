#!/usr/bin/env python3
"""Run full engine test after fixes."""
import json, sys
sys.stdin = open("/dev/null")
from engines.chip_accumulation import run_chip_accumulation

for symbol in ["300342", "600519"]:
    r = run_chip_accumulation({"action": "detect", "symbol": symbol, "lookback": 30})
    print(f"=== {symbol} ({r.get('name', '?')}) ===")
    print(f"Score: {r['total_score']}/100  Level: {r['level']}  Conclusion: {r['conclusion']}")
    print(f"Dimensions: {json.dumps(r['dimension_scores'], ensure_ascii=False)}")
    for s in r.get("signals", []):
        print(f"  [{s['dimension']}] {s['description']}")
    if r.get("errors"):
        for e in r["errors"]:
            print(f"  ERROR: {e}")
    print()
