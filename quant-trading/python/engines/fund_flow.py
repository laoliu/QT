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
import math
import os
import re
import sys
import traceback
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

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
        return {"error": f"未知 action: {action}"}


def _stock_fund_flow(symbol: str, errors: list[str]) -> dict:
    """Get individual stock fund flow data."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    flows = []
    try:
        identity = re.fullmatch(r"(?:(SH|SZ))?(\d{6})(?:\.(SH|SZ))?", symbol.strip().upper())
        if not identity:
            return {"error": f"请提供沪深 A 股股票代码: {symbol}"}
        prefix, code, suffix = identity.groups()
        inferred = "SH" if code.startswith("6") else "SZ" if code.startswith(("0", "3")) else None
        market = prefix or suffix or inferred
        if not inferred or market != inferred or (prefix and suffix and prefix != suffix):
            return {"error": f"资金流接口仅支持沪深 A 股股票，不支持该标的或市场: {symbol}"}
        market = market.lower()
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        if df is not None and not df.empty:
            for _, row in df.sort_values("日期").tail(20).iterrows():
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
        "source": "eastmoney",
        "dataTime": flows[-1]["date"] or None,
        "retrievedAt": _retrieved_at(),
        "note": "资金流为数据商按成交单大小估算；按日期展示，不代表实时或已确认的机构交易。",
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
                    **_today_flows(row),
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
        **_snapshot_metadata(sectors),
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
                    **_today_flows(row),
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
        **_snapshot_metadata(stocks),
        "errors": errors if errors else None,
    }


def _northbound_flow(errors: list[str]) -> dict:
    """Get northbound (沪深港通) fund flow data.

    Note: since 2024-08-19 HKEX stopped disclosing real-time northbound
    flow data.  The summary API (stock_hsgt_fund_flow_summary_em) may
    still return partial data for some channels.  Historical daily flow
    from stock_hsgt_hist_em is NaN after 2024-08-16.
    We fall back to the market-wide fund flow (stock_market_fund_flow)
    for recent history (主力/超大单/大单 净流入).
    """
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    result: dict[str, Any] = {
        "action": "northbound", "source": "eastmoney", "dataTime": None,
        "retrievedAt": _retrieved_at(), "availability": "unavailable",
        "note": "2024-08-19 起不再披露北向实时净流入；接口零值/空值不能解释为真实零流入。市场主力资金不是北向资金。",
    }

    # ── Today's northbound summary ────────────────────────────────
    try:
        df = ak.stock_hsgt_fund_flow_summary_em()
        if df is not None and not df.empty:
            north = df[df["资金方向"] == "北向"]
            south = df[df["资金方向"] == "南向"]
            if not north.empty:
                hk2sh_row = north[north["板块"] == "沪股通"]
                hk2sz_row = north[north["板块"] == "深股通"]

                hk2sh_net = _safe_float(hk2sh_row["资金净流入"].iloc[0]) if not hk2sh_row.empty else None
                hk2sz_net = _safe_float(hk2sz_row["资金净流入"].iloc[0]) if not hk2sz_row.empty else None
                hk2sh_bal = _safe_float(hk2sh_row["当日资金余额"].iloc[0]) if not hk2sh_row.empty else None
                hk2sz_bal = _safe_float(hk2sz_row["当日资金余额"].iloc[0]) if not hk2sz_row.empty else None

                date_str = str(north["交易日"].iloc[0])[:10] if "交易日" in north.columns else ""
                # Preserve vendor fields for audit, but never interpret modern
                # zero placeholders or missing channels as measured net flow.
                result["rawRealtime"] = {
                    "date": date_str, "hk2sh": hk2sh_net, "hk2sz": hk2sz_net,
                    "hk2shBalance": hk2sh_bal, "hk2szBalance": hk2sz_bal,
                }
                before_policy = bool(date_str and "1900-01-01" <= date_str < "2024-08-19")
                available = before_policy and hk2sh_net is not None and hk2sz_net is not None
                result["availability"] = "historical" if available else "unavailable" if before_policy else "not_disclosed"
                result["realtime"] = {
                    "date": date_str,
                    "hk2sh": hk2sh_net if available else None,
                    "hk2sz": hk2sz_net if available else None,
                    "hk2shBalance": hk2sh_bal if before_policy else None,
                    "hk2szBalance": hk2sz_bal if before_policy else None,
                    "northTotal": round(hk2sh_net + hk2sz_net, 4) if available else None,
                    "available": available,
                }
                if available:
                    result["dataTime"] = date_str

                # Market stats from the same rows
                for channel, row in (("sh", hk2sh_row), ("sz", hk2sz_row)):
                    if not row.empty:
                        for suffix, column in (("Up", "上涨数"), ("Down", "下跌数")):
                            result["realtime"][channel + suffix] = _safe_float(row.iloc[0].get(column))

            # Southbound summary
            if not south.empty:
                sh2hk_row = south[south["板块"].str.contains("沪")]
                sz2hk_row = south[south["板块"].str.contains("深")]
                sh2hk_net = _safe_float(sh2hk_row["资金净流入"].iloc[0]) if not sh2hk_row.empty else None
                sz2hk_net = _safe_float(sz2hk_row["资金净流入"].iloc[0]) if not sz2hk_row.empty else None
                result["southbound"] = {
                    "sh2hk": sh2hk_net,
                    "sz2hk": sz2hk_net,
                    "southTotal": round((sh2hk_net or 0) + (sz2hk_net or 0), 4),
                }

    except Exception as e:
        errors.append(f"北向资金实时数据获取失败: {e}")

    # ── Recent market fund flow (主力/大单) as proxy ──────────────
    try:
        df_mf = ak.stock_market_fund_flow()
        if df_mf is not None and not df_mf.empty:
            history = []
            for _, row in df_mf.tail(10).iterrows():
                history.append({
                    "date": str(row["日期"])[:10],
                    "shIndex": _safe_float(row.get("上证-收盘价")),
                    "shChg": _safe_float(row.get("上证-涨跌幅")),
                    "mainNetIn": _safe_float(row.get("主力净流入-净额")),
                    "mainPct": _safe_float(row.get("主力净流入-净占比")),
                    "bigNetIn": _safe_float(row.get("超大单净流入-净额")),
                    "bigPct": _safe_float(row.get("超大单净流入-净占比")),
                })
            result["history"] = history
            result["historyCount"] = len(history)
            result["historyType"] = "market_fund_flow"
            result["historyNote"] = "以下是全市场主力/超大单资金流，不是北向资金历史，不能据此判断外资流入流出。"
    except Exception as e:
        errors.append(f"市场资金流向获取失败: {e}")

    if errors:
        result["errors"] = errors

    return result


def _retrieved_at() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def _today_flows(row: Any) -> dict:
    fields = {
        "mainNetIn": "主力净流入-净额", "mainNetInPct": "主力净流入-净占比",
        "superLargeNetIn": "超大单净流入-净额", "largeNetIn": "大单净流入-净额",
        "mediumNetIn": "中单净流入-净额", "smallNetIn": "小单净流入-净额",
    }
    return {key: _safe_float(row.get("今日" + field, row.get(field))) for key, field in fields.items()}


def _snapshot_metadata(rows: list[dict]) -> dict:
    available = any(row.get("mainNetIn") is not None for row in rows)
    return {
        "source": "eastmoney", "dataTime": None, "retrievedAt": _retrieved_at(),
        "availability": "available" if available else "unavailable",
        "note": "今日资金流为数据商估算，来源未提供时间戳；抓取时间不是行情时间。" if available else "来源未返回有效主力净流入，不能视作零流入。",
    }


def _safe_float(val: Any) -> float | None:
    """Convert value to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        if not math.isfinite(f):
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
