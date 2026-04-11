# coding: utf-8
"""
qmt_data_bridge.py -- Windows QMT Unified Bridge
================================================

Unified bridge for miniQMT: market data (K-lines + quotes) AND trading.
Supersedes BRIDGE_MINI.py -- use this file for all miniQMT operations.

Starts HTTP server on Windows (default 0.0.0.0:58088), Mac directly
POSTs requests to pull data and execute trades.

Usage:
  Data + Trading (default):
    python qmt_data_bridge.py --account YOUR_ACCOUNT_ID

  Data only (disable trading):
    python qmt_data_bridge.py --no-trading

  Custom host/port:
    python qmt_data_bridge.py --host 0.0.0.0 --port 58088

API:
  GET  /health           -> {"status": "ok", "xtdata": true, "trading": true}
  GET  /trading/events    -> {"events": [...], "count": N}
  POST /klines            body: {"codes": [...], "period": "1d", "count": 120}
  POST /quote             body: {"codes": [...]}
  POST /trading           body: {"method": "place_order", "params": {...}}
  POST /sector_list       body: {"sector_type": "industry"|"concept"|""}
  POST /sector_members    body: {"sector": "电力设备"} -> {"data": ["600XXX", ...]}

Compatible with Python 3.6+ (QMT built-in Python).
"""

BRIDGE_VERSION = 3

import glob
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime

# ========== 配置（根据实际环境修改）==========

# HTTP 服务配置
SERVE_HOST = "0.0.0.0"
SERVE_PORT = 58088

# QMT 安装路径（环境变量 QMT_PATH 或 --qmt-path 指定）
QMT_PATH = os.environ.get("QMT_PATH", "")

# miniQMT userdata 路径（用于 XtQuantTrader 交易连接）
MINI_QMT_PATH = os.environ.get("MINI_QMT_PATH", "")

# 交易账户
TRADING_ACCOUNT_ID = os.environ.get("TRADING_ACCOUNT_ID", "")
TRADING_ACCOUNT_TYPE = os.environ.get("TRADING_ACCOUNT_TYPE", "STOCK")


# ========== 日志 ==========

_LOG_FILE = None


def _log(msg):
    """Print with flush and also write to log file."""
    line = "[%s] [data-bridge] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        print(line, file=sys.stderr, flush=True)
    except TypeError:
        print(line, file=sys.stderr)
        try:
            sys.stderr.flush()
        except Exception:
            pass
    if _LOG_FILE:
        try:
            with open(_LOG_FILE, "a") as f:
                f.write("%s\n" % line)
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
    candidates.append(os.path.join(os.environ.get("TEMP", "C:\\Temp")))
    candidates.append(os.getcwd())
    for log_dir in candidates:
        try:
            path = os.path.join(log_dir, "QMT_DATA_BRIDGE_DEBUG.log")
            with open(path, "w") as f:
                f.write("=== qmt_data_bridge v%d started %s ===\n"
                        % (BRIDGE_VERSION, time.strftime("%Y-%m-%d %H:%M:%S")))
            _LOG_FILE = path
            _log("debug log: %s" % _LOG_FILE)
            return
        except Exception:
            continue
    _LOG_FILE = None


def _find_qmt_path():
    """自动搜索 QMT 安装路径。优先使用 QMT_PATH 环境变量。"""
    if QMT_PATH:
        return QMT_PATH

    # 常见 QMT 安装路径模式
    search_patterns = [
        r"D:\st_lc",           # 用户自定义安装路径
        r"C:\*QMT*",
        r"D:\*QMT*",
        r"E:\*QMT*",
        r"C:\*迅投*",
        r"D:\*迅投*",
        r"E:\*迅投*",
        r"C:\Program Files\*QMT*",
        r"D:\Program Files\*QMT*",
        r"C:\Program Files (x86)\*QMT*",
    ]
    for pattern in search_patterns:
        matches = glob.glob(pattern)
        for m in matches:
            site_pkg = os.path.join(m, "bin.x64", "Lib", "site-packages")
            if os.path.isdir(site_pkg) and os.path.isdir(
                os.path.join(site_pkg, "xtquant")
            ):
                _log("自动发现 QMT: %s" % m)
                return m
    return ""


def _setup_xtquant_path(qmt_path=""):
    """确保 xtquant 可以被 import，返回 userdata_mini 路径（用于 connect）。"""
    path = qmt_path or _find_qmt_path()
    if not path:
        return ""  # 依赖系统已安装 xtquant

    # 添加 site-packages 到 sys.path
    site_pkg = os.path.join(path, "bin.x64", "Lib", "site-packages")
    if os.path.isdir(site_pkg) and site_pkg not in sys.path:
        sys.path.insert(0, site_pkg)
        _log("已添加 xtquant 路径: %s" % site_pkg)

    # 返回 userdata_mini 路径
    userdata = os.path.join(path, "userdata_mini")
    if os.path.isdir(userdata):
        return userdata
    # 也试下不带 _mini 后缀
    userdata2 = os.path.join(path, "userdata")
    if os.path.isdir(userdata2):
        return userdata2
    return ""


# 全局 userdata 路径，脚本启动时初始化
_xtquant_userdata = ""

