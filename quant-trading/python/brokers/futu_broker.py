"""
futu_broker.py — Futu OpenD persistent NDJSON bridge.

Protocol (matches PersistentPythonProcess in TS):
  stdin  (line-delimited JSON): { "requestId": "...", "method": "...", "params": {...} }
  stdout (line-delimited JSON): { "requestId": "...", "ok": true, "data": ... }
                                { "event": "fill"|"quote"|"order_status", "data": {...} }

Requires: futu-api (pip install futu-api)
Futu OpenD must be running before starting this process.
"""

import sys
import json
import threading
import time
import os
import logging
from typing import Any, Optional

# ── futu-api imports (graceful degradation) ──────────────────────

try:
    from futu import (
        OpenQuoteContext,
        OpenSecTradeContext,
        TrdSide,
        TrdEnv,
        OrderType,
        OrderStatus,
        RET_OK,
        RET_ERROR,
        TrdMarket,
        SecurityFirm,
        SubType,
        KLType,
        StockQuoteHandlerBase,
        OrderBookHandlerBase,
        TradeOrderHandlerBase,
        TradeDealHandlerBase,
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


# ── Futu status mapping ──────────────────────────────────────────

FUTU_STATUS_MAP = {}
if HAS_FUTU:
    FUTU_STATUS_MAP = {
        OrderStatus.NONE: "pending",
        OrderStatus.UNSUBMITTED: "pending",
        OrderStatus.WAITING_SUBMIT: "pending",
        OrderStatus.SUBMITTING: "pending",
        OrderStatus.SUBMIT_FAILED: "rejected",
        OrderStatus.SUBMITTED: "submitted",
        OrderStatus.FILLED_PART: "partial",
        OrderStatus.FILLED_ALL: "filled",
        OrderStatus.CANCELLING_PART: "submitted",
        OrderStatus.CANCELLING_ALL: "submitted",
        OrderStatus.CANCELLED_PART: "cancelled",
        OrderStatus.CANCELLED_ALL: "cancelled",
        OrderStatus.FAILED: "rejected",
        OrderStatus.DISABLED: "rejected",
        OrderStatus.DELETED: "cancelled",
    }


def map_futu_status(status) -> str:
    """Map Futu OrderStatus to our standard status string."""
    return FUTU_STATUS_MAP.get(status, "submitted")


# ── Futu side mapping ────────────────────────────────────────────

SIDE_TO_FUTU = {}
FUTU_TO_SIDE = {}
if HAS_FUTU:
    SIDE_TO_FUTU = {
        "buy": TrdSide.BUY,
        "sell": TrdSide.SELL,
        "short": TrdSide.SELL_SHORT,
        "cover": TrdSide.BUY_BACK,
        "buy_open": TrdSide.BUY,
        "sell_close": TrdSide.SELL,
        "buy_close": TrdSide.BUY_BACK,
        "sell_open": TrdSide.SELL_SHORT,
    }
    FUTU_TO_SIDE = {
        TrdSide.BUY: "buy",
        TrdSide.SELL: "sell",
        TrdSide.SELL_SHORT: "short",
        TrdSide.BUY_BACK: "cover",
    }


def map_side_to_futu(side: str):
    """Map our side string to Futu TrdSide."""
    return SIDE_TO_FUTU.get(side, TrdSide.BUY if HAS_FUTU else 0)


def map_futu_side(futu_side) -> str:
    """Map Futu TrdSide to our standard side string."""
    return FUTU_TO_SIDE.get(futu_side, "buy")


# ── Order type mapping ────────────────────────────────────────────

def map_order_type_to_futu(order_type: str):
    """Map our order type to Futu OrderType."""
    if not HAS_FUTU:
        return 0
    if order_type == "market":
        return OrderType.MARKET
    elif order_type == "limit":
        return OrderType.NORMAL
    elif order_type == "stop":
        return OrderType.STOP
    elif order_type == "stop_limit":
        return OrderType.STOP_LIMIT
    elif order_type == "auction":
        return OrderType.AUCTION
    elif order_type == "auction_limit":
        return OrderType.AUCTION_LIMIT
    elif order_type == "special_limit":
        # HK odd-lot / special limit order
        return OrderType.SPECIAL_LIMIT
    return OrderType.NORMAL


# ── Market mapping ────────────────────────────────────────────────

def detect_trd_market(symbol: str):
    """Detect Futu TrdMarket from symbol code."""
    if not HAS_FUTU:
        return None
    sym = symbol.upper()
    if sym.startswith("HK."):
        return TrdMarket.HK
    elif sym.startswith("US."):
        return TrdMarket.US
    elif sym.startswith("SH.") or sym.startswith("SZ."):
        return TrdMarket.CN
    elif sym.startswith("SG."):
        return TrdMarket.SG
    elif sym.startswith("JP."):
        return TrdMarket.JP
    return TrdMarket.HK  # default to HK


def to_futu_symbol(symbol: str) -> str:
    """Convert plain symbol to Futu format (e.g. '00700' → 'HK.00700', 'AAPL' → 'US.AAPL').

    If already prefixed with market code, return as-is.
    """
    if "." in symbol and symbol.split(".")[0] in ("HK", "US", "SH", "SZ", "SG", "JP"):
        return symbol
    sym = symbol.strip()
    # Pure digits → likely HK or CN
    if sym.isdigit():
        code_len = len(sym)
        if code_len == 5:
            return f"HK.{sym}"
        elif code_len == 6:
            n = int(sym)
            if n >= 500000:
                return f"SH.{sym}"
            else:
                return f"SZ.{sym}"
        return f"HK.{sym.zfill(5)}"
    # Alphabetic → likely US
    if sym.isalpha():
        return f"US.{sym}"
    # If has .SH/.SZ suffix (QMT format) → convert
    if sym.endswith(".SH"):
        return f"SH.{sym[:-3]}"
    elif sym.endswith(".SZ"):
        return f"SZ.{sym[:-3]}"
    elif sym.endswith(".HK"):
        return f"HK.{sym[:-3]}"
    return symbol


def from_futu_symbol(futu_symbol: str) -> str:
    """Convert Futu symbol back to plain code (e.g. 'HK.00700' → '00700')."""
    parts = futu_symbol.split(".", 1)
    if len(parts) == 2 and parts[0] in ("HK", "US", "SH", "SZ", "SG", "JP"):
        return parts[1]
    return futu_symbol


# ── Push notification handlers ────────────────────────────────────

class FutuQuoteHandler(StockQuoteHandlerBase if HAS_FUTU else object):
    """Receives real-time quote pushes and forwards as NDJSON events."""

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK:
            return ret, data
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
                "bid_price": [float(row.get(f"bid_price", 0))],
                "ask_price": [float(row.get(f"ask_price", 0))],
                "bid_vol": [int(row.get(f"bid_vol", 0))],
                "ask_vol": [int(row.get(f"ask_vol", 0))],
                "time": int(time.time() * 1000),
            })
        return ret, data


