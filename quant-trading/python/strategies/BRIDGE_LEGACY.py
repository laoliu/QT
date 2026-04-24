# coding: utf-8
#
# BRIDGE_LEGACY.py - QMT Data Bridge Legacy Standalone Modes
# VERSION: 19
#
# Two legacy standalone modes (no QMT strategy engine required):
#   push  - Polls Mac RECEIVER.py /pending, fetches data via xtdata, pushes results
#   serve - HTTP server on Windows, Mac sends POST /klines requests
#
# Both modes also support trading bridge (polls trading commands from Mac).
#
# For strategy mode (inside QMT), use BRIDGE.py instead.
# For miniQMT gateway, use BRIDGE_MINI.py instead.
#

BRIDGE_VERSION = 19

import glob
import json
import os
import sys
import threading
import time

# ===================== CONFIG =====================

_BRIDGE_HOST = os.environ.get("OPENCLAW_HOST", "192.168.3.36")
_BRIDGE_PORT = os.environ.get("BRIDGE_RECEIVER_PORT", "58088")
MAC_RECEIVER_URL = "http://%s:%s" % (_BRIDGE_HOST, _BRIDGE_PORT)

SERVE_HOST = "0.0.0.0"
SERVE_PORT = 58088
QMT_PATH = os.environ.get("QMT_PATH", "")
QMT_USERDATA_PATH = os.environ.get("QMT_USERDATA_PATH", "")
MINI_QMT_PATH = os.environ.get("MINI_QMT_PATH", r"d:\st_lc\userdata_mini")
TRADING_ACCOUNT_ID = os.environ.get("TRADING_ACCOUNT_ID", "xxxx")
TRADING_ACCOUNT_TYPE = os.environ.get("TRADING_ACCOUNT_TYPE", "STOCK")

POLL_INTERVAL = 1.0

# ===================== LOGGING =====================

_LOG_FILE = None


def _log(msg):
    line = "[bridge-legacy] %s" % msg
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
            path = os.path.join(log_dir, "BRIDGE_LEGACY_DEBUG.log")
            with open(path, "w") as f:
                f.write("=== BRIDGE_LEGACY v%d debug log started %s ===\n"
                        % (BRIDGE_VERSION, time.strftime("%Y-%m-%d %H:%M:%S")))
            _LOG_FILE = path
            print("[bridge-legacy] debug log: %s" % _LOG_FILE, flush=True)
            return
        except Exception:
            continue
    _LOG_FILE = None


# ===================== UTILITIES =====================

_xt_connected = False
_xtdata_broken = False
_xt_diag = {"connect_attempts": 0, "last_error": "", "connected": False}
_processed_ids = set()


def _normalize_to_qmt(code):
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
        r"C:\*QMT*", r"D:\*QMT*", r"E:\*QMT*",
        r"C:\Program Files\*QMT*", r"D:\Program Files\*QMT*",
    ]
    for pattern in search_patterns:
        matches = glob.glob(pattern)
        for m in matches:
            site_pkg = os.path.join(m, "bin.x64", "Lib", "site-packages")
            if os.path.isdir(site_pkg) and os.path.isdir(
                    os.path.join(site_pkg, "xtquant")):
                return m
    return ""


def _detect_qmt_process():
    try:
        import subprocess
        for proc_name, variant in [("XtItClient.exe", "client"), ("XtMiniQmt.exe", "mini")]:
            try:
                out = subprocess.check_output(
                    ["tasklist", "/FI", "IMAGENAME eq %s" % proc_name],
                    stderr=subprocess.PIPE,
                )
                if proc_name.split(".")[0] in out.decode("utf-8", errors="replace"):
                    return variant, proc_name
            except Exception:
                continue
    except Exception:
        pass
    return None, ""


def _setup_xtquant_path(qmt_path=""):
    path = qmt_path or _find_qmt_path()
    if not path:
        return ""
    site_pkg = os.path.join(path, "bin.x64", "Lib", "site-packages")
    if os.path.isdir(site_pkg) and site_pkg not in sys.path:
        sys.path.insert(0, site_pkg)
        _log("added xtquant path: %s" % site_pkg)
    variant, _ = _detect_qmt_process()
    if variant == "client":
        subdirs = ["userdata", "userdata_mini"]
    else:
        subdirs = ["userdata_mini", "userdata"]
    for subdir in subdirs:
        ud = os.path.join(path, subdir)
        if os.path.isdir(ud):
            return ud
    return ""


