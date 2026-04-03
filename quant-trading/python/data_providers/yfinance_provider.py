#!/usr/bin/env python3
"""
Yahoo Finance data provider bridge.

Fetches market data via yfinance for A-shares, HK, US, and more.

A-share ticker format: 600519.SS (Shanghai), 000858.SZ (Shenzhen)
HK:  0700.HK
US:  AAPL, MSFT

Input: JSON via QUANT_CONFIG env var or stdin.
Output: JSON to stdout.

Requirements:
  pip install yfinance pandas
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


def _to_yf_symbol(symbol: str, market: str) -> str:
    """Convert internal symbol to yfinance ticker format.

    Handles symbols that already carry an exchange suffix (e.g. ``0777.HK``)
    so we never produce ``0777.HK.HK``.
    """
    # Strip known suffixes first to normalise
    sym = symbol.strip()
    upper = sym.upper()
    for suffix in (".HK", ".SS", ".SH", ".SZ", ".T", ".L"):
        if upper.endswith(suffix):
            sym = sym[: -len(suffix)]
            break

    if market == "cn_stock":
        # A-share: guess exchange from first digit
        if sym.startswith(("6", "5")):
            return f"{sym}.SS"  # Shanghai
        elif sym.startswith(("0", "3", "1", "2")):
            return f"{sym}.SZ"  # Shenzhen
        return f"{sym}.SS"
    elif market == "hk_stock":
        return f"{sym}.HK"
    elif market == "us_stock":
        return sym  # US tickers are plain
    elif market == "fund":
        # ETF in China
        if sym.startswith(("5", "1")):
            return f"{sym}.SS"
        return f"{sym}.SZ"
    return symbol


def get_quote(symbol: str, market: str = "cn_stock"):
    """Fetch real-time quote."""
    import yfinance as yf

    try:
        yf_symbol = _to_yf_symbol(symbol, market)
        ticker = yf.Ticker(yf_symbol)
        info = ticker.fast_info

        # Get name from info if available
        name = symbol
        try:
            full_info = ticker.info
            name = full_info.get("shortName") or full_info.get("longName") or symbol
        except Exception:
            pass

        last_price = float(info.last_price)
        prev_close = float(info.previous_close)
        change = last_price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0.0

        volume = int(info.last_volume or 0)
        # yfinance doesn't provide turnover directly; estimate from avg price × volume
        avg_price = (float(info.day_high) + float(info.day_low)) / 2 if info.day_high and info.day_low else last_price
        amount = avg_price * volume

        # Try to get market cap from full_info (already fetched above for name)
        market_cap = 0
        try:
            market_cap = full_info.get("marketCap") or 0
        except Exception:
            pass

        return {
            "symbol": {"code": symbol, "name": name, "market": market},
            "price": last_price,
            "change": round(change, 4),
            "changePct": round(change_pct, 4),
            "open": float(info.open),
            "high": float(info.day_high),
            "low": float(info.day_low),
            "prevClose": prev_close,
            "volume": volume,
            "amount": round(amount, 2),
            "marketCap": market_cap,
            "timestamp": int(datetime.now().timestamp() * 1000),
        }

    except Exception as e:
        print(f"Error fetching quote: {e}", file=sys.stderr)
        return None


def get_klines(symbol: str, interval: str, start: str, end: str, market: str = "cn_stock"):
    """Fetch historical kline/OHLCV data."""
    import yfinance as yf

    try:
        yf_symbol = _to_yf_symbol(symbol, market)
        ticker = yf.Ticker(yf_symbol)

        # Map interval to yfinance format
        interval_map = {"1d": "1d", "1w": "1wk", "1M": "1mo", "5m": "5m", "15m": "15m", "30m": "30m", "60m": "60m", "1h": "60m"}
        yf_interval = interval_map.get(interval, "1d")

        df = ticker.history(start=start, end=end, interval=yf_interval)

        if df is None or df.empty:
            return []

        bars = []
        for idx, row in df.iterrows():
            ts = int(idx.timestamp() * 1000)
            bars.append({
                "timestamp": ts,
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(row["Volume"]),
            })
        return bars

    except Exception as e:
        print(f"Error fetching klines: {e}", file=sys.stderr)
        return []


def search_symbols(keyword: str, market: str = None):
    """Search for symbols by keyword.

    Uses a curated popular list + yfinance ticker lookup for broader coverage.
    """
    # Common A-share stocks
    popular = [
        {"code": "600519", "name": "贵州茅台", "market": "cn_stock", "exchange": "SSE"},
        {"code": "000858", "name": "五粮液", "market": "cn_stock", "exchange": "SZSE"},
        {"code": "601318", "name": "中国平安", "market": "cn_stock", "exchange": "SSE"},
        {"code": "000001", "name": "平安银行", "market": "cn_stock", "exchange": "SZSE"},
        {"code": "600036", "name": "招商银行", "market": "cn_stock", "exchange": "SSE"},
        {"code": "601398", "name": "工商银行", "market": "cn_stock", "exchange": "SSE"},
        {"code": "600276", "name": "恒瑞医药", "market": "cn_stock", "exchange": "SSE"},
        {"code": "000333", "name": "美的集团", "market": "cn_stock", "exchange": "SZSE"},
        {"code": "002594", "name": "比亚迪", "market": "cn_stock", "exchange": "SZSE"},
        {"code": "601012", "name": "隆基绿能", "market": "cn_stock", "exchange": "SSE"},
        {"code": "300750", "name": "宁德时代", "market": "cn_stock", "exchange": "SZSE"},
        {"code": "600900", "name": "长江电力", "market": "cn_stock", "exchange": "SSE"},
        {"code": "601899", "name": "紫金矿业", "market": "cn_stock", "exchange": "SSE"},
        {"code": "300059", "name": "东方财富", "market": "cn_stock", "exchange": "SZSE"},
        {"code": "002714", "name": "牧原股份", "market": "cn_stock", "exchange": "SZSE"},
        {"code": "600809", "name": "山西汾酒", "market": "cn_stock", "exchange": "SSE"},
        {"code": "002475", "name": "立讯精密", "market": "cn_stock", "exchange": "SZSE"},
        {"code": "601888", "name": "中国中免", "market": "cn_stock", "exchange": "SSE"},
        {"code": "000568", "name": "泸州老窖", "market": "cn_stock", "exchange": "SZSE"},
        {"code": "600030", "name": "中信证券", "market": "cn_stock", "exchange": "SSE"},
    ]

    # Common HK stocks
    popular_hk = [
        {"code": "0700", "name": "腾讯控股", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "9988", "name": "阿里巴巴", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "9618", "name": "京东集团", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "3690", "name": "美团", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "1810", "name": "小米集团", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "0005", "name": "汇丰控股", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "1299", "name": "友邦保险", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "2318", "name": "中国平安", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "0941", "name": "中国移动", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "0388", "name": "香港交易所", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "2020", "name": "安踏体育", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "0027", "name": "银河娱乐", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "1211", "name": "比亚迪股份", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "0175", "name": "吉利汽车", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "2382", "name": "舜宇光学", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "0777", "name": "网龙网络", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "0992", "name": "联想集团", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "0981", "name": "中芯国际", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "9999", "name": "网易", "market": "hk_stock", "exchange": "HKEX"},
        {"code": "3968", "name": "招商银行", "market": "hk_stock", "exchange": "HKEX"},
    ]

    # Filter popular list by market
    all_popular = []
    if market is None or market == "cn_stock":
        all_popular.extend(popular)
    if market is None or market == "hk_stock":
        all_popular.extend(popular_hk)

    results = []
    kw = keyword.lower()

    # Check if keyword looks like a ticker code
    if keyword.isdigit():
        for item in all_popular:
            if item["code"].startswith(keyword):
                results.append(item)
        # If code not in popular list but is 6 digits, try to look it up
        if not results and len(keyword) == 6:
            results.append({
                "code": keyword,
                "name": keyword,
                "market": market or "cn_stock",
                "exchange": "SSE" if keyword.startswith(("6", "5")) else "SZSE",
            })
        # 4-5 digit code for HK stock
        if not results and len(keyword) <= 5 and (market is None or market == "hk_stock"):
            padded = keyword.zfill(4)
            results.append({
                "code": padded,
                "name": padded,
                "market": "hk_stock",
                "exchange": "HKEX",
            })
    else:
        for item in all_popular:
            if kw in item["name"].lower() or kw in item["code"]:
                results.append(item)

    return results[:20]


def get_fundamentals(symbol: str, market: str = "cn_stock"):
    """Fetch fundamental/valuation data for a single stock via yfinance.

    Returns PE, PB, ROE, ROA, margins, growth, cash flow, debt and more —
    everything a user/LLM needs for a quick fundamental check.
    """
    import yfinance as yf

    try:
        yf_symbol = _to_yf_symbol(symbol, market)
        ticker = yf.Ticker(yf_symbol)
        info = ticker.info

        def _sf(key):
            """Safe float from info dict."""
            v = info.get(key)
            if v is None:
                return None
            try:
                return float(v)
            except (ValueError, TypeError):
                return None

        def _pct(key):
            """Safe percentage (decimal → %) from info dict."""
            v = _sf(key)
            return round(v * 100, 2) if v is not None else None

        name = info.get("shortName") or info.get("longName") or symbol
        sector = info.get("sector")
        industry = info.get("industry")

        result = {
            "symbol": {"code": symbol, "name": name, "market": market},
            "sector": sector,
            "industry": industry,
            # Valuation
            "pe": _sf("trailingPE"),
            "forwardPE": _sf("forwardPE"),
            "pb": _sf("priceToBook"),
            "ps": _sf("priceToSalesTrailing12Months"),
            "eps": _sf("trailingEps"),
            "forwardEps": _sf("forwardEps"),
            "pegRatio": _sf("pegRatio"),
            "evToEbitda": _sf("enterpriseToEbitda"),
            "evToRevenue": _sf("enterpriseToRevenue"),
            # Profitability
            "roe": _pct("returnOnEquity"),
            "roa": _pct("returnOnAssets"),
            "grossMargin": _pct("grossMargins"),
            "operatingMargin": _pct("operatingMargins"),
            "netMargin": _pct("profitMargins"),
            # Growth
            "revenueGrowth": _pct("revenueGrowth"),
            "earningsGrowth": _pct("earningsGrowth"),
            # Dividends
            "dividendYield": _pct("dividendYield"),
            "payoutRatio": _pct("payoutRatio"),
            # Balance sheet
            "debtToEquity": _sf("debtToEquity"),
            "currentRatio": _sf("currentRatio"),
            "quickRatio": _sf("quickRatio"),
            # Cash flow
            "freeCashFlow": _sf("freeCashflow"),
            "operatingCashFlow": _sf("operatingCashflow"),
            # Size
            "marketCap": _sf("marketCap"),
            "enterpriseValue": _sf("enterpriseValue"),
            "sharesOutstanding": _sf("sharesOutstanding"),
            # Price context
            "price": _sf("currentPrice") or _sf("regularMarketPrice"),
            "fiftyTwoWeekHigh": _sf("fiftyTwoWeekHigh"),
            "fiftyTwoWeekLow": _sf("fiftyTwoWeekLow"),
            "fiftyDayAverage": _sf("fiftyDayAverage"),
            "twoHundredDayAverage": _sf("twoHundredDayAverage"),
            "beta": _sf("beta"),
        }

        # Remove None values for cleaner output
        return {k: v for k, v in result.items() if v is not None}

    except Exception as e:
        print(f"Error fetching fundamentals: {e}", file=sys.stderr)
        return {"error": str(e)}


if __name__ == "__main__":
    config = get_input()
    action = config.get("action")

    if action == "quote":
        result = get_quote(config["symbol"], config.get("market", "cn_stock"))
    elif action == "klines":
        result = get_klines(
            config["symbol"],
            config.get("interval", "1d"),
            config.get("start", "2024-01-01"),
            config.get("end", datetime.now().strftime("%Y-%m-%d")),
            config.get("market", "cn_stock"),
        )
    elif action == "search":
        result = search_symbols(config.get("keyword", ""), config.get("market"))
    elif action == "fundamentals":
        result = get_fundamentals(config["symbol"], config.get("market", "cn_stock"))
    else:
        result = {"error": f"Unknown action: {action}"}

    print(json.dumps(result, ensure_ascii=False))
