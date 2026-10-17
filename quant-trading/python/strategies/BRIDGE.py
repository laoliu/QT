# coding: gbk
#
# BRIDGE.py - QMT Strategy Mode Bridge (pure ASCII, Python 3.6+)
# VERSION: 23
#
# Strategy mode only: runs inside QMT full client via init() callback.
# Uses ContextInfo for market data + passorder()/get_trade_detail_data() for trading.
# All trading runs on main thread (get_trade_detail_data requires thread-local ContextInfo).
#
# For miniQMT gateway, use qmt_data_bridge.py.
#

BRIDGE_VERSION = 23

import json
import os
import random
import sys
import threading
import time

# ===================== CONFIG =====================

# Mac receiver URL (RECEIVER.py running on Mac)
_BRIDGE_HOST = os.environ.get("QUANT_TRADING_HOST", "192.168.3.30")
# Keep the push receiver separate from the QMT data bridge (18888) unless an
# explicit environment override exists.
_BRIDGE_PORT = os.environ.get("BRIDGE_RECEIVER_PORT", "18889")
MAC_RECEIVER_URL = f"http://{_BRIDGE_HOST}:{_BRIDGE_PORT}"

# Log file for debug output
_LOG_FILE = None

def _log(msg):
    """Print with flush and also write to log file."""
    line = "[bridge] %s" % msg
    try:
        print(line, flush=True)
    except Exception:
        print(line)
        try:
            sys.stdout.flush()
        except Exception:
            pass
    if _LOG_FILE:
        try:
            with open(_LOG_FILE, "a") as f:
                f.write("%s %s\n" % (time.strftime("%H:%M:%S"), line))
                f.flush()
        except Exception:
            pass

def _init_log_file():
    global _LOG_FILE
    candidates = []
    try:
        candidates.append(os.path.dirname(os.path.abspath(__file__)))
    except Exception:
        pass
    candidates.append(r"D:\st_lc\bin.x64\strategies")
    candidates.append(r"D:\st_lc")
    candidates.append(os.path.join(os.environ.get("TEMP", "C:\\Temp")))
    candidates.append(os.getcwd())
    for log_dir in candidates:
        try:
            path = os.path.join(log_dir, "BRIDGE_DEBUG.log")
            with open(path, "w") as f:
                f.write("=== BRIDGE v%d debug log started %s ===\n"
                        % (BRIDGE_VERSION, time.strftime("%Y-%m-%d %H:%M:%S")))
            _LOG_FILE = path
            print("[bridge] debug log: %s" % _LOG_FILE, flush=True)
            return
        except Exception:
            continue
    print("[bridge] cannot create log file in any candidate dir", flush=True)
    _LOG_FILE = None

# Polling interval (seconds)
POLL_INTERVAL = 1.0

# ---- QMT Trading Account ----
TRADING_ACCOUNT_ID = os.environ.get("TRADING_ACCOUNT_ID", "")  # set via env or --account
TRADING_ACCOUNT_TYPE = os.environ.get("TRADING_ACCOUNT_TYPE", "STOCK")


def _normalize_to_qmt(code):
    """Normalize a stock code to QMT format (e.g. '600519' -> '600519.SH')."""
    code = str(code).strip()
    if "." in code:
        return code
    if code.startswith(("8", "43")):
        return code + ".BJ"
    try:
        n = int(code)
    except ValueError:
        return code + ".SH"
    if n >= 500_000:
        return code + ".SH"
    return code + ".SZ"

# QMT ContextInfo object (set in init when running as strategy)
_qmt_context = None


# ===================== KLINE FETCH (ContextInfo) =====================

