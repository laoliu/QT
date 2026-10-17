# coding: utf-8
"""
Mock bridge server for local Mac testing.

Simulates qmt_data_bridge.py HTTP endpoints with fake data,
so Mac-side scripts (stock_detail.py, refresh_klines.py, etc.)
can be tested without a Windows machine.

Usage:
  # Terminal 1: start mock server
  python3 tests/test_bridge_mock_server.py

  # Terminal 2: test Mac scripts
  WINDOWS_XTDATA_URL=http://127.0.0.1:18888 python3 scripts/stock_detail.py 600519

  # Or run automated tests
  python3 tests/test_bridge_mock_server.py --selftest
"""

import json
import sys
import time
import threading
import argparse
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse
from urllib.request import Request, urlopen


# ========== Mock Data ==========

def _mock_klines(codes, period, count):
    """Generate fake K-line data for testing."""
    import random
    result = {}
    for code in codes:
        stock_id = code.split(".")[0] if "." in code else code
        bars = []
        base_price = random.uniform(10, 100)
        for i in range(min(count, 30)):
            day = 20260301 + i
            p = base_price * (1 + random.uniform(-0.03, 0.03))
            bars.append({
                "date": str(day),
                "open": round(p * 0.99, 2),
                "high": round(p * 1.02, 2),
                "low": round(p * 0.98, 2),
                "close": round(p, 2),
                "volume": random.randint(10000, 500000),
            })
            base_price = p
        result[stock_id] = bars
    return result


def _mock_quote(codes):
    """Generate fake real-time quote data for testing."""
    import random
    result = {}
    for code in codes:
        stock_id = code.split(".")[0] if "." in code else code
        price = round(random.uniform(10, 100), 2)
        result[stock_id] = {
            "price": price,
            "open": round(price * 0.99, 2),
            "high": round(price * 1.02, 2),
            "low": round(price * 0.98, 2),
            "prevClose": round(price * 1.01, 2),
            "volume": random.randint(100000, 5000000),
            "amount": round(random.uniform(1e7, 5e8), 2),
            "bids": [
                {"price": round(price - 0.01 * i, 2), "volume": random.randint(100, 5000)}
                for i in range(5)
            ],
            "asks": [
                {"price": round(price + 0.01 * (i + 1), 2), "volume": random.randint(100, 5000)}
                for i in range(5)
            ],
        }
    return result


_trading_events = []


# ========== Mock HTTP Server ==========

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


