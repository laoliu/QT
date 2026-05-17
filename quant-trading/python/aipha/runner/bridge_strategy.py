"""QMT Strategy Mode Bridge adapter.

Runs inside QMT full client (策略交易模式), polls a remote receiver
for trading commands and pushes execution results / market data back.

Adapted from QT BRIDGE.py v23 for AlphQ architecture.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field, asdict
from enum import IntEnum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BRIDGE_VERSION = 23
POLL_INTERVAL = 1.0  # seconds between receiver polls
HEARTBEAT_INTERVAL = 30.0  # seconds between heartbeat pings


@dataclass
class BridgeConfig:
    """Bridge configuration loaded from environment or explicit params."""

    receiver_host: str = ""
    receiver_port: int = 58089
    account_id: str = ""
    account_type: str = "STOCK"
    poll_interval: float = POLL_INTERVAL
    heartbeat_interval: float = HEARTBEAT_INTERVAL
    log_file: str = ""

    @property
    def receiver_url(self) -> str:
        host = self.receiver_host or "127.0.0.1"
        return f"http://{host}:{self.receiver_port}"

    @classmethod
    def from_env(cls) -> BridgeConfig:
        """Load configuration from environment variables."""
        host = os.environ.get("OPENCLAW_HOST", os.environ.get("BRIDGE_RECEIVER_HOST", ""))
        port = int(os.environ.get("BRIDGE_RECEIVER_PORT", "58089"))
        return cls(
            receiver_host=host,
            receiver_port=port,
            account_id=os.environ.get("TRADING_ACCOUNT_ID", ""),
            account_type=os.environ.get("TRADING_ACCOUNT_TYPE", "STOCK"),
            log_file=os.environ.get("BRIDGE_LOG_FILE", ""),
        )


class OpType(IntEnum):
    """QMT passorder operation types."""

    BUY = 23
    SELL = 24


# ---------------------------------------------------------------------------
# Skeleton classes — implementations follow in subsequent steps
# ---------------------------------------------------------------------------


class ReceiverClient:
    """HTTP client for communicating with the remote receiver."""

    def __init__(self, base_url: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    def get(self, path: str) -> Any:
        """HTTP GET to receiver."""
        url = f"{self._base_url}{path}"
        try:
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            logger.debug("GET %s failed: %s", url, exc)
            return None

    def post(self, path: str, data: Any) -> Any:
        """HTTP POST to receiver."""
        url = f"{self._base_url}{path}"
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        try:
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as exc:
            logger.debug("POST %s failed: %s", url, exc)
            return None

    def poll_commands(self) -> list[dict[str, Any]]:
        """Poll receiver for pending trading commands."""
        result = self.get("/poll")
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "commands" in result:
            return result["commands"]
        return []

    def push_result(self, result: dict[str, Any]) -> None:
        """Push command execution result back to receiver."""
        self.post("/result", result)

    def heartbeat(self) -> bool:
        """Send heartbeat ping."""
        resp = self.get("/heartbeat")
        return resp is not None


# ---------------------------------------------------------------------------
# Trading Dispatcher — maps method names to QMT ContextInfo calls
# ---------------------------------------------------------------------------


class TradingDispatcher:
    """Dispatches trading commands to QMT ContextInfo API.

    Each method receives (context, params) and returns a result dict.
    ``context`` is the QMT ContextInfo object (or a mock for testing).
    """

    def connect(self, context: Any, params: dict) -> dict:
        """Connect to trading server."""
        return {"status": "connected"}

    def disconnect(self, context: Any, params: dict) -> dict:
        """Disconnect from trading server."""
        return {"status": "disconnected"}

    def place_order(self, context: Any, params: dict) -> dict:
        """Place order via QMT passorder."""
        side = params.get("side", "buy")
        code = params.get("code", "")
        price = float(params.get("price", 0))
        volume = int(params.get("volume", 0))
        price_type = int(params.get("price_type", 5))  # 5=最新价
        remark = params.get("remark", "bridge")
        op = 0 if side == "buy" else 1  # QMT: 0=buy, 1=sell
        account = params.get("account", "")
        try:
            passorder = getattr(context, "passorder", None)
            if passorder:
                passorder(op, 1101, account, code, price_type, price,
                         volume, "bridge", 2, remark, context)
            return {"status": "submitted", "code": code, "side": side,
                    "volume": volume, "price": price}
        except Exception as exc:
            return {"error": str(exc)}

    def cancel_order(self, context: Any, params: dict) -> dict:
        """Cancel order via QMT."""
        order_id = params.get("order_id", "")
        account = params.get("account", "")
        try:
            cancel = getattr(context, "cancel", None)
            if cancel:
                cancel(order_id, account, context)
            return {"status": "cancelled", "order_id": order_id}
        except Exception as exc:
            return {"error": str(exc)}

    def query_account(self, context: Any, params: dict) -> dict:
        """Query account info via get_trade_detail_data."""
        account = params.get("account", "")
        try:
            fn = getattr(context, "get_trade_detail_data", None)
            if not fn:
                return {"error": "get_trade_detail_data not available"}
            rows = fn(account, "STOCK", "ACCOUNT")
            if not rows:
                return {"cash": 0, "total_asset": 0, "frozen": 0}
            r = rows[0]
            return {
                "cash": getattr(r, "m_dAvailable", 0),
                "total_asset": getattr(r, "m_dBalance", 0),
                "frozen": getattr(r, "m_dFrozenCash", 0),
            }
        except Exception as exc:
            return {"error": str(exc)}

    def query_positions(self, context: Any, params: dict) -> dict:
        """Query positions via get_trade_detail_data."""
        account = params.get("account", "")
        try:
            fn = getattr(context, "get_trade_detail_data", None)
            if not fn:
                return {"positions": []}
            rows = fn(account, "STOCK", "POSITION")
            positions = []
            for r in (rows or []):
                vol = getattr(r, "m_nVolume", 0)
                if vol <= 0:
                    continue
                positions.append({
                    "code": getattr(r, "m_strInstrumentID", ""),
                    "volume": vol,
                    "available": getattr(r, "m_nCanUseVolume", 0),
                    "cost": getattr(r, "m_dOpenPrice", 0),
                    "market_value": getattr(r, "m_dMarketValue", 0),
                    "profit": getattr(r, "m_dFloatProfit", 0),
                })
            return {"positions": positions}
        except Exception as exc:
            return {"error": str(exc)}

    def query_orders(self, context: Any, params: dict) -> dict:
        """Query orders via get_trade_detail_data."""
        account = params.get("account", "")
        try:
            fn = getattr(context, "get_trade_detail_data", None)
            if not fn:
                return {"orders": []}
            rows = fn(account, "STOCK", "ORDER")
            orders = []
            for r in (rows or []):
                orders.append({
                    "order_id": getattr(r, "m_strOrderSysID", ""),
                    "code": getattr(r, "m_strInstrumentID", ""),
                    "side": "buy" if getattr(r, "m_nDirection", 0) == 0 else "sell",
                    "price": getattr(r, "m_dLimitPrice", 0),
                    "volume": getattr(r, "m_nVolumeTotalOriginal", 0),
                    "filled": getattr(r, "m_nVolumeTraded", 0),
                    "status": getattr(r, "m_nOrderStatus", 0),
                })
            return {"orders": orders}
        except Exception as exc:
            return {"error": str(exc)}

    def get_quote(self, context: Any, params: dict) -> dict:
        """Get real-time quote for a symbol."""
        code = params.get("code", "")
        try:
            tick_fn = getattr(context, "get_full_tick", None)
            if tick_fn:
                ticks = tick_fn([code])
                if ticks and code in ticks:
                    t = ticks[code]
                    return {
                        "code": code,
                        "last": getattr(t, "lastPrice", 0),
                        "open": getattr(t, "open", 0),
                        "high": getattr(t, "high", 0),
                        "low": getattr(t, "low", 0),
                        "volume": getattr(t, "volume", 0),
                    }
            return {"code": code, "error": "no tick data"}
        except Exception as exc:
            return {"error": str(exc)}

    def get_klines(self, context: Any, params: dict) -> dict:
        """Get historical klines for a symbol."""
        code = params.get("code", "")
        period = params.get("period", "1d")
        count = int(params.get("count", 100))
        try:
            md_fn = getattr(context, "get_market_data", None)
            if md_fn:
                df = md_fn(["open", "high", "low", "close", "volume"],
                          stock_code=[code], period=period, count=count)
                if df is not None and not df.empty:
                    bars = []
                    for idx, row in df.iterrows():
                        bars.append({
                            "time": str(idx),
                            "open": float(row.get("open", 0)),
                            "high": float(row.get("high", 0)),
                            "low": float(row.get("low", 0)),
                            "close": float(row.get("close", 0)),
                            "volume": float(row.get("volume", 0)),
                        })
                    return {"code": code, "bars": bars}
            return {"code": code, "bars": []}
        except Exception as exc:
            return {"error": str(exc)}

    def dispatch(self, method: str, context: Any, params: dict) -> dict:
        """Dispatch a method name to the appropriate handler."""
        handler = self._dispatch_map.get(method)
        if handler is None:
            return {"error": f"Unknown method: {method}"}
        return handler(context, params)

    @property
    def _dispatch_map(self) -> dict[str, Callable]:
        return {
            "connect": self.connect,
            "disconnect": self.disconnect,
            "place_order": self.place_order,
            "cancel_order": self.cancel_order,
            "query_account": self.query_account,
            "query_positions": self.query_positions,
            "query_orders": self.query_orders,
            "get_quote": self.get_quote,
            "get_klines": self.get_klines,
        }


# ---------------------------------------------------------------------------
# BridgeRunner — main loop that polls receiver and dispatches to QMT
# ---------------------------------------------------------------------------


class BridgeRunner:
    """Main bridge runner that coordinates polling, dispatching, results.

    Usage in QMT ``init(context)`` callback::

        runner = BridgeRunner(config=BridgeConfig.from_env())
        runner.run(context)  # blocks on main thread
    """

    def __init__(self, config: Optional[BridgeConfig] = None) -> None:
        self.config = config or BridgeConfig.from_env()
        self.client = ReceiverClient(self.config.receiver_url)
        self.dispatcher = TradingDispatcher()
        self._running = False
        self._lock = threading.Lock()
        self._last_heartbeat = 0.0

    def run(self, context: Any) -> None:
        """Blocking main loop — call from QMT init() callback."""
        self._running = True
        logger.info("Bridge v%d started, receiver=%s", BRIDGE_VERSION,
                     self.config.receiver_url)
        while self._running:
            try:
                self._poll_cycle(context)
            except Exception:
                logger.exception("Poll cycle error")
            time.sleep(self.config.poll_interval)

    def stop(self) -> None:
        """Signal the bridge to stop."""
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    def _poll_cycle(self, context: Any) -> None:
        """Single poll-dispatch-push cycle."""
        now = time.time()
        # Heartbeat
        if now - self._last_heartbeat > self.config.heartbeat_interval:
            self.client.heartbeat()
            self._last_heartbeat = now

        # Poll commands
        commands = self.client.poll_commands()
        for cmd in commands:
            method = cmd.get("method", "")
            params = cmd.get("params", {})
            request_id = cmd.get("requestId", "")
            # Inject account if not specified
            if "account" not in params:
                params["account"] = self.config.account_id
            result = self.dispatcher.dispatch(method, context, params)
            self.client.push_result({
                "requestId": request_id,
                "method": method,
                "ok": "error" not in result,
                "data": result,
            })