# Once-only guard for download_sector_data() -- slow, should only run once
_sector_data_downloaded = False


def _xtdata_connect(xtdata):
    """Safely call xtdata.connect() -- handles multiple xtquant versions.

    Version differences:
      - QMT built-in: connect(userdata_path) accepts a path argument
      - Some pip versions: connect() takes no arguments
      - Some pip versions: no connect() at all (works without it)
      - Some pip versions: get_client().connect()

    Returns:
        (ok: bool, error: str) -- True if connected or connect not needed.
    """
    if hasattr(xtdata, "connect"):
        # Try with userdata path first (QMT built-in version)
        if _xtquant_userdata:
            try:
                xtdata.connect(_xtquant_userdata)
                return True, ""
            except TypeError:
                pass  # This version doesn't accept path argument, try without
            except Exception as e:
                _log("xtdata.connect(path) warning: %s" % e)
                # Fall through to try without path
        # Try without arguments
        try:
            xtdata.connect()
            return True, ""
        except Exception as e:
            _log("xtdata.connect() warning: %s" % e)
            return False, str(e)
    # Fallback: some pip versions expose connect via get_client()
    if hasattr(xtdata, "get_client"):
        try:
            client = xtdata.get_client()
            if client and hasattr(client, "connect"):
                if _xtquant_userdata:
                    try:
                        client.connect(_xtquant_userdata)
                        return True, ""
                    except TypeError:
                        pass
                client.connect()
                return True, ""
        except Exception as e:
            _log("xtdata.get_client().connect() warning: %s" % e)
            return False, str(e)
    # No connect method at all -- pip version works without it
    return True, ""


def _normalize_to_qmt(code):
    """Normalize a stock code to QMT format (e.g. '600519' -> '600519.SH')."""
    code = str(code).strip()
    if "." in code:
        return code
    if code.startswith("6"):
        return code + ".SH"
    if code.startswith(("0", "3")):
        return code + ".SZ"
    if code.startswith(("4", "8", "9")):
        return code + ".BJ"
    return code + ".SH"


def _aggregate_weekly_bars(daily_bars):
    """Aggregate sorted daily bars into weekly bars (ISO week)."""
    if not daily_bars:
        return []

    weekly = []
    current_key = None
    current = None
    for bar in daily_bars:
        try:
            dt = datetime.strptime(bar.get("date", ""), "%Y%m%d")
            week_key = dt.isocalendar()[:2]
        except Exception:
            continue

        if current_key != week_key:
            if current:
                weekly.append(current)
            current_key = week_key
            current = {
                "date": bar["date"],
                "open": bar["open"],
                "high": bar["high"],
                "low": bar["low"],
                "close": bar["close"],
                "volume": bar["volume"],
            }
            continue

        current["date"] = bar["date"]
        current["high"] = max(current["high"], bar["high"])
        current["low"] = min(current["low"], bar["low"])
        current["close"] = bar["close"]
        current["volume"] += bar["volume"]

    if current:
        weekly.append(current)
    return weekly


def _fetch_sector_list_xtdata(sector_type=""):
    """
    Fetch all sector (板块) names from xtdata.

    Args:
        sector_type: optional filter — "industry", "concept", "region", or ""
                     for all. Maps to xtdata sector_type strings.

    Returns:
        list of sector name strings, e.g. ["电力设备", "半导体", ...]
    """
    try:
        import xtquant.xtdata as xtdata
    except ImportError:
        return []

    ok, err = _xtdata_connect(xtdata)
    if not ok:
        _log("xtdata connect failed, cannot fetch sector_list: %s" % err)
        return []

    # Ensure sector data is downloaded/cached locally (once per session)
    global _sector_data_downloaded
    if not _sector_data_downloaded:
        try:
            if hasattr(xtdata, "download_sector_data"):
                xtdata.download_sector_data()
                _log("download_sector_data() done")
        except Exception as e:
            _log("download_sector_data() warning: %s" % e)
        _sector_data_downloaded = True

    # Different xtquant/QMT builds use different sector type keys.
    _XT_SECTOR_TYPE_CANDIDATES = {
        "industry": ["thshy", "行业板块", "证监会行业板块", "申万行业", "中信行业"],
        "concept": ["thsgn", "概念板块", "同花顺概念", "概念"],
        "region": ["thsdy", "地域板块", "同花顺地域", "地域"],
    }

    st_key = str(sector_type or "").strip().lower()

    try:
        # Explicit type: try multiple aliases and return first non-empty result.
        if st_key:
            candidates = _XT_SECTOR_TYPE_CANDIDATES.get(st_key, [sector_type])
            for st in candidates:
                try:
                    sectors = xtdata.get_sector_list(st) or []
                    if sectors:
                        _log("sector_list hit: type=%s alias=%s count=%d" % (sector_type, st, len(sectors)))
                        return sectors
                except Exception:
                    continue
            _log("sector_list empty: type=%s" % sector_type)
            return []

        # No type provided: aggregate across known families.
        all_sectors = []
        seen = set()
        for st in ("thshy", "thsgn", "thsdy", "行业板块", "概念板块", "地域板块"):
            try:
                sectors = xtdata.get_sector_list(st) or []
            except Exception:
                continue
            for name in sectors:
                if name in seen:
                    continue
                seen.add(name)
                all_sectors.append(name)
        return all_sectors
    except Exception as e:
        _log("get_sector_list failed: %s" % e)
        return []


