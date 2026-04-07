"""
dividend_engine — Dividend history, upcoming ex-dividend dates, yield ranking.

Fetches A-share dividend data via akshare.

Input (JSON via stdin / QUANT_CONFIG):
  {
    "action": "history" | "upcoming" | "yield_rank" | "analysis",
    "symbol": "600519",
    "market": "cn",
    "years": 5,
    "days": 30,
    "universe": "hs300",
    "limit": 20
  }

Output: JSON with dividend data.
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


def run_dividend_engine(config: dict) -> dict:
    """Dispatch dividend engine actions."""
    action = config.get("action", "history")
    market = config.get("market", "cn")

    if action == "history":
        symbol = config.get("symbol", "")
        years = config.get("years", 5)
        if not symbol:
            return {"ok": False, "error": "symbol is required"}
        return _dividend_history(symbol, years)
    elif action == "upcoming":
        days = config.get("days", 30)
        limit = config.get("limit", 20)
        return _upcoming_dividends(days, limit)
    elif action == "yield_rank":
        universe = config.get("universe", "hs300")
        limit = config.get("limit", 20)
        return _yield_rank(universe, limit)
    elif action == "analysis":
        symbol = config.get("symbol", "")
        years = config.get("years", 5)
        if not symbol:
            return {"ok": False, "error": "symbol is required"}
        return _dividend_analysis(symbol, years)
    else:
        return {"ok": False, "error": f"未知 action: {action}"}


# ── Dividend History ─────────────────────────────────────────────────────────

def _dividend_history(symbol: str, years: int) -> dict:
    """Fetch dividend history for a stock."""
    try:
        import akshare as ak
    except ImportError:
        return {"ok": False, "error": "akshare not installed"}

    try:
        # Get dividend data via akshare
        df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
        if df is None or df.empty:
            return {"ok": True, "data": {"symbol": symbol, "records": [], "message": "无分红记录"}}

        records = []
        cutoff_year = datetime.now().year - years
        for _, row in df.iterrows():
            try:
                year = int(str(row.get("公告日期", ""))[:4]) if row.get("公告日期") else None
                if year and year < cutoff_year:
                    continue
                records.append({
                    "announce_date": str(row.get("公告日期", "")),
                    "ex_date": str(row.get("除权除息日", "")),
                    "record_date": str(row.get("股权登记日", "")),
                    "dividend_per_share": _safe_float(row.get("派息(税前)(元)")),
                    "bonus_shares": _safe_float(row.get("送股(股)")),
                    "convert_shares": _safe_float(row.get("转增(股)")),
                    "progress": str(row.get("进度", "")),
                })
            except Exception:
                continue

        return {
            "ok": True,
            "data": {
                "symbol": symbol,
                "years": years,
                "total_records": len(records),
                "records": records[:50],  # Limit to avoid huge output
            },
        }
    except Exception as e:
        return {"ok": False, "error": f"获取分红历史失败: {str(e)}"}


# ── Upcoming Dividends ───────────────────────────────────────────────────────

def _upcoming_dividends(days: int, limit: int) -> dict:
    """Fetch stocks with upcoming ex-dividend dates."""
    try:
        import akshare as ak
    except ImportError:
        return {"ok": False, "error": "akshare not installed"}

    try:
        # Get recent dividend announcements
        df = ak.stock_dividend_cninfo()
        if df is None or df.empty:
            return {"ok": True, "data": {"records": [], "message": "暂无即将除权除息的股票"}}

        today = datetime.now()
        end_date = today + timedelta(days=days)
        records = []

        for _, row in df.iterrows():
            try:
                ex_date_str = str(row.get("除权除息日", ""))
                if not ex_date_str or ex_date_str == "nan" or ex_date_str == "":
                    continue
                ex_date = datetime.strptime(ex_date_str[:10], "%Y-%m-%d")
                if today <= ex_date <= end_date:
                    records.append({
                        "symbol": str(row.get("股票代码", "")),
                        "name": str(row.get("股票简称", "")),
                        "ex_date": ex_date_str[:10],
                        "record_date": str(row.get("股权登记日", ""))[:10] if row.get("股权登记日") else "",
                        "dividend_per_share": _safe_float(row.get("派息(元)")),
                        "bonus_shares": _safe_float(row.get("送股(股)")),
                        "convert_shares": _safe_float(row.get("转增(股)")),
                    })
            except Exception:
                continue

        # Sort by ex_date ascending
        records.sort(key=lambda x: x.get("ex_date", ""))

        return {
            "ok": True,
            "data": {
                "days": days,
                "count": len(records[:limit]),
                "records": records[:limit],
            },
        }
    except Exception as e:
        return {"ok": False, "error": f"获取除权除息日历失败: {str(e)}"}


# ── Yield Ranking ────────────────────────────────────────────────────────────

def _yield_rank(universe: str, limit: int) -> dict:
    """Rank stocks by dividend yield."""
    try:
        import akshare as ak
    except ImportError:
        return {"ok": False, "error": "akshare not installed"}

    try:
        # Get stock list with dividend yield
        df = ak.stock_dividend_cninfo()
        if df is None or df.empty:
            return {"ok": True, "data": {"records": [], "message": "暂无股息数据"}}

        # Calculate yield where possible — use latest year's data
        records = []
        seen = set()
        for _, row in df.iterrows():
            try:
                symbol = str(row.get("股票代码", ""))
                if symbol in seen:
                    continue
                seen.add(symbol)
                dps = _safe_float(row.get("派息(元)"))
                if dps and dps > 0:
                    records.append({
                        "symbol": symbol,
                        "name": str(row.get("股票简称", "")),
                        "dividend_per_share": dps,
                        "announce_date": str(row.get("公告日期", ""))[:10] if row.get("公告日期") else "",
                    })
            except Exception:
                continue

        # Sort by dividend per share descending (proxy for yield)
        records.sort(key=lambda x: x.get("dividend_per_share", 0), reverse=True)

        return {
            "ok": True,
            "data": {
                "universe": universe,
                "count": len(records[:limit]),
                "records": records[:limit],
                "note": "排名按每股派息金额排序；精确股息率需要结合当前股价计算",
            },
        }
    except Exception as e:
        return {"ok": False, "error": f"获取股息率排行失败: {str(e)}"}


# ── Dividend Analysis ────────────────────────────────────────────────────────

def _dividend_analysis(symbol: str, years: int) -> dict:
    """Comprehensive dividend analysis for a single stock."""
    history_result = _dividend_history(symbol, years)
    if not history_result.get("ok"):
        return history_result

    records = history_result.get("data", {}).get("records", [])
    if not records:
        return {
            "ok": True,
            "data": {
                "symbol": symbol,
                "message": "无分红记录，该股票近期未进行分红",
                "dividend_score": 0,
            },
        }

    # Calculate stats
    cash_dividends = [r["dividend_per_share"] for r in records if r.get("dividend_per_share") and r["dividend_per_share"] > 0]
    total_cash = sum(cash_dividends) if cash_dividends else 0
    avg_cash = total_cash / len(cash_dividends) if cash_dividends else 0
    max_cash = max(cash_dividends) if cash_dividends else 0
    min_cash = min(cash_dividends) if cash_dividends else 0
    consecutive_years = len(cash_dividends)

    # Score: consistency + amount
    score = min(10, consecutive_years * 2)  # Up to 10 points for consistency
    if avg_cash >= 1.0:
        score += 5
    elif avg_cash >= 0.5:
        score += 3
    elif avg_cash >= 0.1:
        score += 1

    # Trend: increasing or decreasing dividends
    trend = "稳定"
    if len(cash_dividends) >= 3:
        recent = cash_dividends[:3]
        if all(recent[i] >= recent[i + 1] for i in range(len(recent) - 1)):
            trend = "递增"
            score += 2
        elif all(recent[i] <= recent[i + 1] for i in range(len(recent) - 1)):
            trend = "递减"

    return {
        "ok": True,
        "data": {
            "symbol": symbol,
            "years_analyzed": years,
            "total_distributions": len(records),
            "cash_dividend_years": consecutive_years,
            "total_cash_dividend": round(total_cash, 4),
            "avg_cash_dividend": round(avg_cash, 4),
            "max_cash_dividend": round(max_cash, 4),
            "min_cash_dividend": round(min_cash, 4),
            "dividend_trend": trend,
            "dividend_score": min(score, 15),
            "latest_records": records[:5],
            "assessment": _assess_dividend(consecutive_years, avg_cash, trend, years),
        },
    }


def _assess_dividend(years: int, avg: float, trend: str, total_years: int) -> str:
    """Generate a human-readable dividend assessment."""
    if years == 0:
        return "该股票无现金分红记录，不适合分红策略"
    if years >= total_years * 0.8 and avg >= 0.5:
        return f"优秀的分红股：近{total_years}年中{years}年分红，平均每股{avg:.2f}元，分红{trend}"
    if years >= total_years * 0.5:
        return f"分红较规律：近{total_years}年中{years}年分红，平均每股{avg:.2f}元"
    return f"分红不稳定：近{total_years}年仅{years}年分红"


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
        result = run_dividend_engine(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"ok": False, "error": traceback.format_exc()}, ensure_ascii=False))
