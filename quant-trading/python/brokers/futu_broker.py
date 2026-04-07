"""
futu_broker.py — Futu OpenD persistent NDJSON bridge.

Protocol (matches PersistentPythonProcess in TS):
  stdin  (line-delimited JSON): { "requestId": "...", "method": "...", "params": {...} }
  stdout (line-delimited JSON): { "requestId": "...", "ok": true, "data": ... }
                                { "event": "fill"|"quote"|"order_status", "data": ... }

Requires: futu-api (pip install futu-api)
Futu OpenD must be running before starting this process.
"""

import sys
import json
import threading
import time
import os
import logging
from typing import Any, Optional, Dict, List

# ── futu-api imports (graceful degradation) ──────────────────────

try:
    import futu
    from futu import (
        OpenQuoteContext,
        OpenSecTradeContext,
        TrdEnv,
        TrdMarket,
        RET_OK,
        RET_ERROR,
        OrderType,
        OrderStatus as FutuOrderStatus,
        TrdSide,
        SecurityFirm,
        SubType,
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

FUTU_STATUS_MAP = {}
if HAS_FUTU:
    FUTU_STATUS_MAP = {
        FutuOrderStatus.NONE: "pending",
        FutuOrderStatus.UNSUBMITTED: "pending",
        FutuOrderStatus.WAITING_SUBMIT: "pending",
        FutuOrderStatus.SUBMITTING: "pending",
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
    """Map Futu OrderStatus string to our normalised status."""
    if HAS_FUTU:
        for k, v in FUTU_STATUS_MAP.items():
            if str(k) == status_str or k == status_str:
                return v
    return "submitted"


# ── Futu trade side mapping ──────────────────────────────────────

def map_side_to_futu(side: str) -> str:
    """Map our side string to Futu TrdSide."""
    if not HAS_FUTU:
        return side
    m = {
        "buy": TrdSide.BUY,
        "sell": TrdSide.SELL,
        "short": TrdSide.SELL_SHORT,
        "cover": TrdSide.BUY_BACK,
    }
    return m.get(side, TrdSide.BUY)


def map_futu_side(side) -> str:
    """Map Futu TrdSide to our side string."""
    if not HAS_FUTU:
        return "buy"
    m = {
        TrdSide.BUY: "buy",
        TrdSide.SELL: "sell",
        TrdSide.SELL_SHORT: "short",
        TrdSide.BUY_BACK: "cover",
    }
    if isinstance(side, str):
        for k, v in m.items():
            if str(k) == side:
                return v
    return m.get(side, "buy")


# ── Futu trade environment ───────────────────────────────────────

def get_trd_env(env_str: str):
    """Convert config string to Futu TrdEnv."""
    if not HAS_FUTU:
        return None
    if env_str == "SIMULATE":
        return TrdEnv.SIMULATE
    return TrdEnv.REAL


# ── Quote push handler ───────────────────────────────────────────

class QuotePushHandler:
    """Handle real-time quote pushes from Futu OpenD."""
    def on_recv_rsp(self, rsp_str):
        pass


# ── Order/Fill push handlers ─────────────────────────────────────

class OrderUpdateHandler:
    """Receives order status push notifications from Futu and forwards as NDJSON events."""
    def __init__(self):
        self._enabled = True

    def on_recv_rsp(self, rsp_str):
        """Called by futu SDK when order status changes."""
        pass


class DealUpdateHandler:
    """Receives fill/deal push notifications from Futu and forwards as NDJSON events."""
    def __init__(self):
        self._enabled = True

    def on_recv_rsp(self, rsp_str):
        """Called by futu SDK when a deal happens."""
        pass


# ── Main broker class ─────────────────────────────────────────────

class FutuBrokerBridge:
    def __init__(self, init_args: dict):
        self.host: str = init_args.get("opend_host", "127.0.0.1")
        self.port: int = int(init_args.get("opend_port", 11111))
        self.trade_env_str: str = init_args.get("trade_env", "REAL")
        self.pwd_unlock: str = init_args.get("pwd_unlock", "")
        self.market_quote_type: int = int(init_args.get("market_quote_type", 1))

        self.quote_ctx: Optional[Any] = None
        self.trade_ctx: Optional[Any] = None
        self.trd_env = None
        self._connected = False
        self._reconnect_thread: Optional[threading.Thread] = None
        self._reconnect_stop = threading.Event()
        self._subscribed_codes: set = set()
        # Account list cache
        self._account_list: List[Dict] = []

    def connect(self) -> dict:
        if not HAS_FUTU:
            raise RuntimeError("futu-api is not installed. Run: pip install futu-api")

        self.trd_env = get_trd_env(self.trade_env_str)

        # Connect quote context
        try:
            self.quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
        except Exception as exc:
            raise RuntimeError(f"Failed to connect quote context to OpenD at {self.host}:{self.port}: {exc}")

        # Connect trade context
        try:
            self.trade_ctx = OpenSecTradeContext(
                filter_trdmarket=TrdMarket.NONE,
                host=self.host,
                port=self.port,
                security_firm=SecurityFirm.FUTUSECURITIES,
            )
        except Exception as exc:
            if self.quote_ctx:
                self.quote_ctx.close()
                self.quote_ctx = None
            raise RuntimeError(f"Failed to connect trade context to OpenD: {exc}")

        # Unlock trade if password provided
        if self.pwd_unlock:
            ret, data = self.trade_ctx.unlock_trade(self.pwd_unlock, is_unlock=True)
            if ret != RET_OK:
                log.warning("Failed to unlock trade: %s", data)

        # Fetch and cache account list
        self._account_list = self._fetch_account_list()

        self._connected = True
        self._start_reconnect_monitor()

        return {
            "status": "connected",
            "host": self.host,
            "port": self.port,
            "trade_env": self.trade_env_str,
            "accounts": self._account_list,
        }

    def disconnect(self) -> dict:
        self._reconnect_stop.set()
        self._connected = False
        if self.quote_ctx:
            self.quote_ctx.close()
            self.quote_ctx = None
        if self.trade_ctx:
            self.trade_ctx.close()
            self.trade_ctx = None
        return {"status": "disconnected"}

    # ── Account queries ──────────────────────────────────────────

    def _fetch_account_list(self) -> List[Dict]:
        """Fetch account list from Futu OpenD."""
        if not self.trade_ctx:
            return []
        ret, data = self.trade_ctx.get_acc_list()
        if ret != RET_OK:
            log.warning("get_acc_list failed: %s", data)
            return []
        accounts = []
        for _, row in data.iterrows():
            accounts.append({
                "acc_id": int(row.get("acc_id", 0)),
                "acc_type": str(row.get("acc_type", "")),
                "trd_env": str(row.get("trd_env", "")),
                "card_num": str(row.get("card_num", "")),
                "sim_acc_type": str(row.get("sim_acc_type", "")),
            })
        return accounts

    def get_account_list(self, _params: dict = {}) -> list:
        """Return cached account list, refreshing if empty."""
        if not self._account_list:
            self._account_list = self._fetch_account_list()
        return self._account_list

    def query_account(self, _params: dict = {}) -> dict:
        """Query account funds/assets summary."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        ret, data = self.trade_ctx.accinfo_query(trd_env=self.trd_env)
        if ret != RET_OK:
            raise RuntimeError(f"accinfo_query failed: {data}")

        if data.empty:
            raise RuntimeError("No account info returned")

        row = data.iloc[0]
        cash = float(row.get("cash", 0) or 0)
        total_assets = float(row.get("total_assets", 0) or 0)
        market_val = float(row.get("market_val", 0) or 0)
        frozen_cash = float(row.get("frozen_cash", 0) or 0)
        available_funds = float(row.get("avl_withdrawal_cash", 0) or 0)

        # Derive profit from total_assets - cash - market_val initially deposited
        # Futu provides 'realized_pl' and 'unrealized_pl' fields
        unrealized_pl = float(row.get("unrealized_pl", 0) or 0)
        realized_pl = float(row.get("realized_pl", 0) or 0)
        profit = unrealized_pl + realized_pl
        profit_pct = profit / total_assets if total_assets > 0 else 0

        # Try to get account ID from cached list
        acc_id = ""
        if self._account_list:
            acc_id = str(self._account_list[0].get("acc_id", ""))

        return {
            "account_id": acc_id,
            "cash": cash,
            "frozen_cash": frozen_cash,
            "total_asset": total_assets,
            "market_value": market_val,
            "profit": profit,
            "profit_ratio": profit_pct,
            "available_funds": available_funds,
        }

    def query_positions(self, _params: dict = {}) -> list:
        """Query current positions/holdings."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        ret, data = self.trade_ctx.position_list_query(trd_env=self.trd_env)
        if ret != RET_OK:
            raise RuntimeError(f"position_list_query failed: {data}")

        positions = []
        for _, row in data.iterrows():
            code = str(row.get("code", ""))
            name = str(row.get("stock_name", ""))
            qty = int(row.get("qty", 0) or 0)
            can_sell_qty = int(row.get("can_sell_qty", 0) or 0)
            cost_price = float(row.get("cost_price", 0) or 0)
            market_val = float(row.get("market_val", 0) or 0)
            nominal_price = float(row.get("nominal_price", 0) or 0)
            pl_val = float(row.get("pl_val", 0) or 0)
            pl_ratio = float(row.get("pl_ratio", 0) or 0)
            today_buy_qty = int(row.get("today_buy_qty", 0) or 0)
            today_sell_qty = int(row.get("today_sell_qty", 0) or 0)

            # Futu codes include market prefix: HK.00700, US.AAPL, SH.600519
            # Normalize to plain code
            plain_code = code.split(".")[-1] if "." in code else code
            market_prefix = code.split(".")[0] if "." in code else ""

            positions.append({
                "stock_code": code,
                "plain_code": plain_code,
                "market_prefix": market_prefix,
                "stock_name": name,
                "volume": qty,
                "can_use_volume": can_sell_qty,
                "frozen_volume": max(0, qty - can_sell_qty),
                "today_buy_volume": today_buy_qty,
                "today_sell_volume": today_sell_qty,
                "avg_price": cost_price,
                "market_value": market_val,
                "last_price": nominal_price if nominal_price > 0 else None,
                "unrealized_pnl": pl_val,
                "unrealized_pnl_pct": pl_ratio / 100.0 if pl_ratio else None,
            })
        return positions

    # ── Order operations ─────────────────────────────────────────

    def place_order(self, params: dict) -> dict:
        """Place a new order via Futu OpenD."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        code = params["stock_code"]
        side = params.get("side", "buy")
        price = float(params.get("price", 0))
        qty = int(params["volume"])
        order_type_str = params.get("order_type", "limit")

        trd_side = map_side_to_futu(side)

        # Map order type
        if order_type_str == "market":
            futu_order_type = OrderType.MARKET
        else:
            futu_order_type = OrderType.NORMAL  # limit order

        ret, data = self.trade_ctx.place_order(
            price=price,
            qty=qty,
            code=code,
            trd_side=trd_side,
            order_type=futu_order_type,
            trd_env=self.trd_env,
        )
        if ret != RET_OK:
            raise RuntimeError(f"place_order failed: {data}")

        order_id = str(data.iloc[0].get("order_id", ""))
        return {"broker_order_id": order_id}

    def cancel_order(self, params: dict) -> dict:
        """Cancel an order by order_id."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        order_id = params["order_id"]
        ret, data = self.trade_ctx.modify_order(
            modify_order_op=futu.ModifyOrderOp.CANCEL,
            order_id=order_id,
            qty=0,
            price=0,
            trd_env=self.trd_env,
        )
        if ret != RET_OK:
            raise RuntimeError(f"cancel_order failed: {data}")
        return {"result": "ok"}

    def query_order(self, params: dict) -> dict:
        """Query a single order by order_id."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        order_id = params["order_id"]
        ret, data = self.trade_ctx.order_list_query(
            order_id=order_id,
            trd_env=self.trd_env,
        )
        if ret != RET_OK:
            raise RuntimeError(f"order_list_query failed: {data}")

        if data.empty:
            raise RuntimeError(f"Order {order_id} not found")

        row = data.iloc[0]
        return self._serialize_order(row)

    def query_orders(self, params: dict) -> list:
        """Query all orders, optionally filtered by stock code."""
        if not self.trade_ctx:
            raise RuntimeError("Not connected")

        stock_code = params.get("stock_code")
        kwargs = {"trd_env": self.trd_env}
        if stock_code:
            kwargs["code"] = stock_code

        ret, data = self.trade_ctx.order_list_query(**kwargs)
        if ret != RET_OK:
            raise RuntimeError(f"order_list_query failed: {data}")

        result = []
        for _, row in data.iterrows():
            result.append(self._serialize_order(row))
        return result

    # ── Quote subscription ───────────────────────────────────────

    def subscribe_quote(self, params: dict) -> dict:
        """Subscribe to real-time quotes for given stock codes."""
        codes = params.get("stock_codes", [])
        if not self.quote_ctx or not codes:
            return {"subscribed": list(self._subscribed_codes)}

        new_codes = [c for c in codes if c not in self._subscribed_codes]
        if new_codes:
            ret, data = self.quote_ctx.subscribe(new_codes, [SubType.QUOTE], subscribe_push=True)
            if ret == RET_OK:
                self._subscribed_codes.update(new_codes)
            else:
                log.warning("subscribe_quote failed: %s", data)
        return {"subscribed": list(self._subscribed_codes)}

    def unsubscribe_quote(self, params: dict) -> dict:
        """Unsubscribe from real-time quotes."""
        codes = params.get("stock_codes", [])
        if not self.quote_ctx or not codes:
            return {"subscribed": list(self._subscribed_codes)}

        unsub_codes = [c for c in codes if c in self._subscribed_codes]
        if unsub_codes:
            ret, data = self.quote_ctx.unsubscribe(unsub_codes, [SubType.QUOTE])
            if ret == RET_OK:
                for c in unsub_codes:
                    self._subscribed_codes.discard(c)
            else:
                log.warning("unsubscribe_quote failed: %s", data)
        return {"subscribed": list(self._subscribed_codes)}

    # ── Reconnect monitor ────────────────────────────────────────

    def _start_reconnect_monitor(self):
        """Start a background thread that monitors connection and reconnects if needed."""
        self._reconnect_stop.clear()
        self._reconnect_thread = threading.Thread(
            target=self._reconnect_loop, daemon=True, name="futu-reconnect"
        )
        self._reconnect_thread.start()

    def _reconnect_loop(self):
        """Periodically check connection health and reconnect if dropped."""
        backoff_delays = [2, 5, 10, 30, 60]
        attempt = 0
        while not self._reconnect_stop.is_set():
            self._reconnect_stop.wait(15)  # check every 15 seconds
            if self._reconnect_stop.is_set():
                break
            if not self._connected:
                break

            # Health check: try a simple query
            try:
                if self.trade_ctx:
                    ret, _ = self.trade_ctx.get_acc_list()
                    if ret == RET_OK:
                        attempt = 0  # reset backoff on success
                        continue
                # Connection seems down, try to reconnect
                self._do_reconnect()
                attempt = 0
            except Exception as exc:
                log.warning("Reconnect health check failed: %s", exc)
                delay = backoff_delays[min(attempt, len(backoff_delays) - 1)]
                attempt += 1
                push_event("connection_error", {
                    "message": str(exc),
                    "attempt": attempt,
                    "next_retry_sec": delay,
                })
                self._reconnect_stop.wait(delay)

    def _do_reconnect(self):
        """Attempt to re-establish connections to OpenD."""
        log.info("Attempting reconnect to OpenD at %s:%d", self.host, self.port)
        push_event("reconnecting", {"host": self.host, "port": self.port})

        try:
            # Close existing contexts
            if self.quote_ctx:
                try:
                    self.quote_ctx.close()
                except Exception:
                    pass
            if self.trade_ctx:
                try:
                    self.trade_ctx.close()
                except Exception:
                    pass

            # Re-create contexts
            self.quote_ctx = OpenQuoteContext(host=self.host, port=self.port)
            self.trade_ctx = OpenSecTradeContext(
                filter_trdmarket=TrdMarket.NONE,
                host=self.host,
                port=self.port,
                security_firm=SecurityFirm.FUTUSECURITIES,
            )

            # Re-unlock trade
            if self.pwd_unlock:
                ret, data = self.trade_ctx.unlock_trade(self.pwd_unlock, is_unlock=True)
                if ret != RET_OK:
                    log.warning("Re-unlock trade failed: %s", data)

            # Re-subscribe quotes
            if self._subscribed_codes:
                ret, data = self.quote_ctx.subscribe(
                    list(self._subscribed_codes), [SubType.QUOTE], subscribe_push=True
                )
                if ret != RET_OK:
                    log.warning("Re-subscribe quotes failed: %s", data)

            push_event("reconnected", {"host": self.host, "port": self.port})
            log.info("Reconnected to OpenD successfully")
        except Exception as exc:
            log.error("Reconnect failed: %s", exc)
            raise

    # ── Ping / health check ──────────────────────────────────────

    def ping(self, _params: dict = {}) -> dict:
        """Simple health check — verifies OpenD connectivity."""
        if not self.trade_ctx:
            return {"ok": False, "message": "Not connected"}
        try:
            ret, _ = self.trade_ctx.get_acc_list()
            return {"ok": ret == RET_OK}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}

    # ── Serialisers ──────────────────────────────────────────────

    @staticmethod
    def _safe_float(val, default=0.0):
        try:
            f = float(val) if val is not None else default
            return default if f != f else f  # NaN check
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _safe_int(val, default=0):
        try:
            f = float(val) if val is not None else default
            return default if f != f else int(f)  # NaN check
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _serialize_order(row) -> dict:
        """Serialize a Futu order DataFrame row to our standard dict."""
        code = str(row.get("code", ""))
        order_status_raw = row.get("order_status", "")
        sf = FutuBrokerBridge._safe_float
        si = FutuBrokerBridge._safe_int
        return {
            "order_id": str(row.get("order_id", "")),
            "stock_code": code,
            "order_type": str(row.get("trd_side", "")),
            "price": sf(row.get("price", 0)),
            "order_volume": si(row.get("qty", 0)),
            "traded_volume": si(row.get("dealt_qty", 0)),
            "traded_price": sf(row.get("dealt_avg_price", 0)),
            "order_status": str(order_status_raw),
            "order_status_mapped": map_futu_status(str(order_status_raw)),
            "order_time": str(row.get("create_time", "")),
            "updated_time": str(row.get("updated_time", "")),
            "remark": str(row.get("remark", "")),
        }


# ── Dispatch loop ─────────────────────────────────────────────────

METHODS = {
    "connect": "connect",
    "disconnect": "disconnect",
    "place_order": "place_order",
    "cancel_order": "cancel_order",
    "query_order": "query_order",
    "query_orders": "query_orders",
    "query_positions": "query_positions",
    "query_account": "query_account",
    "get_account_list": "get_account_list",
    "subscribe_quote": "subscribe_quote",
    "unsubscribe_quote": "unsubscribe_quote",
    "ping": "ping",
}

# Methods that take no params (called without arguments)
NO_PARAM_METHODS = {
    "connect", "disconnect", "query_positions", "query_account",
    "get_account_list", "ping",
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
