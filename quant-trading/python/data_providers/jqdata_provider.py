#!/usr/bin/env python3
"""
JQData (聚宽) data provider bridge.

Provides historical K-line, real-time quote, symbol search, and financial
data for A-share stocks via jqdatasdk.

Input: JSON via QUANT_CONFIG env var or stdin.
Output: JSON to stdout.

Requirements:
  pip install jqdatasdk pandas
"""

import json
import os
import sys
from datetime import datetime, timedelta


def get_input():
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        return json.loads(config_str)
    return json.loads(sys.stdin.read())


def _auth_jqdata(username: str, password: str) -> bool:
    """Authenticate with JQData, suppressing stdout noise from jq.auth."""
    import jqdatasdk as jq

    if jq.is_auth():
        return True

    if not username or not password:
        print("[jqdata] No credentials provided", file=sys.stderr)
        return False

    # jq.auth prints to stdout; suppress it
    _stdout = sys.stdout
    sys.stdout = open(os.devnull, "w")
    try:
        jq.auth(username, password)
    finally:
        sys.stdout.close()
        sys.stdout = _stdout

    return jq.is_auth()


def _to_jq_code(symbol: str) -> str:
    """Convert 6-digit A-share code to JQData format (e.g. 600519 -> 600519.XSHG)."""
    code = symbol.strip().replace(".SH", "").replace(".SS", "").replace(".SZ", "")
    if not code.isdigit() or len(code) != 6:
        return symbol
    if code.startswith(("6", "5", "9")):
        return f"{code}.XSHG"
    elif code.startswith(("4", "8")):
        return f"{code}.XBJA"
    else:
        return f"{code}.XSHE"


def _from_jq_code(jq_code: str) -> str:
    """Convert JQData code back to plain 6-digit code."""
    return jq_code.split(".")[0] if "." in jq_code else jq_code


def get_quote(symbol: str, market: str, username: str, password: str):
    """Fetch latest daily quote via JQData."""
    if not _auth_jqdata(username, password):
        return {"error": "JQData authentication failed"}

    import jqdatasdk as jq
    import pandas as pd

    try:
        jq_code = _to_jq_code(symbol)
        today = datetime.now().strftime("%Y-%m-%d")

        # Try fetching today's data, fall back to most recent trading day
        df = jq.get_price(
            jq_code,
            start_date=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
            end_date=today,
            frequency="daily",
            fields=["open", "close", "high", "low", "volume", "money", "pre_close"],
        )

        if df is None or df.empty:
            return None

        row = df.iloc[-1]
        price = float(row["close"])
        prev_close = float(row["pre_close"]) if "pre_close" in row and pd.notna(row["pre_close"]) else 0.0
        change = price - prev_close if prev_close > 0 else 0.0
        change_pct = (change / prev_close * 100) if prev_close > 0 else 0.0

        # Try to get stock name
        name = symbol
        try:
            sec_info = jq.get_security_info(jq_code)
            if sec_info is not None:
                name = sec_info.display_name or symbol
        except Exception:
            pass

        return {
            "symbol": {"code": symbol, "name": name, "market": market},
            "price": round(price, 4),
            "change": round(change, 4),
            "changePct": round(change_pct, 4),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "prevClose": prev_close,
            "volume": int(row["volume"]),
            "amount": float(row["money"]) if "money" in row else 0.0,
            "timestamp": int(datetime.now().timestamp() * 1000),
        }

    except Exception as e:
        print(f"[jqdata] Error fetching quote for {symbol}: {e}", file=sys.stderr)
        return None


def get_klines(symbol: str, interval: str, start: str, end: str, market: str,
               username: str, password: str):
    """Fetch historical K-line data via JQData."""
    if not _auth_jqdata(username, password):
        return []

    import jqdatasdk as jq

    try:
        jq_code = _to_jq_code(symbol)

        # Map interval to jqdatasdk frequency
        freq_map = {
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
            "60m": "60m", "1d": "daily", "1w": "daily", "1M": "daily",
        }
        frequency = freq_map.get(interval, "daily")

        fields = ["open", "close", "high", "low", "volume", "money"]

        df = jq.get_price(
            jq_code,
            start_date=start,
            end_date=end,
            frequency=frequency,
            fields=fields,
        )

        if df is None or df.empty:
            return []

        # For weekly/monthly, resample from daily
        if interval == "1w":
            df = df.resample("W").agg({
                "open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum", "money": "sum",
            }).dropna(subset=["close"])
        elif interval == "1M":
            df = df.resample("ME").agg({
                "open": "first", "high": "max", "low": "min",
                "close": "last", "volume": "sum", "money": "sum",
            }).dropna(subset=["close"])

        bars = []
        for idx, row in df.iterrows():
            ts = int(idx.timestamp() * 1000) if hasattr(idx, "timestamp") else 0
            bars.append({
                "timestamp": ts,
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": int(row["volume"]),
                "amount": float(row["money"]) if "money" in row else 0.0,
            })

        return bars

    except Exception as e:
        print(f"[jqdata] Error fetching klines for {symbol}: {e}", file=sys.stderr)
        return []