# ===================== XTDATA CONNECTION =====================

def _ensure_xtdata_connected(force=False):
    global _xt_connected, _xt_diag, _xtdata_broken

    try:
        import xtquant.xtdata as xtdata
    except ImportError:
        _xt_diag["last_error"] = "xtquant not installed"
        _log("ERROR: xtquant not found")
        return None, False

    if _xt_connected and not force:
        return xtdata, True

    _xt_diag["connect_attempts"] += 1
    _log("=== connection attempt #%d ===" % _xt_diag["connect_attempts"])

    qmt_path = _find_qmt_path()
    variant, proc = _detect_qmt_process()
    if variant == "client":
        subdirs = ["userdata", "userdata_mini"]
    else:
        subdirs = ["userdata_mini", "userdata"]
    userdata_candidates = []
    if qmt_path:
        for subdir in subdirs:
            p = os.path.join(qmt_path, subdir)
            if os.path.isdir(p):
                userdata_candidates.append(p)

    _log("detected QMT process: %s (%s)" % (proc or "none", variant or "none"))
    _log("userdata candidates: %s" % userdata_candidates)

    # Fix data_dir to absolute path
    for ud in userdata_candidates:
        abs_datadir = os.path.join(ud, "datadir")
        if os.path.isdir(abs_datadir) and hasattr(xtdata, "data_dir"):
            old_dd = getattr(xtdata, "data_dir", "")
            if old_dd and not os.path.isabs(old_dd):
                xtdata.data_dir = abs_datadir
                if hasattr(xtdata, "default_data_dir"):
                    xtdata.default_data_dir = abs_datadir
                _log("fixed data_dir: %s -> %s" % (old_dd, abs_datadir))
            break

    connected = False
    for userdata in userdata_candidates:
        _log("--- trying userdata: %s ---" % userdata)

        if hasattr(xtdata, "rpc_init"):
            try:
                ret = xtdata.rpc_init(userdata)
                _log("rpc_init('%s') returned: %s" % (userdata, ret))
            except Exception as e:
                _log("rpc_init failed: %s" % e)
                continue

        if hasattr(xtdata, "run"):
            try:
                xtdata.run()
                _log("xtdata.run() OK")
            except Exception as e:
                _log("xtdata.run() failed: %s" % e)

        if hasattr(xtdata, "reconnect"):
            try:
                xtdata.reconnect()
                _log("reconnect() OK")
            except Exception as e:
                _log("reconnect warning: %s" % e)

        if hasattr(xtdata, "connect"):
            try:
                ret = xtdata.connect(userdata)
                _log("connect('%s') returned: %s" % (userdata, ret))
            except Exception as e:
                _log("connect() failed: %s" % e)

        if hasattr(xtdata, "get_instrument_detail"):
            try:
                detail = xtdata.get_instrument_detail("600519.SH")
                if detail and isinstance(detail, dict) and detail.get("InstrumentName"):
                    connected = True
                    _log("verify OK with %s: %s" % (userdata, detail.get("InstrumentName", "")))
                    break
            except Exception as e:
                _log("verify failed with %s: %s" % (userdata, e))

    if not connected:
        if hasattr(xtdata, "run"):
            try:
                xtdata.run()
            except Exception:
                pass
        if hasattr(xtdata, "get_instrument_detail"):
            try:
                detail = xtdata.get_instrument_detail("600519.SH")
                if detail and isinstance(detail, dict) and detail.get("InstrumentName"):
                    connected = True
                    _log("verify OK (no explicit userdata)")
            except Exception:
                pass

    _xt_connected = connected
    _xt_diag["connected"] = connected

    if connected:
        _log("=== connected OK ===")
    else:
        _log("=== NOT connected (will retry on next request) ===")
        if _xt_diag["connect_attempts"] >= 2:
            _xtdata_broken = True
            _log("xtdata marked as broken after %d attempts" % _xt_diag["connect_attempts"])

    return xtdata, connected


