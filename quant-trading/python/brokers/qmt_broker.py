"""
qmt_broker.py — miniQMT / xtquant persistent NDJSON bridge.

Protocol (matches PersistentPythonProcess in TS):
  stdin  (line-delimited JSON): { "requestId": "...", "method": "...", "params": {...} }
  stdout (line-delimited JSON): { "requestId": "...", "ok": true, "data": ... }
                                { "event": "fill"|"quote"|"order_status", "data": ... }

Requires: xtquant (bundled with miniQMT, also available via `pip install xtquant`)
miniQMT must be running on the same machine before starting this process.
"""

import sys
import json
import threading
import time
import os
import logging
from typing import Any, Optional

# ── xtquant imports (graceful degradation) ────────────────────────

try:
    from xtquant import xtdata
    from xtquant.xttrader import XtQuantTrader, XtQuantTraderCallback
    from xtquant.xttype import StockAccount
    HAS_XTQUANT = True
except ImportError:
    HAS_XTQUANT = False

logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger("qmt_broker")

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

# ── QMT callback ──────────────────────────────────────────────────

class BridgeCallback(XtQuantTraderCallback if HAS_XTQUANT else object):
    """Receives async push notifications from QMT and forwards as NDJSON events."""

    def on_disconnected(self):
        push_event("disconnected", {})

    def on_stock_order(self, order):
        """Order status change."""
        push_event("order_status", {
            "order_id": str(order.order_id),
            "stock_code": order.stock_code,
            "order_type": order.order_type,
            "price": order.price,
            "order_volume": order.order_volume,
            "traded_volume": order.traded_volume,
            "traded_price": order.traded_price,
            "order_status": order.order_status,
            "strategy_name": order.strategy_name or "",
            "order_remark": getattr(order, "order_remark", "") or "",
            "strategy_error_msg": order.strategy_error_msg or "",
            "order_time": order.order_time or "",
        })

    def on_stock_trade(self, trade):
        """Fill / execution report."""
        # Estimate commission: A-share 万0.5双向 + ǧ1印花税（卖）
        value = trade.traded_price * trade.traded_volume
        commission = round(value * 0.0005, 4)
        push_event("fill", {
            "order_id": str(trade.order_id),
            "stock_code": trade.stock_code,
            "traded_price": trade.traded_price,
            "traded_volume": trade.traded_volume,
            "traded_amount": value,
            "commission": commission,
            "time": trade.traded_time or "",
        })

    def on_order_error(self, order_error):
        push_event("order_status", {
            "order_id": str(order_error.order_id),
            "stock_code": order_error.stock_code,
            "order_status": 58,  # 废单
            "strategy_error_msg": order_error.error_msg or "unknown error",
        })


# ── Quote subscription handler ────────────────────────────────────

class QuoteHandler:
    """Handles real-time quote pushes from xtdata.subscribe_quote."""
    def on_data(self, datas: dict) -> None:
        for code, bars in datas.items():
            if not bars:
                continue
            bar = bars[-1] if isinstance(bars, list) else bars
            push_event("quote", {
                "stock_code": code,
                "last_price": bar.get("lastPrice", 0),
                "open": bar.get("open", 0),
                "high": bar.get("high", 0),
                "low": bar.get("low", 0),
                "last_close": bar.get("lastClose", 0),
                "volume": bar.get("volume", 0),
                "amount": bar.get("amount", 0),
                "bid_price": bar.get("bidPrice", [0, 0, 0, 0, 0]),
                "ask_price": bar.get("askPrice", [0, 0, 0, 0, 0]),
                "bid_vol": bar.get("bidVol", [0, 0, 0, 0, 0]),
                "ask_vol": bar.get("askVol", [0, 0, 0, 0, 0]),
                "time": int(time.time() * 1000),
            })


# ── Main broker class ─────────────────────────────────────────────

