#!/usr/bin/env python3
"""
Tushare data provider bridge.

Requirements:
  pip install tushare pandas
"""

import json
import os
import sys
from datetime import datetime


def get_input():
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        return json.loads(config_str)
    return json.loads(sys.stdin.read())


def get_tushare_api(token):
    """Initialize tushare API."""
    import tushare as ts
    ts.set_token(token)
    return ts.pro_api()


def get_quote(symbol, market, token):
    """Fetch real-time quote via tushare."""
    if not token:
        return {"error": "Tushare token not configured"}

    pro = get_tushare_api(token)

    try:
        # Convert symbol format: 600519 -> 600519.SH
        ts_code = f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"
        df = pro.daily(ts_code=ts_code, start_date=datetime.now().strftime("%Y%m%d"))

        if df is None or df.empty:
            return None

        r = df.iloc[0]
        return {
            "symbol": {"code": symbol, "name": symbol, "market": market},
            "price": float(r["close"]),
            "change": float(r["change"]),
            "changePct": float(r["pct_chg"]),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "prevClose": float(r["pre_close"]),
            "volume": int(r["vol"]),
            "amount": float(r["amount"]),
            "timestamp": int(datetime.now().timestamp() * 1000),
        }
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return None


def get_klines(symbol, interval, start, end, market, token):
    """Fetch historical kline data via tushare."""
    if not token:
        return []

    pro = get_tushare_api(token)

    try:
        ts_code = f"{symbol}.SH" if symbol.startswith("6") else f"{symbol}.SZ"
        df = pro.daily(
            ts_code=ts_code,
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
        )

        if df is None or df.empty:
            return []

        bars = []
        for _, row in df.iterrows():
            trade_date = str(row["trade_date"])
            bars.append({
                "timestamp": int(datetime.strptime(trade_date, "%Y%m%d").timestamp() * 1000),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["vol"]),
                "amount": float(row["amount"]),
            })

        bars.reverse()  # tushare returns newest first
        return bars

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return []


def search_symbols(keyword, market, token):
    """Search symbols via tushare."""
    if not token:
        return []

    pro = get_tushare_api(token)

    try:
        df = pro.stock_basic(exchange="", list_status="L",
                             fields="ts_code,symbol,name,area,industry,market")
        matched = df[df["name"].str.contains(keyword, na=False) | df["symbol"].str.contains(keyword, na=False)]

        results = []
        for _, row in matched.head(20).iterrows():
            results.append({
                "code": str(row["symbol"]),
                "name": str(row["name"]),
                "market": "cn_stock",
                "exchange": "SSE" if str(row["symbol"]).startswith("6") else "SZSE",
            })

        return results

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return []


if __name__ == "__main__":
    config = get_input()
    action = config.get("action")
    token = config.get("token", "")

    if action == "quote":
        result = get_quote(config["symbol"], config.get("market", "cn_stock"), token)
    elif action == "klines":
        result = get_klines(
            config["symbol"],
            config.get("interval", "1d"),
            config.get("start", "20240101"),
            config.get("end", datetime.now().strftime("%Y%m%d")),
            config.get("market", "cn_stock"),
            token,
        )
    elif action == "search":
        result = search_symbols(config.get("keyword", ""), config.get("market"), token)
    else:
        result = {"error": f"Unknown action: {action}"}

    print(json.dumps(result, ensure_ascii=False))
