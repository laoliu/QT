#!/usr/bin/env python3
"""
Auction data provider — fetches call auction (集合竞价) phase data for A-shares.

Supports two data sources:
  1. AKShare (akshare) — via Sina real-time quote API
  2. QMT Bridge — via Windows xtquant data bridge HTTP API

Input: JSON via stdin or QUANT_CONFIG env var.
Output: JSON to stdout.

Actions:
  auction_quote  — get current auction phase data for specified symbols
  auction_batch  — batch fetch auction data for a list of symbols

Requirements:
  pip install akshare requests pandas
"""

import json
import os
import sys
import urllib.request
from datetime import datetime

# ── Proxy bypass (same as akshare_provider.py) ───────────────────────────────
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

urllib.request.getproxies = lambda: {}  # type: ignore[assignment]

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


def _to_xq_symbol(code: str) -> str:
    """Convert plain A-share code to Xueqiu-style symbol (SH600519, SZ000858)."""
    code = code.strip().replace(".SH", "").replace(".SZ", "").replace(".SS", "")
    if code.startswith(("6", "9", "5")):
        return f"SH{code}"
    else:
        return f"SZ{code}"


def _to_sina_symbol(code: str) -> str:
    """Convert plain A-share code to Sina-style symbol (sh600519, sz000858)."""
    code = code.strip().replace(".SH", "").replace(".SZ", "").replace(".SS", "")
    if code.startswith(("6", "9", "5")):
        return f"sh{code}"
    else:
        return f"sz{code}"


def fetch_auction_via_sina(symbols: list) -> list:
    """
    Fetch real-time auction data via Sina HTTP API (hq.sinajs.cn).
    This works during pre-market (9:15-9:25) and returns auction-stage data.

    Sina fields:
      0: name, 1: open, 2: prev_close, 3: current_price, 4: high, 5: low,
      6: bid1, 7: ask1, 8: volume, 9: amount,
      10-14: bid volumes (5 levels), 15-19: bid prices (5 levels),
      20-24: ask volumes (5 levels), 25-29: ask prices (5 levels),
      30: date, 31: time
    """
    if not symbols:
        return []

    sina_symbols = [_to_sina_symbol(s) for s in symbols]
    url = f"https://hq.sinajs.cn/list={','.join(sina_symbols)}"
    headers = {
        "Referer": "https://finance.sina.com.cn",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.encoding = "gbk"
        lines = resp.text.strip().split("\n")
    except Exception as e:
        return [{"error": str(e)}]

    results = []
    for i, line in enumerate(lines):
        if '="' not in line:
            continue
        raw_code = symbols[i] if i < len(symbols) else "unknown"
        data = line.split('="')[1].rstrip('";').split(",")
        if len(data) < 32:
            results.append({"symbol": raw_code, "error": "数据不完整"})
            continue

        try:
            name = data[0]
            prev_close = float(data[2]) if data[2] else 0
            price = float(data[3]) if data[3] else 0
            volume = int(float(data[8])) if data[8] else 0
            amount = float(data[9]) if data[9] else 0

            # Bid/ask data for ratio calculation
            bid_vol_total = sum(float(data[j]) for j in range(10, 15) if data[j])
            ask_vol_total = sum(float(data[j]) for j in range(20, 25) if data[j])
            bid_ask_ratio = (bid_vol_total / ask_vol_total) if ask_vol_total > 0 else 0

            change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0

            results.append({
                "symbol": raw_code,
                "name": name,
                "price": round(price, 2),
                "prev_close": round(prev_close, 2),
                "change_pct": round(change_pct, 2),
                "volume": volume,
                "amount": round(amount, 2),
                "bid_ask_ratio": round(bid_ask_ratio, 2),
                "bid_vol_total": int(bid_vol_total),
                "ask_vol_total": int(ask_vol_total),
                "time": data[31] if len(data) > 31 else "",
            })
        except (ValueError, IndexError) as e:
            results.append({"symbol": raw_code, "error": str(e)})

    return results


def fetch_auction_via_qmt(symbols: list, bridge_url: str) -> list:
    """
    Fetch auction data from QMT Windows bridge.
    The bridge exposes POST /quote endpoint that returns real-time quotes
    including auction phase data.
    """
    if not symbols or not bridge_url:
        return []

    results = []
    for symbol in symbols:
        try:
            resp = requests.post(
                f"{bridge_url.rstrip('/')}/quote",
                json={"symbol": symbol},
                timeout=10,
            )
            data = resp.json()
            if data.get("ok") and data.get("data"):
                q = data["data"]
                prev_close = q.get("prev_close", 0)
                price = q.get("price", 0)
                change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
                results.append({
                    "symbol": symbol,
                    "name": q.get("name", ""),
                    "price": round(price, 2),
                    "prev_close": round(prev_close, 2),
                    "change_pct": round(change_pct, 2),
                    "volume": q.get("volume", 0),
                    "amount": q.get("amount", 0),
                    "bid_ask_ratio": q.get("bid_ask_ratio", 0),
                    "time": q.get("time", ""),
                })
            else:
                results.append({"symbol": symbol, "error": data.get("error", "获取失败")})
        except Exception as e:
            results.append({"symbol": symbol, "error": str(e)})

    return results


def main():
    args = get_input()
    action = args.get("action", "auction_quote")
    symbols = args.get("symbols", [])
    bridge_url = os.environ.get("WINDOWS_XTDATA_URL", args.get("bridge_url", ""))

    if isinstance(symbols, str):
        symbols = [s.strip() for s in symbols.split(",") if s.strip()]

    if not symbols:
        print(json.dumps({"ok": False, "error": "请提供 symbols 参数"}))
        return

    try:
        if action in ("auction_quote", "auction_batch"):
            # Try QMT bridge first if available, fallback to Sina
            if bridge_url:
                data = fetch_auction_via_qmt(symbols, bridge_url)
            else:
                data = fetch_auction_via_sina(symbols)

            print(json.dumps({
                "ok": True,
                "data": {
                    "source": "qmt" if bridge_url else "sina",
                    "count": len(data),
                    "quotes": data,
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
            }, ensure_ascii=False))
        else:
            print(json.dumps({"ok": False, "error": f"未知操作: {action}"}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