def _normalize_stock_id(code):
    """Convert different stock code formats to 6-digit stock_id when possible."""
    s = str(code or "").strip()
    if not s:
        return ""
    if "." in s:
        s = s.split(".")[0]
    # Handle variants like SH600519 / SZ000001.
    if len(s) >= 8 and s[:2].isalpha() and s[-6:].isdigit():
        return s[-6:]
    if s.isdigit() and len(s) == 6:
        return s
    return ""


def _fetch_sector_members_xtdata(sector):
    """
    Fetch all stock codes in a sector via xtdata.get_stock_list_in_sector().

    Args:
        sector: sector name string, e.g. "电力设备"

    Returns:
        list of stock codes in QMT format, e.g. ["600519.SH", "000001.SZ", ...]
    """
    try:
        import xtquant.xtdata as xtdata
    except ImportError:
        return []

    ok, err = _xtdata_connect(xtdata)
    if not ok:
        _log("xtdata connect failed, cannot fetch sector_members: %s" % err)
        return []

    # Ensure sector data is downloaded/cached locally (once per session)
    global _sector_data_downloaded
    if not _sector_data_downloaded:
        try:
            if hasattr(xtdata, "download_sector_data"):
                xtdata.download_sector_data()
        except Exception as e:
            _log("download_sector_data() warning: %s" % e)
        _sector_data_downloaded = True

    try:
        codes = xtdata.get_stock_list_in_sector(sector)
        _log("get_stock_list_in_sector(%s) => %d codes" % (sector, len(codes or [])))
        return codes or []
    except Exception as e:
        _log("get_stock_list_in_sector(%s) failed: %s" % (sector, e))
        return []


def _fetch_klines_xtdata(codes, period, count):
    """
    用 xtdata.get_market_data_ex() 批量拉取 K 线。

    Args:
        codes:  QMT 格式代码列表，如 ["600519.SH", "000001.SZ"]
        period: K 线周期，"1d" / "5m" / "1m" 等
        count:  拉取最近 N 根 K 线

    Returns:
        dict 格式: {"600519": [{date, open, high, low, close, volume}, ...]}
        key 使用 6 位 stock_id（去掉 .SH/.SZ 后缀），与 screener_engine 保持一致。
    """
    try:
        import xtquant.xtdata as xtdata
    except ImportError:
        _log("xtquant 未安装。请用 QMT 自带的 Python 运行此脚本")
        _log("示例: D:\\st\\bin.x64\\python\\python.exe qmt_data_bridge.py --serve")
        return {}

    # 确保已连接 QMT 数据服务（独立运行时必须显式 connect）
    ok, err = _xtdata_connect(xtdata)
    if not ok:
        _log("xtdata connect failed, cannot fetch klines: %s" % err)
        return {}

    req_count = int(count)

    # xtquant 用 "1h" 表示 60 分钟线；周线用日线聚合兼容。
    period_lc = str(period).lower()
    use_weekly_agg = period_lc in ("1w", "w", "week", "1wk")
    _PERIOD_MAP = {"60m": "1h"}
    fetch_period = _PERIOD_MAP.get(period, period)
    if use_weekly_agg:
        fetch_period = "1d"
    fetch_count = max(req_count * 7, req_count + 20) if use_weekly_agg else req_count

    _log("拉取K线: %d 只, period=%s, count=%d" % (len(codes), period, count))

    # 确保本地有历史数据缓存（pip 版 xtquant 需要先 download）
    if hasattr(xtdata, "download_history_data"):
        for code in codes:
            try:
                xtdata.download_history_data(code, fetch_period, count=fetch_count)
            except TypeError:
                # QMT 内置版不支持 count 参数，回退到 (code, period, "", "")
                try:
                    xtdata.download_history_data(code, fetch_period, "", "")
                except Exception as e2:
                    _log("download_history_data(%s) warning: %s" % (code, e2))
            except Exception as e:
                _log("download_history_data(%s) warning: %s" % (code, e))

    try:
        raw = xtdata.get_market_data_ex(
            field_list=["open", "high", "low", "close", "volume"],
            stock_list=codes,
            period=fetch_period,
            count=fetch_count,
            dividend_type="front",  # 前复权
            fill_data=True,
        )
    except Exception as e:
        _log("xtdata.get_market_data_ex 失败: %s" % e)
        return {}

    if raw is None:
        _log("get_market_data_ex 返回 None (period=%s 可能不支持)" % period)
        return {}

    result = {}
    for qmt_code, df in raw.items():
        if df is None or (hasattr(df, "empty") and df.empty):
            continue

        # 6 位 stock_id（去 .SH/.SZ）
        stock_id = qmt_code.split(".")[0]

        bars = []
        try:
            for ts, row in df.iterrows():
                # ts 可能是 int (20260227) 或 str ("2026-02-27") 或 pandas Timestamp
                date_str = str(ts).replace("-", "")[:8]
                close_val = float(row.get("close", 0) or 0)
                if close_val <= 0:
                    continue  # 跳过无效 bar（停牌等）
                bars.append({
                    "date":   date_str,
                    "open":   float(row.get("open",   0) or 0),
                    "high":   float(row.get("high",   0) or 0),
                    "low":    float(row.get("low",    0) or 0),
                    "close":  close_val,
                    "volume": float(row.get("volume", 0) or 0),
                })
        except Exception as e:
            _log("%s 数据转换失败: %s" % (qmt_code, e))
            continue

        if use_weekly_agg:
            bars = _aggregate_weekly_bars(bars)
            if req_count > 0:
                bars = bars[-req_count:]

        if bars:
            result[stock_id] = bars

    _log("成功获取 %d/%d 只股票K线" % (len(result), len(codes)))
    return result


