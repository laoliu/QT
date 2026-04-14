#!/usr/bin/env python3
"""Quick test: simulate gateway calling backtest_engine with a small set of stocks."""
import os, sys, json, time

os.environ["WINDOWS_XTDATA_URL"] = "http://127.0.0.1:58088"
os.environ["QUANT_CONFIG"] = json.dumps({
    "action": "run",
    "initial_capital": 100000,
    "start_date": "2025-06-01",
    "end_date": "2025-12-31",
    "strategy": "momentum",
    "risk_tolerance": "medium",
    "symbols": ["600519", "002051"],
    "rebalance_days": 14,
})

sys.path.insert(0, os.path.dirname(__file__))

t0 = time.time()
from scripts.backtest_engine import run_backtest

result = run_backtest(
    initial_capital=100000,
    start_date="2025-06-01",
    end_date="2025-12-31",
    strategy="momentum",
    symbols=["600519", "002051"],
)
elapsed = time.time() - t0

print(f"\n=== RESULT in {elapsed:.1f}s ===", file=sys.stderr)
if "error" in result:
    print(f"ERROR: {result['error']}", file=sys.stderr)
else:
    print(f"Total return: {result.get('totalReturn', 'N/A')}", file=sys.stderr)
    print(f"Trades: {result.get('totalTrades', 'N/A')}", file=sys.stderr)
    print(f"Symbols with data: {len(result.get('equityCurve', []))} equity points", file=sys.stderr)
