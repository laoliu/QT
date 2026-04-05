"""
futu_broker.py — Futu OpenD persistent NDJSON bridge.

Protocol (matches PersistentPythonProcess in TS):
  stdin  (line-delimited JSON): { "requestId": "...", "method": "...", "params": {...} }
  stdout (line-delimited JSON): { "requestId": "...", "ok": true, "data": ... }
                                { "event": "fill"|"quote"|"order_status", "data": ... }

Requires: futu-api (pip install futu-api)
Futu OpenD must be running on the same machine (or accessible via network) before starting.
"""

import sys
import json
import threading
import time
import os
import logging
from typing import Any, Optional, Dict, List

# ── futu-api imports (graceful degradation) ─────────────────────────

try:
    from futu import (
        OpenQuoteContext,
        OpenSecTradeContext,
        TrdEnv,
        TrdMarket,
        TrdSide,
        OrderType as FutuOrderType,
        OrderStatus as FutuOrderStatus,
        RET_OK,
        RET_ERROR,
        ModifyOrderOp,
        SubType,
        StockQuoteHandlerBase,
        TradeOrderHandlerBase,
        TradeDealHandlerBase,
    )
    HAS_FUTU = True
except ImportError:
    HAS_FUTU = False

logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger("futu_broker")

# ── NDJSON I/O helpers ──────────────────────────────────────────────

def send(obj: dict) -> None:
    """Write a single JSON line to stdout."""
    print(json.dumps(obj, ensure_ascii=False), flush=True)

def reply_ok(request_id: str, data: Any) -> None:
    send({"requestId": request_id, "ok": True, "data": data})

def reply_err(request_id: str, error: str) -> None:
    send({"requestId": request_id, "ok": False, "error": error})

def push_event(event: str, data: Any) -> None:
    send({"event": event, "data": data})


# ── Futu order status mapping ──────────────────────────────────────

def _map_futu_status(status_str: str) -> str:
    """Map Futu OrderStatus string to our canonical status."""
    # Futu status strings from futu-api
    if not HAS_FUTU:
        return "submitted"
    mapping = {
        "NONE": "pending",
        "UNSUBMITTED": "pending",
        "WAITING_SUBMIT": "pending",
        "SUBMITTING": "pending",
        "SUBMIT_FAILED": "rejected",
        "SUBMITTED": "submitted",
        "FILLED_PART": "partial",
        "FILLED_ALL": "filled",
        "CANCELLING_PART": "submitted",
        "CANCELLING_ALL": "submitted",
        "CANCELLED_PART": "cancelled",
        "CANCELLED_ALL": "cancelled",
        "FAILED": "rejected",
        "DISABLED": "rejected",
        "DELETED": "cancelled",
    }
    return mapping.get(str(status_str), "submitted")


def _map_side_to_futu(side: str):
    """Map our canonical side to Futu TrdSide."""
    if not HAS_FUTU:
        return None
    mapping = {
        "buy": TrdSide.BUY,
        "sell": TrdSide.SELL,
        "short": TrdSide.SELL_SHORT,
        "cover": TrdSide.BUY_BACK,
    }
    return mapping.get(side, TrdSide.BUY)


def _map_futu_side(trd_side) -> str:
    """Map Futu TrdSide to our canonical side."""
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
    if order_type == "market":
        return FutuOrderType.MARKET
    # Default to NORMAL (limit order)
    return FutuOrderType.NORMAL


def _get_trd_env(env_str: str):
    """Get Futu TrdEnv from config string."""
    if not HAS_FUTU:
        return None
    if env_str == "SIMULATE":
        return TrdEnv.SIMULATE
    return TrdEnv.REAL


def _get_trd_market(market: str):
    """Map market string to Futu TrdMarket."""
    if not HAS_FUTU:
        return None
    mapping = {
        "cn_stock": TrdMarket.CN,
        "hk_stock": TrdMarket.HK,
        "us_stock": TrdMarket.US,
        "fund": TrdMarket.CN,
    }
    return mapping.get(market, TrdMarket.HK)


# ── Symbol normalisation ──────────────────────────────────────────

def to_futu_symbol(symbol: str) -> str:
    """
    Convert plain code to Futu format.
    Futu uses: HK.00700, US.AAPL, SH.600519, SZ.000001
    """
    if "." in symbol and symbol.split(".")[0] in ("HK", "US", "SH", "SZ"):
        return symbol  # already in Futu format

    # Strip any existing suffix like .SH, .SZ
    code = symbol.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")

    # If it looks like a US stock ticker (letters only)
    if code.isalpha():
        return f"US.{code}"

    # Numeric code → CN stock
    try:
        n = int(code)
    except ValueError:
        return symbol

    # Shanghai: 5xxxxx (ETF/funds), 6xxxxx (main board + STAR), 9xxxxx (B-share)
    if n >= 500000:
        return f"SH.{code}"
    # Shenzhen: 0xxxxx, 1xxxxx, 2xxxxx, 3xxxxx
    return f"SZ.{code}"


