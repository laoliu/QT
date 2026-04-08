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

# Bypass proxy for all requests
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"
os.environ.pop("http_proxy", None)
os.environ.pop("https_proxy", None)
os.environ.pop("HTTP_PROXY", None)
os.environ.pop("HTTPS_PROXY", None)


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
        return {"error": f"δ֪action: {action}"}


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

    # Today's northbound summary
    try:
        df = ak.stock_hsgt_fund_flow_summary_em()
        if df is not None and not df.empty:
            north = df[df["资金方向"] == "北向"]
            if not north.empty:
                hk2sh_row = north[north["板块"] == "沪股通"]
                hk2sz_row = north[north["板块"] == "深股通"]

                hk2sh_net = _safe_float(hk2sh_row["资金净流入"].iloc[0]) if not hk2sh_row.empty else None
                hk2sz_net = _safe_float(hk2sz_row["资金净流入"].iloc[0]) if not hk2sz_row.empty else None
                hk2sh_buy = _safe_float(hk2sh_row["成交净买额"].iloc[0]) if not hk2sh_row.empty else None
                hk2sz_buy = _safe_float(hk2sz_row["成交净买额"].iloc[0]) if not hk2sz_row.empty else None

                total_net = 0.0
                if hk2sh_net is not None:
                    total_net += hk2sh_net
                if hk2sz_net is not None:
                    total_net += hk2sz_net

                date_str = str(north["交易日"].iloc[0])[:10] if "交易日" in north.columns else ""
                result["realtime"] = {
                    "date": date_str,
                    "hk2sh": hk2sh_net,
                    "hk2sz": hk2sz_net,
                    "hk2shBuy": hk2sh_buy,
                    "hk2szBuy": hk2sz_buy,
                    "northTotal": round(total_net, 4),
                }
    except Exception as e:
        errors.append(f"北向资金实时数据获取失败: {e}")

    # Historical daily flow (last 10 non-NaN trading days)
    try:
        df_sh = ak.stock_hsgt_hist_em(symbol="沪股通")
        df_sz = ak.stock_hsgt_hist_em(symbol="深股通")

        sh_good = df_sh[df_sh["当日成交净买额"].notna()].tail(10) if df_sh is not None else None
        sz_good = df_sz[df_sz["当日成交净买额"].notna()].tail(10) if df_sz is not None else None

        if sh_good is not None and not sh_good.empty:
            history = []
            for _, sh_row in sh_good.iterrows():
                date = str(sh_row["日期"])[:10]
                sz_match = sz_good[sz_good["日期"].astype(str).str[:10] == date] if sz_good is not None else None
                sz_net = _safe_float(sz_match["当日成交净买额"].iloc[0]) if sz_match is not None and not sz_match.empty else None
                sh_net = _safe_float(sh_row["当日成交净买额"])
                total = 0.0
                if sh_net is not None:
                    total += sh_net
                if sz_net is not None:
                    total += sz_net
                history.append({
                    "date": date,
                    "northNetIn": round(total, 4),
                    "hk2sh": sh_net,
                    "hk2sz": sz_net,
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