def _fetch_quotes_xtdata(codes):
    """
    Fetch real-time tick snapshots via xtdata.get_full_tick().

    Args:
        codes: QMT format codes, e.g. ["600519.SH", "000001.SZ"]

    Returns:
        dict: {"600519": {price, open, high, low, prevClose, volume, amount, bids, asks}, ...}
    """
    try:
        import xtquant.xtdata as xtdata
    except ImportError:
        return {}

    ok, err = _xtdata_connect(xtdata)
    if not ok:
        _log("xtdata connect failed, cannot fetch quotes: %s" % err)
        return {}

    result = {}
    for qmt_code in codes:
        stock_id = qmt_code.split(".")[0]
        try:
            ticks = xtdata.get_full_tick([qmt_code])
            tick = ticks.get(qmt_code)
            if tick is None:
                continue
            # get_full_tick returns: lastPrice, open, high, low, lastClose,
            # volume, amount, askPrice[5], askVol[5], bidPrice[5], bidVol[5]
            quote = {
                "price": float(tick.get("lastPrice", 0) or 0),
                "open": float(tick.get("open", 0) or 0),
                "high": float(tick.get("high", 0) or 0),
                "low": float(tick.get("low", 0) or 0),
                "prevClose": float(tick.get("lastClose", 0) or 0),
                "volume": int(tick.get("volume", 0) or 0),
                "amount": float(tick.get("amount", 0) or 0),
            }
            # Bid/Ask (5 levels)
            ask_prices = tick.get("askPrice", [])
            ask_vols = tick.get("askVol", [])
            bid_prices = tick.get("bidPrice", [])
            bid_vols = tick.get("bidVol", [])
            bids = []
            asks = []
            for i in range(min(5, len(bid_prices))):
                p = float(bid_prices[i]) if i < len(bid_prices) else 0
                v = int(bid_vols[i]) if i < len(bid_vols) else 0
                if p > 0:
                    bids.append({"price": p, "volume": v})
            for i in range(min(5, len(ask_prices))):
                p = float(ask_prices[i]) if i < len(ask_prices) else 0
                v = int(ask_vols[i]) if i < len(ask_vols) else 0
                if p > 0:
                    asks.append({"price": p, "volume": v})
            if bids:
                quote["bids"] = bids
            if asks:
                quote["asks"] = asks
            # Resolve stock name via get_instrument_detail
            try:
                detail = xtdata.get_instrument_detail(qmt_code)
                if detail and isinstance(detail, dict):
                    quote["name"] = detail.get("InstrumentName", "") or ""
            except Exception:
                pass
            if quote["price"] > 0:
                result[stock_id] = quote
        except Exception as e:
            _log("%s quote failed: %s" % (qmt_code, e))
            continue

    _log("got %d/%d quotes" % (len(result), len(codes)))
    return result


# ========== 交易桥 (XtQuantTrader, from BRIDGE_MINI) ==========

_trading_trader = None
_trading_account = None
_trading_connected = False
_trading_subscribed_codes = set()
_trading_events_queue = []
_trading_bg_retry_running = False  # background retry thread active


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


def _is_xtminiqmt_running():
    """Check if XtMiniQmt.exe process is running (fast, no side effects)."""
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq XtMiniQmt.exe", "/NH"],
            stderr=subprocess.DEVNULL,
        )
        return b"XtMiniQmt.exe" in out
    except Exception:
        return True  # If tasklist fails, assume running and let connect() decide


