#!/usr/bin/env python3
"""
Tick-level data provider bridge.

Fetches real-time tick data from QMT ContextInfo or local bridge.
Supports:
  - action=subscribe   — subscribe to tick data for given symbols
  - action=unsubscribe — unsubscribe from tick data
  - action=snapshot    — get latest tick snapshot for symbols
  - action=history     — get recent tick history for a symbol

Input: JSON via stdin or QUANT_CONFIG env var.
Output: JSON to stdout.
"""

import json
import os
import sys
import time
import urllib.request

# ── Proxy bypass (same as akshare_provider.py) ─────────────────────
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"
urllib.request.getproxies = lambda: {}  # type: ignore[assignment]

try:
    import requests
    import requests.utils
    requests.utils.getproxies = lambda: {}  # type: ignore[assignment]
    requests.utils.get_environ_proxies = lambda *a, **kw: {}  # type: ignore[assignment]
except ImportError:
    requests = None


def get_input():
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        return json.loads(config_str)
    return json.loads(sys.stdin.read())


def _infer_direction(tick_raw):
    """
    Infer trade direction from tick data.

    Uses the following heuristics (in order):
    1. If data source provides explicit direction field, use it.
    2. If bid1/ask1 available: price >= ask1 → buy, price <= bid1 → sell, else neutral.
    3. If only price and prev_price: price > prev → buy, price < prev → sell.
    """
    # Explicit direction from source
    direction = tick_raw.get("direction") or tick_raw.get("bs_flag") or tick_raw.get("side")
    if direction:
        d = str(direction).lower().strip()
        if d in ("buy", "b", "1", "bid"):
            return "buy"
        if d in ("sell", "s", "-1", "2", "ask", "offer"):
            return "sell"
        return "neutral"

    price = tick_raw.get("price") or tick_raw.get("last_price") or 0
    if not price or price <= 0:
        return "neutral"

    # Infer from bid/ask
    bid_prices = tick_raw.get("bid_price", [])
    ask_prices = tick_raw.get("ask_price", [])
    bid1 = bid_prices[0] if isinstance(bid_prices, list) and len(bid_prices) > 0 else None
    ask1 = ask_prices[0] if isinstance(ask_prices, list) and len(ask_prices) > 0 else None

    if bid1 and ask1 and bid1 > 0 and ask1 > 0:
        if price >= ask1:
            return "buy"
        if price <= bid1:
            return "sell"
        return "neutral"

    return "neutral"


def _normalize_tick(raw, symbol):
    """Normalize raw tick data from various sources into a standard format."""
    price = raw.get("last_price") or raw.get("price") or 0
    prev_close = raw.get("last_close") or raw.get("prev_close") or 0
    ts = raw.get("time") or raw.get("timestamp") or int(time.time() * 1000)

    bid_prices = raw.get("bid_price", [])
    ask_prices = raw.get("ask_price", [])
    bid_volumes = raw.get("bid_volume", raw.get("bid_vol", []))
    ask_volumes = raw.get("ask_volume", raw.get("ask_vol", []))

    bid_levels = []
    if isinstance(bid_prices, list):
        for i, p in enumerate(bid_prices):
            if p and p > 0:
                v = bid_volumes[i] if isinstance(bid_volumes, list) and i < len(bid_volumes) else 0
                bid_levels.append({"price": float(p), "volume": int(v or 0)})

    ask_levels = []
    if isinstance(ask_prices, list):
        for i, p in enumerate(ask_prices):
            if p and p > 0:
                v = ask_volumes[i] if isinstance(ask_volumes, list) and i < len(ask_volumes) else 0
                ask_levels.append({"price": float(p), "volume": int(v or 0)})

    direction = _infer_direction(raw)

    return {
        "symbol": {"code": symbol, "name": raw.get("name", symbol), "market": "cn_stock"},
        "timestamp": int(ts),
        "price": float(price),
        "volume": int(raw.get("volume", 0)),
        "amount": float(raw.get("amount", 0)),
        "open": float(raw.get("open", 0)),
        "high": float(raw.get("high", price)),
        "low": float(raw.get("low", price)),
        "prevClose": float(prev_close),
        "openInterest": raw.get("open_interest"),
        "settlementPrice": raw.get("settlement_price"),
        "bidLevels": bid_levels if bid_levels else None,
        "askLevels": ask_levels if ask_levels else None,
        "direction": direction,
        "tradeCount": raw.get("trade_count") or raw.get("num_trades"),
        "source": "qmt-bridge",
    }


