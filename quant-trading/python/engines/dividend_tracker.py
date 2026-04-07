"""
dividend_tracker — Dividend and ex-dividend date tracking for A-shares.

Tracks upcoming and historical dividend events:
- Upcoming ex-dividend dates (除权除息日)
- Dividend history for a stock
- High-dividend screening
- Dividend calendar

Input:
  {
    "action": "upcoming" | "history" | "calendar" | "high_yield",
    "symbol": "600519",
    "days": 30,
    "limit": 20
  }

Output:
  { "dividends": [...], "calendar": [...] }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def run_dividend_tracker(config: dict) -> dict:
    """Dispatch dividend tracking."""
    action = config.get("action", "upcoming")

    if action == "upcoming":
        days = config.get("days", 30)
        limit = config.get("limit", 30)
        return _upcoming_dividends(days, limit)
    elif action == "history":
        symbol = config.get("symbol", "")
        if not symbol:
            return {"error": "请提供股票代码 (symbol)"}
        return _dividend_history(symbol)
    elif action == "calendar":
        days = config.get("days", 30)
        return _dividend_calendar(days)
    elif action == "high_yield":
        limit = config.get("limit", 20)
        min_yield = config.get("min_yield", 3.0)
        return _high_yield_stocks(limit, min_yield)
    else:
        return {"error": f"未知action: {action}"}


def _upcoming_dividends(days: int, limit: int) -> dict:
    """Get upcoming ex-dividend dates within N days."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    errors: list[str] = []

    try:
        # Fetch dividend data for current year
        year = str(datetime.now().year)
        df = ak.stock_dividents_cninfo(date=year)

        if df is None or df.empty:
            # Try previous year if current year has no data
            year = str(datetime.now().year - 1)
            df = ak.stock_dividents_cninfo(date=year)

        if df is None or df.empty:
            return {"action": "upcoming", "dividends": [], "count": 0,
                    "note": "暂无分红数据"}

        now = datetime.now()
        target_end = now + timedelta(days=days)

        upcoming = []
        for _, row in df.iterrows():
            ex_date_str = str(row.get("除权除息日", ""))
            if not ex_date_str or ex_date_str == "nan" or ex_date_str == "-":
                continue

            try:
                ex_date = datetime.strptime(ex_date_str[:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                continue

            if now <= ex_date <= target_end:
                dividend_info = {
                    "code": str(row.get("证券代码", "")),
                    "name": str(row.get("证券简称", "")),
                    "ex_date": ex_date_str[:10],
                    "record_date": str(row.get("股权登记日", ""))[:10],
                    "cash_dividend": _safe_float(row.get("派息比例")),
                    "bonus_ratio": _safe_float(row.get("送股比例")),
                    "convert_ratio": _safe_float(row.get("转增比例")),
                    "days_until": (ex_date - now).days,
                }
                upcoming.append(dividend_info)

        upcoming.sort(key=lambda x: x.get("days_until", 999))
        upcoming = upcoming[:limit]

        result: dict[str, Any] = {
            "action": "upcoming",
            "dividends": upcoming,
            "count": len(upcoming),
            "query_range_days": days,
        }
        if errors:
            result["errors"] = errors
        return result

    except Exception as e:
        return {"error": f"获取分红数据失败: {str(e)[:100]}"}


def _dividend_history(symbol: str) -> dict:
    """Get dividend history for a specific stock."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    try:
        df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
        if df is None or df.empty:
            return {"action": "history", "symbol": symbol, "dividends": [],
                    "note": "该股票无分红记录"}

        dividends = []
        for _, row in df.iterrows():
            dividend = {
                "report_date": str(row.get("报告期", ""))[:10],
                "record_date": str(row.get("股权登记日", ""))[:10],
                "ex_date": str(row.get("除权除息日", ""))[:10],
                "cash_per_share": _safe_float(row.get("每股派息(税前)")),
                "bonus_ratio": str(row.get("送股", "")),
                "convert_ratio": str(row.get("转增", "")),
            }
            dividends.append(dividend)

        # Calculate TTM dividend yield if we have price data
        ttm_cash = 0
        this_year = datetime.now().year
        for d in dividends[:4]:  # Look at last 4 distributions
            cash = d.get("cash_per_share")
            if cash and isinstance(cash, (int, float)):
                ttm_cash += cash

        result: dict[str, Any] = {
            "action": "history",
            "symbol": symbol,
            "dividends": dividends[:20],
            "total_records": len(dividends),
            "ttm_dividend_per_share": round(ttm_cash, 4) if ttm_cash > 0 else None,
        }

        return result

    except Exception as e:
        return {"error": f"获取{symbol}分红历史失败: {str(e)[:100]}"}


def _dividend_calendar(days: int) -> dict:
    """Dividend calendar — grouped by date."""
    result = _upcoming_dividends(days, 100)
    if "error" in result:
        return result

    dividends = result.get("dividends", [])

    # Group by ex_date
    calendar: dict[str, list] = {}
    for d in dividends:
        date = d.get("ex_date", "未知")
        if date not in calendar:
            calendar[date] = []
        calendar[date].append(d)

    return {
        "action": "calendar",
        "calendar": calendar,
        "total_events": len(dividends),
        "query_range_days": days,
    }


def _high_yield_stocks(limit: int, min_yield: float) -> dict:
    """Screen for high dividend yield stocks."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    try:
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return {"error": "无法获取A股行情"}

        # Filter by stocks that have dividend yield data in the spot data
        # We'll use a different approach - fetch known dividend data
        high_yield = []

        # Try to get stocks with known high yields from fund screening
        try:
            div_df = ak.stock_a_high_dividend_em()
            if div_df is not None and not div_df.empty:
                for _, row in div_df.head(limit).iterrows():
                    stock = {
                        "code": str(row.get("代码", "")),
                        "name": str(row.get("名称", "")),
                        "price": _safe_float(row.get("最新价")),
                        "dividend_yield": _safe_float(row.get("股息率")),
                        "change_pct": _safe_float(row.get("涨跌幅")),
                        "pe": _safe_float(row.get("市盈率")),
                    }
                    dy = stock.get("dividend_yield")
                    if dy and dy >= min_yield:
                        high_yield.append(stock)
        except Exception:
            # Fallback: use spot data with available yield info
            pass

        if not high_yield:
            return {
                "action": "high_yield",
                "stocks": [],
                "note": "高股息数据接口暂不可用，请通过 stock_screen 配合 dividend_yield 参数筛选",
            }

        high_yield.sort(key=lambda x: x.get("dividend_yield", 0) or 0, reverse=True)

        return {
            "action": "high_yield",
            "stocks": high_yield[:limit],
            "count": len(high_yield),
            "min_yield_filter": min_yield,
        }

    except Exception as e:
        return {"error": f"获取高股息数据失败: {str(e)[:100]}"}


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return round(f, 4) if abs(f) < 1e15 else None
    except (ValueError, TypeError):
        return None


# ── CLI entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    try:
        if "--json" in sys.argv:
            raw = sys.stdin.read()
            cfg = json.loads(raw) if raw.strip() else {}
        else:
            cfg = json.loads(os.environ.get("QUANT_CONFIG", "{}"))

        result = run_dividend_tracker(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
