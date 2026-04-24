# coding: utf-8
#
# BRIDGE_MINI.py - miniQMT Gateway (standalone, Python 3.6+)
# VERSION: 19
#
# Standalone script that connects to miniQMT for market data + trading,
# and polls Mac RECEIVER.py for commands.
#
# Unlike BRIDGE.py (which runs inside QMT full client as a strategy
# via init/handlebar), this script:
#   - Runs standalone on Windows (just: python BRIDGE_MINI.py)
#   - Connects to miniQMT via xtdata for market data (no ContextInfo)
#   - Connects to miniQMT via XtQuantTrader for trading
#   - Does NOT require QMT UI. Just needs XtMiniQmt.exe running.
#
# Usage:
#   python BRIDGE_MINI.py
#   python BRIDGE_MINI.py --mac http://192.168.3.36:58088
#   python BRIDGE_MINI.py --account YOUR_ACCOUNT_ID
#   python BRIDGE_MINI.py --mini-path D:\st_lc\userdata_mini
#

BRIDGE_VERSION = 19

import glob
import json
import os
import sys
import time

# ===================== CONFIG =====================

_BRIDGE_HOST = os.environ.get("OPENCLAW_HOST", "192.168.3.36")
_BRIDGE_PORT = os.environ.get("BRIDGE_RECEIVER_PORT", "58088")
MAC_RECEIVER_URL = "http://%s:%s" % (_BRIDGE_HOST, _BRIDGE_PORT)

POLL_INTERVAL = 1.0

QMT_PATH = os.environ.get("QMT_PATH", "")
QMT_USERDATA_PATH = os.environ.get("QMT_USERDATA_PATH", "")
MINI_QMT_PATH = os.environ.get("MINI_QMT_PATH", r"d:\st_lc\userdata_mini")
TRADING_ACCOUNT_ID = os.environ.get("TRADING_ACCOUNT_ID", "xxxx")
TRADING_ACCOUNT_TYPE = os.environ.get("TRADING_ACCOUNT_TYPE", "STOCK")

# ===================== LOGGING =====================

_LOG_FILE = None

def _log(msg):
    """Print with flush and also write to log file."""
    line = "[bridge-mini] %s" % msg
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
    candidates.append(r"D:\st_lc")
    candidates.append(os.path.join(os.environ.get("TEMP", "C:\\Temp")))
    candidates.append(os.getcwd())
    for log_dir in candidates:
        try:
            path = os.path.join(log_dir, "BRIDGE_MINI_DEBUG.log")
            with open(path, "w") as f:
                f.write("=== BRIDGE_MINI v%d debug log started %s ===\n"
                        % (BRIDGE_VERSION, time.strftime("%Y-%m-%d %H:%M:%S")))
            _LOG_FILE = path
            print("[bridge-mini] debug log: %s" % _LOG_FILE, flush=True)
            return
        except Exception:
            continue
    _LOG_FILE = None

# ===================== HELPERS =====================

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


# ===================== QMT PATH DISCOVERY =====================

def _find_qmt_path():
    if QMT_PATH:
        return QMT_PATH
    search_patterns = [
        r"D:\st_lc",
        r"C:\*QMT*",
        r"D:\*QMT*",
        r"E:\*QMT*",
        r"C:\Program Files\*QMT*",
        r"D:\Program Files\*QMT*",
    ]
    for pattern in search_patterns:
        matches = glob.glob(pattern)
        for m in matches:
            site_pkg = os.path.join(m, "bin.x64", "Lib", "site-packages")
            if os.path.isdir(site_pkg) and os.path.isdir(
                os.path.join(site_pkg, "xtquant")
            ):
                _log("found QMT: %s" % m)
                return m
    return ""


def _setup_xtquant_path(qmt_path=""):
    path = qmt_path or _find_qmt_path()
    if not path:
        return ""
    site_pkg = os.path.join(path, "bin.x64", "Lib", "site-packages")
    if os.path.isdir(site_pkg) and site_pkg not in sys.path:
        sys.path.insert(0, site_pkg)
        _log("added xtquant path: %s" % site_pkg)
    for subdir in ["userdata_mini", "userdata"]:
        ud = os.path.join(path, subdir)
        if os.path.isdir(ud):
            return ud
    return ""


# ===================== HTTP HELPERS =====================

