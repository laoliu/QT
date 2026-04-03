"""
Stock code helpers, date utilities, and statistics functions.

Migrated from jtstock/stock_tools/helpers.py and jtstock/common/basefunc.py,
modernized for Python 3.
"""

import datetime as dt
import math
import os
import re
from typing import Optional

# ── Proxy bypass for Chinese financial data hosts ─────────────────────────────
# Bypasses any local HTTP proxy (e.g. Clash on 127.0.0.1:7890) for CN domains
# so akshare requests reach eastmoney / xueqiu directly.
_BYPASS = (
    "eastmoney.com,push2his.eastmoney.com,push2.eastmoney.com,"
    "xueqiu.com,stock.xueqiu.com,akshare.akfast.top,"
    "dcfm.eastmoney.com,datacenter-web.eastmoney.com"
)
os.environ.setdefault("no_proxy", _BYPASS)
os.environ.setdefault("NO_PROXY", _BYPASS)
try:
    import requests as _requests

    _orig_send = _requests.Session.send

    def _no_proxy_send(self, request, **kwargs):
        host = request.url.split("/")[2].split(":")[0] if request.url else ""
        if any(h in host for h in ["eastmoney.com", "xueqiu.com", "akfast.top"]):
            kwargs["proxies"] = {}
        return _orig_send(self, request, **kwargs)

    _requests.Session.send = _no_proxy_send  # type: ignore[method-assign]
except Exception:
    pass


# ── Stock Code Helpers ────────────────────────────────────────────

def get_stock_exchange(stock_code: str) -> str:
    """Determine the exchange prefix for a stock code.

    Rules:
      '50','51','60','90','110' → 'sh'
      '00','13','18','15','16','20','30','39','115' → 'sz'
      starts with '5','6','9','7' → 'sh'
      otherwise → 'sz'
    """
    if stock_code.startswith(("sh", "sz", "SH", "SZ")):
        return stock_code[:2].lower()
    if stock_code.startswith(("50", "51", "60", "90", "110", "113", "132", "204")):
        return "sh"
    if stock_code.startswith(("00", "13", "18", "15", "16", "20", "30", "39", "115", "1318")):
        return "sz"
    if stock_code.startswith(("5", "6", "9", "7")):
        return "sh"
    return "sz"


def gen_stock_header(stock: str) -> str:
    """Prepend exchange prefix to a stock code.

    Examples:
      '600519' → 'sh600519'
      '000001' → 'sz000001'
      'sh600519' → 'sh600519' (already prefixed)
    """
    if stock.startswith(("sh", "sz", "SH", "SZ")):
        return stock.lower()
    code = f"{int(stock):06d}" if stock.isdigit() else stock[-6:]
    return get_stock_exchange(code) + code


def get_stock_type(code: str) -> str:
    """Determine if a code is 'stock' or 'fund'."""
    if code.startswith(("00", "30", "60")):
        return "stock"
    return "fund"


def strip_exchange(code: str) -> str:
    """Remove exchange prefix if present. 'sh600519' → '600519'."""
    if len(code) > 6 and code[:2].lower() in ("sh", "sz"):
        return code[2:]
    return code


# ── Date Utilities ────────────────────────────────────────────────

QUARTER_DATES = [331, 630, 930, 1231]
MONTH_DATES = [131, 228, 331, 430, 531, 630, 731, 831, 930, 1031, 1130, 1231]
HALFYEAR_DATES = [630, 1231]


def get_pre_date_nearby(date_int: int, date_type: str = "quarter") -> int:
    """Get the nearest previous financial report date.

    Args:
      date_int: Date as integer YYYYMMDD.
      date_type: 'month', 'quarter', or 'halfyear'.

    Returns:
      Report date as YYYYMMDD integer.
    """
    date_range = {"month": MONTH_DATES, "quarter": QUARTER_DATES, "halfyear": HALFYEAR_DATES}.get(
        date_type, QUARTER_DATES
    )
    mmdd = date_int % 10000
    yyyy = date_int // 10000
    calc_date = 0
    for d in reversed(date_range):
        if mmdd > d:
            calc_date = d
            break
    if calc_date == 0:
        calc_date = date_range[-1]
        yyyy -= 1
    return yyyy * 10000 + calc_date


def get_offset_datetime(dt_int: int, offset: int) -> int:
    """Get the date offset by N calendar days from dt_int (YYYYMMDD).

    Negative offset = going back.
    """
    base = dt.datetime(dt_int // 10000, dt_int // 100 % 100, dt_int % 100)
    result = base + dt.timedelta(days=offset)
    return int(result.strftime("%Y%m%d"))


def get_offset_days(a_dt: int, b_dt: int) -> int:
    """Get the absolute number of days between two dates (YYYYMMDD)."""
    a = dt.datetime(a_dt // 10000, a_dt // 100 % 100, a_dt % 100)
    b = dt.datetime(b_dt // 10000, b_dt // 100 % 100, b_dt % 100)
    return abs((a - b).days)


def get_week_day(a_dt: int) -> int:
    """Get day of week (1=Monday, 7=Sunday)."""
    d = dt.datetime(a_dt // 10000, a_dt // 100 % 100, a_dt % 100)
    return d.weekday() + 1


def is_trade_time(hhmm: int) -> bool:
    """Check if a timestamp is within A-share trading hours."""
    t = hhmm % 10000
    return (930 <= t <= 1210) or (1259 <= t <= 1510)


def date_to_str(dt_int: int) -> str:
    """Convert YYYYMMDD int to 'YYYY-MM-DD' string."""
    return f"{dt_int // 10000}-{dt_int // 100 % 100:02d}-{dt_int % 100:02d}"


def str_to_date(date_str: str) -> int:
    """Convert 'YYYY-MM-DD' string to YYYYMMDD int."""
    parts = date_str.replace("/", "-").split("-")
    return int(parts[0]) * 10000 + int(parts[1]) * 100 + int(parts[2])


# ── Statistics ────────────────────────────────────────────────────


def get_average(data: list[float]) -> float:
    """Compute the arithmetic mean of a list."""
    if not data:
        return 0.0
    return sum(data) / len(data)


def get_variance(data: list[float]) -> float:
    """Compute the population variance."""
    if not data:
        return 0.0
    avg = get_average(data)
    return sum((x - avg) ** 2 for x in data) / len(data)


def get_standard_deviation(data: list[float]) -> float:
    """Compute the population standard deviation."""
    return math.sqrt(get_variance(data))


def calc_yield(end_value: float, start_value: float, years: float) -> float:
    """Compute compound annual growth rate (CAGR).

    CAGR = (end / start) ^ (1 / years) - 1
    """
    if start_value <= 0 or years <= 0:
        return 0.0
    return math.pow(end_value / start_value, 1.0 / years) - 1
