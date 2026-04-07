#!/usr/bin/env python3
"""
Tushare data provider bridge.

Fetches market data from Tushare Pro API.
Supports A-shares (daily/weekly/monthly), indices, ETFs, HK connect stocks.

Input: JSON via stdin or QUANT_CONFIG env var.
Output: JSON to stdout.

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


_ts_pro_api = None


def get_tushare_api(token):
    """Initialize tushare API (cached per process)."""
    global _ts_pro_api
    if _ts_pro_api is not None:
        return _ts_pro_api
    import tushare as ts
    ts.set_token(token)
    _ts_pro_api = ts.pro_api()
    return _ts_pro_api


def _to_ts_code(symbol: str, market: str = "cn_stock") -> str:
    """Convert plain symbol to Tushare format.

    600519 → 600519.SH;  000858 → 000858.SZ
    """
    code = symbol.strip().replace(".SS", "").replace(".SH", "").replace(".SZ", "")

    if market == "index":
        if code.startswith("399"):
            return f"{code}.SZ"
        return f"{code}.SH"

    if market == "fund":
        if code.startswith(("5", "1")):
            return f"{code}.SH"
        return f"{code}.SZ"

    # cn_stock
    if code.startswith(("6", "5", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


def get_quote(symbol, market="cn_stock", token=""):
    """Fetch real-time quote via tushare."""
    if not token:
        return {"error": "Tushare token not configured"}

    pro = get_tushare_api(token)

    try:
        ts_code = _to_ts_code(symbol, market)
        today = datetime.now().strftime("%Y%m%d")
        df = pro.daily(ts_code=ts_code, start_date=today, end_date=today)

        if df is None or df.empty:
            # Try previous trading day
            df = pro.daily(ts_code=ts_code, end_date=today, limit=1)

        if df is None or df.empty:
            return None

        r = df.iloc[0]
        price = float(r["close"])
        prev_close = float(r["pre_close"])

        # Try to get stock name
        name = symbol
        try:
            info = pro.stock_basic(ts_code=ts_code, fields="name")
            if info is not None and not info.empty:
                name = str(info.iloc[0]["name"])
        except Exception:
            pass

        return {
            "symbol": {"code": symbol, "name": name, "market": market},
            "price": price,
            "change": round(float(r["change"]), 4),
            "changePct": round(float(r["pct_chg"]), 4),
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "prevClose": prev_close,
            "volume": int(r["vol"] * 100),  # tushare vol in 手, convert to 股
            "amount": float(r["amount"] * 1000),  # tushare amount in 千元, convert to 元
            "timestamp": int(datetime.now().timestamp() * 1000),
        }
    except Exception as e:
        print(f"[tushare] Error fetching quote: {e}", file=sys.stderr)
        return None


def get_klines(symbol, interval="1d", start="20240101", end="", market="cn_stock", token=""):
    """Fetch historical kline data via tushare.

    Supports daily, weekly, monthly for A-shares and indices.
    """
    if not token:
        return []

    if not end:
        end = datetime.now().strftime("%Y%m%d")

    pro = get_tushare_api(token)

    try:
        ts_code = _to_ts_code(symbol, market)
        start_date = start.replace("-", "")
        end_date = end.replace("-", "")

        df = None

        if market == "index":
            # Index daily data
            if interval in ("1d", "daily"):
                df = pro.index_daily(
                    ts_code=ts_code,
                    start_date=start_date,
                    end_date=end_date,
                )
            elif interval in ("1w", "weekly"):
                df = pro.index_weekly(
                    ts_code=ts_code,
                    start_date=start_date,
                    end_date=end_date,
                )
            elif interval in ("1M", "monthly"):
                df = pro.index_monthly(
                    ts_code=ts_code,
                    start_date=start_date,
                    end_date=end_date,
                )
        elif market == "fund":
            df = pro.fund_daily(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            )
        else:
            # A-shares: daily, weekly, monthly
            if interval in ("1w", "weekly"):
                df = pro.weekly(
                    ts_code=ts_code,
                    start_date=start_date,
                    end_date=end_date,
                )
            elif interval in ("1M", "monthly"):
                df = pro.monthly(
                    ts_code=ts_code,
                    start_date=start_date,
                    end_date=end_date,
                )
            else:
                df = pro.daily(
                    ts_code=ts_code,
                    start_date=start_date,
                    end_date=end_date,
                )

        if df is None or df.empty:
            return []

        bars = []
        for _, row in df.iterrows():
            trade_date = str(row["trade_date"])
            try:
                ts = int(datetime.strptime(trade_date, "%Y%m%d").timestamp() * 1000)
            except ValueError:
                continue

            close_val = float(row["close"])
            if close_val <= 0:
                continue

            bars.append({
                "timestamp": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": close_val,
                "volume": int(row.get("vol", 0) * 100),  # 手 → 股
                "amount": float(row.get("amount", 0) * 1000),  # 千元 → 元
            })

        bars.reverse()  # tushare returns newest first
        return bars

    except Exception as e:
        print(f"[tushare] Error fetching klines: {e}", file=sys.stderr)
        return []


def search_symbols(keyword, market=None, token=""):
    """Search symbols via tushare."""
    if not token:
        return []

    pro = get_tushare_api(token)

    try:
        results = []

        # Search A-share stocks
        if market is None or market == "cn_stock":
            df = pro.stock_basic(
                exchange="", list_status="L",
                fields="ts_code,symbol,name,area,industry,market",
            )
            if df is not None and not df.empty:
                matched = df[
                    df["name"].str.contains(keyword, na=False)
                    | df["symbol"].str.contains(keyword, na=False)
                ]
                for _, row in matched.head(15).iterrows():
                    code = str(row["symbol"])
                    results.append({
                        "code": code,
                        "name": str(row["name"]),
                        "market": "cn_stock",
                        "exchange": "SSE" if code.startswith("6") else "SZSE",
                    })

        # Search indices
        if market is None or market == "index":
            try:
                idx_df = pro.index_basic(fields="ts_code,name,market")
                if idx_df is not None and not idx_df.empty:
                    matched = idx_df[idx_df["name"].str.contains(keyword, na=False)]
                    for _, row in matched.head(5).iterrows():
                        ts_code = str(row["ts_code"])
                        plain = ts_code.split(".")[0]
                        results.append({
                            "code": plain,
                            "name": str(row["name"]),
                            "market": "index",
                            "exchange": "SSE" if ".SH" in ts_code else "SZSE",
                        })
            except Exception:
                pass

        # Search ETFs/funds
        if market is None or market == "fund":
            try:
                fund_df = pro.fund_basic(
                    market="E",  # exchange-traded
                    fields="ts_code,name",
                )
                if fund_df is not None and not fund_df.empty:
                    matched = fund_df[fund_df["name"].str.contains(keyword, na=False)]
                    for _, row in matched.head(5).iterrows():
                        ts_code = str(row["ts_code"])
                        plain = ts_code.split(".")[0]
                        results.append({
                            "code": plain,
                            "name": str(row["name"]),
                            "market": "fund",
                            "exchange": "SSE" if ".SH" in ts_code else "SZSE",
                        })
            except Exception:
                pass

        return results[:20]

    except Exception as e:
        print(f"[tushare] Error searching: {e}", file=sys.stderr)
        return []


def get_fundamentals(symbol, market="cn_stock", token=""):
    """Fetch fundamental data via Tushare (daily_basic + income + balancesheet)."""
    if not token:
        return {"error": "Tushare token not configured"}

    pro = get_tushare_api(token)

    try:
        ts_code = _to_ts_code(symbol, market)
        today = datetime.now().strftime("%Y%m%d")

        result = {
            "symbol": {"code": symbol, "name": symbol, "market": market},
        }

        # Daily basic indicators (PE, PB, PS, etc.)
        try:
            db = pro.daily_basic(ts_code=ts_code, trade_date=today)
            if db is None or db.empty:
                # Try last available trading day
                db = pro.daily_basic(ts_code=ts_code, end_date=today, limit=1)
            if db is not None and not db.empty:
                r = db.iloc[0]
                result.update({
                    "pe": float(r.get("pe_ttm", 0) or 0) or None,
                    "pb": float(r.get("pb", 0) or 0) or None,
                    "ps": float(r.get("ps_ttm", 0) or 0) or None,
                    "marketCap": float(r.get("total_mv", 0) or 0) * 10000,  # 万元 → 元
                    "circulatingMarketCap": float(r.get("circ_mv", 0) or 0) * 10000,
                    "turnoverRatio": float(r.get("turnover_rate", 0) or 0),
                    "dividendYield": float(r.get("dv_ttm", 0) or 0) or None,
                })
        except Exception as e:
            print(f"[tushare] daily_basic error: {e}", file=sys.stderr)

        # Financial indicators (ROE, EPS, etc.)
        try:
            fin = pro.fina_indicator(ts_code=ts_code, limit=1)
            if fin is not None and not fin.empty:
                f = fin.iloc[0]
                result.update({
                    "eps": float(f.get("eps", 0) or 0) or None,
                    "roe": float(f.get("roe", 0) or 0) or None,
                    "roa": float(f.get("roa", 0) or 0) or None,
                    "grossMargin": float(f.get("grossprofit_margin", 0) or 0) or None,
                    "netMargin": float(f.get("netprofit_margin", 0) or 0) or None,
                    "debtToAssets": float(f.get("debt_to_assets", 0) or 0) or None,
                    "currentRatio": float(f.get("current_ratio", 0) or 0) or None,
                    "quickRatio": float(f.get("quick_ratio", 0) or 0) or None,
                    "revenueGrowth": float(f.get("or_yoy", 0) or 0) or None,
                    "earningsGrowth": float(f.get("netprofit_yoy", 0) or 0) or None,
                })
        except Exception as e:
            print(f"[tushare] fina_indicator error: {e}", file=sys.stderr)

        # Get stock name
        try:
            info = pro.stock_basic(ts_code=ts_code, fields="name")
            if info is not None and not info.empty:
                result["symbol"]["name"] = str(info.iloc[0]["name"])
        except Exception:
            pass

        # Remove None values
        return {k: v for k, v in result.items() if v is not None}

    except Exception as e:
        print(f"[tushare] Error fetching fundamentals: {e}", file=sys.stderr)
        return {"error": str(e)}


if __name__ == "__main__":
    config = get_input()
    action = config.get("action")
    token = config.get("token", "")

    if action == "quote":
        result = get_quote(
            config["symbol"],
            config.get("market", "cn_stock"),
            token,
        )
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
        result = search_symbols(
            config.get("keyword", ""),
            config.get("market"),
            token,
        )
    elif action == "fundamentals":
        result = get_fundamentals(
            config["symbol"],
            config.get("market", "cn_stock"),
            token,
        )
    else:
        result = {"error": f"Unknown action: {action}"}

    print(json.dumps(result, ensure_ascii=False))
