#!/usr/bin/env python3
"""Debug turnover calculation in the engine context."""
import json, os, sys
sys.stdin = open("/dev/null")

# Import engine which sets up proxy bypass
from engines.chip_accumulation import (
    _fetch_klines, _fetch_float_shares, _enrich_turnover_rate, _retry_call, _safe_float
)
import akshare as ak

symbol = "300342"
errors = []

# 1) Fetch klines
klines = _fetch_klines(ak, symbol, 10, errors)
print(f"Kline source errors: {errors}")
print(f"Kline count: {len(klines)}")
if klines:
    last3 = klines[-3:]
    for k in last3:
        print(f"  date={k['date']}, vol={k['volume']}, turnover_rate={k['turnover_rate']}, close={k['close']}")

# 2) Fetch float shares
flt = _fetch_float_shares(ak, symbol)
print(f"\nFloat shares: {flt}")

# 3) Test enrichment
errors2 = []
_enrich_turnover_rate(ak, symbol, klines, errors2)
print(f"Enrichment errors: {errors2}")
if klines:
    last3 = klines[-3:]
    for k in last3:
        print(f"  date={k['date']}, vol={k['volume']}, turnover_rate={k['turnover_rate']}")

# 4) Manual check
if flt and klines:
    vol = klines[-1]['volume']
    # Detect if volume is in lots
    volumes = sorted([k.get("volume", 0) for k in klines if k.get("volume", 0) > 0])
    median_vol = volumes[len(volumes)//2] if volumes else 0
    mult = 100 if median_vol < flt / 500 else 1
    manual = vol * mult / flt * 100
    print(f"\nManual: vol={vol}, mult={mult}, float={flt}, turnover={manual:.4f}%")
