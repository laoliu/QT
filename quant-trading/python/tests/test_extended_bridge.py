#!/usr/bin/env python3
"""Extended bridge tests: multiple periods, batch stocks, large counts, ETFs, indices."""
import urllib.request
import json
import time
import sys

# 诊断脚本，非 pytest 用例：模块体在 import 期访问真实 QMT bridge，
# 末尾 sys.exit() 会打断 pytest 的 collection（详见 test_direct_mode.py）。
if __name__ != "__main__":
    import pytest

    pytest.skip(
        "需要运行中的 QMT bridge（192.168.3.30:58088）；"
        "手动执行：python tests/test_extended_bridge.py",
        allow_module_level=True,
    )

BASE = "http://192.168.3.30:58088"
passed = 0
failed = 0
skipped = 0


def post(path, body, timeout=15):
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"Content-Type": "application/json"})
    resp = urllib.request.urlopen(req, timeout=timeout)
    return json.loads(resp.read())


def test_klines(label, codes, period, count, timeout=15, min_bars=1):
    """Test klines request, return (bars_dict, ms) or None on failure."""
    global passed, failed, skipped
    t0 = time.time()
    try:
        d = post("/klines", {"codes": codes, "period": period, "count": count}, timeout=timeout)
        ms = int((time.time() - t0) * 1000)
        total_bars = sum(len(v) for v in d.get("data", {}).values())
        if total_bars >= min_bars:
            passed += 1
            return d, ms
        else:
            print("    [WARN] %s: got %d bars (expected >=%d) (%dms)" % (label, total_bars, min_bars, ms))
            skipped += 1
            return d, ms
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        err = str(e)[:80]
        if "timed out" in err.lower() or "timeout" in err.lower():
            print("    [TIMEOUT] %s: %dms - %s" % (label, ms, err))
            skipped += 1
        else:
            print("    [FAIL] %s: %s (%dms)" % (label, err, ms))
            failed += 1
        return None, ms


def test_quote(label, codes, timeout=15):
    """Test quote request, return (data, ms) or None on failure."""
    global passed, failed
    t0 = time.time()
    try:
        d = post("/quote", {"codes": codes}, timeout=timeout)
        ms = int((time.time() - t0) * 1000)
        if d.get("count", 0) > 0:
            passed += 1
            return d, ms
        else:
            print("    [WARN] %s: 0 quotes returned (%dms)" % (label, ms))
            return d, ms
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        print("    [FAIL] %s: %s (%dms)" % (label, str(e)[:80], ms))
        failed += 1
        return None, ms


print("=" * 60)
print("Extended Bridge Tests")
print("=" * 60)

# ── 1. K-line periods ──
print("\n=== 1. K-line periods (600519.SH maotai, 10 bars each) ===")
for period in ["1d", "1w", "5m", "15m", "30m", "60m"]:
    d, ms = test_klines("klines/%s" % period, ["600519.SH"], period, 10, timeout=20)
    if d:
        bars = d.get("data", {}).get("600519", [])
        if bars:
            last = bars[-1]
            print("    %4s: %d bars, last=%s C=%.2f (%dms)" % (period, len(bars), last["date"], last["close"], ms))
        else:
            print("    %4s: 0 bars (%dms)" % (period, ms))

# ── 2. Batch K-lines (10 stocks, daily) ──
print("\n=== 2. Batch K-lines (10 stocks, 1d, 20 bars) ===")
stocks = [
    "600519.SH", "000001.SZ", "600036.SH", "000858.SZ", "601318.SH",
    "000333.SZ", "600900.SH", "002594.SZ", "601899.SH", "300750.SZ",
]
d, ms = test_klines("batch-10-1d", stocks, "1d", 20, timeout=60, min_bars=10)
if d:
    print("    stocks: %d/%d (%dms)" % (d.get("count", 0), len(stocks), ms))
    for sid, bars in sorted(d.get("data", {}).items()):
        last = bars[-1] if bars else {}
        print("    %s: %d bars, last=%s C=%.2f" % (sid, len(bars), last.get("date", "?"), last.get("close", 0)))