def _trading_connect(params):
    """Connect to miniQMT via XtQuantTrader for trading."""
    global _trading_trader, _trading_account, _trading_connected
    if _trading_connected and _trading_trader:
        return {"ok": True, "data": {"status": "already_connected", "account_id": TRADING_ACCOUNT_ID}}

    # Fast pre-check: skip heavy XtQuantTrader init if process isn't even running
    if not _is_xtminiqmt_running():
        return {"ok": False, "error": "XtMiniQmt.exe is not running"}

    try:
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount
    except ImportError:
        return {"ok": False, "error": "xtquant not installed"}

    account_id = params.get("account_id", "") or TRADING_ACCOUNT_ID
    account_type = params.get("account_type", "") or TRADING_ACCOUNT_TYPE
    qmt_path = params.get("qmt_userdata_path", "") or MINI_QMT_PATH or _xtquant_userdata

    if not qmt_path or not os.path.isdir(qmt_path):
        return {"ok": False, "error": "miniQMT userdata path not found: %s" % qmt_path}
    if not account_id:
        return {"ok": False, "error": "account_id not configured (set TRADING_ACCOUNT_ID)"}

    _log("trading: connecting, userdata=%s" % qmt_path)

    try:
        _trading_trader = XtQuantTrader(qmt_path, int(time.time()) % 10000)
        cb = _TradingCallback()
        _trading_trader.register_callback(cb)
        _trading_trader.start()

        # connect() blocks ~3s per call when QMT isn't ready.
        # Keep retries low so background thread can cycle faster.
        for _ in range(3):
            if _trading_trader.connect() == 0:
                break
            time.sleep(0.5)
        else:
            return {"ok": False, "error": "Failed to connect to miniQMT"}

        _trading_account = StockAccount(account_id, account_type)
        result = _trading_trader.subscribe(_trading_account)
        if result != 0:
            return {"ok": False, "error": "Failed to subscribe account %s (code=%d)" % (account_id, result)}

        _trading_connected = True
        _log("trading: connected, account=%s" % account_id)

        # Probe query APIs once so startup log can reveal signature/version mismatch early.
        probe_asset, probe_asset_err = _trading_call_compat([
            "query_stock_asset", "query_asset", "query_account", "query_stock_account"
        ])
        probe_pos, probe_pos_err = _trading_call_compat([
            "query_stock_positions", "query_positions", "query_position"
        ])
        if probe_asset is None:
            _log("trading probe account failed: %s" % probe_asset_err)
        else:
            _log("trading probe account ok")
        if probe_pos is None:
            _log("trading probe positions failed: %s" % probe_pos_err)
        else:
            _log("trading probe positions ok")

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
    stock_code = params.get("stock_code", "")
    try:
        result = _trading_trader.cancel_order_stock(_trading_account, order_id, stock_code)
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


def _serialize_qmt_position(p):
    def _pick(*names):
        for n in names:
            try:
                if hasattr(p, n):
                    return getattr(p, n)
            except Exception:
                continue
        return None

    def _to_float(v):
        try:
            return float(v or 0)
        except Exception:
            return 0.0

    def _to_int(v):
        try:
            return int(v or 0)
        except Exception:
            return 0

    stock_code = str(_pick("stock_code", "ticker", "code") or "")
    stock_name = str(_pick("stock_name", "name") or "")
    if not stock_name and stock_code:
        try:
            import xtquant.xtdata as xtdata
            detail = xtdata.get_instrument_detail(stock_code)
            if detail and isinstance(detail, dict):
                stock_name = detail.get("InstrumentName", "") or ""
        except Exception:
            pass
    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "volume": _to_int(_pick("volume", "total_volume", "total_amount", "position_volume")),
        "can_use_volume": _to_int(_pick("can_use_volume", "available_volume", "enable_amount", "can_use_amount")),
        "frozen_volume": _to_int(_pick("frozen_volume", "frozen_amount", "lock_volume")),
        "avg_price": _to_float(_pick("avg_price", "open_price", "cost_price", "position_avg_price")),
        "market_value": _to_float(_pick("market_value", "position_value", "mkt_val")),
    }


def _trading_account_candidates():
    """Return account arguments to try across xtquant API variants."""
    candidates = []
    if _trading_account is not None:
        candidates.append((_trading_account,))
    acc_id = ""
    try:
        acc_id = str(getattr(_trading_account, "account_id", "") or "")
    except Exception:
        acc_id = ""
    if not acc_id:
        acc_id = str(TRADING_ACCOUNT_ID or "")
    if acc_id:
        candidates.append((acc_id,))
        acc_type = ""
        try:
            acc_type = str(getattr(_trading_account, "account_type", "") or "")
        except Exception:
            acc_type = ""
        if acc_type:
            candidates.append((acc_id, acc_type))
    candidates.append(tuple())
    return candidates


def _trading_call_compat(method_names):
    """Try multiple method names and signatures for xtquant compatibility."""
    if not _trading_trader:
        return None, "Not connected"
    errors = []
    for name in method_names:
        fn = getattr(_trading_trader, name, None)
        if not callable(fn):
            continue
        for args in _trading_account_candidates():
            try:
                return fn(*args), ""
            except TypeError as e:
                errors.append("%s%s: %s" % (name, args, e))
                continue
            except Exception as e:
                errors.append("%s%s: %s" % (name, args, e))
                continue
    if errors:
        return None, "; ".join(errors)
    return None, "No compatible query method found: %s" % ", ".join(method_names)


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
        positions, err = _trading_call_compat([
            "query_stock_positions", "query_positions", "query_position"
        ])
        if positions is None:
            return {"ok": False, "error": "query_positions error: %s" % err}
        if isinstance(positions, dict):
            positions = positions.get("data", [])
        elif not isinstance(positions, list):
            positions = [positions]
        positions = positions or []
        result = [_serialize_qmt_position(p) for p in positions]
        return {"ok": True, "data": result}
    except Exception as e:
        return {"ok": False, "error": "query_positions error: %s" % str(e)}


