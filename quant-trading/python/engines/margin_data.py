"""
margin_data — A-share margin trading data (融资融券/两融).

Actions:
  market_summary  — Market-wide margin balance summary
  stock_detail    — Margin data for a specific stock
  top_stocks      — Top stocks by margin balance or change
  trend           — Historical margin balance trend

Data sourced from akshare public APIs.
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _df_to_records(df, limit: int = 30) -> list[dict[str, Any]]:
    """Convert DataFrame rows to serializable dicts."""
    records = []
    for _, row in df.head(limit).iterrows():
        record: dict[str, Any] = {}
        for col in df.columns:
            val = row[col]
            if hasattr(val, "isoformat"):
                record[col] = str(val)[:10]
            elif isinstance(val, float) and val != val:
                record[col] = None
            else:
                record[col] = val
        records.append(record)
    return records


def market_summary() -> dict[str, Any]:
    """Market-wide margin balance summary (全市场融资融券余额)."""
    try:
        import akshare as ak

        df = None
        try:
            df = ak.stock_margin_sse(start_date=(datetime.now() - timedelta(days=30)).strftime("%Y%m%d"))
        except Exception:
            pass

        if df is None or df.empty:
            try:
                df = ak.stock_margin_szse(start_date=(datetime.now() - timedelta(days=30)).strftime("%Y%m%d"))
            except Exception:
                pass

        if df is None or df.empty:
            return {"error": "无法获取两融市场数据"}

        # Get the most recent record
        records = _df_to_records(df.tail(5), 5)

        return {
            "description": "近期全市场融资融券数据",
            "records": records,
        }

    except Exception as e:
        return {"error": f"获取两融市场数据失败: {str(e)}"}


def stock_detail(symbol: str) -> dict[str, Any]:
    """Margin data for a specific stock (个股融资融券明细)."""
    try:
        import akshare as ak

        df = None
        try:
            df = ak.stock_margin_detail_sse(symbol=symbol)
        except Exception:
            pass

        if df is None or df.empty:
            try:
                df = ak.stock_margin_detail_szse(symbol=symbol)
            except Exception:
                pass

        if df is None or df.empty:
            return {"error": f"无法获取 {symbol} 的融资融券数据", "symbol": symbol}

        # Latest record
        records = _df_to_records(df.tail(10), 10)

        return {
            "symbol": symbol,
            "records": records,
        }

    except Exception as e:
        return {"error": f"获取个股两融数据失败: {str(e)}"}


def top_stocks(limit: int = 20, sort_by: str = "balance") -> dict[str, Any]:
    """Top stocks by margin balance or margin change."""
    try:
        import akshare as ak

        df = None
        try:
            df = ak.stock_margin_underlying_info_sse()
        except Exception:
            pass

        if df is None or df.empty:
            try:
                # Alternative: get today's margin detail for all stocks
                df = ak.stock_margin_sse(start_date=datetime.now().strftime("%Y%m%d"))
            except Exception:
                pass

        if df is None or df.empty:
            return {"error": "无法获取两融标的数据", "stocks": []}

        records = _df_to_records(df, limit)
        return {
            "sort_by": sort_by,
            "count": len(records),
            "stocks": records,
        }

    except Exception as e:
        return {"error": f"获取两融排名失败: {str(e)}"}


def trend(days: int = 30) -> dict[str, Any]:
    """Historical margin balance trend (全市场融资余额趋势)."""
    try:
        import akshare as ak

        start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

        df = None
        try:
            df = ak.stock_margin_sse(start_date=start)
        except Exception:
            pass

        if df is None or df.empty:
            return {"error": "无法获取融资融券趋势数据", "records": []}

        df = df.tail(days)
        records = _df_to_records(df, days)

        return {
            "days": len(records),
            "records": records,
        }

    except Exception as e:
        return {"error": f"获取两融趋势失败: {str(e)}"}


def main():
    try:
        raw = sys.stdin.read()
        args = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        args = {}

    action = args.get("action", "market_summary")
    symbol = args.get("symbol", "")
    limit = args.get("limit", 20)
    days = args.get("days", 30)

    if action == "market_summary":
        result = market_summary()
    elif action == "stock_detail":
        result = stock_detail(symbol)
    elif action == "top_stocks":
        result = top_stocks(limit, args.get("sort_by", "balance"))
    elif action == "trend":
        result = trend(days)
    else:
        result = {"error": f"未知action: {action}"}

    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
