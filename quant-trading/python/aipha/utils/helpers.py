"""Common helper functions for stock code, date, and statistics.

Provides stock exchange detection, code formatting, date offset
calculations, and basic statistical functions.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta
from typing import List, Optional

__all__ = [
    "get_stock_exchange", "gen_stock_header", "get_stock_type",
    "strip_exchange", "get_offset_days", "is_trade_time",
    "get_average", "get_variance", "get_standard_deviation",
    "calc_yield",
]

# Report date constants
QUARTER_DATES = ["0331", "0630", "0930", "1231"]
MONTH_DATES = [f"{m:02d}01" for m in range(1, 13)]


def get_stock_exchange(stock_code: str) -> str:
    """Determine exchange from stock code: 'sh' or 'sz'."""
    code = stock_code.split(".")[0].strip()
    if code.startswith(("6", "5", "9")):
        return "sh"
    return "sz"


def gen_stock_header(stock: str) -> str:
    """Prepend exchange prefix: '600519' -> 'sh600519'."""
    code = stock.split(".")[0].strip()
    ex = get_stock_exchange(code)
    return f"{ex}{code}"


def get_stock_type(code: str) -> str:
    """Return 'stock' or 'fund' based on code prefix."""
    c = code.split(".")[0].strip()
    if c.startswith(("5", "1")):
        return "fund"
    return "stock"


def strip_exchange(code: str) -> str:
    """Strip exchange suffix: '600519.SH' -> '600519'."""
    return code.split(".")[0].strip()


def get_offset_days(a_date: str, b_date: str) -> int:
    """Days between two dates (YYYYMMDD format). Returns a - b."""
    try:
        da = datetime.strptime(a_date, "%Y%m%d")
        db = datetime.strptime(b_date, "%Y%m%d")
        return (da - db).days
    except (ValueError, TypeError):
        return 0


def is_trade_time(hhmm: str) -> bool:
    """Check if time (HHMM) is within A-share trading hours."""
    try:
        h = int(hhmm[:2])
        m = int(hhmm[2:4])
        t = h * 60 + m
        # 9:30-11:30, 13:00-15:00
        return (570 <= t <= 690) or (780 <= t <= 900)
    except (ValueError, IndexError):
        return False


def get_average(data: List[float]) -> float:
    return sum(data) / len(data) if data else 0.0


def get_variance(data: List[float]) -> float:
    if len(data) < 2:
        return 0.0
    avg = get_average(data)
    return sum((x - avg) ** 2 for x in data) / (len(data) - 1)


def get_standard_deviation(data: List[float]) -> float:
    return math.sqrt(get_variance(data))


def calc_yield(end_value: float, start_value: float, years: float) -> float:
    """Calculate CAGR (compound annual growth rate)."""
    if start_value <= 0 or years <= 0 or end_value <= 0:
        return 0.0
    return (end_value / start_value) ** (1.0 / years) - 1.0
