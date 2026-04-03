#!/usr/bin/env python3
"""
Batch sync K-line data — callable via Python bridge (stdin JSON → stdout JSON).

Supports three modes:
  1. "bridge"  — Fetch stale/missing data from Windows xtdata bridge (fast, batch)
  2. "akshare" — Fetch stale/missing data from akshare eastmoney (slower, per-stock)
  3. "auto"    — Try bridge first, fall back to akshare if bridge is down

Input (JSON via stdin):
  {
    "action": "sync",
    "source": "auto" | "bridge" | "akshare",
    "symbols": ["600519", "000001"],   // optional: specific symbols (empty=all)
    "max_stocks": 0,                   // optional: limit (0=all)
    "days": 120                        // optional: kline history depth
  }

  action="check" → just check what needs updating, don't fetch
  action="status" → bridge health + cache freshness summary

Output (JSON to stdout):
  { "status": "ok", "source": "bridge"|"akshare",
    "fetched": 42, "failed": 3, "skipped": 100, "total": 145 }
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta

# Add parent dir to path
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PYTHON_ROOT)

# ── Proxy bypass (same as akshare_provider) ───────────────────────
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

WINDOWS_XTDATA_URL = os.environ.get("WINDOWS_XTDATA_URL", "http://127.0.0.1:58088")
BATCH_SIZE = 100
TIMEOUT = 90
AKSHARE_DELAY = 0.35


def _to_qmt_code(stock_id: str) -> str | None:
    """Convert 6-digit code to QMT format (600519 → 600519.SH)."""
    if not stock_id or len(stock_id) != 6:
        return None
    first = stock_id[0]
    if first in ("8", "4"):
        return None
    if first in ("6", "5"):
        return f"{stock_id}.SH"
    if first in ("0", "3"):
        return f"{stock_id}.SZ"
    return None


def _qmt_to_plain(qmt: str) -> str:
    return qmt.split(".")[0]


def check_bridge() -> dict:
    """Check if Windows xtdata bridge is reachable."""
    try:
        req = urllib.request.Request(f"{WINDOWS_XTDATA_URL}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            health = json.loads(resp.read())
            return {"online": True, "url": WINDOWS_XTDATA_URL, "detail": health}
    except Exception as e:
        return {"online": False, "url": WINDOWS_XTDATA_URL, "error": str(e)}


def get_stale_symbols(cache, stock_list: dict, kline_days: int = 120) -> tuple[list[str], int, int, int]:
    """Determine which symbols need refresh.

    Returns: (codes_need_fetch_qmt, fresh_count, stale_count, no_cache_count)
    """
    conn = cache._ensure_conn()
    stale_cutoff = datetime.now() - timedelta(days=2)
    stale_cutoff_date = stale_cutoff.strftime("%Y-%m-%d")

    fresh_symbols: set[str] = set()
    symbol_bar_counts: dict[str, int] = {}

    rows = conn.execute("""
        SELECT symbol, MAX(trade_date) as latest_date, COUNT(*) as bar_count
        FROM kline_data
        WHERE market = 'cn_stock' AND period = 'daily'
        GROUP BY symbol
    """).fetchall()

    for symbol, latest_date, bar_count in rows:
        symbol_bar_counts[symbol] = bar_count
        if latest_date >= stale_cutoff_date and bar_count >= kline_days * 0.6:
            fresh_symbols.add(symbol)

    codes_need_fetch = []
    codes_fresh = 0
    codes_stale = 0
    codes_no_cache = 0

    for sid in stock_list:
        qmt = _to_qmt_code(sid)
        if not qmt:
            continue
        if sid in fresh_symbols:
            codes_fresh += 1
        elif sid in symbol_bar_counts:
            codes_stale += 1
            codes_need_fetch.append(qmt)
        else:
            codes_no_cache += 1
            codes_need_fetch.append(qmt)

    return codes_need_fetch, codes_fresh, codes_stale, codes_no_cache


def fetch_via_bridge(codes: list[str], kline_days: int) -> tuple[dict, int]:
    """Batch fetch from xtdata bridge. Returns (data_dict, failed_count)."""
    result: dict[str, list[dict]] = {}
    failed = 0

    for i in range(0, len(codes), BATCH_SIZE):
        batch = codes[i: i + BATCH_SIZE]
        body = json.dumps(
            {"codes": batch, "period": "1d", "count": kline_days, "timeout": TIMEOUT},
            ensure_ascii=False,
        ).encode("utf-8")

        req = urllib.request.Request(
            f"{WINDOWS_XTDATA_URL}/klines",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT + 10) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
                if resp_data.get("status") != "ok":
                    failed += len(batch)
                    continue
                data = resp_data.get("data", {})
                for qmt_code, bars in data.items():
                    plain = _qmt_to_plain(qmt_code)
                    converted = []
                    for bar in bars:
                        date_str = str(bar.get("date", ""))
                        if len(date_str) == 8:
                            ts = int(datetime.strptime(date_str, "%Y%m%d").timestamp() * 1000)
                        else:
                            continue
                        converted.append({
                            "timestamp": ts,
                            "open": bar.get("open", 0),
                            "high": bar.get("high", 0),
                            "low": bar.get("low", 0),
                            "close": bar.get("close", 0),
                            "volume": bar.get("volume", 0),
                        })
                    if converted:
                        result[plain] = converted
        except Exception:
            failed += len(batch)

    return result, failed


def fetch_via_akshare(codes: list[str], kline_days: int) -> tuple[dict, int, int]:
    """Fetch one-by-one via akshare. Returns (data_dict, failed_count, skipped_count)."""
    from data_providers.akshare_provider import get_klines

    result: dict[str, list[dict]] = {}
    failed = 0
    skipped = 0

    end_date = datetime.now().strftime("%Y%m%d")
    start_dt = datetime.now() - timedelta(days=int(kline_days * 1.5))
    start_date = start_dt.strftime("%Y%m%d")

    for idx, qmt in enumerate(codes, 1):
        plain = _qmt_to_plain(qmt)
        try:
            bars = get_klines(
                symbol=plain, interval="1d",
                start=start_date, end=end_date,
                market="cn_stock",
            )
            if bars and len(bars) > 0:
                result[plain] = bars
            else:
                skipped += 1
        except Exception:
            failed += 1

        if idx < len(codes):
            time.sleep(AKSHARE_DELAY)

    return result, failed, skipped


def do_sync(config: dict) -> dict:
    """Main sync entrypoint."""
    from cache.stock_cache import StockCache

    source = config.get("source", "auto")
    requested_symbols = config.get("symbols", [])
    max_stocks = config.get("max_stocks", 0)
    kline_days = config.get("days", 120)

    cache = StockCache()

    # Get stock list from cache or requested symbols
    if requested_symbols:
        stock_list = {s: {} for s in requested_symbols}
    else:
        stock_list = cache.get_stock_list()
        if not stock_list:
            # Try from kline_data
            conn = cache._ensure_conn()
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM kline_data WHERE market = 'cn_stock'"
            ).fetchall()
            stock_list = {r[0]: {} for r in rows} if rows else {}

    if not stock_list:
        return {"status": "error", "message": "无股票列表，请先执行选股或手动指定代码"}

    # Determine what needs refresh
    codes_need_fetch, fresh, stale, no_cache = get_stale_symbols(cache, stock_list, kline_days)

    if not codes_need_fetch:
        return {
            "status": "ok", "source": "none",
            "message": "所有K线数据已是最新",
            "total": len(stock_list), "fresh": fresh,
            "fetched": 0, "failed": 0, "skipped": 0,
        }

    # Apply limit
    if max_stocks and len(codes_need_fetch) > max_stocks:
        codes_need_fetch = codes_need_fetch[:max_stocks]

    # Check bridge availability
    bridge_info = check_bridge()
    bridge_ok = bridge_info["online"]

    actual_source = source
    if source == "auto":
        actual_source = "bridge" if bridge_ok else "akshare"
    elif source == "bridge" and not bridge_ok:
        return {"status": "error", "message": f"迅投桥接不可达: {bridge_info.get('error', '')}",
                "bridge": bridge_info}

    # Fetch
    fetched = 0
    failed_count = 0
    skipped_count = 0

    if actual_source == "bridge":
        data, fail = fetch_via_bridge(codes_need_fetch, kline_days)
        failed_count = fail
        # Save to cache
        for sid, bars in data.items():
            if bars:
                try:
                    cache.save_klines(sid, bars, "cn_stock", "daily")
                    fetched += 1
                except Exception:
                    failed_count += 1
    else:
        data, fail, skip = fetch_via_akshare(codes_need_fetch, kline_days)
        failed_count = fail
        skipped_count = skip
        fetched = len(data)
        # akshare provider auto-saves to cache inside get_klines()

    return {
        "status": "ok",
        "source": actual_source,
        "bridge": bridge_info,
        "total": len(stock_list),
        "fresh": fresh,
        "stale": stale,
        "no_cache": no_cache,
        "need_fetch": len(codes_need_fetch),
        "fetched": fetched,
        "failed": failed_count,
        "skipped": skipped_count,
    }


def do_check(config: dict) -> dict:
    """Check what needs updating without actually fetching."""
    from cache.stock_cache import StockCache

    requested_symbols = config.get("symbols", [])
    kline_days = config.get("days", 120)
    cache = StockCache()

    if requested_symbols:
        stock_list = {s: {} for s in requested_symbols}
    else:
        stock_list = cache.get_stock_list()
        if not stock_list:
            conn = cache._ensure_conn()
            rows = conn.execute(
                "SELECT DISTINCT symbol FROM kline_data WHERE market = 'cn_stock'"
            ).fetchall()
            stock_list = {r[0]: {} for r in rows} if rows else {}

    if not stock_list:
        return {"status": "ok", "total": 0, "fresh": 0, "stale": 0, "no_cache": 0, "need_fetch": 0}

    codes, fresh, stale, no_cache = get_stale_symbols(cache, stock_list, kline_days)

    bridge_info = check_bridge()

    return {
        "status": "ok",
        "total": len(stock_list),
        "fresh": fresh,
        "stale": stale,
        "no_cache": no_cache,
        "need_fetch": len(codes),
        "bridge": bridge_info,
    }


def do_status() -> dict:
    """Bridge health + cache freshness summary."""
    from cache.stock_cache import StockCache

    bridge_info = check_bridge()
    cache = StockCache()
    stats = cache.stats()

    return {
        "status": "ok",
        "bridge": bridge_info,
        "cache": {
            "db_size_mb": stats.get("db_size_mb", 0),
            "klines": stats.get("klines", {}),
            "stock_list": stats.get("stock_list", {}),
        },
    }


# ── CLI entry (stdin JSON → stdout JSON) ──────────────────────────

if __name__ == "__main__":
    _raw = os.environ.get("QUANT_CONFIG") or sys.stdin.read()
    config = json.loads(_raw)
    action = config.get("action", "sync")

    try:
        if action == "sync":
            result = do_sync(config)
        elif action == "check":
            result = do_check(config)
        elif action == "status":
            result = do_status()
        else:
            result = {"status": "error", "message": f"未知操作: {action}"}
    except Exception as e:
        result = {"status": "error", "message": str(e)}

    print(json.dumps(result, ensure_ascii=False))
