#!/usr/bin/env python3
"""
AKShare data provider bridge.

Fetches market data from akshare (A-share, futures, fund, index).

Input: JSON via stdin or QUANT_CONFIG env var.
Output: JSON to stdout.

Requirements:
  pip install akshare pandas
"""

import json
import os
import sys
import urllib.request
from datetime import datetime

# ── Proxy bypass for Chinese financial data hosts ─────────────────────────────
# On macOS the system proxy (e.g. ClashX on 127.0.0.1:7890) is picked up by
# Python's urllib.request.getproxies() via CoreFoundation.  The proxy may
# reject / close connections to Chinese financial sites (eastmoney, xueqiu,
# etc.), causing RemoteDisconnected errors for all akshare API calls.
#
# Three-layer fix (all MUST be applied BEFORE `requests`/`akshare` import):
#   1. Clear proxy env vars
#   2. Monkeypatch urllib.request.getproxies → {}  (system proxy)
#   3. Monkeypatch requests.utils.getproxies → {}  (cached reference)
#
# This is safe because this module's process only ever talks to CN finance APIs.

# Layer 1: env vars
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

# Layer 2: urllib.request system-proxy detection (macOS CoreFoundation)
urllib.request.getproxies = lambda: {}  # type: ignore[assignment]

# Layer 3: requests caches its own reference at import time
try:
    import requests, requests.utils  # noqa: E401
    requests.utils.getproxies = lambda: {}  # type: ignore[assignment]
    requests.utils.get_environ_proxies = lambda *a, **kw: {}  # type: ignore[assignment]
except ImportError:
    pass


def get_input():
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        return json.loads(config_str)
    return json.loads(sys.stdin.read())


def _xq_session():
    """Create a requests session with Xueqiu cookie for API access."""
    from akshare.stock.cons import xq_a_token
    s = requests.Session()
    s.headers.update({
        "cookie": f"xq_a_token={xq_a_token};",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
                      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
                      "Mobile/15E148 Safari/604.1",
    })
    s.get("https://xueqiu.com")
    return s


def _xq_quote(xq_symbol: str):
    """Fetch raw Xueqiu quote JSON for a symbol (e.g. SH600519, SZ000777, 00777 for HK)."""
    s = _xq_session()
    url = f"https://stock.xueqiu.com/v5/stock/quote.json?symbol={xq_symbol}&extend=detail"
    r = s.get(url, timeout=10)
    data = r.json()
    return data.get("data", {}).get("quote") or {}


def _sina_quote(symbol: str, market: str = "cn_stock"):
    """Fallback: fetch real-time quote via Sina Finance API (no auth needed).

    Sina returns a JS variable like:
      var hq_str_sz000690="宝新能源,4.88,4.90,4.76,4.93,4.73,...";
    Fields: name,open,prevClose,price,high,low,bid1,ask1,volume,amount,...
    """
    try:
        if market == "hk_stock":
            return None  # Sina HK format differs, skip
        code = symbol.zfill(6)
        prefix = "sh" if code.startswith(("6", "5", "9")) else "sz"
        url = f"https://hq.sinajs.cn/list={prefix}{code}"
        r = requests.get(url, timeout=5, headers={
            "Referer": "https://finance.sina.com.cn",
            "User-Agent": "Mozilla/5.0",
        })
        text = r.text.strip()
        if '="' not in text:
            return None
        csv = text.split('="')[1].rstrip('";')
        if not csv:
            return None
        fields = csv.split(",")
        if len(fields) < 10:
            return None
        price = float(fields[3])
        if price <= 0:
            return None
        return {
            "name": fields[0],
            "current": price,
            "open": float(fields[1] or 0),
            "last_close": float(fields[2] or 0),
            "high": float(fields[4] or 0),
            "low": float(fields[5] or 0),
            "volume": int(float(fields[8] or 0)),
            "amount": float(fields[9] or 0),
            "chg": round(price - float(fields[2] or 0), 4),
            "percent": round((price - float(fields[2] or 0)) / float(fields[2] or 1) * 100, 4),
        }
    except Exception as e:
        print(f"[akshare_provider] Sina quote fallback failed: {e}", file=sys.stderr)
        return None