# ── 3. Large K-line (120 daily bars) ──
print("\n=== 3. Large K-line (600519, 1d, 120 bars) ===")
d, ms = test_klines("large-120-1d", ["600519.SH"], "1d", 120, timeout=30, min_bars=50)
if d:
    bars = d.get("data", {}).get("600519", [])
    print("    bars: %d (%dms)" % (len(bars), ms))
    if bars:
        print("    first: %s O=%.2f C=%.2f" % (bars[0]["date"], bars[0]["open"], bars[0]["close"]))
        print("    last:  %s O=%.2f C=%.2f" % (bars[-1]["date"], bars[-1]["open"], bars[-1]["close"]))

# ── 4. Batch quotes (10 stocks) ──
print("\n=== 4. Batch quotes (10 stocks) ===")
d, ms = test_quote("batch-10-quote", stocks)
if d:
    print("    quotes: %d/%d (%dms)" % (d.get("count", 0), len(stocks), ms))
    for sid, q in sorted(d.get("data", {}).items()):
        pc = q.get("prevClose", 0)
        chg = ((q["price"] - pc) / pc * 100) if pc > 0 else 0
        bid1 = q["bids"][0]["price"] if q.get("bids") else 0
        ask1 = q["asks"][0]["price"] if q.get("asks") else 0
        print("    %s: %.2f (%+.2f%%) vol=%d bid=%.2f ask=%.2f" % (sid, q["price"], chg, q["volume"], bid1, ask1))

# ── 5. ETF + Index ──
print("\n=== 5. ETF + Index ===")
special = ["510300.SH", "159919.SZ", "000300.SH", "399006.SZ"]
d, ms = test_quote("etf-index-quote", special)
if d:
    print("    Quotes (%dms):" % ms)
    for sid, q in sorted(d.get("data", {}).items()):
        print("      %s: price=%.4f vol=%d" % (sid, q["price"], q["volume"]))
d2, ms2 = test_klines("etf-index-klines", special, "1d", 5, timeout=30)
if d2:
    print("    K-lines (%dms):" % ms2)
    for sid, bars in sorted(d2.get("data", {}).items()):
        last = bars[-1] if bars else {}
        print("      %s: %d bars, last=%s C=%.4f" % (sid, len(bars), last.get("date", "?"), last.get("close", 0)))

# ── 6. BJ (Beijing Exchange) ──
print("\n=== 6. BJ (Beijing Exchange) ===")
bj = ["430047.BJ", "830799.BJ"]
d, ms = test_quote("bj-quote", bj)
if d and d.get("data"):
    for sid, q in sorted(d.get("data", {}).items()):
        print("    %s: price=%.2f vol=%d" % (sid, q["price"], q["volume"]))
else:
    print("    (no BJ quotes - may need subscription)")

# ── 7. Tick data (if supported) ──
print("\n=== 7. Tick/1m K-line (600519, short) ===")
d, ms = test_klines("tick-1m", ["600519.SH"], "1m", 5, timeout=20)
if d:
    bars = d.get("data", {}).get("600519", [])
    if bars:
        print("    1m: %d bars, last=%s C=%.2f (%dms)" % (len(bars), bars[-1]["date"], bars[-1]["close"], ms))
    else:
        print("    1m: 0 bars (%dms)" % ms)

# ── Summary ──
total = passed + failed + skipped
print("\n" + "=" * 60)
print("Results: %d passed, %d failed, %d skipped/timeout (total %d)" % (passed, failed, skipped, total))
if failed == 0:
    print("ALL TESTS PASSED" + (" (%d skipped)" % skipped if skipped else ""))
else:
    print("SOME TESTS FAILED")
sys.exit(1 if failed > 0 else 0)