def _http_get(url, timeout=10, verbose=True):
    """Simple HTTP GET using urllib."""
    if verbose:
        _log("HTTP GET %s (timeout=%d) ..." % (url, timeout))
    try:
        import urllib.request
        req = urllib.request.Request(url)
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read().decode("utf-8")
    except Exception as e:
        if verbose:
            _log("HTTP GET %s failed: %s" % (url, e))
        return None


def _http_post(url, data_dict, timeout=10):
    """Simple HTTP POST JSON using urllib."""
    try:
        body = json.dumps(data_dict, ensure_ascii=False, default=str).encode("utf-8")
        import urllib.request
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read().decode("utf-8")
    except Exception as e:
        _log("HTTP POST %s failed: %s" % (url, e))
        return None


# ===================== DATA: xtdata FETCH =====================

_xt_connected = False
_xtdata_broken = False


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
        _log("df_to_bars(%s): %s" % (label, e))
    return bars


def _fetch_klines_xtdata(codes, period, count):
    """Fetch klines via xtdata (miniQMT mode -- no ContextInfo fallback)."""
    global _xt_connected, _xtdata_broken

    codes = [_normalize_to_qmt(c) for c in codes]

    if _xtdata_broken:
        _log("xtdata known broken, skipping")
        return {}

    try:
        import xtquant.xtdata as xtdata
    except ImportError:
        _log("ERROR: xtquant not installed")
        return {}

    if not _xt_connected:
        _log("xtdata not connected, data may be empty")

    _log("fetching klines: %d stocks, period=%s, count=%d" % (len(codes), period, count))

    raw = None

    # Method 1: download + get_market_data_ex
    for code in codes:
        try:
            xtdata.download_history_data(code, period)
        except Exception as e:
            _log("download %s failed: %s" % (code, e))
    try:
        raw = xtdata.get_market_data_ex(
            field_list=["open", "high", "low", "close", "volume"],
            stock_list=codes,
            period=period,
            count=count,
            dividend_type="front",
            fill_data=True,
        )
        if raw:
            nonempty = sum(1 for v in raw.values()
                          if v is not None and not (hasattr(v, "empty") and v.empty))
            _log("get_market_data_ex: %d/%d non-empty" % (nonempty, len(raw)))
    except Exception as e:
        _log("get_market_data_ex failed: %s" % e)

    # Method 2: get_local_data fallback
    if (not raw or all(v is None or (hasattr(v, "empty") and v.empty) for v in raw.values())) \
            and hasattr(xtdata, "get_local_data"):
        _log("trying get_local_data fallback...")
        try:
            raw = xtdata.get_local_data(
                field_list=["open", "high", "low", "close", "volume"],
                stock_list=codes,
                period=period,
                count=count,
                dividend_type="front",
                fill_data=True,
            )
            if raw:
                _log("get_local_data returned %d keys" % len(raw))
        except Exception as e:
            _log("get_local_data failed: %s" % e)

    # Method 3: get_market_data (older API)
    if (not raw or all(v is None or (hasattr(v, "empty") and v.empty) for v in raw.values())) \
            and hasattr(xtdata, "get_market_data"):
        _log("trying get_market_data fallback...")
        try:
            raw = xtdata.get_market_data(
                field_list=["open", "high", "low", "close", "volume"],
                stock_list=codes,
                period=period,
                count=count,
            )
        except Exception as e:
            _log("get_market_data failed: %s" % e)

    if not raw:
        _log("all methods returned empty/None")
        _xt_connected = False
        return {}

    result = {}
    for qmt_code, df in raw.items():
        if df is None or (hasattr(df, "empty") and df.empty):
            continue
        stock_id = qmt_code.split(".")[0]
        bars = _df_to_bars(df, qmt_code)
        if bars:
            result[stock_id] = bars

    _log("got %d/%d stocks" % (len(result), len(codes)))
    return result


# ===================== TRADING BRIDGE =====================

_trading_trader = None
_trading_account = None
_trading_connected = False
_trading_subscribed_codes = set()
_trading_events_queue = []


