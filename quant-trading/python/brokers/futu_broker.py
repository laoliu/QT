"""
futu_broker.py — Futu OpenD persistent NDJSON bridge.

Protocol (matches PersistentPythonProcess in TS):
  stdin  (line-delimited JSON): { "requestId": "...", "method": "...", "params": {...} }
  stdout (line-delimited JSON): { "requestId": "...", "ok": true, "data": ... }
                                { "event": "fill"|"quote"|"order_status", "data": ... }

Requires: futu-api (pip install futu-api)
Futu OpenD must be running (default: 127.0.0.1:11111) before starting this process.
"""

import sys
import json
import threading
import time
import os
import logging
from typing import Any, Optional, Dict, List

# ── futu-api imports (graceful degradation) ────────────────────────

try:
    from futu import (
        OpenQuoteContext,
        OpenSecTradeContext,
        RET_OK,
        RET_ERROR,
        TrdEnv,
        TrdSide,
        OrderType as FutuOrderType,
        OrderStatus as FutuOrderStatus,
        ModifyOrderOp,
        TrdMarket,
        SubType,
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

def send(obj: dict) -> None:
    """Write a single JSON line to stdout."""
    print(json.dumps(obj, ensure_ascii=False), flush=True)

def reply_ok(request_id: str, data: Any) -> None:
    send({"requestId": request_id, "ok": True, "data": data})

def reply_err(request_id: str, error: str) -> None:
    send({"requestId": request_id, "ok": False, "error": error})

def push_event(event: str, data: Any) -> None:
    send({"event": event, "data": data})

# ── Futu order status mapping ────────────────────────────────────

# Futu OrderStatus → our status string
FUTU_STATUS_MAP: Dict[str, str] = {}
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

def map_futu_status(status_str: str) -> str:
    """Map Futu order status to our unified status."""
    return FUTU_STATUS_MAP.get(status_str, "submitted")

# ── Futu side mapping ────────────────────────────────────────────

def map_side_to_futu(side: str) -> str:
    """Map our side to Futu TrdSide."""
    if not HAS_FUTU:
        return side
    mapping = {
        "buy": TrdSide.BUY,
        "sell": TrdSide.SELL,
        "buy_open": TrdSide.BUY,
        "sell_close": TrdSide.SELL,
        "sell_open": TrdSide.SELL,      # short sell
        "buy_close": TrdSide.BUY,
        "short": TrdSide.SELL_SHORT,
        "cover": TrdSide.BUY_BACK,
    }
    return mapping.get(side, TrdSide.BUY)

def map_futu_side(trd_side: str) -> str:
    """Map Futu TrdSide to our side string."""
    if not HAS_FUTU:
        return "buy"
    if trd_side == TrdSide.BUY:
        return "buy"
    elif trd_side == TrdSide.SELL:
        return "sell"
    elif trd_side == TrdSide.SELL_SHORT:
        return "short"
    elif trd_side == TrdSide.BUY_BACK:
        return "cover"
    return "buy"

def map_order_type_to_futu(order_type: str) -> str:
    """Map our order type to Futu OrderType."""
    if not HAS_FUTU:
        return order_type
    mapping = {
        "market": FutuOrderType.MARKET,
        "limit": FutuOrderType.NORMAL,
        "stop": FutuOrderType.STOP,
        "stop_limit": FutuOrderType.STOP_LIMIT,
    }
    return mapping.get(order_type, FutuOrderType.NORMAL)

# ── Futu market mapping ──────────────────────────────────────────

def map_market_to_futu(market: str) -> str:
    """Map our market type to Futu TrdMarket."""
    if not HAS_FUTU:
        return market
    mapping = {
        "hk_stock": TrdMarket.HK,
        "us_stock": TrdMarket.US,
        "cn_stock": TrdMarket.CN,
        "futures": TrdMarket.HKCC,
    }
    return mapping.get(market, TrdMarket.HK)

# ── Futu callbacks ───────────────────────────────────────────────

class OrderStatusHandler(TradeOrderHandlerBase if HAS_FUTU else object):
    """Receives order status push notifications from Futu and forwards as NDJSON events."""

    def on_recv_rsp(self, rsp_pb):
        try:
            ret, data = super().on_recv_rsp(rsp_pb)
            if ret != RET_OK:
                return
            for _, row in data.iterrows():
                order_id = str(row.get("order_id", ""))
                status = map_futu_status(row.get("order_status", ""))
                push_event("order_status", {
                    "order_id": order_id,
                    "code": row.get("code", ""),
                    "trd_side": str(row.get("trd_side", "")),
                    "order_type": str(row.get("order_type", "")),
                    "price": float(row.get("price", 0)),
                    "qty": float(row.get("qty", 0)),
                    "dealt_qty": float(row.get("dealt_qty", 0)),
                    "dealt_avg_price": float(row.get("dealt_avg_price", 0)),
                    "status": status,
                    "order_id_str": order_id,
                    "create_time": row.get("create_time", ""),
                    "updated_time": row.get("updated_time", ""),
                })
        except Exception as exc:
            log.exception("OrderStatusHandler error: %s", exc)

class FillHandler(TradeDealHandlerBase if HAS_FUTU else object):
    """Receives fill (deal) push notifications from Futu and forwards as NDJSON events."""

    def on_recv_rsp(self, rsp_pb):
        try:
            ret, data = super().on_recv_rsp(rsp_pb)
            if ret != RET_OK:
                return
            for _, row in data.iterrows():
                order_id = str(row.get("order_id", ""))
                dealt_qty = float(row.get("qty", 0))
                dealt_price = float(row.get("price", 0))
                value = dealt_price * dealt_qty
                # Estimate commission: HK stocks 0.03% + platform fee
                commission = round(value * 0.0003, 4)
                push_event("fill", {
                    "order_id": order_id,
                    "code": row.get("code", ""),
                    "trd_side": str(row.get("trd_side", "")),
                    "dealt_price": dealt_price,
                    "dealt_qty": dealt_qty,
                    "dealt_amount": value,
                    "commission": commission,
                    "time": row.get("create_time", ""),
                })
        except Exception as exc:
            log.exception("FillHandler error: %s", exc)

class QuoteHandler(StockQuoteHandlerBase if HAS_FUTU else object):
    """Receives real-time quote push from Futu and forwards as NDJSON events."""

    def on_recv_rsp(self, rsp_pb):
        try:
            ret, data = super().on_recv_rsp(rsp_pb)
            if ret != RET_OK:
                return
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
                    "timestamp": row.get("data_time", ""),
                })
        except Exception as exc:
            log.exception("QuoteHandler error: %s", exc)


