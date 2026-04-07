"""
margin_trading — 两融数据 (Margin Trading Data).

Fetches margin financing balance (融资余额), margin lending balance (融券余额),
and advance/decline market breadth data from A-share markets.

Called from TypeScript margin_trading tool via python-bridge.
Input:
  { "action": "summary" | "detail" | "history" | "breadth",
    "symbol": "600519", "limit": 20, "date": "20260401" }

Output varies by action:
  summary  → { date, rzye (融资余额), rqye (融券余额), rzrqye (两融余额), change, changePct }
  detail   → { stocks: [{ code, name, rzye, rqye, rzNet, rqNet }] }
  history  → { symbol, name, history: [{ date, rzye, rqye, rzNet }] }
  breadth  → { date, total, advancers, decliners, flat, limitUp, limitDown, advDecRatio }
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

import urllib.request
urllib.request.getproxies = lambda: {}  # type: ignore[assignment]
try:
    import requests, requests.utils  # noqa: E401
    requests.utils.getproxies = lambda: {}  # type: ignore[assignment]
    requests.utils.get_environ_proxies = lambda *a, **kw: {}  # type: ignore[assignment]
except ImportError:
    pass


def run_margin_trading(config: dict) -> dict:
    """Dispatch margin trading data queries."""
    action = config.get("action", "summary")
    limit = config.get("limit", 20)
    errors: list[str] = []

    if action == "summary":
        return _margin_summary(errors)
    elif action == "detail":
        return _margin_detail(limit, errors)
    elif action == "history":
        symbol = config.get("symbol", "")
        if not symbol:
            return {"error": "请提供股票代码 (symbol)"}
        return _margin_history(symbol, limit, errors)
    elif action == "breadth":
        date_str = config.get("date", "")
        return _market_breadth(date_str, errors)
    else:
        return {"error": f"未知action: {action}"}


def _margin_summary(errors: list[str]) -> dict:
    """Get the latest margin trading summary (融资融券余额汇总)."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    try:
        # 沪深两市融资融券汇总
        df = ak.stock_margin_sse()
        if df is not None and not df.empty:
            latest = df.iloc[-1]
            cols = df.columns.tolist()
            # akshare column names may vary; try to map them
            date_val = str(latest.iloc[0]) if len(cols) > 0 else ""
            rzye = float(latest.iloc[1]) if len(cols) > 1 else 0
            rqye = float(latest.iloc[2]) if len(cols) > 2 else 0
            rzrqye = float(latest.iloc[3]) if len(cols) > 3 else rzye + rqye

            # Get previous day for change calculation
            prev_rzrqye = float(df.iloc[-2].iloc[3]) if len(df) >= 2 and len(cols) > 3 else 0
            change = rzrqye - prev_rzrqye
            change_pct = (change / prev_rzrqye * 100) if prev_rzrqye else 0

            return {
                "action": "summary",
                "date": date_val,
                "rzye": round(rzye / 1e8, 2),  # 亿元
                "rqye": round(rqye / 1e8, 2),
                "rzrqye": round(rzrqye / 1e8, 2),
                "change": round(change / 1e8, 2),
                "changePct": round(change_pct, 2),
                "unit": "亿元",
                "errors": errors,
            }
    except Exception as e:
        errors.append(f"akshare融资融券汇总: {e}")

    # Fallback: try eastmoney
    try:
        return _margin_summary_eastmoney(errors)
    except Exception as e:
        errors.append(f"eastmoney fallback: {e}")

    return {"action": "summary", "error": "无法获取两融数据", "errors": errors}


def _margin_summary_eastmoney(errors: list[str]) -> dict:
    """Fallback: fetch margin summary from Eastmoney datacenter."""
    url = (
        "https://datacenter-web.eastmoney.com/api/data/v1/get?"
        "reportName=RPTA_RZRQ_LSHJ&columns=ALL&pageSize=5&pageNumber=1&sortColumns=DIM_DATE&sortTypes=-1"
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": "https://data.eastmoney.com/",
    }
    resp = requests.get(url, headers=headers, timeout=15)
    data = resp.json()
    if data.get("success") and data.get("result", {}).get("data"):
        rows = data["result"]["data"]
        latest = rows[0]
        prev = rows[1] if len(rows) > 1 else {}
        rzye = float(latest.get("RZYE", 0))
        rqye = float(latest.get("RQYE", 0))
        rzrqye = rzye + rqye
        prev_rzrqye = float(prev.get("RZYE", 0)) + float(prev.get("RQYE", 0)) if prev else 0
        change = rzrqye - prev_rzrqye
        change_pct = (change / prev_rzrqye * 100) if prev_rzrqye else 0

        return {
            "action": "summary",
            "date": str(latest.get("DIM_DATE", ""))[:10],
            "rzye": round(rzye / 1e8, 2),
            "rqye": round(rqye / 1e8, 2),
            "rzrqye": round(rzrqye / 1e8, 2),
            "change": round(change / 1e8, 2),
            "changePct": round(change_pct, 2),
            "unit": "亿元",
            "errors": errors,
        }
    return {"error": "eastmoney返回数据为空", "errors": errors}