class _TradingCallback(object):
    """Receives async push notifications from QMT and queues for Mac."""
    def on_disconnected(self):
        _trading_events_queue.append({"event": "disconnected", "data": {}})

    def on_stock_order(self, order):
        _trading_events_queue.append({"event": "order_status", "data": {
            "order_id": str(order.order_id),
            "stock_code": order.stock_code,
            "order_type": order.order_type,
            "price": order.price,
            "order_volume": order.order_volume,
            "traded_volume": order.traded_volume,
            "traded_price": order.traded_price,
            "order_status": order.order_status,
            "strategy_name": order.strategy_name or "",
            "strategy_error_msg": order.strategy_error_msg or "",
            "order_time": order.order_time or "",
        }})

    def on_stock_trade(self, trade):
        value = trade.traded_price * trade.traded_volume
        commission = round(value * 0.0005, 4)
        _trading_events_queue.append({"event": "fill", "data": {
            "order_id": str(trade.order_id),
            "stock_code": trade.stock_code,
            "traded_price": trade.traded_price,
            "traded_volume": trade.traded_volume,
            "traded_amount": value,
            "commission": commission,
            "time": trade.traded_time or "",
        }})

    def on_order_error(self, order_error):
        _trading_events_queue.append({"event": "order_status", "data": {
            "order_id": str(order_error.order_id),
            "stock_code": order_error.stock_code,
            "order_status": 58,
            "strategy_error_msg": order_error.error_msg or "unknown error",
        }})


def _trading_connect(params):
    """Connect to miniQMT via XtQuantTrader for trading."""
    global _trading_trader, _trading_account, _trading_connected
    if _trading_connected and _trading_trader:
        _log("trading: already connected, skipping")
        return {"ok": True, "data": {"status": "already_connected", "account_id": TRADING_ACCOUNT_ID}}
    try:
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount
    except ImportError:
        return {"ok": False, "error": "xtquant not installed"}

    account_id = params.get("account_id", "") or TRADING_ACCOUNT_ID
    account_type = params.get("account_type", "") or TRADING_ACCOUNT_TYPE
    qmt_path = params.get("qmt_userdata_path", "") or MINI_QMT_PATH

    if not qmt_path or not os.path.isdir(qmt_path):
        return {"ok": False, "error": "miniQMT userdata path not found: %s" % qmt_path}
    if not account_id or account_id == "xxxx":
        return {"ok": False, "error": "account_id not configured (set TRADING_ACCOUNT_ID)"}

    _log("trading: connecting via miniQMT, userdata=%s" % qmt_path)

    try:
        _trading_trader = XtQuantTrader(qmt_path, int(time.time()) % 10000)
        cb = _TradingCallback()
        _trading_trader.register_callback(cb)
        _trading_trader.start()

        for _ in range(20):
            if _trading_trader.connect() == 0:
                break
            time.sleep(0.5)
        else:
            return {"ok": False, "error": "Failed to connect to miniQMT within 10 seconds"}

        _trading_account = StockAccount(account_id, account_type)
        result = _trading_trader.subscribe(_trading_account)
        if result != 0:
            return {"ok": False, "error": "Failed to subscribe account %s (code=%d)" % (account_id, result)}

        _trading_connected = True
        _log("trading: connected, account=%s" % account_id)
        return {"ok": True, "data": {"status": "connected", "account_id": account_id}}
    except Exception as e:
        return {"ok": False, "error": "connect failed: %s" % str(e)}


def _trading_disconnect(_params):
    global _trading_trader, _trading_account, _trading_connected
    if _trading_trader:
        try:
            _trading_trader.stop()
        except Exception:
            pass
        _trading_trader = None
    _trading_account = None
    _trading_connected = False
    _log("trading: disconnected")
    return {"ok": True, "data": {"status": "disconnected"}}


def _trading_place_order(params):
    if not _trading_trader or not _trading_account:
        return {"ok": False, "error": "Not connected"}
    stock_code = params.get("stock_code", "")
    order_type = int(params.get("order_type", 23))
    price_type = int(params.get("price_type", 11))
    price = float(params.get("price", 0))
    volume = int(params.get("volume", 0))
    strategy = params.get("strategy_name", "")
    remark = params.get("order_remark", "")
    try:
        order_id = _trading_trader.order_stock(
            _trading_account, stock_code, order_type, volume,
            price_type, price, strategy, remark)
        if order_id < 0:
            return {"ok": False, "error": "place_order failed, QMT returned %d" % order_id}
        _log("trading: placed order %s qty=%d price=%.2f => id=%s"
             % (stock_code, volume, price, order_id))
        return {"ok": True, "data": {"broker_order_id": str(order_id)}}
    except Exception as e:
        return {"ok": False, "error": "place_order error: %s" % str(e)}


def _trading_cancel_order(params):
    if not _trading_trader or not _trading_account:
        return {"ok": False, "error": "Not connected"}
    order_id = int(params.get("order_id", 0))
    try:
        result = _trading_trader.cancel_order_stock(_trading_account, order_id)
        return {"ok": True, "data": {"result": result}}
    except Exception as e:
        return {"ok": False, "error": "cancel_order error: %s" % str(e)}


