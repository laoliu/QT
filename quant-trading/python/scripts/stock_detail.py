#!/usr/bin/env python3
"""
Rich stock quote — returns detailed quote data including bid/ask depth and basic technicals.

Input (JSON via stdin):
  { "action": "detail", "symbol": "600519", "market": "cn_stock", "kline_days": 120 }

Output: comprehensive quote snapshot with order book, technicals, and recent klines.
"""

import json
import math
import os
import socket
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from datetime import datetime, timedelta

# Global socket timeout — limits akshare's internal HTTP calls to 15 seconds each
# so a single slow source can't block the entire script.
socket.setdefaulttimeout(15)

# Proxy bypass
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"
urllib.request.getproxies = lambda: {}  # type: ignore[assignment]
try:
    import requests, requests.utils  # noqa: E401
    requests.utils.getproxies = lambda: {}  # type: ignore[assignment]
except ImportError:
    pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PYTHON_ROOT)

def _bridge_url() -> str:
    from data_providers.akshare_provider import _get_qmt_bridge_url
    return _get_qmt_bridge_url()


def _safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _safe_int(v, default=0):
    try:
        return int(float(v)) if v is not None else default
    except (ValueError, TypeError):
        return default


def _sanitize(obj):
    """Replace NaN/Infinity with None so json.dumps produces valid JSON."""
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def _sina_market_prefix(symbol: str) -> str:
    """Return Sina market prefix for a stock code."""
    if symbol.startswith("6") or symbol.startswith("5") or symbol.startswith("9"):
        return "sh"
    return "sz"


def _tencent_market_prefix(symbol: str) -> str:
    """Return Tencent market prefix for a stock code."""
    if symbol.startswith("6") or symbol.startswith("5") or symbol.startswith("9"):
        return "sh"
    return "sz"


