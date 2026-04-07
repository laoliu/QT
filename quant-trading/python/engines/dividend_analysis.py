"""
dividend_analysis — A-share dividend data and analysis.

Actions:
  history     — Historical dividend payments for a stock
  calendar    — Upcoming ex-dividend dates
  high_yield  — Screen for high dividend yield stocks
  detail      — Detailed dividend analysis for a stock (yield trend, payout ratio)

Data sourced from akshare public APIs.
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime
from typing import Any


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def history(symbol: str, limit: int = 10) -> dict[str, Any]:
    """Historical dividend payments for a given stock."""
    try:
        import akshare as ak

        # akshare: stock_history_dividend_detail (分红历史)
        try:
            df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
        except Exception:
            try:
                df = ak.stock_dividents_cninfo(symbol=symbol)
            except Exception:
                return {"error": f"无法获取 {symbol} 的分红历史数据", "dividends": []}

        if df is None or df.empty:
            return {"symbol": symbol, "dividends": [], "total_records": 0}

        records = []
        for _, row in df.head(limit).iterrows():
            record: dict[str, Any] = {}
            for col in df.columns:
                val = row[col]
                if hasattr(val, "isoformat"):
                    record[col] = str(val)[:10]
                else:
                    record[col] = val if not (isinstance(val, float) and val != val) else None
            records.append(record)

        return {
            "symbol": symbol,
            "total_records": len(df),
            "dividends": records,
        }

    except Exception as e:
        return {"error": f"获取分红历史失败: {str(e)}"}


def calendar(limit: int = 30) -> dict[str, Any]:
    """Upcoming ex-dividend dates across the market."""
    try:
        import akshare as ak

        # Try to get upcoming dividend calendar
        try:
            df = ak.stock_fhps_em(date=datetime.now().strftime("%Y%m%d"))
        except Exception:
            try:
                df = ak.stock_fhps_detail_em(date=datetime.now().strftime("%Y%m%d"))
            except Exception:
                return {"error": "无法获取分红日历数据", "upcoming": []}

        if df is None or df.empty:
            return {"upcoming": [], "count": 0}

        records = []
        for _, row in df.head(limit).iterrows():
            record: dict[str, Any] = {}
            for col in df.columns:
                val = row[col]
                if hasattr(val, "isoformat"):
                    record[col] = str(val)[:10]
                else:
                    record[col] = val if not (isinstance(val, float) and val != val) else None
            records.append(record)

        return {
            "count": len(records),
            "upcoming": records,
        }

    except Exception as e:
        return {"error": f"获取分红日历失败: {str(e)}"}


def high_yield(limit: int = 20, min_yield: float = 3.0) -> dict[str, Any]:
    """Screen for high dividend yield stocks."""
    try:
        import akshare as ak

        # Get all A-share stocks with dividend yield data
        try:
            df = ak.stock_zh_a_spot_em()
        except Exception:
            return {"error": "无法获取A股行情数据", "stocks": []}

        if df is None or df.empty:
            return {"error": "无数据", "stocks": []}

        # Look for dividend yield column
        yield_col = None
        for col in df.columns:
            if "股息" in col or "dividend" in col.lower():
                yield_col = col
                break

        if yield_col is None:
            # Fallback: try to calculate from known columns
            return {
                "error": "当前数据源不包含股息率字段，建议使用 market_data(action=fundamentals) 逐个查询",
                "stocks": [],
            }

        df[yield_col] = df[yield_col].apply(lambda x: _safe_float(x))
        df = df[df[yield_col] >= min_yield].sort_values(yield_col, ascending=False).head(limit)

        stocks = []
        for _, row in df.iterrows():
            code = str(row.get("代码", row.get("symbol", "")))
            name = str(row.get("名称", row.get("name", "")))
            price = _safe_float(row.get("最新价", row.get("close", 0)))
            change = _safe_float(row.get("涨跌幅", 0))
            div_yield = _safe_float(row[yield_col])

            stocks.append({
                "code": code,
                "name": name,
                "price": price,
                "change_pct": change,
                "dividend_yield": div_yield,
            })

        return {
            "min_yield": min_yield,
            "count": len(stocks),
            "stocks": stocks,
        }

    except Exception as e:
        return {"error": f"筛选高股息股失败: {str(e)}"}


def detail(symbol: str) -> dict[str, Any]:
    """Detailed dividend analysis for a stock."""
    try:
        hist = history(symbol, limit=20)
        dividends = hist.get("dividends", [])

        # Calculate statistics
        amounts = []
        for d in dividends:
            # Try to extract dividend amount from various column names
            for key in d:
                if "派" in str(key) or "分红" in str(key) or "每股" in str(key):
                    amt = _safe_float(d[key])
                    if amt > 0:
                        amounts.append(amt)
                        break

        analysis: dict[str, Any] = {
            "symbol": symbol,
            "dividend_count": len(dividends),
            "recent_dividends": dividends[:5],
        }

        if amounts:
            analysis["avg_dividend"] = round(sum(amounts) / len(amounts), 4)
            analysis["max_dividend"] = round(max(amounts), 4)
            analysis["min_dividend"] = round(min(amounts), 4)
            analysis["is_consistent"] = len(amounts) >= 3  # At least 3 years of dividends
            if len(amounts) >= 2:
                analysis["trend"] = (
                    "增长" if amounts[0] > amounts[-1]
                    else "下降" if amounts[0] < amounts[-1]
                    else "稳定"
                )
        else:
            analysis["note"] = "未能解析具体分红金额，请查看 recent_dividends 中的原始数据"

        return analysis

    except Exception as e:
        return {"error": f"分红详细分析失败: {str(e)}"}


def main():
    try:
        raw = sys.stdin.read()
        args = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        args = {}

    action = args.get("action", "history")
    symbol = args.get("symbol", "")
    limit = args.get("limit", 20)

    if action == "history":
        result = history(symbol, limit)
    elif action == "calendar":
        result = calendar(limit)
    elif action == "high_yield":
        result = high_yield(limit, args.get("min_yield", 3.0))
    elif action == "detail":
        result = detail(symbol)
    else:
        result = {"error": f"未知action: {action}"}

    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