def _serialize_qmt_order(o):
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
    }


def _lookup_stock_name(stock_code):
    """Look up stock name via xtdata.get_instrument_detail."""
    try:
        import xtquant.xtdata as xtdata
        detail = xtdata.get_instrument_detail(stock_code)
        if detail and isinstance(detail, dict):
            return detail.get("InstrumentName", "") or ""
    except Exception:
        pass
    return ""


def _serialize_qmt_position(p):
    stock_name = getattr(p, "stock_name", "") or ""
    if not stock_name:
        stock_name = _lookup_stock_name(p.stock_code)
    return {
        "stock_code": p.stock_code,
        "stock_name": stock_name,
        "volume": p.volume,
        "can_use_volume": p.can_use_volume,
        "frozen_volume": p.frozen_volume,
        "avg_price": p.avg_price,
        "market_value": p.market_value,
    }


def _trading_query_order(params):
    if not _trading_trader or not _trading_account:
        return {"ok": False, "error": "Not connected"}
    order_id = int(params.get("order_id", 0))
    try:
        orders = _trading_trader.query_stock_orders(_trading_account, cancel_able_only=False)
        for o in (orders or []):
            if o.order_id == order_id:
                return {"ok": True, "data": _serialize_qmt_order(o)}
        return {"ok": False, "error": "Order %d not found" % order_id}
    except Exception as e:
        return {"ok": False, "error": "query_order error: %s" % str(e)}


def _trading_query_orders(params):
    if not _trading_trader or not _trading_account:
        return {"ok": False, "error": "Not connected"}
    stock_code = params.get("stock_code")
    try:
        orders = _trading_trader.query_stock_orders(_trading_account, cancel_able_only=False) or []
        result = [_serialize_qmt_order(o) for o in orders]
        if stock_code:
            result = [o for o in result if o["stock_code"] == stock_code]
        return {"ok": True, "data": result}
    except Exception as e:
        return {"ok": False, "error": "query_orders error: %s" % str(e)}


def _trading_query_positions(_params):
    if not _trading_trader or not _trading_account:
        return {"ok": False, "error": "Not connected"}
    try:
        positions = _trading_trader.query_stock_positions(_trading_account) or []
        result = [_serialize_qmt_position(p) for p in positions]
        return {"ok": True, "data": result}
    except Exception as e:
        return {"ok": False, "error": "query_positions error: %s" % str(e)}


def _trading_query_account(_params):
    if not _trading_trader or not _trading_account:
        return {"ok": False, "error": "Not connected"}
    try:
        assets = _trading_trader.query_stock_asset(_trading_account)
        if not assets:
            return {"ok": False, "error": "Failed to query account assets"}
        return {"ok": True, "data": {
            "account_id": _trading_account.account_id if _trading_account else "",
            "cash": assets.cash,
            "frozen_cash": assets.frozen_cash,
            "total_asset": assets.total_asset,
            "market_value": assets.market_value,
            "profit": assets.profit,
            "profit_ratio": assets.profit_ratio,
        }}
    except Exception as e:
        return {"ok": False, "error": "query_account error: %s" % str(e)}


def _on_quote_push(datas):
    """Callback for xtdata.subscribe_whole_quote — queues quote events for Mac polling."""
    import time as _time
    now = _time.time()
    for code, bars in datas.items():
        if not bars:
            continue
        # Throttle: max 1 event per symbol per 3 seconds to avoid queue bloat
        last = _quote_push_last.get(code, 0)
        if now - last < 3.0:
            continue
        _quote_push_last[code] = now
        bar = bars[-1] if isinstance(bars, list) else bars
        _trading_events_queue.append({"event": "quote", "data": {
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
            "time": int(now * 1000),
        }})

_quote_push_last = {}  # {stock_code: last_push_timestamp}


def _trading_subscribe_quote(params):
    codes = params.get("stock_codes", [])
    new_codes = [c for c in codes if c not in _trading_subscribed_codes]
    if new_codes:
        try:
            import xtquant.xtdata as xtdata
            xtdata.subscribe_whole_quote(new_codes, callback=_on_quote_push)
            _trading_subscribed_codes.update(new_codes)
        except Exception as e:
            return {"ok": False, "error": "subscribe_quote error: %s" % str(e)}
    return {"ok": True, "data": {"subscribed": list(_trading_subscribed_codes)}}