def _to_xq_symbol(symbol: str, market: str) -> str:
    """Convert plain code + market to Xueqiu symbol format.
       600519 + cn_stock → SH600519;  000777 + cn_stock → SZ000777
       0777 + hk_stock → 00777  (bare 5-digit code, no HK prefix — Xueqiu quirk)
    """
    if market == "hk_stock":
        # Xueqiu v5 API returns null for "HK00777" but works with bare "00777"
        return symbol.zfill(5)
    # A-share: 6xx/5xx/9xx = SH (Shanghai), others = SZ (Shenzhen)
    if symbol.startswith(("6", "5", "9")):
        return f"SH{symbol}"
    return f"SZ{symbol}"


def get_quote(symbol, market="cn_stock"):
    """Fetch real-time quote via Xueqiu API (direct HTTP, bypasses broken akshare pandas).

    Uses local SQLite cache for stock name lookups.
    """
    try:
        if market in ("cn_stock", "hk_stock"):
            # Sina first (no auth needed, more stable), Xueqiu as fallback
            q = _sina_quote(symbol, market)
            if not q or q.get("current") is None:
                xq_sym = _to_xq_symbol(symbol, market)
                q = _xq_quote(xq_sym)
            if not q or q.get("current") is None:
                return None

            price = float(q.get("current", 0) or 0)
            prev_close = float(q.get("last_close", 0) or 0)
            change = float(q.get("chg", 0) or 0)
            change_pct = float(q.get("percent", 0) or 0)

            name = q.get("name") or symbol
            display_code = symbol
            if market == "hk_stock" and "." not in symbol:
                display_code = f"{symbol}.HK"

            return {
                "symbol": {"code": display_code, "name": name, "market": market},
                "price": price,
                "change": round(change, 4),
                "changePct": round(change_pct, 4),
                "open": float(q.get("open", 0) or 0),
                "high": float(q.get("high", 0) or 0),
                "low": float(q.get("low", 0) or 0),
                "prevClose": prev_close,
                "volume": int(q.get("volume", 0) or 0),
                "amount": float(q.get("amount", 0) or 0),
                "marketCap": float(q.get("market_capital", 0) or 0),
                "timestamp": int(datetime.now().timestamp() * 1000),
            }

        elif market == "futures":
            import akshare as ak
            df = ak.futures_zh_spot()
            row = df[df["symbol"] == symbol]
            if row.empty:
                return None
            r = row.iloc[0]
            return {
                "symbol": {"code": symbol, "name": str(r.get("name", symbol)), "market": market},
                "price": float(r.get("current_price", 0)),
                "change": float(r.get("change", 0)),
                "changePct": float(r.get("change_percent", 0)),
                "open": float(r.get("open", 0)),
                "high": float(r.get("high", 0)),
                "low": float(r.get("low", 0)),
                "prevClose": float(r.get("prev_close", 0)),
                "volume": int(r.get("volume", 0)),
                "amount": float(r.get("amount", 0)),
                "openInterest": int(r.get("hold", 0)),
                "settlementPrice": float(r.get("settle", 0)),
                "timestamp": int(datetime.now().timestamp() * 1000),
            }

        elif market == "fund":
            df = ak.fund_etf_spot_em()
            row = df[df["代码"] == symbol]
            if row.empty:
                return None
            r = row.iloc[0]
            return {
                "symbol": {"code": symbol, "name": str(r["名称"]), "market": market},
                "price": float(r["最新价"]),
                "change": float(r.get("涨跌额", 0)),
                "changePct": float(r.get("涨跌幅", 0)),
                "open": float(r.get("今开", 0)),
                "high": float(r.get("最高", 0)),
                "low": float(r.get("最低", 0)),
                "prevClose": float(r.get("昨收", 0)),
                "volume": int(r.get("成交量", 0)),
                "amount": float(r.get("成交额", 0)),
                "timestamp": int(datetime.now().timestamp() * 1000),
            }

    except Exception as e:
        print(f"Error fetching quote: {e}", file=sys.stderr)
        return None

    return None


def _cn_ak_symbol(code: str) -> str:
    """Add sh/sz prefix for stock_zh_a_daily (Baidu Finance interface).

    AKShare stock_zh_a_daily requires:
      sh + code  for SSE stocks (codes starting with 6...)
      sz + code  for SZSE stocks (codes starting with 0..., 3...)
    """
    code = code.strip()
    if code.startswith(("sh", "sz", "SH", "SZ")):
        return code.lower()
    if code.startswith(("6", "9")):
        return f"sh{code}"
    return f"sz{code}"


# Well-known A-share index codes (Shanghai: sh prefix, Shenzhen: sz prefix)
_INDEX_CODES = {
    "000001", "000002", "000003", "000016", "000300", "000688", "000852",
    "000903", "000905", "000906", "000985",
    "399001", "399005", "399006", "399106", "399300", "399673",
}


