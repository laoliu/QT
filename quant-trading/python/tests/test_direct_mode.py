#!/usr/bin/env python3
"""
Test direct HTTP mode — validates the exact same request shapes that
remote-qmt-broker.ts and market-data-provider.ts send in direct mode.

This replaces the old RECEIVER.py polling path. Tests:
  1. POST /trading {method: "connect", params: {...}}
  2. POST /trading {method: "query_account", params: {}}
  3. POST /trading {method: "query_positions", params: {}}
  4. POST /trading {method: "query_orders", params: {}}
  5. POST /trading {method: "subscribe_quote", params: {stock_codes: [...]}}
  6. POST /trading {method: "unsubscribe_quote", params: {stock_codes: [...]}}
  7. GET  /trading/events  (event polling — replaces RECEIVER.py push)
  8. POST /quote   {codes: [...]}  (QMTBridgeProvider direct)
  9. POST /klines  {codes: [...], period: "1d", count: 10}  (QMTBridgeProvider direct)
  10. POST /trading {method: "disconnect", params: {}}
"""

import urllib.request
import json
import sys
import time

# 这个文件是**诊断脚本**而不是 pytest 用例：整个模块体在 import 期就向
# 真实 QMT bridge 发 HTTP 请求，末尾还 sys.exit()。被 pytest 收集时那句
# sys.exit 会让整个 collection 直接 INTERNALERROR，全量套件必须靠
# --ignore 才能跑完。这里在 import 期就跳过，脚本主体一行都不执行；
# 手动跑 `python tests/test_direct_mode.py` 时 __name__ == "__main__"，
# 行为与原来完全一致。
if __name__ != "__main__":
    import pytest

    pytest.skip(
        "需要运行中的 QMT bridge（192.168.3.30:18888）；"
        "手动执行：python tests/test_direct_mode.py",
        allow_module_level=True,
    )

BASE = "http://192.168.3.30:18888"
passed = 0
failed = 0


def post(path, body, timeout=15):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        BASE + path, data=data, headers={"Content-Type": "application/json"}
    )
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())


def get(path, timeout=10):
    req = urllib.request.Request(BASE + path)
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())


def run_test(label, fn):
    global passed, failed
    t0 = time.time()
    try:
        result = fn()
        ms = int((time.time() - t0) * 1000)
        passed += 1
        return result, ms
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        print("  [FAIL] %s: %s (%dms)" % (label, str(e)[:120], ms))
        failed += 1
        return None, ms


print("=" * 60)
print("Direct HTTP Mode Tests (replaces RECEIVER.py polling)")
print("=" * 60)

# ── 1. Trading: connect ──
print("\n--- 1. POST /trading {method: connect} ---")
result, ms = run_test("connect", lambda: post("/trading", {
    "method": "connect",
    "params": {
        "mini_qmt_path": "D:\\st_lc",
        "account_id": "",
        "account_type": "STOCK",
    },
}))
if result:
    ok = result.get("ok", False)
    print("  ok=%s (%dms)" % (ok, ms))

# ── 2. Trading: query_account ──
print("\n--- 2. POST /trading {method: query_account} ---")
result, ms = run_test("query_account", lambda: post("/trading", {
    "method": "query_account",
    "params": {},
}))
if result:
    ok = result.get("ok", False)
    data = result.get("data", {})
    print("  ok=%s (%dms)" % (ok, ms))
    if ok and data:
        print("  cash=%.2f total_asset=%.2f market_value=%.2f" % (
            data.get("cash", 0),
            data.get("total_asset", 0),
            data.get("market_value", 0),
        ))

# ── 3. Trading: query_positions ──
print("\n--- 3. POST /trading {method: query_positions} ---")
result, ms = run_test("query_positions", lambda: post("/trading", {
    "method": "query_positions",
    "params": {},
}))
if result:
    ok = result.get("ok", False)
    data = result.get("data", [])
    print("  ok=%s positions=%d (%dms)" % (ok, len(data) if isinstance(data, list) else 0, ms))
    if ok and isinstance(data, list):
        for pos in data[:5]:
            print("    %s: vol=%s avg_price=%s market_value=%s" % (
                pos.get("stock_code", "?"),
                pos.get("volume", "?"),
                pos.get("avg_price", "?"),
                pos.get("market_value", "?"),
            ))