def search_symbols(keyword: str, market: str, username: str, password: str):
    """Search A-share symbols via JQData."""
    if not _auth_jqdata(username, password):
        return []

    import jqdatasdk as jq

    try:
        # Get all A-share stocks
        stocks = jq.get_all_securities(types=["stock"], date=datetime.now().strftime("%Y-%m-%d"))

        if stocks is None or stocks.empty:
            return []

        # Filter by keyword (code or name)
        kw = keyword.lower()
        matched = stocks[
            stocks["display_name"].str.lower().str.contains(kw, na=False)
            | stocks.index.str.lower().str.contains(kw, na=False)
        ]

        results = []
        for jq_code, row in matched.head(20).iterrows():
            code = _from_jq_code(jq_code)
            exchange = "SSE" if jq_code.endswith(".XSHG") else "SZSE"
            results.append({
                "code": code,
                "name": str(row["display_name"]),
                "market": "cn_stock",
                "exchange": exchange,
            })

        return results

    except Exception as e:
        print(f"[jqdata] Error searching symbols: {e}", file=sys.stderr)
        return []


def get_fundamentals_data(symbol: str, username: str, password: str):
    """Fetch fundamental/valuation data via JQData."""
    if not _auth_jqdata(username, password):
        return {"error": "JQData authentication failed"}

    import jqdatasdk as jq

    try:
        jq_code = _to_jq_code(symbol)
        today = datetime.now().strftime("%Y-%m-%d")

        # Try fetching from today, fall back progressively
        result = {}
        data_date = None
        for days_back in [0, 7, 30, 60, 90, 180]:
            check_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
            try:
                q = jq.query(
                    jq.valuation
                ).filter(
                    jq.valuation.code == jq_code
                )
                df = jq.get_fundamentals(q, date=check_date)
                if df is not None and not df.empty:
                    data_date = check_date
                    row = df.iloc[0]
                    result["pe"] = _safe_float(row.get("pe_ratio"))
                    result["pb"] = _safe_float(row.get("pb_ratio"))
                    result["marketCap"] = _safe_float(row.get("market_cap"))
                    result["circMarketCap"] = _safe_float(row.get("circulating_market_cap"))
                    result["turnoverRatio"] = _safe_float(row.get("turnover_ratio"))
                    result["pe_ration_lyr"] = _safe_float(row.get("pe_ratio_lyr"))
                    break
            except Exception:
                continue

        if not data_date:
            return {"error": f"No fundamental data for {symbol}"}

        # Fetch indicator data (ROE, margins, etc.)
        try:
            q = jq.query(
                jq.indicator
            ).filter(
                jq.indicator.code == jq_code
            )
            df = jq.get_fundamentals(q, date=data_date)
            if df is not None and not df.empty:
                row = df.iloc[0]
                result["roe"] = _safe_float(row.get("roe"))
                result["roa"] = _safe_float(row.get("roa"))
                result["grossMargin"] = _safe_float(row.get("gross_profit_margin"))
                result["netMargin"] = _safe_float(row.get("net_profit_margin"))
                result["eps"] = _safe_float(row.get("eps"))
                result["revenueYoy"] = _safe_float(row.get("inc_revenue_year_on_year"))
                result["netProfitYoy"] = _safe_float(row.get("inc_net_profit_year_on_year"))
                result["operatingProfit"] = _safe_float(row.get("operating_profit"))
        except Exception:
            pass

        # Get stock name
        name = symbol
        try:
            sec_info = jq.get_security_info(jq_code)
            if sec_info is not None:
                name = sec_info.display_name or symbol
        except Exception:
            pass

        result["symbol"] = {"code": symbol, "name": name, "market": "cn_stock"}
        result["dataDate"] = data_date

        return {k: v for k, v in result.items() if v is not None}

    except Exception as e:
        print(f"[jqdata] Error fetching fundamentals for {symbol}: {e}", file=sys.stderr)
        return {"error": str(e)}


def _safe_float(val) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN
            return None
        return round(f, 4)
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    config = get_input()
    action = config.get("action")
    username = config.get("username", "") or os.environ.get("JQDATA_USERNAME", "")
    password = config.get("password", "") or os.environ.get("JQDATA_PASSWORD", "")

    if action == "quote":
        result = get_quote(
            config["symbol"], config.get("market", "cn_stock"),
            username, password,
        )
    elif action == "klines":
        result = get_klines(
            config["symbol"],
            config.get("interval", "1d"),
            config.get("start", "2024-01-01"),
            config.get("end", datetime.now().strftime("%Y-%m-%d")),
            config.get("market", "cn_stock"),
            username, password,
        )
    elif action == "search":
        result = search_symbols(
            config.get("keyword", ""), config.get("market", "cn_stock"),
            username, password,
        )
    elif action == "fundamentals":
        result = get_fundamentals_data(config["symbol"], username, password)
    else:
        result = {"error": f"Unknown action: {action}"}

    print(json.dumps(result, ensure_ascii=False))
