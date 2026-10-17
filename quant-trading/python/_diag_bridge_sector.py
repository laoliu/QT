#!/usr/bin/env python3
"""Diagnostic: test sector endpoints on the Windows xtdata bridge."""
import json
import os
import sys
import urllib.request

def _read_bridge_url():
    try:
        config_path = os.path.expanduser("~/.quant-trading/quant-trading.json")
        with open(config_path) as f:
            cfg = json.load(f)
        return (cfg.get("plugins", {}).get("entries", {})
                .get("quant-trading", {}).get("config", {})
                .get("tradingAccount", {}).get("qmt", {})
                .get("windowsBridgeUrl", "http://192.168.3.30:18888"))
    except Exception:
        return "http://192.168.3.30:18888"

BRIDGE = _read_bridge_url().rstrip("/")
print(f"Bridge: {BRIDGE}\n")

def post(path, body):
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BRIDGE}{path}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def get(path):
    req = urllib.request.Request(f"{BRIDGE}{path}", method="GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())

# 1. Health
print("=== Health ===")
try:
    h = get("/health")
    print(json.dumps(h, indent=2))
except Exception as e:
    print(f"FAIL: {e}")
    sys.exit(1)

# 2. sector_list with various types
print("\n=== sector_list ===")
for stype in ["industry", "concept", "thshy", "thsgn", ""]:
    try:
        data = post("/sector_list", {"sector_type": stype})
        count = data.get("count", "?")
        items = data.get("data", [])
        preview = items[:5] if items else "EMPTY"
        print(f"  type={stype!r:12s}  count={count}  preview={preview}")
    except Exception as e:
        print(f"  type={stype!r:12s}  ERROR: {e}")

# 3. sector_members with various names
print("\n=== sector_members ===")
test_sectors = [
    "电力设备",
    "半导体",
    "银行",
    "计算机",
    "医药生物",
    "电力",
    "沪深300",
    "上证50",
]
for sector in test_sectors:
    try:
        data = post("/sector_members", {"sector": sector})
        count = data.get("count", "?")
        items = data.get("data", [])
        preview = items[:5] if items else "EMPTY"
        print(f"  {sector:8s}  count={count}  preview={preview}")
    except Exception as e:
        print(f"  {sector:8s}  ERROR: {e}")

# 4. Test multi-source cascade
print("\n=== Multi-source cascade (sector_data.py) ===")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from data_providers.sector_data import (
        WINDOWS_XTDATA_URL as resolved_url,
        _bridge_available,
        fetch_sector_members_multi,
        fetch_zt_pool_multi,
    )
    print(f"  Resolved bridge URL: {resolved_url}")
    print(f"  Bridge available: {_bridge_available()}")

    members, src = fetch_sector_members_multi("电力设备", {})
    print(f"  fetch_sector_members_multi('电力设备'): {len(members)} members, source={src}")
    if members:
        print(f"    first 5: {members[:5]}")

    zt = fetch_zt_pool_multi(date="20250620", use_previous=True)
    zt_stocks = zt.get("stocks", [])
    print(f"  fetch_zt_pool_multi(): {len(zt_stocks)} stocks, source={zt.get('source', '?')}")
    if zt_stocks:
        print(f"    first 3: {zt_stocks[:3]}")
except Exception as e:
    import traceback
    print(f"  FAIL: {e}")
    traceback.print_exc()

print("\nDone.")
