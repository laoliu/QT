"""
futu_broker.py — Futu OpenD persistent NDJSON bridge.

Protocol (matches PersistentPythonProcess in TS):
  stdin  (line-delimited JSON): { "requestId": "...", "method": "...", "params": {...} }
  stdout (line-delimited JSON): { "requestId": "...", "ok": true, "data": ... }
                                { "event": "fill"|"quote"|"order_status", "data": ... }

Requires: futu-api (pip install futu-api)
Futu OpenD must be running on the same machine (or accessible at the configured host:port)
before starting this process.
"""

import sys
import json
import time
import os
import logging
from typing import Any, Optional

# ── futu-api imports (graceful degradation) ─────────────────────

try:
    from futu import (
        OpenQuoteContext,
        OpenSecTradeContext,
        TrdSide,
        TrdEnv,
        OrderType as FutuOrderType,
        OrderStatus as FutuOrderStatus,
        RET_OK,
        Market,
        SubType,
        SecurityFirm,
        ModifyOrderOp,
        TrdMarket,
    )
    HAS_FUTU = True
except ImportError:
    HAS_FUTU = False

logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger("futu_broker")

# ── NDJSON I/O helpers ──────────────────────────────────────────

def send(obj: dict) -> None:
    """Write a single JSON line to stdout."""
    print(json.dumps(obj, ensure_ascii=False), flush=True)

def reply_ok(request_id: str, data: Any) -> None:
    send({"requestId": request_id, "ok": True, "data": data})

def reply_err(request_id: str, error: str) -> None:
    send({"requestId": request_id, "ok": False, "error": error})

def push_event(event: str, data: Any) -> None:
    send({"event": event, "data": data})


# ── Order/Trade callbacks ────────────────────────────────────────

class OrderUpdateHandler:
    """Receives async order-status push from Futu OpenD."""
    def on_recv_rsp(self, rsp_pb):
        try:
            ret, data = super().on_recv_rsp(rsp_pb)
            if ret != RET_OK or data is None:
                return ret, data
            for _, row in data.iterrows():
                push_event("order_status", {
                    "order_id": str(row.get("order_id", "")),
                    "stock_code": str(row.get("code", "")),
                    "order_type": str(row.get("trd_side", "")),
                    "price": float(row.get("price", 0)),
                    "order_volume": int(row.get("qty", 0)),
                    "traded_volume": int(row.get("dealt_qty", 0)),
                    "traded_price": float(row.get("dealt_avg_price", 0)),
                    "order_status": str(row.get("order_status", "")),
                    "strategy_name": "",
                    "strategy_error_msg": str(row.get("remark", "")),
                    "order_time": str(row.get("create_time", "")),
                })
            return ret, data
        except Exception as exc:
            log.exception("OrderUpdateHandler error")
            return -1, None


class DealUpdateHandler:
    """Receives async fill/deal push from Futu OpenD."""
    def on_recv_rsp(self, rsp_pb):
        try:
            ret, data = super().on_recv_rsp(rsp_pb)
            if ret != RET_OK or data is None:
                return ret, data
            for _, row in data.iterrows():
                value = float(row.get("price", 0)) * int(row.get("qty", 0))
                commission = round(value * 0.0003, 4)
                push_event("fill", {
                    "order_id": str(row.get("order_id", "")),
                    "stock_code": str(row.get("code", "")),
                    "traded_price": float(row.get("price", 0)),
                    "traded_volume": int(row.get("qty", 0)),
                    "traded_amount": value,
                    "commission": commission,
                    "time": str(row.get("create_time", "")),
                })
            return ret, data
        except Exception as exc:
            log.exception("DealUpdateHandler error")
            return -1, None


# ── Futu order-status mapping ────────────────────────────────────

_FUTU_STATUS_MAP = {}
if HAS_FUTU:
    _FUTU_STATUS_MAP = {
        FutuOrderStatus.NONE: "pending",
        FutuOrderStatus.UNSUBMITTED: "pending",
        FutuOrderStatus.WAITING_SUBMIT: "pending",
        FutuOrderStatus.SUBMITTING: "submitted",
        FutuOrderStatus.SUBMIT_FAILED: "rejected",
        FutuOrderStatus.SUBMITTED: "submitted",
        FutuOrderStatus.FILLED_PART: "partial",
        FutuOrderStatus.FILLED_ALL: "filled",
        FutuOrderStatus.CANCELLED_PART: "cancelled",
        FutuOrderStatus.CANCELLED_ALL: "cancelled",
        FutuOrderStatus.FAILED: "rejected",
        FutuOrderStatus.DISABLED: "rejected",
        FutuOrderStatus.DELETED: "cancelled",
    }