def from_futu_symbol(futu_code: str) -> str:
    """Convert Futu symbol back to plain code. HK.00700 → 00700, SH.600519 → 600519."""
    parts = futu_code.split(".")
    if len(parts) == 2 and parts[0] in ("HK", "US", "SH", "SZ"):
        return parts[1]
    return futu_code


def detect_market_from_futu(futu_code: str) -> str:
    """Detect market type from Futu symbol prefix."""
    if futu_code.startswith("HK."):
        return "hk_stock"
    if futu_code.startswith("US."):
        return "us_stock"
    if futu_code.startswith("SH.") or futu_code.startswith("SZ."):
        return "cn_stock"
    return "hk_stock"  # default for Futu


# ── Futu event handlers ──────────────────────────────────────────

if HAS_FUTU:
    class QuoteHandler(StockQuoteHandlerBase):
        """Receives real-time quote pushes from Futu and forwards as NDJSON events."""
        def on_recv_rsp(self, rsp_str):
            ret_code, data = super().on_recv_rsp(rsp_str)
            if ret_code != RET_OK:
                return RET_ERROR, data
            for _, row in data.iterrows():
                push_event("quote", {
                    "stock_code": row["code"],
                    "last_price": float(row.get("last_price", 0)),
                    "open": float(row.get("open_price", 0)),
                    "high": float(row.get("high_price", 0)),
                    "low": float(row.get("low_price", 0)),
                    "last_close": float(row.get("prev_close_price", 0)),
                    "volume": int(row.get("volume", 0)),
                    "amount": float(row.get("turnover", 0)),
                    "bid_price": [float(row.get("bid_price", 0))],
                    "ask_price": [float(row.get("ask_price", 0))],
                    "bid_vol": [int(row.get("bid_vol", 0))],
                    "ask_vol": [int(row.get("ask_vol", 0))],
                    "time": int(time.time() * 1000),
                })
            return RET_OK, data

    class OrderHandler(TradeOrderHandlerBase):
        """Receives order status changes from Futu."""
        def on_recv_rsp(self, rsp_str):
            ret_code, data = super().on_recv_rsp(rsp_str)
            if ret_code != RET_OK:
                return RET_ERROR, data
            for _, row in data.iterrows():
                push_event("order_status", {
                    "order_id": str(row.get("order_id", "")),
                    "stock_code": row.get("code", ""),
                    "order_type": str(row.get("trd_side", "")),
                    "price": float(row.get("price", 0)),
                    "order_volume": int(row.get("qty", 0)),
                    "traded_volume": int(row.get("dealt_qty", 0)),
                    "traded_price": float(row.get("dealt_avg_price", 0)),
                    "order_status": str(row.get("order_status", "")),
                    "order_time": str(row.get("create_time", "")),
                })
            return RET_OK, data

    class DealHandler(TradeDealHandlerBase):
        """Receives fill (trade execution) notifications from Futu."""
        def on_recv_rsp(self, rsp_str):
            ret_code, data = super().on_recv_rsp(rsp_str)
            if ret_code != RET_OK:
                return RET_ERROR, data
            for _, row in data.iterrows():
                # Estimate commission
                value = float(row.get("price", 0)) * int(row.get("qty", 0))
                commission = round(value * 0.0005, 4)
                push_event("fill", {
                    "order_id": str(row.get("order_id", "")),
                    "stock_code": row.get("code", ""),
                    "traded_price": float(row.get("price", 0)),
                    "traded_volume": int(row.get("qty", 0)),
                    "traded_amount": value,
                    "commission": commission,
                    "time": str(row.get("create_time", "")),
                })
            return RET_OK, data


# ── Main broker class ──────────────────────────────────────────────