class FutuOrderHandler(TradeOrderHandlerBase if HAS_FUTU else object):
    """Receives order status push notifications."""

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK:
            return ret, data
        for _, row in data.iterrows():
            status = map_futu_status(row.get("order_status", ""))
            push_event("order_status", {
                "order_id": str(row.get("order_id", "")),
                "stock_code": row.get("code", ""),
                "order_type": row.get("trd_side", ""),
                "price": float(row.get("price", 0)),
                "order_volume": int(row.get("qty", 0)),
                "traded_volume": int(row.get("dealt_qty", 0)),
                "traded_price": float(row.get("dealt_avg_price", 0)),
                "order_status": status,
                "order_time": row.get("create_time", ""),
            })
        return ret, data


class FutuDealHandler(TradeDealHandlerBase if HAS_FUTU else object):
    """Receives fill/deal push notifications."""

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK:
            return ret, data
        for _, row in data.iterrows():
            value = float(row.get("price", 0)) * int(row.get("qty", 0))
            push_event("fill", {
                "order_id": str(row.get("order_id", "")),
                "stock_code": row.get("code", ""),
                "traded_price": float(row.get("price", 0)),
                "traded_volume": int(row.get("qty", 0)),
                "traded_amount": value,
                "commission": 0,  # Futu does not push commission per deal
                "time": row.get("create_time", ""),
            })
        return ret, data


class FutuOrderBookHandler(OrderBookHandlerBase if HAS_FUTU else object):
    """Receives order book push notifications and forwards as NDJSON events."""

    def on_recv_rsp(self, rsp_pb):
        ret, data = super().on_recv_rsp(rsp_pb)
        if ret != RET_OK:
            return ret, data
        stock_code = data.get("code", "")
        bids = []
        asks = []
        for item in data.get("Bid", []):
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                bids.append({"price": float(item[0]), "volume": int(item[1]), "order_num": int(item[2]) if len(item) > 2 else 0})
        for item in data.get("Ask", []):
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                asks.append({"price": float(item[0]), "volume": int(item[1]), "order_num": int(item[2]) if len(item) > 2 else 0})
        push_event("orderbook", {
            "stock_code": stock_code,
            "bids": bids,
            "asks": asks,
        })
        return ret, data


