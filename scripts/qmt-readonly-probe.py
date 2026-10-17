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
import os
import sys
import traceback
from pathlib import Path


def main() -> int:
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
    if not account_id:
        print(json.dumps({"ok": False, "error": "TRADING_ACCOUNT_ID is required"}, ensure_ascii=False))
        return 2

    sys.path.insert(0, str((Path(__file__).resolve().parents[1] / "quant-trading" / "python")))
    from brokers.qmt_broker import QMTBrokerBridge

    bridge = QMTBrokerBridge({
        "mini_qmt_path": mini_qmt_path,
        "account_id": account_id,
        "account_type": account_type,
    })
    try:
        connected = bridge.connect()
        account = bridge.query_account({})
        positions = bridge.query_positions({})
        orders = bridge.query_orders({})
        print(json.dumps({
            "ok": True,
            "connected": connected,
            "account": account,
            "positionCount": len(positions),
            "orderCount": len(orders),
            "ordersSample": orders[:3],
        }, ensure_ascii=False, default=str))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        try:
            bridge.disconnect()
        except Exception:
            traceback.print_exc(file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
