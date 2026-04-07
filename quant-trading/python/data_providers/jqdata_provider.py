#!/usr/bin/env python3
"""
JQData (聚宽) data provider bridge.

Fetches market data from JoinQuant's JQData API.
Supports A-shares, indices, ETFs, futures.

Input: JSON via stdin or QUANT_CONFIG env var.
Output: JSON to stdout.

Requirements:
  pip install jqdatasdk pandas
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


_jq_authed = False


def _ensure_auth(username: str, password: str):
    """Authenticate with JQData (only once per process)."""
    global _jq_authed
    if _jq_authed:
        return
    import jqdatasdk as jq
    jq.auth(username, password)
    _jq_authed = True


def _to_jq_code(symbol: str, market: str = "cn_stock") -> str:
    """Convert plain symbol to JQData format.

    600519 → 600519.XSHG (Shanghai)
    000858 → 000858.XSHE (Shenzhen)
    """
    code = symbol.strip().replace(".SH", "").replace(".SS", "").replace(".SZ", "")

    if market == "index":
        if code.startswith("399"):
            return f"{code}.XSHE"
        return f"{code}.XSHG"

    if market == "fund":
        if code.startswith(("5", "1")):
            return f"{code}.XSHG"
        return f"{code}.XSHE"

    if market == "futures":
        # JQData uses uppercase, e.g. RB2505.XSGE
        return code.upper()

    # cn_stock
    if code.startswith(("6", "5", "9")):
        return f"{code}.XSHG"
    return f"{code}.XSHE"


def get_quote(symbol: str, market: str, username: str, password: str):
    """Fetch real-time quote via JQData."""
    if not username or not password:
        return {"error": "JQData credentials not configured"}

    try:
        import jqdatasdk as jq
        _ensure_auth(username, password)

        jq_code = _to_jq_code(symbol, market)
        df = jq.get_price(
            jq_code,
            count=1,
            end_date=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            frequency="daily",
            fields=["open", "high", "low", "close", "volume", "money",
                     "pre_close"],
        )

        if df is None or df.empty:
            return None

        r = df.iloc[-1]
        price = float(r["close"])
        prev_close = float(r.get("pre_close", 0) or 0)

        # Try to get stock name
        name = symbol
        try:
            sec_info = jq.get_security_info(jq_code)
            if sec_info is not None:
                name = sec_info.display_name
        except Exception:
            pass

        return {
            "symbol": {"code": symbol, "name": name, "market": market},
            "price": price,
            "change": round(price - prev_close, 4) if prev_close else 0,
            "changePct": round((price - prev_close) / prev_close * 100, 4) if prev_close else 0,
            "open": float(r["open"]),
            "high": float(r["high"]),
            "low": float(r["low"]),
            "prevClose": prev_close,
            "volume": int(r["volume"]),
            "amount": float(r.get("money", 0) or 0),
            "timestamp": int(datetime.now().timestamp() * 1000),
        }
    except Exception as e:
        print(f"[jqdata] Error fetching quote: {e}", file=sys.stderr)
        return None


def get_klines(symbol: str, interval: str, start: str, end: str,
               market: str, username: str, password: str):
    """Fetch historical kline data via JQData."""
    if not username or not password:
        return []

    try:
        import jqdatasdk as jq
        _ensure_auth(username, password)

        jq_code = _to_jq_code(symbol, market)

        # Map interval to JQData frequency
        freq_map = {
            "1d": "daily", "1w": "weekly", "1M": "monthly",
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m", "60m": "60m",
        }
        frequency = freq_map.get(interval, "daily")

        # Normalize date format
        start_date = start.replace("-", "")
        if len(start_date) == 8:
            start_date = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        else:
            start_date = start[:10]
        end_date = end.replace("-", "")
        if len(end_date) == 8:
            end_date = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
        else:
            end_date = end[:10]

        df = jq.get_price(
            jq_code,
            start_date=start_date,
            end_date=end_date,
            frequency=frequency,
            fields=["open", "high", "low", "close", "volume", "money"],
            fq="pre",  # 前复权
        )

        if df is None or df.empty:
            return []

        bars = []
        for idx, row in df.iterrows():
            ts_str = str(idx)[:10]
            try:
                ts = int(datetime.strptime(ts_str, "%Y-%m-%d").timestamp() * 1000)
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
                "volume": int(row["volume"]),
                "amount": float(row.get("money", 0) or 0),
            })

        return bars

    except Exception as e:
        print(f"[jqdata] Error fetching klines: {e}", file=sys.stderr)
        return []


def search_symbols(keyword: str, market: str, username: str, password: str):
    """Search symbols via JQData."""
    if not username or not password:
        return []

    try:
        import jqdatasdk as jq
        _ensure_auth(username, password)

        # Get all securities
        sec_type = "stock"
        if market == "fund":
            sec_type = "etf"
        elif market == "futures":
            sec_type = "futures"
        elif market == "index":
            sec_type = "index"

        df = jq.get_all_securities(types=[sec_type])

        if df is None or df.empty:
            return []

        results = []
        for jq_code, row in df.iterrows():
            name = str(row.get("display_name", ""))
            code_str = str(jq_code)
            # Extract plain code (e.g. 600519 from 600519.XSHG)
            plain = code_str.split(".")[0]

            if keyword in name or keyword in plain:
                exchange = "SSE" if ".XSHG" in code_str else "SZSE"
                results.append({
                    "code": plain,
                    "name": name,
                    "market": market or "cn_stock",
                    "exchange": exchange,
                })
                if len(results) >= 20:
                    break

        return results

    except Exception as e:
        print(f"[jqdata] Error searching: {e}", file=sys.stderr)
        return []


def get_fundamentals_data(symbol: str, market: str, username: str, password: str):
    """Fetch fundamental data via JQData valuation/indicator tables."""
    if not username or not password:
        return {"error": "JQData credentials not configured"}

    try:
        import jqdatasdk as jq
        _ensure_auth(username, password)

        jq_code = _to_jq_code(symbol, market)
        today = datetime.now().strftime("%Y-%m-%d")

        # Query valuation data
        q = jq.query(
            jq.valuation
        ).filter(
            jq.valuation.code == jq_code
        )
        val_df = jq.get_fundamentals(q, date=today)

        # Query indicator data
        q2 = jq.query(
            jq.indicator
        ).filter(
            jq.indicator.code == jq_code
        )
        ind_df = jq.get_fundamentals(q2, date=today)

        result = {
            "symbol": {"code": symbol, "name": symbol, "market": market},
        }

        if val_df is not None and not val_df.empty:
            v = val_df.iloc[0]
            result.update({
                "pe": float(v.get("pe_ratio", 0) or 0) or None,
                "pb": float(v.get("pb_ratio", 0) or 0) or None,
                "ps": float(v.get("ps_ratio", 0) or 0) or None,
                "pcf": float(v.get("pcf_ratio", 0) or 0) or None,
                "marketCap": float(v.get("market_cap", 0) or 0) * 1e8,  # 亿 → 元
                "circulatingMarketCap": float(v.get("circulating_market_cap", 0) or 0) * 1e8,
                "turnoverRatio": float(v.get("turnover_ratio", 0) or 0),
            })

        if ind_df is not None and not ind_df.empty:
            i = ind_df.iloc[0]
            result.update({
                "eps": float(i.get("eps", 0) or 0) or None,
                "roe": float(i.get("roe", 0) or 0) or None,
                "roa": float(i.get("roa", 0) or 0) or None,
                "netMargin": float(i.get("net_profit_margin", 0) or 0) or None,
                "grossMargin": float(i.get("gross_profit_margin", 0) or 0) or None,
                "revenueGrowth": float(i.get("inc_revenue_year_on_year", 0) or 0) or None,
                "earningsGrowth": float(i.get("inc_net_profit_year_on_year", 0) or 0) or None,
            })

        # Remove None values
        return {k: v for k, v in result.items() if v is not None}

    except Exception as e:
        print(f"[jqdata] Error fetching fundamentals: {e}", file=sys.stderr)
        return {"error": str(e)}


if __name__ == "__main__":
    config = get_input()
    action = config.get("action")
    username = config.get("username", "")
    password = config.get("password", "")

    if action == "quote":
        result = get_quote(
            config["symbol"],
            config.get("market", "cn_stock"),
            username, password,
        )
    elif action == "klines":
        result = get_klines(
            config["symbol"],
            config.get("interval", "1d"),
            config.get("start", "20240101"),
            config.get("end", datetime.now().strftime("%Y%m%d")),
            config.get("market", "cn_stock"),
            username, password,
        )
    elif action == "search":
        result = search_symbols(
            config.get("keyword", ""),
            config.get("market", "cn_stock"),
            username, password,
        )
    elif action == "fundamentals":
        result = get_fundamentals_data(
            config["symbol"],
            config.get("market", "cn_stock"),
            username, password,
        )
    else:
        result = {"error": f"Unknown action: {action}"}

    print(json.dumps(result, ensure_ascii=False))