# ── Main broker class ─────────────────────────────────────────────

class FutuBrokerBridge:
    def __init__(self, init_args: dict):
        self.host: str = init_args.get("host", "127.0.0.1")
        self.port: int = int(init_args.get("port", 11111))
        self.trade_env = None
        self.trade_pwd: str = init_args.get("trade_pwd", "")
        self.account_id: str = init_args.get("account_id", "")
        self.quote_ctx: Optional[Any] = None
        self.trade_ctx: Optional[Any] = None
        self._subscribed_codes: set = set()

        if HAS_FUTU:
            env_str = init_args.get("trade_env", "SIMULATE")
            self.trade_env = TrdEnv.REAL if env_str == "REAL" else TrdEnv.SIMULATE

    def connect(self) -> dict:
        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed. Run: pip install futu-api")

        # Open quote context
        self.quote_ctx = OpenQuoteContext(host=self.host, port=self.port)

        # Open trade context
        self.trade_ctx = OpenSecTradeContext(
            host=self.host,
            port=self.port,
            filter_trdmarket=TrdMarket.NONE,
            security_firm=SecurityFirm.FUTUSECURITIES,
        )

        # Unlock trading if password provided
        if self.trade_pwd and self.trade_env == TrdEnv.REAL:
            ret, data = self.trade_ctx.unlock_trade(self.trade_pwd)
            if ret != RET_OK:
                raise RuntimeError(f"Failed to unlock trading: {data}")

        # Set up push handlers
        self.trade_ctx.set_handler(FutuOrderHandler())
        self.trade_ctx.set_handler(FutuDealHandler())
        self.quote_ctx.set_handler(FutuQuoteHandler())
        self.quote_ctx.set_handler(FutuOrderBookHandler())

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
        order_type_str = params.get("order_type", "limit")
        price = float(params.get("price", 0))
        volume = int(params["volume"])
        trd_market = detect_trd_market(stock_code)

        futu_side = map_side_to_futu(side)
        futu_order_type = map_order_type_to_futu(order_type_str)

        ret, data = self.trade_ctx.place_order(
            price=price,
            qty=volume,
            code=stock_code,
            trd_side=futu_side,
            order_type=futu_order_type,
            trd_env=self.trade_env,
            trd_market=trd_market,
        )

        if ret != RET_OK:
            raise RuntimeError(f"place_order failed: {data}")

        order_id = str(data.iloc[0]["order_id"])
        return {"broker_order_id": order_id}

    def cancel_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        order_id = params["order_id"]
        trd_market = None
        if "stock_code" in params:
            trd_market = detect_trd_market(params["stock_code"])

        ret, data = self.trade_ctx.modify_order(
            modify_order_op=2,  # CANCEL
            order_id=order_id,
            qty=0,
            price=0,
            trd_env=self.trade_env,
            trd_market=trd_market,
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

        ret, data = self.trade_ctx.modify_order(
            modify_order_op=1,  # NORMAL (modify)
            order_id=order_id,
            qty=volume,
            price=price,
            trd_env=self.trade_env,
        )

        if ret != RET_OK:
            raise RuntimeError(f"modify_order failed: {data}")
        return {"result": "modified"}

    def query_order(self, params: dict) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        order_id = params["order_id"]
        ret, data = self.trade_ctx.order_list_query(
            order_id=order_id,
            trd_env=self.trade_env,
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

        stock_code = params.get("stock_code")
        code_filter = [stock_code] if stock_code else []

        ret, data = self.trade_ctx.order_list_query(
            code=code_filter[0] if code_filter else "",
            trd_env=self.trade_env,
        )

        if ret != RET_OK:
            raise RuntimeError(f"query_orders failed: {data}")

        result = []
        for _, row in data.iterrows():
            result.append(self._serialize_order(row))
        return result

    def query_positions(self, _params: dict = {}) -> list:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        ret, data = self.trade_ctx.position_list_query(trd_env=self.trade_env)

        if ret != RET_OK:
            raise RuntimeError(f"query_positions failed: {data}")

        result = []
        for _, row in data.iterrows():
            result.append(self._serialize_position(row))
        return result

    def query_account(self, _params: dict = {}) -> dict:
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        ret, data = self.trade_ctx.accinfo_query(trd_env=self.trade_env)

        if ret != RET_OK:
            raise RuntimeError(f"query_account failed: {data}")

        row = data.iloc[0]
        return {
            "account_id": self.account_id or "futu",
            "cash": float(row.get("cash", 0)),
            "frozen_cash": float(row.get("frozen_cash", 0)),
            "total_asset": float(row.get("total_assets", 0)),
            "market_value": float(row.get("market_val", 0)),
            "profit": float(row.get("unrealized_pl", 0)),
            "profit_ratio": float(row.get("unrealized_pl", 0)) / max(float(row.get("total_assets", 1)), 1),
            "today_profit": float(row.get("realized_pl", 0)),
            "power": float(row.get("power", 0)),
            "max_power_short": float(row.get("max_power_short", 0)) if "max_power_short" in data.columns else None,
            "maintenance_margin": float(row.get("maintenance_margin", 0)) if "maintenance_margin" in data.columns else None,
        }

    def subscribe_quote(self, params: dict) -> dict:
        if not self.quote_ctx:
            raise RuntimeError("Not connected")

        codes = params.get("stock_codes", [])
        new_codes = [c for c in codes if c not in self._subscribed_codes]
        if new_codes:
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

        codes = params.get("stock_codes", [])
        for code in codes:
            ret, data = self.quote_ctx.unsubscribe(code, [SubType.QUOTE])
            if ret == RET_OK:
                self._subscribed_codes.discard(code)
        return {"subscribed": list(self._subscribed_codes)}

    def ping(self, _params: dict = {}) -> dict:
        """Health check — try querying account to verify connectivity."""
        if not self.trade_ctx:
            return {"alive": False}
        try:
            ret, _ = self.trade_ctx.accinfo_query(trd_env=self.trade_env)
            return {"alive": ret == RET_OK}
        except Exception:
            return {"alive": False}

    def query_history_orders(self, params: dict) -> list:
        """Query historical orders across multiple days."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        start = params.get("start", "")
        end = params.get("end", "")
        stock_code = params.get("stock_code", "")

        ret, data = self.trade_ctx.history_order_list_query(
            status_filter_list=[],
            code=stock_code,
            start=start,
            end=end,
            trd_env=self.trade_env,
        )

        if ret != RET_OK:
            raise RuntimeError(f"query_history_orders failed: {data}")

        result = []
        for _, row in data.iterrows():
            result.append(self._serialize_order(row))
        return result

    def query_history_deals(self, params: dict) -> list:
        """Query historical deals/fills across multiple days."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        start = params.get("start", "")
        end = params.get("end", "")
        stock_code = params.get("stock_code", "")

        ret, data = self.trade_ctx.history_deal_list_query(
            code=stock_code,
            start=start,
            end=end,
            trd_env=self.trade_env,
        )

        if ret != RET_OK:
            raise RuntimeError(f"query_history_deals failed: {data}")

        result = []
        for _, row in data.iterrows():
            value = float(row.get("price", 0)) * int(row.get("qty", 0))
            result.append({
                "deal_id": str(row.get("deal_id", "")),
                "order_id": str(row.get("order_id", "")),
                "stock_code": row.get("code", ""),
                "stock_name": row.get("stock_name", ""),
                "side": map_futu_side(row.get("trd_side", "")) if HAS_FUTU else "buy",
                "price": float(row.get("price", 0)),
                "qty": int(row.get("qty", 0)),
                "amount": value,
                "time": row.get("create_time", ""),
            })
        return result

    def query_deals(self, params: dict) -> list:
        """Query today's deal/trade history."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        stock_code = params.get("stock_code")
        code_filter = stock_code if stock_code else ""

        ret, data = self.trade_ctx.deal_list_query(
            code=code_filter,
            trd_env=self.trade_env,
        )

        if ret != RET_OK:
            raise RuntimeError(f"query_deals failed: {data}")

        result = []
        for _, row in data.iterrows():
            value = float(row.get("price", 0)) * int(row.get("qty", 0))
            result.append({
                "deal_id": str(row.get("deal_id", "")),
                "order_id": str(row.get("order_id", "")),
                "stock_code": row.get("code", ""),
                "stock_name": row.get("stock_name", ""),
                "side": map_futu_side(row.get("trd_side", "")) if HAS_FUTU else "buy",
                "price": float(row.get("price", 0)),
                "qty": int(row.get("qty", 0)),
                "amount": value,
                "time": row.get("create_time", ""),
            })
        return result

    def get_klines(self, params: dict) -> list:
        """Fetch K-line (candlestick) data for a symbol."""
        if not self.quote_ctx:
            raise RuntimeError("Not connected")

        stock_code = params["stock_code"]
        kl_type_str = params.get("kl_type", "K_DAY")
        count = int(params.get("count", 100))

        # Map string to Futu KLType
        kl_type = KLType.K_DAY if HAS_FUTU else None
        if HAS_FUTU:
            kl_map = {
                "K_1M": KLType.K_1M,
                "K_5M": KLType.K_5M,
                "K_15M": KLType.K_15M,
                "K_30M": KLType.K_30M,
                "K_60M": KLType.K_60M,
                "K_DAY": KLType.K_DAY,
                "K_WEEK": KLType.K_WEEK,
                "K_MON": KLType.K_MON,
            }
            kl_type = kl_map.get(kl_type_str, KLType.K_DAY)

        ret, data, _ = self.quote_ctx.request_history_kline(
            stock_code,
            ktype=kl_type,
            max_count=count,
        )

        if ret != RET_OK:
            raise RuntimeError(f"get_klines failed: {data}")

        result = []
        for _, row in data.iterrows():
            result.append({
                "time_key": row.get("time_key", ""),
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": int(row.get("volume", 0)),
                "turnover": float(row.get("turnover", 0)),
                "turnover_rate": float(row.get("turnover_rate", 0)) if "turnover_rate" in row else None,
            })
        return result

    def subscribe_orderbook(self, params: dict) -> dict:
        """Subscribe to order book (bid/ask depth) for a symbol."""
        if not self.quote_ctx:
            raise RuntimeError("Not connected")

        codes = params.get("stock_codes", [])
        for code in codes:
            ret, data = self.quote_ctx.subscribe(code, [SubType.ORDER_BOOK], subscribe_push=True)
            if ret != RET_OK:
                log.warning("subscribe_orderbook failed for %s: %s", code, data)
        return {"subscribed_orderbook": codes}

    def get_orderbook(self, params: dict) -> dict:
        """Get current order book snapshot for a symbol."""
        if not self.quote_ctx:
            raise RuntimeError("Not connected")

        stock_code = params["stock_code"]

        # Need to subscribe first
        ret, data = self.quote_ctx.subscribe(stock_code, [SubType.ORDER_BOOK])
        if ret != RET_OK:
            raise RuntimeError(f"subscribe for orderbook failed: {data}")

        ret, data = self.quote_ctx.get_order_book(stock_code)
        if ret != RET_OK:
            raise RuntimeError(f"get_orderbook failed: {data}")

        bids = []
        asks = []
        for _, row in data.get("Bid", []):
            bids.append({"price": float(row[0]), "volume": int(row[1]), "order_num": int(row[2]) if len(row) > 2 else 0})
        for _, row in data.get("Ask", []):
            asks.append({"price": float(row[0]), "volume": int(row[1]), "order_num": int(row[2]) if len(row) > 2 else 0})

        return {
            "stock_code": stock_code,
            "bids": bids,
            "asks": asks,
        }

    def get_market_snapshot(self, params: dict) -> list:
        """Get real-time snapshot for one or more symbols."""
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
                "stock_name": row.get("name", ""),
                "last_price": float(row.get("last_price", 0)),
                "open": float(row.get("open_price", 0)),
                "high": float(row.get("high_price", 0)),
                "low": float(row.get("low_price", 0)),
                "prev_close": float(row.get("prev_close_price", 0)),
                "volume": int(row.get("volume", 0)),
                "turnover": float(row.get("turnover", 0)),
                "turnover_rate": float(row.get("turnover_rate", 0)) if "turnover_rate" in data.columns else None,
                "amplitude": float(row.get("amplitude", 0)) if "amplitude" in data.columns else None,
                "pe_ratio": float(row.get("pe_ratio", 0)) if "pe_ratio" in data.columns else None,
                "pb_ratio": float(row.get("pb_ratio", 0)) if "pb_ratio" in data.columns else None,
                "time": int(time.time() * 1000),
            })
        return result

    def exercise_option(self, params: dict) -> dict:
        """Exercise an option position (American-style)."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        stock_code = params["stock_code"]
        volume = int(params["volume"])
        trd_market = detect_trd_market(stock_code)

        # Futu uses place_order with SPECIAL_LIMIT or exercise-specific API
        # For HK/US options, exercise is done via the trade context
        try:
            ret, data = self.trade_ctx.exercise_assign(
                exercise_code=stock_code,
                exercise_qty=volume,
                trd_env=self.trade_env,
                trd_market=trd_market,
            )
            if ret != RET_OK:
                return {"success": False, "message": f"行权失败: {data}"}
            return {"success": True, "message": f"行权请求已提交: {stock_code} x {volume}"}
        except AttributeError:
            # Fallback: older futu-api versions may not have exercise_assign
            return {"success": False, "message": "当前 futu-api 版本不支持行权操作，请升级到最新版本"}
        except Exception as e:
            return {"success": False, "message": f"行权异常: {str(e)}"}

    def query_option_positions(self, _params: dict = {}) -> list:
        """Query option-specific positions."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        ret, data = self.trade_ctx.position_list_query(trd_env=self.trade_env)
        if ret != RET_OK:
            raise RuntimeError(f"query_option_positions failed: {data}")

        result = []
        if HAS_FUTU:
            from futu import SecurityType
            # Futu SecurityType constants for option-like instruments
            option_types = set()
            for attr in ("WARRANT", "DRVT", "FUTURE", "PLATE_SET"):
                if hasattr(SecurityType, attr):
                    option_types.add(getattr(SecurityType, attr))

        for _, row in data.iterrows():
            code = row.get("code", "")
            # Filter to option/warrant/derivative contracts
            if HAS_FUTU and "sec_type" in data.columns:
                sec_type = row.get("sec_type", "")
                if sec_type and sec_type not in option_types:
                    continue
            else:
                # Fallback: detect by code pattern (options typically have longer codes or special prefixes)
                # Skip plain equity codes (5-digit HK, 6-digit CN, short alpha US)
                plain = code.split(".")[-1] if "." in code else code
                if plain.isdigit() and len(plain) <= 6:
                    continue
                if plain.isalpha() and len(plain) <= 5:
                    continue
            result.append(self._serialize_position(row))
        return result

    def query_fund_flow(self, params: dict) -> list:
        """Query account fund flow (deposit/withdrawal/dividend/commission) history."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        start = params.get("start", "")
        end = params.get("end", "")

        ret, data = self.trade_ctx.acctradinginfo_query(
            order_type=0,  # 0 = all
            code="",
            start=start,
            end=end,
            trd_env=self.trade_env,
        )

        if ret != RET_OK:
            raise RuntimeError(f"query_fund_flow failed: {data}")

        result = []
        for _, row in data.iterrows():
            result.append({
                "time": row.get("create_time", ""),
                "type": row.get("trd_side", ""),
                "stock_code": row.get("code", ""),
                "amount": float(row.get("amount", 0)),
                "balance": float(row.get("balance", 0)),
            })
        return result

    def query_ipo_list(self, params: dict) -> list:
        """Query available IPO list."""
        if not self.quote_ctx:
            raise RuntimeError("Not connected")

        market_str = params.get("market", "HK")

        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed")

        market_map = {
            "HK": TrdMarket.HK,
            "US": TrdMarket.US,
        }
        trd_market = market_map.get(market_str.upper(), TrdMarket.HK)

        try:
            ret, data = self.quote_ctx.get_ipo_list(trd_market)
            if ret != RET_OK:
                raise RuntimeError(f"query_ipo_list failed: {data}")

            result = []
            for _, row in data.iterrows():
                result.append({
                    "stock_code": row.get("code", ""),
                    "stock_name": row.get("name", ""),
                    "list_time": row.get("list_time", ""),
                    "ipo_price_min": float(row.get("ipo_price_min", 0)) if "ipo_price_min" in data.columns else None,
                    "ipo_price_max": float(row.get("ipo_price_max", 0)) if "ipo_price_max" in data.columns else None,
                    "lot_size": int(row.get("lot_size", 0)) if "lot_size" in data.columns else None,
                    "apply_time": row.get("apply_time", "") if "apply_time" in data.columns else None,
                    "apply_end_time": row.get("apply_end_time", "") if "apply_end_time" in data.columns else None,
                })
            return result
        except AttributeError:
            raise RuntimeError("当前 futu-api 版本不支持 IPO 查询，请升级到最新版本")

    def subscribe_ipo(self, params: dict) -> dict:
        """Subscribe to an IPO."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        stock_code = params["stock_code"]
        quantity = int(params["quantity"])
        price = float(params.get("price", 0))

        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed")

        try:
            ret, data = self.trade_ctx.subscribe_ipo(
                code=stock_code,
                qty=quantity,
                price=price if price > 0 else None,
                trd_env=self.trade_env,
            )
            if ret != RET_OK:
                return {"success": False, "message": f"IPO 认购失败: {data}"}
            return {"success": True, "message": f"IPO 认购请求已提交: {stock_code} x {quantity}"}
        except AttributeError:
            return {"success": False, "message": "当前 futu-api 版本不支持 IPO 认购，请升级到最新版本"}
        except Exception as e:
            return {"success": False, "message": f"IPO 认购异常: {str(e)}"}

    def currency_exchange(self, params: dict) -> dict:
        """Exchange currency between HKD/USD/CNH for cross-market trading."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        from_currency = params.get("from_currency", "HKD")
        to_currency = params.get("to_currency", "USD")
        amount = float(params["amount"])

        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed")

        try:
            ret, data = self.trade_ctx.currency_swap(
                from_currency=from_currency,
                to_currency=to_currency,
                amount=amount,
                trd_env=self.trade_env,
            )
            if ret != RET_OK:
                return {"success": False, "message": f"换汇失败: {data}"}
            return {"success": True, "message": f"换汇请求已提交: {amount} {from_currency} → {to_currency}"}
        except AttributeError:
            return {"success": False, "message": "当前 futu-api 版本不支持换汇操作，请升级到最新版本"}
        except Exception as e:
            return {"success": False, "message": f"换汇异常: {str(e)}"}

    def change_position_cost(self, params: dict) -> dict:
        """Adjust position cost price (after corporate actions, etc.)."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        stock_code = params["stock_code"]
        new_cost = float(params["cost_price"])

        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed")

        try:
            ret, data = self.trade_ctx.modify_position(
                code=stock_code,
                cost_price=new_cost,
                trd_env=self.trade_env,
            )
            if ret != RET_OK:
                return {"success": False, "message": f"调整持仓成本失败: {data}"}
            return {"success": True, "message": f"持仓成本已调整: {stock_code} → {new_cost}"}
        except AttributeError:
            return {"success": False, "message": "当前 futu-api 版本不支持调整持仓成本，请升级到最新版本"}
        except Exception as e:
            return {"success": False, "message": f"调整持仓成本异常: {str(e)}"}

    # ── Option chain methods ──────────────────────────────────

    def get_option_expiration(self, params: dict) -> list:
        """Get available option expiration dates for an underlying."""
        if not self.quote_ctx:
            raise RuntimeError("Not connected")
        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed")

        stock_code = params["stock_code"]
        try:
            ret, data = self.quote_ctx.get_option_expiration_date(code=stock_code)
            if ret != RET_OK:
                raise RuntimeError(f"get_option_expiration failed: {data}")
            result = []
            for _, row in data.iterrows():
                result.append({
                    "strike_time": row.get("strike_time", ""),
                    "option_expiry_date_distance": int(row.get("option_expiry_date_distance", 0)),
                })
            return result
        except AttributeError:
            raise RuntimeError("当前 futu-api 版本不支持期权到期日查询")

    def get_option_chain(self, params: dict) -> list:
        """Get option chain for an underlying at a specific expiration date."""
        if not self.quote_ctx:
            raise RuntimeError("Not connected")
        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed")

        stock_code = params["stock_code"]
        start = params.get("start", "")
        end = params.get("end", "")

        try:
            ret, data = self.quote_ctx.get_option_chain(
                code=stock_code,
                start=start,
                end=end,
            )
            if ret != RET_OK:
                raise RuntimeError(f"get_option_chain failed: {data}")
            result = []
            for _, row in data.iterrows():
                result.append({
                    "code": row.get("code", ""),
                    "name": row.get("name", ""),
                    "lot_size": int(row.get("lot_size", 0)),
                    "stock_type": row.get("stock_type", ""),
                    "option_type": row.get("option_type", ""),
                    "stock_owner": row.get("stock_owner", ""),
                    "strike_time": row.get("strike_time", ""),
                    "strike_price": float(row.get("strike_price", 0)),
                    "suspension": bool(row.get("suspension", False)),
                    "stock_id": int(row.get("stock_id", 0)),
                })
            return result
        except AttributeError:
            raise RuntimeError("当前 futu-api 版本不支持期权链查询")

    def get_stock_basicinfo(self, params: dict) -> list:
        """Get basic info for stocks (name, lot size, listing status, etc.)."""
        if not self.quote_ctx:
            raise RuntimeError("Not connected")
        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed")

        from futu import SecurityType as SecType
        market_str = params.get("market", "HK")
        sec_type = params.get("sec_type", "STOCK")

        market_map = {"HK": "HK", "US": "US", "SH": "SH", "SZ": "SZ", "CN": "SH"}
        sec_type_map = {
            "STOCK": SecType.STOCK,
            "WARRANT": SecType.WARRANT,
            "ETF": SecType.ETF,
            "BOND": SecType.BOND,
        }

        futu_market = market_map.get(market_str.upper(), "HK")
        futu_sec_type = sec_type_map.get(sec_type.upper(), SecType.STOCK)
        codes = params.get("stock_codes", [])

        try:
            if codes:
                ret, data = self.quote_ctx.get_stock_basicinfo(futu_market, futu_sec_type, codes)
            else:
                ret, data = self.quote_ctx.get_stock_basicinfo(futu_market, futu_sec_type)
            if ret != RET_OK:
                raise RuntimeError(f"get_stock_basicinfo failed: {data}")
            result = []
            for _, row in data.iterrows():
                result.append({
                    "code": row.get("code", ""),
                    "name": row.get("name", ""),
                    "lot_size": int(row.get("lot_size", 0)),
                    "stock_type": row.get("stock_type", ""),
                    "listing_date": row.get("listing_date", ""),
                })
            return result
        except AttributeError:
            raise RuntimeError("当前 futu-api 版本不支持股票基本信息查询")

    # ── Serialisers ────────────────────────────────────────────

    @staticmethod
    def _serialize_order(row) -> dict:
        status = map_futu_status(row.get("order_status", ""))
        side_val = row.get("trd_side", "")
        side = map_futu_side(side_val) if HAS_FUTU else "buy"
        return {
            "order_id": str(row.get("order_id", "")),
            "stock_code": row.get("code", ""),
            "side": side,
            "price": float(row.get("price", 0)),
            "order_volume": int(row.get("qty", 0)),
            "traded_volume": int(row.get("dealt_qty", 0)),
            "traded_price": float(row.get("dealt_avg_price", 0)),
            "order_status": status,
            "order_time": row.get("create_time", ""),
        }

    @staticmethod
    def _serialize_position(row) -> dict:
        qty = int(row.get("qty", 0))
        cost_price = float(row.get("cost_price", 0))
        market_val = float(row.get("market_val", 0))
        current_price = float(row.get("nominal_price", 0))
        unrealized_pnl = float(row.get("unrealized_pl", 0))
        today_buy_qty = int(row.get("today_buy_qty", 0))

        # T+1 tracking: for CN A-shares, today_buy_qty cannot be sold today
        # can_use_volume = total qty - today's buy qty (T+1 rule)
        code = row.get("code", "")
        is_cn_stock = code.startswith("SH.") or code.startswith("SZ.")
        if is_cn_stock:
            can_use_volume = max(0, qty - today_buy_qty)
            frozen_volume = today_buy_qty
        else:
            # HK/US stocks can be sold on the same day (T+0)
            can_use_volume = qty
            frozen_volume = 0

        unrealized_pnl_pct = None
        if cost_price > 0 and qty > 0:
            cost_value = cost_price * qty
            unrealized_pnl_pct = unrealized_pnl / cost_value if cost_value > 0 else None

        return {
            "stock_code": row.get("code", ""),
            "stock_name": row.get("stock_name", ""),
            "volume": qty,
            "can_use_volume": can_use_volume,
            "frozen_volume": frozen_volume,
            "today_buy_volume": today_buy_qty,
            "avg_price": cost_price,
            "market_value": market_val,
            "last_price": current_price if current_price > 0 else None,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pnl_pct,
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
    "ping": "ping",
    "query_deals": "query_deals",
    "query_history_orders": "query_history_orders",
    "query_history_deals": "query_history_deals",
    "query_fund_flow": "query_fund_flow",
    "query_ipo_list": "query_ipo_list",
    "subscribe_ipo": "subscribe_ipo",
    "currency_exchange": "currency_exchange",
    "change_position_cost": "change_position_cost",
    "get_klines": "get_klines",
    "subscribe_orderbook": "subscribe_orderbook",
    "get_orderbook": "get_orderbook",
    "get_market_snapshot": "get_market_snapshot",
    "exercise_option": "exercise_option",
    "query_option_positions": "query_option_positions",
    "get_option_expiration": "get_option_expiration",
    "get_option_chain": "get_option_chain",
    "get_stock_basicinfo": "get_stock_basicinfo",
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
            # Already initialised above; just ack
            reply_ok(request_id, {"status": "ok"})
            continue

        if method not in METHODS:
            reply_err(request_id, f"Unknown method: {method}")
            continue

        try:
            fn = getattr(bridge, METHODS[method])
            if method in ("query_positions", "query_account", "disconnect", "connect", "ping", "query_option_positions"):
                result = fn()
            else:
                result = fn(params)
            reply_ok(request_id, result)
        except Exception as exc:
            log.exception("Method %s failed", method)
            reply_err(request_id, str(exc))


if __name__ == "__main__":
    main()
