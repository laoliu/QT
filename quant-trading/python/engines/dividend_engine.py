"""
Dividend data engine — fetch dividend/ex-dividend info from AKShare.

Input (JSON via stdin):
  {
    "action": "query",
    "symbols": ["600519", "000858", "300750"]
  }

Output: JSON with dividend records per symbol, including:
  - 分红方案 (dividend plan)
  - 股权登记日 (record date)
  - 除权除息日 (ex-dividend date)
  - 进度 (status)
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


def _fetch_dividend_data(symbols: list[str]) -> dict[str, Any]:
    """Fetch dividend data for the given A-share symbols using AKShare."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare is not installed. Run: pip install akshare"}

    results: dict[str, list[dict[str, Any]]] = {}
    today = datetime.now()
    # Look at dividends within ±365 days window to catch upcoming ones
    cutoff_past = today - timedelta(days=90)
    cutoff_future = today + timedelta(days=365)

    for symbol in symbols:
        sym = symbol.strip()
        if not sym:
            continue
        try:
            records = _fetch_single(ak, sym, cutoff_past, cutoff_future)
            results[sym] = records
        except Exception as e:
            results[sym] = [{"error": str(e)}]

    return {"ok": True, "data": results}


def _fetch_single(
    ak: Any, symbol: str, cutoff_past: datetime, cutoff_future: datetime
) -> list[dict[str, Any]]:
    """Fetch dividend info for a single symbol."""
    records: list[dict[str, Any]] = []

    # Try stock_dividend_cninfo (from cninfo, most comprehensive for A-shares)
    try:
        df = ak.stock_dividend_cninfo(symbol=symbol)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                rec = _parse_cninfo_row(row, cutoff_past, cutoff_future)
                if rec:
                    records.append(rec)
            if records:
                return records
    except Exception:
        pass

    # Fallback: stock_fhps_em (东方财富 分红配送)
    try:
        df = ak.stock_fhps_em(symbol=symbol)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                rec = _parse_fhps_row(row, cutoff_past, cutoff_future)
                if rec:
                    records.append(rec)
    except Exception:
        pass

    return records


def _parse_date(val: Any) -> datetime | None:
    """Parse date from various formats."""
    if val is None or (isinstance(val, float) and str(val) == "nan"):
        return None
    s = str(val).strip()
    if not s or s == "-" or s == "nan" or s == "NaT":
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:10], fmt[:len(s[:10]) + s[:10].count("-")])
        except (ValueError, IndexError):
            continue
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d")
    except (ValueError, IndexError):
        return None


def _parse_cninfo_row(
    row: Any, cutoff_past: datetime, cutoff_future: datetime
) -> dict[str, Any] | None:
    """Parse a row from stock_dividend_cninfo."""
    rec: dict[str, Any] = {}

    # Extract plan description
    plan_parts = []
    for col in row.index:
        col_str = str(col)
        if "方案" in col_str or "分红" in col_str or "派" in col_str:
            val = row[col]
            if val is not None and str(val).strip() and str(val) != "nan":
                plan_parts.append(f"{col_str}: {val}")

    # Extract key dates
    record_date = None
    ex_date = None
    for col in row.index:
        col_str = str(col)
        if "登记" in col_str:
            record_date = _parse_date(row[col])
            rec["recordDate"] = str(row[col]).strip() if record_date else None
        if "除权" in col_str or "除息" in col_str:
            ex_date = _parse_date(row[col])
            rec["exDate"] = str(row[col]).strip() if ex_date else None

    # Filter by date relevance
    relevant_date = ex_date or record_date
    if relevant_date:
        if relevant_date < cutoff_past or relevant_date > cutoff_future:
            return None

    # Extract status/progress
    for col in row.index:
        col_str = str(col)
        if "进度" in col_str or "状态" in col_str:
            val = row[col]
            if val is not None and str(val).strip() and str(val) != "nan":
                rec["status"] = str(val).strip()

    # Build plan text
    if not plan_parts:
        # Try to build from common columns
        for col in row.index:
            col_str = str(col)
            if any(k in col_str for k in ("送", "转", "派", "红")):
                val = row[col]
                if val is not None and str(val).strip() and str(val) != "nan":
                    try:
                        fval = float(val)
                        if fval > 0:
                            plan_parts.append(f"{col_str}{val}")
                    except (ValueError, TypeError):
                        plan_parts.append(f"{col_str}: {val}")

    rec["plan"] = " | ".join(plan_parts) if plan_parts else "详见公告"

    # Only return if we have at least a date or plan
    if rec.get("exDate") or rec.get("recordDate") or plan_parts:
        return rec
    return None


def _parse_fhps_row(
    row: Any, cutoff_past: datetime, cutoff_future: datetime
) -> dict[str, Any] | None:
    """Parse a row from stock_fhps_em (东方财富)."""
    rec: dict[str, Any] = {}

    plan_parts = []
    for col in row.index:
        col_str = str(col)
        if any(k in col_str for k in ("送", "转", "派", "分红")):
            val = row[col]
            if val is not None and str(val).strip() and str(val) != "nan":
                try:
                    fval = float(val)
                    if fval > 0:
                        plan_parts.append(f"{col_str}{val}")
                except (ValueError, TypeError):
                    plan_parts.append(f"{col_str}: {val}")

    record_date = None
    ex_date = None
    for col in row.index:
        col_str = str(col)
        if "登记" in col_str:
            record_date = _parse_date(row[col])
            rec["recordDate"] = str(row[col]).strip() if record_date else None
        if "除权" in col_str or "除息" in col_str:
            ex_date = _parse_date(row[col])
            rec["exDate"] = str(row[col]).strip() if ex_date else None

    relevant_date = ex_date or record_date
    if relevant_date:
        if relevant_date < cutoff_past or relevant_date > cutoff_future:
            return None

    for col in row.index:
        col_str = str(col)
        if "进度" in col_str or "状态" in col_str:
            val = row[col]
            if val is not None and str(val).strip() and str(val) != "nan":
                rec["status"] = str(val).strip()

    rec["plan"] = " | ".join(plan_parts) if plan_parts else "详见公告"

    if rec.get("exDate") or rec.get("recordDate") or plan_parts:
        return rec
    return None


def main() -> None:
    raw = sys.stdin.read().strip()
    if not raw:
        json.dump({"ok": False, "error": "empty input"}, sys.stdout)
        return

    try:
        args = json.loads(raw)
    except json.JSONDecodeError as e:
        json.dump({"ok": False, "error": f"invalid JSON: {e}"}, sys.stdout)
        return

    action = args.get("action", "query")
    symbols = args.get("symbols", [])

    if action == "query":
        if not symbols:
            json.dump({"ok": False, "error": "no symbols provided"}, sys.stdout)
            return
        result = _fetch_dividend_data(symbols)
        json.dump(result, sys.stdout, ensure_ascii=False, default=str)
    else:
        json.dump({"ok": False, "error": f"unknown action: {action}"}, sys.stdout)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        json.dump(
            {"ok": False, "error": traceback.format_exc()},
            sys.stdout,
            ensure_ascii=False,
        )
