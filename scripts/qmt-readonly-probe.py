"""Read-only miniQMT or full-client QMT/RECEIVER connectivity probe.

This command intentionally performs only connect/query/disconnect.  It never
calls place_order, cancel_order, or any account-mutating API.

Usage (PowerShell):
    $env:MINI_QMT_PATH = 'D:\\st_lc\\userdata_mini'
    $env:TRADING_ACCOUNT_ID = '11304375'
    python scripts/qmt-readonly-probe.py
    python scripts/qmt-readonly-probe.py --receiver-url http://127.0.0.1:18889

RECEIVER mode uses the trading request/response queue, never cached portfolio
routes. It leaves the shared BRIDGE connection and subscriptions intact.
"""
from __future__ import annotations

import json
import math
import multiprocessing as mp
import os
import queue
import sys
import time
import traceback
from argparse import ArgumentParser
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, ProxyHandler


def _probe_receiver(receiver_url: str, account_id: str, timeout: float) -> dict:
    """Query the real strategy bridge through RECEIVER, with one total deadline."""
    url = urlsplit(receiver_url)
    if (url.scheme not in {"http", "https"} or not url.hostname
            or url.username or url.password or url.query or url.fragment
            or url.path not in {"", "/"}):
        raise ValueError("--receiver-url must be an HTTP(S) origin without credentials or a path")
    base = receiver_url.rstrip("/")
    deadline = time.monotonic() + timeout
    # A configured system proxy must not redirect the local QMT validation path.
    opener = build_opener(ProxyHandler({}))
    pending = object()

    def remaining() -> float:
        left = deadline - time.monotonic()
        if left <= 0:
            raise TimeoutError(f"RECEIVER probe timed out after {timeout:g}s")
        return left

    def request(path: str, body: dict | None = None):
        payload = None if body is None else json.dumps(body).encode("utf-8")
        req = Request(base + path, data=payload,
                      headers={"Content-Type": "application/json"})
        with opener.open(req, timeout=min(3.0, remaining())) as response:
            if response.status == 204:
                return pending
            if response.status != 200:
                raise RuntimeError(f"RECEIVER returned HTTP {response.status}")
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise RuntimeError("RECEIVER response exceeds 2 MB")
            return json.loads(raw)

    health = request("/health")
    if not isinstance(health, dict) or health.get("status") != "ok" or health.get("mode") != "push-receiver":
        raise RuntimeError("The endpoint is not a healthy push-receiver")
    if health.get("bridge_connected") is not True:
        raise RuntimeError("QMT BRIDGE is not polling RECEIVER; start BRIDGE in the full QMT client")

    def call(method: str, params: dict | None = None):
        # Keep a strict allowlist even if another caller reuses this helper later.
        if method not in {"connect", "query_account", "query_positions", "query_orders"}:
            raise ValueError(f"Non-readonly method rejected: {method}")
        ack = request("/quant/api/trading-request", {"method": method, "params": params or {}})
        if not isinstance(ack, dict) or ack.get("status") != "ok" or not isinstance(ack.get("requestId"), str) or not ack["requestId"]:
            raise RuntimeError(f"{method}: invalid request acknowledgement: {ack!r}")
        request_id = ack["requestId"]
        while True:
            result = request("/quant/api/trading-response?" + urlencode({"requestId": request_id}))
            if result is not pending:
                if not isinstance(result, dict) or result.get("requestId") != request_id or type(result.get("ok")) is not bool:
                    raise RuntimeError(f"{method}: malformed or mismatched bridge response")
                if not result["ok"]:
                    raise RuntimeError(f"{method}: {result.get('error') or 'QMT bridge rejected the query'}")
                if "data" not in result:
                    raise RuntimeError(f"{method}: response has no data")
                return result["data"]
            time.sleep(min(0.1, remaining()))

    connected = call("connect", {"account_id": account_id})
    if (not isinstance(connected, dict) or connected.get("mode") != "strategy"
            or connected.get("status") not in {"connected", "already_connected"}
            or connected.get("account_id") != account_id):
        raise RuntimeError("connect: expected the configured account in QMT strategy mode")
    account = call("query_account")
    if not isinstance(account, dict) or account.get("account_id") != account_id or account.get("mode") != "strategy":
        raise RuntimeError("query_account: expected real strategy account data for the configured account")
    for field in ("cash", "frozen_cash", "total_asset", "market_value"):
        value = account.get(field)
        if type(value) not in (int, float) or not math.isfinite(value):
            raise RuntimeError(f"query_account: invalid {field}")
    positions = call("query_positions")
    orders = call("query_orders")
    for method, rows, key in (("query_positions", positions, "stock_code"), ("query_orders", orders, "order_id")):
        if not isinstance(rows, list) or any(not isinstance(row, dict) or not str(row.get(key) or "").strip() for row in rows):
            raise RuntimeError(f"{method}: expected an array of valid broker records")
    remaining()
    return {"ok": True, "transport": "receiver", "connected": connected,
            "account": account, "positionCount": len(positions), "orderCount": len(orders),
            "ordersSample": orders[:3], "orderScope": "broker_query_not_local_history"}