# ── Main broker class ─────────────────────────────────────────────

class FutuBrokerBridge:
    def __init__(self, init_args: dict):
        self.host: str = init_args.get("host", "127.0.0.1")
        self.port: int = int(init_args.get("port", 11111))
        self.trade_env: str = init_args.get("trade_env", "REAL")
        self.pwd_unlock: str = init_args.get("pwd_unlock", "")
        self.market: str = init_args.get("market", "HK")
        self.quote_ctx: Optional[Any] = None
        self.trade_ctx: Optional[Any] = None
        self._subscribed_codes: set = set()

    def connect(self) -> dict:
        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed. Run: pip install futu-api")

        # Create quote context
        self.quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
        self.quote_ctx.set_handler(QuoteHandler())

        # Create trade context
        trd_env = TrdEnv.REAL if self.trade_env == "REAL" else TrdEnv.SIMULATE
        self.trade_ctx = OpenSecTradeContext(
            filter_trdmarket=map_market_to_futu(self._resolve_market()),
            host=self.host,
            port=self.port,
            security_firm=None,
        )

        # Set order/fill handlers
        self.trade_ctx.set_handler(OrderStatusHandler())
        self.trade_ctx.set_handler(FillHandler())

        # Unlock trade if password provided
        if self.pwd_unlock:
            ret, data = self.trade_ctx.unlock_trade(self.pwd_unlock, is_unlock=True)
            if ret != RET_OK:
                raise RuntimeError(f"Failed to unlock trade: {data}")

        return {"status": "connected", "host": self.host, "port": self.port}

    def _resolve_market(self) -> str:
        """Resolve market string to our MarketType."""
        m = self.market.upper()
        if m in ("HK", "HK_STOCK"):
            return "hk_stock"
        elif m in ("US", "US_STOCK"):
            return "us_stock"
        elif m in ("CN", "CN_STOCK", "SH", "SZ"):
            return "cn_stock"
        return "hk_stock"

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
        side = params.get("side", "buy")
        order_type = params.get("order_type", "limit")
        price = float(params.get("price", 0))
        qty = float(params.get("qty", 0))
        remark = params.get("remark", "")
        trd_env = TrdEnv.REAL if self.trade_env == "REAL" else TrdEnv.SIMULATE

        futu_side = map_side_to_futu(side)
        futu_order_type = map_order_type_to_futu(order_type)

        ret, data = self.trade_ctx.place_order(
            price=price,
            qty=qty,
            code=code,
            trd_side=futu_side,
            order_type=futu_order_type,
            trd_env=trd_env,
            remark=remark,
        )
        if ret != RET_OK:
            raise RuntimeError(f"place_order failed: {data}")

        order_id = str(data.iloc[0]["order_id"])
        return {"broker_order_id": order_id}

    def modify_order(self, params: dict) -> dict:
        """Modify an existing order (price/quantity).

        Futu supports native order modification via modify_order API,
        unlike QMT which requires cancel+resubmit.
        """
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        order_id = params["order_id"]
        price = float(params.get("price", 0))
        qty = float(params.get("qty", 0))
        trd_env = TrdEnv.REAL if self.trade_env == "REAL" else TrdEnv.SIMULATE

        ret, data = self.trade_ctx.modify_order(
            modify_order_op=ModifyOrderOp.NORMAL,
            order_id=order_id,
            price=price,
            qty=qty,
            trd_env=trd_env,
        )
        if ret != RET_OK:
            raise RuntimeError(f"modify_order failed: {data}")

        return {"order_id": order_id, "status": "modified"}

    def cancel_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        order_id = params["order_id"]
        trd_env = TrdEnv.REAL if self.trade_env == "REAL" else TrdEnv.SIMULATE

        ret, data = self.trade_ctx.modify_order(
            modify_order_op=ModifyOrderOp.CANCEL,
            order_id=order_id,
            price=0,
            qty=0,
            trd_env=trd_env,
        )
        if ret != RET_OK:
            raise RuntimeError(f"cancel_order failed: {data}")

        return {"order_id": order_id, "status": "cancelled"}

    def query_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        order_id = params["order_id"]
        trd_env = TrdEnv.REAL if self.trade_env == "REAL" else TrdEnv.SIMULATE

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

        code = params.get("code")
        status_filter = params.get("status_filter")
        trd_env = TrdEnv.REAL if self.trade_env == "REAL" else TrdEnv.SIMULATE

        kwargs: Dict[str, Any] = {"trd_env": trd_env}
        if code:
            kwargs["code"] = code
        if status_filter:
            kwargs["status_filter_list"] = status_filter

        ret, data = self.trade_ctx.order_list_query(**kwargs)
        if ret != RET_OK:
            raise RuntimeError(f"query_orders failed: {data}")

        result = []
        for _, row in data.iterrows():
            result.append(self._serialize_order(row))
        return result

    def query_positions(self) -> list:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        trd_env = TrdEnv.REAL if self.trade_env == "REAL" else TrdEnv.SIMULATE

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

        trd_env = TrdEnv.REAL if self.trade_env == "REAL" else TrdEnv.SIMULATE

        ret, data = self.trade_ctx.accinfo_query(trd_env=trd_env)
        if ret != RET_OK:
            raise RuntimeError(f"query_account failed: {data}")

        row = data.iloc[0]
        return {
            "cash": float(row.get("cash", 0)),
            "total_assets": float(row.get("total_assets", 0)),
            "market_val": float(row.get("market_val", 0)),
            "frozen_cash": float(row.get("frozen_cash", 0)),
            "avl_withdrawal_cash": float(row.get("avl_withdrawal_cash", 0)),
            "unrealized_pl": float(row.get("unrealized_pl", 0)),
            "realized_pl": float(row.get("realized_pl", 0)),
        }

    def subscribe_quote(self, params: dict) -> dict:
        if not self.quote_ctx:
            raise RuntimeError("Not connected")

        codes = params.get("codes", [])
        new_codes = [c for c in codes if c not in self._subscribed_codes]
        if new_codes:
            ret, data = self.quote_ctx.subscribe(new_codes, [SubType.QUOTE])
            if ret != RET_OK:
                log.warning("subscribe_quote failed: %s", data)
            else:
                self._subscribed_codes.update(new_codes)
        return {"subscribed": list(self._subscribed_codes)}

    def unsubscribe_quote(self, params: dict) -> dict:
        if not self.quote_ctx:
            raise RuntimeError("Not connected")

        codes = params.get("codes", [])
        unsub = [c for c in codes if c in self._subscribed_codes]
        if unsub:
            ret, data = self.quote_ctx.unsubscribe(unsub, [SubType.QUOTE])
            if ret != RET_OK:
                log.warning("unsubscribe_quote failed: %s", data)
            else:
                for c in unsub:
                    self._subscribed_codes.discard(c)
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
            "qty": float(row.get("qty", 0)),
            "dealt_qty": float(row.get("dealt_qty", 0)),
            "dealt_avg_price": float(row.get("dealt_avg_price", 0)),
            "status": map_futu_status(row.get("order_status", "")),
            "create_time": row.get("create_time", ""),
            "updated_time": row.get("updated_time", ""),
            "remark": row.get("remark", ""),
        }

    @staticmethod
    def _serialize_position(row) -> dict:
        qty = float(row.get("qty", 0))
        cost_price = float(row.get("cost_price", 0))
        market_val = float(row.get("market_val", 0))
        nominal_price = float(row.get("nominal_price", 0))

        unrealized_pl = float(row.get("pl_val", 0))
        unrealized_pl_pct = float(row.get("pl_ratio", 0))

        return {
            "code": row.get("code", ""),
            "stock_name": row.get("stock_name", ""),
            "qty": qty,
            "can_sell_qty": float(row.get("can_sell_qty", 0)),
            "cost_price": cost_price,
            "nominal_price": nominal_price,
            "market_val": market_val,
            "unrealized_pl": unrealized_pl,
            "unrealized_pl_pct": unrealized_pl_pct,
            "today_buy_qty": float(row.get("today_buy_qty", 0)),
            "today_sell_qty": float(row.get("today_sell_qty", 0)),
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
