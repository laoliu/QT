"""
futu_broker.py — FutuOpenD persistent NDJSON bridge.

Protocol (matches PersistentPythonProcess in TS):
  stdin  (line-delimited JSON): { "requestId": "...", "method": "...", "params": {...} }
  stdout (line-delimited JSON): { "requestId": "...", "ok": true, "data": ... }
                                { "event": "fill"|"quote"|"order_status"|"position_change", "data": ... }

Requires: futu-api (pip install futu-api)
FutuOpenD must be running on the same or reachable machine before starting this process.
"""

import sys
import json
import threading
import time
import os
import logging
from typing import Any, Optional

# ── futu-api imports (graceful degradation) ─────────────────────

try:
    from futu import (
        OpenQuoteContext,
        OpenSecTradeContext,
        TrdEnv,
        TrdSide,
        OrderType as FutuOrderType,
        OrderStatus as FutuOrderStatus,
        SubType,
        RET_OK,
        RET_ERROR,
        TrdMarket,
        SecurityFirm,
    )
    from futu import StockQuoteHandlerBase, TradeOrderHandlerBase, TradeDealHandlerBase
    HAS_FUTU = True
except ImportError:
    HAS_FUTU = False

logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger("futu_broker")

# ── NDJSON I/O helpers ────────────────────────────────────────────

_stdout_lock = threading.Lock()

def send(obj: dict) -> None:
    """Write a single JSON line to stdout (thread-safe)."""
    with _stdout_lock:
        print(json.dumps(obj, ensure_ascii=False), flush=True)

def reply_ok(request_id: str, data: Any) -> None:
    send({"requestId": request_id, "ok": True, "data": data})

def reply_err(request_id: str, error: str) -> None:
    send({"requestId": request_id, "ok": False, "error": error})

def push_event(event: str, data: Any) -> None:
    send({"event": event, "data": data})

# ── Futu push handlers ───────────────────────────────────────────

if HAS_FUTU:
    class QuotePushHandler(StockQuoteHandlerBase):
        """Handles real-time quote pushes from OpenD and forwards as NDJSON events."""
        def on_recv_rsp(self, rsp_pb):
            ret, data = super().on_recv_rsp(rsp_pb)
            if ret != RET_OK:
                return ret, data
            for _, row in data.iterrows():
                push_event("quote", {
                    "code": row.get("code", ""),
                    "last_price": float(row.get("last_price", 0)),
                    "open_price": float(row.get("open_price", 0)),
                    "high_price": float(row.get("high_price", 0)),
                    "low_price": float(row.get("low_price", 0)),
                    "prev_close_price": float(row.get("prev_close_price", 0)),
                    "volume": int(row.get("volume", 0)),
                    "turnover": float(row.get("turnover", 0)),
                    "timestamp": int(time.time() * 1000),
                    "bid_price": float(row.get("bid_price", 0)),
                    "ask_price": float(row.get("ask_price", 0)),
                })
            return ret, data

    class OrderPushHandler(TradeOrderHandlerBase):
        """Handles order status change pushes from OpenD."""
        def on_recv_rsp(self, rsp_pb):
            ret, data = super().on_recv_rsp(rsp_pb)
            if ret != RET_OK:
                return ret, data
            for _, row in data.iterrows():
                push_event("order_status", {
                    "order_id": str(row.get("order_id", "")),
                    "code": row.get("code", ""),
                    "trd_side": row.get("trd_side", ""),
                    "order_type": row.get("order_type", ""),
                    "price": float(row.get("price", 0)),
                    "qty": int(row.get("qty", 0)),
                    "dealt_qty": int(row.get("dealt_qty", 0)),
                    "dealt_avg_price": float(row.get("dealt_avg_price", 0)),
                    "order_status": row.get("order_status", ""),
                    "create_time": row.get("create_time", ""),
                    "updated_time": row.get("updated_time", ""),
                    "remark": row.get("remark", ""),
                })
            return ret, data

    class DealPushHandler(TradeDealHandlerBase):
        """Handles trade deal (fill) pushes from OpenD."""
        def on_recv_rsp(self, rsp_pb):
            ret, data = super().on_recv_rsp(rsp_pb)
            if ret != RET_OK:
                return ret, data
            for _, row in data.iterrows():
                push_event("fill", {
                    "order_id": str(row.get("order_id", "")),
                    "code": row.get("code", ""),
                    "trd_side": row.get("trd_side", ""),
                    "price": float(row.get("price", 0)),
                    "qty": int(row.get("qty", 0)),
                    "create_time": row.get("create_time", ""),
                    "counter_broker_id": row.get("counter_broker_id", None),
                    "counter_broker_name": row.get("counter_broker_name", None),
                })
            return ret, data


# ── Futu TrdSide / OrderType mapping ─────────────────────────────