def _fetch_klines_context(codes, period, count):
    """Fetch klines via QMT ContextInfo (works inside QMT strategy)."""
    ctx = _qmt_context
    if ctx is None:
        return {}

    print("[bridge] !!! _fetch_klines_context called (ctx=%s) !!!" % type(ctx).__name__)

    # Refresh local cache before reading so we get latest bars
    try:
        import xtquant.xtdata as _xtdata
        for _code in codes:
            try:
                _xtdata.download_history_data(_code, period, "", "")
            except Exception as _e:
                print("[bridge] download_history_data(%s) warning: %s" % (_code, _e))
    except Exception:
        pass

    result = {}

    # Method 1: get_market_data (PROVEN WORKING in probe)
    try:
        if hasattr(ctx, "get_market_data"):
            for code in codes:
                try:
                    raw = ctx.get_market_data(
                        ["open", "high", "low", "close", "volume"],
                        stock_code=[code],
                        period=period,
                        count=count,
                    )
                    print("[bridge] ctx.get_market_data(%s): type=%s val=%s"
                          % (code, type(raw).__name__, str(raw)[:300]))
                    if raw is not None:
                        stock_id = code.split(".")[0]
                        if hasattr(raw, "iterrows"):
                            bars = _df_to_bars(raw, code)
                            if bars:
                                result[stock_id] = bars
                        elif isinstance(raw, dict):
                            df = raw.get(code, raw.get(stock_id, None))
                            if df is not None and hasattr(df, "iterrows"):
                                bars = _df_to_bars(df, code)
                                if bars:
                                    result[stock_id] = bars
                except Exception as e:
                    print("[bridge] ctx.get_market_data(%s): %s" % (code, e))
            if result:
                return result
    except Exception as e:
        print("[bridge] ctx.get_market_data: %s" % e)

    # Method 2: get_market_data_ex (ContextInfo version)
    try:
        if hasattr(ctx, "get_market_data_ex"):
            field_list = ["open", "high", "low", "close", "volume"]
            raw = ctx.get_market_data_ex(
                codes, field_list, period, "", "", count, "front", True, False
            )
            print("[bridge] ctx.get_market_data_ex: type=%s val=%s"
                  % (type(raw).__name__, str(raw)[:300]))
            if raw and isinstance(raw, dict):
                for qmt_code, df in raw.items():
                    if df is None or (hasattr(df, "empty") and df.empty):
                        continue
                    stock_id = qmt_code.split(".")[0]
                    bars = _df_to_bars(df, qmt_code)
                    if bars:
                        result[stock_id] = bars
                if result:
                    return result
            elif raw is not None and hasattr(raw, "iterrows"):
                bars = _df_to_bars(raw, str(codes))
                if bars and codes:
                    result[codes[0].split(".")[0]] = bars
                    return result
    except Exception as e:
        print("[bridge] ctx.get_market_data_ex: %s" % e)

    # Method 3: get_history_data
    try:
        if hasattr(ctx, "get_history_data"):
            for code in codes:
                try:
                    data = ctx.get_history_data(count, period, code)
                    print("[bridge] ctx.get_history_data(%s): type=%s val=%s"
                          % (code, type(data).__name__, str(data)[:300]))
                    if data:
                        stock_id = code.split(".")[0]
                        bars = _parse_history_data(data, code)
                        if bars:
                            result[stock_id] = bars
                except Exception as e:
                    print("[bridge] ctx.get_history_data(%s): %s" % (code, e))
            if result:
                return result
    except Exception as e:
        print("[bridge] ctx.get_history_data: %s" % e)

    # Method 4: get_close_price field-by-field
    try:
        if hasattr(ctx, "get_close_price"):
            for code in codes:
                try:
                    closes = ctx.get_close_price(code, period, count)
                    print("[bridge] ctx.get_close_price(%s): type=%s val=%s"
                          % (code, type(closes).__name__, str(closes)[:200]))
                    if closes:
                        stock_id = code.split(".")[0]
                        bars = []
                        if isinstance(closes, (list, tuple)):
                            for c in closes:
                                bars.append({"date": "", "open": 0, "high": 0,
                                             "low": 0, "close": float(c), "volume": 0})
                        if bars:
                            result[stock_id] = bars
                except Exception as e:
                    print("[bridge] ctx.get_close_price(%s): %s" % (code, e))
    except Exception as e:
        print("[bridge] ctx.get_close_price: %s" % e)

    return result


def _df_to_bars(df, label=""):
    """Convert a pandas DataFrame to list of bar dicts."""
    bars = []
    try:
        for ts, row in df.iterrows():
            date_str = str(ts).replace("-", "")[:8]
            close_val = float(row.get("close", 0) or 0)
            if close_val <= 0:
                continue
            bars.append({
                "date":   date_str,
                "open":   float(row.get("open",   0) or 0),
                "high":   float(row.get("high",   0) or 0),
                "low":    float(row.get("low",    0) or 0),
                "close":  close_val,
                "volume": float(row.get("volume", 0) or 0),
            })
    except Exception as e:
        print("[bridge] df_to_bars(%s): %s" % (label, e))
    return bars


def _parse_history_data(data, code=""):
    """Parse QMT get_history_data result to bars."""
    bars = []
    try:
        if hasattr(data, "iterrows"):
            return _df_to_bars(data, code)
        if isinstance(data, dict):
            opens = data.get("open", [])
            highs = data.get("high", [])
            lows = data.get("low", [])
            closes = data.get("close", [])
            volumes = data.get("volume", [])
            n = len(closes) if closes else 0
            for i in range(n):
                c = float(closes[i]) if i < len(closes) else 0
                if c <= 0:
                    continue
                bars.append({
                    "date": "",
                    "open": float(opens[i]) if i < len(opens) else 0,
                    "high": float(highs[i]) if i < len(highs) else 0,
                    "low": float(lows[i]) if i < len(lows) else 0,
                    "close": c,
                    "volume": float(volumes[i]) if i < len(volumes) else 0,
                })
        if isinstance(data, (list, tuple)):
            for item in data:
                if isinstance(item, dict):
                    c = float(item.get("close", 0) or 0)
                    if c > 0:
                        bars.append({
                            "date": str(item.get("date", "")),
                            "open": float(item.get("open", 0) or 0),
                            "high": float(item.get("high", 0) or 0),
                            "low": float(item.get("low", 0) or 0),
                            "close": c,
                            "volume": float(item.get("volume", 0) or 0),
                        })
    except Exception as e:
        print("[bridge] parse_history(%s): %s" % (code, e))
    return bars


# ===================== HTTP HELPERS =====================

def _http_get(url, timeout=10, verbose=True):
    """Simple HTTP GET using urllib (Python 3.6 compatible)."""
    if verbose:
        _log("HTTP GET %s (timeout=%d) ..." % (url, timeout))
    try:
        if sys.version_info[0] >= 3:
            import urllib.request
            req = urllib.request.Request(url)
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.read().decode("utf-8")
        else:
            import urllib2
            resp = urllib2.urlopen(url, timeout=timeout)
            return resp.read().decode("utf-8")
    except Exception as e:
        if verbose:
            _log("HTTP GET %s failed: %s" % (url, e))
        return None


