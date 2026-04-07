"""
futu_broker.py — Futu OpenD persistent NDJSON bridge.

Protocol (matches PersistentPythonProcess in TS):
  stdin  (line-delimited JSON): { "requestId": "...", "method": "...", "params": {...} }
  stdout (line-delimited JSON): { "requestId": "...", "ok": true, "data": ... }
                                { "event": "fill"|"quote"|"order_status"|"position_change", "data": ... }

Requires: futu-api (pip install futu-api)
FutuOpenD must be running and accessible before starting this process.
"""

import sys
import json
import threading
import time
import os
import logging
from typing import Any, Optional, Dict, Set

# ── futu-api imports (graceful degradation) ─────────────────────

try:
    from futu import (
        OpenQuoteContext,
        OpenSecTradeContext,
        RET_OK,
        RET_ERROR,
        TrdSide,
        TrdEnv,
        OrderType as FutuOrderType,
        OrderStatus as FutuOrderStatus,
        SubType,
        StockQuoteHandlerBase,
        OrderBookHandlerBase,
        TickerHandlerBase,
        TradeOrderHandlerBase,
        TradeDealHandlerBase,
    )
    HAS_FUTU = True
except ImportError:
    HAS_FUTU = False

logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger("futu_broker")

# ── NDJSON I/O helpers ──────────────────────────────────────────

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


# ── Futu quote subscription handler ────────────────────────────

class QuoteHandler(StockQuoteHandlerBase if HAS_FUTU else object):
    """Receives real-time quote pushes from Futu OpenD and forwards as NDJSON events."""

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK:
            return ret, data
        for _, row in data.iterrows():
            # Extract up to 10 levels of bid/ask from Futu push data
            bid_prices = []
            ask_prices = []
            bid_vols = []
            ask_vols = []
            for i in range(1, 11):
                bp = row.get(f"bid_price_{i}" if i > 1 else "bid_price", None)
                ap = row.get(f"ask_price_{i}" if i > 1 else "ask_price", None)
                bv = row.get(f"bid_vol_{i}" if i > 1 else "bid_vol", None)
                av = row.get(f"ask_vol_{i}" if i > 1 else "ask_vol", None)
                if bp is not None and float(bp) > 0:
                    bid_prices.append(float(bp))
                    bid_vols.append(int(bv) if bv is not None else 0)
                if ap is not None and float(ap) > 0:
                    ask_prices.append(float(ap))
                    ask_vols.append(int(av) if av is not None else 0)
            # Fallback: if no multi-level data, use single-level fields
            if not bid_prices:
                bp = row.get("bid_price", 0)
                if bp and float(bp) > 0:
                    bid_prices = [float(bp)]
                    bid_vols = [int(row.get("bid_vol", 0))]
            if not ask_prices:
                ap = row.get("ask_price", 0)
                if ap and float(ap) > 0:
                    ask_prices = [float(ap)]
                    ask_vols = [int(row.get("ask_vol", 0))]

            push_event("quote", {
                "stock_code": row["code"],
                "last_price": float(row.get("last_price", 0)),
                "open": float(row.get("open_price", 0)),
                "high": float(row.get("high_price", 0)),
                "low": float(row.get("low_price", 0)),
                "last_close": float(row.get("prev_close_price", 0)),
                "volume": int(row.get("volume", 0)),
                "amount": float(row.get("turnover", 0)),
                "bid_price": bid_prices,
                "ask_price": ask_prices,
                "bid_vol": bid_vols,
                "ask_vol": ask_vols,
                "time": int(time.time() * 1000),
            })
        return ret, data


class OrderBookHandler(OrderBookHandlerBase if HAS_FUTU else object):
    """Receives order book (depth) pushes from Futu OpenD and forwards as NDJSON events."""

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK:
            return ret, data
        code = data.get("code", "") if isinstance(data, dict) else ""
        bids = []
        asks = []
        if isinstance(data, dict):
            for item in data.get("Bid", []):
                bids.append({"price": float(item[0]), "volume": int(item[1]), "order_count": int(item[2])})
            for item in data.get("Ask", []):
                asks.append({"price": float(item[0]), "volume": int(item[1]), "order_count": int(item[2])})
        push_event("order_book", {
            "stock_code": code,
            "bids": bids,
            "asks": asks,
            "time": int(time.time() * 1000),
        })
        return ret, data


