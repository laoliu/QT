# coding: utf-8
#
# RECEIVER.py - Mac-side HTTP server for QMT push-mode data bridge
#
# Runs on Mac, receives data pushed from Windows QMT.
# Windows BRIDGE.py polls /pending for requests, fetches data via ContextInfo,
# then POSTs results back here.
#
# Usage:
#   python3 RECEIVER.py [--port 18889] [--host 0.0.0.0]
#
# API:
#   POST /request      <- Mac submits a data request, returns {"id": "..."}
#   GET  /pending       <- Windows polls for pending requests
#   POST /result/<id>   <- Windows pushes result for a request
#   GET  /result/<id>   <- Mac reads result (blocks until available or timeout)
#   GET  /health        <- Health check
#   GET  /status        <- Show all requests and their status
#
# Trading shortcuts (proxy to gateway, convenience for stock traders):
#   POST /buy           <- Quick buy order {code, volume, price?}
#   POST /sell          <- Quick sell order {code, volume, price?}
#   POST /cancel        <- Cancel order {order_id}
#   GET  /positions     <- Query current positions
#   GET  /account       <- Query account summary
#   GET  /orders        <- Query today's orders
#   GET  /quote/<code>  <- Real-time quote for a single stock
#   POST /quotes        <- Batch quotes {codes: ["600519", "000001"]}
#   GET  /trades        <- Query recent trades/fills
#   GET  /pnl           <- Query P&L summary
#
# Data bridge proxy (relay to gateway for Windows bridge polling):
#   GET/POST /quant/api/data-request    <- Data bridge request relay
#   GET/POST /quant/api/data-response   <- Data bridge response relay
#   GET/POST /quant/api/stocklist       <- Stock list management
#

import json
import os
import sys
import threading
import time
import argparse

try:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from socketserver import ThreadingMixIn
    from urllib.parse import urlparse, parse_qs
except ImportError:
    # Python 2 fallback (shouldn't happen on Mac)
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
    from SocketServer import ThreadingMixIn
    from urlparse import urlparse, parse_qs


class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    """Handle each request in a new thread so blocking /result waits
    don't block /pending polls from Windows."""
    daemon_threads = True

# ===================== CONFIG =====================

DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 18889
REQUEST_TIMEOUT = 60  # seconds to wait for result before timing out
REQUEST_EXPIRY = 300  # seconds before a request is cleaned up

# Gateway proxy — forward /quant/api/trading-* to the local OpenClaw gateway
GATEWAY_URL = "http://127.0.0.1:" + os.environ.get("OPENCLAW_GATEWAY_PORT", "18789")

# ===================== REQUEST STORE =====================

_lock = threading.Lock()
_requests = {}       # id -> {"id", "codes", "period", "count", "status", "created_at"}
_results = {}        # id -> {"data": ..., "received_at": ...}
_events = {}         # id -> threading.Event (for blocking GET /result/<id>)
_next_id = [0]       # mutable counter
_last_bridge_poll = [0.0]  # timestamp of last Windows /pending poll


def _new_request(codes, period, count):
    """Create a new data request, return its ID."""
    with _lock:
        _next_id[0] += 1
        req_id = "r%d_%d" % (_next_id[0], int(time.time()) % 100000)
        _requests[req_id] = {
            "id": req_id,
            "codes": codes,
            "period": period,
            "count": count,
            "status": "pending",
            "created_at": time.time(),
        }
        _events[req_id] = threading.Event()
        return req_id


def _get_pending():
    """Return list of pending requests."""
    with _lock:
        now = time.time()
        pending = []
        for req_id, req in list(_requests.items()):
            # Clean up expired requests
            if now - req["created_at"] > REQUEST_EXPIRY:
                req["status"] = "expired"
                continue
            if req["status"] == "pending":
                pending.append(req)
        return pending


def _submit_result(req_id, data):
    """Store result for a request and notify waiters."""
    with _lock:
        if req_id not in _requests:
            return False
        _results[req_id] = {
            "data": data,
            "received_at": time.time(),
        }
        _requests[req_id]["status"] = "completed"
        if req_id in _events:
            _events[req_id].set()
        return True


def _wait_for_result(req_id, timeout=REQUEST_TIMEOUT):
    """Block until result is available or timeout. Returns result or None."""
    evt = None
    with _lock:
        evt = _events.get(req_id)
        # Check if already completed
        if req_id in _results:
            return _results[req_id]
    if evt is None:
        return None
    evt.wait(timeout=timeout)
    with _lock:
        return _results.get(req_id)