class FutuBrokerBridge:
    def __init__(self, init_args: dict):
        self.host: str = init_args.get("opend_host", "127.0.0.1")
        self.port: int = int(init_args.get("opend_port", 11111))
        self.trade_env_str: str = init_args.get("trade_env", "REAL")
        self.pwd_unlock: str = init_args.get("pwd_unlock", "")
        self.market_quote_type: int = int(init_args.get("market_quote_type", 1))

        self.quote_ctx: Optional[Any] = None
        self.trade_ctx: Optional[Any] = None
        self._subscribed_codes: set = set()

    def connect(self) -> dict:
        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed. Run: pip install futu-api")

        trd_env = _get_trd_env(self.trade_env_str)

        # Open quote context
        self.quote_ctx = OpenQuoteContext(host=self.host, port=self.port)

        # Open trade context (default to HK market; will be overridden per-order)
        self.trade_ctx = OpenSecTradeContext(
            host=self.host,
            port=self.port,
            filter_trdmarket=TrdMarket.NONE,  # accept all markets
        )

        # Unlock trade if password provided
        if self.pwd_unlock:
            ret, data = self.trade_ctx.unlock_trade(self.pwd_unlock, is_unlock=True)
            if ret != RET_OK:
                raise RuntimeError(f"Failed to unlock trade: {data}")

        # Register push handlers
        self.quote_ctx.set_handler(QuoteHandler())
        self.trade_ctx.set_handler(OrderHandler())
        self.trade_ctx.set_handler(DealHandler())

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

        futu_code = params["stock_code"]
        side = params["side"]
        order_type = params.get("order_type", "limit")
        price = float(params.get("price", 0))
        volume = int(params["volume"])
        trd_env = _get_trd_env(self.trade_env_str)

        futu_side = _map_side_to_futu(side)
        futu_order_type = _map_order_type_to_futu(order_type)

        ret, data = self.trade_ctx.place_order(
            price=price,
            qty=volume,
            code=futu_code,
            trd_side=futu_side,
            order_type=futu_order_type,
            trd_env=trd_env,
            remark=params.get("remark", ""),
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
            modify_order_op=ModifyOrderOp.CANCEL,
            order_id=order_id,
            qty=0,
            price=0,
            trd_env=trd_env,
        )

        if ret != RET_OK:
            raise RuntimeError(f"cancel_order failed: {data}")

        return {"result": "cancelled"}

    def modify_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        order_id = params["order_id"]
        price = float(params.get("price", 0))
        volume = int(params.get("volume", 0))
        trd_env = _get_trd_env(self.trade_env_str)

        ret, data = self.trade_ctx.modify_order(
            modify_order_op=ModifyOrderOp.NORMAL,
            order_id=order_id,
            qty=volume,
            price=price,
            trd_env=trd_env,
        )

        if ret != RET_OK:
            raise RuntimeError(f"modify_order failed: {data}")

        return {"order_id": order_id, "status": "modified"}

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

        ret, data = self.trade_ctx.order_list_query(
            trd_env=trd_env,
        )

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

        for code in new_codes:
            ret, data = self.quote_ctx.subscribe(code, [SubType.QUOTE], subscribe_push=True)
            if ret == RET_OK:
                self._subscribed_codes.add(code)
            else:
                log.warning("subscribe_quote failed for %s: %s", code, data)

        return {"subscribed": list(self._subscribed_codes)}

    def unsubscribe_quote(self, params: dict) -> dict:
        if not self.quote_ctx:
            raise RuntimeError("Not connected")

        codes = params.get("stock_codes", [])
        for code in codes:
            if code in self._subscribed_codes:
                ret, data = self.quote_ctx.unsubscribe(code, [SubType.QUOTE])
                if ret == RET_OK:
                    self._subscribed_codes.discard(code)

        return {"subscribed": list(self._subscribed_codes)}

    # ── Serialisers ──────────────────────────────────────────────

    @staticmethod
    def _serialize_order(row) -> dict:
        return {
            "order_id": str(row.get("order_id", "")),
            "stock_code": str(row.get("code", "")),
            "trd_side": str(row.get("trd_side", "")),
            "price": float(row.get("price", 0)),
            "order_volume": int(row.get("qty", 0)),
            "traded_volume": int(row.get("dealt_qty", 0)),
            "traded_price": float(row.get("dealt_avg_price", 0)),
            "order_status": str(row.get("order_status", "")),
            "order_time": str(row.get("create_time", "")),
        }

    @staticmethod
    def _serialize_position(row) -> dict:
        volume = int(row.get("qty", 0))
        avg_price = float(row.get("cost_price", 0))
        market_value = float(row.get("market_val", 0))
        last_price = float(row.get("last_price", 0))

        unrealized_pnl = float(row.get("unrealized_pl", 0))
        unrealized_pnl_pct = None
        if avg_price > 0 and volume > 0:
            unrealized_pnl_pct = (last_price - avg_price) / avg_price

        stock_name = str(row.get("stock_name", ""))
        stock_code = str(row.get("code", ""))

        # Futu reports today_buy_qty and today_sell_qty
        today_buy_volume = int(row.get("today_buy_qty", 0))

        # can_use_volume: for T+0 markets (HK, US) = qty; for T+1 (CN) = qty - today_buy_qty
        can_use_volume = volume - today_buy_volume

        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "volume": volume,
            "can_use_volume": max(can_use_volume, 0),
            "frozen_volume": 0,
            "today_buy_volume": today_buy_volume,
            "avg_price": avg_price,
            "market_value": market_value,
            "last_price": last_price if last_price > 0 else None,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pnl_pct,
        }


# ── Dispatch loop ──────────────────────────────────────────────────

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
