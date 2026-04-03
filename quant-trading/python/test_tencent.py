#!/usr/bin/env python3
"""Test Tencent stock API for float shares."""
import requests, os, re

for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

symbol = "300342"
prefix = "sz" if symbol.startswith("0") or symbol.startswith("3") else "sh"

# Tencent stock API - returns lots of data
s = requests.Session()
s.trust_env = False

# Basic quote (s_ prefix = simple)
print("=== Tencent simple quote ===")
r = s.get(f"https://qt.gtimg.cn/q=s_{prefix}{symbol}", timeout=10,
          headers={"User-Agent": "Mozilla/5.0"})
print(f"Response: {r.text[:200]}")

# Full quote (no s_ prefix = detailed)
print("\n=== Tencent full quote ===")
r = s.get(f"https://qt.gtimg.cn/q={prefix}{symbol}", timeout=10,
          headers={"User-Agent": "Mozilla/5.0"})
text = r.text
if "=" in text and '"' in text:
    fields = text.split('"')[1].split("~")
    print(f"Total fields: {len(fields)}")
    # Print all fields with index
    for i, f in enumerate(fields):
        if f:
            print(f"  [{i:3d}]: {f}")

# Also try Tencent market cap fields
# Field 44: 总市值, Field 45: 流通市值
print(f"\nField 44 (总市值): {fields[44] if len(fields) > 44 else 'N/A'}")
print(f"Field 45 (流通市值): {fields[45] if len(fields) > 45 else 'N/A'}")
print(f"Field 3 (当前价): {fields[3] if len(fields) > 3 else 'N/A'}")

# If we have float market cap and price, calc float shares
if len(fields) > 45:
    try:
        float_mcap = float(fields[45])
        price = float(fields[3])
        float_shares = float_mcap / price
        print(f"\nCalculated float shares: {float_mcap} / {price} = {float_shares:,.0f}")
        print(f"Expected:  ~418,798,863")
    except:
        print("Could not parse values")

# Also try field 38 (总股本) and 39 (流通股本)
print(f"\nField 38: {fields[38] if len(fields) > 38 else 'N/A'}")
print(f"Field 39: {fields[39] if len(fields) > 39 else 'N/A'}")
