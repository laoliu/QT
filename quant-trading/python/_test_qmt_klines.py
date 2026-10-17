#!/usr/bin/env python3
"""Quick test: can akshare_provider fetch klines via QMT?"""
import os, sys
os.environ["WINDOWS_XTDATA_URL"] = "http://127.0.0.1:18888"

from data_providers.akshare_provider import _fetch_qmt_klines, _read_qmt_bridge_url

print(f"Env WINDOWS_XTDATA_URL: {os.environ.get('WINDOWS_XTDATA_URL')}", file=sys.stderr)
print(f"Config bridge URL: {_read_qmt_bridge_url()}", file=sys.stderr)

try:
    bars = _fetch_qmt_klines("600519", "2025-01-01", "2026-04-08")
    print(f"Got {len(bars)} bars", file=sys.stderr)
    if bars:
        print(f"First: {bars[0]}", file=sys.stderr)
        print(f"Last:  {bars[-1]}", file=sys.stderr)
    else:
        print("EMPTY result!", file=sys.stderr)
except Exception as e:
    print(f"ERROR: {e}", file=sys.stderr)
    import traceback
    traceback.print_exc()