def _index_prefix(code: str) -> str:
    """Return sh/sz prefix for stock_zh_index_daily."""
    if code.startswith("399"):
        return f"sz{code}"
    return f"sh{code}"


def _fetch_index_klines(ak_mod, symbol: str, gap_start: str, gap_end: str) -> list:
    """Fetch index klines via ak.stock_zh_index_daily (Sina API).

    Returns the full history then filters to the requested date range.
    Column schema: date, open, high, low, close, volume (no amount).
    """
    import pandas as pd

    idx_symbol = _index_prefix(symbol)
    df = ak_mod.stock_zh_index_daily(symbol=idx_symbol)
    if df is None or df.empty:
        return []

    df["date"] = pd.to_datetime(df["date"])
    mask = (df["date"] >= gap_start) & (df["date"] <= gap_end)
    df = df.loc[mask]

    bars = []
    for _, row in df.iterrows():
        date_str = str(row["date"])[:10]
        bars.append({
            "timestamp": int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000),
            "open": float(row.get("open", 0)),
            "high": float(row.get("high", 0)),
            "low": float(row.get("low", 0)),
            "close": float(row.get("close", 0)),
            "volume": int(row.get("volume", 0)),
            "amount": 0.0,
        })
    return bars


def _fetch_cn_klines_baidu(ak, symbol: str, period: str, gap_start: str, gap_end: str) -> list:
    """Fallback: fetch A-share klines from Baidu Finance via stock_zh_a_daily.

    Used when the primary eastmoney endpoint (stock_zh_a_hist) is unreachable.
    Only supports daily/weekly/monthly (periodicity maps to 'daily').
    """
    ak_sym = _cn_ak_symbol(symbol)
    df = ak.stock_zh_a_daily(
        symbol=ak_sym,
        adjust="qfq",
        start_date=gap_start.replace("-", ""),
    )
    if df is None or df.empty:
        return []

    # Filter to the requested end date
    end_dt = gap_end.replace("-", "")
    bars = []
    for _, row in df.iterrows():
        date_val = str(row.get("date", ""))[:10]
        if date_val.replace("-", "") > end_dt:
            break
        try:
            bars.append({
                "timestamp": int(datetime.strptime(date_val, "%Y-%m-%d").timestamp() * 1000),
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": int(row.get("volume", 0)),
                "amount": float(row.get("amount", 0)),
            })
        except Exception:
            pass
    return bars


# ── QMT Bridge K-line fetcher ─────────────────────────────────────

_qmt_bridge_url_cache: str | None = None
_qmt_bridge_failed: bool = False  # fast-fail: skip QMT after first connection error


def _read_qmt_bridge_url() -> str:
    """Read QMT windowsBridgeUrl from openclaw config (cached).

    Checks config files in order:
      1. $OPENCLAW_STATE_DIR/openclaw.json  (explicit state dir)
      2. ~/.openclaw-dev/openclaw.json      (dev lane)
      3. ~/.openclaw/openclaw.json          (global lane)
    Returns the first non-empty windowsBridgeUrl found.
    """
    global _qmt_bridge_url_cache
    if _qmt_bridge_url_cache is not None:
        return _qmt_bridge_url_cache

    home = os.path.expanduser("~")
    candidates = []
    state_dir = os.environ.get("OPENCLAW_STATE_DIR", "")
    if state_dir:
        candidates.append(os.path.join(state_dir, "openclaw.json"))
    candidates.append(os.path.join(home, ".openclaw-dev", "openclaw.json"))
    candidates.append(os.path.join(home, ".openclaw", "openclaw.json"))

    for config_path in candidates:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            url = (cfg.get("plugins", {})
                   .get("entries", {})
                   .get("quant-trading", {})
                   .get("config", {})
                   .get("tradingAccount", {})
                   .get("qmt", {})
                   .get("windowsBridgeUrl", ""))
            if url:
                _qmt_bridge_url_cache = url.rstrip("/")
                return _qmt_bridge_url_cache
        except Exception:
            continue

    _qmt_bridge_url_cache = ""
    return _qmt_bridge_url_cache