class TickerHandler(TickerHandlerBase if HAS_FUTU else object):
    """Receives tick-by-tick trade pushes from Futu OpenD and forwards as NDJSON events."""

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK:
            return ret, data
        for _, row in data.iterrows():
            push_event("ticker", {
                "stock_code": row.get("code", ""),
                "price": float(row.get("price", 0)),
                "volume": int(row.get("volume", 0)),
                "turnover": float(row.get("turnover", 0)),
                "ticker_direction": str(row.get("ticker_direction", "")),
                "sequence": int(row.get("sequence", 0)),
                "time": row.get("time", ""),
            })
        return ret, data


# ── Futu order status handler ──────────────────────────────────

class OrderHandler(TradeOrderHandlerBase if HAS_FUTU else object):
    """Receives order status change pushes from Futu and forwards as NDJSON events."""

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK:
            return ret, data
        for _, row in data.iterrows():
            push_event("order_status", {
                "order_id": str(row.get("order_id", "")),
                "stock_code": row.get("code", ""),
                "order_type": row.get("trd_side", ""),
                "price": float(row.get("price", 0)),
                "order_volume": int(row.get("qty", 0)),
                "traded_volume": int(row.get("dealt_qty", 0)),
                "traded_price": float(row.get("dealt_avg_price", 0)),
                "order_status": str(row.get("order_status", "")),
                "order_time": row.get("create_time", ""),
                "remark": row.get("remark", ""),
            })
        return ret, data


# ── Futu deal/fill handler ─────────────────────────────────────

class DealHandler(TradeDealHandlerBase if HAS_FUTU else object):
    """Receives trade deal/fill pushes from Futu and forwards as NDJSON events."""

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK:
            return ret, data
        for _, row in data.iterrows():
            value = float(row.get("price", 0)) * int(row.get("qty", 0))
            # Futu commission: estimate HK stock 万5 or A-share 万2.5
            commission = round(value * 0.0005, 4)
            push_event("fill", {
                "order_id": str(row.get("order_id", "")),
                "stock_code": row.get("code", ""),
                "traded_price": float(row.get("price", 0)),
                "traded_volume": int(row.get("qty", 0)),
                "traded_amount": value,
                "commission": commission,
                "time": row.get("create_time", ""),
            })
        return ret, data


# ── Futu order status mapping ──────────────────────────────────

FUTU_STATUS_MAP = {
    "NONE": "pending",
    "UNSUBMITTED": "pending",
    "WAITING_SUBMIT": "pending",
    "SUBMITTING": "submitted",
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
} if HAS_FUTU else {}


def _map_trd_env(env_str: str):
    """Map config string to Futu TrdEnv."""
    if not HAS_FUTU:
        return None
    if env_str == "SIMULATE":
        return TrdEnv.SIMULATE
    return TrdEnv.REAL


def _map_order_side(side: str):
    """Map order side string to Futu TrdSide."""
    if not HAS_FUTU:
        return None
    mapping = {
        "buy": TrdSide.BUY,
        "sell": TrdSide.SELL,
        "buy_open": TrdSide.BUY,
        "sell_close": TrdSide.SELL,
        "sell_open": TrdSide.SELL,
        "buy_close": TrdSide.BUY,
    }
    return mapping.get(side, TrdSide.BUY)


def _map_order_type(order_type: str):
    """Map order type string to Futu OrderType."""
    if not HAS_FUTU:
        return None
    if order_type == "market":
        return FutuOrderType.MARKET
    return FutuOrderType.NORMAL  # limit order


# ── Main broker class ───────────────────────────────────────────

