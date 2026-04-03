# coding: utf-8
#
# RECEIVER.py - Mac-side HTTP server for QMT push-mode data bridge
#
# Runs on Mac, receives data pushed from Windows QMT.
# Windows BRIDGE.py polls /pending for requests, fetches data via ContextInfo,
# then POSTs results back here.
#
# Usage:
#   python3 RECEIVER.py [--port 58088] [--host 0.0.0.0]
#
# API:
#   POST /request      <- Mac submits a data request, returns {"id": "..."}
#   GET  /pending       <- Windows polls for pending requests
#   POST /result/<id>   <- Windows pushes result for a request
#   GET  /result/<id>   <- Mac reads result (blocks until available or timeout)
#   GET  /health        <- Health check
#   GET  /status        <- Show all requests and their status
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
DEFAULT_PORT = 58088
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

    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
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

        # Proxy /quant/api/trading-* to local gateway
        if path.startswith("/quant/api/trading"):
            qs = urlparse(self.path).query
            proxy_path = path + ("?" + qs if qs else "")
            status, body = _proxy_to_gateway("GET", proxy_path)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(body)
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

        # Proxy /quant/api/trading-* to local gateway
        if path.startswith("/quant/api/trading"):
            status, resp_body = _proxy_to_gateway("POST", path, body=body)
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(resp_body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(resp_body)
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
    print("  Windows QMT polls:  GET  /pending")
    print("  Windows QMT pushes: POST /result/<id>")
    print("  Mac requests:       POST /request  or  POST /klines")
    print("=" * 60)
    print("")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[receiver] shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