def _bridge_url(params):
    """Get bridge URL from params or environment."""
    return (
        params.get("bridge_url")
        or os.environ.get("QMT_BRIDGE_URL")
        or "http://127.0.0.1:58088"
    )


def _fetch_from_bridge(bridge_url, endpoint, payload, timeout=10):
    """POST to QMT bridge and return parsed JSON."""
    if requests is None:
        # Fallback to urllib
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{bridge_url.rstrip('/')}{endpoint}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    else:
        resp = requests.post(
            f"{bridge_url.rstrip('/')}{endpoint}",
            json=payload,
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()


def handle_subscribe(params):
    """Subscribe to tick data for given symbols via bridge."""
    symbols = params.get("symbols", [])
    if not symbols:
        return {"ok": False, "error": "No symbols provided"}

    bridge_url = _bridge_url(params)
    try:
        result = _fetch_from_bridge(bridge_url, "/trading", {
            "method": "subscribe_quote",
            "params": {"stock_codes": symbols},
        })
        return {"ok": True, "data": {"subscribed": symbols, "response": result}}
    except Exception as e:
        return {"ok": False, "error": f"Subscribe failed: {str(e)}"}


def handle_unsubscribe(params):
    """Unsubscribe from tick data for given symbols."""
    symbols = params.get("symbols", [])
    if not symbols:
        return {"ok": False, "error": "No symbols provided"}

    bridge_url = _bridge_url(params)
    try:
        result = _fetch_from_bridge(bridge_url, "/trading", {
            "method": "unsubscribe_quote",
            "params": {"stock_codes": symbols},
        })
        return {"ok": True, "data": {"unsubscribed": symbols, "response": result}}
    except Exception as e:
        return {"ok": False, "error": f"Unsubscribe failed: {str(e)}"}


def handle_snapshot(params):
    """Get latest tick snapshot for given symbols from the bridge."""
    symbols = params.get("symbols", [])
    if not symbols:
        return {"ok": False, "error": "No symbols provided"}

    bridge_url = _bridge_url(params)
    try:
        result = _fetch_from_bridge(bridge_url, "/quote", {"codes": symbols})
        data = result.get("data", {})
        ticks = {}
        for sym_key, raw in data.items():
            if not isinstance(raw, dict):
                continue
            # Strip .SH/.SZ suffix for internal code
            internal_code = sym_key.split(".")[0] if "." in sym_key else sym_key
            ticks[internal_code] = _normalize_tick(raw, internal_code)
        return {"ok": True, "data": {"ticks": ticks, "count": len(ticks)}}
    except Exception as e:
        return {"ok": False, "error": f"Snapshot fetch failed: {str(e)}"}


def handle_history(params):
    """
    Fetch recent tick history from the bridge events endpoint.
    This returns whatever the bridge has buffered.
    """
    symbol = params.get("symbol", "")
    limit = params.get("limit", 100)
    bridge_url = _bridge_url(params)

    if not symbol:
        return {"ok": False, "error": "No symbol provided"}

    try:
        # Attempt to fetch from bridge events endpoint
        result = _fetch_from_bridge(bridge_url, "/events", {
            "symbol": symbol,
            "limit": limit,
        }, timeout=15)
        events = result.get("events", [])
        ticks = []
        internal_code = symbol.split(".")[0] if "." in symbol else symbol
        for evt in events:
            if evt.get("event") == "quote":
                raw = evt.get("data", {})
                tick_code = str(raw.get("stock_code", "")).split(".")[0]
                if tick_code == internal_code:
                    ticks.append(_normalize_tick(raw, internal_code))

        # Keep only the last N ticks
        ticks = ticks[-limit:]
        return {"ok": True, "data": {"symbol": internal_code, "ticks": ticks, "count": len(ticks)}}
    except Exception as e:
        return {"ok": False, "error": f"History fetch failed: {str(e)}"}


def main():
    params = get_input()
    action = params.get("action", "")

    try:
        if action == "subscribe":
            result = handle_subscribe(params)
        elif action == "unsubscribe":
            result = handle_unsubscribe(params)
        elif action == "snapshot":
            result = handle_snapshot(params)
        elif action == "history":
            result = handle_history(params)
        else:
            result = {"ok": False, "error": f"Unknown action: {action}"}
    except Exception as e:
        result = {"ok": False, "error": str(e)}

    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