def _map_futu_status(status) -> str:
    return _FUTU_STATUS_MAP.get(status, "submitted")


# ── Side mapping ─────────────────────────────────────────────────

def _map_side_to_futu(side: str):
    """Map our canonical side string to Futu TrdSide."""
    if not HAS_FUTU:
        return None
    mapping = {
        "buy": TrdSide.BUY,
        "sell": TrdSide.SELL,
        "buy_open": TrdSide.BUY,
        "sell_close": TrdSide.SELL,
        "sell_open": TrdSide.SELL,
        "buy_close": TrdSide.BUY,
        "short": TrdSide.SELL_SHORT,
        "cover": TrdSide.BUY_BACK,
    }
    return mapping.get(side, TrdSide.BUY)


def _map_futu_side(trd_side) -> str:
    if not HAS_FUTU:
        return "buy"
    mapping = {
        TrdSide.BUY: "buy",
        TrdSide.SELL: "sell",
        TrdSide.SELL_SHORT: "short",
        TrdSide.BUY_BACK: "cover",
    }
    return mapping.get(trd_side, "buy")


def _map_order_type_to_futu(order_type: str):
    """Map our canonical order type to Futu OrderType."""
    if not HAS_FUTU:
        return None
    mapping = {
        "market": FutuOrderType.MARKET,
        "limit": FutuOrderType.NORMAL,
        "stop": FutuOrderType.STOP,
        "stop_limit": FutuOrderType.STOP_LIMIT,
    }
    return mapping.get(order_type, FutuOrderType.NORMAL)


# ── Trade environment mapping ────────────────────────────────────

def _get_trd_env(env_str: str):
    if not HAS_FUTU:
        return None
    if env_str == "SIMULATE":
        return TrdEnv.SIMULATE
    return TrdEnv.REAL


# ── Market mapping ───────────────────────────────────────────────

def _get_trd_market(market: str):
    """Map our MarketType to Futu TrdMarket."""
    if not HAS_FUTU:
        return None
    mapping = {
        "hk_stock": TrdMarket.HK,
        "us_stock": TrdMarket.US,
        "cn_stock": TrdMarket.CN,
        "futures": TrdMarket.FUTURES,
    }
    return mapping.get(market, TrdMarket.HK)


# ── Symbol normalisation ────────────────────────────────────────

def to_futu_symbol(symbol: str, market: str = "") -> str:
    """
    Convert a plain symbol to Futu format.
    Futu uses: HK.00700, US.AAPL, SH.600519, SZ.000001
    """
    if "." in symbol and symbol.split(".")[0] in ("HK", "US", "SH", "SZ"):
        return symbol  # already in Futu format

    # Try to detect from code pattern
    code = symbol.replace(".", "").strip()

    if market == "hk_stock":
        return f"HK.{code.zfill(5)}"
    if market == "us_stock":
        return f"US.{code}"

    # A-share detection (same logic as QMT)
    try:
        n = int(code)
        if code.startswith("6") or code.startswith("5") or code.startswith("9"):
            return f"SH.{code}"
        return f"SZ.{code}"
    except ValueError:
        pass

    return f"HK.{code}"  # default to HK


def from_futu_symbol(s: str) -> str:
    """Strip the market prefix: 'HK.00700' → '00700'."""
    parts = s.split(".", 1)
    return parts[1] if len(parts) == 2 else s


# ── Main broker class ───────────────────────────────────────────