def _trading_query_account(_params):
    if not _trading_trader or not _trading_account:
        return {"ok": False, "error": "Not connected"}
    try:
        assets, err = _trading_call_compat([
            "query_stock_asset", "query_asset", "query_account", "query_stock_account"
        ])
        if assets is None:
            return {"ok": False, "error": "query_account error: %s" % err}
        if isinstance(assets, list):
            assets = assets[0] if assets else None
        if isinstance(assets, dict):
            data = assets.get("data", assets)
            return {"ok": True, "data": {
                "account_id": str(data.get("account_id", getattr(_trading_account, "account_id", "")) or ""),
                "cash": float(data.get("cash", 0) or 0),
                "frozen_cash": float(data.get("frozen_cash", 0) or 0),
                "total_asset": float(data.get("total_asset", 0) or 0),
                "market_value": float(data.get("market_value", 0) or 0),
                "profit": float(data.get("profit", 0) or 0),
                "profit_ratio": float(data.get("profit_ratio", 0) or 0),
            }}
        if not assets:
            return {"ok": False, "error": "Failed to query account assets"}
        return {"ok": True, "data": {
            "account_id": getattr(_trading_account, "account_id", "") if _trading_account else "",
            "cash": float(getattr(assets, "cash", 0) or 0),
            "frozen_cash": float(getattr(assets, "frozen_cash", 0) or 0),
            "total_asset": float(getattr(assets, "total_asset", 0) or 0),
            "market_value": float(getattr(assets, "market_value", 0) or 0),
            "profit": float(getattr(assets, "profit", 0) or 0),
            "profit_ratio": float(getattr(assets, "profit_ratio", 0) or 0),
        }}
    except Exception as e:
        return {"ok": False, "error": "query_account error: %s" % str(e)}


def _on_quote_push(datas):
    """Callback for xtdata.subscribe_whole_quote — queues quote events for TS polling."""
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


def _trading_connect_background(interval=15, max_retries=0):
    """Background thread: retry trading connection until success.

    Args:
        interval: seconds between retries (default 15)
        max_retries: 0 = unlimited retries
    """
    global _trading_bg_retry_running
    _trading_bg_retry_running = True
    attempt = 0
    while not _trading_connected:
        attempt += 1
        if max_retries > 0 and attempt > max_retries:
            _log("trading bg: gave up after %d attempts" % max_retries)
            break
        _log("trading bg: attempt #%d (account=%s)..." % (attempt, TRADING_ACCOUNT_ID))
        try:
            result = _trading_connect({})
            if result.get("ok"):
                _log("trading bg: connected! %s" % json.dumps(result, ensure_ascii=False))
                break
            else:
                _log("trading bg: failed: %s -- retry in %ds" % (result.get("error", ""), interval))
        except Exception as e:
            _log("trading bg: exception: %s -- retry in %ds" % (e, interval))
        time.sleep(interval)
    _trading_bg_retry_running = False
    _log("trading bg: thread exiting (connected=%s)" % _trading_connected)


# ========== HTTP 服务 ==========

# Request stats — periodically printed to show bridge is alive
_req_stats = {"health": 0, "events": 0, "klines": 0, "quote": 0,
              "trading": 0, "sector": 0, "other": 0, "last_report": 0}
_REQ_STATS_INTERVAL = 60  # seconds between periodic status reports


def _bump_stat(key):
    _req_stats[key] = _req_stats.get(key, 0) + 1
    now = time.time()
    elapsed = now - _req_stats["last_report"]
    if elapsed >= _REQ_STATS_INTERVAL:
        _req_stats["last_report"] = now
        parts = []
        for k in ("health", "events", "klines", "quote", "trading", "sector", "other"):
            v = _req_stats.get(k, 0)
            if v > 0:
                parts.append("%s=%d" % (k, v))
        _log("[stats] 最近 %ds 请求: %s" % (int(elapsed), ", ".join(parts) if parts else "(无)"))
        for k in ("health", "events", "klines", "quote", "trading", "sector", "other"):
            _req_stats[k] = 0