def _trading_unsubscribe_quote(params):
    codes = params.get("stock_codes", [])
    for c in codes:
        _trading_subscribed_codes.discard(c)
    return {"ok": True, "data": {"subscribed": list(_trading_subscribed_codes)}}


def _trading_save_strategy_file(params):
    """Save a strategy file to the MiniQMT strategies directory."""
    content = params.get("content", "")
    filename = params.get("filename", "")
    save_dir = params.get("save_dir", "")
    if not content or not filename:
        return {"ok": False, "error": "content and filename are required"}

    if not save_dir:
        # Default to MiniQMT userdata strategies dir
        save_dir = os.path.join(MINI_QMT_PATH, "strategies") if MINI_QMT_PATH else ""
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
    "save_strategy_file": _trading_save_strategy_file,
}


# ===================== TRADING POLL =====================

_trading_parse_err_count = 0

def _trading_poll_and_execute():
    """Poll Mac for pending trading commands, execute locally, push results."""
    global _trading_parse_err_count
    url = MAC_RECEIVER_URL + "/quant/api/trading-request"
    raw = _http_get(url, timeout=5, verbose=False)
    if raw is None:
        return 0
    if not raw.strip():
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

    result["requestId"] = req_id
    resp_url = MAC_RECEIVER_URL + "/quant/api/trading-response"
    try:
        resp = _http_post(resp_url, result, timeout=10)
        if resp:
            _log("trading: pushed result for %s: ok=%s" % (req_id, result.get("ok")))
        else:
            _log("trading: FAILED to push result for %s" % req_id)
    except Exception as e:
        _log("trading: push error for %s: %s" % (req_id, e))

    return 1


def _trading_push_events():
    """Push queued real-time events (fills, order_status) to Mac."""
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


# ===================== RESOLVE MINI PATH =====================

def _resolve_mini_path():
    """Resolve miniQMT userdata path."""
    if QMT_USERDATA_PATH and os.path.isdir(QMT_USERDATA_PATH):
        return QMT_USERDATA_PATH
    if MINI_QMT_PATH and os.path.isdir(MINI_QMT_PATH):
        return MINI_QMT_PATH
    qmt_root = _find_qmt_path()
    if qmt_root:
        candidate = os.path.join(qmt_root, "userdata_mini")
        if os.path.isdir(candidate):
            return candidate
    return ""


# ===================== MAIN GATEWAY LOOP =====================

_processed_ids = set()

