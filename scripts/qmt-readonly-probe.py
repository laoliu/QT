"""Read-only miniQMT connectivity probe.

This command intentionally performs only connect/query/disconnect.  It never
calls place_order, cancel_order, or any account-mutating API.

Usage (PowerShell):
    $env:MINI_QMT_PATH = 'D:\\st_lc\\userdata_mini'
    $env:TRADING_ACCOUNT_ID = '11304375'
    python scripts/qmt-readonly-probe.py
"""
from __future__ import annotations

import json
import multiprocessing as mp
import os
import queue
import sys
import traceback
from argparse import ArgumentParser
from pathlib import Path


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
        help="maximum seconds for native QMT connect/query calls (default: 20)",
    )
    args = parser.parse_args()
    if args.timeout <= 0:
        print(json.dumps({"ok": False, "error": "--timeout must be positive"}, ensure_ascii=False))
        return 2

    mini_qmt_path, account_id, account_type = _load_settings()
    if not account_id:
        print(json.dumps({"ok": False, "error": "TRADING_ACCOUNT_ID is required"}, ensure_ascii=False))
        return 2

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