# ── 4. Trading: query_orders ──
print("\n--- 4. POST /trading {method: query_orders} ---")
result, ms = run_test("query_orders", lambda: post("/trading", {
    "method": "query_orders",
    "params": {},
}))
if result:
    ok = result.get("ok", False)
    data = result.get("data", [])
    print("  ok=%s orders=%d (%dms)" % (ok, len(data) if isinstance(data, list) else 0, ms))
    if ok and isinstance(data, list):
        for o in data[:3]:
            print("    order_id=%s stock=%s status=%s" % (
                o.get("order_id", "?"),
                o.get("stock_code", "?"),
                o.get("order_status", "?"),
            ))

# ── 5. Trading: subscribe_quote ──
print("\n--- 5. POST /trading {method: subscribe_quote} ---")
result, ms = run_test("subscribe_quote", lambda: post("/trading", {
    "method": "subscribe_quote",
    "params": {"stock_codes": ["600519.SH", "000001.SZ"]},
}))
if result:
    print("  ok=%s (%dms)" % (result.get("ok", False), ms))

# ── 6. GET /trading/events (event polling) ──
print("\n--- 6. GET /trading/events (replaces RECEIVER.py push) ---")
result, ms = run_test("trading_events", lambda: get("/trading/events"))
if result:
    events = result.get("events", [])
    count = result.get("count", 0)
    print("  events=%d count=%d (%dms)" % (len(events), count, ms))
    for evt in events[:3]:
        print("    event=%s data=%s" % (evt.get("event", "?"), str(evt.get("data", ""))[:80]))

# ── 7. Trading: unsubscribe_quote ──
print("\n--- 7. POST /trading {method: unsubscribe_quote} ---")
result, ms = run_test("unsubscribe_quote", lambda: post("/trading", {
    "method": "unsubscribe_quote",
    "params": {"stock_codes": ["600519.SH", "000001.SZ"]},
}))
if result:
    print("  ok=%s (%dms)" % (result.get("ok", False), ms))

# ── 8. Data: POST /quote (QMTBridgeProvider.directCall) ──
print("\n--- 8. POST /quote (QMTBridgeProvider direct mode) ---")
result, ms = run_test("direct_quote", lambda: post("/quote", {
    "codes": ["600519.SH", "000001.SZ"],
}))
if result:
    data = result.get("data", {})
    count = result.get("count", 0)
    print("  count=%d (%dms)" % (count, ms))
    for sid, q in sorted(data.items()):
        print("    %s: price=%.2f prevClose=%.2f vol=%d" % (
            sid, q.get("price", 0), q.get("prevClose", 0), q.get("volume", 0),
        ))

# ── 9. Data: POST /klines (QMTBridgeProvider direct mode) ──
print("\n--- 9. POST /klines (QMTBridgeProvider direct mode) ---")
for period in ["1d", "1w", "60m"]:
    result, ms = run_test("direct_klines_%s" % period, lambda p=period: post("/klines", {
        "codes": ["600519.SH"],
        "period": p,
        "count": 5,
    }))
    if result:
        bars = result.get("data", {}).get("600519", [])
        last = bars[-1] if bars else {}
        print("  %s: %d bars, last=%s C=%.2f (%dms)" % (
            period, len(bars), last.get("date", "?"), last.get("close", 0), ms,
        ))

# ── 10. Trading: disconnect ──
print("\n--- 10. POST /trading {method: disconnect} ---")
result, ms = run_test("disconnect", lambda: post("/trading", {
    "method": "disconnect",
    "params": {},
}))
if result:
    print("  ok=%s (%dms)" % (result.get("ok", False), ms))

# ── Summary ──
total = passed + failed
print("\n" + "=" * 60)
print("Results: %d passed, %d failed (total %d)" % (passed, failed, total))
if failed == 0:
    print("ALL DIRECT MODE TESTS PASSED")
    print("RECEIVER.py polling can be fully replaced by direct HTTP!")
else:
    print("SOME TESTS FAILED")
sys.exit(1 if failed > 0 else 0)
