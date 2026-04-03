"""
fund_flow — Individual stock and sector fund flow analysis.

Called from TypeScript market_data tool via python-bridge.
Input:
  { "action": "stock" | "sector" | "rank", "symbol": "600519", "limit": 20 }

Output varies by action:
  stock → { symbol, flows: [{date, main_in, main_out, net, ...}] }
  sector → { sectors: [{name, main_net, ...}] }
  rank → { stocks: [{code, name, main_net, changePct, ...}] }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any

os.environ.setdefault("no_proxy", "eastmoney.com,push2his.eastmoney.com,datacenter-web.eastmoney.com")
os.environ.setdefault("NO_PROXY", "eastmoney.com,push2his.eastmoney.com,datacenter-web.eastmoney.com")


def run_fund_flow(config: dict) -> dict:
    """Dispatch fund flow analysis based on action."""
    action = config.get("action", "rank")
    limit = config.get("limit", 20)
    errors: list[str] = []

    if action == "stock":
        symbol = config.get("symbol", "")
        if not symbol:
            return {"error": "请提供股票代码 (symbol)"}
        return _stock_fund_flow(symbol, errors)
    elif action == "sector":
        return _sector_fund_flow(limit, errors)
    elif action == "rank":
        return _fund_flow_rank(limit, errors)
    elif action == "northbound":
        return _northbound_flow(errors)
    else:
        return {"error": f"未知action: {action}"}


def _stock_fund_flow(symbol: str, errors: list[str]) -> dict:
    """Get individual stock fund flow data."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    flows = []
    try:
        df = ak.stock_individual_fund_flow(stock=symbol, market="sh" if symbol.startswith("6") else "sz")
        if df is not None and not df.empty:
            for _, row in df.tail(20).iterrows():
                flow = {
                    "date": str(row.get("日期", ""))[:10],
                    "close": _safe_float(row.get("收盘价")),
                    "changePct": _safe_float(row.get("涨跌幅")),
                    "mainNetIn": _safe_float(row.get("主力净流入-净额")),
                    "mainNetInPct": _safe_float(row.get("主力净流入-净占比")),
                    "superLargeNetIn": _safe_float(row.get("超大单净流入-净额")),
                    "largeNetIn": _safe_float(row.get("大单净流入-净额")),
                    "mediumNetIn": _safe_float(row.get("中单净流入-净额")),
                    "smallNetIn": _safe_float(row.get("小单净流入-净额")),
                }
                flows.append(flow)
    except Exception as e:
        errors.append(f"个股资金流向获取失败: {e}")

    if not flows:
        return {"error": f"无法获取 {symbol} 的资金流向数据", "errors": errors}

    return {
        "action": "stock",
        "symbol": symbol,
        "count": len(flows),
        "flows": flows,
        "errors": errors if errors else None,
    }


def _sector_fund_flow(limit: int, errors: list[str]) -> dict:
    """Get sector-level fund flow ranking."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    sectors = []
    try:
        df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
        if df is not None and not df.empty:
            for _, row in df.head(limit).iterrows():
                sector = {
                    "name": str(row.get("名称", "")),
                    "changePct": _safe_float(row.get("今日涨跌幅")),
                    "mainNetIn": _safe_float(row.get("主力净流入-净额")),
                    "mainNetInPct": _safe_float(row.get("主力净流入-净占比")),
                    "superLargeNetIn": _safe_float(row.get("超大单净流入-净额")),
                    "largeNetIn": _safe_float(row.get("大单净流入-净额")),
                    "mediumNetIn": _safe_float(row.get("中单净流入-净额")),
                    "smallNetIn": _safe_float(row.get("小单净流入-净额")),
                }
                sectors.append(sector)
    except Exception as e:
        errors.append(f"行业资金流向获取失败: {e}")

    if not sectors:
        return {"error": "无法获取行业资金流向数据", "errors": errors}

    return {
        "action": "sector",
        "count": len(sectors),
        "sectors": sectors,
        "errors": errors if errors else None,
    }


def _fund_flow_rank(limit: int, errors: list[str]) -> dict:
    """Get individual stock fund flow ranking (today's top inflows)."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    stocks = []
    try:
        df = ak.stock_individual_fund_flow_rank(indicator="今日")
        if df is not None and not df.empty:
            for _, row in df.head(limit).iterrows():
                stock = {
                    "code": str(row.get("代码", "")),
                    "name": str(row.get("名称", "")),
                    "price": _safe_float(row.get("最新价")),
                    "changePct": _safe_float(row.get("今日涨跌幅")),
                    "mainNetIn": _safe_float(row.get("主力净流入-净额")),
                    "mainNetInPct": _safe_float(row.get("主力净流入-净占比")),
                    "superLargeNetIn": _safe_float(row.get("超大单净流入-净额")),
                    "largeNetIn": _safe_float(row.get("大单净流入-净额")),
                }
                stocks.append(stock)
    except Exception as e:
        errors.append(f"个股资金流排名获取失败: {e}")

    if not stocks:
        return {"error": "无法获取个股资金流排名数据", "errors": errors}

    return {
        "action": "rank",
        "count": len(stocks),
        "stocks": stocks,
        "errors": errors if errors else None,
    }


def _northbound_flow(errors: list[str]) -> dict:
    """Get northbound (沪深港通) fund flow data."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    result: dict[str, Any] = {"action": "northbound"}

    # Today's real-time northbound flow
    try:
        df = ak.stock_hsgt_fund_min_em(symbol="北向资金")
        if df is not None and not df.empty:
            last = df.iloc[-1]
            result["realtime"] = {
                "date": str(last.get("日期", ""))[:10],
                "time": str(last.get("时间", "")),
                "hk2sh": _safe_float(last.get("沪股通")),
                "hk2sz": _safe_float(last.get("深股通")),
                "northTotal": _safe_float(last.get("北向资金")),
            }
    except Exception as e:
        errors.append(f"北向资金实时数据获取失败: {e}")

    # Historical daily summary
    try:
        df2 = ak.stock_hsgt_fund_flow_summary_em()
        if df2 is not None and not df2.empty:
            history = []
            for _, row in df2.tail(5).iterrows():
                history.append({
                    "date": str(row.get("日期", ""))[:10],
                    "northNetIn": _safe_float(row.get("北向资金")),
                    "hk2sh": _safe_float(row.get("沪股通")),
                    "hk2sz": _safe_float(row.get("深股通")),
                })
            result["history"] = history
            result["historyCount"] = len(history)
    except Exception as e:
        errors.append(f"北向资金历史数据获取失败: {e}")

    if errors:
        result["errors"] = errors

    return result


def _safe_float(val: Any) -> float | None:
    """Convert value to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN check
            return None
        return round(f, 4)
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

        result = run_fund_flow(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