def _fetch_tencent_quote(symbol: str, market: str = "cn_stock") -> dict | None:
    """Fetch real-time quote + 5-level depth + fundamentals from Tencent Finance API.

    Returns parsed dict or None on failure.
    Tencent qt.gtimg.cn fields (tilde-separated):
      [0]:市场 [1]:名称 [2]:代码 [3]:现价 [4]:昨收 [5]:今开
      [6]:成交量(手) [7]:外盘 [8]:内盘
      [9-18]:买一~买五(价/量交替) [19-28]:卖一~卖五(价/量交替)
      [31]:涨跌 [32]:涨跌% [33]:最高 [34]:最低
      [36]:成交量(手) [37]:成交额(万) [38]:换手率 [39]:市盈率
      [43]:振幅 [44]:流通市值(亿) [45]:总市值(亿) [46]:市净率
      [47]:涨停价 [48]:跌停价 [49]:量比
    """
    prefix = _tencent_market_prefix(symbol)
    if market == "index":
        prefix = "sz" if symbol.startswith("399") else "sh"
    url = f"http://qt.gtimg.cn/q={prefix}{symbol}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
            for enc in ("utf-8", "gbk", "gb2312"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                return None
        eq_pos = text.find("=")
        if eq_pos < 0:
            return None
        inner = text[eq_pos + 1:].strip().strip('"').strip(";").strip('"')
        fields = inner.split("~")
        if len(fields) < 50:
            return None
        return {
            "name": fields[1],
            "price": _safe_float(fields[3]),
            "prevClose": _safe_float(fields[4]),
            "open": _safe_float(fields[5]),
            "volume": _safe_int(_safe_float(fields[6]) * 100),  # 手→股
            "high": _safe_float(fields[33]),
            "low": _safe_float(fields[34]),
            "amount": _safe_float(fields[37]) * 10000,  # 万→元
            "change": _safe_float(fields[31]),
            "changePct": _safe_float(fields[32]),
            # 5-level bid: [9]=买一价,[10]=买一量, [11]=买二价,[12]=买二量, ...
            "bids": [
                {"price": _safe_float(fields[9]), "volume": _safe_int(fields[10]) * 100},
                {"price": _safe_float(fields[11]), "volume": _safe_int(fields[12]) * 100},
                {"price": _safe_float(fields[13]), "volume": _safe_int(fields[14]) * 100},
                {"price": _safe_float(fields[15]), "volume": _safe_int(fields[16]) * 100},
                {"price": _safe_float(fields[17]), "volume": _safe_int(fields[18]) * 100},
            ],
            # 5-level ask: [19]=卖一价,[20]=卖一量, ...
            "asks": [
                {"price": _safe_float(fields[19]), "volume": _safe_int(fields[20]) * 100},
                {"price": _safe_float(fields[21]), "volume": _safe_int(fields[22]) * 100},
                {"price": _safe_float(fields[23]), "volume": _safe_int(fields[24]) * 100},
                {"price": _safe_float(fields[25]), "volume": _safe_int(fields[26]) * 100},
                {"price": _safe_float(fields[27]), "volume": _safe_int(fields[28]) * 100},
            ],
            # Fundamental fields only Tencent provides
            "turnoverRate": _safe_float(fields[38]),
            "pe": _safe_float(fields[39]),
            "amplitude": _safe_float(fields[43]),
            "circulatingMarketCap": _safe_float(fields[44]) * 100000000,  # 亿→元
            "totalMarketCap": _safe_float(fields[45]) * 100000000,  # 亿→元
            "pb": _safe_float(fields[46]),
            "volumeRatio": _safe_float(fields[49]),
        }
    except Exception:
        return None


def _fetch_sina_quote(symbol: str, market: str = "cn_stock") -> dict | None:
    """Fetch real-time quote + 5-level depth from Sina Finance API.

    Returns parsed dict or None on failure.
    Sina response format (comma-separated):
      0:名称, 1:今开, 2:昨收, 3:最新价, 4:最高, 5:最低,
      6:买一价, 7:卖一价, 8:成交量(股), 9:成交额(Ԫ),
      10-19: 买一量/价 ~ 买五量/价, 20-29: 卖一量/价 ~ 卖五量/价,
      30:日期, 31:时间
    """
    prefix = _sina_market_prefix(symbol)
    if market == "index":
        prefix = "sz" if symbol.startswith("399") else "sh"
    url = f"https://hq.sinajs.cn/list={prefix}{symbol}"
    req = urllib.request.Request(url, headers={
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
            # Try utf-8 first, then gbk
            for enc in ("utf-8", "gbk", "gb2312"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                return None
        # Parse: var hq_str_shXXXXXX="field0,field1,...";
        start = text.find('"')
        end = text.rfind('"')
        if start < 0 or end <= start:
            return None
        fields = text[start + 1:end].split(",")
        if len(fields) < 32:
            return None
        return {
            "name": fields[0],
            "open": _safe_float(fields[1]),
            "prevClose": _safe_float(fields[2]),
            "price": _safe_float(fields[3]),
            "high": _safe_float(fields[4]),
            "low": _safe_float(fields[5]),
            "volume": _safe_int(fields[8]),
            "amount": _safe_float(fields[9]),
            # 5-level bid: fields[10]=buy1vol, [11]=buy1price, [12]=buy2vol, ...
            "bids": [
                {"price": _safe_float(fields[11]), "volume": _safe_int(fields[10])},
                {"price": _safe_float(fields[13]), "volume": _safe_int(fields[12])},
                {"price": _safe_float(fields[15]), "volume": _safe_int(fields[14])},
                {"price": _safe_float(fields[17]), "volume": _safe_int(fields[16])},
                {"price": _safe_float(fields[19]), "volume": _safe_int(fields[18])},
            ],
            # 5-level ask: fields[20]=sell1vol, [21]=sell1price, ...
            "asks": [
                {"price": _safe_float(fields[21]), "volume": _safe_int(fields[20])},
                {"price": _safe_float(fields[23]), "volume": _safe_int(fields[22])},
                {"price": _safe_float(fields[25]), "volume": _safe_int(fields[24])},
                {"price": _safe_float(fields[27]), "volume": _safe_int(fields[26])},
                {"price": _safe_float(fields[29]), "volume": _safe_int(fields[28])},
            ],
            "date": fields[30] if len(fields) > 30 else "",
            "time": fields[31] if len(fields) > 31 else "",
        }
    except Exception:
        return None


# ── Bridge helpers ─────────────────────────────────────────────────

def _check_bridge() -> bool:
    """Return True if the Windows xtdata bridge is reachable."""
    try:
        url = _bridge_url()
        if not url:
            return False
        req = urllib.request.Request(f"{url}/health")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            # Accept both response formats: {"status":"ok"} and {"ok":true}
            status_ok = data.get("status") in ("ok", "degraded") or data.get("ok") is True
            return status_ok and data.get("bridge_connected", data.get("xtdata", False))
    except Exception:
        return False


def _fetch_klines_bridge(symbol: str, period: str = "1d", count: int = 120) -> list[dict] | None:
    """Fetch K-lines from the Windows xtdata bridge.

    Returns list of K-line bars in the same format as akshare_provider.get_klines,
    or None if bridge is unavailable/fails.
    """
    # Convert symbol to QMT format
    first = symbol[0] if symbol else ""
    if first in ("6", "5"):
        qmt_code = f"{symbol}.SH"
    elif first in ("0", "3"):
        qmt_code = f"{symbol}.SZ"
    else:
        return None

    # Map our interval names to xtdata period names
    period_map = {"1d": "1d", "1w": "1w", "1M": "1mon"}
    xt_period = period_map.get(period)
    if not xt_period:
        return None  # bridge doesn't support this interval

    body = json.dumps(
        {"codes": [qmt_code], "period": xt_period, "count": count},
        ensure_ascii=False,
    ).encode("utf-8")

    try:
        url = _bridge_url()
        if not url:
            return None
        req = urllib.request.Request(
            f"{url}/klines",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            # Accept both: {"status":"ok","data":{...}} and {"ok":true,"data":{"bars":{...}}}
            if not (resp_data.get("status") == "ok" or resp_data.get("ok") is True):
                return None
            kline_data = resp_data.get("data", {})
            # AlphQ bridge nests under "bars", legacy bridge puts directly in data
            if "bars" in kline_data and isinstance(kline_data["bars"], dict):
                kline_data = kline_data["bars"]
            raw_bars = kline_data.get(qmt_code) or kline_data.get(symbol, [])
            if not raw_bars:
                return None
            # Convert bridge format {date, open, high, low, close, volume}
            # to our standard format {timestamp, open, high, low, close, volume, amount}
            bars: list[dict] = []
            for bar in raw_bars:
                date_str = str(bar.get("date", ""))
                if len(date_str) != 8:
                    continue
                try:
                    ts = int(datetime.strptime(date_str, "%Y%m%d").timestamp() * 1000)
                except (ValueError, OSError):
                    continue
                close_val = float(bar.get("close", 0) or 0)
                if close_val <= 0:
                    continue
                bars.append({
                    "timestamp": ts,
                    "open": float(bar.get("open", 0) or 0),
                    "high": float(bar.get("high", 0) or 0),
                    "low": float(bar.get("low", 0) or 0),
                    "close": close_val,
                    "volume": float(bar.get("volume", 0) or 0),
                    "amount": float(bar.get("amount", 0) or 0),
                })
            return bars if bars else None
    except Exception:
        return None


def _fetch_quote_bridge(symbol: str, market: str = "cn_stock") -> dict | None:
    """Fetch real-time quote from the Windows xtdata bridge /quote endpoint.

    Returns quote dict with price/depth or None if unavailable.
    """
    from data_providers.symbols import to_qmt_code
    qmt_code = to_qmt_code(symbol, market)

    body = json.dumps({"codes": [qmt_code]}, ensure_ascii=False).encode("utf-8")
    try:
        url = _bridge_url()
        if not url:
            return None
        req = urllib.request.Request(
            f"{url}/quote",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
            # Accept both: {"status":"ok","data":{...}} and {"ok":true,"data":{"quotes":{...}}}
            if not (resp_data.get("status") == "ok" or resp_data.get("ok") is True):
                return None
            quote_data = resp_data.get("data", {})
            # AlphQ bridge nests under "quotes", legacy bridge puts directly in data
            if "quotes" in quote_data and isinstance(quote_data["quotes"], dict):
                quote_data = quote_data["quotes"]
            q = quote_data.get(qmt_code) or quote_data.get(symbol)
            if not q:
                return None
            raw_stock_tick = market == "cn_stock" and "lastPrice" in q and "price" not in q
            # Normalize field names: AlphQ uses lastPrice/lastClose, legacy uses price/prevClose
            if "lastPrice" in q and "price" not in q:
                q["price"] = q["lastPrice"]
            if "lastClose" in q and "prevClose" not in q:
                q["prevClose"] = q["lastClose"]
            # Map bid/ask arrays from AlphQ format
            if "askPrice" in q and "asks" not in q:
                asks = q.get("askPrice", [])
                ask_vols = q.get("askVol", [])
                q["asks"] = [{"price": p, "volume": int(v)} for p, v in zip(asks, ask_vols) if p > 0]
            if "bidPrice" in q and "bids" not in q:
                bids = q.get("bidPrice", [])
                bid_vols = q.get("bidVol", [])
                q["bids"] = [{"price": p, "volume": int(v)} for p, v in zip(bids, bid_vols) if p > 0]
            if raw_stock_tick:
                # xtdata stock tick volumes are lots; the detail page uses shares.
                q["volume"] = _safe_int(q.get("volume")) * 100
                for side in ("bids", "asks"):
                    q[side] = [{**level, "volume": _safe_int(level.get("volume")) * 100}
                               for level in q.get(side, [])]
            if not q.get("price"):
                return None
            return q
    except Exception:
        return None


def _aggregate_klines(bars: list[dict], interval: str) -> list[dict]:
    """Aggregate monthly K-line bars into quarterly (1Q), semi-annual (6M), or annual (1Y) candles."""
    if not bars:
        return []

    def _group_key(ts_ms: int) -> str:
        dt = datetime.fromtimestamp(ts_ms / 1000)
        if interval == "1Q":
            q = (dt.month - 1) // 3
            return f"{dt.year}-Q{q}"
        elif interval == "6M":
            h = 0 if dt.month <= 6 else 1
            return f"{dt.year}-H{h}"
        else:  # 1Y
            return str(dt.year)

    groups: dict[str, list[dict]] = {}
    for bar in bars:
        key = _group_key(bar["timestamp"])
        groups.setdefault(key, []).append(bar)

    result = []
    for key in sorted(groups.keys()):
        group = groups[key]
        result.append({
            "timestamp": group[0]["timestamp"],
            "open": group[0]["open"],
            "high": max(b["high"] for b in group),
            "low": min(b["low"] for b in group),
            "close": group[-1]["close"],
            "volume": sum(b.get("volume", 0) for b in group),
            "amount": sum(b.get("amount", 0) for b in group),
        })
    return result


def get_stock_history(symbol: str, market: str = "cn_stock", kline_days: int = 120,
                      kline_interval: str = "1d", local_only: bool = False) -> dict:
    """Read the chart snapshot separately from optional remote gap recovery."""
    import re
    from data_providers.akshare_provider import get_klines

    symbol = re.sub(r"^(?:SH|SZ)(\d{6})$", r"\1", symbol.strip(), flags=re.IGNORECASE)
    fetch_interval = "1M" if kline_interval in ("1Q", "6M", "1Y") else kline_interval
    multiplier = {"1w": 10, "1M": 35, "1Q": 100, "6M": 200, "1Y": 400}.get(
        kline_interval, 2.5 if kline_days < 300 else 1.5)
    start_date = (datetime.now() - timedelta(days=int(kline_days * multiplier))).strftime("%Y%m%d")
    end_date = datetime.now().strftime("%Y%m%d")
    if market in ("cn_stock", "index"):
        from scripts.batch_sync import latest_completed_trading_date
        end_date = latest_completed_trading_date().replace("-", "")
    bars = get_klines(symbol, fetch_interval, start_date, end_date, market, local_only)
    incomplete = not bars
    if bars and market in ("cn_stock", "index"):
        incomplete = datetime.fromtimestamp(bars[-1]["timestamp"] / 1000).strftime("%Y%m%d") < end_date
    if kline_interval in ("1Q", "6M", "1Y") and bars:
        bars = _aggregate_klines(bars, kline_interval)
    bars = bars[-kline_days:] if bars else []
    result = {"symbol": symbol, "market": market, "klines": bars,
              "klineInterval": kline_interval, "klineSource": "local" if local_only else "mixed",
              "klineIncomplete": incomplete}
    if len(bars) >= 20:
        result["technicals"] = compute_technicals([b["close"] for b in bars])
    return result


def get_stock_detail(symbol: str, market: str = "cn_stock", kline_days: int = 120, kline_interval: str = "1d", section: str = "all") -> dict:
    """Fetch comprehensive stock detail."""
    import akshare as ak

    # Normalize: strip SH/SZ/sh/sz prefix (e.g. "SZ002131" → "002131")
    import re as _re
    _m = _re.match(r"^(?:SH|SZ|sh|sz)(\d{6})$", symbol)
    if _m:
        symbol = _m.group(1)

    result: dict = {"symbol": symbol, "market": market}

    # ── 1. Parallel fetch: quotes (Bridge/Sina/Tencent) + K-lines ─
    # All sources are independent — run them concurrently to cut latency
    # from ~20s serial to ~8s parallel.
    def _task_bridge_quote():
        if market not in ("cn_stock", "index"):
            return None
        return _fetch_quote_bridge(symbol, market)

    def _task_sina_quote():
        return _fetch_sina_quote(symbol, market)

    def _task_tencent_quote():
        return _fetch_tencent_quote(symbol, market)

    def _task_klines():
        if section == "quote":
            return {}
        try:
            return get_stock_history(symbol, market, kline_days, kline_interval, section == "core")
        except Exception as exc:
            return {"klines": [], "klineIncomplete": True, "klinesError": str(exc)}

    fast_section = section in ("core", "quote")
    _QUOTE_KLINE_TIMEOUT = 12 if fast_section else 18

    bridge_quote = None
    sina = None
    tencent = None
    kline_result = {}

    pool_qk = ThreadPoolExecutor(max_workers=4)
    fut_bridge = pool_qk.submit(_task_bridge_quote)
    fut_sina = pool_qk.submit(_task_sina_quote)
    fut_tencent = pool_qk.submit(_task_tencent_quote)
    fut_klines = pool_qk.submit(_task_klines)

    try:
        for fut in as_completed([fut_bridge, fut_sina, fut_tencent, fut_klines],
                                timeout=_QUOTE_KLINE_TIMEOUT):
            try:
                val = fut.result(timeout=1)
            except Exception:
                val = None
            if fut is fut_bridge:
                bridge_quote = val
            elif fut is fut_sina:
                sina = val
            elif fut is fut_tencent:
                tencent = val
            elif fut is fut_klines:
                if val is not None:
                    kline_result = val
            valid_bridge = bridge_quote and bridge_quote.get("price", 0) > 0
            valid_fallback = fut_bridge.done() and any(q and q.get("price", 0) > 0 for q in (sina, tencent))
            if fast_section and (valid_bridge or valid_fallback) and fut_klines.done():
                # Optional public sources must not hold up a ready QMT snapshot.
                kline_result = fut_klines.result()
                # Even a valid QMT book lacks valuation metrics and may expose
                # fewer price levels. Give the enrichers a bounded grace period.
                wait([fut_sina, fut_tencent], timeout=0.2)
                for source, future in (("sina", fut_sina), ("tencent", fut_tencent)):
                    if future.done():
                        try:
                            if source == "sina":
                                sina = future.result()
                            else:
                                tencent = future.result()
                        except Exception:
                            pass
                break
    except TimeoutError:
        if fut_klines.done():
            kline_result = fut_klines.result()
        elif section != "quote":
            kline_result = {"klines": [], "klineIncomplete": True}
    finally:
        pool_qk.shutdown(wait=False, cancel_futures=True)

    # ── 1b. Assemble quote from parallel results ─────────────────
    # Priority: Bridge → Sina → Tencent → EastMoney (last resort)
    if bridge_quote and bridge_quote.get("price", 0) > 0:
        prev = bridge_quote.get("prevClose") or 1
        change = bridge_quote["price"] - prev
        change_pct = change / prev * 100
        result["quote"] = {
            "price": bridge_quote["price"],
            "change": round(change, 2),
            "changePct": round(change_pct, 2),
            "open": bridge_quote.get("open", 0),
            "high": bridge_quote.get("high", 0),
            "low": bridge_quote.get("low", 0),
            "prevClose": prev,
            "volume": bridge_quote.get("volume", 0),
            "amount": bridge_quote.get("amount", 0),
            "timestamp": int(datetime.now().timestamp() * 1000),
        }
        bids = bridge_quote.get("bids", [])
        asks = bridge_quote.get("asks", [])
        if bids or asks:
            result["depth"] = {"bids": bids, "asks": asks}
            result["depthSource"] = "bridge"
        result["quoteSource"] = "bridge"

    # Get stock name from Sina/Tencent regardless of quote source
    if sina and sina.get("name"):
        result["name"] = sina["name"]
    elif tencent and tencent.get("name"):
        result["name"] = tencent["name"]

    # Sina as primary (if bridge didn't provide quote)
    if "quote" not in result and sina and sina["price"] > 0:
        prev = sina["prevClose"] or 1
        change = sina["price"] - prev
        change_pct = change / prev * 100
        result["quote"] = {
            "price": sina["price"],
            "change": round(change, 2),
            "changePct": round(change_pct, 2),
            "open": sina["open"],
            "high": sina["high"],
            "low": sina["low"],
            "prevClose": sina["prevClose"],
            "volume": sina["volume"],
            "amount": sina["amount"],
            "timestamp": int(datetime.now().timestamp() * 1000),
        }
        result["name"] = sina["name"]
        bids = [b for b in sina["bids"] if b["price"] > 0]
        asks = [a for a in sina["asks"] if a["price"] > 0]
        if bids or asks:
            result["depth"] = {"bids": bids, "asks": asks}
        result["quoteSource"] = "sina"
    # Tencent as fallback
    elif "quote" not in result and tencent and tencent["price"] > 0:
        result["quote"] = {
            "price": tencent["price"],
            "change": tencent["change"],
            "changePct": tencent["changePct"],
            "open": tencent["open"],
            "high": tencent["high"],
            "low": tencent["low"],
            "prevClose": tencent["prevClose"],
            "volume": tencent["volume"],
            "amount": tencent["amount"],
            "turnoverRate": tencent["turnoverRate"],
            "pe": tencent["pe"],
            "pb": tencent["pb"],
            "totalMarketCap": tencent["totalMarketCap"],
            "circulatingMarketCap": tencent["circulatingMarketCap"],
            "amplitude": tencent["amplitude"],
            "volumeRatio": tencent["volumeRatio"],
            "timestamp": int(datetime.now().timestamp() * 1000),
        }
        result["name"] = tencent["name"]
        bids = [b for b in tencent["bids"] if b["price"] > 0]
        asks = [a for a in tencent["asks"] if a["price"] > 0]
        if bids or asks:
            result["depth"] = {"bids": bids, "asks": asks}
        result["quoteSource"] = "tencent"

    # Supplement: merge Tencent fundamentals into bridge or Sina quote
    if tencent and "quote" in result and result.get("quoteSource") in ("sina", "bridge"):
        q = result["quote"]
        q.setdefault("turnoverRate", tencent.get("turnoverRate"))
        q.setdefault("pe", tencent.get("pe"))
        q.setdefault("pb", tencent.get("pb"))
        q.setdefault("totalMarketCap", tencent.get("totalMarketCap"))
        q.setdefault("circulatingMarketCap", tencent.get("circulatingMarketCap"))
        q.setdefault("amplitude", tencent.get("amplitude"))
        q.setdefault("volumeRatio", tencent.get("volumeRatio"))
        result["quoteSource"] = result["quoteSource"] + "+tencent"

    # Last resort: try eastmoney via akshare (only when all three failed)
    if "quote" not in result and not fast_section and market == "cn_stock":
        try:
            df_spot = ak.stock_zh_a_spot_em()
            if df_spot is not None and not df_spot.empty:
                row = df_spot[df_spot["代码"] == symbol]
                if not row.empty:
                    r = row.iloc[0]
                    result["quote"] = {
                        "price": _safe_float(r.get("最新价")),
                        "change": _safe_float(r.get("涨跌额")),
                        "changePct": _safe_float(r.get("涨跌幅")),
                        "open": _safe_float(r.get("今开")),
                        "high": _safe_float(r.get("最高")),
                        "low": _safe_float(r.get("最低")),
                        "prevClose": _safe_float(r.get("昨收")),
                        "volume": _safe_int(r.get("成交量")),
                        "amount": _safe_float(r.get("成交额")),
                        "turnoverRate": _safe_float(r.get("换手率")),
                        "pe": _safe_float(r.get("市盈率-动态")),
                        "pb": _safe_float(r.get("市净率")),
                        "totalMarketCap": _safe_float(r.get("总市值")),
                        "circulatingMarketCap": _safe_float(r.get("流通市值")),
                        "amplitude": _safe_float(r.get("振幅")),
                        "volumeRatio": _safe_float(r.get("量比")),
                        "high52w": _safe_float(r.get("52周最高")),
                        "low52w": _safe_float(r.get("52周最低")),
                        "timestamp": int(datetime.now().timestamp() * 1000),
                    }
                    result["quoteSource"] = "eastmoney"
        except Exception as e:
            result["quoteError"] = str(e)

    # Prefer a complete public book when it agrees with the current QMT price.
    # Replace the whole snapshot rather than mixing levels from two sources.
    if result.get("depthSource") == "bridge":
        depth = result["depth"]
        for source, candidate in (("tencent", tencent), ("sina", sina)):
            if not candidate or abs(candidate.get("price", 0) - result["quote"]["price"]) > 0.001:
                continue
            if abs(candidate.get("prevClose", 0) - result["quote"]["prevClose"]) > 0.001:
                continue
            bids = [b for b in candidate.get("bids", []) if b["price"] > 0]
            asks = [a for a in candidate.get("asks", []) if a["price"] > 0]
            if len(bids) + len(asks) > len(depth["bids"]) + len(depth["asks"]):
                depth = {"bids": bids, "asks": asks}
                result["depth"] = depth
                result["depthSource"] = source

    # ── 2. Bid/Ask depth (if not already populated) ───────────────
    if "depth" not in result:
        for candidate in (tencent, sina):
            if not candidate:
                continue
            bids = [b for b in candidate.get("bids", []) if b["price"] > 0]
            asks = [a for a in candidate.get("asks", []) if a["price"] > 0]
            if bids or asks:
                result["depth"] = {"bids": bids, "asks": asks}
                break
        if "depth" not in result and not fast_section and market == "cn_stock":
            try:
                df_bid = ak.stock_bid_ask_em(symbol=symbol)
                if df_bid is not None and not df_bid.empty:
                    depth = {"bids": [], "asks": []}
                    for _, row in df_bid.iterrows():
                        item_name = str(row.get("item", ""))
                        price = _safe_float(row.get("value"))
                        vol = _safe_int(row.get("value2", 0))
                        if "买" in item_name:
                            depth["bids"].append({"price": price, "volume": vol})
                        elif "卖" in item_name:
                            depth["asks"].append({"price": price, "volume": vol})
                    depth["asks"] = sorted(depth["asks"], key=lambda x: x["price"])
                    depth["bids"] = sorted(depth["bids"], key=lambda x: x["price"], reverse=True)
                    result["depth"] = depth
            except Exception as e:
                result["depthError"] = str(e)

    # ── 3. Stock name (if not already from Sina/Tencent) ──────────
    if ("name" not in result or not result["name"]) and market != "index":
        try:
            from cache.stock_cache import get_cache
            cache = get_cache()
            name = cache.get_stock_name(symbol)
            if name:
                result["name"] = name
            elif not fast_section:
                name_df = ak.stock_info_a_code_name()
                match = name_df[name_df["code"] == symbol]
                if not match.empty:
                    result["name"] = str(match.iloc[0]["name"])
                    cache.save_code_name_map({symbol: result["name"]})
        except Exception:
            result["name"] = symbol

    if section == "quote":
        return result

    result.update(kline_result)
    klines = result.get("klines", [])

    # ── 5b. Compute 52-week high/low from klines if still missing ──
    # Each bar (daily/weekly/monthly) already contains high/low for that period,
    # so we can compute 52-week range from any interval without extra fetches.
    if "quote" in result:
        q = result["quote"]
        if q.get("high52w") is None or q.get("low52w") is None:
            _52w_count = {"1d": 252, "1w": 52, "1M": 12, "1Q": 4, "6M": 2, "1Y": 1}
            n = _52w_count.get(kline_interval, 252)
            _52w_bars = klines[-n:] if klines else None
            if _52w_bars:
                highs = [b["high"] for b in _52w_bars if b.get("high")]
                lows = [b["low"] for b in _52w_bars if b.get("low")]
                if highs and q.get("high52w") is None:
                    q["high52w"] = max(highs)
                if lows and q.get("low52w") is None:
                    q["low52w"] = min(lows)

    if "quote" not in result and klines:
        last = klines[-1]
        prev = klines[-2] if len(klines) >= 2 else last
        change = last["close"] - prev["close"]
        result["quote"] = {
            "price": last["close"], "change": round(change, 2),
            "changePct": round(change / prev["close"] * 100, 2) if prev["close"] else 0,
            "open": last["open"], "high": last["high"], "low": last["low"],
            "prevClose": prev["close"], "volume": last.get("volume", 0),
            "amount": last.get("amount", 0), "timestamp": last.get("timestamp", 0),
        }
        result["quoteSource"] = "cache"
    if section == "all" and market != "index":
        result.update(get_stock_supplement(symbol, market, stock_name=result.get("name", "")))
    return result


def get_stock_supplement(symbol: str, market: str = "cn_stock", section: str = "all", stock_name: str = "") -> dict:
    """Fetch optional sections independently of the quote and chart request."""
    import akshare as ak
    result = {"symbol": symbol, "market": market}
    if market == "index":
        return result

    # ── 6. Parallel fetch: company info, financials, shareholders, news ──
    # All sections below are independent — run them concurrently to avoid
    # sequential 10-20s waits per akshare call.

    def _fetch_company_info():
        """6a. Company profile."""
        try:
            info_df = ak.stock_profile_cninfo(symbol=symbol)
            if info_df is not None and not info_df.empty:
                row = info_df.iloc[0]
                return ("companyInfo", {
                    "name": str(row.get("公司名称", "")),
                    "industry": str(row.get("所属行业", "")),
                    "market": str(row.get("所属市场", "")),
                    "listDate": str(row.get("上市日期", "")),
                    "legalRep": str(row.get("法人代表", "")),
                    "regCapital": _safe_float(row.get("注册资金")),
                    "website": str(row.get("官方网站", "")),
                    "mainBiz": str(row.get("主营业务", "")),
                })
        except Exception:
            pass
        return None

    def _fetch_business():
        """6b. Main business description."""
        try:
            biz_df = ak.stock_zyjs_ths(symbol=symbol)
            if biz_df is not None and not biz_df.empty:
                row = biz_df.iloc[0]
                return ("business", {
                    "mainBusiness": str(row.get("主营业务", "")),
                    "products": str(row.get("产品名称", "")),
                    "scope": str(row.get("经营范围", "")),
                })
        except Exception:
            pass
        return None

    def _fetch_financials():
        """6c. Financial summary."""
        try:
            fin_df = ak.stock_financial_abstract(symbol=symbol)
            if fin_df is not None and not fin_df.empty:
                date_cols = [c for c in fin_df.columns if c not in ("选项", "指标")]
                recent_dates = sorted(date_cols, reverse=True)[:4]
                financials = []
                for _, row in fin_df.iterrows():
                    indicator = str(row.get("指标", ""))
                    if indicator in ("归母净利润", "营业总收入", "营业成本", "净利润",
                                     "总资产", "净资产", "资产负债率", "每股收益",
                                     "每股净资产", "净资产收益率", "毛利率"):
                        item = {"name": indicator}
                        for d in recent_dates:
                            val = row.get(d)
                            item[d] = float(val) if val is not None and val == val else None
                        financials.append(item)
                if financials:
                    return ("financials", {"periods": recent_dates, "items": financials})
        except Exception:
            pass
        return None

    def _fetch_shareholders():
        """6d. Top shareholders."""
        try:
            holder_df = ak.stock_main_stock_holder(stock=symbol)
            if holder_df is not None and not holder_df.empty:
                latest_date = holder_df["截至日期"].max()
                latest = holder_df[holder_df["截至日期"] == latest_date].head(10)
                holders = []
                for _, row in latest.iterrows():
                    holders.append({
                        "name": str(row.get("股东名称", "")),
                        "ratio": _safe_float(row.get("持股比例")),
                        "shares": _safe_float(row.get("持股数量")),
                        "type": str(row.get("股本性质", "")),
                    })
                if holders:
                    return ("shareholders", {
                        "date": str(latest_date),
                        "totalCount": _safe_int(latest.iloc[0].get("股东总数")),
                        "avgShares": _safe_int(latest.iloc[0].get("平均持股数")),
                        "holders": holders,
                    })
        except Exception:
            pass
        return None

    # ── News sources (each returns a list of news items) ──────────
    def _news_eastmoney():
        """直接调用东方财富搜索 API，绕过 akshare stock_news_em 的 pyarrow 正则 bug"""
        try:
            import json as _json, re as _re
            from curl_cffi import requests as _cffi_req
            url = "https://search-api-web.eastmoney.com/search/jsonp"
            inner_param = {
                "uid": "",
                "keyword": symbol,
                "type": ["cmsArticleWebOld"],
                "client": "web",
                "clientType": "web",
                "clientVersion": "curr",
                "param": {
                    "cmsArticleWebOld": {
                        "searchScope": "default",
                        "sort": "default",
                        "pageIndex": 1,
                        "pageSize": 10,
                        "preTag": "<em>",
                        "postTag": "</em>",
                    }
                },
            }
            cb = "jQuery35101792940631092459_1764599530165"
            params = {
                "cb": cb,
                "param": _json.dumps(inner_param, ensure_ascii=False),
                "_": "1764599530176",
            }
            r = _cffi_req.get(url, params=params, timeout=8)
            text = r.text
            start = text.index("(") + 1
            end = text.rindex(")")
            data_json = _json.loads(text[start:end])
            rows = data_json.get("result", {}).get("cmsArticleWebOld", [])
            _strip_em = _re.compile(r"</?em>")
            items = []
            for row in rows[:8]:
                title = _strip_em.sub("", row.get("title", ""))
                content = _strip_em.sub("", row.get("content", ""))
                content = content.replace("\u3000", "").replace("\r\n", " ")
                code = row.get("code", "")
                items.append({
                    "title": title,
                    "time": row.get("date", ""),
                    "source": row.get("mediaName", ""),
                    "content": content[:200],
                    "url": f"http://finance.eastmoney.com/a/{code}.html" if code else "",
                    "channel": "eastmoney",
                })
            return items
        except Exception:
            pass
        return []

    def _news_caixin():
        if not stock_name:
            return []
        try:
            cx_df = ak.stock_news_main_cx()
            if cx_df is not None and not cx_df.empty:
                items = []
                for _, row in cx_df.iterrows():
                    summary = str(row.get("summary", ""))
                    if stock_name in summary or symbol in summary:
                        items.append({
                            "title": summary[:80],
                            "time": "",
                            "source": "财新数据通",
                            "content": summary[:200],
                            "url": str(row.get("url", "")),
                            "channel": "caixin",
                        })
                        if len(items) >= 3:
                            break
                return items
        except Exception:
            pass
        return []

    def _news_research():
        try:
            rpt_df = ak.stock_research_report_em(symbol=symbol)
            if rpt_df is not None and not rpt_df.empty:
                items = []
                for _, row in rpt_df.head(5).iterrows():
                    items.append({
                        "title": str(row.get("报告名称", "")),
                        "time": str(row.get("日期", "")),
                        "source": str(row.get("机构", "")),
                        "rating": str(row.get("东财评级", "")),
                        "url": str(row.get("报告PDF链接", "")),
                        "channel": "research",
                    })
                return items
        except Exception:
            pass
        return []

    def _news_global_em():
        if not stock_name:
            return []
        try:
            global_df = ak.stock_info_global_em()
            if global_df is not None and not global_df.empty:
                items = []
                for _, row in global_df.iterrows():
                    title = str(row.get("标题", ""))
                    content = str(row.get("ժҪ", str(row.get("内容", ""))))
                    if stock_name in title or stock_name in content or symbol in title:
                        items.append({
                            "title": title,
                            "time": str(row.get("发布时间", "")),
                            "source": "东方财富全球",
                            "content": content[:200],
                            "url": "",
                            "channel": "global",
                        })
                        if len(items) >= 3:
                            break
                return items
        except Exception:
            pass
        return []

    def _news_cls():
        if not stock_name:
            return []
        try:
            cls_df = ak.stock_info_global_cls()
            if cls_df is not None and not cls_df.empty:
                items = []
                for _, row in cls_df.iterrows():
                    title = str(row.get("标题", ""))
                    content = str(row.get("内容", ""))
                    if stock_name in title or stock_name in content or symbol in content:
                        items.append({
                            "title": title if title else content[:80],
                            "time": f'{row.get("发布日期", "")} {row.get("发布时间", "")}',
                            "source": "财联社",
                            "content": content[:200],
                            "url": "",
                            "channel": "cls",
                        })
                        if len(items) >= 3:
                            break
                return items
        except Exception:
            pass
        return []

    def _news_ths():
        if not stock_name:
            return []
        try:
            ths_df = ak.stock_info_global_ths()
            if ths_df is not None and not ths_df.empty:
                items = []
                for _, row in ths_df.iterrows():
                    title = str(row.get("标题", ""))
                    content = str(row.get("内容", ""))
                    if stock_name in title or stock_name in content or symbol in content:
                        items.append({
                            "title": title,
                            "time": str(row.get("发布时间", "")),
                            "source": "同花顺",
                            "content": content[:200],
                            "url": str(row.get("链接", "")),
                            "channel": "ths",
                        })
                        if len(items) >= 3:
                            break
                return items
        except Exception:
            pass
        return []

    def _news_sina():
        if not stock_name:
            return []
        try:
            sina_df = ak.stock_info_global_sina()
            if sina_df is not None and not sina_df.empty:
                items = []
                for _, row in sina_df.iterrows():
                    content = str(row.get("内容", ""))
                    if stock_name in content or symbol in content:
                        items.append({
                            "title": content[:80],
                            "time": str(row.get("时间", "")),
                            "source": "新浪财经",
                            "content": content[:200],
                            "url": "",
                            "channel": "sina",
                        })
                        if len(items) >= 3:
                            break
                return items
        except Exception:
            pass
        return []

    # Run all fetches in parallel with a 15-second overall timeout.
    # "info" tasks return (key, value) tuples; "news" tasks return item lists.
    _PARALLEL_TIMEOUT = 15  # seconds
    info_tasks = {
        "companyInfo": _fetch_company_info,
        "business": _fetch_business,
        "financials": _fetch_financials,
        "shareholders": _fetch_shareholders,
    }
    news_tasks = {
        "eastmoney": _news_eastmoney,
        "caixin": _news_caixin,
        "research": _news_research,
        "global_em": _news_global_em,
        "cls": _news_cls,
        "ths": _news_ths,
        "sina": _news_sina,
    }
    if section == "profile":
        news_tasks = {}
    elif section == "news":
        info_tasks = {}

    news_list: list[dict] = []
    research_list: list[dict] = []

    pool = ThreadPoolExecutor(max_workers=11)
    futures = {}
    for name, fn in info_tasks.items():
        futures[pool.submit(fn)] = ("info", name)
    for name, fn in news_tasks.items():
        futures[pool.submit(fn)] = ("news", name)

    try:
        for fut in as_completed(futures, timeout=_PARALLEL_TIMEOUT):
            kind, name = futures[fut]
            try:
                val = fut.result(timeout=1)
            except Exception:
                continue
            if kind == "info" and val is not None:
                result[val[0]] = val[1]
            elif kind == "news" and val:
                if name == "research":
                    research_list.extend(val)
                else:
                    news_list.extend(val)
    except TimeoutError:
        pass  # some sources were too slow — continue with what we got
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    # Sort by time (newest first), deduplicate by title
    seen_titles: set[str] = set()
    unique_news: list[dict] = []
    for n in sorted(news_list, key=lambda x: x.get("time", ""), reverse=True):
        t = n["title"].strip()
        if t and t not in seen_titles:
            seen_titles.add(t)
            unique_news.append(n)
    if unique_news:
        result["news"] = unique_news
    if research_list:
        result["research"] = research_list

    return result


def compute_technicals(closes: list[float]) -> dict:
    """Compute basic technical indicators from close prices."""
    n = len(closes)

    def _sma(data, period):
        if len(data) < period:
            return None
        return sum(data[-period:]) / period

    def _ema(data, period):
        if len(data) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(data[:period]) / period
        for v in data[period:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

    def _rsi(data, period=14):
        if len(data) < period + 1:
            return None
        gains = []
        losses = []
        for i in range(1, len(data)):
            d = data[i] - data[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _macd(data, fast=12, slow=26, signal=9):
        if len(data) < slow + signal:
            return None, None, None
        ema_fast = _ema(data, fast)
        ema_slow = _ema(data, slow)
        if ema_fast is None or ema_slow is None:
            return None, None, None
        # Compute full EMA series for MACD line
        k_fast = 2.0 / (fast + 1)
        k_slow = 2.0 / (slow + 1)
        ef = sum(data[:fast]) / fast
        es = sum(data[:slow]) / slow
        macd_line = []
        for i, v in enumerate(data):
            if i < fast:
                ef = sum(data[:i + 1]) / (i + 1) if i > 0 else data[0]
            else:
                ef = v * k_fast + ef * (1 - k_fast)
            if i < slow:
                es = sum(data[:i + 1]) / (i + 1) if i > 0 else data[0]
            else:
                es = v * k_slow + es * (1 - k_slow)
            if i >= slow - 1:
                macd_line.append(ef - es)
        if len(macd_line) < signal:
            return None, None, None
        # Signal line = EMA of MACD line
        k_sig = 2.0 / (signal + 1)
        sig = sum(macd_line[:signal]) / signal
        for v in macd_line[signal:]:
            sig = v * k_sig + sig * (1 - k_sig)
        dif = macd_line[-1]
        histogram = dif - sig
        return round(dif, 4), round(sig, 4), round(histogram, 4)

    ma5 = _sma(closes, 5)
    ma10 = _sma(closes, 10)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60) if n >= 60 else None
    rsi14 = _rsi(closes, 14)
    dif, dea, macd_hist = _macd(closes)

    # Simple signal logic
    price = closes[-1]
    signals = []
    if ma5 and ma10:
        if ma5 > ma10:
            signals.append("MA5上穿MA10(金叉)")
        else:
            signals.append("MA5下穿MA10(死叉)")
    if ma20 and price > ma20:
        signals.append("价格在MA20之上")
    elif ma20:
        signals.append("价格在MA20之下")
    if rsi14 is not None:
        if rsi14 > 70:
            signals.append("RSI超买(>70)")
        elif rsi14 < 30:
            signals.append("RSI超卖(<30)")
    if macd_hist is not None:
        if macd_hist > 0:
            signals.append("MACD柱为正")
        else:
            signals.append("MACD柱为负")

    # Overall recommendation
    bullish = 0
    bearish = 0
    if ma5 and ma10 and ma5 > ma10: bullish += 1
    else: bearish += 1
    if ma20 and price > ma20: bullish += 1
    else: bearish += 1
    if rsi14 and rsi14 < 30: bullish += 1
    elif rsi14 and rsi14 > 70: bearish += 1
    if macd_hist and macd_hist > 0: bullish += 1
    elif macd_hist: bearish += 1

    if bullish > bearish + 1:
        recommendation = "买入"
        rec_color = "green"
    elif bearish > bullish + 1:
        recommendation = "卖出"
        rec_color = "red"
    else:
        recommendation = "观望"
        rec_color = "yellow"

    return {
        "ma5": round(ma5, 2) if ma5 else None,
        "ma10": round(ma10, 2) if ma10 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma60": round(ma60, 2) if ma60 else None,
        "rsi14": round(rsi14, 2) if rsi14 else None,
        "macd": {"dif": dif, "dea": dea, "histogram": macd_hist},
        "signals": signals,
        "recommendation": recommendation,
        "recColor": rec_color,
        "bullish": bullish,
        "bearish": bearish,
    }


if __name__ == "__main__":
    _raw = os.environ.get("QUANT_CONFIG") or sys.stdin.read()
    config = json.loads(_raw)
    action = config.get("action", "detail")

    try:
        if action == "detail":
            result = get_stock_detail(
                symbol=config.get("symbol", ""),
                market=config.get("market", "cn_stock"),
                kline_days=config.get("kline_days", 120),
                kline_interval=config.get("kline_interval", "1d"),
                section=config.get("section", "all"),
            )
        elif action == "history":
            result = get_stock_history(
                config.get("symbol", ""), config.get("market", "cn_stock"),
                config.get("kline_days", 120), config.get("kline_interval", "1d"))
        elif action in ("profile", "news"):
            result = get_stock_supplement(
                symbol=config.get("symbol", ""), market=config.get("market", "cn_stock"),
                section=action, stock_name=config.get("stock_name", ""))
        else:
            result = {"error": f"未知操作: {action}"}
    except Exception as e:
        result = {"error": str(e)}

    output = json.dumps(_sanitize(result), ensure_ascii=False)
    sys.stdout.write(output)
    sys.stdout.flush()
    # Force-exit to avoid waiting for ThreadPoolExecutor daemon threads
    # that may still be blocked on slow HTTP calls.
    os._exit(0)