def _fetch_qmt_klines(symbol: str, start: str, end: str, market: str = "cn_stock") -> list:
    """Fetch K-lines from QMT Windows bridge via HTTP.

    Bridge URL resolution order:
      1. WINDOWS_XTDATA_URL env var (set by TypeScript bridge)
      2. openclaw config files (~/.openclaw-dev, ~/.openclaw)

    Returns list of bar dicts, or empty list if bridge unavailable.
    """
    global _qmt_bridge_failed
    if _qmt_bridge_failed:
        return []

    bridge_url = os.environ.get("WINDOWS_XTDATA_URL", "").rstrip("/")
    if not bridge_url:
        bridge_url = _read_qmt_bridge_url()
    if not bridge_url:
        return []

    try:
        # Convert symbol to QMT format: 600711 → 600711.SH
        if market in ("cn_stock", "index"):
            if symbol.startswith(("6", "5", "9")):
                qmt_code = f"{symbol}.SH"
            else:
                qmt_code = f"{symbol}.SZ"
        else:
            qmt_code = symbol

        # Estimate count from date range (dates already normalized to %Y-%m-%d by get_klines)
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        days_diff = max(1, (end_dt - start_dt).days)
        count = days_diff + 10  # small buffer

        resp = requests.post(
            f"{bridge_url}/klines",
            json={"codes": [qmt_code], "period": "1d", "count": count},
            timeout=15,
        )
        data = resp.json()
        if data.get("status") != "ok":
            return []

        raw_bars = data.get("data", {}).get(qmt_code, [])
        if not raw_bars:
            # Try with plain symbol as key (bridge may return without .SH/.SZ suffix)
            raw_bars = data.get("data", {}).get(symbol, [])
        if not raw_bars:
            # Try with plain symbol as key
            raw_bars = list(data.get("data", {}).values())
            raw_bars = raw_bars[0] if raw_bars else []

        bars = []
        for b in raw_bars:
            date_str = str(b.get("date", ""))
            if len(date_str) == 8:
                ts_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
            elif len(date_str) >= 10:
                ts_str = date_str[:10]
            else:
                continue

            if ts_str < start or ts_str > end:
                continue

            close_val = float(b.get("close", 0))
            if close_val <= 0:
                continue

            bars.append({
                "timestamp": int(datetime.strptime(ts_str, "%Y-%m-%d").timestamp() * 1000),
                "open": float(b.get("open", 0)),
                "high": float(b.get("high", 0)),
                "low": float(b.get("low", 0)),
                "close": close_val,
                "volume": int(b.get("volume", 0)),
                "amount": float(b.get("amount", 0)),
            })
        return bars
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        _qmt_bridge_failed = True
        print(f"[QMT] 桥接连接失败，本次运行跳过QMT: {e}", file=sys.stderr)
        return []
    except Exception as e:
        print(f"[QMT] K线获取失败: {e}", file=sys.stderr)
        return []


# ── TDX (通达信) K-line fetcher ───────────────────────────────────

_TDX_KLINE_SERVERS = [
    ("180.153.18.170", 7709),
    ("60.12.136.250", 7709),
    ("218.75.126.9", 7709),
    ("119.147.212.81", 7709),
    ("101.227.73.20", 7709),
    ("14.215.128.18", 7709),
    ("59.173.18.140", 7709),
]


def _fetch_tdx_klines(symbol: str, start: str, end: str, market: str = "cn_stock") -> list:
    """Fetch daily K-lines from 通达信 public servers via pytdx.

    Uses get_security_bars (category=9 for daily bars).
    Returns list of bar dicts, or empty list on failure.
    """
    try:
        from pytdx.hq import TdxHq_API
    except ImportError:
        return []

    # Determine TDX market code: 0=SZ, 1=SH
    if symbol.startswith(("6", "5", "9")):
        tdx_market = 1  # SH
    else:
        tdx_market = 0  # SZ

    api = TdxHq_API()
    connected = False
    for host, port in _TDX_KLINE_SERVERS:
        try:
            ok = api.connect(host, port, time_out=5)
            if ok:
                connected = True
                break
        except Exception:
            continue

    if not connected:
        return []

    try:
        start_dt = datetime.strptime(start, "%Y-%m-%d")
        end_dt = datetime.strptime(end, "%Y-%m-%d")
        # Normalize for comparisons below
        start = start_dt.strftime("%Y-%m-%d")
        end = end_dt.strftime("%Y-%m-%d")
        days_needed = (end_dt - start_dt).days + 10

        # pytdx get_security_bars: category 9=daily, max 800 bars per call
        all_rows = []
        offset = 0
        while offset < days_needed:
            batch_size = min(800, days_needed - offset)
            rows = api.get_security_bars(9, tdx_market, symbol, offset, batch_size)
            if not rows:
                break
            all_rows.extend(rows)
            if len(rows) < batch_size:
                break
            offset += batch_size

        api.disconnect()

        bars = []
        for row in all_rows:
            date_str = str(row.get("datetime", ""))[:10]
            if date_str < start or date_str > end:
                continue

            close_val = float(row.get("close", 0))
            if close_val <= 0:
                continue

            bars.append({
                "timestamp": int(datetime.strptime(date_str, "%Y-%m-%d").timestamp() * 1000),
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": close_val,
                "volume": int(row.get("vol", row.get("volume", 0))),
                "amount": float(row.get("amount", 0)),
            })
        return bars
    except Exception as e:
        print(f"[通达信] K线获取失败: {e}", file=sys.stderr)
        try:
            api.disconnect()
        except Exception:
            pass
        return []


