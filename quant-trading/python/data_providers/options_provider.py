#!/usr/bin/env python3
"""
Options (期权) data provider bridge.

Fetches option chain, quotes, contracts, IV surface from AKShare.

Supported markets:
  - ETF options   (上证50ETF, 沪深300ETF, etc.) — SSE, SZSE
  - Index options (沪深300股指期权, 中证1000股指期权) — CFFEX
  - Commodity options (豆粕, 白糖, ͭ, etc.) — DCE, CZCE, SHFE, GFEX

Input: JSON via stdin or QUANT_CONFIG env var.
Output: JSON to stdout.

Requirements:
  pip install akshare pandas numpy
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Optional

# ── Proxy bypass (same as akshare_provider.py) ────────────────────────────────
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


# ── Common underlying info ────────────────────────────────────────────────────

# ETF option underlyings (上交所/深交所)
ETF_OPTION_UNDERLYINGS = {
    "510050": {"name": "50ETF", "exchange": "SSE", "multiplier": 10000},
    "510300": {"name": "沪深300ETF(华泰)", "exchange": "SSE", "multiplier": 10000},
    "510500": {"name": "中证500ETF", "exchange": "SSE", "multiplier": 10000},
    "159919": {"name": "沪深300ETF(嘉实)", "exchange": "SZSE", "multiplier": 10000},
    "159901": {"name": "深100ETF", "exchange": "SZSE", "multiplier": 10000},
    "159922": {"name": "中证500ETF(南方)", "exchange": "SZSE", "multiplier": 10000},
    "588000": {"name": "科创50ETF", "exchange": "SSE", "multiplier": 10000},
    "588080": {"name": "科创板50ETF(易方达)", "exchange": "SSE", "multiplier": 10000},
    "159915": {"name": "创业板ETF", "exchange": "SZSE", "multiplier": 10000},
}

# 股指期权 (CFFEX)
INDEX_OPTION_UNDERLYINGS = {
    "000300": {"name": "沪深300指数", "exchange": "CFFEX", "multiplier": 100},
    "000852": {"name": "中证1000指数", "exchange": "CFFEX", "multiplier": 100},
    "000016": {"name": "上证50指数", "exchange": "CFFEX", "multiplier": 100},
}

# 商品期权品种 (按交易所)
COMMODITY_OPTION_UNDERLYINGS = {
    # 大商所 DCE
    "m": {"name": "豆粕", "exchange": "DCE", "multiplier": 10},
    "c": {"name": "玉米", "exchange": "DCE", "multiplier": 10},
    "i": {"name": "铁矿石", "exchange": "DCE", "multiplier": 100},
    "pg": {"name": "LPG", "exchange": "DCE", "multiplier": 20},
    "p": {"name": "棕榈油", "exchange": "DCE", "multiplier": 10},
    "l": {"name": "聚乙烯", "exchange": "DCE", "multiplier": 5},
    "pp": {"name": "聚丙烯", "exchange": "DCE", "multiplier": 5},
    "v": {"name": "PVC", "exchange": "DCE", "multiplier": 5},
    # 郑商所 CZCE
    "SR": {"name": "白糖", "exchange": "CZCE", "multiplier": 10},
    "CF": {"name": "棉花", "exchange": "CZCE", "multiplier": 5},
    "TA": {"name": "PTA", "exchange": "CZCE", "multiplier": 5},
    "MA": {"name": "甲醇", "exchange": "CZCE", "multiplier": 10},
    "RM": {"name": "菜粕", "exchange": "CZCE", "multiplier": 10},
    "PK": {"name": "花生", "exchange": "CZCE", "multiplier": 5},
    # 上期所 SHFE
    "cu": {"name": "ͭ", "exchange": "SHFE", "multiplier": 5},
    "au": {"name": "黄金", "exchange": "SHFE", "multiplier": 1000},
    "al": {"name": "铝", "exchange": "SHFE", "multiplier": 5},
    "zn": {"name": "п", "exchange": "SHFE", "multiplier": 5},
    "ru": {"name": "天然橡胶", "exchange": "SHFE", "multiplier": 10},
    # 广期所 GFEX
    "si": {"name": "工业硅", "exchange": "GFEX", "multiplier": 5},
    "lc": {"name": "碳酸锂", "exchange": "GFEX", "multiplier": 1},
}


def get_input() -> dict:
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        return json.loads(config_str)
    return json.loads(sys.stdin.read())


# ── Helper: safe float conversion ─────────────────────────────────────────────

def _safe_float(val: Any, default: float = 0.0) -> float:
    """Convert value to float, returning default on failure."""
    if val is None:
        return default
    try:
        f = float(val)
        # pandas NaN check
        if f != f:
            return default
        return f
    except (ValueError, TypeError):
        return default


def _detect_option_market(underlying: str) -> str:
    """Auto-detect option market from underlying code."""
    u = underlying.strip()
    if u in ETF_OPTION_UNDERLYINGS:
        return "etf_option"
    if u in INDEX_OPTION_UNDERLYINGS:
        return "index_option"
    # Commodity — try case-insensitive match on product codes
    u_lower = u.lower()
    for code in COMMODITY_OPTION_UNDERLYINGS:
        if u_lower == code.lower() or u_lower.startswith(code.lower()):
            return "commodity_option"
    return "etf_option"  # default


# ═══════════════════════════════════════════════════════════════════════════════
# ETF Option Data (上交所/深交所)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_etf_option_contracts(underlying: str) -> list[dict]:
    """Get all available ETF option contracts for a given underlying."""
    import akshare as ak

    contracts = []
    info = ETF_OPTION_UNDERLYINGS.get(underlying, {})
    exchange = info.get("exchange", "SSE")

    try:
        if exchange == "SSE":
            # 上交所 ETF 期权合约列表 (新浪)
            df = ak.option_sse_list_sina(symbol="510050" if underlying == "510050" else underlying,
                                         exchange="null")
        else:
            # 深交所 — use EM (东方财富) API
            df = ak.option_sse_list_sina(symbol=underlying, exchange="null")

        if df is not None and not df.empty:
            for _, row in df.iterrows():
                contract = _parse_sina_option_contract(row, underlying, info)
                if contract:
                    contracts.append(contract)
    except Exception as e:
        print(f"[options] Error fetching ETF option contracts for {underlying}: {e}",
              file=sys.stderr)

    # Fallback: use EM API for broader coverage
    if not contracts:
        try:
            contracts = _get_etf_option_contracts_em(underlying)
        except Exception as e2:
            print(f"[options] EM fallback also failed for {underlying}: {e2}",
                  file=sys.stderr)

    return contracts


def _get_etf_option_contracts_em(underlying: str) -> list[dict]:
    """Fetch ETF option contracts via East Money (东方财富) API."""
    import akshare as ak

    contracts = []
    info = ETF_OPTION_UNDERLYINGS.get(underlying, {})

    try:
        # ak.option_sse_underlying_spot_em returns underlying spot data
        # For contract list, we use option_value_analysis_em or similar
        df = ak.option_current_em()
        if df is not None and not df.empty:
            # Filter by underlying
            for _, row in df.iterrows():
                name_str = str(row.get("名称", ""))
                # Match underlying by name patterns
                und_name = info.get("name", "")
                if und_name and und_name[:3] in name_str:
                    contract = {
                        "symbol": str(row.get("代码", "")),
                        "name": name_str,
                        "underlying": underlying,
                        "strike": _safe_float(row.get("行权价")),
                        "expiry": str(row.get("到期日", "")),
                        "optionType": "call" if "购" in name_str else "put",
                        "exerciseType": "european",
                        "contractMultiplier": info.get("multiplier", 10000),
                        "exchange": info.get("exchange", "SSE"),
                    }
                    contracts.append(contract)
    except Exception as e:
        print(f"[options] EM contracts fetch error: {e}", file=sys.stderr)

    return contracts


def _parse_sina_option_contract(row, underlying: str, info: dict) -> Optional[dict]:
    """Parse a single Sina option contract row into dict."""
    try:
        name = str(row.get("合约名称", row.get("contractId", "")))
        code = str(row.get("合约代码", row.get("code", "")))
        if not code:
            return None

        # Determine call/put from contract name
        option_type = "call" if ("购" in name or "C" in name.upper()) else "put"

        return {
            "symbol": code,
            "name": name,
            "underlying": underlying,
            "underlyingName": info.get("name", ""),
            "strike": _safe_float(row.get("行权价", row.get("strike"))),
            "expiry": str(row.get("到期日", row.get("maturity", ""))),
            "optionType": option_type,
            "exerciseType": "european",
            "contractMultiplier": info.get("multiplier", 10000),
            "exchange": info.get("exchange", "SSE"),
        }
    except Exception:
        return None


def _get_etf_option_chain(underlying: str, expiry: Optional[str] = None) -> dict:
    """Get T-shaped option chain for ETF options."""
    import akshare as ak

    info = ETF_OPTION_UNDERLYINGS.get(underlying, {})
    result = {
        "underlying": underlying,
        "underlyingPrice": 0,
        "expiryDates": [],
        "currentExpiry": "",
        "strikes": [],
        "calls": [],
        "puts": [],
    }

    try:
        # Get underlying spot price
        try:
            spot_df = ak.stock_individual_spot_xq(symbol=underlying)
            if spot_df is not None and not spot_df.empty:
                for _, row in spot_df.iterrows():
                    if str(row.get("item_name", "")) == "最新价":
                        result["underlyingPrice"] = _safe_float(row.get("item_value"))
                        break
        except Exception:
            pass

        # Get real-time option quotes (新浪) — fetch once, split by call/put
        try:
            all_quotes_df = ak.option_sse_spot_price_sina(symbol=underlying)
        except Exception as e:
            all_quotes_df = None
            print(f"[options] Error fetching quotes for {underlying}: {e}", file=sys.stderr)

        if all_quotes_df is not None and not all_quotes_df.empty:
            for opt_type, label in [("call", "购"), ("put", "沽")]:
                for _, row in all_quotes_df.iterrows():
                    name = str(row.get("合约名称", ""))
                    if label not in name:
                        continue

                    quote = _parse_sina_option_quote(row, underlying, opt_type, info)
                    if quote:
                        if opt_type == "call":
                            result["calls"].append(quote)
                        else:
                            result["puts"].append(quote)

                        # Collect expiry dates and strikes
                        if quote.get("expiry") and quote["expiry"] not in result["expiryDates"]:
                            result["expiryDates"].append(quote["expiry"])
                        if quote.get("strike") and quote["strike"] not in result["strikes"]:
                            result["strikes"].append(quote["strike"])

        # Sort
        result["expiryDates"].sort()
        result["strikes"].sort()

        # Filter by expiry if specified
        if expiry:
            result["currentExpiry"] = expiry
            result["calls"] = [c for c in result["calls"] if c.get("expiry") == expiry]
            result["puts"] = [p for p in result["puts"] if p.get("expiry") == expiry]
        elif result["expiryDates"]:
            result["currentExpiry"] = result["expiryDates"][0]  # nearest expiry

    except Exception as e:
        print(f"[options] Error building option chain for {underlying}: {e}", file=sys.stderr)

    return result


def _parse_sina_option_quote(row, underlying: str, option_type: str, info: dict) -> Optional[dict]:
    """Parse a Sina option quote row."""
    try:
        return {
            "symbol": str(row.get("合约代码", row.get("code", ""))),
            "name": str(row.get("合约名称", "")),
            "price": _safe_float(row.get("最新价", row.get("last"))),
            "change": _safe_float(row.get("涨跌额")),
            "changePct": _safe_float(row.get("涨跌幅")),
            "open": _safe_float(row.get("今开", row.get("open"))),
            "high": _safe_float(row.get("最高", row.get("high"))),
            "low": _safe_float(row.get("最低", row.get("low"))),
            "prevClose": _safe_float(row.get("昨收")),
            "volume": _safe_float(row.get("成交量", row.get("volume"))),
            "amount": _safe_float(row.get("成交额", row.get("amount"))),
            "openInterest": _safe_float(row.get("持仓量", row.get("position"))),
            "timestamp": int(datetime.now().timestamp() * 1000),
            "underlying": underlying,
            "strike": _safe_float(row.get("行权价", row.get("strike"))),
            "expiry": str(row.get("到期日", row.get("maturity", ""))),
            "optionType": option_type,
            "impliedVol": _safe_float(row.get("隐含波动率", row.get("iv"))),
            "delta": _safe_float(row.get("Delta")),
            "gamma": _safe_float(row.get("Gamma")),
            "theta": _safe_float(row.get("Theta")),
            "vega": _safe_float(row.get("Vega")),
            "rho": _safe_float(row.get("Rho")),
            "bid1": _safe_float(row.get("买一价", row.get("bid_price"))),
            "ask1": _safe_float(row.get("卖一价", row.get("ask_price"))),
            "bidVol1": _safe_float(row.get("买一量", row.get("bid_vol"))),
            "askVol1": _safe_float(row.get("卖一量", row.get("ask_vol"))),
        }
    except Exception:
        return None


def _infer_underlying_from_symbol(symbol: str) -> str:
    """Infer the underlying ETF code from an option contract code.

    SSE/SZSE ETF option contract codes are 8 digits. The Sina API is keyed
    by underlying code, so we need a reverse mapping. This inspects
    ETF_OPTION_UNDERLYINGS (which is small) by fetching all contracts
    from cache or by heuristic prefix matching.

    Well-known prefixes (may not be exhaustive):
      10005xxx → 510050 (50ETF)
      10006xxx → 510300 (300ETF)
      90000xxx → SZSE (159919, 159901, etc.)
    """
    if not symbol or len(symbol) != 8:
        return ""
    # Try a direct fetch from all known underlyings' contracts (expensive but correct)
    # For now, use prefix heuristics for common SSE underlyings
    prefix_map = {
        "10005": "510050",   # 50ETF
        "10006": "510300",   # 300ETF
        "10007": "510500",   # 500ETF
        "10008": "588000",   # 科创50ETF
    }
    for prefix, und in prefix_map.items():
        if symbol.startswith(prefix):
            return und
    # SZSE options (9000xxxx)
    if symbol.startswith("9000"):
        # Default to 嘉实300ETF — most common SZSE underlying
        return "159919"
    return ""


def _get_etf_option_quote(symbol: str, underlying: str = "") -> Optional[dict]:
    """Get single ETF option contract quote.

    The Sina API (option_sse_spot_price_sina) requires the *underlying* code
    (e.g. "510050") and returns all option quotes for that underlying.
    We fetch the full table and filter by contract code.
    If `underlying` is not provided, we attempt to infer it from the symbol.
    """
    import akshare as ak

    # Attempt to infer underlying if not supplied
    if not underlying:
        # SSE ETF options: 100056xx → 510050, 100063xx → 510300, etc.
        underlying = _infer_underlying_from_symbol(symbol)

    if not underlying:
        return None

    try:
        df = ak.option_sse_spot_price_sina(symbol=underlying)
        if df is not None and not df.empty:
            # Filter by contract code
            code_col = "合约代码" if "合约代码" in df.columns else "code"
            match = df[df[code_col].astype(str) == symbol]
            if not match.empty:
                row = match.iloc[0]
                info = ETF_OPTION_UNDERLYINGS.get(underlying, {})
                opt_type = "call" if ("购" in str(row.get("合约名称", ""))) else "put"
                return _parse_sina_option_quote(row, underlying, opt_type, info)
    except Exception as e:
        print(f"[options] Error fetching option quote {symbol}: {e}", file=sys.stderr)
    return None


def _get_etf_option_klines(symbol: str, start_date: str, end_date: str) -> list[dict]:
    """Get historical daily klines for an ETF option contract."""
    import akshare as ak

    klines = []
    try:
        df = ak.option_sse_daily_sina(symbol=symbol)
        if df is not None and not df.empty:
            # Convert columns
            col_map = {
                "日期": "date", "date": "date",
                "开盘价": "open", "open": "open",
                "最高价": "high", "high": "high",
                "最低价": "low", "low": "low",
                "收盘价": "close", "close": "close",
                "成交量": "volume", "volume": "volume",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

            if "date" in df.columns:
                df["date"] = df["date"].astype(str)
                if start_date:
                    df = df[df["date"] >= start_date]
                if end_date:
                    df = df[df["date"] <= end_date]

            for _, row in df.iterrows():
                date_str = str(row.get("date", ""))
                # Convert date string to unix milliseconds for TS KlineBar
                try:
                    ts = int(datetime.strptime(date_str[:10], "%Y-%m-%d").timestamp() * 1000)
                except (ValueError, TypeError):
                    ts = 0
                klines.append({
                    "date": date_str,
                    "timestamp": ts,
                    "open": _safe_float(row.get("open")),
                    "high": _safe_float(row.get("high")),
                    "low": _safe_float(row.get("low")),
                    "close": _safe_float(row.get("close")),
                    "volume": _safe_float(row.get("volume")),
                })
    except Exception as e:
        print(f"[options] Error fetching option klines {symbol}: {e}", file=sys.stderr)

    return klines


# ═══════════════════════════════════════════════════════════════════════════════
# Index Option Data (中金所 CFFEX)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_index_option_chain(underlying: str, expiry: Optional[str] = None) -> dict:
    """Get option chain for CFFEX index options (沪深300, 中证1000)."""
    import akshare as ak

    info = INDEX_OPTION_UNDERLYINGS.get(underlying, {})
    result = {
        "underlying": underlying,
        "underlyingPrice": 0,
        "expiryDates": [],
        "currentExpiry": "",
        "strikes": [],
        "calls": [],
        "puts": [],
    }

    try:
        # Map underlying to CFFEX product code
        cffex_map = {
            "000300": "沪深300",
            "000852": "中证1000",
            "000016": "上证50",
        }
        product_name = cffex_map.get(underlying, "沪深300")

        # Get CFFEX option quotes
        df = ak.option_cffex_hs300_spot_sina(symbol=product_name)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                name_str = str(row.get("合约名称", ""))
                opt_type = "call" if "购" in name_str else "put"

                quote = {
                    "symbol": str(row.get("合约代码", "")),
                    "name": name_str,
                    "price": _safe_float(row.get("最新价")),
                    "change": _safe_float(row.get("涨跌额")),
                    "changePct": _safe_float(row.get("涨跌幅")),
                    "open": _safe_float(row.get("今开")),
                    "high": _safe_float(row.get("最高")),
                    "low": _safe_float(row.get("最低")),
                    "prevClose": _safe_float(row.get("昨收")),
                    "volume": _safe_float(row.get("成交量")),
                    "amount": _safe_float(row.get("成交额")),
                    "openInterest": _safe_float(row.get("持仓量")),
                    "timestamp": int(datetime.now().timestamp() * 1000),
                    "underlying": underlying,
                    "strike": _safe_float(row.get("行权价")),
                    "expiry": str(row.get("到期日", "")),
                    "optionType": opt_type,
                    "impliedVol": _safe_float(row.get("隐含波动率")),
                    "delta": _safe_float(row.get("Delta")),
                    "gamma": _safe_float(row.get("Gamma")),
                    "theta": _safe_float(row.get("Theta")),
                    "vega": _safe_float(row.get("Vega")),
                }

                if opt_type == "call":
                    result["calls"].append(quote)
                else:
                    result["puts"].append(quote)

                exp = quote.get("expiry", "")
                if exp and exp not in result["expiryDates"]:
                    result["expiryDates"].append(exp)
                st = quote.get("strike", 0)
                if st and st not in result["strikes"]:
                    result["strikes"].append(st)

        result["expiryDates"].sort()
        result["strikes"].sort()

        if expiry:
            result["currentExpiry"] = expiry
            result["calls"] = [c for c in result["calls"] if c.get("expiry") == expiry]
            result["puts"] = [p for p in result["puts"] if p.get("expiry") == expiry]
        elif result["expiryDates"]:
            result["currentExpiry"] = result["expiryDates"][0]

    except Exception as e:
        print(f"[options] Error fetching index option chain for {underlying}: {e}",
              file=sys.stderr)

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Commodity Option Data (大商所/郑商所/上期所/广期所)
# ═══════════════════════════════════════════════════════════════════════════════

def _get_commodity_option_chain(underlying: str, expiry: Optional[str] = None) -> dict:
    """Get option chain for commodity options."""
    import akshare as ak

    u_lower = underlying.lower()
    info = None
    matched_code = None
    for code, meta in COMMODITY_OPTION_UNDERLYINGS.items():
        if u_lower == code.lower() or u_lower.startswith(code.lower()):
            info = meta
            matched_code = code
            break

    if not info:
        return {"error": f"Unknown commodity option underlying: {underlying}"}

    result = {
        "underlying": underlying,
        "underlyingPrice": 0,
        "expiryDates": [],
        "currentExpiry": "",
        "strikes": [],
        "calls": [],
        "puts": [],
    }

    exchange = info["exchange"]
    try:
        # Fetch by exchange
        if exchange == "DCE":
            df = ak.option_dce_daily(symbol=matched_code, trade_date="")
        elif exchange == "CZCE":
            df = ak.option_czce_daily(symbol=matched_code, trade_date="")
        elif exchange == "SHFE":
            df = ak.option_shfe_daily(symbol=matched_code, trade_date="")
        else:
            df = None

        if df is not None and not df.empty:
            for _, row in df.iterrows():
                name_str = str(row.get("合约名称", row.get("合约代码", "")))
                opt_type = "call" if ("C" in name_str.upper() or "购" in name_str) else "put"

                quote = {
                    "symbol": str(row.get("合约代码", "")),
                    "name": name_str,
                    "price": _safe_float(row.get("收盘价", row.get("结算价"))),
                    "change": _safe_float(row.get("涨跌")),
                    "changePct": _safe_float(row.get("涨跌幅")),
                    "open": _safe_float(row.get("开盘价")),
                    "high": _safe_float(row.get("最高价")),
                    "low": _safe_float(row.get("最低价")),
                    "prevClose": _safe_float(row.get("前结算价")),
                    "volume": _safe_float(row.get("成交量")),
                    "amount": _safe_float(row.get("成交额")),
                    "openInterest": _safe_float(row.get("持仓量")),
                    "timestamp": int(datetime.now().timestamp() * 1000),
                    "underlying": underlying,
                    "strike": _safe_float(row.get("行权价")),
                    "expiry": str(row.get("到期日", "")),
                    "optionType": opt_type,
                    "contractMultiplier": info.get("multiplier", 10),
                    "exchange": exchange,
                }

                if opt_type == "call":
                    result["calls"].append(quote)
                else:
                    result["puts"].append(quote)

                exp = quote.get("expiry", "")
                if exp and exp not in result["expiryDates"]:
                    result["expiryDates"].append(exp)
                st = quote.get("strike", 0)
                if st and st not in result["strikes"]:
                    result["strikes"].append(st)

        result["expiryDates"].sort()
        result["strikes"].sort()

        if expiry:
            result["currentExpiry"] = expiry
            result["calls"] = [c for c in result["calls"] if c.get("expiry") == expiry]
            result["puts"] = [p for p in result["puts"] if p.get("expiry") == expiry]
        elif result["expiryDates"]:
            result["currentExpiry"] = result["expiryDates"][0]

    except Exception as e:
        print(f"[options] Error fetching commodity option chain for {underlying}: {e}",
              file=sys.stderr)

    return result


def _find_quote_in_chain(chain: dict, symbol: str) -> Optional[dict]:
    """Find a specific contract quote within a chain result by symbol."""
    for q in chain.get("calls", []) + chain.get("puts", []):
        if q.get("symbol") == symbol:
            return q
    return None


def _get_index_option_klines(symbol: str, start_date: str, end_date: str) -> list[dict]:
    """Get historical klines for an index option contract.
    Currently returns empty — index option klines require CFFEX data source.
    """
    # TODO: implement when CFFEX kline API is available in akshare
    return []


def _get_commodity_option_klines(symbol: str, start_date: str, end_date: str) -> list[dict]:
    """Get historical klines for a commodity option contract.
    Currently returns empty — commodity option klines require exchange-specific API.
    """
    # TODO: implement when commodity option kline API is available
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# IV Surface
# ═══════════════════════════════════════════════════════════════════════════════

def _build_iv_surface(underlying: str, market: str) -> dict:
    """Build implied volatility surface from option chain data."""
    # Get full chain
    if market == "etf_option":
        chain = _get_etf_option_chain(underlying)
    elif market == "index_option":
        chain = _get_index_option_chain(underlying)
    elif market == "commodity_option":
        chain = _get_commodity_option_chain(underlying)
    else:
        return {"error": f"Unsupported market for IV surface: {market}"}

    surface_data = []
    und_price = chain.get("underlyingPrice", 0)

    for quote in chain.get("calls", []) + chain.get("puts", []):
        iv = _safe_float(quote.get("impliedVol"))
        strike = _safe_float(quote.get("strike"))
        expiry_str = quote.get("expiry", "")

        if iv > 0 and strike > 0:
            moneyness = strike / und_price if und_price > 0 else 0
            surface_data.append({
                "expiry": expiry_str,
                "strike": strike,
                "iv": iv,
                "delta": _safe_float(quote.get("delta")),
                "moneyness": round(moneyness, 4),
                "optionType": quote.get("optionType", ""),
            })

    return {
        "underlying": underlying,
        "underlyingPrice": und_price,
        "data": surface_data,
        "timestamp": int(datetime.now().timestamp() * 1000),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Search & Listing
# ═══════════════════════════════════════════════════════════════════════════════

def search_option_underlying(keyword: str) -> list[dict]:
    """Search available option underlyings by keyword."""
    results = []
    kw = keyword.lower()

    # Search ETF options
    for code, info in ETF_OPTION_UNDERLYINGS.items():
        if kw in code or kw in info["name"].lower():
            results.append({
                "code": code,
                "name": info["name"],
                "market": "etf_option",
                "exchange": info["exchange"],
                "multiplier": info["multiplier"],
            })

    # Search index options
    for code, info in INDEX_OPTION_UNDERLYINGS.items():
        if kw in code or kw in info["name"].lower():
            results.append({
                "code": code,
                "name": info["name"],
                "market": "index_option",
                "exchange": info["exchange"],
                "multiplier": info["multiplier"],
            })

    # Search commodity options
    for code, info in COMMODITY_OPTION_UNDERLYINGS.items():
        if kw in code.lower() or kw in info["name"].lower():
            results.append({
                "code": code,
                "name": info["name"],
                "market": "commodity_option",
                "exchange": info["exchange"],
                "multiplier": info["multiplier"],
            })

    return results


# ═══════════════════════════════════════════════════════════════════════════════
# Main Dispatcher
# ═══════════════════════════════════════════════════════════════════════════════

def run_options_data(config: dict) -> dict:
    """Main entry point for options data queries.

    Actions:
        chain       — T-shaped option chain
        quote       — single contract quote
        contracts   — list available contracts
        klines      — historical daily klines
        iv_surface  — implied volatility surface
        search      — search option underlyings
    """
    action = config.get("action", "chain")
    underlying = config.get("underlying", "510050")
    symbol = config.get("symbol", "")
    expiry = config.get("expiry")
    market = config.get("market", "")

    # Auto-detect market if not specified
    if not market:
        market = _detect_option_market(underlying)

    try:
        if action == "chain":
            if market == "etf_option":
                return {"ok": True, "data": _get_etf_option_chain(underlying, expiry)}
            elif market == "index_option":
                return {"ok": True, "data": _get_index_option_chain(underlying, expiry)}
            elif market == "commodity_option":
                return {"ok": True, "data": _get_commodity_option_chain(underlying, expiry)}
            else:
                return {"ok": False, "error": f"Unsupported market: {market}"}

        elif action == "quote":
            if not symbol:
                return {"ok": False, "error": "symbol is required for quote action"}
            # Route by market; default to etf_option
            if market == "index_option":
                # For index options, fetch chain and find the matching contract
                chain = _get_index_option_chain(underlying or "")
                quote = _find_quote_in_chain(chain, symbol)
            elif market == "commodity_option":
                chain = _get_commodity_option_chain(underlying or "")
                quote = _find_quote_in_chain(chain, symbol)
            else:
                quote = _get_etf_option_quote(symbol, underlying)
            if quote:
                return {"ok": True, "data": quote}
            return {"ok": False, "error": f"No quote available for {symbol}"}

        elif action == "contracts":
            if market == "etf_option":
                contracts = _get_etf_option_contracts(underlying)
            else:
                # For index/commodity, contracts are embedded in chain data
                chain = (
                    _get_index_option_chain(underlying) if market == "index_option"
                    else _get_commodity_option_chain(underlying)
                )
                contracts = []
                for q in chain.get("calls", []) + chain.get("puts", []):
                    contracts.append({
                        "symbol": q.get("symbol"),
                        "underlying": underlying,
                        "strike": q.get("strike"),
                        "expiry": q.get("expiry"),
                        "optionType": q.get("optionType"),
                        "exchange": q.get("exchange", ""),
                    })
            return {"ok": True, "data": contracts}

        elif action == "klines":
            if not symbol:
                return {"ok": False, "error": "symbol is required for klines action"}
            start_date = config.get("startDate", "")
            end_date = config.get("endDate", "")
            # Route by market; default to etf_option
            if market == "index_option":
                klines = _get_index_option_klines(symbol, start_date, end_date)
            elif market == "commodity_option":
                klines = _get_commodity_option_klines(symbol, start_date, end_date)
            else:
                klines = _get_etf_option_klines(symbol, start_date, end_date)
            return {"ok": True, "data": klines}

        elif action == "iv_surface":
            return {"ok": True, "data": _build_iv_surface(underlying, market)}

        elif action == "search":
            keyword = config.get("keyword", underlying)
            results = search_option_underlying(keyword)
            return {"ok": True, "data": results}

        else:
            return {"ok": False, "error": f"Unknown action: {action}"}

    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        return {"ok": False, "error": str(e)}


# ── stdin → stdout JSON bridge ────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        cfg = get_input()
        result = run_options_data(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
