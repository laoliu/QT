"""
ipo_calendar — A-share IPO tracking and new listing calendar.

Actions:
  upcoming    — Upcoming IPO subscription dates (新股申购日历)
  recent      — Recently listed new stocks (近期上市新股)
  detail      — IPO detail for a specific stock
  lottery     — IPO lottery results (中签率查询)

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


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
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


def upcoming(limit: int = 30) -> dict[str, Any]:
    """Upcoming IPO subscription calendar."""
    try:
        import akshare as ak

        # Try multiple AKShare APIs for IPO data
        df = None
        source = ""

        try:
            df = ak.stock_xgsglb_em(symbol="全部股票")
            source = "stock_xgsglb_em"
        except Exception:
            pass

        if df is None or df.empty:
            try:
                df = ak.stock_register_ipo()
                source = "stock_register_ipo"
            except Exception:
                pass

        if df is None or df.empty:
            return {"error": "无法获取新股申购数据", "upcoming": []}

        records = _df_to_records(df, limit)
        return {
            "source": source,
            "count": len(records),
            "upcoming": records,
        }

    except Exception as e:
        return {"error": f"获取新股申购日历失败: {str(e)}"}


def recent(days: int = 30, limit: int = 30) -> dict[str, Any]:
    """Recently listed new stocks."""
    try:
        import akshare as ak

        df = None
        try:
            df = ak.stock_zh_a_new_em()
        except Exception:
            pass

        if df is None or df.empty:
            try:
                df = ak.stock_new_gh_cninfo()
            except Exception:
                pass

        if df is None or df.empty:
            return {"error": "无法获取近期新股数据", "stocks": []}

        records = _df_to_records(df, limit)
        return {
            "count": len(records),
            "stocks": records,
        }

    except Exception as e:
        return {"error": f"获取近期新股失败: {str(e)}"}


def detail(symbol: str) -> dict[str, Any]:
    """IPO detail for a specific stock."""
    try:
        import akshare as ak

        # Try to get IPO detail
        info: dict[str, Any] = {"symbol": symbol}

        try:
            df = ak.stock_xgsglb_em(symbol="全部股票")
            if df is not None and not df.empty:
                code_col = None
                for col in df.columns:
                    if "代码" in col or "code" in col.lower():
                        code_col = col
                        break
                if code_col:
                    match = df[df[code_col].astype(str).str.contains(symbol)]
                    if not match.empty:
                        row = match.iloc[0]
                        for col in df.columns:
                            val = row[col]
                            if hasattr(val, "isoformat"):
                                info[col] = str(val)[:10]
                            elif isinstance(val, float) and val != val:
                                info[col] = None
                            else:
                                info[col] = val
        except Exception:
            pass

        if len(info) <= 1:
            return {"error": f"未找到 {symbol} 的IPO信息", "symbol": symbol}

        return info

    except Exception as e:
        return {"error": f"获取IPO详情失败: {str(e)}"}


def lottery(limit: int = 20) -> dict[str, Any]:
    """IPO lottery results (中签率)."""
    try:
        import akshare as ak

        df = None
        try:
            df = ak.stock_xgsglb_em(symbol="全部股票")
        except Exception:
            pass

        if df is None or df.empty:
            return {"error": "无法获取中签率数据", "results": []}

        # Filter for stocks with lottery rate data
        rate_col = None
        for col in df.columns:
            if "中签" in col:
                rate_col = col
                break

        records = _df_to_records(df, limit)
        return {
            "count": len(records),
            "results": records,
        }

    except Exception as e:
        return {"error": f"获取中签率失败: {str(e)}"}


def main():
    try:
        raw = sys.stdin.read()
        args = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        args = {}

    action = args.get("action", "upcoming")
    symbol = args.get("symbol", "")
    limit = args.get("limit", 30)
    days = args.get("days", 30)

    if action == "upcoming":
        result = upcoming(limit)
    elif action == "recent":
        result = recent(days, limit)
    elif action == "detail":
        result = detail(symbol)
    elif action == "lottery":
        result = lottery(limit)
    else:
        result = {"error": f"未知action: {action}"}

    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
