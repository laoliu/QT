"""
market_calendar 66Trading calendar, holidays, IPO dates, ex-dividend dates, index rebalancing.

Fetches A-share trading calendar, holiday schedule, IPO subscription info,
ex-dividend dates, and index constituent changes from akshare/baostock.

Input (JSON via stdin / QUANT_CONFIG):
  {
    "action": "trading_days" | "holidays" | "ipo_calendar" | "ex_dividend" | "index_rebalance",
    "date": "2025-04-07",
    "start_date": "2025-01-01",
    "end_date": "2025-12-31",
    "year": "2025",
    "symbol": "600519",
    "index_code": "000300"
  }

Output: JSON with calendar data.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

# ── Proxy bypass ──────────────────────────────────────────────────────────────
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def run_market_calendar(config: dict) -> dict:
    """Dispatch market calendar actions."""
    action = config.get("action", "trading_days")

    if action == "trading_days":
        return _trading_days(config)
    elif action == "holidays":
        return _holidays(config)
    elif action == "ipo_calendar":
        return _ipo_calendar(config)
    elif action == "ex_dividend":
        return _ex_dividend(config)
    elif action == "index_rebalance":
        return _index_rebalance(config)
    else:
        return {"error": f"Unknown action: {action}"}


def _trading_days(config: dict) -> dict:
    """Check if a date is a trading day, or list trading days in a range."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    try:
        date_str = config.get("date")
        start_date = config.get("start_date")
        end_date = config.get("end_date")

        # Get trading calendar from SSE
        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty:
            return {"error": "Failed to fetch trading calendar"}

        trade_dates = set()
        for _, row in df.iterrows():
            d = str(row.iloc[0])[:10]
            trade_dates.add(d)

        if date_str:
            normalized = date_str[:10]
            is_trading = normalized in trade_dates
            # Find next trading day
            next_td = None
            check = datetime.strptime(normalized, "%Y-%m-%d") + timedelta(days=1)
            for _ in range(10):
                if check.strftime("%Y-%m-%d") in trade_dates:
                    next_td = check.strftime("%Y-%m-%d")
                    break
                check += timedelta(days=1)
            return {
                "date": normalized,
                "is_trading_day": is_trading,
                "next_trading_day": next_td,
            }

        if start_date and end_date:
            days_in_range = sorted(
                d for d in trade_dates if start_date <= d <= end_date
            )
            return {
                "start_date": start_date,
                "end_date": end_date,
                "trading_day_count": len(days_in_range),
                "trading_days": days_in_range[-30:] if len(days_in_range) > 30 else days_in_range,
                "note": f"Showing last 30 of {len(days_in_range)}" if len(days_in_range) > 30 else None,
            }

        # Default: show next 10 trading days
        today = datetime.now().strftime("%Y-%m-%d")
        future = sorted(d for d in trade_dates if d >= today)[:10]
        return {"upcoming_trading_days": future}

    except Exception as e:
        return {"error": f"trading_days failed: {str(e)}"}


def _holidays(config: dict) -> dict:
    """List market holidays for a given year."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    try:
        year = config.get("year", str(datetime.now().year))

        # Get all calendar days and trading days for the year, compute non-trading weekdays
        df = ak.tool_trade_date_hist_sina()
        trade_dates = set()
        for _, row in df.iterrows():
            d = str(row.iloc[0])[:10]
            if d.startswith(year):
                trade_dates.add(d)

        # Find consecutive non-trading day blocks (exclude weekends-only)
        from datetime import date as dt_date
        start = dt_date(int(year), 1, 1)
        end = dt_date(int(year), 12, 31)
        holidays = []
        current = start
        block_start = None
        block_days = 0

        while current <= end:
            ds = current.strftime("%Y-%m-%d")
            is_weekend = current.weekday() >= 5
            is_trading = ds in trade_dates

            if not is_trading and not is_weekend:
                if block_start is None:
                    block_start = ds
                    block_days = 1
                else:
                    block_days += 1
            else:
                if block_start is not None:
                    holidays.append({
                        "date": block_start,
                        "days": block_days,
                        "name": _guess_holiday_name(block_start),
                    })
                    block_start = None
                    block_days = 0
            current += timedelta(days=1)

        if block_start is not None:
            holidays.append({
                "date": block_start,
                "days": block_days,
                "name": _guess_holiday_name(block_start),
            })

        return {"year": year, "holidays": holidays}

    except Exception as e:
        return {"error": f"holidays failed: {str(e)}"}


def _guess_holiday_name(date_str: str) -> str:
    """Guess Chinese holiday name from date."""
    month_day = date_str[5:10]
    if "01-01" <= month_day <= "01-03":
        return "元旦"
    elif "02-" in month_day[:3] and int(month_day[3:5]) <= 20:
        return "春节"
    elif "04-04" <= month_day <= "04-06":
        return "清明节"
    elif "05-01" <= month_day <= "05-05":
        return "劳动节"
    elif "06-" in month_day[:3]:
        return "端午节"
    elif "09-" in month_day[:3] or "10-01" <= month_day <= "10-07":
        return "中秋/国庆"
    return "休市"


def _ipo_calendar(config: dict) -> dict:
    """Get upcoming IPO subscription dates."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    try:
        df = ak.stock_xgsglb_em(symbol="0007")
        if df is None or df.empty:
            return {"ipo_list": [], "note": "暂无新股数据"}

        result = []
        for _, row in df.head(20).iterrows():
            item: dict[str, Any] = {}
            cols = df.columns.tolist()
            for i, col in enumerate(cols):
                val = row.iloc[i]
                if hasattr(val, 'strftime'):
                    val = val.strftime("%Y-%m-%d")
                elif hasattr(val, 'item'):
                    val = val.item()
                item[col] = val
            result.append(item)

        return {"ipo_list": result}

    except Exception as e:
        return {"error": f"ipo_calendar failed: {str(e)}"}


