"""QMT 数据桥接 HTTP 服务 — 在 Windows 上运行，暴露 K 线/行情/板块/交易接口。

在 miniQMT 运行的 Windows 机器上启动，通过 HTTP 提供数据和交易服务，
远端（Mac/Linux）可通过 ``XTDataProvider`` 或 ``RemoteQMTBroker`` 访问。

端点:
  GET  /health          → 服务状态
  GET  /trading/events  → 交易事件（成交/委托状态推送）
  GET  /debug/klines    → 诊断：xtdata 原始输出
  POST /klines          → K 线数据（支持周线聚合、前复权）
  POST /quote           → 实时行情（含股票名称、五档盘口）
  POST /tick_download   → 历史 tick 数据
  POST /sector_list     → 板块列表（多版本兼容）
  POST /sector_members  → 板块成员
  POST /trading         → 交易指令（下单/撤单/查询/保存策略文件等）

启动:
  python -m aipha.execution.qmt_data_bridge --port 18888
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from typing import Any

logger = logging.getLogger(__name__)

BRIDGE_VERSION = 3
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 18888

# 环境变量配置
MINI_QMT_PATH = os.environ.get("MINI_QMT_PATH", "")
TRADING_ACCOUNT_ID = os.environ.get("TRADING_ACCOUNT_ID", "")
TRADING_ACCOUNT_TYPE = os.environ.get("TRADING_ACCOUNT_TYPE", "STOCK")

# xtquant 可选导入
try:
    from xtquant import xtdata  # type: ignore[import-untyped]
    xtdata.enable_hello = False
    HAS_XTDATA = True
except ImportError:
    xtdata = None  # type: ignore[assignment]
    HAS_XTDATA = False

# 板块成员缓存（避免频繁查询）
_sector_cache: dict[str, tuple[float, list[str]]] = {}
_CACHE_TTL = 300  # 5 分钟

# Once-only guard for download_sector_data()
_sector_data_downloaded = False

# Global userdata path for QMT (set at startup if --mini-path provided)
_xtquant_userdata = ""


def _xtdata_connect() -> tuple[bool, str]:
    """Safely connect xtdata — handles multiple xtquant versions."""
    if not HAS_XTDATA:
        return False, "xtdata not available"
    if hasattr(xtdata, "connect"):
        # xtdata.connect() expects an optional server IP, not a userdata path.
        # The userdata directory is only used by the trading client below.
        try:
            xtdata.connect()
            return True, ""
        except Exception as e:
            logger.warning(
                "xtdata 行情连接失败: %s。该连接独立于交易连接；请确认 QMT 行情服务已启动，"
                "并检查本机 TCP 58610 是否处于 LISTENING 状态（netstat -ano | findstr :58610）",
                e,
            )
            return False, str(e)
    if hasattr(xtdata, "get_client"):
        try:
            client = xtdata.get_client()
            if client and hasattr(client, "connect"):
                client.connect()
                return True, ""
        except Exception as e:
            logger.warning("xtdata.get_client().connect(): %s", e)
            return False, str(e)
    return True, ""


def _normalize_to_qmt(code: str) -> str:
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
    return (code + ".SH") if n >= 500_000 else (code + ".SZ")


def _normalize_stock_id(code: str) -> str:
    """Convert different stock code formats to 6-digit stock_id."""
    s = str(code or "").strip()
    if not s:
        return ""
    if "." in s:
        s = s.split(".")[0]
    if len(s) >= 8 and s[:2].isalpha() and s[-6:].isdigit():
        return s[-6:]
    if s.isdigit() and len(s) == 6:
        return s
    return ""


def _aggregate_weekly_bars(daily_bars: list[dict]) -> list[dict]:
    """Aggregate sorted daily bars into weekly bars (ISO week)."""
    if not daily_bars:
        return []
    weekly: list[dict] = []
    current_key = None
    current: dict | None = None
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
        if current is not None:
            current["date"] = bar["date"]
            current["high"] = max(current["high"], bar["high"])
            current["low"] = min(current["low"], bar["low"])
            current["close"] = bar["close"]
            current["volume"] += bar["volume"]
    if current:
        weekly.append(current)
    return weekly


def _json_response(handler: BaseHTTPRequestHandler, data: Any, status: int = 200) -> None:
    """发送 JSON 响应。"""
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Access-Control-Allow-Origin", "*")
        handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        handler.send_header("Access-Control-Allow-Headers", "Content-Type")
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionError, OSError):
        logger.debug("客户端已断开，响应未发送 (status=%d)", status)


def _read_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    """读取并解析请求体 JSON。"""
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    return json.loads(raw)


# ========== 交易桥 (XtQuantTrader) ==========

_trading_trader: Any = None
_trading_account: Any = None
_trading_connected = False
_require_execution_plan = False
_trading_subscribed_codes: set[str] = set()
_trading_events_queue: list[dict] = []
_trading_bg_retry_running = False


class _TradingCallback:
    """Receives async push notifications from QMT and queues for polling."""

    def on_disconnected(self) -> None:
        _trading_events_queue.append({"event": "disconnected", "data": {}})

    def on_stock_order(self, order: Any) -> None:
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
            "strategy_error_msg": getattr(order, "strategy_error_msg", "") or "",
            "order_time": getattr(order, "order_time", "") or "",
        }})

    def on_stock_trade(self, trade: Any) -> None:
        value = trade.traded_price * trade.traded_volume
        commission = round(value * 0.0005, 4)
        _trading_events_queue.append({"event": "fill", "data": {
            "order_id": str(trade.order_id),
            "stock_code": trade.stock_code,
            "traded_price": trade.traded_price,
            "traded_volume": trade.traded_volume,
            "traded_amount": value,
            "commission": commission,
            "time": getattr(trade, "traded_time", "") or "",
        }})

    def on_order_error(self, order_error: Any) -> None:
        _trading_events_queue.append({"event": "order_status", "data": {
            "order_id": str(order_error.order_id),
            "stock_code": order_error.stock_code,
            "order_status": 58,
            "strategy_error_msg": order_error.error_msg or "unknown error",
        }})


def _is_xtminiqmt_running() -> bool:
    try:
        out = subprocess.check_output(
            ["tasklist", "/FI", "IMAGENAME eq XtMiniQmt.exe", "/NH"],
            stderr=subprocess.DEVNULL,
        )
        return b"XtMiniQmt.exe" in out
    except Exception:
        return True


def _trading_account_candidates() -> list[tuple]:
    """Return account arguments to try across xtquant API variants."""
    candidates: list[tuple] = []
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
    candidates.append(())
    return candidates


def _trading_call_compat(method_names: list[str]) -> tuple[Any, str]:
    """Try multiple method names and signatures for xtquant compatibility."""
    if not _trading_trader:
        return None, "Not connected"
    errors: list[str] = []
    for name in method_names:
        fn = getattr(_trading_trader, name, None)
        if not callable(fn):
            continue
        for args in _trading_account_candidates():
            try:
                return fn(*args), ""
            except (TypeError, Exception) as e:
                errors.append(f"{name}{args}: {e}")
    if errors:
        return None, "; ".join(errors)
    return None, "No compatible query method found: %s" % ", ".join(method_names)


def _trading_connect(params: dict) -> dict:
    global _trading_trader, _trading_account, _trading_connected, _require_execution_plan
    _require_execution_plan = bool(params.get("require_execution_plan", False))
    if _trading_connected and _trading_trader:
        return {"ok": True, "data": {"status": "already_connected", "account_id": TRADING_ACCOUNT_ID}}

    if not _is_xtminiqmt_running():
        return {"ok": False, "error": "XtMiniQmt.exe is not running"}

    try:
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount
    except ImportError:
        return {"ok": False, "error": "xtquant not installed"}

    account_id = params.get("account_id", "") or TRADING_ACCOUNT_ID
    account_type = params.get("account_type", "") or TRADING_ACCOUNT_TYPE
    qmt_path = params.get("mini_qmt_path", "") or params.get("qmt_userdata_path", "") or MINI_QMT_PATH

    if not qmt_path or not os.path.isdir(qmt_path):
        return {"ok": False, "error": "miniQMT userdata path not found: %s" % qmt_path}
    if not account_id:
        return {"ok": False, "error": "account_id not configured (set TRADING_ACCOUNT_ID)"}

    logger.info("trading: connecting, userdata=%s, account=%s", qmt_path, account_id)

    try:
        _trading_trader = XtQuantTrader(qmt_path, int(time.time()) % 10000)
        cb = _TradingCallback()
        _trading_trader.register_callback(cb)
        _trading_trader.start()

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
        logger.info("trading: connected, account=%s", account_id)

        # Probe query APIs
        probe_asset, probe_err = _trading_call_compat([
            "query_stock_asset", "query_asset", "query_account", "query_stock_account"
        ])
        logger.info("trading probe account: %s", "ok" if probe_asset is not None else probe_err)

        return {"ok": True, "data": {"status": "connected", "account_id": account_id}}
    except Exception as e:
        return {"ok": False, "error": "connect failed: %s" % str(e)}


def _trading_disconnect(_params: dict) -> dict:
    global _trading_trader, _trading_account, _trading_connected, _require_execution_plan
    if _trading_trader:
        try:
            _trading_trader.stop()
        except Exception:
            pass
        _trading_trader = None
    _trading_account = None
    _trading_connected = False
    _require_execution_plan = False
    logger.info("trading: disconnected")
    return {"ok": True, "data": {"status": "disconnected"}}


def _trading_place_order(params: dict) -> dict:
    if not _trading_trader or not _trading_account:
        return {"ok": False, "error": "Not connected"}
    # This HTTP endpoint receives a raw single-order payload and cannot verify
    # membership in the full target plan. Provenance strings are forgeable.
    # Keep the generic relay query-only; live orders go through QMTGateway's
    # submit_target_plan adapter after plan and risk validation.
    return {"ok": False, "error": "direct QMT orders are disabled; submit a validated execution plan"}
    stock_code = params.get("stock_code", "")
    order_type = int(params.get("order_type", 23))
    price_type = int(params.get("price_type", 11))
    price = float(params.get("price", 0))
    volume = int(params.get("volume", 0))
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
    try:
        order_id = _trading_trader.order_stock(
            _trading_account, stock_code, order_type, volume,
            price_type, price, strategy, remark)
        if order_id < 0:
            return {"ok": False, "error": "place_order failed, QMT returned %d" % order_id}
        logger.info("trading: placed order %s qty=%d price=%.2f => id=%s",
                     stock_code, volume, price, order_id)
        return {"ok": True, "data": {"broker_order_id": str(order_id)}}
    except Exception as e:
        return {"ok": False, "error": "place_order error: %s" % str(e)}


def _trading_cancel_order(params: dict) -> dict:
    if not _trading_trader or not _trading_account:
        return {"ok": False, "error": "Not connected"}
    order_id = int(params.get("order_id", 0))
    stock_code = params.get("stock_code", "")
    try:
        result = _trading_trader.cancel_order_stock(_trading_account, order_id, stock_code)
        return {"ok": True, "data": {"result": result}}
    except Exception as e:
        return {"ok": False, "error": "cancel_order error: %s" % str(e)}


def _serialize_qmt_order(o: Any) -> dict:
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
    }


def _serialize_qmt_position(p: Any) -> dict:
    def _pick(*names: str) -> Any:
        for n in names:
            try:
                if hasattr(p, n):
                    return getattr(p, n)
            except Exception:
                continue
        return None

    def _to_float(v: Any) -> float:
        try:
            return float(v or 0)
        except Exception:
            return 0.0

    def _to_int(v: Any) -> int:
        try:
            return int(v or 0)
        except Exception:
            return 0

    stock_code = str(_pick("stock_code", "ticker", "code") or "")
    stock_name = str(_pick("stock_name", "name") or "")
    if not stock_name and stock_code:
        try:
            from xtquant import xtdata as _xt  # type: ignore
            detail = _xt.get_instrument_detail(stock_code)
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


def _trading_query_order(params: dict) -> dict:
    if not _trading_trader or not _trading_account:
        return {"ok": False, "error": "Not connected"}
    order_id = int(params.get("order_id", 0))
    try:
        # SDK versions spell this argument differently; False requests all orders.
        orders = _trading_trader.query_stock_orders(_trading_account, False)
        for o in (orders or []):
            if o.order_id == order_id:
                return {"ok": True, "data": _serialize_qmt_order(o)}
        return {"ok": False, "error": "Order %d not found" % order_id}
    except Exception as e:
        return {"ok": False, "error": "query_order error: %s" % str(e)}


def _trading_query_orders(params: dict) -> dict:
    if not _trading_trader or not _trading_account:
        return {"ok": False, "error": "Not connected"}
    stock_code = params.get("stock_code")
    try:
        orders = _trading_trader.query_stock_orders(_trading_account, False) or []
        result = [_serialize_qmt_order(o) for o in orders]
        if stock_code:
            result = [o for o in result if o["stock_code"] == stock_code]
        return {"ok": True, "data": result}
    except Exception as e:
        return {"ok": False, "error": "query_orders error: %s" % str(e)}


def _trading_query_positions(_params: dict) -> dict:
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


def _trading_query_account(_params: dict) -> dict:
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


_quote_push_last: dict[str, float] = {}


def _on_quote_push(datas: dict) -> None:
    now = time.time()
    for code, bars in datas.items():
        if not bars:
            continue
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


def _trading_subscribe_quote(params: dict) -> dict:
    codes = params.get("stock_codes", [])
    new_codes = [c for c in codes if c not in _trading_subscribed_codes]
    if new_codes:
        try:
            from xtquant import xtdata as _xt  # type: ignore
            _xt.subscribe_whole_quote(new_codes, callback=_on_quote_push)
            _trading_subscribed_codes.update(new_codes)
        except Exception as e:
            return {"ok": False, "error": "subscribe_quote error: %s" % str(e)}
    return {"ok": True, "data": {"subscribed": list(_trading_subscribed_codes)}}


def _trading_unsubscribe_quote(params: dict) -> dict:
    codes = params.get("stock_codes", [])
    for c in codes:
        _trading_subscribed_codes.discard(c)
    return {"ok": True, "data": {"subscribed": list(_trading_subscribed_codes)}}


def _trading_save_strategy_file(params: dict) -> dict:
    """Save a strategy file to the MiniQMT strategies directory."""
    content = params.get("content", "")
    filename = params.get("filename", "")
    save_dir = params.get("save_dir", "")
    if not content or not filename:
        return {"ok": False, "error": "content and filename are required"}

    if not save_dir:
        base = MINI_QMT_PATH or _xtquant_userdata
        save_dir = os.path.join(base, "strategies") if base else ""
    if not save_dir:
        return {"ok": False, "error": "Cannot determine save directory (MINI_QMT_PATH not set)"}

    try:
        os.makedirs(save_dir, exist_ok=True)
        filepath = os.path.join(save_dir, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info("Saved strategy file: %s", filepath)
        return {"ok": True, "data": {"filepath": filepath, "filename": filename}}
    except Exception as e:
        return {"ok": False, "error": "Failed to save file: %s" % str(e)}


_TRADING_DISPATCH: dict[str, Any] = {
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


def _trading_connect_background(interval: int = 15, max_retries: int = 0) -> None:
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
            logger.info("trading bg: gave up after %d attempts", max_retries)
            break
        logger.info("trading bg: attempt #%d (account=%s)...", attempt, TRADING_ACCOUNT_ID)
        try:
            result = _trading_connect({})
            if result.get("ok"):
                logger.info("trading bg: connected!")
                break
            else:
                logger.info("trading bg: failed: %s -- retry in %ds", result.get("error", ""), interval)
        except Exception as e:
            logger.info("trading bg: exception: %s -- retry in %ds", e, interval)
        time.sleep(interval)
    _trading_bg_retry_running = False


class BridgeHandler(BaseHTTPRequestHandler):
    """QMT 数据 + 交易桥接 HTTP 处理器。"""

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ARG002
        logger.debug(fmt, *args)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/health":
            xtdata_ok = False
            xtdata_err = ""
            if HAS_XTDATA:
                xtdata_ok, xtdata_err = _xtdata_connect()
            else:
                xtdata_err = "xtquant not installed"
            trading_status = "connected" if _trading_connected else (
                "connecting" if _trading_bg_retry_running else "disabled"
            )
            _json_response(self, {
                "ok": xtdata_ok,
                "status": "ok" if xtdata_ok else "degraded",
                "version": BRIDGE_VERSION,
                "xtdata": xtdata_ok,
                "trading": _trading_connected,
                "trading_status": trading_status,
                "error": xtdata_err or None,
                "timestamp": datetime.now().isoformat(),
            })
        elif self.path == "/trading/events" or self.path.startswith("/trading/events?"):
            global _trading_events_queue
            events = _trading_events_queue[:]
            _trading_events_queue = []
            body = json.dumps({"events": events, "count": len(events)}, ensure_ascii=False).encode("utf-8")
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionError, OSError):
                _trading_events_queue = events + _trading_events_queue
        elif self.path == "/debug/klines":
            self._handle_debug_klines()
        else:
            _json_response(self, {"error": "not found"}, 404)

    def _handle_debug_klines(self) -> None:
        """Diagnostic: raw xtdata output for 600519.SH."""
        diag: dict[str, Any] = {"error": None}
        try:
            if not HAS_XTDATA:
                diag["error"] = "xtdata not available"
            else:
                _xtdata_connect()
                diag["has_download"] = hasattr(xtdata, "download_history_data")
                diag["has_get_market_data_ex"] = hasattr(xtdata, "get_market_data_ex")
                if hasattr(xtdata, "download_history_data"):
                    try:
                        dl_res = xtdata.download_history_data("600519.SH", "1d", count=5)
                        diag["download_result"] = str(dl_res)
                    except Exception as e:
                        diag["download_error"] = str(e)
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
                except Exception as e:
                    diag["get_market_data_ex_error"] = str(e)
        except Exception as e:
            diag["error"] = str(e)
        _json_response(self, diag)

    def do_POST(self) -> None:
        try:
            body = _read_body(self)
        except Exception as e:
            _json_response(self, {"error": f"invalid JSON: {e}"}, 400)
            return

        path = self.path
        try:
            if path == "/klines":
                result = _handle_klines(body)
            elif path == "/quote":
                result = _handle_quote(body)
            elif path == "/tick_download":
                result = _handle_tick_download(body)
            elif path == "/sector_list":
                result = _handle_sector_list(body)
            elif path == "/sector_members":
                result = _handle_sector_members(body)
            elif path == "/trading":
                self._handle_trading(body)
                return
            else:
                _json_response(self, {"error": "not found"}, 404)
                return
            _json_response(self, {"ok": True, "data": result})
        except Exception as e:
            logger.exception("处理 %s 异常", path)
            _json_response(self, {"ok": False, "error": str(e)}, 500)

    def _handle_trading(self, body: dict) -> None:
        method = body.get("method", "")
        params = body.get("params", {})
        if not method:
            _json_response(self, {"error": "method required"}, 400)
            return
        handler = _TRADING_DISPATCH.get(method)
        if not handler:
            _json_response(self, {"error": "unknown method: %s" % method}, 400)
            return
        try:
            result = handler(params)
        except Exception as e:
            result = {"ok": False, "error": "handler exception: %s" % str(e)}
        _json_response(self, result)
        if result.get("ok"):
            logger.info("[trading] %s => ok", method)
        else:
            logger.info("[trading] %s => error: %s", method, result.get("error", ""))


# ---------------------------------------------------------------------------
# 数据处理函数
# ---------------------------------------------------------------------------

def _handle_klines(body: dict[str, Any]) -> dict[str, Any]:
    """获取 K 线数据（支持周线聚合、前复权）。"""
    if not HAS_XTDATA:
        raise RuntimeError("xtdata 不可用")
    codes = body.get("codes", [])
    period = body.get("period", "1d")
    count = int(body.get("count", 120))
    if not codes:
        return {"bars": {}}

    _xtdata_connect()

    # 周线通过日线聚合实现
    period_lc = str(period).lower()
    use_weekly_agg = period_lc in ("1w", "w", "week", "1wk")
    _PERIOD_MAP = {"60m": "1h"}
    fetch_period = _PERIOD_MAP.get(period, period)
    if use_weekly_agg:
        fetch_period = "1d"
    fetch_count = max(count * 7, count + 20) if use_weekly_agg else count

    # 确保数据已下载
    if hasattr(xtdata, "download_history_data2"):
        import datetime as _dt
        _cal_days = fetch_count * 2 if fetch_period == "1d" else fetch_count
        _start = (_dt.datetime.now() - _dt.timedelta(days=_cal_days)).strftime("%Y%m%d")
        try:
            xtdata.download_history_data2(codes, period=fetch_period, start_time=_start)
        except Exception:
            pass
    elif hasattr(xtdata, "download_history_data"):
        for code in codes:
            try:
                xtdata.download_history_data(code, fetch_period, count=fetch_count)
            except TypeError:
                try:
                    xtdata.download_history_data(code, fetch_period, "", "")
                except Exception as e2:
                    logger.warning("download_history_data(%s) warning: %s", code, e2)
            except Exception as e:
                logger.warning("download_history_data(%s) warning: %s", code, e)

    result: dict[str, list[dict]] = {}
    for code in codes:
        try:
            raw = xtdata.get_market_data_ex(
                field_list=["open", "high", "low", "close", "volume", "amount"],
                stock_list=[code],
                period=fetch_period,
                count=fetch_count,
                dividend_type="front",
                fill_data=True,
            )
        except Exception as e:
            logger.warning("get_market_data_ex(%s) failed: %s", code, e)
            continue
        df = raw.get(code)
        if df is None or (hasattr(df, "empty") and df.empty):
            result[code] = []
            continue
        bars: list[dict] = []
        for idx, row in df.iterrows():
            date_str = str(idx).replace("-", "")[:8]
            close_val = float(row.get("close", 0) or 0)
            if close_val <= 0:
                continue
            bars.append({
                "date": date_str,
                "open": float(row.get("open", 0) or 0),
                "high": float(row.get("high", 0) or 0),
                "low": float(row.get("low", 0) or 0),
                "close": close_val,
                "volume": float(row.get("volume", 0) or 0),
                "amount": float(row.get("amount", 0) or 0),
            })
        if use_weekly_agg:
            bars = _aggregate_weekly_bars(bars)
            if count > 0:
                bars = bars[-count:]
        result[code] = bars
    return {"bars": result}


def _handle_quote(body: dict[str, Any]) -> dict[str, Any]:
    """获取实时行情（含股票名称、五档盘口）。"""
    if not HAS_XTDATA:
        raise RuntimeError("xtdata 不可用")
    codes = body.get("codes", [])
    if not codes:
        return {"quotes": {}}

    _xtdata_connect()
    raw = xtdata.get_full_tick(codes)
    quotes: dict[str, dict] = {}
    for code in codes:
        tick = raw.get(code)
        if not tick:
            continue
        quote: dict[str, Any] = {
            "lastPrice": float(tick.get("lastPrice", 0) or 0),
            "open": float(tick.get("open", 0) or 0),
            "high": float(tick.get("high", 0) or 0),
            "low": float(tick.get("low", 0) or 0),
            "lastClose": float(tick.get("lastClose", 0) or 0),
            "volume": int(tick.get("volume", 0) or 0),
            "amount": float(tick.get("amount", 0) or 0),
            "askPrice": [float(x) for x in tick.get("askPrice", [])],
            "bidPrice": [float(x) for x in tick.get("bidPrice", [])],
            "askVol": [int(x) for x in tick.get("askVol", [])],
            "bidVol": [int(x) for x in tick.get("bidVol", [])],
        }
        # Structured bids/asks (5 levels)
        ask_prices = tick.get("askPrice", [])
        ask_vols = tick.get("askVol", [])
        bid_prices = tick.get("bidPrice", [])
        bid_vols = tick.get("bidVol", [])
        bids, asks = [], []
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
        # Resolve stock name
        try:
            detail = xtdata.get_instrument_detail(code)
            if detail and isinstance(detail, dict):
                quote["name"] = detail.get("InstrumentName", "") or ""
        except Exception:
            pass
        if quote["lastPrice"] > 0:
            quotes[code] = quote
    return {"quotes": quotes}


def _handle_sector_list(body: dict[str, Any]) -> list[str]:
    """获取板块列表（支持多版本 xtquant 板块类型别名）。"""
    if not HAS_XTDATA:
        raise RuntimeError("xtdata 不可用")
    _xtdata_connect()

    global _sector_data_downloaded
    if not _sector_data_downloaded:
        try:
            if hasattr(xtdata, "download_sector_data"):
                xtdata.download_sector_data()
        except Exception as e:
            logger.warning("download_sector_data() warning: %s", e)
        _sector_data_downloaded = True

    _SECTOR_TYPE_CANDIDATES = {
        "industry": ["thshy", "行业板块", "证监会行业板块", "申万行业", "中信行业"],
        "concept": ["thsgn", "概念板块", "同花顺概念", "概念"],
        "region": ["thsdy", "地域板块", "同花顺地域", "地域"],
    }

    sector_type = str(body.get("sector_type", "") or "").strip().lower()

    if sector_type:
        candidates = _SECTOR_TYPE_CANDIDATES.get(sector_type, [sector_type])
        for st in candidates:
            try:
                sectors = xtdata.get_sector_list(st) or []
                if sectors:
                    return sectors
            except Exception:
                continue
        return []

    # No type: aggregate across known families
    all_sectors: list[str] = []
    seen: set[str] = set()
    for st in ("thshy", "thsgn", "thsdy", "行业板块", "概念板块", "地域板块"):
        try:
            sectors = xtdata.get_sector_list(st) or []
        except Exception:
            continue
        for name in sectors:
            if name not in seen:
                seen.add(name)
                all_sectors.append(name)
    return all_sectors


def _handle_sector_members(body: dict[str, Any]) -> list[str]:
    """获取板块成员（带缓存、download_sector_data 预加载）。"""
    if not HAS_XTDATA:
        raise RuntimeError("xtdata 不可用")
    sector = body.get("sector", "")
    if not sector:
        return []
    _xtdata_connect()

    global _sector_data_downloaded
    if not _sector_data_downloaded:
        try:
            if hasattr(xtdata, "download_sector_data"):
                xtdata.download_sector_data()
        except Exception as e:
            logger.warning("download_sector_data() warning: %s", e)
        _sector_data_downloaded = True

    # 检查缓存
    cached = _sector_cache.get(sector)
    if cached and (time.time() - cached[0]) < _CACHE_TTL:
        return cached[1]
    members = xtdata.get_stock_list_in_sector(sector) or []
    _sector_cache[sector] = (time.time(), members)
    return members


def _handle_tick_download(body: dict[str, Any]) -> dict[str, Any]:
    """下载指定日期的历史 tick 数据。

    请求体::

        {"codes": ["600519.SH"], "date": "20260424"}

    返回::

        {"ticks": {"600519.SH": [{timestamp, price, volume, ...}, ...]}, "count": 1}
    """
    if not HAS_XTDATA:
        raise RuntimeError("xtdata 不可用")
    codes = body.get("codes", [])
    trade_date = body.get("date", "")
    if not codes:
        return {"ticks": {}, "count": 0}
    if not trade_date:
        raise ValueError("date 参数必填 (YYYYMMDD)")
    return _fetch_tick_data(codes, trade_date)


def _fetch_tick_data(codes: list[str], trade_date: str) -> dict[str, Any]:
    """通过 xtdata 获取指定日期的历史 tick 数据。"""
    _xtdata_connect()
    result: dict[str, list[dict]] = {}
    for code in codes:
        ticks = _fetch_tick_single(code, trade_date)
        if ticks:
            result[code] = ticks
            logger.info("tick: got %d ticks for %s on %s", len(ticks), code, trade_date)
    logger.info("tick_download: got data for %d/%d stocks", len(result), len(codes))
    return {"ticks": result, "count": len(result)}


def _fetch_tick_single(code: str, trade_date: str) -> list[dict]:
    """获取单只标的在指定日期的 tick 数据。"""
    # 先确保本地有 tick 缓存
    if hasattr(xtdata, "download_history_data"):
        try:
            xtdata.download_history_data(
                code, "tick", start_time=trade_date, end_time=trade_date,
            )
        except TypeError:
            try:
                xtdata.download_history_data(code, "tick", trade_date, trade_date)
            except Exception as e:
                logger.warning("download tick %s warning: %s", code, e)
        except Exception as e:
            logger.warning("download tick %s warning: %s", code, e)

    raw = xtdata.get_market_data_ex(
        field_list=[],
        stock_list=[code],
        period="tick",
        start_time=trade_date,
        end_time=trade_date,
        fill_data=False,
    )
    if raw is None:
        return []
    df = raw.get(code)
    if df is None or (hasattr(df, "empty") and df.empty):
        return []
    return _parse_tick_dataframe(df)


def _parse_tick_dataframe(df: Any) -> list[dict]:
    """将 xtdata tick DataFrame 解析为字典列表。"""
    ticks: list[dict] = []
    for ts, row in df.iterrows():
        try:
            time_val = row.get("time")
            if time_val is not None and int(time_val) > 0:
                timestamp = int(time_val)
            elif isinstance(ts, (int, float)):
                timestamp = int(ts)
            else:
                timestamp = str(ts)
            tick: dict[str, Any] = {
                "timestamp": timestamp,
                "price": float(row.get("lastPrice", 0) or 0),
                "volume": int(row.get("volume", 0) or 0),
                "amount": float(row.get("amount", 0) or 0),
                "open": float(row.get("open", 0) or 0),
                "high": float(row.get("high", 0) or 0),
                "low": float(row.get("low", 0) or 0),
                "lastClose": float(row.get("lastClose", 0) or 0),
                "tickvol": int(row.get("tickvol", 0) or 0),
                "transactionNum": int(row.get("transactionNum", 0) or 0),
            }
            for key in ("bidPrice", "askPrice"):
                arr = row.get(key)
                if arr is not None:
                    if hasattr(arr, "tolist"):
                        tick[key] = [float(x) for x in arr.tolist()]
                    elif isinstance(arr, (list, tuple)):
                        tick[key] = [float(x) for x in arr]
            for key in ("bidVol", "askVol"):
                arr = row.get(key)
                if arr is not None:
                    if hasattr(arr, "tolist"):
                        tick[key] = [int(x) for x in arr.tolist()]
                    elif isinstance(arr, (list, tuple)):
                        tick[key] = [int(x) for x in arr]
            ticks.append(tick)
        except Exception:
            continue
    return ticks


# ---------------------------------------------------------------------------
# 服务器启动
# ---------------------------------------------------------------------------

class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def start_bridge_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    enable_trading: bool = True,
) -> None:
    """启动 QMT 数据 + 交易桥接 HTTP 服务。"""
    server = ThreadingHTTPServer((host, port), BridgeHandler)

    if enable_trading and TRADING_ACCOUNT_ID:
        t = threading.Thread(
            target=_trading_connect_background,
            kwargs={"interval": 5, "max_retries": 0},
            daemon=True,
        )
        t.start()
        logger.info("Trading auto-connect started (account=%s)", TRADING_ACCOUNT_ID)

    endpoints = "GET /health /trading/events, POST /klines /quote /tick_download /trading /sector_list /sector_members"
    logger.info("QMT Data Bridge v%d 启动: http://%s:%d", BRIDGE_VERSION, host, port)
    logger.info("Endpoints: %s", endpoints)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("QMT Data Bridge 关闭")
    finally:
        server.server_close()


def main() -> None:
    """命令行入口。"""
    global MINI_QMT_PATH, TRADING_ACCOUNT_ID, _xtquant_userdata

    parser = argparse.ArgumentParser(description="QMT Data Bridge Server")
    parser.add_argument("--host", default=os.environ.get("SERVE_HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=int(os.environ.get("SERVE_PORT", DEFAULT_PORT)))
    parser.add_argument("--qmt-path", default="", help="QMT 安装路径 (也可通过 QMT_PATH 环境变量设置)")
    parser.add_argument("--mini-path", default="", help="miniQMT userdata 路径")
    parser.add_argument("--account", default="", help="交易账户 ID")
    parser.add_argument("--no-trading", action="store_true", help="禁用交易功能")
    args = parser.parse_args()

    if args.account:
        TRADING_ACCOUNT_ID = args.account
    if args.mini_path:
        MINI_QMT_PATH = args.mini_path
    if MINI_QMT_PATH:
        _xtquant_userdata = MINI_QMT_PATH

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    start_bridge_server(args.host, args.port, enable_trading=not args.no_trading)


if __name__ == "__main__":
    main()