def _get_diagnostics():
    import copy
    diag = copy.deepcopy(_xt_diag)
    try:
        variant, proc_name = _detect_qmt_process()
        diag["qmt_variant"] = variant or "none"
        diag["qmt_process"] = proc_name or "none"
    except Exception:
        diag["qmt_variant"] = "check failed"
    try:
        import xtquant
        diag["xtquant_version"] = getattr(xtquant, "__version__", "unknown")
    except Exception:
        diag["xtquant_version"] = "not installed"
    return diag


# ===================== KLINE FETCH (xtdata only, no ContextInfo) =====================

def _df_to_bars(df, label=""):
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
    global _xt_connected, _xtdata_broken

    codes = [_normalize_to_qmt(c) for c in codes]

    if _xtdata_broken:
        _log("xtdata known broken, skipping")
        return {}

    xtdata, connected = _ensure_xtdata_connected()
    if xtdata is None:
        return {}

    _log("fetching klines: %d stocks, period=%s, count=%d (connected=%s)"
         % (len(codes), period, count, connected))

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
            stock_list=codes, period=period, count=count,
            dividend_type="front", fill_data=True,
        )
    except Exception as e:
        _log("get_market_data_ex failed: %s" % e)

    # Method 2: get_local_data fallback
    if (not raw or all(v is None or (hasattr(v, "empty") and v.empty) for v in raw.values())) \
            and hasattr(xtdata, "get_local_data"):
        try:
            raw = xtdata.get_local_data(
                field_list=["open", "high", "low", "close", "volume"],
                stock_list=codes, period=period, count=count,
                dividend_type="front", fill_data=True,
            )
        except Exception as e:
            _log("get_local_data failed: %s" % e)

    # Method 3: get_market_data (older API)
    if (not raw or all(v is None or (hasattr(v, "empty") and v.empty) for v in raw.values())) \
            and hasattr(xtdata, "get_market_data"):
        try:
            raw = xtdata.get_market_data(
                field_list=["open", "high", "low", "close", "volume"],
                stock_list=codes, period=period, count=count,
            )
        except Exception as e:
            _log("get_market_data failed: %s" % e)

    if not raw:
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


# ===================== HTTP HELPERS =====================

def _http_get(url, timeout=10, verbose=True):
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
    try:
        body = json.dumps(data_dict, ensure_ascii=False, default=str).encode("utf-8")
        if sys.version_info[0] >= 3:
            import urllib.request
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib.request.urlopen(req, timeout=timeout)
            return resp.read().decode("utf-8")
        else:
            import urllib2
            req = urllib2.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
            )
            resp = urllib2.urlopen(req, timeout=timeout)
            return resp.read().decode("utf-8")
    except Exception as e:
        _log("HTTP POST %s failed: %s" % (url, e))
        return None


# ===================== TRADING BRIDGE =====================

_trading_trader = None
_trading_account = None
_trading_connected = False
_trading_subscribed_codes = set()
_trading_events_queue = []


class _TradingCallback(object):
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


def _resolve_qmt_userdata(params):
    explicit = params.get("qmt_userdata_path", "") or params.get("mini_qmt_path", "")
    if explicit and os.path.isdir(explicit):
        return explicit, "explicit"
    if QMT_USERDATA_PATH and os.path.isdir(QMT_USERDATA_PATH):
        return QMT_USERDATA_PATH, "env"
    variant, proc_name = _detect_qmt_process()
    if variant:
        qmt_root = _find_qmt_path()
        if qmt_root:
            if variant == "client":
                userdata = os.path.join(qmt_root, "userdata")
            else:
                userdata = os.path.join(qmt_root, "userdata_mini")
            if os.path.isdir(userdata):
                return userdata, variant
    if MINI_QMT_PATH and os.path.isdir(MINI_QMT_PATH):
        return MINI_QMT_PATH, "mini-fallback"
    detected = _setup_xtquant_path()
    if detected:
        return detected, "auto"
    return "", "none"