class QMTBrokerBridge:
    def __init__(self, init_args: dict):
        self.mini_qmt_path: str = init_args.get("mini_qmt_path", "")
        self.account_id: str = init_args.get("account_id", "")
        self.account_type: str = init_args.get("account_type", "STOCK")
        self.require_execution_plan: bool = bool(init_args.get("require_execution_plan", False))
        self.trader: Optional[Any] = None
        self.account: Optional[Any] = None
        self._quote_handler = QuoteHandler()
        self._subscribed_codes: set = set()

    def connect(self) -> dict:
        if not HAS_XTQUANT:
            raise RuntimeError("xtquant is not installed. Run: pip install xtquant")
        if not self.mini_qmt_path:
            raise RuntimeError("mini_qmt_path is not configured")

        # ── 1) Initialise xtdata (market data channel) ───────────
        try:
            if hasattr(xtdata, "connect"):
                xtdata.connect(self.mini_qmt_path)
            elif hasattr(xtdata, "rpc_init"):
                xtdata.rpc_init(self.mini_qmt_path)
            if hasattr(xtdata, "run"):
                xtdata.run()
            log.info("xtdata market-data channel initialised")
        except Exception as e:
            log.warning("xtdata init failed (quotes may be unavailable): %s", e)

        # ── 2) Connect XtQuantTrader (trading channel) ───────────
        # xtquant versions expose the second constructor argument as either
        # ``session`` or ``session_id``.  Pass it positionally so the bridge
        # works with both the bundled miniQMT runtime and pip-installed builds.
        self.trader = XtQuantTrader(self.mini_qmt_path, int(time.time()) % 10000)
        callback = BridgeCallback()
        self.trader.register_callback(callback)
        self.trader.start()

        # Wait for connection (up to 10 s)
        for _ in range(20):
            if self.trader.connect() == 0:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Failed to connect to miniQMT within 10 seconds")

        self.account = StockAccount(self.account_id, self.account_type)
        result = self.trader.subscribe(self.account)
        if result != 0:
            raise RuntimeError(f"Failed to subscribe account {self.account_id} (code={result})")
        return {"status": "connected", "account_id": self.account_id}

    def disconnect(self) -> dict:
        if self.trader:
            self.trader.stop()
            self.trader = None
        return {"status": "disconnected"}

    def place_order(self, params: dict) -> dict:
        if not self.trader or not self.account:
            raise RuntimeError("Not connected")
        # This NDJSON bridge accepts only a single raw order and cannot prove
        # that it belongs to a validated target plan. Provenance strings are
        # caller-controlled, so this adapter never places orders directly;
        # QMTGateway.submit_target_plan is the only order path.
        raise RuntimeError(
            "QMT direct order bridge is disabled; submit a validated execution plan"
        )
        stock_code = params["stock_code"]
        order_type = int(params["order_type"])    # 23=buy, 24=sell
        price_type = int(params.get("price_type", 11))  # 11=限价, 5=市价
        price = float(params.get("price", 0))
        volume = int(params["volume"])
        strategy = params.get("strategy_name", "")
        remark = params.get("order_remark", "")
        provenance = [
            f"run_id={params['run_id']}" if params.get("run_id") else "",
            f"model_id={params['model_id']}" if params.get("model_id") else "",
            f"signal_id={params['signal_id']}" if params.get("signal_id") else "",
            f"strategy_version={params['strategy_version']}" if params.get("strategy_version") else "",
        ]
        provenance = [item for item in provenance if item]
        if provenance:
            remark = f"{remark}|{'|'.join(provenance)}"

        order_id = self.trader.order_stock(
            self.account,
            stock_code,
            order_type,
            volume,
            price_type,
            price,
            strategy,
            remark,
        )
        if order_id < 0:
            raise RuntimeError(f"place_order failed, QMT returned {order_id}")
        return {"broker_order_id": str(order_id)}

    def cancel_order(self, params: dict) -> dict:
        if not self.trader or not self.account:
            raise RuntimeError("Not connected")
        order_id = int(params["order_id"])
        result = self.trader.cancel_order_stock(self.account, order_id)
        return {"result": result}

    def query_order(self, params: dict) -> dict:
        if not self.trader or not self.account:
            raise RuntimeError("Not connected")
        order_id = int(params["order_id"])
        orders = self.trader.query_stock_orders(self.account, cancel_able_only=False)
        for o in (orders or []):
            if o.order_id == order_id:
                return self._serialize_order(o)
        raise RuntimeError(f"Order {order_id} not found")

    def query_orders(self, params: dict) -> list:
        if not self.trader or not self.account:
            raise RuntimeError("Not connected")
        stock_code = params.get("stock_code")
        orders = self.trader.query_stock_orders(self.account, cancel_able_only=False) or []
        result = [self._serialize_order(o) for o in orders]
        if stock_code:
            result = [o for o in result if o["stock_code"] == stock_code]
        return result

    def query_positions(self, _params: dict = {}) -> list:
        if not self.trader or not self.account:
            raise RuntimeError("Not connected")
        positions = self.trader.query_stock_positions(self.account) or []
        return [self._serialize_position(p) for p in positions]

    @staticmethod
    def _lookup_stock_name(stock_code: str) -> str:
        """Look up stock name via xtdata.get_instrument_detail."""
        if not HAS_XTQUANT:
            return ""
        try:
            detail = xtdata.get_instrument_detail(stock_code)
            if detail and isinstance(detail, dict):
                return detail.get("InstrumentName", "") or ""
        except Exception:
            pass
        return ""

    @staticmethod
    def _lookup_last_price(stock_code: str) -> float:
        """Look up last price via xtdata.get_full_tick for when market_value is zero."""
        if not HAS_XTQUANT:
            return 0.0
        try:
            ticks = xtdata.get_full_tick([stock_code])
            if ticks and stock_code in ticks:
                tick = ticks[stock_code]
                price = tick.get("lastPrice", 0) or 0
                if price > 0:
                    return float(price)
        except Exception:
            pass
        return 0.0

    def query_account(self, _params: dict = {}) -> dict:
        if not self.trader or not self.account:
            raise RuntimeError("Not connected")
        assets = self.trader.query_stock_asset(self.account)
        if not assets:
            return {
                "account_id": self.account_id,
                "cash": 0,
                "frozen_cash": 0,
                "total_asset": 0,
                "market_value": 0,
                "profit": 0,
                "profit_ratio": 0,
                "_unavailable": True,
            }
        return {
            "account_id": self.account_id,
            "cash": assets.cash,
            "frozen_cash": assets.frozen_cash,
            "total_asset": assets.total_asset,
            "market_value": assets.market_value,
            "profit": assets.profit,
            "profit_ratio": assets.profit_ratio,
        }

    def subscribe_quote(self, params: dict) -> dict:
        codes = params.get("stock_codes", [])
        new_codes = [c for c in codes if c not in self._subscribed_codes]
        if new_codes and HAS_XTQUANT:
            xtdata.subscribe_whole_quote(new_codes, callback=self._quote_handler.on_data)
            self._subscribed_codes.update(new_codes)
        return {"subscribed": list(self._subscribed_codes)}

    def unsubscribe_quote(self, params: dict) -> dict:
        codes = params.get("stock_codes", [])
        for c in codes:
            self._subscribed_codes.discard(c)
        # xtquant doesn't expose per-symbol unsubscribe cleanly; re-subscribe remaining
        if self._subscribed_codes and HAS_XTQUANT:
            xtdata.subscribe_whole_quote(list(self._subscribed_codes), callback=self._quote_handler.on_data)
        return {"subscribed": list(self._subscribed_codes)}

    # ── Option-specific methods ────────────────────────────────

    def exercise_option(self, params: dict) -> dict:
        """Exercise an option position (American-style only).

        QMT uses xttrader.order_option for option orders and
        xttrader.exercise_option for exercise.
        Currently only available for OPTIONS account type.
        """
        if not self.trader or not self.account:
            raise RuntimeError("Not connected")
        if self.account_type != "OPTIONS":
            raise RuntimeError("Option exercise requires OPTIONS account type")

        stock_code = params["stock_code"]
        volume = int(params["volume"])

        # xtquant exercise API — order_type 50 for exercise
        try:
            result = self.trader.order_stock(
                self.account,
                stock_code,
                50,       # 50 = exercise (行权)
                volume,
                11,       # price_type = 限价 (exercise uses settlement price)
                0,        # price — not used for exercise
                "exercise",
                "",
            )
            if result < 0:
                return {"success": False, "message": f"行权失败，QMT返回码: {result}"}
            return {"success": True, "message": f"行权申报成功，合约: {stock_code}，张数: {volume}"}
        except Exception as exc:
            return {"success": False, "message": f"行权异常: {str(exc)}"}

    # ── Scheduled strategy file saving ───────────────────────────

    def save_scheduled_strategy(self, params: dict) -> dict:
        """Save a pre-generated strategy file to the MiniQMT strategies directory."""
        content = params.get("content", "")
        filename = params.get("filename", "")
        if not content or not filename:
            raise RuntimeError("content and filename are required")

        save_dir = params.get("save_dir", "")
        if not save_dir:
            save_dir = os.path.join(self.mini_qmt_path, "strategies") if self.mini_qmt_path else ""
        if not save_dir:
            raise RuntimeError("Cannot determine save directory (mini_qmt_path not set)")

        os.makedirs(save_dir, exist_ok=True)
        filepath = os.path.join(save_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

        log.info("Saved scheduled strategy to %s", filepath)
        return {"filepath": filepath, "filename": filename}

    # ── Serialisers ────────────────────────────────────────────

    @staticmethod
    def _serialize_order(o) -> dict:
        return {
            "order_id": str(o.order_id),
            "stock_code": o.stock_code,
            "order_type": o.order_type,
            "price": o.price,
            "order_volume": o.order_volume,
            "traded_volume": o.traded_volume,
            "traded_price": o.traded_price,
            "order_status": o.order_status,
            "strategy_name": o.strategy_name or "",
            "order_remark": getattr(o, "order_remark", "") or "",
            "strategy_error_msg": getattr(o, "strategy_error_msg", "") or "",
            "order_time": getattr(o, "order_time", "") or "",
        }

    @staticmethod
    def _serialize_position(p) -> dict:
        stock_code = p.stock_code
        # XtPosition doesn't have stock_name; look it up via xtdata
        stock_name = getattr(p, "stock_name", "") or ""
        if not stock_name and HAS_XTQUANT:
            stock_name = QMTBrokerBridge._lookup_stock_name(stock_code)

        volume = p.volume
        market_value = p.market_value
        avg_price = p.avg_price

        # When market_value is 0 (e.g. outside trading hours), try fetching
        # latest price from xtdata and reconstruct market_value
        last_price = 0.0
        if volume > 0 and market_value > 0:
            last_price = market_value / volume
        elif volume > 0 and HAS_XTQUANT:
            last_price = QMTBrokerBridge._lookup_last_price(stock_code)
            if last_price > 0:
                market_value = last_price * volume

        # Calculate unrealized PnL directly so TS doesn't have to derive it
        unrealized_pnl = None
        unrealized_pnl_pct = None
        if last_price > 0 and avg_price > 0:
            unrealized_pnl = (last_price - avg_price) * volume
            unrealized_pnl_pct = (last_price - avg_price) / avg_price

        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "volume": volume,
            "can_use_volume": p.can_use_volume,
            "frozen_volume": volume - p.can_use_volume,
            "today_buy_volume": getattr(p, "today_buy_volume", 0),
            "avg_price": avg_price,
            "market_value": market_value,
            "last_price": last_price if last_price > 0 else None,
            "unrealized_pnl": unrealized_pnl,
            "unrealized_pnl_pct": unrealized_pnl_pct,
            "yesterday_volume": getattr(p, "yesterday_volume", 0),
            "on_road_volume": getattr(p, "on_road_volume", 0),
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
    "subscribe_quote": "subscribe_quote",
    "unsubscribe_quote": "unsubscribe_quote",
    "exercise_option": "exercise_option",
    "save_scheduled_strategy": "save_scheduled_strategy",
}

def main():
    # Read init args from environment
    try:
        init_args = json.loads(os.environ.get("QUANT_INIT_ARGS", "{}"))
    except Exception:
        init_args = {}

    bridge = QMTBrokerBridge(init_args)

    # Signal ready to the TS side
    push_event("ready", {"module": "qmt_broker", "xtquant": HAS_XTQUANT})

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