def get_klines(symbol, interval, start, end, market="cn_stock"):
    """Fetch historical kline data with local SQLite caching.

    Priority chain for cn_stock:
      1. Cache (SQLite)  — permanent for historical bars, short TTL for today
      2. QMT bridge      — if WINDOWS_XTDATA_URL env is set
      3. TDX (通达信)    — free pytdx public servers
      4. EastMoney       — stock_zh_a_hist (akshare)
      5. Baidu Finance   — stock_zh_a_daily (akshare fallback)
    """
    import akshare as ak

    # Normalize dates to %Y-%m-%d (cache, QMT, TDX all use this format)
    def _norm_date(d: str) -> str:
        d = d.strip()
        if len(d) == 8 and d.isdigit():
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        return d[:10]  # already %Y-%m-%d or longer

    start = _norm_date(start)
    end = _norm_date(end)

    period_map = {"1d": "daily", "1w": "weekly", "1M": "monthly"}
    period = period_map.get(interval, "daily")

    # Try cache first
    try:
        from cache.stock_cache import get_cache
        cache = get_cache()
        cached_bars, cached_start, cached_end = cache.get_klines(
            symbol, market, period, start, end
        )

        # Check if cache fully covers the requested range
        if cached_bars and cached_start <= start and cached_end >= end:
            print(f"[缓存] K线命中: {symbol} {start}~{end} ({len(cached_bars)} bars)", file=sys.stderr)
            return cached_bars

        # Determine what gaps to fill
        gaps = cache.get_klines_missing_range(symbol, start, end, market, period)
    except Exception as e:
        print(f"[缓存] K线缓存读取失败: {e}", file=sys.stderr)
        cached_bars = []
        gaps = [(start, end)]

    try:
        all_new_bars = []
        for gap_start, gap_end in gaps:
            if market == "cn_stock" or market == "index":
                # For known index codes, try dedicated index API first
                if symbol in _INDEX_CODES or market == "index":
                    try:
                        idx_bars = _fetch_index_klines(ak, symbol, gap_start, gap_end)
                        if idx_bars:
                            print(f"[指数接口] {symbol} 获取 {len(idx_bars)} 条数据", file=sys.stderr)
                            all_new_bars.extend(idx_bars)
                            continue
                    except Exception as e_idx:
                        print(f"[指数接口] {symbol} 获取失败: {e_idx}", file=sys.stderr)

                # Priority 1: QMT bridge (if WINDOWS_XTDATA_URL configured)
                try:
                    qmt_bars = _fetch_qmt_klines(symbol, gap_start, gap_end, market)
                    if qmt_bars:
                        print(f"[QMT] {symbol} 获取 {len(qmt_bars)} 条数据", file=sys.stderr)
                        all_new_bars.extend(qmt_bars)
                        continue
                except Exception as e_qmt:
                    print(f"[QMT] {symbol} 获取失败: {e_qmt}", file=sys.stderr)

                # Priority 2: TDX (通达信 pytdx public servers)
                try:
                    tdx_bars = _fetch_tdx_klines(symbol, gap_start, gap_end, market)
                    if tdx_bars:
                        print(f"[通达信] {symbol} 获取 {len(tdx_bars)} 条数据", file=sys.stderr)
                        all_new_bars.extend(tdx_bars)
                        continue
                except Exception as e_tdx:
                    print(f"[通达信] {symbol} 获取失败: {e_tdx}", file=sys.stderr)

                # Priority 3: eastmoney via stock_zh_a_hist
                df = None
                try:
                    df = ak.stock_zh_a_hist(
                        symbol=symbol,
                        period=period,
                        start_date=gap_start.replace("-", ""),
                        end_date=gap_end.replace("-", ""),
                        adjust="qfq",
                    )
                except Exception as e_primary:
                    print(f"[东财接口] {symbol} 获取失败: {e_primary}", file=sys.stderr)

                if df is None or df.empty:
                    # Priority 4: Baidu Finance via stock_zh_a_daily
                    try:
                        baidu_bars = _fetch_cn_klines_baidu(ak, symbol, period, gap_start, gap_end)
                        if baidu_bars:
                            print(f"[百度金融] {symbol} 获取 {len(baidu_bars)} 条数据", file=sys.stderr)
                            all_new_bars.extend(baidu_bars)
                    except Exception as e_fallback:
                        print(f"[百度金融] {symbol} 也失败: {e_fallback}", file=sys.stderr)
                    continue

                for _, row in df.iterrows():
                    date_val = row.get("日期", row.get("date", ""))
                    all_new_bars.append({
                        "timestamp": int(datetime.strptime(str(date_val)[:10], "%Y-%m-%d").timestamp() * 1000),
                        "open": float(row.get("开盘", row.get("open", 0))),
                        "high": float(row.get("最高", row.get("high", 0))),
                        "low": float(row.get("最低", row.get("low", 0))),
                        "close": float(row.get("收盘", row.get("close", 0))),
                        "volume": int(row.get("成交量", row.get("volume", 0))),
                        "amount": float(row.get("成交额", row.get("amount", 0))),
                    })
                continue  # primary processed — skip shared block below
            elif market == "futures":
                # Futures klines via akshare
                # Extract product code for main contract (e.g. "RB2505" → "RB0", "IF2503" → "IF0")
                product_code = ''.join(c for c in symbol if c.isalpha())
                try:
                    # Try specific contract first (e.g. RB2505)
                    df = ak.futures_zh_daily_sina(symbol=symbol)
                except Exception:
                    try:
                        # Fallback to main continuous contract (e.g. RB0)
                        df = ak.futures_zh_daily_sina(symbol=f"{product_code}0")
                    except Exception as e_fut:
                        print(f"[期货K线] {symbol} 获取失败: {e_fut}", file=sys.stderr)
                        continue

                if df is not None and not df.empty:
                    for _, row in df.iterrows():
                        date_val = row.get("date", row.get("日期", ""))
                        ts_str = str(date_val)[:10]
                        # Filter by date range
                        if ts_str < gap_start or ts_str > gap_end:
                            continue
                        all_new_bars.append({
                            "timestamp": int(datetime.strptime(ts_str, "%Y-%m-%d").timestamp() * 1000),
                            "open": float(row.get("open", 0)),
                            "high": float(row.get("high", 0)),
                            "low": float(row.get("low", 0)),
                            "close": float(row.get("close", 0)),
                            "volume": int(row.get("volume", 0)),
                            "amount": float(row.get("hold", 0)),  # hold = open interest (no amount field)
                        })
                continue
            elif market == "fund":
                df = ak.fund_etf_hist_em(
                    symbol=symbol,
                    period=period,
                    start_date=gap_start.replace("-", ""),
                    end_date=gap_end.replace("-", ""),
                    adjust="qfq",
                )
            else:
                continue

            if df is None or df.empty:
                continue

            for _, row in df.iterrows():
                date_val = row.get("日期", row.get("date", ""))
                all_new_bars.append({
                    "timestamp": int(datetime.strptime(str(date_val)[:10], "%Y-%m-%d").timestamp() * 1000),
                    "open": float(row.get("开盘", row.get("open", 0))),
                    "high": float(row.get("最高", row.get("high", 0))),
                    "low": float(row.get("最低", row.get("low", 0))),
                    "close": float(row.get("收盘", row.get("close", 0))),
                    "volume": int(row.get("成交量", row.get("volume", 0))),
                    "amount": float(row.get("成交额", row.get("amount", 0))),
                })

        # Save new bars to cache
        if all_new_bars:
            try:
                cache = get_cache()
                cache.save_klines(symbol, all_new_bars, market, period)
            except Exception as e:
                print(f"[缓存] K线缓存保存失败: {e}", file=sys.stderr)

        # Merge cached + new bars, sorted by timestamp
        merged = cached_bars + all_new_bars
        merged.sort(key=lambda b: b["timestamp"])
        # Deduplicate by timestamp
        seen = set()
        result = []
        for bar in merged:
            ts = bar["timestamp"]
            if ts not in seen:
                seen.add(ts)
                result.append(bar)
        return result

    except Exception as e:
        print(f"Error fetching klines: {e}", file=sys.stderr)
        # Return cached data if available even on fetch error
        return cached_bars if cached_bars else []