def _get_status():
    """Return all requests and their status."""
    with _lock:
        now = time.time()
        items = []
        for req_id, req in sorted(_requests.items(), key=lambda x: x[1]["created_at"], reverse=True):
            age = now - req["created_at"]
            entry = dict(req)
            entry["age_seconds"] = round(age, 1)
            entry["has_result"] = req_id in _results
            items.append(entry)
        return items[:50]  # last 50


def _cleanup_old():
    """Remove old requests/results to prevent memory leak."""
    with _lock:
        now = time.time()
        expired_ids = [
            rid for rid, req in _requests.items()
            if now - req["created_at"] > REQUEST_EXPIRY * 2
        ]
        for rid in expired_ids:
            _requests.pop(rid, None)
            _results.pop(rid, None)
            evt = _events.pop(rid, None)
            if evt:
                evt.set()  # unblock any waiters


# ===================== GATEWAY PROXY =====================

def _proxy_to_gateway(method, path, body=None, timeout=15):
    """Forward a request to the local OpenClaw gateway and return (status, body_bytes)."""
    import urllib.request
    import urllib.error
    url = GATEWAY_URL + path
    try:
        if body is not None:
            if isinstance(body, str):
                body = body.encode("utf-8")
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
        else:
            req = urllib.request.Request(url)
        req.get_method = lambda: method
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:
        err = json.dumps({"error": "gateway proxy failed: %s" % e}).encode("utf-8")
        return 502, err


# ===================== HTTP HANDLER =====================

class BridgeHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the push-mode bridge receiver."""

    def log_message(self, fmt, *args):
        # Custom log format
        print("[receiver] %s %s" % (self.address_string(), fmt % args))

    def _send_json(self, code, data):
        body = json.dumps(data, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return b""
        return self.rfile.read(length)

    def _proxy_response(self, status, body):
        """Send a raw proxy response back to the client."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path

        # GET /health
        if path == "/health":
            pending = _get_pending()
            last_poll = _last_bridge_poll[0]
            bridge_age = round(time.time() - last_poll, 1) if last_poll > 0 else None
            self._send_json(200, {
                "status": "ok",
                "mode": "push-receiver",
                "pending_requests": len(pending),
                "bridge_connected": bridge_age is not None and bridge_age < 30,
                "bridge_last_poll_age": bridge_age,
            })
            return

        # GET /pending — Windows polls this
        if path == "/pending":
            _last_bridge_poll[0] = time.time()
            pending = _get_pending()
            self._send_json(200, {
                "requests": pending,
                "count": len(pending),
            })
            return

        # GET /result/<id> — Mac reads result (blocks until available)
        if path.startswith("/result/"):
            req_id = path[len("/result/"):]
            if not req_id:
                self._send_json(400, {"error": "missing request id"})
                return

            # Parse timeout from query string
            qs = parse_qs(urlparse(self.path).query)
            timeout = int(qs.get("timeout", [str(REQUEST_TIMEOUT)])[0])

            with _lock:
                if req_id not in _requests:
                    self._send_json(404, {"error": "request not found", "id": req_id})
                    return

            result = _wait_for_result(req_id, timeout=timeout)
            if result is None:
                self._send_json(408, {
                    "error": "timeout waiting for result",
                    "id": req_id,
                    "timeout": timeout,
                })
                return

            self._send_json(200, {
                "status": "ok",
                "id": req_id,
                "data": result["data"],
                "received_at": result["received_at"],
            })
            return

        # GET /status — show all requests
        if path == "/status":
            items = _get_status()
            self._send_json(200, {
                "requests": items,
                "total": len(items),
            })
            return

        # ── Trading shortcuts: GET endpoints ──────────────────────

        # GET /positions — query current positions
        if path == "/positions":
            status, body = _proxy_to_gateway("GET", "/quant/api/portfolio")
            self._proxy_response(status, body)
            return

        # GET /account — query account summary
        if path == "/account":
            status, body = _proxy_to_gateway("GET", "/quant/api/portfolio")
            self._proxy_response(status, body)
            return

        # GET /orders — query today's orders
        if path == "/orders":
            qs = urlparse(self.path).query
            proxy_path = "/quant/api/orders"
            if qs:
                proxy_path += "?" + qs
            status, body = _proxy_to_gateway("GET", proxy_path)
            self._proxy_response(status, body)
            return

        # GET /trades — query recent trades/fills
        if path == "/trades":
            qs = urlparse(self.path).query
            proxy_path = "/quant/api/trades"
            if qs:
                proxy_path += "?" + qs
            status, body = _proxy_to_gateway("GET", proxy_path)
            self._proxy_response(status, body)
            return

        # GET /pnl — query P&L summary
        if path == "/pnl":
            status, body = _proxy_to_gateway("GET", "/quant/api/pnl")
            self._proxy_response(status, body)
            return

        # GET /quote/<code> — real-time quote for a single stock
        if path.startswith("/quote/"):
            code = path[len("/quote/"):]
            if not code:
                self._send_json(400, {"error": "missing stock code"})
                return
            proxy_path = "/quant/api/quote?symbol=%s" % code
            status, body = _proxy_to_gateway("GET", proxy_path)
            self._proxy_response(status, body)
            return

        # GET /signals — query recent strategy signals
        if path == "/signals":
            qs = urlparse(self.path).query
            proxy_path = "/quant/api/signals"
            if qs:
                proxy_path += "?" + qs
            status, body = _proxy_to_gateway("GET", proxy_path)
            self._proxy_response(status, body)
            return

        # GET /alerts — query active price alerts
        if path == "/alerts":
            qs = urlparse(self.path).query
            proxy_path = "/quant/api/price-alerts"
            if qs:
                proxy_path += "?" + qs
            status, body = _proxy_to_gateway("GET", proxy_path)
            self._proxy_response(status, body)
            return

        # GET /confirmations — pending order confirmations
        if path == "/confirmations":
            status, body = _proxy_to_gateway("GET", "/quant/api/confirmations")
            self._proxy_response(status, body)
            return

        # GET /engine/status — live engine status
        if path == "/engine/status":
            status, body = _proxy_to_gateway("GET", "/quant/api/live/status")
            self._proxy_response(status, body)
            return

        # Proxy /quant/api/* to local gateway (trading, data-bridge, stocklist, etc.)
        if path.startswith("/quant/api/"):
            qs = urlparse(self.path).query
            proxy_path = path + ("?" + qs if qs else "")
            status, body = _proxy_to_gateway("GET", proxy_path)
            self._proxy_response(status, body)
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = urlparse(self.path).path
        body = self._read_body()

        # POST /request — Mac submits a data request
        if path == "/request":
            if not body:
                self._send_json(400, {"error": "empty body"})
                return
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception as e:
                self._send_json(400, {"error": "JSON parse error: %s" % e})
                return

            codes = data.get("codes", [])
            if not codes or not isinstance(codes, list):
                self._send_json(400, {"error": "codes required (list of stock codes)"})
                return

            period = data.get("period", "1d")
            count = int(data.get("count", 120))

            req_id = _new_request(codes, period, count)
            print("[receiver] new request %s: %d codes, period=%s, count=%d"
                  % (req_id, len(codes), period, count))

            self._send_json(200, {
                "status": "ok",
                "id": req_id,
                "message": "request queued, poll GET /result/%s for data" % req_id,
            })
            return

        # POST /result/<id> — Windows pushes result
        if path.startswith("/result/"):
            req_id = path[len("/result/"):]
            if not req_id:
                self._send_json(400, {"error": "missing request id"})
                return
            if not body:
                self._send_json(400, {"error": "empty body"})
                return
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception as e:
                self._send_json(400, {"error": "JSON parse error: %s" % e})
                return

            ok = _submit_result(req_id, data)
            if not ok:
                self._send_json(404, {"error": "request not found", "id": req_id})
                return

            count = 0
            if isinstance(data, dict):
                count = len(data)
            print("[receiver] result for %s: %d items" % (req_id, count))

            self._send_json(200, {
                "status": "ok",
                "id": req_id,
                "message": "result stored",
            })
            return

        # POST /klines — convenience: submit + wait (synchronous)
        if path == "/klines":
            if not body:
                self._send_json(400, {"error": "empty body"})
                return
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception as e:
                self._send_json(400, {"error": "JSON parse error: %s" % e})
                return

            codes = data.get("codes", [])
            if not codes or not isinstance(codes, list):
                self._send_json(400, {"error": "codes required"})
                return

            period = data.get("period", "1d")
            count = int(data.get("count", 120))
            timeout = int(data.get("timeout", REQUEST_TIMEOUT))

            req_id = _new_request(codes, period, count)
            print("[receiver] sync request %s: %d codes, period=%s, count=%d, timeout=%ds"
                  % (req_id, len(codes), period, count, timeout))

            # Block until Windows pushes the result
            result = _wait_for_result(req_id, timeout=timeout)
            if result is None:
                self._send_json(408, {
                    "error": "timeout: Windows QMT did not respond in %ds" % timeout,
                    "id": req_id,
                    "hint": "Check that BRIDGE.py is running in QMT and polling this server",
                })
                return

            kline_data = result.get("data", {})
            # Bridge pushes {data: {stock: bars}, count, source} — unwrap if nested
            if isinstance(kline_data, dict) and "data" in kline_data and "source" in kline_data:
                kline_data = kline_data["data"]
            self._send_json(200, {
                "status": "ok",
                "data": kline_data,
                "count": len(kline_data) if isinstance(kline_data, dict) else 0,
                "id": req_id,
            })
            return

        # ── Trading shortcuts: POST endpoints ─────────────────────

        # POST /buy — quick buy order
        if path == "/buy":
            self._handle_quick_order(body, "buy")
            return

        # POST /sell — quick sell order
        if path == "/sell":
            self._handle_quick_order(body, "sell")
            return

        # POST /cancel — cancel order
        if path == "/cancel":
            if not body:
                self._send_json(400, {"error": "empty body"})
                return
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception as e:
                self._send_json(400, {"error": "JSON parse error: %s" % e})
                return
            order_id = data.get("order_id", "")
            if not order_id:
                self._send_json(400, {"error": "order_id required"})
                return
            # Proxy cancel to gateway
            cancel_body = json.dumps({
                "action": "cancel",
                "orderId": order_id,
            }).encode("utf-8")
            status, resp_body = _proxy_to_gateway("DELETE",
                "/quant/api/orders?id=%s" % order_id)
            self._proxy_response(status, resp_body)
            return

        # POST /quotes — batch quotes for multiple stocks
        if path == "/quotes":
            if not body:
                self._send_json(400, {"error": "empty body"})
                return
            try:
                data = json.loads(body.decode("utf-8"))
            except Exception as e:
                self._send_json(400, {"error": "JSON parse error: %s" % e})
                return
            codes = data.get("codes", [])
            if not codes or not isinstance(codes, list):
                self._send_json(400, {"error": "codes required (list of stock codes)"})
                return
            # Fetch quotes in parallel via threads
            quotes = {}
            errors = {}
            threads = []
            results_lock = threading.Lock()

            def _fetch_quote(code):
                try:
                    proxy_path = "/quant/api/quote?symbol=%s" % code
                    st, bd = _proxy_to_gateway("GET", proxy_path, timeout=10)
                    if st == 200:
                        parsed = json.loads(bd.decode("utf-8"))
                        with results_lock:
                            quotes[code] = parsed.get("data", parsed)
                    else:
                        with results_lock:
                            errors[code] = "HTTP %d" % st
                except Exception as e:
                    with results_lock:
                        errors[code] = str(e)

            for code in codes[:50]:  # limit to 50 codes per batch
                t = threading.Thread(target=_fetch_quote, args=(code,))
                t.start()
                threads.append(t)
            for t in threads:
                t.join(timeout=15)

            self._send_json(200, {
                "status": "ok",
                "quotes": quotes,
                "errors": errors if errors else None,
                "count": len(quotes),
            })
            return

        # POST /alert — set a price alert
        if path == "/alert":
            if not body:
                self._send_json(400, {"error": "empty body"})
                return
            status, resp_body = _proxy_to_gateway("POST",
                "/quant/api/price-alerts", body=body)
            self._proxy_response(status, resp_body)
            return

        # POST /engine/<action> — engine control (start/stop/pause/resume)
        if path.startswith("/engine/"):
            action = path[len("/engine/"):]
            if action not in ("start", "stop", "pause", "resume"):
                self._send_json(400, {"error": "invalid engine action: %s" % action})
                return
            status, resp_body = _proxy_to_gateway("POST",
                "/quant/api/engine/%s" % action, body=body or None)
            self._proxy_response(status, resp_body)
            return

        # Proxy /quant/api/* to local gateway (trading, data-bridge, stocklist, etc.)
        if path.startswith("/quant/api/"):
            status, resp_body = _proxy_to_gateway("POST", path, body=body)
            self._proxy_response(status, resp_body)
            return

        self._send_json(404, {"error": "not found"})

    def _handle_quick_order(self, body, side):
        """Handle quick buy/sell order via gateway proxy."""
        if not body:
            self._send_json(400, {"error": "empty body"})
            return
        try:
            data = json.loads(body.decode("utf-8"))
        except Exception as e:
            self._send_json(400, {"error": "JSON parse error: %s" % e})
            return

        code = data.get("code", "") or data.get("symbol", "")
        volume = int(data.get("volume", 0) or data.get("quantity", 0))
        price = data.get("price")
        order_type = "limit" if price else "market"

        if not code:
            self._send_json(400, {"error": "code/symbol required"})
            return
        if volume <= 0:
            self._send_json(400, {"error": "volume/quantity must be positive"})
            return

        # Build order payload for the gateway
        order_payload = {
            "symbol": code,
            "side": side,
            "orderType": order_type,
            "quantity": volume,
        }
        if price:
            order_payload["price"] = float(price)

        order_body = json.dumps(order_payload).encode("utf-8")
        print("[receiver] quick %s: %s x%d @ %s"
              % (side, code, volume, price or "market"))
        status, resp_body = _proxy_to_gateway("POST",
            "/quant/api/orders", body=order_body)
        self._proxy_response(status, resp_body)

    def do_DELETE(self):
        """Handle DELETE requests (order cancellation, alert deletion)."""
        path = urlparse(self.path).path

        # DELETE /orders?id=xxx — cancel order
        if path == "/orders":
            qs = urlparse(self.path).query
            proxy_path = "/quant/api/orders"
            if qs:
                proxy_path += "?" + qs
            status, body = _proxy_to_gateway("DELETE", proxy_path)
            self._proxy_response(status, body)
            return

        # DELETE /alerts?id=xxx — delete price alert
        if path == "/alerts":
            qs = urlparse(self.path).query
            proxy_path = "/quant/api/price-alerts"
            if qs:
                proxy_path += "?" + qs
            status, body = _proxy_to_gateway("DELETE", proxy_path)
            self._proxy_response(status, body)
            return

        # Proxy DELETE /quant/api/* to gateway
        if path.startswith("/quant/api/"):
            qs = urlparse(self.path).query
            proxy_path = path + ("?" + qs if qs else "")
            status, body = _proxy_to_gateway("DELETE", proxy_path)
            self._proxy_response(status, body)
            return

        self._send_json(404, {"error": "not found"})


