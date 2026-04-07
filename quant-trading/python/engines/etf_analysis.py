"""
etf_analysis — A-share ETF analysis tool.

Actions:
  list          — List popular ETFs with key metrics
  compare       — Compare multiple ETFs side-by-side
  premium       — ETF premium/discount rate (折溢价率)
  flow          — ETF fund flow (份额变化 proxy for 申购/赎回)
  detail        — Detailed ETF analysis

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


def list_etfs(limit: int = 30, category: str = "all") -> dict[str, Any]:
    """List popular ETFs with key metrics."""
    try:
        import akshare as ak

        df = None
        try:
            df = ak.fund_etf_spot_em()
        except Exception:
            pass

        if df is None or df.empty:
            try:
                df = ak.fund_etf_category_sina(symbol="ETF基金")
            except Exception:
                pass

        if df is None or df.empty:
            return {"error": "无法获取ETF数据", "etfs": []}

        records = _df_to_records(df, limit)
        return {
            "total": len(df),
            "count": len(records),
            "etfs": records,
        }

    except Exception as e:
        return {"error": f"获取ETF列表失败: {str(e)}"}


def compare(symbols: list[str], days: int = 60) -> dict[str, Any]:
    """Compare multiple ETFs side-by-side."""
    try:
        import akshare as ak

        results: dict[str, Any] = {}
        start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")

        for symbol in symbols:
            try:
                df = ak.fund_etf_hist_em(symbol=symbol, period="daily",
                                          start_date=start, end_date=end, adjust="qfq")
                if df is None or df.empty:
                    results[symbol] = {"error": f"无法获取 {symbol} 数据"}
                    continue

                df = df.tail(days)
                closes = df["收盘"].apply(lambda x: _safe_float(x)).tolist()

                if not closes or closes[0] == 0:
                    results[symbol] = {"error": "数据异常"}
                    continue

                results[symbol] = {
                    "start_price": round(closes[0], 4),
                    "end_price": round(closes[-1], 4),
                    "return_pct": round((closes[-1] / closes[0] - 1) * 100, 2),
                    "max_price": round(max(closes), 4),
                    "min_price": round(min(closes), 4),
                    "data_points": len(closes),
                }

            except Exception as e:
                results[symbol] = {"error": str(e)}

        return {
            "period_days": days,
            "etfs": results,
        }

    except Exception as e:
        return {"error": f"ETF对比失败: {str(e)}"}


def premium(symbol: str) -> dict[str, Any]:
    """ETF premium/discount rate."""
    try:
        import akshare as ak

        # Get real-time ETF data which may include IOPV
        try:
            df = ak.fund_etf_spot_em()
            if df is not None and not df.empty:
                code_col = None
                for col in df.columns:
                    if "代码" in col:
                        code_col = col
                        break
                if code_col:
                    match = df[df[code_col].astype(str) == symbol]
                    if not match.empty:
                        row = match.iloc[0]
                        info: dict[str, Any] = {"symbol": symbol}
                        for col in df.columns:
                            val = row[col]
                            if hasattr(val, "isoformat"):
                                info[col] = str(val)[:10]
                            elif isinstance(val, float) and val != val:
                                info[col] = None
                            else:
                                info[col] = val
                        return info
        except Exception:
            pass

        return {"error": f"无法获取 {symbol} 的折溢价数据", "symbol": symbol}

    except Exception as e:
        return {"error": f"获取折溢价失败: {str(e)}"}


def flow(symbol: str, days: int = 20) -> dict[str, Any]:
    """ETF fund flow (shares outstanding changes as proxy)."""
    try:
        import akshare as ak

        start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")
        end = datetime.now().strftime("%Y%m%d")

        try:
            df = ak.fund_etf_hist_em(symbol=symbol, period="daily",
                                      start_date=start, end_date=end, adjust="")
            if df is None or df.empty:
                return {"error": f"无法获取 {symbol} 历史数据"}

            df = df.tail(days)
            records = _df_to_records(df, days)

            return {
                "symbol": symbol,
                "days": len(records),
                "records": records,
            }
        except Exception as e:
            return {"error": f"获取ETF数据失败: {str(e)}"}

    except Exception as e:
        return {"error": f"获取ETF流向失败: {str(e)}"}


def detail(symbol: str) -> dict[str, Any]:
    """Detailed ETF analysis."""
    try:
        import akshare as ak

        info: dict[str, Any] = {"symbol": symbol}

        # Get spot data
        try:
            df = ak.fund_etf_spot_em()
            if df is not None and not df.empty:
                code_col = None
                for col in df.columns:
                    if "代码" in col:
                        code_col = col
                        break
                if code_col:
                    match = df[df[code_col].astype(str) == symbol]
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
            return {"error": f"未找到ETF {symbol}", "symbol": symbol}

        return info

    except Exception as e:
        return {"error": f"获取ETF详情失败: {str(e)}"}


def main():
    try:
        raw = sys.stdin.read()
        args = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        args = {}

    action = args.get("action", "list")
    symbol = args.get("symbol", "")
    symbols_raw = args.get("symbols", "")
    if isinstance(symbols_raw, list):
        symbols = symbols_raw
    else:
        symbols = [s.strip() for s in str(symbols_raw).split(",") if s.strip()]
    limit = args.get("limit", 30)
    days = args.get("days", 20)

    if action == "list":
        result = list_etfs(limit, args.get("category", "all"))
    elif action == "compare":
        result = compare(symbols, days)
    elif action == "premium":
        result = premium(symbol)
    elif action == "flow":
        result = flow(symbol, days)
    elif action == "detail":
        result = detail(symbol)
    else:
        result = {"error": f"未知action: {action}"}

    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