class FutuBrokerBridge:
    def __init__(self, init_args: dict):
        self.host: str = init_args.get("opend_host", "127.0.0.1")
        self.port: int = int(init_args.get("opend_port", 11111))
        self.trd_env_str: str = init_args.get("trade_env", "REAL")
        self.pwd_unlock: str = init_args.get("pwd_unlock", "")
        self.quote_ctx: Optional[Any] = None
        self.trade_ctx: Optional[Any] = None
        self._subscribed_codes: Set[str] = set()
        self._quote_handler = QuoteHandler() if HAS_FUTU else None
        self._order_book_handler = OrderBookHandler() if HAS_FUTU else None
        self._ticker_handler = TickerHandler() if HAS_FUTU else None
        self._order_handler = OrderHandler() if HAS_FUTU else None
        self._deal_handler = DealHandler() if HAS_FUTU else None
        self._position_poll_thread: Optional[threading.Thread] = None
        self._position_poll_running = False
        self._last_positions: Dict[str, dict] = {}

    def connect(self) -> dict:
        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed. Run: pip install futu-api")

        # Open quote context
        self.quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
        self.quote_ctx.set_handler(self._quote_handler)
        self.quote_ctx.set_handler(self._order_book_handler)
        self.quote_ctx.set_handler(self._ticker_handler)

        # Open trade context
        trd_env = _map_trd_env(self.trd_env_str)
        self.trade_ctx = OpenSecTradeContext(
            host=self.host, port=self.port,
            filter_trdmarket=0,  # all markets
        )
        self.trade_ctx.set_handler(self._order_handler)
        self.trade_ctx.set_handler(self._deal_handler)

        # Unlock trade if password provided
        if self.pwd_unlock:
            ret, data = self.trade_ctx.unlock_trade(self.pwd_unlock)
            if ret != RET_OK:
                raise RuntimeError(f"Failed to unlock trade: {data}")

        # Start position change polling
        self._start_position_poll()

        return {"status": "connected", "host": self.host, "port": self.port}

    def disconnect(self) -> dict:
        self._stop_position_poll()
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
        side = _map_order_side(params.get("side", "buy"))
        order_type = _map_order_type(params.get("order_type", "limit"))
        price = float(params.get("price", 0))
        volume = int(params["volume"])
        trd_env = _map_trd_env(self.trd_env_str)
        remark = params.get("remark", "")

        ret, data = self.trade_ctx.place_order(
            price=price,
            qty=volume,
            code=stock_code,
            trd_side=side,
            order_type=order_type,
            trd_env=trd_env,
            remark=remark,
        )
        if ret != RET_OK:
            raise RuntimeError(f"place_order failed: {data}")

        order_id = str(data.iloc[0]["order_id"])
        return {"broker_order_id": order_id}

    def cancel_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")
        order_id = params["order_id"]
        trd_env = _map_trd_env(self.trd_env_str)

        ret, data = self.trade_ctx.modify_order(
            modify_order_op=1,  # CANCEL
            order_id=order_id,
            qty=0,
            price=0,
            trd_env=trd_env,
        )
        if ret != RET_OK:
            raise RuntimeError(f"cancel_order failed: {data}")
        return {"result": "ok"}

    def query_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")
        order_id = params["order_id"]
        trd_env = _map_trd_env(self.trd_env_str)

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
        trd_env = _map_trd_env(self.trd_env_str)
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
        trd_env = _map_trd_env(self.trd_env_str)

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
        trd_env = _map_trd_env(self.trd_env_str)

        ret, data = self.trade_ctx.accinfo_query(trd_env=trd_env)
        if ret != RET_OK:
            raise RuntimeError(f"query_account failed: {data}")

        row = data.iloc[0]
        return {
            "account_id": str(row.get("trdMarket", "")),
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
        sub_types = params.get("sub_types", ["QUOTE"])
        new_codes = [c for c in codes if c not in self._subscribed_codes]

        # Map sub_type strings to Futu SubType enums
        type_map = {
            "QUOTE": SubType.QUOTE,
            "ORDER_BOOK": SubType.ORDER_BOOK,
            "TICKER": SubType.TICKER,
        }
        futu_sub_types = [type_map[t] for t in sub_types if t in type_map]
        if not futu_sub_types:
            futu_sub_types = [SubType.QUOTE]

        for code in new_codes:
            ret, data = self.quote_ctx.subscribe(code, futu_sub_types, subscribe_push=True)
            if ret != RET_OK:
                log.warning("Failed to subscribe %s: %s", code, data)
            else:
                self._subscribed_codes.add(code)

        return {"subscribed": list(self._subscribed_codes)}

    def unsubscribe_quote(self, params: dict) -> dict:
        if not self.quote_ctx:
            raise RuntimeError("Not connected")
        codes = params.get("stock_codes", [])
        sub_types = params.get("sub_types", ["QUOTE"])

        type_map = {
            "QUOTE": SubType.QUOTE,
            "ORDER_BOOK": SubType.ORDER_BOOK,
            "TICKER": SubType.TICKER,
        }
        futu_sub_types = [type_map[t] for t in sub_types if t in type_map]
        if not futu_sub_types:
            futu_sub_types = [SubType.QUOTE]

        for code in codes:
            if code in self._subscribed_codes:
                ret, data = self.quote_ctx.unsubscribe(code, futu_sub_types)
                if ret == RET_OK:
                    self._subscribed_codes.discard(code)
                else:
                    log.warning("Failed to unsubscribe %s: %s", code, data)

        return {"subscribed": list(self._subscribed_codes)}

    # ── Position change polling (Futu doesn't have native position push) ──

    def get_quote_snapshot(self, params: dict) -> list:
        """Get current quote snapshot for given codes without subscribing to push."""
        if not self.quote_ctx:
            raise RuntimeError("Not connected")
        codes = params.get("stock_codes", [])
        if not codes:
            return []

        ret, data = self.quote_ctx.get_market_snapshot(codes)
        if ret != RET_OK:
            raise RuntimeError(f"get_market_snapshot failed: {data}")

        result = []
        for _, row in data.iterrows():
            result.append({
                "stock_code": row.get("code", ""),
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
        return result

    def _start_position_poll(self):
        """Start a background thread that polls positions every 5s and emits change events."""
        self._position_poll_running = True
        self._position_poll_thread = threading.Thread(
            target=self._position_poll_loop, daemon=True
        )
        self._position_poll_thread.start()

    def _stop_position_poll(self):
        self._position_poll_running = False
        if self._position_poll_thread:
            self._position_poll_thread.join(timeout=6)
            self._position_poll_thread = None

    def _position_poll_loop(self):
        """Poll positions every 5 seconds and emit position_change events on diff."""
        while self._position_poll_running:
            try:
                if self.trade_ctx:
                    trd_env = _map_trd_env(self.trd_env_str)
                    ret, data = self.trade_ctx.position_list_query(trd_env=trd_env)
                    if ret == RET_OK:
                        current: Dict[str, dict] = {}
                        for _, row in data.iterrows():
                            pos = self._serialize_position(row)
                            current[pos["stock_code"]] = pos

                        # Detect changes
                        all_codes = set(current.keys()) | set(self._last_positions.keys())
                        for code in all_codes:
                            old = self._last_positions.get(code)
                            new = current.get(code)
                            if old != new:
                                push_event("position_change", {
                                    "stock_code": code,
                                    "previous": old,
                                    "current": new,
                                })

                        self._last_positions = current
            except Exception as exc:
                log.warning("Position poll error: %s", exc)

            time.sleep(5)

    # ── Serialisers ──────────────────────────────────────────────

    @staticmethod
    def _serialize_order(row) -> dict:
        status_str = str(row.get("order_status", ""))
        return {
            "order_id": str(row.get("order_id", "")),
            "stock_code": row.get("code", ""),
            "order_type": str(row.get("trd_side", "")),
            "price": float(row.get("price", 0)),
            "order_volume": int(row.get("qty", 0)),
            "traded_volume": int(row.get("dealt_qty", 0)),
            "traded_price": float(row.get("dealt_avg_price", 0)),
            "order_status": status_str,
            "order_time": row.get("create_time", ""),
            "remark": row.get("remark", ""),
        }

    @staticmethod
    def _serialize_position(row) -> dict:
        volume = int(row.get("qty", 0))
        avg_price = float(row.get("cost_price", 0))
        market_value = float(row.get("market_val", 0))
        last_price = float(row.get("nominal_price", 0))
        unrealized_pnl = float(row.get("unrealized_pl", 0))
        unrealized_pnl_pct = None
        if avg_price > 0:
            unrealized_pnl_pct = (last_price - avg_price) / avg_price

        return {
            "stock_code": row.get("code", ""),
            "stock_name": row.get("stock_name", ""),
            "volume": volume,
            "can_use_volume": volume,  # Futu doesn't have T+1 freeze for HK
            "frozen_volume": 0,
            "today_buy_volume": 0,
            "avg_price": avg_price,
            "market_value": market_value,
            "last_price": last_price if last_price > 0 else None,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pnl_pct,
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
    "get_quote_snapshot": "get_quote_snapshot",
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