def _http_post(url, data_dict, timeout=10):
    """Simple HTTP POST JSON using urllib (Python 3.6 compatible)."""
    try:
        body = json.dumps(data_dict, ensure_ascii=False, default=str).encode("utf-8")
        if sys.version_info[0] >= 3:
            import urllib.request
            req = urllib.request.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.read().decode("utf-8")
        else:
            import urllib2
            req = urllib2.Request(
                url,
                data=body,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib2.urlopen(req, timeout=timeout)
            return resp.read().decode("utf-8")
    except Exception as e:
        _log("HTTP POST %s failed: %s" % (url, e))
        return None


_processed_ids = set()  # track already-processed request IDs


# ===================== TRADING BRIDGE (strategy mode) =====================
# Uses passorder() and get_trade_detail_data() injected by QMT strategy engine.
# No XtQuantTrader IPC needed (can't connect to self from inside QMT process).

_trading_connected = False
_trading_events_queue = []  # events to push back to Mac
_trading_subscribed_codes = set()
_trading_order_snapshot = {}
_trading_quote_last_push = {}
_TRADING_QUOTE_MIN_INTERVAL = 1.0


def _safe_attr(obj, names, default=None):
    """Get first available attribute from obj, trying each name in order."""
    for name in names:
        val = getattr(obj, name, None)
        if val is not None:
            return val
    return default


def _trading_connect(params):
    """Connect to QMT for trading via strategy mode (passorder/get_trade_detail_data)."""
    global _trading_connected

    if _trading_connected:
        return {"ok": True, "data": {"status": "already_connected",
                "account_id": TRADING_ACCOUNT_ID, "mode": "strategy"}}

    account_id = params.get("account_id", "") or TRADING_ACCOUNT_ID
    if not account_id or account_id == "xxxx":
        return {"ok": False, "error": "account_id not configured (set TRADING_ACCOUNT_ID env)"}

    if _qmt_context is None:
        return {"ok": False, "error": "ContextInfo not available (not running as QMT strategy?)"}

    po_fn = globals().get("passorder")
    gtdd_fn = globals().get("get_trade_detail_data")
    if not po_fn and not gtdd_fn:
        return {"ok": False, "error": "passorder/get_trade_detail_data not in strategy namespace"}

    _trading_connected = True
    _log("trading: connected via strategy mode (passorder=%s, gtdd=%s, account=%s)"
         % (po_fn is not None, gtdd_fn is not None, account_id))
    return {"ok": True, "data": {"status": "connected", "mode": "strategy",
            "account_id": account_id}}


def _trading_disconnect(_params):
    global _trading_connected, _trading_order_snapshot, _trading_quote_last_push
    _trading_connected = False
    _trading_subscribed_codes.clear()
    _trading_order_snapshot = {}
    _trading_quote_last_push = {}
    _log("trading: disconnected")
    return {"ok": True, "data": {"status": "disconnected"}}


def _trading_place_order(params):
    """Place order via passorder() in QMT strategy mode."""
    if not _trading_connected:
        return {"ok": False, "error": "Not connected"}

    # Raw polling orders cannot prove membership in a validated target plan.
    return {"ok": False, "error": "direct QMT orders are disabled; submit a validated execution plan"}

    po = globals().get("passorder")
    if not po:
        return {"ok": False, "error": "passorder not available in strategy namespace"}

    stock_code = params.get("stock_code", "")
    order_type = int(params.get("order_type", 23))
    price_type = int(params.get("price_type", 11))
    price = float(params.get("price", 0))
    volume = int(params.get("volume", 0))
    strategy = params.get("strategy_name", "") or "BRIDGE"
    remark = params.get("order_remark", "")

    # Map xtquant order_type to passorder opType: 23=buy->0, 24=sell->1
    if order_type == 23:
        op_type = 0
    elif order_type == 24:
        op_type = 1
    else:
        return {"ok": False, "error": "unsupported order_type: %d (expect 23=buy, 24=sell)" % order_type}

    if not stock_code or volume <= 0:
        return {"ok": False, "error": "invalid stock_code=%s or volume=%d" % (stock_code, volume)}

    try:
        # passorder(opType, orderType, accountid, orderCode, prType, price, volume,
        #           strategyName, quickTrade, userOrderId, C)
        # orderType=1101 for normal stock account, quickTrade=2 (no confirmation dialog)
        result = po(op_type, 1101, TRADING_ACCOUNT_ID, stock_code,
                    price_type, price, volume, strategy, 2, remark, _qmt_context)
        _log("trading: passorder(%s, op=%d, qty=%d, price=%.2f) => %s"
             % (stock_code, op_type, volume, price, result))
        return {"ok": True, "data": {
            "broker_order_id": str(result) if result else "submitted",
            "mode": "strategy",
        }}
    except Exception as e:
        return {"ok": False, "error": "passorder error: %s" % str(e)}


def _trading_cancel_order(params):
    """Cancel order via QMT strategy cancel function."""
    if not _trading_connected:
        return {"ok": False, "error": "Not connected"}

    cancel_fn = globals().get("cancel")
    if not cancel_fn:
        return {"ok": False, "error": "cancel() not available in strategy mode, use QMT client UI"}
    order_id = params.get("order_id", "")
    try:
        result = cancel_fn(order_id, TRADING_ACCOUNT_ID, TRADING_ACCOUNT_TYPE, _qmt_context)
        _log("trading: cancel(%s) => %s" % (order_id, result))
        return {"ok": True, "data": {"result": str(result) if result else "submitted"}}
    except Exception as e:
        return {"ok": False, "error": "cancel error: %s" % str(e)}


def _trading_query_account(_params):
    """Query account info via get_trade_detail_data in strategy mode."""
    if not _trading_connected:
        return {"ok": False, "error": "Not connected"}

    gtdd = globals().get("get_trade_detail_data")
    if not gtdd:
        return {"ok": False, "error": "get_trade_detail_data not available"}
    try:
        result = gtdd(TRADING_ACCOUNT_ID, TRADING_ACCOUNT_TYPE, "ACCOUNT")
        if not result:
            return {"ok": False, "error": "No account data returned"}
        acct = result[0] if isinstance(result, (list, tuple)) else result
        data = {
            "account_id": TRADING_ACCOUNT_ID,
            "cash": float(_safe_attr(acct, ["m_dAvailable", "m_dBalance", "available", "cash"], 0)),
            "frozen_cash": float(_safe_attr(acct, ["m_dFrozenCash", "frozen_cash"], 0)),
            "total_asset": float(_safe_attr(acct, ["m_dTotalAsset", "m_dAssureAsset", "total_asset"], 0)),
            "market_value": float(_safe_attr(acct, ["m_dMarketValue", "market_value"], 0)),
            "profit": float(_safe_attr(acct, ["m_dPositionProfit", "m_dProfit", "profit"], 0)),
            "profit_ratio": 0.0,
            "mode": "strategy",
        }
        return {"ok": True, "data": data}
    except Exception as e:
        _log("trading: query_account EXCEPTION: %s" % str(e))
        return {"ok": False, "error": "query_account error: %s" % str(e)}


def _trading_query_positions(_params):
    """Query positions via get_trade_detail_data in strategy mode."""
    if not _trading_connected:
        return {"ok": False, "error": "Not connected"}

    gtdd = globals().get("get_trade_detail_data")
    if not gtdd:
        return {"ok": False, "error": "get_trade_detail_data not available"}
    try:
        result = gtdd(TRADING_ACCOUNT_ID, TRADING_ACCOUNT_TYPE, "POSITION")
        if result is None:
            return {"ok": False, "error": "No position data returned"}
        positions = []
        for p in result:
            positions.append({
                "stock_code": str(_safe_attr(p, ["m_strInstrumentID", "stock_code"], "")),
                "stock_name": str(_safe_attr(p, ["m_strInstrumentName", "stock_name"], "")),
                "volume": int(_safe_attr(p, ["m_nVolume", "volume"], 0)),
                "can_use_volume": int(_safe_attr(p, ["m_nCanUseVolume", "can_use_volume"], 0)),
                "frozen_volume": int(_safe_attr(p, ["m_nFrozenVolume", "frozen_volume"], 0)),
                "avg_price": float(_safe_attr(p, ["m_dOpenPrice", "avg_price"], 0)),
                "market_value": float(_safe_attr(p, ["m_dMarketValue", "market_value"], 0)),
            })
        return {"ok": True, "data": positions}
    except Exception as e:
        _log("trading: query_positions EXCEPTION: %s" % str(e))
        return {"ok": False, "error": "query_positions error: %s" % str(e)}


def _trading_query_orders(params):
    """Query orders via get_trade_detail_data in strategy mode."""
    if not _trading_connected:
        return {"ok": False, "error": "Not connected"}

    gtdd = globals().get("get_trade_detail_data")
    if not gtdd:
        return {"ok": False, "error": "get_trade_detail_data not available"}
    stock_code = params.get("stock_code")
    try:
        result = gtdd(TRADING_ACCOUNT_ID, TRADING_ACCOUNT_TYPE, "ORDER")
        if result is None:
            return {"ok": True, "data": []}
        orders = []
        for o in result:
            order = {
                "order_id": str(_safe_attr(o, ["m_strOrderSysID", "order_id"], "")),
                "stock_code": str(_safe_attr(o, ["m_strInstrumentID", "stock_code"], "")),
                "order_type": int(_safe_attr(o, ["m_nOrderDirection", "order_type"], 0)),
                "price": float(_safe_attr(o, ["m_dOrderPrice", "price"], 0)),
                "order_volume": int(_safe_attr(o, ["m_nOrderVol", "order_volume"], 0)),
                "traded_volume": int(_safe_attr(o, ["m_nDealedVol", "traded_volume"], 0)),
                "traded_price": float(_safe_attr(o, ["m_dDealedPrice", "traded_price"], 0)),
                "order_status": int(_safe_attr(o, ["m_nOrderStatus", "order_status"], 0)),
                "strategy_name": str(_safe_attr(o, ["m_strStrategyName", "strategy_name"], "")),
            }
            if stock_code and order["stock_code"] != stock_code:
                continue
            orders.append(order)
        return {"ok": True, "data": orders}
    except Exception as e:
        return {"ok": False, "error": "query_orders error: %s" % str(e)}


def _trading_query_order(params):
    """Query single order by ID in strategy mode."""
    if not _trading_connected:
        return {"ok": False, "error": "Not connected"}
    order_id = str(params.get("order_id", ""))
    result = _trading_query_orders({})
    if not result.get("ok"):
        return result
    for o in result.get("data", []):
        if o["order_id"] == order_id:
            return {"ok": True, "data": o}
    return {"ok": False, "error": "Order %s not found" % order_id}


def _trading_subscribe_quote(params):
    """Subscribe QMT quote callbacks and enable periodic event forwarding."""
    codes = params.get("stock_codes", [])
    if isinstance(codes, str):
        codes = [codes]
    normalized = [_normalize_to_qmt(c) for c in codes if c]
    if not normalized:
        return {"ok": True, "data": {"subscribed": sorted(_trading_subscribed_codes)}}

    subscribe = getattr(_qmt_context, "subscribe_quote", None) if _qmt_context is not None else None
    if subscribe is not None:
        for code in normalized:
            try:
                subscribe(code, "tick", "front_ratio")
            except TypeError:
                subscribe(code, "tick")
            except Exception as exc:
                return {"ok": False, "error": "subscribe_quote(%s) error: %s" % (code, exc)}
    elif _qmt_context is None or not hasattr(_qmt_context, "get_full_tick"):
        return {"ok": False, "error": "QMT context does not expose subscribe_quote/get_full_tick"}

    _trading_subscribed_codes.update(normalized)
    return {"ok": True, "data": {"subscribed": sorted(_trading_subscribed_codes)}}


def _trading_unsubscribe_quote(params):
    codes = params.get("stock_codes", [])
    if isinstance(codes, str):
        codes = [codes]
    for code in codes:
        _trading_subscribed_codes.discard(_normalize_to_qmt(code))
    return {"ok": True, "data": {"subscribed": sorted(_trading_subscribed_codes)}}


def _trading_get_quote(params):
    """Fetch real-time quote snapshot via ContextInfo."""
    stock_code = params.get("stock_code", "")
    if not stock_code:
        return {"ok": False, "error": "stock_code required"}
    stock_code = _normalize_to_qmt(stock_code)
    ctx = _qmt_context
    if ctx is None:
        return {"ok": False, "error": "ContextInfo not available"}

    # Method 1: get_full_tick (real-time tick data)
    if hasattr(ctx, "get_full_tick"):
        try:
            tick = ctx.get_full_tick([stock_code])
            if tick and isinstance(tick, dict) and stock_code in tick:
                t = tick[stock_code]
                return {"ok": True, "data": {
                    "stock_code": stock_code,
                    "price": float(t.get("lastPrice", 0) or 0),
                    "open": float(t.get("open", 0) or 0),
                    "high": float(t.get("high", 0) or 0),
                    "low": float(t.get("low", 0) or 0),
                    "prev_close": float(t.get("lastClose", 0) or 0),
                    "volume": float(t.get("volume", 0) or 0),
                    "amount": float(t.get("amount", 0) or 0),
                    "bid1": float(t.get("bidPrice", [0])[0] if isinstance(t.get("bidPrice"), (list, tuple)) else t.get("bidPrice", 0) or 0),
                    "ask1": float(t.get("askPrice", [0])[0] if isinstance(t.get("askPrice"), (list, tuple)) else t.get("askPrice", 0) or 0),
                    "source": "get_full_tick",
                }}
        except Exception as e:
            _log("get_full_tick(%s) error: %s" % (stock_code, e))

    # Method 2: get_market_data with count=1 (last bar as quote)
    if hasattr(ctx, "get_market_data"):
        try:
            df = ctx.get_market_data(
                ["open", "high", "low", "close", "volume"],
                stock_code=[stock_code], period="1d", count=1)
            if df is not None and hasattr(df, "iterrows"):
                for _, row in df.iterrows():
                    return {"ok": True, "data": {
                        "stock_code": stock_code,
                        "price": float(row.get("close", 0) or 0),
                        "open": float(row.get("open", 0) or 0),
                        "high": float(row.get("high", 0) or 0),
                        "low": float(row.get("low", 0) or 0),
                        "prev_close": 0,
                        "volume": float(row.get("volume", 0) or 0),
                        "amount": 0,
                        "source": "get_market_data",
                    }}
        except Exception as e:
            _log("get_market_data quote(%s) error: %s" % (stock_code, e))

    # Method 3: get_instrument_detail (static info, limited price data)
    if hasattr(ctx, "get_instrument_detail"):
        try:
            detail = ctx.get_instrument_detail(stock_code)
            if detail and isinstance(detail, dict):
                return {"ok": True, "data": {
                    "stock_code": stock_code,
                    "price": float(detail.get("PreClose", 0) or 0),
                    "open": 0, "high": 0, "low": 0,
                    "prev_close": float(detail.get("PreClose", 0) or 0),
                    "volume": 0, "amount": 0,
                    "name": detail.get("InstrumentName", ""),
                    "source": "get_instrument_detail",
                }}
        except Exception as e:
            _log("get_instrument_detail(%s) error: %s" % (stock_code, e))

    return {"ok": False, "error": "No quote data available for %s" % stock_code}


def _trading_get_klines(params):
    """Fetch historical K-line data via ContextInfo."""
    codes = params.get("codes", [])
    if isinstance(codes, str):
        codes = [codes]
    if not codes:
        stock_code = params.get("stock_code", "")
        if stock_code:
            codes = [stock_code]
    if not codes:
        return {"ok": False, "error": "codes or stock_code required"}

    codes = [_normalize_to_qmt(c) for c in codes]
    period = params.get("period", "1d")
    count = int(params.get("count", 120))
    ctx = _qmt_context
    if ctx is None:
        return {"ok": False, "error": "ContextInfo not available"}

    result = {}
    for code in codes:
        bars = []
        # get_market_data (proven working)
        if hasattr(ctx, "get_market_data"):
            try:
                df = ctx.get_market_data(
                    ["open", "high", "low", "close", "volume"],
                    stock_code=[code], period=period, count=count)
                if df is not None and hasattr(df, "iterrows"):
                    bars = _df_to_bars(df, code)
            except Exception as e:
                _log("get_klines(%s) error: %s" % (code, e))

        if not bars and hasattr(ctx, "get_market_data_ex"):
            try:
                raw = ctx.get_market_data_ex(
                    [code], ["open", "high", "low", "close", "volume"],
                    period, "", "", count, "front", True, False)
                if raw and isinstance(raw, dict):
                    df = raw.get(code)
                    if df is not None and hasattr(df, "iterrows"):
                        bars = _df_to_bars(df, code)
            except Exception as e:
                _log("get_klines_ex(%s) error: %s" % (code, e))

        stock_id = code.split(".")[0]
        if bars:
            result[stock_id] = bars

    return {"ok": True, "data": result}


def _trading_save_strategy_file(params):
    """Save a strategy file to the MiniQMT strategies directory."""
    content = params.get("content", "")
    filename = params.get("filename", "")
    save_dir = params.get("save_dir", "")
    if not content or not filename:
        return {"ok": False, "error": "content and filename are required"}

    if not save_dir:
        mini_qmt = os.environ.get("MINI_QMT_PATH", r"d:\st_lc\userdata_mini")
        save_dir = os.path.join(mini_qmt, "strategies") if mini_qmt else ""
    if not save_dir:
        return {"ok": False, "error": "Cannot determine save directory (MINI_QMT_PATH not set)"}

    try:
        os.makedirs(save_dir, exist_ok=True)
        filepath = os.path.join(save_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        _log("Saved strategy file: %s" % filepath)
        return {"ok": True, "data": {"filepath": filepath, "filename": filename}}
    except Exception as e:
        return {"ok": False, "error": "Failed to save file: %s" % str(e)}


_TRADING_DISPATCH = {
    "connect": _trading_connect,
    "disconnect": _trading_disconnect,
    "place_order": _trading_place_order,
    "cancel_order": _trading_cancel_order,
    "query_order": _trading_query_order,
    "query_orders": _trading_query_orders,
    "query_positions": _trading_query_positions,
    "query_account": _trading_query_account,
    "subscribe_quote": _trading_subscribe_quote,
    "unsubscribe_quote": _trading_unsubscribe_quote,
    "get_quote": _trading_get_quote,
    "get_klines": _trading_get_klines,
    "save_strategy_file": _trading_save_strategy_file,
}


_trading_parse_err_count = 0

def _trading_poll_and_execute():
    """Poll Mac for pending trading commands, execute locally, push results.
    Returns number of commands processed."""
    global _trading_parse_err_count
    url = MAC_RECEIVER_URL + "/quant/api/trading-request"
    raw = _http_get(url, timeout=5, verbose=False)
    if raw is None:
        return 0
    if not raw.strip():
        # 204 No Content (no pending commands)
        _trading_parse_err_count = 0
        return 0

    try:
        req = json.loads(raw)
        _trading_parse_err_count = 0
    except Exception as e:
        _trading_parse_err_count += 1
        if _trading_parse_err_count <= 3:
            _log("trading: parse error: %s (raw=%s)" % (e, repr(raw[:200])))
        elif _trading_parse_err_count % 60 == 0:
            _log("trading: parse error x%d (suppressed): %s" % (_trading_parse_err_count, e))
        return 0

    req_id = req.get("requestId", "")
    method = req.get("method", "")
    if not req_id or not method:
        return 0

    params = req.get("params", {})
    _log("trading: executing %s (req=%s)" % (method, req_id))

    handler = _TRADING_DISPATCH.get(method)
    if handler:
        try:
            result = handler(params)
        except Exception as e:
            result = {"ok": False, "error": "handler exception: %s" % str(e)}
    else:
        result = {"ok": False, "error": "unknown method: %s" % method}

    # Push result back to Mac
    result["requestId"] = req_id
    resp_url = MAC_RECEIVER_URL + "/quant/api/trading-response"
    try:
        resp = _http_post(resp_url, result, timeout=10)
        if resp:
            ok = result.get("ok")
            if ok:
                _log("trading: pushed result for %s: ok=True" % req_id)
            else:
                _log("trading: pushed result for %s: ok=False error=%s"
                     % (req_id, result.get("error", "?")))
        else:
            _log("trading: FAILED to push result for %s" % req_id)
    except Exception as e:
        _log("trading: push error for %s: %s" % (req_id, e))

    return 1


def _queue_trading_event(event, data):
    _trading_events_queue.append({"event": event, "data": data})
    if len(_trading_events_queue) > 5000:
        del _trading_events_queue[:-5000]


def _trading_collect_events():
    """Collect quote and order changes from QMT on the strategy thread."""
    if not _trading_connected or _qmt_context is None:
        return

    now = time.time()
    if _trading_subscribed_codes and hasattr(_qmt_context, "get_full_tick"):
        try:
            ticks = _qmt_context.get_full_tick(sorted(_trading_subscribed_codes)) or {}
            for code, tick in ticks.items():
                if not isinstance(tick, dict) or now - _trading_quote_last_push.get(code, 0) < _TRADING_QUOTE_MIN_INTERVAL:
                    continue
                _trading_quote_last_push[code] = now
                _queue_trading_event("quote", {
                    "stock_code": code,
                    "last_price": float(tick.get("lastPrice", 0) or 0),
                    "open": float(tick.get("open", 0) or 0),
                    "high": float(tick.get("high", 0) or 0),
                    "low": float(tick.get("low", 0) or 0),
                    "last_close": float(tick.get("lastClose", 0) or 0),
                    "volume": float(tick.get("volume", 0) or 0),
                    "amount": float(tick.get("amount", 0) or 0),
                    "bid_price": tick.get("bidPrice", [0, 0, 0, 0, 0]),
                    "ask_price": tick.get("askPrice", [0, 0, 0, 0, 0]),
                    "time": int(now * 1000),
                })
        except Exception as exc:
            _log("trading: quote collection error: %s" % exc)

    gtdd = globals().get("get_trade_detail_data")
    if not gtdd:
        return
    try:
        orders = gtdd(TRADING_ACCOUNT_ID, TRADING_ACCOUNT_TYPE, "ORDER") or []
        current = {}
        for order in orders:
            oid = str(_safe_attr(order, ["m_strOrderSysID", "order_id"], ""))
            if not oid:
                continue
            item = {
                "order_id": oid,
                "stock_code": str(_safe_attr(order, ["m_strInstrumentID", "stock_code"], "")),
                "order_type": int(_safe_attr(order, ["m_nOrderDirection", "order_type"], 0) or 0),
                "price": float(_safe_attr(order, ["m_dOrderPrice", "price"], 0) or 0),
                "order_volume": int(_safe_attr(order, ["m_nOrderVol", "order_volume"], 0) or 0),
                "traded_volume": int(_safe_attr(order, ["m_nDealedVol", "traded_volume"], 0) or 0),
                "traded_price": float(_safe_attr(order, ["m_dDealedPrice", "traded_price"], 0) or 0),
                "order_status": int(_safe_attr(order, ["m_nOrderStatus", "order_status"], 0) or 0),
            }
            current[oid] = item
            previous = _trading_order_snapshot.get(oid)
            if previous is not None and item["order_status"] != previous["order_status"]:
                _queue_trading_event("order_status", item)
            if previous is not None and item["traded_volume"] > previous["traded_volume"]:
                _queue_trading_event("fill", {
                    "order_id": oid,
                    "stock_code": item["stock_code"],
                    "traded_price": item["traded_price"],
                    "traded_volume": item["traded_volume"] - previous["traded_volume"],
                    "time": int(now * 1000),
                })
        _trading_order_snapshot.clear()
        _trading_order_snapshot.update(current)
    except Exception as exc:
        _log("trading: event collection error: %s" % exc)


def _trading_push_events():
    """Push queued real-time events (quotes, fills, order_status) to Mac."""
    global _trading_events_queue
    if not _trading_events_queue:
        return
    events = _trading_events_queue[:]
    _trading_events_queue = []
    url = MAC_RECEIVER_URL + "/quant/api/trading-events"
    try:
        resp = _http_post(url, {"events": events}, timeout=5)
        if resp:
            _log("trading: pushed %d events" % len(events))
        else:
            _trading_events_queue = events + _trading_events_queue
    except Exception as e:
        _log("trading: event push error: %s" % e)
        _trading_events_queue = events + _trading_events_queue


def _trading_tick(loop_count):
    """Run one trading poll+execute cycle. Called from main thread's sync loop."""
    # Auto-retry connection every 60 loops if not connected
    if not _trading_connected and loop_count % 60 == 1:
        if TRADING_ACCOUNT_ID and TRADING_ACCOUNT_ID != "xxxx":
            _log("trading: auto-connecting (account=%s)" % TRADING_ACCOUNT_ID)
            try:
                result = _trading_connect({})
                _log("trading: connect result: %s" % json.dumps(result, ensure_ascii=False))
            except Exception as e:
                _log("trading: connect failed: %s" % e)

    # Poll and execute one trading command
    try:
        _trading_poll_and_execute()
    except Exception as e:
        _log("trading poll error: %s" % e)

    try:
        _trading_collect_events()
    except Exception as e:
        _log("trading event collection error: %s" % e)

    # Push real-time events
    _trading_push_events()



# ===================== SYNCHRONOUS POLLING LOOP =====================

def _sync_poll_loop(context):
    """Synchronous polling loop on main thread.
    Polls Mac /pending, fetches data with ContextInfo, pushes results.
    Also runs trading poll+execute (must be on main thread for get_trade_detail_data)."""
    loop_count = 0
    fail_count = 0
    _HEARTBEAT = 60
    while True:
        loop_count += 1
        try:
            if loop_count % _HEARTBEAT == 0:
                _log("heartbeat #%d (processed=%d, trading=%s)"
                     % (loop_count, len(_processed_ids), _trading_connected))

            # Trading: poll and execute on main thread (ContextInfo required)
            try:
                _trading_tick(loop_count)
            except Exception as e:
                _log("trading tick error: %s" % e)

            # 1. Poll Mac for pending requests
            url = MAC_RECEIVER_URL + "/pending"
            raw = _http_get(url, timeout=5, verbose=False)
            if raw is None:
                fail_count += 1
                if fail_count <= 3 or fail_count % 60 == 0:
                    _log("Mac not reachable (attempt %d)" % fail_count)
                time.sleep(POLL_INTERVAL)
                continue
            fail_count = 0

            try:
                resp = json.loads(raw)
            except Exception as e:
                _log("parse /pending error: %s" % e)
                time.sleep(POLL_INTERVAL)
                continue

            requests = resp.get("requests", [])

            # 2. Process each request using ContextInfo
            for req in requests:
                req_id = req.get("id", "")
                codes = req.get("codes", [])
                period = req.get("period", "1d")
                count = int(req.get("count", 120))
                if not req_id or not codes:
                    continue
                if req_id in _processed_ids:
                    continue

                codes = [_normalize_to_qmt(c) for c in codes]

                _log("processing %s: codes=%s period=%s count=%d"
                     % (req_id, codes, period, count))

                klines = {}
                source = "sync-init"

                # Refresh local cache so get_market_data returns latest bars
                try:
                    import xtquant.xtdata as _xtdata
                    for _code in codes:
                        try:
                            _xtdata.download_history_data(_code, period, "", "")
                        except Exception as _e:
                            _log("download_history_data(%s) warning: %s" % (_code, _e))
                except Exception:
                    pass

                for code in codes:
                    try:
                        df = context.get_market_data(
                            ["open", "high", "low", "close", "volume"],
                            stock_code=[code], period=period, count=count)
                        _log("get_market_data(%s) => type=%s" % (code, type(df).__name__))
                        if df is not None and hasattr(df, "iterrows"):
                            stock_id = code.split(".")[0]
                            bars = _df_to_bars(df, code)
                            _log("  => %d bars" % len(bars))
                            if bars:
                                klines[stock_id] = bars
                        elif df is not None:
                            _log("  => unexpected type: %s" % str(df)[:200])
                    except Exception as e:
                        _log("get_market_data(%s) error: %s" % (code, e))

                # 3. Push result back to Mac
                result_url = MAC_RECEIVER_URL + "/result/" + req_id
                push_data = {
                    "data": klines,
                    "count": len(klines),
                    "source": source,
                }
                try:
                    push_resp = _http_post(result_url, push_data, timeout=10)
                    if push_resp:
                        _log("pushed result %s: %d stocks" % (req_id, len(klines)))
                    else:
                        _log("FAILED push result %s" % req_id)
                except Exception as e:
                    _log("push error %s: %s" % (req_id, e))

                _processed_ids.add(req_id)

        except Exception as e:
            _log("!!! loop error: %s" % e)
            try:
                import traceback
                import io
                buf = io.StringIO()
                traceback.print_exc(file=buf)
                _log("traceback: %s" % buf.getvalue())
            except Exception:
                pass

        time.sleep(POLL_INTERVAL)


# ===================== QMT STRATEGY CALLBACKS =====================

_bridge_started = False

def init(context):
    """QMT strategy init -- called by QMT exec(). Survives re-exec via env guard."""
    global _bridge_started, _qmt_context

    _init_log_file()

    # Always update context — QMT may pass a fresh ContextInfo on re-entry
    _qmt_context = context

    # Robust re-entry guard
    if _bridge_started:
        print("[bridge] already running (var), skip (context refreshed)")
        return
    existing = [t for t in threading.enumerate()
                if getattr(t, 'name', '') == 'bridge-sync-poll' and t.is_alive()]
    if existing:
        print("[bridge] already running (thread), skip (context refreshed)")
        return

    print("[bridge] ========================================")
    print("[bridge] Data Bridge v%d (QMT strategy mode)" % BRIDGE_VERSION)
    print("[bridge] ContextInfo: %s" % type(context).__name__)
    print("[bridge] Trading fns: passorder=%s gtdd=%s cancel=%s"
          % (globals().get("passorder") is not None,
             globals().get("get_trade_detail_data") is not None,
             globals().get("cancel") is not None))
    print("[bridge] ========================================")

    _bridge_started = True

    # Run synchronous polling loop on main thread (blocking)
    # Trading also runs here (get_trade_detail_data needs main thread ContextInfo)
    print("[bridge] Starting synchronous polling loop (trading + data, single thread)")
    print("[bridge] Polling: %s/pending" % MAC_RECEIVER_URL)
    print("[bridge] ========================================")
    threading.current_thread().name = 'bridge-sync-poll'
    _sync_poll_loop(context)
