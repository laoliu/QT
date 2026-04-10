"""
Market-aware data provider factory.

Auto-detects market from symbol format and routes to the appropriate
data provider with multi-source fallback:

  A-shares: AKShare → Tushare → JQData → YFinance
  US/HK:    YFinance → AKShare
  Index:    AKShare → Tushare → JQData
  Futures:  AKShare → JQData

Provides a unified interface for fetching backtest data as pandas DataFrames.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional


def detect_market(symbol: str) -> str:
    """Detect market type from symbol format.

    Returns:
        'cn_stock'         — 6-digit numeric codes (600519, 000001)
        'hk_stock'         — ends with .HK or 5-digit numeric
        'us_stock'         — alphabetic tickers (AAPL, GOOGL)
        'jp_stock'         — ends with .T
        'uk_stock'         — ends with .L
        'index'            — common index codes (000300, 399001, SPY)
        'etf_option'       — ETF option contract codes
        'index_option'     — index option codes
        'commodity_option' — commodity option codes
    """
    sym = symbol.strip().upper()

    # ── Option contract detection ──
    # ETF option codes: typically 8-digit starting with 1000xxxx (SSE) or 9000xxxx (SZSE)
    if sym.isdigit() and len(sym) == 8 and (sym.startswith("1000") or sym.startswith("9000")):
        return "etf_option"
    # CFFEX index options: e.g. IO2406-C-4000
    if "IO" in sym and ("-C-" in sym or "-P-" in sym):
        return "index_option"
    # Commodity options: e.g. m2409-C-3200, SR410C5200
    if any(sym.startswith(prefix) for prefix in ["M2", "C2", "I2", "CU2", "AU2", "SR", "CF", "TA"]):
        if "-C-" in sym or "-P-" in sym or "C" in sym[2:] or "P" in sym[2:]:
            return "commodity_option"

    # Explicit suffix-based detection
    if sym.endswith(".HK"):
        return "hk_stock"
    if sym.endswith((".SS", ".SZ")):
        return "cn_stock"
    if sym.endswith(".T"):
        return "jp_stock"
    if sym.endswith((".L", ".LON")):
        return "uk_stock"

    # Pure numeric — likely A-share or index
    digits_only = sym.replace(".", "")
    if digits_only.isdigit() and len(digits_only) == 6:
        # Common index codes
        if sym in ("000300", "399001", "399006", "000016", "000905"):
            return "index"
        return "cn_stock"

    # Alphabetic — likely US stock or common index ETF
    if sym.isalpha():
        return "us_stock"

    # Mixed (e.g. "00700.HK") — try suffix
    if "." in sym:
        suffix = sym.rsplit(".", 1)[-1]
        if suffix == "HK":
            return "hk_stock"
        if suffix == "SS":
            return "cn_stock"
        if suffix == "SZ":
            return "cn_stock"

    return "us_stock"  # Default


def to_yfinance_symbol(symbol: str, market: str = "") -> str:
    """Convert symbol to yfinance-compatible format.

    A-share codes need .SS/.SZ suffix for Yahoo Finance.
    """
    if not market:
        market = detect_market(symbol)

    sym = symbol.strip()

    if market == "cn_stock":
        # Add exchange suffix for Yahoo Finance
        if not (sym.endswith(".SS") or sym.endswith(".SZ")):
            if sym.startswith(("6", "5", "9")):
                return f"{sym}.SS"
            else:
                return f"{sym}.SZ"
    elif market == "hk_stock":
        if not sym.endswith(".HK"):
            return f"{sym}.HK"
    elif market == "index":
        # Map common Chinese indices to Yahoo Finance symbols
        index_map = {
            "000300": "000300.SS",  # CSI 300
            "399001": "399001.SZ",  # SZSE Component
            "399006": "399006.SZ",  # ChiNext
            "000016": "000016.SS",  # SSE 50
            "000905": "000905.SS",  # CSI 500
        }
        if sym in index_map:
            return index_map[sym]

    return sym


def _get_tushare_token() -> str:
    """Read Tushare token from env or config files."""
    token = os.environ.get("TUSHARE_TOKEN", "")
    if token:
        return token
    # Try reading from openclaw config
    import json
    home = os.path.expanduser("~")
    for cfg_dir in [".openclaw-dev", ".openclaw"]:
        try:
            cfg_path = os.path.join(home, cfg_dir, "openclaw.json")
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            token = (cfg.get("plugins", {})
                     .get("entries", {})
                     .get("quant-trading", {})
                     .get("config", {})
                     .get("dataProviders", {})
                     .get("tushare", {})
                     .get("token", ""))
            if token:
                return token
        except Exception:
            continue
    return ""


def _get_jqdata_creds() -> tuple[str, str]:
    """Read JQData credentials from env or config files."""
    username = os.environ.get("JQDATA_USERNAME", "")
    password = os.environ.get("JQDATA_PASSWORD", "")
    if username and password:
        return username, password
    import json
    home = os.path.expanduser("~")
    for cfg_dir in [".openclaw-dev", ".openclaw"]:
        try:
            cfg_path = os.path.join(home, cfg_dir, "openclaw.json")
            with open(cfg_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            jq_cfg = (cfg.get("plugins", {})
                      .get("entries", {})
                      .get("quant-trading", {})
                      .get("config", {})
                      .get("dataProviders", {})
                      .get("jqdata", {}))
            u = jq_cfg.get("username", "")
            p = jq_cfg.get("password", "")
            if u and p:
                return u, p
        except Exception:
            continue
    return "", ""


def _fetch_tushare_data(symbol: str, start_date: str, end_date: str, market: str = "cn_stock"):
    """Fetch data via Tushare as fallback."""
    try:
        from data_providers.tushare_provider import get_klines
        import pandas as pd

        token = _get_tushare_token()
        if not token:
            return None

        bars = get_klines(symbol, "1d", start_date, end_date, market, token)
        if not bars:
            return None

        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.set_index("date")
        df = df[["open", "high", "low", "close", "volume"]]
        df = df.sort_index()
        return df

    except Exception as e:
        print(f"[tushare] Error fetching {symbol}: {e}", file=sys.stderr)
        return None


def _fetch_jqdata_data(symbol: str, start_date: str, end_date: str, market: str = "cn_stock"):
    """Fetch data via JQData as fallback."""
    try:
        from data_providers.jqdata_provider import get_klines
        import pandas as pd

        username, password = _get_jqdata_creds()
        if not username or not password:
            return None

        bars = get_klines(symbol, "1d", start_date, end_date, market, username, password)
        if not bars:
            return None

        df = pd.DataFrame(bars)
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms")
        df = df.set_index("date")
        df = df[["open", "high", "low", "close", "volume"]]
        df = df.sort_index()
        return df

    except Exception as e:
        print(f"[jqdata] Error fetching {symbol}: {e}", file=sys.stderr)
        return None


def fetch_data_for_backtest(
    symbol: str,
    start_date: str,
    end_date: str,
    market: str = "",
):
    """Fetch historical data for backtesting. Returns pandas DataFrame.

    Auto-routes based on symbol with multi-source fallback:
      A-shares:  AKShare → Tushare → JQData → YFinance
      US/HK:     YFinance → AKShare
      Index:     AKShare → Tushare → JQData

    Args:
        symbol: Stock code (600519, AAPL, 00700.HK, etc.)
        start_date: YYYY-MM-DD
        end_date: YYYY-MM-DD
        market: Optional market override

    Returns:
        pandas DataFrame with columns [open, high, low, close, volume],
        indexed by datetime. Returns None on failure.
    """
    if not market:
        market = detect_market(symbol)

    if market == "cn_stock" or market == "index":
        # Primary: AKShare
        df = _fetch_cn_data(symbol, start_date, end_date)
        if df is not None and not df.empty:
            return df

        # Fallback 1: Tushare
        print(f"[provider_factory] AKShare failed for {symbol}, trying Tushare", file=sys.stderr)
        df = _fetch_tushare_data(symbol, start_date, end_date, market)
        if df is not None and not df.empty:
            return df

        # Fallback 2: JQData
        print(f"[provider_factory] Tushare failed for {symbol}, trying JQData", file=sys.stderr)
        df = _fetch_jqdata_data(symbol, start_date, end_date, market)
        if df is not None and not df.empty:
            return df

        # Fallback 3: YFinance
        print(f"[provider_factory] JQData failed for {symbol}, trying YFinance", file=sys.stderr)
        return _fetch_yfinance_data(symbol, start_date, end_date, market)
    else:
        # Non-CN: YFinance primary, AKShare fallback for HK
        df = _fetch_yfinance_data(symbol, start_date, end_date, market)
        if df is not None and not df.empty:
            return df
        if market == "hk_stock":
            return _fetch_cn_data(symbol, start_date, end_date)
        return None


def _fetch_cn_data(symbol: str, start_date: str, end_date: str):
    """Fetch A-share data via akshare."""
    try:
        import akshare as ak
        import pandas as pd

        # Remove any exchange suffix
        code = symbol.replace(".SS", "").replace(".SZ", "").strip()

        df = ak.stock_zh_a_hist(
            symbol=code,
            period="daily",
            start_date=start_date.replace("-", ""),
            end_date=end_date.replace("-", ""),
            adjust="qfq",
        )

        if df is None or df.empty:
            print(f"[akshare] No data for {symbol}", file=sys.stderr)
            return None

        # Rename Chinese columns to English
        df = df.rename(columns={
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
        })

        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df = df[["open", "high", "low", "close", "volume"]]
        df = df.sort_index()

        return df

    except Exception as e:
        print(f"[akshare] Error fetching {symbol}: {e}", file=sys.stderr)
        # Fallback to yfinance for A-shares
        return _fetch_yfinance_data(symbol, start_date, end_date, "cn_stock")


def fetch_klines_batch(
    symbols: list[str],
    start_date: str,
    end_date: str,
    market: str = "",
    max_workers: int = 8,
) -> dict[str, Any]:
    """Fetch K-line data for multiple symbols concurrently.

    Returns dict of {symbol: DataFrame_or_None}.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, Any] = {}

    def _fetch_one(sym: str) -> tuple[str, Any]:
        return sym, fetch_data_for_backtest(sym, start_date, end_date, market or detect_market(sym))

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_fetch_one, sym): sym for sym in symbols}
        for future in as_completed(futures):
            sym, df = future.result()
            results[sym] = df

    return results


def _fetch_yfinance_data(symbol: str, start_date: str, end_date: str, market: str):
    """Fetch data via Yahoo Finance."""
    try:
        import yfinance as yf
        import pandas as pd

        yf_sym = to_yfinance_symbol(symbol, market)
        ticker = yf.Ticker(yf_sym)
        df = ticker.history(start=start_date, end=end_date, auto_adjust=True)

        if df is None or df.empty:
            print(f"[yfinance] No data for {yf_sym} ({start_date} ~ {end_date})", file=sys.stderr)
            return None

        # Normalize column names
        df.columns = [c.lower() for c in df.columns]

        # Keep only OHLCV
        cols = [c for c in ["open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[cols]
        df = df.sort_index()

        # Remove timezone info
        if df.index.tz is not None:
            df.index = df.index.tz_localize(None)

        return df

    except Exception as e:
        print(f"[yfinance] Error fetching {symbol}: {e}", file=sys.stderr)
        return None