def _ex_dividend(config: dict) -> dict:
    """Get ex-dividend dates."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    try:
        symbol = config.get("symbol")

        if symbol:
            # Single stock dividend history
            df = ak.stock_history_dividend_detail(symbol=symbol, indicator="00")
            if df is None or df.empty:
                return {"ex_dividend_list": [], "note": f"{symbol} 190004"}

            items = []
            for _, row in df.head(20).iterrows():
                item: dict[str, Any] = {}
                for col in df.columns:
                    val = row[col]
                    if hasattr(val, 'strftime'):
                        val = val.strftime("%Y-%m-%d")
                    elif hasattr(val, 'item'):
                        val = val.item()
                    item[col] = val
                items.append(item)
            return {"symbol": symbol, "ex_dividend_list": items}

        else:
            # Market-wide upcoming ex-dividend
            today = datetime.now().strftime("%Y%m%d")
            df = ak.stock_fhps_em(date=today[:6])
            if df is None or df.empty:
                return {"ex_dividend_list": [], "note": "15190704"}

            items = []
            for _, row in df.head(30).iterrows():
                item: dict[str, Any] = {}
                for col in df.columns:
                    val = row[col]
                    if hasattr(val, 'strftime'):
                        val = val.strftime("%Y-%m-%d")
                    elif hasattr(val, 'item'):
                        val = val.item()
                    item[col] = val
                items.append(item)
            return {"ex_dividend_list": items}

    except Exception as e:
        return {"error": f"ex_dividend failed: {str(e)}"}


def _index_rebalance(config: dict) -> dict:
    """Get index rebalancing info."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    try:
        index_code = config.get("index_code", "000300")
        index_names = {
            "000300": "沪深300",
            "000016": "0850",
            "000905": "08500",
            "000852": "081000",
            "399006": "0908",
            "000688": "0050",
        }
        index_name = index_names.get(index_code, f"08{index_code}")

        # CSI indices rebalance in June and December (effective 2nd Friday)
        now = datetime.now()
        year = now.year
        rebalance_months = [6, 12]
        next_dates = []
        for m in rebalance_months:
            # Approximate: 2nd Friday of June/December
            from datetime import date as dt_date
            for y in [year, year + 1]:
                d = dt_date(y, m, 1)
                # Find 2nd Friday
                fridays = 0
                while fridays < 2:
                    if d.weekday() == 4:
                        fridays += 1
                        if fridays == 2:
                            break
                    d += timedelta(days=1)
                if d > now.date():
                    next_dates.append(d.strftime("%Y-%m-%d"))

        next_dates.sort()

        return {
            "index_code": index_code,
            "index_name": index_name,
            "rebalance_schedule": "每年6月和12月的第二个星期五调整成分股",
            "upcoming_rebalance_dates": next_dates[:3],
            "note": "实际调整日期以交易所公告为准",
        }

    except Exception as e:
        return {"error": f"index_rebalance failed: {str(e)}"}


# ── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    raw = os.environ.get("QUANT_CONFIG", "")
    if not raw:
        raw = sys.stdin.read()
    try:
        cfg = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        cfg = {}

    try:
        result = run_market_calendar(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