class FutuBrokerBridge:
    def __init__(self, init_args: dict):
        self.host: str = init_args.get("opend_host", "127.0.0.1")
        self.port: int = int(init_args.get("opend_port", 11111))
        self.trade_env_str: str = init_args.get("trade_env", "SIMULATE")
        self.pwd_unlock: str = init_args.get("pwd_unlock", "")
        self.trd_market_str: str = init_args.get("trd_market", "hk_stock")
        self.security_firm_str: str = init_args.get("security_firm", "")
        self.account_id: str = init_args.get("account_id", "")

        self.quote_ctx: Optional[Any] = None
        self.trade_ctx: Optional[Any] = None
        self._subscribed_codes: set = set()

    def connect(self) -> dict:
        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed. Run: pip install futu-api")

        # Quote context
        self.quote_ctx = OpenQuoteContext(host=self.host, port=self.port)

        # Trade context — with optional security firm for HK multi-broker accounts
        trd_env = _get_trd_env(self.trade_env_str)
        trade_kwargs = {
            "host": self.host,
            "port": self.port,
            "filter_trdmarket": _get_trd_market(self.trd_market_str),
        }
        if self.security_firm_str and hasattr(SecurityFirm, self.security_firm_str):
            trade_kwargs["security_firm"] = getattr(SecurityFirm, self.security_firm_str)
        self.trade_ctx = OpenSecTradeContext(**trade_kwargs)

        # Unlock trading if password provided (required for real trading)
        if self.pwd_unlock and trd_env == TrdEnv.REAL:
            ret, data = self.trade_ctx.unlock_trade(self.pwd_unlock)
            if ret != RET_OK:
                raise RuntimeError(f"Failed to unlock trading: {data}")

        return {"status": "connected", "host": self.host, "port": self.port}

    def disconnect(self) -> dict:
        if self.quote_ctx:
            self.quote_ctx.close()
            self.quote_ctx = None
        if self.trade_ctx:
            self.trade_ctx.close()
            self.trade_ctx = None
        return {"status": "disconnected"}

    def place_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        stock_code = params["stock_code"]
        side = params.get("side", "buy")
        order_type = params.get("order_type", "limit")
        price = float(params.get("price", 0))
        volume = int(params["volume"])

        trd_env = _get_trd_env(self.trade_env_str)
        futu_side = _map_side_to_futu(side)
        futu_order_type = _map_order_type_to_futu(order_type)

        ret, data = self.trade_ctx.place_order(
            price=price,
            qty=volume,
            code=stock_code,
            trd_side=futu_side,
            trd_env=trd_env,
            order_type=futu_order_type,
            remark=params.get("strategy_name", ""),
        )

        if ret != RET_OK:
            raise RuntimeError(f"place_order failed: {data}")

        order_id = str(data["order_id"].iloc[0])
        return {"broker_order_id": order_id}

    def cancel_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        order_id = params["order_id"]
        trd_env = _get_trd_env(self.trade_env_str)

        ret, data = self.trade_ctx.modify_order(
            ModifyOrderOp.CANCEL,
            order_id=order_id,
            qty=0,
            price=0,
            trd_env=trd_env,
        )

        if ret != RET_OK:
            raise RuntimeError(f"cancel_order failed: {data}")

        return {"result": 0}

    def query_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        order_id = params["order_id"]
        trd_env = _get_trd_env(self.trade_env_str)

        ret, data = self.trade_ctx.order_list_query(
            order_id=order_id,
            trd_env=trd_env,
        )

        if ret != RET_OK:
            raise RuntimeError(f"query_order failed: {data}")

        if data.empty:
            raise RuntimeError(f"Order {order_id} not found")

        row = data.iloc[0]
        return self._serialize_order(row)

    def query_orders(self, params: dict) -> list:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        trd_env = _get_trd_env(self.trade_env_str)
        stock_code = params.get("stock_code")

        ret, data = self.trade_ctx.order_list_query(trd_env=trd_env)
        if ret != RET_OK:
            raise RuntimeError(f"query_orders failed: {data}")

        result = []
        for _, row in data.iterrows():
            order = self._serialize_order(row)
            if stock_code and order["stock_code"] != stock_code:
                continue
            result.append(order)
        return result

    def query_positions(self, _params: dict = {}) -> list:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        trd_env = _get_trd_env(self.trade_env_str)
        ret, data = self.trade_ctx.position_list_query(trd_env=trd_env)
        if ret != RET_OK:
            raise RuntimeError(f"query_positions failed: {data}")

        result = []
        for _, row in data.iterrows():
            result.append(self._serialize_position(row))
        return result

    def query_account(self, _params: dict = {}) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        trd_env = _get_trd_env(self.trade_env_str)
        ret, data = self.trade_ctx.accinfo_query(trd_env=trd_env)
        if ret != RET_OK:
            raise RuntimeError(f"query_account failed: {data}")

        row = data.iloc[0]
        return {
            "cash": float(row.get("cash", 0)),
            "total_asset": float(row.get("total_assets", 0)),
            "market_value": float(row.get("market_val", 0)),
            "frozen_cash": float(row.get("frozen_cash", 0)),
            "profit": float(row.get("unrealized_pl", 0)),
            "profit_ratio": float(row.get("unrealized_pl", 0)) / max(float(row.get("total_assets", 1)), 1),
        }

    def subscribe_quote(self, params: dict) -> dict:
        if not self.quote_ctx:
            raise RuntimeError("Not connected")

        codes = params.get("stock_codes", [])
        new_codes = [c for c in codes if c not in self._subscribed_codes]
        if new_codes and HAS_FUTU:
            for code in new_codes:
                ret, data = self.quote_ctx.subscribe(code, [SubType.QUOTE], subscribe_push=True)
                if ret == RET_OK:
                    self._subscribed_codes.add(code)
                else:
                    log.warning("subscribe %s failed: %s", code, data)
        return {"subscribed": list(self._subscribed_codes)}

    def unsubscribe_quote(self, params: dict) -> dict:
        if not self.quote_ctx:
            raise RuntimeError("Not connected")

        codes = params.get("stock_codes", [])
        for code in codes:
            if code in self._subscribed_codes:
                self.quote_ctx.unsubscribe(code, [SubType.QUOTE])
                self._subscribed_codes.discard(code)
        return {"subscribed": list(self._subscribed_codes)}

    def ping(self) -> dict:
        """Health check — verifies that both quote and trade contexts are alive."""
        if not self.quote_ctx or not self.trade_ctx:
            raise RuntimeError("Not connected")
        # A lightweight call to verify connectivity
        trd_env = _get_trd_env(self.trade_env_str)
        ret, data = self.trade_ctx.accinfo_query(trd_env=trd_env)
        if ret != RET_OK:
            raise RuntimeError(f"Ping failed: {data}")
        return {"status": "ok"}

    # ── Serialisers ────────────────────────────────────────────

    @staticmethod
    def _serialize_order(row) -> dict:
        return {
            "order_id": str(row.get("order_id", "")),
            "stock_code": str(row.get("code", "")),
            "order_type": str(row.get("trd_side", "")),
            "price": float(row.get("price", 0)),
            "order_volume": int(row.get("qty", 0)),
            "traded_volume": int(row.get("dealt_qty", 0)),
            "traded_price": float(row.get("dealt_avg_price", 0)),
            "order_status": str(row.get("order_status", "")),
            "strategy_name": str(row.get("remark", "")),
            "strategy_error_msg": "",
            "order_time": str(row.get("create_time", "")),
        }

    @staticmethod
    def _serialize_position(row) -> dict:
        volume = int(row.get("qty", 0))
        avg_price = float(row.get("cost_price", 0))
        market_value = float(row.get("market_val", 0))
        last_price = float(row.get("last_price", 0))
        unrealized_pnl = float(row.get("pl_val", 0))
        unrealized_pnl_pct = float(row.get("pl_ratio", 0))

        return {
            "stock_code": str(row.get("code", "")),
            "stock_name": str(row.get("stock_name", "")),
            "volume": volume,
            "can_use_volume": volume,  # Futu doesn't separate frozen in same way
            "frozen_volume": 0,
            "today_buy_volume": 0,
            "avg_price": avg_price,
            "market_value": market_value,
            "last_price": last_price if last_price > 0 else None,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "yesterday_volume": volume,
            "on_road_volume": 0,
        }