def main(mac_url):
    """miniQMT gateway main loop."""
    global MAC_RECEIVER_URL, _xt_connected
    MAC_RECEIVER_URL = mac_url

    _init_log_file()
    _log("========================================")
    _log("BRIDGE_MINI v%d (miniQMT gateway)" % BRIDGE_VERSION)
    _log("========================================")
    _log("Mac receiver: %s" % MAC_RECEIVER_URL)

    # 1. Setup xtquant path
    _xtquant_userdata = _setup_xtquant_path()
    if _xtquant_userdata:
        _log("QMT userdata: %s" % _xtquant_userdata)

    # 2. Resolve miniQMT path
    mini_path = _resolve_mini_path()
    _log("miniQMT userdata: %s" % (mini_path or "NOT FOUND"))
    if not mini_path or not os.path.isdir(mini_path):
        _log("ERROR: miniQMT userdata path not found")
        _log("Ensure XtMiniQmt.exe is installed and set MINI_QMT_PATH if needed")
        _log("Default search: d:\\st_lc\\userdata_mini")
        return

    # 3. Connect xtdata for market data
    _log("Connecting xtdata to miniQMT...")
    try:
        import xtquant.xtdata as xtdata
        if hasattr(xtdata, "connect"):
            ret = xtdata.connect(mini_path)
            _log("xtdata.connect('%s') => %s" % (mini_path, ret))
        else:
            if hasattr(xtdata, "rpc_init"):
                xtdata.rpc_init(mini_path)
                _log("xtdata.rpc_init('%s') OK" % mini_path)
            if hasattr(xtdata, "run"):
                xtdata.run()
                _log("xtdata.run() OK")

        detail = xtdata.get_instrument_detail("600519.SH")
        if detail and isinstance(detail, dict) and detail.get("InstrumentName"):
            _xt_connected = True
            _log("xtdata connected: %s" % detail.get("InstrumentName", ""))
        else:
            _log("WARNING: xtdata loaded but verify failed: %s" % str(detail)[:200])
    except ImportError:
        _log("ERROR: xtquant not installed. Ensure QMT site-packages is on path.")
        return
    except Exception as e:
        _log("WARNING: xtdata connection failed: %s" % e)
        _log("Market data will be unavailable until xtdata connects")

    # 4. Connect trading
    if TRADING_ACCOUNT_ID and TRADING_ACCOUNT_ID != "xxxx":
        _log("Connecting trading (account=%s)..." % TRADING_ACCOUNT_ID)
        result = _trading_connect({"qmt_userdata_path": mini_path})
        _log("Trading connect: %s" % json.dumps(result, ensure_ascii=False))
    else:
        _log("Trading disabled (set TRADING_ACCOUNT_ID to enable)")

    # 5. Combined polling loop
    _log("Starting miniQMT gateway loop...")
    _log("========================================")

    fail_count = 0
    loop_count = 0
    _HEARTBEAT = 60

    while True:
        loop_count += 1
        try:
            if loop_count % _HEARTBEAT == 0:
                _log("heartbeat #%d (xtdata=%s, trading=%s, processed=%d)"
                     % (loop_count, _xt_connected, _trading_connected,
                        len(_processed_ids)))

            # --- Data polling ---
            try:
                url = MAC_RECEIVER_URL + "/pending"
                raw = _http_get(url, timeout=5, verbose=False)
                if raw is None:
                    fail_count += 1
                    if fail_count <= 3 or fail_count % 60 == 0:
                        _log("Mac not reachable (attempt %d)" % fail_count)
                else:
                    if fail_count > 0:
                        _log("Mac connection recovered")
                    fail_count = 0
                    try:
                        resp = json.loads(raw)
                    except Exception as e:
                        _log("parse /pending error: %s" % e)
                        time.sleep(POLL_INTERVAL)
                        continue

                    for req in resp.get("requests", []):
                        req_id = req.get("id", "")
                        codes = req.get("codes", [])
                        period = req.get("period", "1d")
                        count = int(req.get("count", 120))
                        if not req_id or not codes or req_id in _processed_ids:
                            continue

                        codes = [_normalize_to_qmt(c) for c in codes]
                        _log("processing %s: codes=%s period=%s count=%d"
                             % (req_id, codes, period, count))

                        klines = _fetch_klines_xtdata(codes, period, count)
                        source = "mini-xtdata" if klines else "mini-empty"

                        result_url = MAC_RECEIVER_URL + "/result/" + req_id
                        push_data = {
                            "data": klines,
                            "count": len(klines),
                            "source": source,
                        }
                        push_resp = _http_post(result_url, push_data, timeout=10)
                        if push_resp:
                            _log("pushed %s: %d stocks (%s)"
                                 % (req_id, len(klines), source))
                        else:
                            _log("FAILED to push %s" % req_id)
                        _processed_ids.add(req_id)
            except Exception as e:
                _log("data poll error: %s" % e)

            # --- Trading polling ---
            try:
                _trading_poll_and_execute()
            except Exception as e:
                _log("trading poll error: %s" % e)

            # --- Push trading events ---
            _trading_push_events()

        except Exception as e:
            _log("loop error: %s" % e)

        time.sleep(POLL_INTERVAL)


# ===================== ENTRY POINT =====================

if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(
        description="BRIDGE_MINI v%d - miniQMT Gateway" % BRIDGE_VERSION,
        epilog="""
Standalone gateway for miniQMT (no QMT UI needed).
Connects to XtMiniQmt.exe for market data + trading,
polls Mac RECEIVER.py for commands.

Examples:
  python BRIDGE_MINI.py
  python BRIDGE_MINI.py --mac http://192.168.3.36:58088
  python BRIDGE_MINI.py --account YOUR_ACCOUNT_ID
  python BRIDGE_MINI.py --mini-path D:\\st_lc\\userdata_mini
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--mac", default=MAC_RECEIVER_URL,
                   help="Mac receiver URL (default: %s)" % MAC_RECEIVER_URL)
    p.add_argument("--account", default="",
                   help="Trading account ID (overrides TRADING_ACCOUNT_ID env)")
    p.add_argument("--mini-path", default="",
                   help="miniQMT userdata path (overrides MINI_QMT_PATH env)")
    args, _ = p.parse_known_args()

    if args.account:
        TRADING_ACCOUNT_ID = args.account
    if args.mini_path:
        MINI_QMT_PATH = args.mini_path

    try:
        main(args.mac)
    except KeyboardInterrupt:
        print("\n[bridge-mini] stopped")
