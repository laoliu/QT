#!/usr/bin/env python3
"""Comprehensive bridge v3 test: health + sector + klines + quotes."""
import json
import os
import sys
import time
import urllib.request

def _read_bridge_url():
    try:
        with open(os.path.expanduser("~/.quant-trading/quant-trading.json")) as f:
            cfg = json.load(f)
        return (cfg.get("plugins", {}).get("entries", {})
                .get("quant-trading", {}).get("config", {})
                .get("tradingAccount", {}).get("qmt", {})
                .get("windowsBridgeUrl", "http://192.168.3.30:58088"))
    except Exception:
        return "http://192.168.3.30:58088"

BRIDGE = _read_bridge_url().rstrip("/")

def post(path, body, timeout=30):
    payload = json.dumps(body).encode()
    req = urllib.request.Request(
        f"{BRIDGE}{path}", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def get(path, timeout=15):
    req = urllib.request.Request(f"{BRIDGE}{path}", method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

results = {}
print(f"Bridge: {BRIDGE}\n")

# 1. Health
print("1. Health check...")
t0 = time.time()
try:
    h = get("/health")
    elapsed = time.time() - t0
    ver = h.get("version", "?")
    xt = h.get("xtdata", False)
    print(f"   version={ver}, xtdata={xt}, elapsed={elapsed:.1f}s")
    results["health"] = "OK" if xt else "DEGRADED"
except Exception as e:
    print(f"   FAIL: {e}")
    results["health"] = "FAIL"

# 2. Sector members (quick check)
print("\n2. Sector members...")
for sector in ["沪深300", "医药生物", "电力设备"]:
    t0 = time.time()
    try:
        d = post("/sector_members", {"sector": sector})
        elapsed = time.time() - t0
        count = d.get("count", 0)
        status = "OK" if count > 0 else "EMPTY"
        print(f"   {sector}: count={count} ({status}) {elapsed:.1f}s")
        results[f"sector_{sector}"] = status
    except Exception as e:
        print(f"   {sector}: FAIL ({e})")
        results[f"sector_{sector}"] = "FAIL"

# 3. Klines
print("\n3. Klines (600519.SH, 000001.SZ)...")
t0 = time.time()
try:
    d = post("/klines", {"codes": ["600519.SH", "000001.SZ"], "period": "1d", "count": 5}, timeout=60)
    elapsed = time.time() - t0
    count = d.get("count", 0)
    data = d.get("data", {})
    print(f"   returned {count} stocks, elapsed={elapsed:.1f}s")
    for sid, bars in data.items():
        if bars:
            last = bars[-1]
            print(f"   {sid}: {len(bars)} bars, last={last.get('date','?')} close={last.get('close','?')}")
    results["klines"] = "OK" if count > 0 else "EMPTY"
except Exception as e:
    print(f"   FAIL: {e}")
    results["klines"] = "FAIL"

# 4. Quotes
print("\n4. Quotes (600519.SH, 000001.SZ)...")
t0 = time.time()
try:
    d = post("/quote", {"codes": ["600519.SH", "000001.SZ"]}, timeout=30)
    elapsed = time.time() - t0
    count = d.get("count", 0)
    data = d.get("data", {})
    print(f"   returned {count} quotes, elapsed={elapsed:.1f}s")
    for sid, q in data.items():
        print(f"   {sid}: price={q.get('price','?')} prevClose={q.get('prevClose','?')}")
    results["quotes"] = "OK" if count > 0 else "EMPTY"
except Exception as e:
    print(f"   FAIL: {e}")
    results["quotes"] = "FAIL"

# 5. Multi-source cascade
print("\n5. Multi-source cascade (sector_data.py)...")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from data_providers.sector_data import fetch_sector_members_multi, fetch_zt_pool_multi
    members, src = fetch_sector_members_multi("医药生物", {})
    print(f"   fetch_sector_members_multi('医药生物'): {len(members)} members, source={src}")
    results["cascade_members"] = f"OK({src})" if members else "FAIL"

    from datetime import datetime
    today = datetime.now().strftime("%Y%m%d")
    zt = fetch_zt_pool_multi(date=today, use_previous=True)
    zt_stocks = zt.get("stocks", [])
    print(f"   fetch_zt_pool_multi({today}): {len(zt_stocks)} stocks, source={zt.get('source','?')}")
    results["cascade_zt"] = f"OK({zt.get('source','?')})" if zt_stocks else "EMPTY"
except Exception as e:
    import traceback
    print(f"   FAIL: {e}")
    traceback.print_exc()
    results["cascade"] = "FAIL"

# Summary
print("\n" + "=" * 50)
print("SUMMARY")
print("=" * 50)
all_ok = True
for key, val in results.items():
    mark = "✅" if "OK" in val else ("⚠️" if val in ("EMPTY", "DEGRADED") else "❌")
    print(f"  {mark} {key}: {val}")
    if "FAIL" in val:
        all_ok = False
print()
if all_ok:
    print("All critical tests passed.")
else:
    print("Some tests failed - see details above.")
