#!/usr/bin/env python3
"""Test real Windows QMT bridge at 192.168.3.30:18888"""
import urllib.request
import json
import sys

# 诊断脚本，非 pytest 用例：模块体在 import 期访问真实 QMT bridge，
# 中途 sys.exit() 会打断 pytest 的 collection（详见 test_direct_mode.py）。
if __name__ != "__main__":
    import pytest

    pytest.skip(
        "需要运行中的 QMT bridge（192.168.3.30:18888）；"
        "手动执行：python tests/test_real_bridge.py",
        allow_module_level=True,
    )

BASE = "http://192.168.3.30:18888"

def get(path, timeout=5):
    resp = urllib.request.urlopen(BASE + path, timeout=timeout)
    return json.loads(resp.read())

def post(path, body, timeout=15):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())

passed = 0
failed = 0

# 1. Health check
print("=== 1. GET /health ===")
try:
    d = get("/health")
    print(json.dumps(d, indent=2, ensure_ascii=False))
    status = d.get("status")
    if status == "ok":
        print("=> PASS")
        passed += 1
    else:
        print("=> WARN: status=%s (xtdata may not be loaded)" % status)
        passed += 1  # still counts as reachable
except Exception as e:
    print("=> FAIL: %s" % e)
    print("  - Windows 防火墙是否放行 18888 端口？")
    print("  - qmt_data_bridge.py 是否已启动？")
    print("  - miniQMT 客户端是否已登录？")
    failed += 1
    print("\n连接失败，无法继续后续测试。")
    sys.exit(1)

# 2. K-lines - single stock
print("\n=== 2. POST /klines (ę́ 600519 近5日) ===")
try:
    d = post("/klines", {"codes": ["600519.SH"], "period": "1d", "count": 5})
    bars = d.get("data", {}).get("600519", [])
    print("count: %d" % len(bars))
    for b in bars:
        print("  %s  O:%.2f H:%.2f L:%.2f C:%.2f V:%.0f" % (
            b["date"], b["open"], b["high"], b["low"], b["close"], b["volume"]))
    if len(bars) > 0:
        print("=> PASS (%d bars)" % len(bars))
        passed += 1
    else:
        print("=> FAIL: no bars returned")
        failed += 1
except Exception as e:
    print("=> FAIL: %s" % e)
    failed += 1

# 3. K-lines - batch
print("\n=== 3. POST /klines (batch: 600519+000001 近3日) ===")
try:
    d = post("/klines", {"codes": ["600519.SH", "000001.SZ"], "period": "1d", "count": 3})
    total = d.get("count", 0)
    print("total bars: %d, stocks: %d" % (total, len(d.get("data", {}))))
    for sid, bars in d.get("data", {}).items():
        print("  %s: %d bars" % (sid, len(bars)))
    if total > 0:
        print("=> PASS")
        passed += 1
    else:
        print("=> FAIL: no data")
        failed += 1
except Exception as e:
    print("=> FAIL: %s" % e)
    failed += 1

# 4. Quote
print("\n=== 4. POST /quote (ę́+平安) ===")
try:
    d = post("/quote", {"codes": ["600519.SH", "000001.SZ"]})
    print("count: %d" % d.get("count", 0))
    for sid, q in d.get("data", {}).items():
        print("  %s: price=%.2f open=%.2f high=%.2f low=%.2f vol=%d" % (
            sid, q.get("price", 0), q.get("open", 0), q.get("high", 0),
            q.get("low", 0), q.get("volume", 0)))
        bids = q.get("bids", [])
        asks = q.get("asks", [])
        if bids:
            print("    bid1: %.2f x %d" % (bids[0]["price"], bids[0]["volume"]))
        if asks:
            print("    ask1: %.2f x %d" % (asks[0]["price"], asks[0]["volume"]))
    if d.get("count", 0) > 0:
        print("=> PASS")
        passed += 1
    else:
        print("=> FAIL: no quotes")
        failed += 1
except Exception as e:
    print("=> FAIL: %s" % e)
    failed += 1

# 5. Trading events
print("\n=== 5. GET /trading/events ===")
try:
    d = get("/trading/events")
    print("events: %d" % d.get("count", 0))
    for ev in d.get("events", []):
        print("  [%s] %s" % (ev.get("type"), json.dumps(ev.get("data", {}), ensure_ascii=False)))
    print("=> PASS")
    passed += 1
except Exception as e:
    print("=> FAIL: %s" % e)
    failed += 1

# Summary
print("\n" + "=" * 40)
print("RESULT: %d passed, %d failed" % (passed, failed))
if failed == 0:
    print("All bridge endpoints working!")
else:
    print("Some tests failed - check output above")
    sys.exit(1)