def _xq_search(keyword: str, size: int = 10) -> list:
    """Search stocks via Xueqiu API. Returns results for A-share, HK, and US."""
    try:
        s = _xq_session()
        url = "https://xueqiu.com/query/v1/search/web/stock.json"
        r = s.get(url, params={"q": keyword, "size": size}, timeout=10)
        data = r.json()
        results = []
        for item in data.get("list", []):
            code = item.get("code", "")
            name = item.get("name", "")
            # Determine market from code format
            if code.startswith(("SH", "SZ")):
                plain = code[2:]
                market = "cn_stock"
                exchange = "SSE" if code.startswith("SH") else "SZSE"
            elif code.isdigit() and len(code) == 5:
                plain = code
                market = "hk_stock"
                exchange = "HKEX"
            elif code.isalpha():
                plain = code
                market = "us_stock"
                exchange = "US"
            else:
                continue  # skip funds, bonds, indices, warrants
            results.append({"code": plain, "name": name, "market": market, "exchange": exchange})
        return results
    except Exception as e:
        print(f"Xueqiu search error: {e}", file=sys.stderr)
        return []


def search_symbols(keyword, market=None):
    """Search for symbols via Xueqiu API (all markets) + futures fallback."""
    try:
        results = []

        # Search futures contracts (Xueqiu doesn't cover futures well)
        if market is None or market == "futures":
            results.extend(_search_futures(keyword))
            if market == "futures":
                return results

        # Xueqiu universal search — covers A-share, HK, US in one call
        xq_hits = _xq_search(keyword, size=10)
        if market:
            xq_hits = [h for h in xq_hits if h["market"] == market]
        results.extend(xq_hits)

        # Fallback: if no Xueqiu results for cn_stock, try cached code-name map
        if not results and (market is None or market == "cn_stock"):
            try:
                from cache.stock_cache import get_cache
                cache = get_cache()
                code_name_map = cache.get_code_name_map()
                if code_name_map:
                    for code, name in code_name_map.items():
                        if keyword in name or keyword in code:
                            results.append({
                                "code": code,
                                "name": name,
                                "market": "cn_stock",
                                "exchange": "SSE" if code.startswith("6") else "SZSE",
                            })
                            if len(results) >= 20:
                                break
            except Exception:
                pass

        return results

    except Exception as e:
        print(f"Error searching: {e}", file=sys.stderr)
        return []