class MockBridgeHandler(BaseHTTPRequestHandler):
    def _cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json_response(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/health":
            self._json_response(200, {
                "status": "ok",
                "xtdata": True,
                "trading": True,
                "version": 1,
                "error": None,
                "mock": True,
            })
        elif path == "/trading/events":
            global _trading_events
            events = _trading_events[:]
            _trading_events = []
            self._json_response(200, {"events": events, "count": len(events)})
        else:
            self.send_error(404)

    def do_POST(self):
        path = urlparse(self.path).path
        content_len = int(self.headers.get("Content-Length", 0))
        if content_len == 0:
            self._json_response(400, {"error": "empty body"})
            return

        try:
            raw = self.rfile.read(content_len)
            req_body = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._json_response(400, {"error": "JSON parse failed: %s" % e})
            return

        if path == "/klines":
            codes = req_body.get("codes", [])
            period = req_body.get("period", "1d")
            count = int(req_body.get("count", 120))
            data = _mock_klines(codes, period, count)
            self._json_response(200, {"status": "ok", "data": data, "count": len(data)})
            print("[mock] /klines: %d codes, period=%s -> %d results" % (len(codes), period, len(data)))

        elif path == "/quote":
            codes = req_body.get("codes", [])
            data = _mock_quote(codes)
            self._json_response(200, {"status": "ok", "data": data, "count": len(data)})
            print("[mock] /quote: %d codes -> %d results" % (len(codes), len(data)))

        elif path == "/trading":
            method = req_body.get("method", "")
            params = req_body.get("params", {})
            if method == "connect":
                self._json_response(200, {"ok": True, "data": {"status": "connected", "account_id": "mock_account"}})
            elif method == "query_account":
                self._json_response(200, {"ok": True, "data": {
                    "account_id": "mock_account",
                    "cash": 100000.0,
                    "frozen_cash": 0.0,
                    "total_asset": 250000.0,
                    "market_value": 150000.0,
                    "profit": 5000.0,
                    "profit_ratio": 0.02,
                }})
            elif method == "query_positions":
                self._json_response(200, {"ok": True, "data": [
                    {"stock_code": "600519.SH", "volume": 100, "can_use_volume": 100, "frozen_volume": 0,
                     "avg_price": 1800.0, "market_value": 185000.0, "stock_name": "贵州茅台"},
                ]})
            elif method == "place_order":
                self._json_response(200, {"ok": True, "data": {"broker_order_id": "mock_%d" % int(time.time())}})
                # Simulate fill event
                _trading_events.append({"event": "fill", "data": {
                    "order_id": "mock_%d" % int(time.time()),
                    "stock_code": params.get("stock_code", ""),
                    "traded_price": params.get("price", 0),
                    "traded_volume": params.get("volume", 0),
                    "traded_amount": params.get("price", 0) * params.get("volume", 0),
                    "commission": 0.5,
                    "time": time.strftime("%H:%M:%S"),
                }})
            else:
                self._json_response(200, {"ok": True, "data": {}})
            print("[mock] /trading: method=%s" % method)

        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        if "/health" in str(args[0]) if args else "":
            return
        print("[mock] " + (fmt % args))


# ========== Self-Test ==========

def _selftest(port):
    """Run automated tests against the mock server."""
    base = "http://127.0.0.1:%d" % port
    passed = 0
    failed = 0

    def _test(name, method, path, body=None, check=None):
        nonlocal passed, failed
        url = base + path
        try:
            if body:
                data = json.dumps(body).encode("utf-8")
                req = Request(url, data=data, headers={"Content-Type": "application/json"})
            else:
                req = Request(url)
            resp = urlopen(req, timeout=5)
            result = json.loads(resp.read().decode("utf-8"))
            if check and not check(result):
                print("  FAIL %s: unexpected response %s" % (name, json.dumps(result)[:200]))
                failed += 1
            else:
                print("  PASS %s" % name)
                passed += 1
        except Exception as e:
            print("  FAIL %s: %s" % (name, e))
            failed += 1

    print("\n=== Bridge Mock Self-Test ===\n")

    _test("GET /health",
          "GET", "/health",
          check=lambda r: r.get("status") == "ok" and r.get("mock") is True)

    _test("POST /klines (single)",
          "POST", "/klines",
          body={"codes": ["600519.SH"], "period": "1d", "count": 10},
          check=lambda r: r.get("count") == 1 and "600519" in r.get("data", {}))

    _test("POST /klines (batch)",
          "POST", "/klines",
          body={"codes": ["600519.SH", "000001.SZ", "300750.SZ"], "period": "1d", "count": 5},
          check=lambda r: r.get("count") == 3)

    _test("POST /quote",
          "POST", "/quote",
          body={"codes": ["600519.SH"]},
          check=lambda r: r.get("count") == 1 and r["data"]["600519"]["price"] > 0)

    _test("POST /trading connect",
          "POST", "/trading",
          body={"method": "connect", "params": {}},
          check=lambda r: r.get("ok") is True)

    _test("POST /trading query_account",
          "POST", "/trading",
          body={"method": "query_account", "params": {}},
          check=lambda r: r.get("ok") is True and r["data"]["total_asset"] > 0)

    _test("POST /trading query_positions",
          "POST", "/trading",
          body={"method": "query_positions", "params": {}},
          check=lambda r: r.get("ok") is True and len(r["data"]) > 0)

    _test("POST /trading place_order",
          "POST", "/trading",
          body={"method": "place_order", "params": {"stock_code": "600519.SH", "price": 1800, "volume": 100}},
          check=lambda r: r.get("ok") is True and "broker_order_id" in r.get("data", {}))

    _test("GET /trading/events (after order)",
          "GET", "/trading/events",
          check=lambda r: r.get("count") >= 1 and r["events"][0]["event"] == "fill")

    _test("GET /trading/events (empty after drain)",
          "GET", "/trading/events",
          check=lambda r: r.get("count") == 0)

    print("\n=== Results: %d passed, %d failed ===" % (passed, failed))
    return failed == 0


# ========== Main ==========

def main():
    parser = argparse.ArgumentParser(description="Mock QMT Bridge Server for Mac testing")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18888)
    parser.add_argument("--selftest", action="store_true", help="Run self-test then exit")
    args = parser.parse_args()

    if args.selftest:
        # Start server in background, run tests, exit
        server = ThreadingHTTPServer((args.host, args.port), MockBridgeHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        print("Mock server started on http://%s:%d" % (args.host, args.port))
        time.sleep(0.3)
        ok = _selftest(args.port)
        server.shutdown()
        sys.exit(0 if ok else 1)

    # Interactive mode
    server = ThreadingHTTPServer((args.host, args.port), MockBridgeHandler)
    print("=" * 60)
    print("  QMT Bridge Mock Server (for Mac testing)")
    print("  http://%s:%d" % (args.host, args.port))
    print("")
    print("  Test with:")
    print("    WINDOWS_XTDATA_URL=http://127.0.0.1:%d python3 scripts/stock_detail.py 600519" % args.port)
    print("    curl http://127.0.0.1:%d/health" % args.port)
    print("=" * 60)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[mock] stopped")
        server.shutdown()


if __name__ == "__main__":
    main()
