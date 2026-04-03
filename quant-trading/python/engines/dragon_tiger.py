"""
dragon_tiger — Dragon Tiger List (龙虎榜) data.

Fetches institutional trading activity, block trades, and stock exchange
disclosed top buyer/seller data.

Called from TypeScript dragon_tiger tool via python-bridge.
Input:
  { "action": "daily" | "stock" | "institution", "date": "2026-03-03", "symbol": "600519", "limit": 20 }

Output varies by action:
  daily → { date, stocks: [{code, name, changePct, reason, buyAmount, sellAmount, netAmount}] }
  stock → { symbol, records: [{date, reason, buyAmount, sellAmount, netAmount, departments}] }
  institution → { institutions: [{name, buyCount, sellCount, netAmount}] }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

os.environ.setdefault("no_proxy", "eastmoney.com,push2his.eastmoney.com,datacenter-web.eastmoney.com")
os.environ.setdefault("NO_PROXY", "eastmoney.com,push2his.eastmoney.com,datacenter-web.eastmoney.com")


def run_dragon_tiger(config: dict) -> dict:
    """Dispatch dragon tiger list analysis."""
    action = config.get("action", "daily")
    limit = config.get("limit", 20)
    errors: list[str] = []

    if action == "daily":
        date = config.get("date", "")
        return _daily_list(date, limit, errors)
    elif action == "stock":
        symbol = config.get("symbol", "")
        if not symbol:
            return {"error": "请提供股票代码 (symbol)"}
        return _stock_detail(symbol, limit, errors)
    elif action == "institution":
        return _institution_stats(limit, errors)
    else:
        return {"error": f"未知action: {action}"}


def _daily_list(date_str: str, limit: int, errors: list[str]) -> dict:
    """Get daily dragon tiger list."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    # Default to most recent trading day
    if not date_str:
        # Try today, then yesterday, then 2 days ago
        for delta in range(0, 5):
            d = datetime.now() - timedelta(days=delta)
            if d.weekday() < 5:  # weekday
                date_str = d.strftime("%Y%m%d")
                break

    stocks = []
    try:
        df = ak.stock_lhb_detail_em(
            start_date=date_str,
            end_date=date_str,
        )
        if df is not None and not df.empty:
            for _, row in df.head(limit).iterrows():
                stock = {
                    "code": str(row.get("代码", "")),
                    "name": str(row.get("名称", "")),
                    "close": _safe_float(row.get("收盘价")),
                    "changePct": _safe_float(row.get("涨跌幅")),
                    "reason": str(row.get("上榜原因", "")),
                    "buyAmount": _safe_float(row.get("买入额")),
                    "sellAmount": _safe_float(row.get("卖出额")),
                    "netAmount": _safe_float(row.get("净买额")),
                    "turnoverRate": _safe_float(row.get("换手率")),
                    "totalAmount": _safe_float(row.get("成交额")),
                    "circMarketCap": _safe_float(row.get("流通市值")),
                }
                stocks.append(stock)
    except Exception as e:
        errors.append(f"龙虎榜数据获取失败: {e}")

    if not stocks:
        # Try alternate API
        try:
            df = ak.stock_lhb_jgmmtj_em(start_date=date_str, end_date=date_str)
            if df is not None and not df.empty:
                for _, row in df.head(limit).iterrows():
                    stock = {
                        "code": str(row.get("代码", "")),
                        "name": str(row.get("名称", "")),
                        "changePct": _safe_float(row.get("涨跌幅")),
                        "buyAmount": _safe_float(row.get("机构买入额")),
                        "sellAmount": _safe_float(row.get("机构卖出额")),
                        "netAmount": _safe_float(row.get("机构净买额")),
                        "type": "institutional",
                    }
                    stocks.append(stock)
        except Exception as e2:
            errors.append(f"机构龙虎榜获取失败: {e2}")

    if not stocks:
        return {"error": f"日期 {date_str} 无龙虎榜数据（可能为非交易日，或当日数据尚未发布，通常在收盘后 17:00-18:00 更新）", "errors": errors}

    return {
        "action": "daily",
        "date": date_str,
        "count": len(stocks),
        "stocks": stocks,
        "errors": errors if errors else None,
    }


def _stock_detail(symbol: str, limit: int, errors: list[str]) -> dict:
    """Get dragon tiger history for a specific stock."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    records = []
    try:
        df = ak.stock_lhb_stock_statistic_em(symbol="近一月")
        if df is not None and not df.empty:
            # Filter for the target stock
            mask = df["代码"].astype(str) == symbol
            matched = df[mask]
            if matched.empty:
                # No exact match, show top stocks from list
                errors.append(f"近一月龙虎榜中未找到 {symbol}")
            else:
                for _, row in matched.head(limit).iterrows():
                    record = {
                        "code": str(row.get("代码", "")),
                        "name": str(row.get("名称", "")),
                        "appearances": _safe_int(row.get("上榜次数", 0)),
                        "totalBuy": _safe_float(row.get("龙虎榜买入额")),
                        "totalSell": _safe_float(row.get("龙虎榜卖出额")),
                        "totalNet": _safe_float(row.get("龙虎榜净买额")),
                        "changePct": _safe_float(row.get("涨跌幅")),
                    }
                    records.append(record)
    except Exception as e:
        errors.append(f"个股龙虎榜统计获取失败: {e}")

    if not records:
        return {
            "error": f"未找到 {symbol} 的龙虎榜记录",
            "errors": errors,
        }

    return {
        "action": "stock",
        "symbol": symbol,
        "count": len(records),
        "records": records,
        "errors": errors if errors else None,
    }


def _institution_stats(limit: int, errors: list[str]) -> dict:
    """Get institutional trading statistics from dragon tiger list."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    institutions = []
    try:
        df = ak.stock_lhb_stock_statistic_em(symbol="近一月")
        if df is not None and not df.empty:
            for _, row in df.head(limit).iterrows():
                inst = {
                    "code": str(row.get("代码", "")),
                    "name": str(row.get("名称", "")),
                    "appearances": _safe_int(row.get("上榜次数", 0)),
                    "totalBuy": _safe_float(row.get("龙虎榜买入额")),
                    "totalSell": _safe_float(row.get("龙虎榜卖出额")),
                    "totalNet": _safe_float(row.get("龙虎榜净买额")),
                }
                institutions.append(inst)
    except Exception as e:
        errors.append(f"龙虎榜统计获取失败: {e}")

    if not institutions:
        return {"error": "无法获取龙虎榜统计数据", "errors": errors}

    return {
        "action": "institution",
        "count": len(institutions),
        "stocks": institutions,
        "errors": errors if errors else None,
    }


def _safe_float(val: Any) -> float | None:
    """Convert value to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN
            return None
        return round(f, 4)
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> int:
    """Convert value to int, returning 0 on failure."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


# ── CLI entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    try:
        if "--json" in sys.argv:
            raw = sys.stdin.read()
            cfg = json.loads(raw) if raw.strip() else {}
        else:
            cfg = json.loads(os.environ.get("QUANT_CONFIG", "{}"))

        result = run_dragon_tiger(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
