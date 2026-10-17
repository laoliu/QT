#!/usr/bin/env python3
"""Test fetching klines for an uncommon stock to see which data source is actually used."""
import os, sys, time
os.environ["WINDOWS_XTDATA_URL"] = "http://127.0.0.1:18888"

sys.path.insert(0, os.path.dirname(__file__))
from data_providers.akshare_provider import get_klines

# Use a less common stock that may not be cached
t0 = time.time()
bars = get_klines("688692", "1d", "2025-01-01", "2025-12-31", "cn_stock")
print(f"688692: {len(bars) if bars else 0} bars in {time.time()-t0:.1f}s", file=sys.stderr)
