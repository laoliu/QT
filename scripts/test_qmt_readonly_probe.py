"""Protocol regression tests; a fake server is not real QMT acceptance evidence."""
import importlib.util
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest


spec = importlib.util.spec_from_file_location("qmt_readonly_probe", Path(__file__).with_name("qmt-readonly-probe.py"))
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


@contextmanager
def receiver(scenario="ok"):
    commands, paths, requests, polls = [], [], {}, {}
    account = {"account_id": "test-account", "mode": "strategy", "cash": 100.0,
               "frozen_cash": 0.0, "total_asset": 100.0, "market_value": 0.0}
    data = {
        "connect": {"status": "connected", "mode": "strategy", "account_id": "test-account"},
        "query_account": account,
        "query_positions": [{"stock_code": "600519.SH", "volume": 100}],
        "query_orders": [{"order_id": "broker-1", "stock_code": "600519.SH"}],
    }
    if scenario == "wrong_account":
        account["account_id"] = "another-account"
    if scenario == "bad_account":
        account["cash"] = "100"
    if scenario == "bad_positions":
        data["query_positions"] = None
    if scenario == "bad_orders":
        data["query_orders"] = [{}]
    if scenario == "empty":
        data["query_positions"] = []
        data["query_orders"] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, body, status=200):
            raw = b"" if status == 204 else json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            paths.append(self.path)
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            commands.append(body)
            assert self.path == "/quant/api/trading-request"
            request_id = str(len(commands))
            requests[request_id] = body["method"]
            if scenario == "bad_ack":
                self.respond({"status": "error", "error": "unavailable"})
            else:
                self.respond({"status": "ok", "requestId": request_id})

        def do_GET(self):
            paths.append(self.path)
            url = urlsplit(self.path)
            if url.path == "/health":
                self.respond({"status": "ok", "mode": "paper" if scenario == "wrong_mode" else "push-receiver",
                              "bridge_connected": scenario != "inactive"})
                return
            assert url.path == "/quant/api/trading-response"
            request_id = parse_qs(url.query)["requestId"][0]
            method = requests[request_id]
            polls[request_id] = polls.get(request_id, 0) + 1
            if scenario == "timeout" or (scenario == "ok" and polls[request_id] == 1):
                self.respond(None, 204)
                return
            if scenario == "null_response":
                self.respond(None)
                return
            self.respond({"requestId": "other" if scenario == "wrong_id" else request_id,
                          "ok": "true" if scenario == "bad_ok" else scenario != "remote_error",
                          "data": data[method], "error": "get_trade_detail_data unavailable"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", commands, paths
    finally:
        server.shutdown()
        server.server_close()
        thread.join(1)


@pytest.mark.parametrize("scenario,count", [("ok", 1), ("empty", 0)])
def test_receiver_queries_real_trading_queue_only(scenario, count):
    with receiver(scenario) as (url, commands, paths):
        result = probe._probe_receiver(url, "test-account", 3)
    assert result["ok"] is True
    assert result["positionCount"] == result["orderCount"] == count
    assert result["orderScope"] == "broker_query_not_local_history"
    assert [c["method"] for c in commands] == ["connect", "query_account", "query_positions", "query_orders"]
    assert commands[0]["params"] == {"account_id": "test-account"}
    assert all(urlsplit(p).path in {"/health", "/quant/api/trading-request", "/quant/api/trading-response"} for p in paths)


@pytest.mark.parametrize("scenario,error", [
    ("inactive", "not polling"), ("wrong_mode", "not a healthy push-receiver"),
    ("bad_ack", "invalid request acknowledgement"), ("wrong_id", "mismatched"),
    ("bad_ok", "malformed"), ("null_response", "malformed"),
    ("remote_error", "get_trade_detail_data unavailable"),
    ("wrong_account", "configured account"), ("bad_account", "invalid cash"),
    ("bad_positions", "query_positions"), ("bad_orders", "query_orders"),
])
def test_receiver_rejects_unavailable_or_malformed_results(scenario, error):
    with receiver(scenario) as (url, commands, _):
        with pytest.raises(RuntimeError, match=error):
            probe._probe_receiver(url, "test-account", 2)
    assert all(c["method"] in {"connect", "query_account", "query_positions", "query_orders"} for c in commands)
    if scenario in {"inactive", "wrong_mode"}:
        assert commands == []


def test_pending_bridge_has_one_bounded_deadline():
    with receiver("timeout") as (url, commands, _):
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="timed out"):
            probe._probe_receiver(url, "test-account", 0.25)
        assert time.monotonic() - started < 1
    assert [c["method"] for c in commands] == ["connect"]


@pytest.mark.parametrize("url", ["file:///tmp/qmt", "http://user:password@localhost", "http://localhost/orders", "http://localhost?x=1"])
def test_receiver_requires_explicit_origin(url):
    with pytest.raises(ValueError, match="HTTP"):
        probe._probe_receiver(url, "test-account", 1)