# ===================== CLEANUP THREAD =====================

def _cleanup_loop():
    """Periodically clean up expired requests."""
    while True:
        time.sleep(60)
        _cleanup_old()


# ===================== MAIN =====================

def main():
    parser = argparse.ArgumentParser(description="QMT Push-Mode Data Bridge Receiver (Mac side)")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host (default: %s)" % DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Bind port (default: %d)" % DEFAULT_PORT)
    args = parser.parse_args()

    # Start cleanup thread
    t = threading.Thread(target=_cleanup_loop, name="cleanup", daemon=True)
    t.start()

    server = ThreadingHTTPServer((args.host, args.port), BridgeHandler)
    print("=" * 60)
    print("  QMT Data Bridge Receiver (Push Mode) [THREADED]")
    print("  Listening on http://%s:%d" % (args.host, args.port))
    print("")
    print("  === Data Bridge (Windows QMT) ===")
    print("  Windows QMT polls:  GET  /pending")
    print("  Windows QMT pushes: POST /result/<id>")
    print("  Mac requests:       POST /request  or  POST /klines")
    print("")
    print("  === Trading Shortcuts ===")
    print("  Quick buy:      POST /buy   {code, volume, price?}")
    print("  Quick sell:     POST /sell  {code, volume, price?}")
    print("  Cancel order:   POST /cancel {order_id}")
    print("  Positions:      GET  /positions")
    print("  Account:        GET  /account")
    print("  Orders:         GET  /orders")
    print("  Trades:         GET  /trades")
    print("  P&L:            GET  /pnl")
    print("  Single quote:   GET  /quote/<code>")
    print("  Batch quotes:   POST /quotes {codes: [...]}")
    print("  Signals:        GET  /signals")
    print("  Alerts:         GET  /alerts")
    print("  Engine status:  GET  /engine/status")
    print("")
    print("  === Gateway Proxy ===")
    print("  All /quant/api/* routes proxied to gateway (%s)" % GATEWAY_URL)
    print("=" * 60)
    print("")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[receiver] shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
