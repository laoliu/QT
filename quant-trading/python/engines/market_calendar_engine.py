"""
market_calendar_engine — Trading calendar: holidays, ex-dividend dates,
IPO dates, lock-up expiry, index rebalancing.

Fetches A-share calendar data via akshare.

Input (JSON via stdin / QUANT_CONFIG):
  {
    "action": "trading_days" | "holidays" | "events" | "weekly",
    "date": "2026-04-07",
    "days": 30,
    "count": 5,
    "event_types": "all" | "dividend,ipo,lockup,index",
    "symbol": "",
    "market": "cn"
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
        date = config.get("date", datetime.now().strftime("%Y-%m-%d"))
        count = config.get("count", 5)
        return _trading_days(date, count)
    elif action == "holidays":
        date = config.get("date", datetime.now().strftime("%Y-%m-%d"))
        days = config.get("days", 30)
        return _holidays(date, days)
    elif action == "events":
        date = config.get("date", datetime.now().strftime("%Y-%m-%d"))
        days = config.get("days", 30)
        event_types = config.get("event_types", "all")
        symbol = config.get("symbol", "")
        return _market_events(date, days, event_types, symbol)
    elif action == "weekly":
        date = config.get("date", datetime.now().strftime("%Y-%m-%d"))
        return _weekly_overview(date)
    else:
        return {"ok": False, "error": f"未知 action: {action}"}


# ── Trading Days ─────────────────────────────────────────────────────────────

def _trading_days(date_str: str, count: int) -> dict:
    """Check if a date is a trading day and get next N trading days."""
    try:
        import akshare as ak
    except ImportError:
        return {"ok": False, "error": "akshare not installed"}

    try:
        target = datetime.strptime(date_str, "%Y-%m-%d")

        # Get trading calendar for the year
        year = str(target.year)
        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty:
            return {"ok": False, "error": "无法获取交易日历"}

        # Convert to datetime
        trade_dates = set()
        for _, row in df.iterrows():
            try:
                td = str(row.iloc[0])[:10]
                trade_dates.add(td)
            except Exception:
                continue

        is_trading_day = date_str in trade_dates

        # Find next N trading days
        next_days = []
        check_date = target + timedelta(days=1)
        max_check = 60  # Safety limit
        while len(next_days) < count and max_check > 0:
            ds = check_date.strftime("%Y-%m-%d")
            if ds in trade_dates:
                next_days.append({
                    "date": ds,
                    "weekday": _weekday_cn(check_date.weekday()),
                })
            check_date += timedelta(days=1)
            max_check -= 1

        # Find previous trading day
        prev_day = None
        check_date = target - timedelta(days=1)
        max_check = 10
        while max_check > 0:
            ds = check_date.strftime("%Y-%m-%d")
            if ds in trade_dates:
                prev_day = ds
                break
            check_date -= timedelta(days=1)
            max_check -= 1

        return {
            "ok": True,
            "data": {
                "date": date_str,
                "weekday": _weekday_cn(target.weekday()),
                "is_trading_day": is_trading_day,
                "prev_trading_day": prev_day,
                "next_trading_days": next_days,
            },
        }
    except Exception as e:
        return {"ok": False, "error": f"交易日查询失败: {str(e)}"}


# ── Holidays ─────────────────────────────────────────────────────────────────

def _holidays(date_str: str, days: int) -> dict:
    """Get upcoming market holidays / rest days."""
    try:
        import akshare as ak
    except ImportError:
        return {"ok": False, "error": "akshare not installed"}

    try:
        target = datetime.strptime(date_str, "%Y-%m-%d")
        end_date = target + timedelta(days=days)

        # Get trading calendar
        df = ak.tool_trade_date_hist_sina()
        if df is None or df.empty:
            return {"ok": False, "error": "无法获取交易日历"}

        trade_dates = set()
        for _, row in df.iterrows():
            try:
                td = str(row.iloc[0])[:10]
                trade_dates.add(td)
            except Exception:
                continue

        # Find non-trading weekdays (holidays, not weekends)
        holidays = []
        check_date = target
        while check_date <= end_date:
            ds = check_date.strftime("%Y-%m-%d")
            weekday = check_date.weekday()
            if weekday < 5 and ds not in trade_dates:  # Weekday but not trading
                holidays.append({
                    "date": ds,
                    "weekday": _weekday_cn(weekday),
                    "type": "holiday",
                })
            check_date += timedelta(days=1)

        # Count consecutive rest days (including weekends)
        rest_periods = []
        check_date = target
        rest_start = None
        rest_count = 0
        while check_date <= end_date + timedelta(days=5):
            ds = check_date.strftime("%Y-%m-%d")
            if ds not in trade_dates:
                if rest_start is None:
                    rest_start = ds
                rest_count += 1
            else:
                if rest_start and rest_count >= 3:
                    rest_periods.append({
                        "start": rest_start,
                        "end": (check_date - timedelta(days=1)).strftime("%Y-%m-%d"),
                        "days": rest_count,
                    })
                rest_start = None
                rest_count = 0
            check_date += timedelta(days=1)

        return {
            "ok": True,
            "data": {
                "from_date": date_str,
                "days_ahead": days,
                "holiday_count": len(holidays),
                "holidays": holidays,
                "long_rest_periods": rest_periods,
            },
        }
    except Exception as e:
        return {"ok": False, "error": f"休市日历查询失败: {str(e)}"}


# ── Market Events ────────────────────────────────────────────────────────────

def _market_events(date_str: str, days: int, event_types: str, symbol: str) -> dict:
    """Get upcoming market events: dividends, IPOs, lock-up expiry, index rebalancing."""
    try:
        import akshare as ak
    except ImportError:
        return {"ok": False, "error": "akshare not installed"}

    types = event_types.split(",") if event_types != "all" else ["dividend", "ipo", "lockup"]
    target = datetime.strptime(date_str, "%Y-%m-%d")
    end_date = target + timedelta(days=days)
    all_events: list[dict] = []

    # ── Dividend events ──
    if "dividend" in types or "all" in types:
        try:
            df = ak.stock_dividend_cninfo()
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    try:
                        ex_date_str = str(row.get("除权除息日", ""))
                        if not ex_date_str or ex_date_str == "nan":
                            continue
                        ex_date = datetime.strptime(ex_date_str[:10], "%Y-%m-%d")
                        if target <= ex_date <= end_date:
                            s = str(row.get("股票代码", ""))
                            if symbol and s != symbol:
                                continue
                            all_events.append({
                                "date": ex_date_str[:10],
                                "type": "dividend",
                                "symbol": s,
                                "name": str(row.get("股票简称", "")),
                                "detail": f"派息{_safe_float(row.get('派息(元)'))}元/股",
                            })
                    except Exception:
                        continue
        except Exception:
            pass

    # ── IPO events ──
    if "ipo" in types or "all" in types:
        try:
            df = ak.stock_new_gh_cninfo()
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    try:
                        list_date_str = str(row.get("上市日期", ""))
                        if not list_date_str or list_date_str == "nan":
                            continue
                        list_date = datetime.strptime(list_date_str[:10], "%Y-%m-%d")
                        if target <= list_date <= end_date:
                            all_events.append({
                                "date": list_date_str[:10],
                                "type": "ipo",
                                "symbol": str(row.get("股票代码", "")),
                                "name": str(row.get("股票简称", "")),
                                "detail": f"发行价{row.get('发行价格', '未知')}元",
                            })
                    except Exception:
                        continue
        except Exception:
            pass

    # ── Lock-up expiry ──
    if "lockup" in types or "all" in types:
        try:
            today_str = target.strftime("%Y%m%d")
            end_str = end_date.strftime("%Y%m%d")
            df = ak.stock_restricted_release_queue_sina(today_str)
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    try:
                        release_date = str(row.get("解禁日期", ""))[:10]
                        s = str(row.get("股票代码", ""))
                        if symbol and s != symbol:
                            continue
                        all_events.append({
                            "date": release_date,
                            "type": "lockup",
                            "symbol": s,
                            "name": str(row.get("股票简称", "")),
                            "detail": f"解禁股数{row.get('解禁数量(万股)', '未知')}万股",
                        })
                    except Exception:
                        continue
        except Exception:
            pass

    # Sort by date
    all_events.sort(key=lambda x: x.get("date", ""))

    return {
        "ok": True,
        "data": {
            "from_date": date_str,
            "to_date": end_date.strftime("%Y-%m-%d"),
            "event_types": types,
            "count": len(all_events),
            "events": all_events[:100],
        },
    }


# ── Weekly Overview ──────────────────────────────────────────────────────────

def _weekly_overview(date_str: str) -> dict:
    """Get this week's trading overview."""
    try:
        import akshare as ak
    except ImportError:
        return {"ok": False, "error": "akshare not installed"}

    try:
        target = datetime.strptime(date_str, "%Y-%m-%d")
        # Find Monday of this week
        monday = target - timedelta(days=target.weekday())
        friday = monday + timedelta(days=4)

        # Get trading calendar
        df = ak.tool_trade_date_hist_sina()
        trade_dates = set()
        if df is not None:
            for _, row in df.iterrows():
                try:
                    td = str(row.iloc[0])[:10]
                    trade_dates.add(td)
                except Exception:
                    continue

        # Build daily overview
        week_days = []
        for i in range(5):
            d = monday + timedelta(days=i)
            ds = d.strftime("%Y-%m-%d")
            is_today = ds == date_str
            is_trading = ds in trade_dates
            week_days.append({
                "date": ds,
                "weekday": _weekday_cn(d.weekday()),
                "is_trading_day": is_trading,
                "is_today": is_today,
                "status": "今天" if is_today else ("交易日" if is_trading else "休市"),
            })

        trading_days_count = sum(1 for d in week_days if d["is_trading_day"])
        remaining = sum(1 for d in week_days if d["is_trading_day"] and d["date"] >= date_str)

        # Get events for this week
        events_result = _market_events(
            monday.strftime("%Y-%m-%d"),
            7,
            "all",
            "",
        )
        week_events = events_result.get("data", {}).get("events", []) if events_result.get("ok") else []

        return {
            "ok": True,
            "data": {
                "week_start": monday.strftime("%Y-%m-%d"),
                "week_end": friday.strftime("%Y-%m-%d"),
                "trading_days_total": trading_days_count,
                "trading_days_remaining": remaining,
                "days": week_days,
                "events": week_events[:20],
            },
        }
    except Exception as e:
        return {"ok": False, "error": f"周概览生成失败: {str(e)}"}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _weekday_cn(weekday: int) -> str:
    """Convert weekday number to Chinese."""
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    return names[weekday] if 0 <= weekday < 7 else ""


def _safe_float(val: Any) -> float:
    """Safely convert a value to float."""
    if val is None:
        return 0.0
    try:
        f = float(val)
        if f != f:  # NaN check
            return 0.0
        return f
    except (ValueError, TypeError):
        return 0.0


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        raw = os.environ.get("QUANT_CONFIG") or sys.stdin.read()
        cfg = json.loads(raw)
        result = run_market_calendar(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"ok": False, "error": traceback.format_exc()}, ensure_ascii=False))