def _map_trd_side(side: str):
    """Map string side to futu TrdSide enum."""
    if not HAS_FUTU:
        return None
    mapping = {
        "BUY": TrdSide.BUY,
        "SELL": TrdSide.SELL,
        "SELL_SHORT": TrdSide.SELL_SHORT,
        "BUY_BACK": TrdSide.BUY_BACK,
    }
    return mapping.get(side, TrdSide.BUY)

def _map_order_type(ot: str):
    """Map string order type to futu OrderType enum."""
    if not HAS_FUTU:
        return None
    mapping = {
        "MARKET": FutuOrderType.MARKET,
        "NORMAL": FutuOrderType.NORMAL,
        "STOP": FutuOrderType.STOP,
        "STOP_LIMIT": FutuOrderType.STOP_LIMIT,
    }
    return mapping.get(ot, FutuOrderType.NORMAL)

def _map_trd_env(env: str):
    """Map string trade environment to futu TrdEnv enum."""
    if not HAS_FUTU:
        return None
    if env == "REAL":
        return TrdEnv.REAL
    return TrdEnv.SIMULATE


# ── Main broker class ─────────────────────────────────────────────

class FutuBrokerBridge:
    def __init__(self, init_args: dict):
        self.host: str = init_args.get("host", "127.0.0.1")
        self.port: int = int(init_args.get("port", 11111))
        self.trade_env_str: str = init_args.get("trade_env", "SIMULATE")
        self.pwd_unlock: str = init_args.get("pwd_unlock", "")
        self.quote_ctx: Optional[Any] = None
        self.trade_ctx: Optional[Any] = None
        self._subscribed_codes: set = set()

    def connect(self) -> dict:
        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed. Run: pip install futu-api")

        # Open quote context for real-time data
        self.quote_ctx = OpenQuoteContext(host=self.host, port=self.port)

        # Register push handlers
        self.quote_ctx.set_handler(QuotePushHandler())

        # Open trade context
        trd_env = _map_trd_env(self.trade_env_str)
        self.trade_ctx = OpenSecTradeContext(
            host=self.host,
            port=self.port,
            filter_trdmarket=TrdMarket.NONE,
        )

        # Register trade push handlers
        self.trade_ctx.set_handler(OrderPushHandler())
        self.trade_ctx.set_handler(DealPushHandler())

        # Unlock trade if password provided (required for real trading)
        if self.pwd_unlock and trd_env == TrdEnv.REAL:
            ret, data = self.trade_ctx.unlock_trade(self.pwd_unlock)
            if ret != RET_OK:
                raise RuntimeError(f"Failed to unlock trade: {data}")

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

        code = params["code"]
        trd_side = _map_trd_side(params["trd_side"])
        order_type = _map_order_type(params["order_type"])
        price = float(params.get("price", 0))
        qty = int(params["qty"])
        remark = params.get("remark", "")
        trd_env = _map_trd_env(self.trade_env_str)

        ret, data = self.trade_ctx.place_order(
            price=price,
            qty=qty,
            code=code,
            trd_side=trd_side,
            order_type=order_type,
            trd_env=trd_env,
            remark=remark,
        )

        if ret != RET_OK:
            raise RuntimeError(f"place_order failed: {data}")

        order_id = str(data.iloc[0]["order_id"]) if len(data) > 0 else ""
        return {"broker_order_id": order_id}

    def cancel_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")
        order_id = params["order_id"]
        trd_env = _map_trd_env(self.trade_env_str)

        ret, data = self.trade_ctx.modify_order(
            modify_order_op=2,  # CANCEL
            order_id=order_id,
            qty=0,
            price=0,
            trd_env=trd_env,
        )
        if ret != RET_OK:
            raise RuntimeError(f"cancel_order failed: {data}")
        return {"result": "ok"}

    def modify_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        order_id = params["order_id"]
        price = float(params.get("price", 0))
        qty = int(params.get("qty", 0))
        trd_env = _map_trd_env(self.trade_env_str)

        ret, data = self.trade_ctx.modify_order(
            modify_order_op=1,  # NORMAL (modify)
            order_id=order_id,
            qty=qty,
            price=price,
            trd_env=trd_env,
        )
        if ret != RET_OK:
            raise RuntimeError(f"modify_order failed: {data}")
        new_order_id = str(data.iloc[0]["order_id"]) if len(data) > 0 else order_id
        return {"broker_order_id": new_order_id}

    def query_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")
        order_id = params["order_id"]
        trd_env = _map_trd_env(self.trade_env_str)

        ret, data = self.trade_ctx.order_list_query(
            order_id=order_id,
            trd_env=trd_env,
        )
        if ret != RET_OK:
            raise RuntimeError(f"query_order failed: {data}")
        if len(data) == 0:
            raise RuntimeError(f"Order {order_id} not found")
        return self._serialize_order(data.iloc[0])

    def query_orders(self, params: dict) -> list:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")
        trd_env = _map_trd_env(self.trade_env_str)

        ret, data = self.trade_ctx.order_list_query(trd_env=trd_env)
        if ret != RET_OK:
            raise RuntimeError(f"query_orders failed: {data}")

        result = []
        for _, row in data.iterrows():
            result.append(self._serialize_order(row))

        code_filter = params.get("code")
        if code_filter:
            result = [o for o in result if o["code"] == code_filter]
        return result

    def query_positions(self) -> list:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")
        trd_env = _map_trd_env(self.trade_env_str)

        ret, data = self.trade_ctx.position_list_query(trd_env=trd_env)
        if ret != RET_OK:
            raise RuntimeError(f"query_positions failed: {data}")

        result = []
        for _, row in data.iterrows():
            result.append(self._serialize_position(row))
        return result

    def query_account(self) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")
        trd_env = _map_trd_env(self.trade_env_str)

        ret, data = self.trade_ctx.accinfo_query(trd_env=trd_env)
        if ret != RET_OK:
            raise RuntimeError(f"query_account failed: {data}")

        row = data.iloc[0] if len(data) > 0 else {}
        return {
            "acc_id": str(row.get("acc_id", "")),
            "cash": float(row.get("cash", 0)),
            "frozen_cash": float(row.get("frozen_cash", 0)),
            "total_assets": float(row.get("total_assets", 0)),
            "market_val": float(row.get("market_val", 0)),
            "unrealized_pl": float(row.get("unrealized_pl", 0)),
            "realized_pl": float(row.get("realized_pl", 0)),
        }

    def subscribe_quote(self, params: dict) -> dict:
        if not self.quote_ctx:
            raise RuntimeError("Not connected")
        codes = params.get("codes", [])
        new_codes = [c for c in codes if c not in self._subscribed_codes]
        if new_codes:
            # Subscribe to basic quote type for real-time push
            for code in new_codes:
                ret, data = self.quote_ctx.subscribe(code, [SubType.QUOTE], subscribe_push=True)
                if ret != RET_OK:
                    log.warning("subscribe_quote failed for %s: %s", code, data)
                else:
                    self._subscribed_codes.add(code)
        return {"subscribed": list(self._subscribed_codes)}

    def unsubscribe_quote(self, params: dict) -> dict:
        if not self.quote_ctx:
            raise RuntimeError("Not connected")
        codes = params.get("codes", [])
        for code in codes:
            if code in self._subscribed_codes:
                ret, data = self.quote_ctx.unsubscribe(code, [SubType.QUOTE])
                if ret != RET_OK:
                    log.warning("unsubscribe_quote failed for %s: %s", code, data)
                self._subscribed_codes.discard(code)
        return {"subscribed": list(self._subscribed_codes)}

    # ── Serialisers ────────────────────────────────────────────

    @staticmethod
    def _serialize_order(row) -> dict:
        return {
            "order_id": str(row.get("order_id", "")),
            "code": row.get("code", ""),
            "trd_side": str(row.get("trd_side", "")),
            "order_type": str(row.get("order_type", "")),
            "price": float(row.get("price", 0)),
            "qty": int(row.get("qty", 0)),
            "dealt_qty": int(row.get("dealt_qty", 0)),
            "dealt_avg_price": float(row.get("dealt_avg_price", 0)),
            "order_status": str(row.get("order_status", "")),
            "create_time": str(row.get("create_time", "")),
            "updated_time": str(row.get("updated_time", "")),
            "remark": row.get("remark", ""),
        }

    @staticmethod
    def _serialize_position(row) -> dict:
        return {
            "code": row.get("code", ""),
            "stock_name": row.get("stock_name", ""),
            "qty": int(row.get("qty", 0)),
            "can_sell_qty": int(row.get("can_sell_qty", 0)),
            "today_buy_qty": int(row.get("today_buy_qty", 0)),
            "cost_price": float(row.get("cost_price", 0)),
            "market_val": float(row.get("market_val", 0)),
            "current_price": float(row.get("nominal_price", 0)),
            "unrealized_pl": float(row.get("unrealized_pl", 0)),
            "pl_ratio": float(row.get("pl_ratio", 0)),
            "position_side": str(row.get("position_side", "")),
        }


# ── Dispatch loop ─────────────────────────────────────────────────

METHODS = {
    "connect": "connect",
    "disconnect": "disconnect",
    "place_order": "place_order",
    "cancel_order": "cancel_order",
    "modify_order": "modify_order",
    "query_order": "query_order",
    "query_orders": "query_orders",
    "query_positions": "query_positions",
    "query_account": "query_account",
    "subscribe_quote": "subscribe_quote",
    "unsubscribe_quote": "unsubscribe_quote",
}

NO_PARAM_METHODS = {"connect", "disconnect", "query_positions", "query_account"}

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
            if method in NO_PARAM_METHODS:
                result = fn()
            else:
                result = fn(params)
            reply_ok(request_id, result)
        except Exception as exc:
            log.exception("Method %s failed", method)
            reply_err(request_id, str(exc))


if __name__ == "__main__":
    main()
