"""
corporate_actions — Dividend history, rights issues, stock splits, allotments, name changes.

Fetches A-share corporate action data from akshare/baostock.

Input (JSON via stdin / QUANT_CONFIG):
  {
    "action": "dividends" | "rights_issue" | "stock_split" | "allotment" | "name_changes" | "summary",
    "symbol": "600519",
    "year": "2025",
    "upcoming_only": true
  }

Output: JSON with corporate action data.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from typing import Any

# ── Proxy bypass ──────────────────────────────────────────────────────────────
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def _safe_val(val: Any) -> Any:
    """Convert pandas/numpy values to JSON-safe types."""
    if hasattr(val, 'strftime'):
        return val.strftime("%Y-%m-%d")
    if hasattr(val, 'item'):
        return val.item()
    if isinstance(val, float) and (val != val):  # NaN check
        return None
    return val


def _df_to_list(df, max_rows: int = 30) -> list[dict]:
    """Convert a DataFrame to a list of dicts with safe values."""
    result = []
    for _, row in df.head(max_rows).iterrows():
        item = {}
        for col in df.columns:
            item[col] = _safe_val(row[col])
        result.append(item)
    return result


def run_corporate_actions(config: dict) -> dict:
    """Dispatch corporate action queries."""
    action = config.get("action", "dividends")

    if action == "dividends":
        return _dividends(config)
    elif action == "rights_issue":
        return _rights_issue(config)
    elif action == "stock_split":
        return _stock_split(config)
    elif action == "allotment":
        return _allotment(config)
    elif action == "name_changes":
        return _name_changes(config)
    elif action == "summary":
        return _summary(config)
    else:
        return {"error": f"Unknown action: {action}"}


def _dividends(config: dict) -> dict:
    """Fetch dividend history for a stock."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    symbol = config.get("symbol", "")
    if not symbol:
        return {"error": "symbol is required"}

    try:
        df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
        if df is None or df.empty:
            return {"dividends": [], "note": f"{symbol} 暂无分红记录"}

        items = _df_to_list(df, max_rows=30)

        # Compute simple dividend yield if we have price data
        total_div = 0
        for item in items:
            for k, v in item.items():
                if "每股" in str(k) and "派" in str(k) and v is not None:
                    try:
                        total_div += float(v)
                    except (ValueError, TypeError):
                        pass

        return {
            "symbol": symbol,
            "dividends": items,
            "total_records": len(items),
        }

    except Exception as e:
        return {"error": f"dividends failed: {str(e)}"}


def _rights_issue(config: dict) -> dict:
    """Fetch rights issue events."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    try:
        symbol = config.get("symbol")
        if symbol:
            df = ak.stock_history_dividend_detail(symbol=symbol, indicator="配股")
            if df is None or df.empty:
                return {"rights_issues": [], "note": f"{symbol} 暂无配股记录"}
            return {"symbol": symbol, "rights_issues": _df_to_list(df)}
        else:
            # Market-wide recent rights issues
            df = ak.stock_pg_em()
            if df is None or df.empty:
                return {"rights_issues": [], "note": "暂无近期配股数据"}
            return {"rights_issues": _df_to_list(df, max_rows=20)}

    except Exception as e:
        return {"error": f"rights_issue failed: {str(e)}"}


def _stock_split(config: dict) -> dict:
    """Fetch stock split history (送股/转增)."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    symbol = config.get("symbol", "")
    if not symbol:
        return {"error": "symbol is required"}

    try:
        df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
        if df is None or df.empty:
            return {"splits": [], "note": f"{symbol} 暂无送转记录"}

        splits = []
        for _, row in df.iterrows():
            item = {}
            has_split = False
            for col in df.columns:
                val = _safe_val(row[col])
                item[col] = val
                # Check for 送股 or 转增
                if ("送" in str(col) or "转" in str(col)) and val is not None:
                    try:
                        if float(val) > 0:
                            has_split = True
                    except (ValueError, TypeError):
                        pass
            if has_split:
                splits.append(item)

        return {"symbol": symbol, "splits": splits[:20]}

    except Exception as e:
        return {"error": f"stock_split failed: {str(e)}"}


def _allotment(config: dict) -> dict:
    """Fetch share allotment (增发) data."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    try:
        df = ak.stock_zf_em()
        if df is None or df.empty:
            return {"allotments": [], "note": "暂无近期增发数据"}

        symbol = config.get("symbol")
        if symbol:
            # Filter for specific symbol
            for col in df.columns:
                if "代码" in str(col):
                    mask = df[col].astype(str).str.contains(symbol)
                    df = df[mask]
                    break

        return {"allotments": _df_to_list(df, max_rows=20)}

    except Exception as e:
        return {"error": f"allotment failed: {str(e)}"}


def _name_changes(config: dict) -> dict:
    """Fetch stock name change history."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    symbol = config.get("symbol", "")
    if not symbol:
        return {"error": "symbol is required"}

    try:
        # Try baostock for name change history
        try:
            import baostock as bs
            lg = bs.login()
            rs = bs.query_stock_basic(code=f"sh.{symbol}" if symbol[0] == '6' else f"sz.{symbol}")
            data = []
            while rs.error_code == '0' and rs.next():
                data.append(rs.get_row_data())
            bs.logout()
            if data:
                return {
                    "symbol": symbol,
                    "name_changes": [{"info": row} for row in data],
                }
        except ImportError:
            pass

        # Fallback: return basic info from akshare
        df = ak.stock_info_a_code_name()
        if df is not None and not df.empty:
            for col in df.columns:
                if "代码" in str(col):
                    match = df[df[col].astype(str) == symbol]
                    if not match.empty:
                        return {
                            "symbol": symbol,
                            "current_name": _safe_val(match.iloc[0].get("名称", match.iloc[0].iloc[1])),
                            "name_changes": [],
                            "note": "历史更名记录需要baostock支持",
                        }

        return {"symbol": symbol, "name_changes": [], "note": "未找到该股票信息"}

    except Exception as e:
        return {"error": f"name_changes failed: {str(e)}"}


def _summary(config: dict) -> dict:
    """Get comprehensive corporate action summary for a symbol."""
    symbol = config.get("symbol", "")
    if not symbol:
        return {"error": "symbol is required"}

    result: dict[str, Any] = {"symbol": symbol}

    # Collect all actions, continuing even if some fail
    div_result = _dividends(config)
    if "dividends" in div_result:
        result["recent_dividends"] = div_result["dividends"][:5]

    split_result = _stock_split(config)
    if "splits" in split_result:
        result["recent_splits"] = split_result["splits"][:3]

    rights_result = _rights_issue(config)
    if "rights_issues" in rights_result:
        result["recent_rights_issues"] = rights_result["rights_issues"][:3]

    return result


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
        result = run_corporate_actions(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