def run_serve_mode(host=SERVE_HOST, port=SERVE_PORT):
    """
    启动 HTTP 服务器，Mac 可以直接拉取数据和执行交易。

    API:
      GET  /health
        -> {"status":"ok", "xtdata": true/false, "trading": true/false}

      GET  /trading/events
        -> {"events": [...], "count": N}
        Mac polls this to get async trading events (fills, order status).

      POST /klines
        body: {"codes": ["600519.SH", ...], "period": "1d", "count": 120}
        -> {"status":"ok", "data": {"600519": [...]}, "count": N}

      POST /quote
        body: {"codes": ["600519.SH", ...]}
        -> {"status":"ok", "data": {"600519": {...}}, "count": N}

      POST /trading
        body: {"method": "place_order", "params": {...}}
        -> {"ok": true, "data": {...}}
    """
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
        from socketserver import ThreadingMixIn
        from urllib.parse import urlparse
    except ImportError:
        from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
        from SocketServer import ThreadingMixIn
        from urlparse import urlparse

    class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    class BridgeHandler(BaseHTTPRequestHandler):
        def _cors_headers(self):
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

        def do_OPTIONS(self):
            self.send_response(204)
            self._cors_headers()
            self.end_headers()
        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/health":
                _bump_stat("health")
                xtdata_ok = False
                xtdata_err = ""
                try:
                    import xtquant.xtdata as xtdata
                except ImportError as e:
                    xtdata_err = "xtquant import failed: %s" % e
                else:
                    xtdata_ok, xtdata_err = _xtdata_connect(xtdata)
                trading_status = "connected" if _trading_connected else (
                    "connecting" if _trading_bg_retry_running else "disabled"
                )
                body = json.dumps({
                    "status": "ok" if xtdata_ok else "degraded",
                    "xtdata": xtdata_ok,
                    "trading": _trading_connected,
                    "trading_status": trading_status,
                    "version": BRIDGE_VERSION,
                    "error": xtdata_err or None,
                }).encode("utf-8")
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self._cors_headers()
                    self.end_headers()
                    self.wfile.write(body)
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass
            elif path == "/trading/events":
                _bump_stat("events")
                # Mac polls Windows for trading events (fills, order status)
                global _trading_events_queue
                events = _trading_events_queue[:]
                _trading_events_queue = []
                body = json.dumps({
                    "events": events,
                    "count": len(events),
                }).encode("utf-8")
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self._cors_headers()
                    self.end_headers()
                    self.wfile.write(body)
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    # Client disconnected; re-queue events so they aren't lost
                    _trading_events_queue = events + _trading_events_queue
                    return
            elif path == "/debug/klines":
                # Diagnostic: raw xtdata output for 600519.SH
                diag = {"error": None}
                try:
                    import xtquant.xtdata as xtdata
                    _xtdata_connect(xtdata)
                    diag["has_download"] = hasattr(xtdata, "download_history_data")
                    diag["has_get_market_data_ex"] = hasattr(xtdata, "get_market_data_ex")
                    diag["has_get_market_data"] = hasattr(xtdata, "get_market_data")
                    diag["has_get_local_data"] = hasattr(xtdata, "get_local_data")
                    # Try download first
                    if hasattr(xtdata, "download_history_data"):
                        try:
                            dl_res = xtdata.download_history_data("600519.SH", "1d", count=5)
                            diag["download_result"] = str(dl_res)
                        except Exception as e:
                            diag["download_error"] = str(e)
                    # Try get_market_data_ex
                    try:
                        raw = xtdata.get_market_data_ex(
                            field_list=["open", "high", "low", "close", "volume"],
                            stock_list=["600519.SH"],
                            period="1d", count=5,
                            dividend_type="front", fill_data=True)
                        diag["raw_type"] = str(type(raw))
                        diag["raw_keys"] = list(raw.keys()) if isinstance(raw, dict) else str(raw)[:500]
                        for k, v in raw.items():
                            diag["df_type_%s" % k] = str(type(v))
                            diag["df_len_%s" % k] = len(v) if v is not None else 0
                            if hasattr(v, "to_string"):
                                diag["df_str_%s" % k] = v.to_string()[:1000]
                            elif hasattr(v, "head"):
                                diag["df_head_%s" % k] = str(v.head())[:1000]
                            else:
                                diag["df_repr_%s" % k] = repr(v)[:1000]
                    except Exception as e:
                        diag["get_market_data_ex_error"] = str(e)
                        import traceback
                        diag["traceback"] = traceback.format_exc()[:2000]
                except Exception as e:
                    diag["error"] = str(e)
                body = json.dumps(diag, ensure_ascii=False, default=str).encode("utf-8")
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self._cors_headers()
                    self.end_headers()
                    self.wfile.write(body)
                except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                    pass
            else:
                self.send_error(404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path not in ("/klines", "/quote", "/trading", "/sector_list", "/sector_members"):
                self.send_error(404)
                return

            content_len = int(self.headers.get("Content-Length", 0))
            if content_len == 0:
                self._json_response(400, {"error": "empty request body"})
                return

            try:
                raw = self.rfile.read(content_len)
                req_body = json.loads(raw.decode("utf-8"))
            except Exception as e:
                self._json_response(400, {"error": "JSON parse failed: %s" % e})
                return

            # --- Trading endpoint ---
            if path == "/trading":
                _bump_stat("trading")
                method = req_body.get("method", "")
                params = req_body.get("params", {})
                if not method:
                    self._json_response(400, {"error": "method required"})
                    return
                handler = _TRADING_DISPATCH.get(method)
                if not handler:
                    self._json_response(400, {"error": "unknown method: %s" % method})
                    return
                try:
                    result = handler(params)
                except Exception as e:
                    result = {"ok": False, "error": "handler exception: %s" % str(e)}
                self._json_response(200, result)
                if result.get("ok"):
                    _log("[serve] trading %s => ok=True" % method)
                else:
                    _log("[serve] trading %s => ok=False, error=%s" % (method, result.get("error", "")))
                return

            # --- Sector endpoints ---
            if path == "/sector_list":
                _bump_stat("sector")
                sector_type = req_body.get("sector_type", "")
                sectors = _fetch_sector_list_xtdata(sector_type)
                self._json_response(200, {"status": "ok", "data": sectors, "count": len(sectors)})
                _log("[serve] returned %d sectors (type=%s)" % (len(sectors), sector_type))
                return

            if path == "/sector_members":
                _bump_stat("sector")
                sector = req_body.get("sector", "")
                if not sector:
                    self._json_response(400, {"error": "sector name required"})
                    return
                codes_raw = _fetch_sector_members_xtdata(sector)
                # Return normalized 6-digit stock_id codes.
                members = []
                seen = set()
                for c in (codes_raw or []):
                    sid = _normalize_stock_id(c)
                    if not sid or sid in seen:
                        continue
                    seen.add(sid)
                    members.append(sid)
                self._json_response(200, {"status": "ok", "data": members, "count": len(members), "sector": sector})
                _log("[serve] returned %d members for sector '%s'" % (len(members), sector))
                return

            # --- Data endpoints ---
            codes = req_body.get("codes", [])
            if not codes or not isinstance(codes, list):
                self._json_response(400, {"error": "codes must not be empty"})
                return

            if path == "/quote":
                _bump_stat("quote")
                quotes = _fetch_quotes_xtdata(codes)
                self._json_response(200, {"status": "ok", "data": quotes, "count": len(quotes)})
                _log("[serve] returned %d quotes" % len(quotes))
                return

            period = req_body.get("period", "1d")
            count = int(req_body.get("count", 120))
            _bump_stat("klines")
            _log("[serve] klines request: %d stocks, period=%s, count=%d" % (len(codes), period, count))

            klines = _fetch_klines_xtdata(codes, period, count)
            self._json_response(200, {"status": "ok", "data": klines, "count": len(klines)})
            _log("[serve] returned %d stocks klines" % len(klines))

        def _json_response(self, code, obj):
            body = json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8")
            try:
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self._cors_headers()
                self.end_headers()
                self.wfile.write(body)
            except (ConnectionAbortedError, ConnectionResetError, BrokenPipeError):
                pass  # client disconnected mid-response

        def log_message(self, fmt, *args):
            if len(args) > 0 and "/health" in str(args[0]):
                return
            # Suppress high-frequency long-poll access logs; keep errors/non-200 visible.
            if len(args) > 1 and "/trading/events" in str(args[0]) and str(args[1]) == "200":
                return
            _log("[serve] " + (fmt % args))

    server = ThreadingHTTPServer((host, port), BridgeHandler)
    _req_stats["last_report"] = time.time()
    _log("HTTP serve mode started -- http://%s:%d (threaded)" % (host, port))
    _log("Endpoints: GET /health /trading/events, POST /klines /quote /trading")
    _trading_label = "connected" if _trading_connected else (
        "connecting (background)" if _trading_bg_retry_running else "disabled"
    )
    _log("Trading: %s" % _trading_label)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        _log("HTTP serve stopped")
        server.shutdown()
        server.server_close()


# ========== 脚本入口 ==========

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="QMT Unified Bridge v%d -- 数据 + 交易 HTTP 服务" % BRIDGE_VERSION,
        epilog="""
Examples:
  Data + Trading (default):
    python qmt_data_bridge.py --account YOUR_ACCOUNT_ID

  Data only (disable trading):
    python qmt_data_bridge.py --no-trading

  Custom host/port:
    python qmt_data_bridge.py --host 0.0.0.0 --port 58088
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--host",
        default=SERVE_HOST,
        help="HTTP 服务监听地址 (默认: %s)" % SERVE_HOST,
    )
    parser.add_argument(
        "--port",
        type=int,
        default=SERVE_PORT,
        help="HTTP 服务端口 (默认: %d)" % SERVE_PORT,
    )
    parser.add_argument(
        "--qmt-path",
        default="",
        help="QMT 安装路径 (也可通过 QMT_PATH 环境变量设置)",
    )
    parser.add_argument(
        "--mini-path",
        default="",
        help="miniQMT userdata 路径 (也可通过 MINI_QMT_PATH 环境变量设置)",
    )
    parser.add_argument(
        "--no-trading",
        action="store_true",
        help="禁用交易功能 (默认启用，需要 XtMiniQmt.exe 运行中)",
    )
    parser.add_argument(
        "--account",
        default="",
        help="交易账户 ID (也可通过 TRADING_ACCOUNT_ID 环境变量设置)",
    )
    args = parser.parse_args()

    # 覆盖全局配置
    if args.account:
        TRADING_ACCOUNT_ID = args.account
    if args.mini_path:
        MINI_QMT_PATH = args.mini_path

    _init_log_file()

    # 初始化 xtquant 路径
    _xtquant_userdata = _setup_xtquant_path(args.qmt_path)
    if _xtquant_userdata:
        _log("QMT userdata: %s" % _xtquant_userdata)
    else:
        _log("未找到 QMT 安装路径，将使用默认 xtdata.connect()")

    # 初始化交易连接（后台重试，不阻塞 HTTP 服务启动）
    if not args.no_trading:
        if TRADING_ACCOUNT_ID:
            _log("trading will connect in background (account=%s)" % TRADING_ACCOUNT_ID)
            t = threading.Thread(
                target=_trading_connect_background,
                kwargs={"interval": 5, "max_retries": 0},
                daemon=True,
            )
            t.start()
        else:
            _log("trading enabled but no account ID set (use --account or TRADING_ACCOUNT_ID)")

    run_serve_mode(host=args.host, port=args.port)