# ── Dispatch loop ───────────────────────────────────────────────

METHODS = {
    "connect": "connect",
    "disconnect": "disconnect",
    "place_order": "place_order",
    "cancel_order": "cancel_order",
    "query_order": "query_order",
    "query_orders": "query_orders",
    "query_positions": "query_positions",
    "query_account": "query_account",
    "subscribe_quote": "subscribe_quote",
    "unsubscribe_quote": "unsubscribe_quote",
    "ping": "ping",
}

def main():
    # Read init args from environment
    try:
        init_args = json.loads(os.environ.get("QUANT_INIT_ARGS", "{}"))
    except Exception:
        init_args = {}

    bridge = FutuBrokerBridge(init_args)

    # Signal ready to the TS side
    push_event("ready", {"module": "futu_broker", "futu_api": HAS_FUTU})

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        request_id = msg.get("requestId", "")
        method = msg.get("method", "")
        params = msg.get("params", {})

        if method == "__init__":
            reply_ok(request_id, {"status": "ok"})
            continue

        if method not in METHODS:
            reply_err(request_id, f"Unknown method: {method}")
            continue

        try:
            fn = getattr(bridge, METHODS[method])
            if method in ("query_positions", "query_account", "disconnect", "connect", "ping"):
                result = fn()
            else:
                result = fn(params)
            reply_ok(request_id, result)
        except Exception as exc:
            log.exception("Method %s failed", method)
            reply_err(request_id, str(exc))


if __name__ == "__main__":
    main()