def _trading_connect(params):
    global _trading_trader, _trading_account, _trading_connected
    if _trading_connected and _trading_trader:
        return {"ok": True, "data": {"status": "already_connected", "account_id": TRADING_ACCOUNT_ID}}
    try:
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount
    except ImportError:
        return {"ok": False, "error": "xtquant not installed"}

    account_id = params.get("account_id", "") or TRADING_ACCOUNT_ID
    account_type = params.get("account_type", "") or TRADING_ACCOUNT_TYPE
    qmt_path, variant = _resolve_qmt_userdata(params)
    if not qmt_path:
        return {"ok": False, "error": "QMT userdata path not found"}
    if not account_id:
        return {"ok": False, "error": "account_id not configured"}

    _log("trading: connecting via %s, userdata=%s" % (variant, qmt_path))
    try:
        _trading_trader = XtQuantTrader(qmt_path, int(time.time()) % 10000)
        _trading_trader.register_callback(_TradingCallback())
        _trading_trader.start()
        for _ in range(20):
            if _trading_trader.connect() == 0:
                break
            time.sleep(0.5)
        else:
            return {"ok": False, "error": "Failed to connect to QMT (%s) within 10s" % variant}

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
        "order_id": str(o.order_id), "stock_code": o.stock_code,
        "order_type": o.order_type, "price": o.price,
        "order_volume": o.order_volume, "traded_volume": o.traded_volume,
        "traded_price": o.traded_price, "order_status": o.order_status,
        "strategy_name": o.strategy_name or "",
    }


def _serialize_qmt_position(p):
    return {
        "stock_code": p.stock_code, "stock_name": getattr(p, "stock_name", ""),
        "volume": p.volume, "can_use_volume": p.can_use_volume,
        "frozen_volume": p.frozen_volume, "avg_price": p.avg_price,
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
        return {"ok": True, "data": [_serialize_qmt_position(p) for p in positions]}
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
            "cash": assets.cash, "frozen_cash": assets.frozen_cash,
            "total_asset": assets.total_asset, "market_value": assets.market_value,
            "profit": assets.profit, "profit_ratio": assets.profit_ratio,
        }}
    except Exception as e:
        return {"ok": False, "error": "query_account error: %s" % str(e)}


def _trading_subscribe_quote(params):
    codes = params.get("stock_codes", [])
    new_codes = [c for c in codes if c not in _trading_subscribed_codes]
    if new_codes:
        try:
            import xtquant.xtdata as xtdata
            xtdata.subscribe_whole_quote(new_codes)
            _trading_subscribed_codes.update(new_codes)
        except Exception as e:
            return {"ok": False, "error": "subscribe_quote error: %s" % str(e)}
    return {"ok": True, "data": {"subscribed": list(_trading_subscribed_codes)}}


def _trading_unsubscribe_quote(params):
    codes = params.get("stock_codes", [])
    for c in codes:
        _trading_subscribed_codes.discard(c)
    return {"ok": True, "data": {"subscribed": list(_trading_subscribed_codes)}}


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
}


_trading_parse_err_count = 0


def _trading_poll_and_execute():
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


def _trading_poll_loop():
    _log("trading poll thread started")
    if TRADING_ACCOUNT_ID and TRADING_ACCOUNT_ID != "xxxx" and not _trading_connected:
        _log("trading: auto-connecting (account=%s)" % TRADING_ACCOUNT_ID)
        try:
            result = _trading_connect({})
            _log("trading: auto-connect: %s" % json.dumps(result, ensure_ascii=False))
        except Exception as e:
            _log("trading: auto-connect failed: %s" % e)

    fail_count = 0
    loop_count = 0
    while True:
        loop_count += 1
        try:
            if loop_count % 60 == 0:
                _log("trading heartbeat #%d (connected=%s)" % (loop_count, _trading_connected))
            try:
                _trading_poll_and_execute()
                fail_count = 0
            except Exception as e:
                fail_count += 1
                if fail_count <= 3 or fail_count % 30 == 0:
                    _log("trading poll error #%d: %s" % (fail_count, e))
            _trading_push_events()
        except Exception as e:
            _log("trading loop error: %s" % e)
        time.sleep(POLL_INTERVAL)


# ===================== PUSH MODE =====================