# ── Exchange code mapping for futures ────────────────────────────

# Product code → exchange mapping for Chinese futures
_FUTURES_EXCHANGE_MAP = {
    # CFFEX (中金所) — financial futures
    "IF": "CFFEX", "IC": "CFFEX", "IH": "CFFEX", "IM": "CFFEX",
    "T": "CFFEX", "TF": "CFFEX", "TS": "CFFEX", "TL": "CFFEX",
    # SHFE (上期所) — metals & energy
    "CU": "SHFE", "AL": "SHFE", "ZN": "SHFE", "PB": "SHFE",
    "NI": "SHFE", "SN": "SHFE", "AU": "SHFE", "AG": "SHFE",
    "RB": "SHFE", "WR": "SHFE", "HC": "SHFE", "SS": "SHFE",
    "BU": "SHFE", "RU": "SHFE", "FU": "SHFE", "SP": "SHFE",
    "AO": "SHFE", "BR": "SHFE",
    # DCE (大商所) — agricultural & industrial
    "A": "DCE", "B": "DCE", "M": "DCE", "Y": "DCE",
    "C": "DCE", "CS": "DCE", "P": "DCE", "J": "DCE",
    "JM": "DCE", "I": "DCE", "V": "DCE", "L": "DCE",
    "PP": "DCE", "EG": "DCE", "EB": "DCE", "PG": "DCE",
    "RR": "DCE", "JD": "DCE", "FB": "DCE", "BB": "DCE",
    "LH": "DCE",
    # CZCE (郑商所) — agricultural & chemical
    "CF": "CZCE", "SR": "CZCE", "TA": "CZCE", "MA": "CZCE",
    "OI": "CZCE", "RM": "CZCE", "FG": "CZCE", "ZC": "CZCE",
    "SA": "CZCE", "UR": "CZCE", "AP": "CZCE", "CJ": "CZCE",
    "PF": "CZCE", "PK": "CZCE", "SH": "CZCE", "SF": "CZCE",
    "SM": "CZCE", "PX": "CZCE",
    # INE (能源中心)
    "SC": "INE", "LU": "INE", "NR": "INE", "BC": "INE",
    "EC": "INE",
    # GFEX (广期所)
    "SI": "GFEX", "LC": "GFEX",
}