def _load_settings() -> tuple[str, str, str]:
    env_file = Path(__file__).resolve().parents[1] / ".env"
    dotenv: dict[str, str] = {}
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                dotenv[key.strip()] = value.strip().strip('"')
    mini_qmt_path = os.environ.get("MINI_QMT_PATH", dotenv.get("MINI_QMT_PATH", r"D:\st_lc\userdata_mini"))
    account_id = os.environ.get("TRADING_ACCOUNT_ID", dotenv.get("TRADING_ACCOUNT_ID", ""))
    account_type = os.environ.get("TRADING_ACCOUNT_TYPE", dotenv.get("TRADING_ACCOUNT_TYPE", "STOCK"))
    return mini_qmt_path, account_id, account_type


def _probe_worker(
    mini_qmt_path: str,
    account_id: str,
    account_type: str,
    result_queue: "mp.Queue[dict]",
) -> None:
    """Run QMT calls in a killable child process.

    xtquant can block inside a native RPC call when miniQMT is not serving.
    Keeping the broker interaction in a child process lets the diagnostic
    command return a reliable timeout instead of hanging the operator shell.
    """
    bridge = None
    try:
        sys.path.insert(0, str((Path(__file__).resolve().parents[1] / "quant-trading" / "python")))
        from brokers.qmt_broker import QMTBrokerBridge

        bridge = QMTBrokerBridge({
            "mini_qmt_path": mini_qmt_path,
            "account_id": account_id,
            "account_type": account_type,
        })
        connected = bridge.connect()
        account = bridge.query_account({})
        positions = bridge.query_positions({})
        orders = bridge.query_orders({})
        result_queue.put({
            "ok": True,
            "connected": connected,
            "account": account,
            "positionCount": len(positions),
            "orderCount": len(orders),
            "ordersSample": orders[:3],
        })
    except Exception as exc:
        result_queue.put({"ok": False, "error": str(exc)})
    finally:
        if bridge is not None:
            try:
                bridge.disconnect()
            except Exception:
                traceback.print_exc(file=sys.stderr)


def main() -> int:
    parser = ArgumentParser(description=__doc__)
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("QMT_PROBE_TIMEOUT_SECS", "20")),
        help="maximum seconds for the complete connect/query probe (default: 20)",
    )
    parser.add_argument("--receiver-url", help="full QMT RECEIVER origin, e.g. http://127.0.0.1:18889")
    args = parser.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        print(json.dumps({"ok": False, "error": "--timeout must be positive"}, ensure_ascii=False))
        return 2

    mini_qmt_path, account_id, account_type = _load_settings()
    if not account_id:
        print(json.dumps({"ok": False, "error": "TRADING_ACCOUNT_ID is required"}, ensure_ascii=False))
        return 2

    if args.receiver_url:
        try:
            result = _probe_receiver(args.receiver_url, account_id, args.timeout)
        except Exception as exc:
            result = {"ok": False, "transport": "receiver", "error": str(exc)}
        print(json.dumps(result, ensure_ascii=False, default=str))
        return 0 if result.get("ok") else 1

    context = mp.get_context("spawn")
    result_queue = context.Queue()
    worker = context.Process(
        target=_probe_worker,
        args=(mini_qmt_path, account_id, account_type, result_queue),
        name="qmt-readonly-probe-worker",
    )
    worker.start()
    worker.join(args.timeout)
    if worker.is_alive():
        worker.terminate()
        worker.join(3)
        print(json.dumps({
            "ok": False,
            "error": f"QMT probe timed out after {args.timeout:g}s; miniQMT RPC did not return",
        }, ensure_ascii=False))
        return 124
    try:
        result = result_queue.get(timeout=1)
    except queue.Empty:
        result = {
            "ok": False,
            "error": f"QMT probe worker exited without a result (exitcode={worker.exitcode})",
        }
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