def _poll_and_push():
    """Poll Mac /pending, fetch via xtdata, push results."""
    url = MAC_RECEIVER_URL + "/pending"
    raw = _http_get(url, timeout=5, verbose=False)
    if raw is None:
        return 0

    try:
        resp = json.loads(raw)
    except Exception as e:
        _log("parse /pending error: %s" % e)
        return 0

    requests = resp.get("requests", [])
    if not requests:
        return 0

    _log("got %d pending requests" % len(requests))

    for req in requests:
        req_id = req.get("id", "")
        codes = req.get("codes", [])
        period = req.get("period", "1d")
        count = int(req.get("count", 120))
        if not req_id or not codes or req_id in _processed_ids:
            continue

        codes = [_normalize_to_qmt(c) for c in codes]
        _log("processing %s: codes=%s period=%s count=%d" % (req_id, codes, period, count))

        klines = _fetch_klines_xtdata(codes, period, count)
        source = "legacy-push" if klines else "legacy-empty"

        result_url = MAC_RECEIVER_URL + "/result/" + req_id
        push_data = {"data": klines, "count": len(klines), "source": source}
        push_resp = _http_post(result_url, push_data, timeout=10)
        if push_resp:
            _log("pushed %s: %d stocks (%s)" % (req_id, len(klines), source))
        else:
            _log("FAILED to push %s" % req_id)
        _processed_ids.add(req_id)

    return len(requests)


# ===================== SERVE MODE (HTTP server) =====================

def _send_http_response(conn, code, status_text, body_bytes):
    header = (
        "HTTP/1.1 %d %s\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        "Content-Length: %d\r\n"
        "Connection: close\r\n"
        "\r\n"
    ) % (code, status_text, len(body_bytes))
    try:
        conn.sendall(header.encode("ascii") + body_bytes)
    except Exception:
        pass


def _handle_one_request(conn, addr):
    try:
        conn.settimeout(2)
        raw = b""
        while b"\r\n\r\n" not in raw and len(raw) < 65536:
            try:
                chunk = conn.recv(4096)
            except Exception:
                break
            if not chunk:
                break
            raw += chunk

        header_end = raw.find(b"\r\n\r\n")
        if header_end < 0:
            return
        header_part = raw[:header_end].decode("utf-8", errors="replace")
        body_part = raw[header_end + 4:]

        lines = header_part.split("\r\n")
        if not lines:
            return
        parts = lines[0].split(" ")
        if len(parts) < 2:
            return
        method = parts[0].upper()
        path = parts[1]

        content_length = 0
        for line in lines[1:]:
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1].strip())

        while len(body_part) < content_length:
            try:
                chunk = conn.recv(4096)
            except Exception:
                break
            if not chunk:
                break
            body_part += chunk

        # GET /health
        if method == "GET" and path == "/health":
            xt_ok = False
            try:
                import xtquant.xtdata
                xt_ok = True
            except Exception:
                pass
            resp = json.dumps({"status": "ok" if xt_ok else "degraded",
                               "xtdata": xt_ok, "connected": _xt_connected})
            _send_http_response(conn, 200, "OK", resp.encode("utf-8"))
            return

        # GET /diag
        if method == "GET" and path == "/diag":
            resp = json.dumps(_get_diagnostics(), ensure_ascii=False, default=str)
            _send_http_response(conn, 200, "OK", resp.encode("utf-8"))
            return

        # POST /reconnect
        if method == "POST" and path == "/reconnect":
            _, ok = _ensure_xtdata_connected(force=True)
            resp = json.dumps({"status": "ok" if ok else "failed", "connected": ok},
                              ensure_ascii=False, default=str)
            _send_http_response(conn, 200 if ok else 503,
                                "OK" if ok else "Service Unavailable",
                                resp.encode("utf-8"))
            return

        # POST /klines
        if method == "POST" and path == "/klines":
            if content_length == 0:
                resp = json.dumps({"error": "empty body"})
                _send_http_response(conn, 400, "Bad Request", resp.encode("utf-8"))
                return
            try:
                req_body = json.loads(body_part.decode("utf-8"))
            except Exception as e:
                resp = json.dumps({"error": "JSON parse error: %s" % e})
                _send_http_response(conn, 400, "Bad Request", resp.encode("utf-8"))
                return

            codes = req_body.get("codes", [])
            if not codes or not isinstance(codes, list):
                resp = json.dumps({"error": "codes required"})
                _send_http_response(conn, 400, "Bad Request", resp.encode("utf-8"))
                return

            period = req_body.get("period", "1d")
            count = int(req_body.get("count", 120))
            _log("serve request: %d stocks, period=%s, count=%d" % (len(codes), period, count))

            klines = _fetch_klines_xtdata(codes, period, count)
            resp = json.dumps({"status": "ok", "data": klines, "count": len(klines)},
                              ensure_ascii=False)
            _send_http_response(conn, 200, "OK", resp.encode("utf-8"))
            return

        # 404
        resp = json.dumps({"error": "not found"})
        _send_http_response(conn, 404, "Not Found", resp.encode("utf-8"))

    except Exception as e:
        _log("serve error: %s" % e)
    finally:
        try:
            conn.close()
        except Exception:
            pass