# Product code → Chinese name mapping for common futures contracts
_FUTURES_NAME_MAP = {
    "IF": "沪深300股指", "IC": "中证500股指", "IH": "上证50股指", "IM": "中证1000股指",
    "T": "10年期国债", "TF": "5年期国债", "TS": "2年期国债", "TL": "30年期国债",
    "CU": "ͭ", "AL": "铝", "ZN": "п", "PB": "Ǧ",
    "NI": "镍", "SN": "锡", "AU": "黄金", "AG": "白银",
    "RB": "螺纹钢", "HC": "热卷", "SS": "不锈钢", "WR": "线材",
    "BU": "沥青", "RU": "天然橡胶", "FU": "燃料油", "SP": "纸浆",
    "A": "豆一", "B": "豆二", "M": "豆粕", "Y": "豆油",
    "C": "玉米", "CS": "玉米淀粉", "P": "棕榈油", "J": "焦炭",
    "JM": "焦煤", "I": "铁矿石", "V": "PVC", "L": "聚乙烯",
    "PP": "聚丙烯", "EG": "乙二醇", "EB": "苯乙烯", "PG": "液化石油气",
    "JD": "鸡蛋", "LH": "生猪", "RR": "粳米",
    "CF": "棉花", "SR": "白糖", "TA": "PTA", "MA": "甲醇",
    "OI": "菜籽油", "RM": "菜粕", "FG": "玻璃", "ZC": "动力煤",
    "SA": "纯碱", "UR": "尿素", "AP": "苹果", "CJ": "红枣",
    "PF": "短纤", "PK": "花生", "SH": "烧碱", "PX": "对二甲苯",
    "SC": "原油", "LU": "低硫燃油", "NR": "20号胶", "BC": "国际铜",
    "EC": "集运指数", "SI": "工业硅", "LC": "碳酸锂",
}


def _search_futures(keyword):
    """Search futures contracts by product code or Chinese name."""
    import re
    keyword_upper = keyword.upper()
    results = []

    # First try matching against the static product-name map
    for code, name in _FUTURES_NAME_MAP.items():
        if keyword in name or keyword_upper == code or keyword_upper in code:
            exchange = _FUTURES_EXCHANGE_MAP.get(code, "")
            results.append({
                "code": code,
                "name": f"{name}期货",
                "market": "futures",
                "exchange": exchange,
            })
            if len(results) >= 20:
                return results

    # If no static match or fewer than 5 results, try akshare for main contracts
    if len(results) < 5:
        try:
            import akshare as ak
            df = ak.futures_display_main_sina()
            if df is not None and not df.empty:
                # Columns: symbol, …  — try to match keyword
                for _, row in df.iterrows():
                    symbol = str(row.get("symbol", ""))
                    name = str(row.get("name", str(row.get("symbol", ""))))
                    prod = re.sub(r"\d+", "", symbol).upper()
                    if keyword in name or keyword_upper in symbol.upper() or keyword_upper in prod:
                        already = any(r["code"] == prod for r in results)
                        if not already:
                            results.append({
                                "code": symbol,
                                "name": name,
                                "market": "futures",
                                "exchange": _FUTURES_EXCHANGE_MAP.get(prod, ""),
                            })
                            if len(results) >= 20:
                                break
        except Exception as e:
            print(f"[futures search] akshare fallback failed: {e}", file=sys.stderr)

    return results


if __name__ == "__main__":
    config = get_input()
    action = config.get("action")

    if action == "quote":
        result = get_quote(config["symbol"], config.get("market", "cn_stock"))
    elif action == "klines":
        result = get_klines(
            config["symbol"],
            config.get("interval", "1d"),
            config.get("start", "20240101"),
            config.get("end", datetime.now().strftime("%Y%m%d")),
            config.get("market", "cn_stock"),
        )
    elif action == "search":
        result = search_symbols(config.get("keyword", ""), config.get("market"))
    else:
        result = {"error": f"Unknown action: {action}"}

    print(json.dumps(result, ensure_ascii=False))
