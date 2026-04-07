"""
futu_broker.py — Futu OpenD persistent NDJSON bridge.

Protocol (matches PersistentPythonProcess in TS):
  stdin  (line-delimited JSON): { "requestId": "...", "method": "...", "params": {...} }
  stdout (line-delimited JSON): { "requestId": "...", "ok": true, "data": ... }
                                { "event": "fill"|"quote"|"order_status", "data": ... }

Requires: futu-api (pip install futu-api)
FutuOpenD must be running and accessible before starting this process.
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
        TrdMarket,
        TrdSide,
        OrderType,
        OrderStatus as FutuOrderStatus,
        ModifyOrderOp,
        RET_OK,
        RET_ERROR,
        TradeOrderHandlerBase,
        TradeDealHandlerBase,
        StockQuoteHandlerBase,
    )
    HAS_FUTU = True
except ImportError:
    HAS_FUTU = False

logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger("futu_broker")

# ── NDJSON I/O helpers ────────────────────────────────────────────

_io_lock = threading.Lock()

def send(obj: dict) -> None:
    with _io_lock:
        print(json.dumps(obj, ensure_ascii=False), flush=True)

def reply_ok(request_id: str, data: Any) -> None:
    send({"requestId": request_id, "ok": True, "data": data})

def reply_err(request_id: str, error: str) -> None:
    send({"requestId": request_id, "ok": False, "error": error})

def push_event(event: str, data: Any) -> None:
    send({"event": event, "data": data})

# ── Futu order status mapping ────────────────────────────────────

FUTU_STATUS_MAP = {}
if HAS_FUTU:
    FUTU_STATUS_MAP = {
        FutuOrderStatus.NONE: "pending",
        FutuOrderStatus.UNSUBMITTED: "pending",
        FutuOrderStatus.WAITING_SUBMIT: "pending",
        FutuOrderStatus.SUBMITTING: "submitted",
        FutuOrderStatus.SUBMIT_FAILED: "rejected",
        FutuOrderStatus.SUBMITTED: "submitted",
        FutuOrderStatus.FILLED_PART: "partial",
        FutuOrderStatus.FILLED_ALL: "filled",
        FutuOrderStatus.CANCELLING_PART: "submitted",
        FutuOrderStatus.CANCELLING_ALL: "submitted",
        FutuOrderStatus.CANCELLED_PART: "cancelled",
        FutuOrderStatus.CANCELLED_ALL: "cancelled",
        FutuOrderStatus.FAILED: "rejected",
        FutuOrderStatus.DISABLED: "rejected",
        FutuOrderStatus.DELETED: "cancelled",
    }


def map_futu_status(status_val) -> str:
    return FUTU_STATUS_MAP.get(status_val, "submitted")


def map_side_to_futu(side: str):
    if not HAS_FUTU:
        return None
    mapping = {
        "buy": TrdSide.BUY,
        "sell": TrdSide.SELL,
        "buy_open": TrdSide.BUY,
        "sell_close": TrdSide.SELL,
        "short": TrdSide.SELL_SHORT,
        "cover": TrdSide.BUY_BACK,
    }
    return mapping.get(side, TrdSide.BUY)


def map_futu_side(side_val) -> str:
    if not HAS_FUTU:
        return "buy"
    mapping = {
        TrdSide.BUY: "buy",
        TrdSide.SELL: "sell",
        TrdSide.SELL_SHORT: "short",
        TrdSide.BUY_BACK: "cover",
    }
    return mapping.get(side_val, "buy")


def map_order_type_to_futu(order_type: str):
    if not HAS_FUTU:
        return None
    mapping = {
        "market": OrderType.MARKET,
        "limit": OrderType.NORMAL,
        "stop": OrderType.STOP,
        "stop_limit": OrderType.STOP_LIMIT,
    }
    return mapping.get(order_type, OrderType.NORMAL)


def map_trd_env(env_str: str):
    if not HAS_FUTU:
        return None
    if env_str == "REAL":
        return TrdEnv.REAL
    return TrdEnv.SIMULATE


# ── Futu callbacks ────────────────────────────────────────────────

class OrderUpdateHandler(TradeOrderHandlerBase if HAS_FUTU else object):
    """Receives async order status pushes from Futu and forwards as NDJSON events."""

    def on_recv_rsp(self, rsp_pb):
        try:
            ret, data = super().on_recv_rsp(rsp_pb)
            if ret != RET_OK:
                return
            for _, row in data.iterrows():
                push_event("order_status", {
                    "order_id": str(row.get("order_id", "")),
                    "stock_code": row.get("code", ""),
                    "order_type": str(row.get("order_type", "")),
                    "price": float(row.get("price", 0)),
                    "order_volume": int(row.get("qty", 0)),
                    "traded_volume": int(row.get("dealt_qty", 0)),
                    "traded_price": float(row.get("dealt_avg_price", 0)),
                    "order_status": map_futu_status(row.get("order_status", "")),
                    "order_status_raw": str(row.get("order_status", "")),
                    "trd_side": str(row.get("trd_side", "")),
                    "strategy_error_msg": str(row.get("remark", "") or ""),
                    "order_time": str(row.get("create_time", "") or ""),
                    "updated_time": str(row.get("updated_time", "") or ""),
                })
        except Exception as e:
            log.exception("OrderUpdateHandler error: %s", e)


class DealUpdateHandler(TradeDealHandlerBase if HAS_FUTU else object):
    """Receives async fill/deal pushes from Futu."""

    def on_recv_rsp(self, rsp_pb):
        try:
            ret, data = super().on_recv_rsp(rsp_pb)
            if ret != RET_OK:
                return
            for _, row in data.iterrows():
                value = float(row.get("price", 0)) * int(row.get("qty", 0))
                # HK stock commission: ~0.03% (varies by broker)
                commission = round(value * 0.0003, 4)
                push_event("fill", {
                    "order_id": str(row.get("order_id", "")),
                    "deal_id": str(row.get("deal_id", "")),
                    "stock_code": row.get("code", ""),
                    "traded_price": float(row.get("price", 0)),
                    "traded_volume": int(row.get("qty", 0)),
                    "traded_amount": value,
                    "commission": commission,
                    "trd_side": str(row.get("trd_side", "")),
                    "time": str(row.get("create_time", "") or ""),
                })
        except Exception as e:
            log.exception("DealUpdateHandler error: %s", e)


class QuoteUpdateHandler(StockQuoteHandlerBase if HAS_FUTU else object):
    """Receives real-time quote pushes from Futu."""

    def on_recv_rsp(self, rsp_str):
        try:
            ret, data = super().on_recv_rsp(rsp_str)
            if ret != RET_OK:
                return
            for _, row in data.iterrows():
                push_event("quote", {
                    "stock_code": row.get("code", ""),
                    "last_price": float(row.get("last_price", 0)),
                    "open": float(row.get("open_price", 0)),
                    "high": float(row.get("high_price", 0)),
                    "low": float(row.get("low_price", 0)),
                    "last_close": float(row.get("prev_close_price", 0)),
                    "volume": int(row.get("volume", 0)),
                    "amount": float(row.get("turnover", 0)),
                    "bid_price": [float(row.get(f"bid_price_{i}", 0)) for i in range(1, 6)],
                    "ask_price": [float(row.get(f"ask_price_{i}", 0)) for i in range(1, 6)],
                    "bid_vol": [int(row.get(f"bid_vol_{i}", 0)) for i in range(1, 6)],
                    "ask_vol": [int(row.get(f"ask_vol_{i}", 0)) for i in range(1, 6)],
                    "time": int(time.time() * 1000),
                })
        except Exception as e:
            log.exception("QuoteUpdateHandler error: %s", e)


# ── Main broker class ─────────────────────────────────────────────

class FutuBrokerBridge:
    def __init__(self, init_args: dict):
        self.host: str = init_args.get("opend_host", "127.0.0.1")
        self.port: int = int(init_args.get("opend_port", 11111))
        self.trd_env_str: str = init_args.get("trade_env", "SIMULATE")
        self.pwd_unlock: str = init_args.get("pwd_unlock", "")
        self.quote_ctx: Optional[Any] = None
        self.trade_ctx: Optional[Any] = None
        self._subscribed_codes: set = set()
        self._retry_config = {
            "max_retries": int(init_args.get("max_retries", 3)),
            "retry_delay": float(init_args.get("retry_delay", 1.0)),
            "retry_backoff": float(init_args.get("retry_backoff", 2.0)),
        }

    def _retry_call(self, fn, *args, operation: str = "operation", **kwargs):
        """Execute a Futu API call with retry logic for transient failures."""
        max_retries = self._retry_config["max_retries"]
        delay = self._retry_config["retry_delay"]
        backoff = self._retry_config["retry_backoff"]
        last_err = None

        for attempt in range(max_retries + 1):
            try:
                ret, data = fn(*args, **kwargs)
                if ret == RET_OK:
                    return ret, data
                # Non-retryable errors: parameter errors, permission denied, etc.
                err_msg = str(data)
                if any(kw in err_msg.lower() for kw in [
                    "parameter", "invalid", "not support", "permission",
                    "unlock", "insufficient",
                ]):
                    raise RuntimeError(f"{operation} failed: {err_msg}")
                last_err = err_msg
            except RuntimeError:
                raise
            except Exception as e:
                last_err = str(e)

            if attempt < max_retries:
                log.warning(
                    "%s failed (attempt %d/%d): %s — retrying in %.1fs",
                    operation, attempt + 1, max_retries + 1, last_err, delay,
                )
                time.sleep(delay)
                delay *= backoff

        raise RuntimeError(f"{operation} failed after {max_retries + 1} attempts: {last_err}")

    def connect(self) -> dict:
        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed. Run: pip install futu-api")

        # Initialize quote context
        self.quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
        self.quote_ctx.set_handler(QuoteUpdateHandler())

        # Initialize trade context
        trd_env = map_trd_env(self.trd_env_str)
        self.trade_ctx = OpenSecTradeContext(
            host=self.host, port=self.port,
            filter_trdmarket=TrdMarket.HK,
        )
        self.trade_ctx.set_handler(OrderUpdateHandler())
        self.trade_ctx.set_handler(DealUpdateHandler())

        # Unlock trade if password provided (required for real trading)
        if self.pwd_unlock and trd_env == TrdEnv.REAL:
            ret, data = self.trade_ctx.unlock_trade(self.pwd_unlock)
            if ret != RET_OK:
                raise RuntimeError(f"Failed to unlock trade: {data}")

        return {
            "status": "connected",
            "host": self.host,
            "port": self.port,
            "trade_env": self.trd_env_str,
        }

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
        side = map_side_to_futu(params.get("side", "buy"))
        order_type = map_order_type_to_futu(params.get("order_type", "limit"))
        price = float(params.get("price", 0))
        quantity = int(params["volume"])
        trd_env = map_trd_env(self.trd_env_str)
        remark = params.get("remark", "")

        if order_type == OrderType.NORMAL and price <= 0:
            raise RuntimeError("Limit order requires a positive price")

        ret, data = self._retry_call(
            self.trade_ctx.place_order,
            price=price,
            qty=quantity,
            code=stock_code,
            trd_side=side,
            order_type=order_type,
            trd_env=trd_env,
            remark=remark[:64] if remark else "",
            operation="place_order",
        )

        order_id = str(data.iloc[0]["order_id"])
        return {"broker_order_id": order_id}

    def modify_order(self, params: dict) -> dict:
        """Modify an existing order (price and/or quantity). Futu supports native modification."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        order_id = str(params["order_id"])
        price = float(params.get("price", 0))
        quantity = int(params.get("volume", 0))
        trd_env = map_trd_env(self.trd_env_str)

        modify_args = {
            "order_id": order_id,
            "modify_order_op": ModifyOrderOp.NORMAL,
            "trd_env": trd_env,
        }
        if price > 0:
            modify_args["price"] = price
        if quantity > 0:
            modify_args["qty"] = quantity

        ret, data = self._retry_call(
            self.trade_ctx.modify_order,
            **modify_args,
            operation="modify_order",
        )

        return {
            "order_id": order_id,
            "result": "ok",
        }

    def cancel_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        order_id = str(params["order_id"])
        trd_env = map_trd_env(self.trd_env_str)

        ret, data = self._retry_call(
            self.trade_ctx.modify_order,
            modify_order_op=ModifyOrderOp.CANCEL,
            order_id=order_id,
            qty=0,
            price=0,
            trd_env=trd_env,
            operation="cancel_order",
        )

        return {"order_id": order_id, "result": "ok"}

    def query_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        order_id = str(params["order_id"])
        trd_env = map_trd_env(self.trd_env_str)

        ret, data = self._retry_call(
            self.trade_ctx.order_list_query,
            order_id=order_id,
            trd_env=trd_env,
            operation="query_order",
        )

        if data.empty:
            raise RuntimeError(f"Order {order_id} not found")

        row = data.iloc[0]
        return self._serialize_order(row)

    def query_orders(self, params: dict) -> list:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        trd_env = map_trd_env(self.trd_env_str)
        stock_code = params.get("stock_code")

        kwargs = {"trd_env": trd_env}
        if stock_code:
            kwargs["code"] = stock_code

        ret, data = self._retry_call(
            self.trade_ctx.order_list_query,
            **kwargs,
            operation="query_orders",
        )

        if data.empty:
            return []

        result = []
        for _, row in data.iterrows():
            result.append(self._serialize_order(row))
        return result

    def query_positions(self, _params: dict = {}) -> list:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        trd_env = map_trd_env(self.trd_env_str)

        ret, data = self._retry_call(
            self.trade_ctx.position_list_query,
            trd_env=trd_env,
            operation="query_positions",
        )

        if data.empty:
            return []

        result = []
        for _, row in data.iterrows():
            result.append(self._serialize_position(row))
        return result

    def query_account(self, _params: dict = {}) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        trd_env = map_trd_env(self.trd_env_str)

        ret, data = self._retry_call(
            self.trade_ctx.accinfo_query,
            trd_env=trd_env,
            operation="query_account",
        )

        if data.empty:
            raise RuntimeError("Failed to query account info")

        row = data.iloc[0]
        return {
            "cash": float(row.get("cash", 0)),
            "frozen_cash": float(row.get("frozen_cash", 0)),
            "total_asset": float(row.get("total_assets", 0)),
            "market_value": float(row.get("market_val", 0)),
            "profit": float(row.get("unrealized_pl", 0)),
            "profit_ratio": float(row.get("unrealized_pl", 0)) / max(float(row.get("total_assets", 1)), 1),
        }

    def subscribe_quote(self, params: dict) -> dict:
        if not self.quote_ctx:
            raise RuntimeError("Not connected")

        codes = params.get("stock_codes", [])
        new_codes = [c for c in codes if c not in self._subscribed_codes]
        if new_codes:
            for code in new_codes:
                ret, data = self.quote_ctx.subscribe(code, "QUOTE", push=True)
                if ret == RET_OK:
                    self._subscribed_codes.add(code)
                else:
                    log.warning("Failed to subscribe quote for %s: %s", code, data)
        return {"subscribed": list(self._subscribed_codes)}

    def unsubscribe_quote(self, params: dict) -> dict:
        if not self.quote_ctx:
            raise RuntimeError("Not connected")

        codes = params.get("stock_codes", [])
        for code in codes:
            if code in self._subscribed_codes:
                self.quote_ctx.unsubscribe(code, "QUOTE")
                self._subscribed_codes.discard(code)
        return {"subscribed": list(self._subscribed_codes)}

    def query_deals(self, params: dict) -> list:
        """Query today's executed deals/fills."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        trd_env = map_trd_env(self.trd_env_str)
        stock_code = params.get("stock_code")

        kwargs = {"trd_env": trd_env}
        if stock_code:
            kwargs["code"] = stock_code

        ret, data = self._retry_call(
            self.trade_ctx.deal_list_query,
            **kwargs,
            operation="query_deals",
        )

        if data.empty:
            return []

        result = []
        for _, row in data.iterrows():
            value = float(row.get("price", 0)) * int(row.get("qty", 0))
            commission = round(value * 0.0003, 4)
            result.append({
                "order_id": str(row.get("order_id", "")),
                "deal_id": str(row.get("deal_id", "")),
                "stock_code": row.get("code", ""),
                "traded_price": float(row.get("price", 0)),
                "traded_volume": int(row.get("qty", 0)),
                "traded_amount": value,
                "commission": commission,
                "trd_side": str(row.get("trd_side", "")),
                "time": str(row.get("create_time", "") or ""),
            })
        return result

    # ── Serialisers ────────────────────────────────────────────

    @staticmethod
    def _serialize_order(row) -> dict:
        return {
            "order_id": str(row.get("order_id", "")),
            "stock_code": row.get("code", ""),
            "order_type": str(row.get("order_type", "")),
            "trd_side": str(row.get("trd_side", "")),
            "price": float(row.get("price", 0)),
            "order_volume": int(row.get("qty", 0)),
            "traded_volume": int(row.get("dealt_qty", 0)),
            "traded_price": float(row.get("dealt_avg_price", 0)),
            "order_status": map_futu_status(row.get("order_status", "")),
            "order_status_raw": str(row.get("order_status", "")),
            "strategy_error_msg": str(row.get("remark", "") or ""),
            "order_time": str(row.get("create_time", "") or ""),
            "updated_time": str(row.get("updated_time", "") or ""),
        }

    @staticmethod
    def _serialize_position(row) -> dict:
        qty = int(row.get("qty", 0))
        cost_price = float(row.get("cost_price", 0))
        current_price = float(row.get("nominal_price", 0))
        market_value = float(row.get("market_val", 0))
        unrealized_pnl = float(row.get("unrealized_pl", 0))
        unrealized_pnl_pct = 0.0
        if cost_price > 0:
            unrealized_pnl_pct = (current_price - cost_price) / cost_price

        return {
            "stock_code": row.get("code", ""),
            "stock_name": row.get("stock_name", ""),
            "volume": qty,
            "can_use_volume": qty,  # Futu doesn't have T+1 freeze for HK
            "frozen_volume": 0,
            "today_buy_volume": int(row.get("today_buy_qty", 0)),
            "avg_price": cost_price,
            "market_value": market_value,
            "last_price": current_price if current_price > 0 else None,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pnl_pct,
        }


# ── Dispatch loop ─────────────────────────────────────────────────

METHODS = {
    "connect": "connect",
    "disconnect": "disconnect",
    "place_order": "place_order",
    "modify_order": "modify_order",
    "cancel_order": "cancel_order",
    "query_order": "query_order",
    "query_orders": "query_orders",
    "query_positions": "query_positions",
    "query_account": "query_account",
    "subscribe_quote": "subscribe_quote",
    "unsubscribe_quote": "unsubscribe_quote",
    "query_deals": "query_deals",
}

def main():
    try:
        init_args = json.loads(os.environ.get("QUANT_INIT_ARGS", "{}"))
    except Exception:
        init_args = {}

    bridge = FutuBrokerBridge(init_args)

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
            if method in ("query_positions", "query_account", "disconnect", "connect"):
                result = fn()
            else:
                result = fn(params)
            reply_ok(request_id, result)
        except Exception as exc:
            log.exception("Method %s failed", method)
            reply_err(request_id, str(exc))


if __name__ == "__main__":
    main()