def run_serve_mode(host=SERVE_HOST, port=SERVE_PORT):
    """Blocking HTTP server loop."""
    import socket as _socket

    srv = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
    srv.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    srv.bind((host, port))
    srv.listen(5)
    _log("HTTP server started -- http://%s:%d" % (host, port))

    while True:
        try:
            conn, addr = srv.accept()
            _handle_one_request(conn, addr)
        except KeyboardInterrupt:
            _log("server stopped")
            break
        except Exception as e:
            _log("accept error: %s" % e)


# ===================== ENTRY =====================

if __name__ == "__main__":
    import argparse as _argparse

    _p = _argparse.ArgumentParser(
        description="QMT Data Bridge v%d (legacy standalone)" % BRIDGE_VERSION,
        epilog="""
Modes:
  push   Poll Mac RECEIVER.py /pending, fetch via xtdata, push results
  serve  HTTP server on Windows, Mac sends POST /klines

For QMT strategy mode, use BRIDGE.py (loaded via QMT exec()).
For miniQMT gateway, use BRIDGE_MINI.py.

Examples:
  python BRIDGE_LEGACY.py --mode push --mac http://192.168.3.36:58088
  python BRIDGE_LEGACY.py --mode serve
  python BRIDGE_LEGACY.py --mode serve --port 58099
""",
        formatter_class=_argparse.RawDescriptionHelpFormatter,
    )
    _p.add_argument("--mode", choices=["push", "serve"], default="push",
                     help="push=poll+push; serve=HTTP server (default: push)")
    _p.add_argument("--mac", default=MAC_RECEIVER_URL,
                     help="Mac receiver URL (default: %s)" % MAC_RECEIVER_URL)
    _p.add_argument("--port", type=int, default=SERVE_PORT,
                     help="Serve mode listen port (default: %d)" % SERVE_PORT)
    _p.add_argument("--account", default="",
                     help="Trading account ID (overrides TRADING_ACCOUNT_ID env)")
    _args, _unknown = _p.parse_known_args()

    if _args.account:
        TRADING_ACCOUNT_ID = _args.account

    _init_log_file()
    _log("========================================")
    _log("Data Bridge v%d (legacy %s mode)" % (BRIDGE_VERSION, _args.mode))
    _log("========================================")

    _xtquant_userdata = _setup_xtquant_path()
    if _xtquant_userdata:
        _log("QMT userdata: %s" % _xtquant_userdata)

    if _args.mode == "serve":
        # Start trading thread alongside serve mode
        _t = threading.Thread(target=_trading_poll_loop, daemon=True)
        _t.start()
        _log("trading poll thread started")
        run_serve_mode(port=_args.port)

    else:  # push
        MAC_RECEIVER_URL = _args.mac
        _log("Mac receiver: %s" % MAC_RECEIVER_URL)

        try:
            _ensure_xtdata_connected()
        except Exception as e:
            _log("xtdata init skipped: %s" % e)

        if not _xt_connected:
            _log("WARNING: xtdata not connected")
            _log("TIP: Ensure QMT client is running, or use BRIDGE_MINI.py")

        # Start trading thread
        _t = threading.Thread(target=_trading_poll_loop, daemon=True)
        _t.start()
        _log("trading poll thread started")
        _log("Push loop starting (Ctrl+C to stop)...")

        try:
            while True:
                try:
                    _poll_and_push()
                except Exception as e:
                    _log("push error: %s" % e)
                time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            _log("stopped")