def _margin_detail(limit: int, errors: list[str]) -> dict:
    """Get per-stock margin trading details (个股融资融券明细)."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    try:
        # 沪深两市融资融券明细
        df = ak.stock_margin_detail_sse()
        if df is not None and not df.empty:
            # Normalize column names
            df = df.head(limit)
            stocks = []
            for _, row in df.iterrows():
                vals = row.tolist()
                stocks.append({
                    "code": str(vals[0]) if len(vals) > 0 else "",
                    "name": str(vals[1]) if len(vals) > 1 else "",
                    "rzye": round(float(vals[2]) / 1e8, 2) if len(vals) > 2 and vals[2] else 0,
                    "rqye": round(float(vals[3]) / 1e8, 2) if len(vals) > 3 and vals[3] else 0,
                    "rzNet": round(float(vals[4]) / 1e8, 2) if len(vals) > 4 and vals[4] else 0,
                    "rqNet": round(float(vals[5]) / 1e8, 2) if len(vals) > 5 and vals[5] else 0,
                })
            return {
                "action": "detail",
                "count": len(stocks),
                "stocks": stocks,
                "unit": "亿元",
                "errors": errors,
            }
    except Exception as e:
        errors.append(f"akshare个股融资融券: {e}")

    return {"action": "detail", "error": "无法获取个股两融数据", "errors": errors}


def _margin_history(symbol: str, limit: int, errors: list[str]) -> dict:
    """Get margin trading history for a specific stock."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    # Normalize symbol — strip market prefix
    code = symbol.replace("SH", "").replace("SZ", "").replace("sh", "").replace("sz", "")
    code = code.replace(".", "").strip()

    try:
        df = ak.stock_margin_detail_sse(code)
        if df is not None and not df.empty:
            df = df.tail(limit)
            history = []
            for _, row in df.iterrows():
                vals = row.tolist()
                history.append({
                    "date": str(vals[0]) if len(vals) > 0 else "",
                    "rzye": round(float(vals[1]) / 1e8, 2) if len(vals) > 1 and vals[1] else 0,
                    "rqye": round(float(vals[2]) / 1e8, 2) if len(vals) > 2 and vals[2] else 0,
                    "rzNet": round(float(vals[3]) / 1e8, 2) if len(vals) > 3 and vals[3] else 0,
                })
            return {
                "action": "history",
                "symbol": code,
                "count": len(history),
                "history": history,
                "unit": "亿元",
                "errors": errors,
            }
    except Exception as e:
        errors.append(f"akshare个股融资融券历史: {e}")

    return {"action": "history", "symbol": code, "error": "无法获取个股两融历史", "errors": errors}


def _market_breadth(date_str: str, errors: list[str]) -> dict:
    """Get market breadth: advance/decline ratio, limit-up/down counts."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    if not date_str:
        date_str = datetime.now().strftime("%Y%m%d")

    result: dict[str, Any] = {"action": "breadth", "date": date_str}

    # Get all A-share stock data for the day
    try:
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            total = len(df)
            # Column for change percent — typically '涨跌幅'
            change_col = None
            for col in df.columns:
                if '涨跌幅' in str(col):
                    change_col = col
                    break

            if change_col:
                changes = df[change_col].dropna().astype(float)
                advancers = int((changes > 0).sum())
                decliners = int((changes < 0).sum())
                flat = int((changes == 0).sum())

                # Limit-up/down detection (10% or 20% for 创业板/科创板)
                name_col = None
                code_col = None
                for col in df.columns:
                    if '名称' in str(col):
                        name_col = col
                    if '代码' in str(col):
                        code_col = col

                limit_up = 0
                limit_down = 0
                limit_up_stocks: list[dict] = []
                limit_down_stocks: list[dict] = []

                for idx, row in df.iterrows():
                    chg = float(row[change_col]) if row[change_col] else 0
                    code = str(row[code_col]) if code_col else ""
                    name = str(row[name_col]) if name_col else ""

                    # 创业板(30x)/科创板(688x) use 20% limit
                    is_20pct = code.startswith("30") or code.startswith("688")
                    threshold = 19.5 if is_20pct else 9.5

                    if chg >= threshold:
                        limit_up += 1
                        if len(limit_up_stocks) < 10:
                            limit_up_stocks.append({"code": code, "name": name, "changePct": round(chg, 2)})
                    elif chg <= -threshold:
                        limit_down += 1
                        if len(limit_down_stocks) < 10:
                            limit_down_stocks.append({"code": code, "name": name, "changePct": round(chg, 2)})

                ad_ratio = round(advancers / decliners, 2) if decliners > 0 else float('inf')

                result.update({
                    "total": total,
                    "advancers": advancers,
                    "decliners": decliners,
                    "flat": flat,
                    "limitUp": limit_up,
                    "limitDown": limit_down,
                    "advDecRatio": ad_ratio,
                    "limitUpStocks": limit_up_stocks,
                    "limitDownStocks": limit_down_stocks,
                })
            else:
                errors.append("找不到涨跌幅列")
    except Exception as e:
        errors.append(f"市场宽度数据: {e}")

    result["errors"] = errors
    return result


# ── CLI entry point ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    raw = sys.stdin.read().strip()
    try:
        cfg = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        cfg = {}

    result = run_margin_trading(cfg)
    print(json.dumps(result, ensure_ascii=False, default=str))
