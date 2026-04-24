"""
institutional_flow — Institutional holdings, block trades, insider transactions.

Fetches fund holdings, main force money flow, block trades, and insider
trading data from akshare.

Input (JSON via stdin / QUANT_CONFIG):
  {
    "action": "fund_holdings" | "holding_changes" | "block_trades"
             | "insider_trades" | "smart_money_signal",
    "symbol": "600519",
    "limit": 20
  }

Output: JSON with institutional data.
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


def run_institutional_flow(config: dict) -> dict:
    """Dispatch institutional flow actions."""
    action = config.get("action", "fund_holdings")
    symbol = config.get("symbol", "")
    limit = config.get("limit", 20)

    if action == "fund_holdings":
        if not symbol:
            return {"error": "symbol is required for fund_holdings"}
        return _fund_holdings(symbol, limit)
    elif action == "holding_changes":
        if not symbol:
            return {"error": "symbol is required for holding_changes"}
        return _holding_changes(symbol, limit)
    elif action == "block_trades":
        return _block_trades(symbol, limit)
    elif action == "insider_trades":
        if not symbol:
            return {"error": "symbol is required for insider_trades"}
        return _insider_trades(symbol, limit)
    elif action == "smart_money_signal":
        if not symbol:
            return {"error": "symbol is required for smart_money_signal"}
        return _smart_money_signal(symbol)
    else:
        return {"error": f"δ֪ action: {action}"}


# ── Fund holdings ─────────────────────────────────────────────────────────────

def _fund_holdings(symbol: str, limit: int) -> dict:
    """Which funds hold this stock and how much."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    holders: list[dict] = []

    # Top 10 tradable shareholders
    try:
        df = ak.stock_gdfx_free_holding_detail_em(symbol=symbol)
        if df is not None and not df.empty:
            for _, row in df.head(limit).iterrows():
                holders.append({
                    "holder": str(row.get("股东名称", "")),
                    "shares": _safe_float(row.get("持股数量", row.get("持股数", None))),
                    "pct": _safe_float(row.get("持股比例", row.get("占总股本比例", None))),
                    "change": str(row.get("增减", row.get("变动", ""))),
                    "holder_type": str(row.get("股东性质", "")),
                    "report_date": str(row.get("报告期", ""))[:10],
                })
    except Exception:
        pass

    # Institutional investors (fund holdings)
    fund_holders: list[dict] = []
    try:
        df2 = ak.stock_gdfx_holding_detail_em(symbol=symbol)
        if df2 is not None and not df2.empty:
            for _, row in df2.head(limit).iterrows():
                fund_holders.append({
                    "fund_name": str(row.get("基金名称", row.get("股东名称", ""))),
                    "shares": _safe_float(row.get("持股数量", row.get("持股数", None))),
                    "pct": _safe_float(row.get("持股比例", row.get("占流通股比例", None))),
                    "market_value": _safe_float(row.get("持股市值", None)),
                    "report_date": str(row.get("报告期", ""))[:10],
                })
    except Exception:
        pass

    return {
        "symbol": symbol,
        "top_holders_count": len(holders),
        "top_holders": holders,
        "fund_holders_count": len(fund_holders),
        "fund_holders": fund_holders[:limit],
    }


# ── Holding changes (quarter-over-quarter delta) ─────────────────────────────

def _holding_changes(symbol: str, limit: int) -> dict:
    """Track institutional holding changes across quarters."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    changes: list[dict] = []

    try:
        # Institutional holding change tracking
        df = ak.stock_gdfx_free_holding_analyse_em(symbol=symbol)
        if df is not None and not df.empty:
            for _, row in df.head(limit).iterrows():
                changes.append({
                    "holder": str(row.get("股东名称", "")),
                    "report_date": str(row.get("报告期", ""))[:10],
                    "shares": _safe_float(row.get("持股数量", row.get("期末持股-数量", None))),
                    "pct": _safe_float(row.get("持股比例", row.get("期末持股-比例", None))),
                    "change_shares": _safe_float(row.get("持股变动-数量", row.get("变动数量", None))),
                    "change_pct": _safe_float(row.get("持股变动-比例", row.get("变动比例", None))),
                    "change_direction": str(row.get("增减", row.get("变动方向", ""))),
                })
    except Exception:
        pass

    # Aggregate: net institutional change
    total_increase = sum(c.get("change_shares", 0) or 0 for c in changes
                         if c.get("change_direction") in ("增持", "新进", "增加"))
    total_decrease = sum(abs(c.get("change_shares", 0) or 0) for c in changes
                         if c.get("change_direction") in ("减持", "减少"))

    return {
        "symbol": symbol,
        "count": len(changes),
        "changes": changes,
        "summary": {
            "total_increase_shares": total_increase,
            "total_decrease_shares": total_decrease,
            "net_change": total_increase - total_decrease,
            "trend": ("机构净增持" if total_increase > total_decrease
                      else "机构净减持" if total_decrease > total_increase
                      else "持平"),
        },
    }


# ── Block trades (大宗交易) ───────────────────────────────────────────────────

def _block_trades(symbol: str, limit: int) -> dict:
    """Recent block trades (大宗交易)."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    from datetime import datetime, timedelta

    trades: list[dict] = []

    try:
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=30)).strftime("%Y%m%d")

        if symbol:
            # Individual stock block trades — fetch A股 market then filter
            df = ak.stock_dzjy_mrmx(symbol="A股", start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                df = df[df["证券代码"].astype(str).str.strip() == symbol]
        else:
            # Market-wide block trade summary
            df = ak.stock_dzjy_mrtj(start_date=start_date, end_date=end_date)

        if df is not None and not df.empty:
            # stock_dzjy_mrtj returns 成交总量(万股)/成交总额(万元),
            # stock_dzjy_mrmx returns 成交量(股)/成交额(元) — need conversion
            is_mrtj = "成交总量" in df.columns

            for _, row in df.head(limit).iterrows():
                vol = _safe_float(row.get("成交量", row.get("成交总量", row.get("成交量(万股)", None))))
                amt = _safe_float(row.get("成交额", row.get("成交总额", row.get("成交额(万元)", None))))
                # Convert raw units to 万 for mrmx data
                if not is_mrtj:
                    if vol is not None:
                        vol = round(vol / 10000, 2)
                    if amt is not None:
                        amt = round(amt / 10000, 2)

                trade = {
                    "date": str(row.get("交易日期", row.get("日期", "")))[:10],
                    "symbol": str(row.get("证券代码", row.get("代码", symbol))),
                    "name": str(row.get("证券简称", row.get("名称", ""))),
                    "price": _safe_float(row.get("成交价", row.get("成交价格", None))),
                    "volume": vol,
                    "amount": amt,
                }
                # Premium/discount — prefer 折溢率 column directly
                prem = _safe_float(row.get("折溢率", None))
                if prem is not None:
                    trade["premium_pct"] = round(prem, 2)
                else:
                    close = _safe_float(row.get("收盘价", None))
                    trade_price = trade.get("price")
                    if close and trade_price:
                        trade["premium_pct"] = round((trade_price - close) / close * 100, 2)

                buyer = str(row.get("买方营业部", ""))
                seller = str(row.get("卖方营业部", ""))
                if buyer:
                    trade["buyer"] = buyer
                if seller:
                    trade["seller"] = seller

                trades.append(trade)
    except Exception:
        pass

    return {
        "symbol": symbol or "全市场",
        "count": len(trades),
        "trades": trades,
    }


# ── Insider trades ────────────────────────────────────────────────────────────

def _insider_trades(symbol: str, limit: int) -> dict:
    """Management/insider buy/sell transactions."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    transactions: list[dict] = []

    # Director/executive trades
    try:
        df = ak.stock_ggcg_em(symbol=symbol)
        if df is not None and not df.empty:
            for _, row in df.head(limit).iterrows():
                transactions.append({
                    "date": str(row.get("变动日期", row.get("公告日期", "")))[:10],
                    "person": str(row.get("变动人", row.get("董监高姓名", ""))),
                    "position": str(row.get("职务", "")),
                    "action": str(row.get("变动方向", row.get("变动类型", ""))),
                    "shares": _safe_float(row.get("变动股数", row.get("变动数量", None))),
                    "price": _safe_float(row.get("成交均价", row.get("变动均价", None))),
                    "shares_after": _safe_float(row.get("变动后持股", row.get("结存股数", None))),
                })
    except Exception:
        pass

    # Separate buys vs sells
    buys = [t for t in transactions if "增持" in str(t.get("action", ""))
            or "买入" in str(t.get("action", ""))]
    sells = [t for t in transactions if "减持" in str(t.get("action", ""))
             or "卖出" in str(t.get("action", ""))]

    return {
        "symbol": symbol,
        "count": len(transactions),
        "transactions": transactions,
        "summary": {
            "buy_count": len(buys),
            "sell_count": len(sells),
            "net_direction": ("内部人净买入" if len(buys) > len(sells)
                              else "内部人净卖出" if len(sells) > len(buys)
                              else "持平"),
        },
    }


# ── Smart money composite signal ─────────────────────────────────────────────

def _smart_money_signal(symbol: str) -> dict:
    """
    Composite institutional conviction score combining:
    - Institutional holding trend (increase/decrease)
    - Block trade premium/discount
    - Insider buy/sell ratio
    - Main force money flow
    """
    score = 0
    signals: dict[str, Any] = {}
    details: list[str] = []

    # 1. Institutional holding trend
    holding_data = _holding_changes(symbol, 10)
    net_change = holding_data.get("summary", {}).get("net_change", 0) or 0
    if net_change > 0:
        score += 25
        signals["institutional_trend"] = "增持"
        details.append(f"机构净增持 {net_change:.0f} 股")
    elif net_change < 0:
        score -= 15
        signals["institutional_trend"] = "减持"
        details.append(f"机构净减持 {abs(net_change):.0f} 股")
    else:
        signals["institutional_trend"] = "无变动"

    # 2. Block trade premium
    block_data = _block_trades(symbol, 5)
    premiums = [t.get("premium_pct", 0) for t in block_data.get("trades", [])
                if t.get("premium_pct") is not None]
    if premiums:
        avg_premium = sum(premiums) / len(premiums)
        signals["block_trade_premium"] = round(avg_premium, 2)
        if avg_premium > 0:
            score += 15
            details.append(f"大宗交易溢价 {avg_premium:.1f}%")
        elif avg_premium < -3:
            score -= 10
            details.append(f"大宗交易折价 {avg_premium:.1f}%")

    # 3. Insider trading
    insider_data = _insider_trades(symbol, 10)
    insider_summary = insider_data.get("summary", {})
    buy_count = insider_summary.get("buy_count", 0)
    sell_count = insider_summary.get("sell_count", 0)
    if buy_count > sell_count:
        score += 20
        signals["insider_direction"] = "净买入"
        details.append(f"内部人买入{buy_count}笔 > 卖出{sell_count}笔")
    elif sell_count > buy_count:
        score -= 15
        signals["insider_direction"] = "净卖出"
        details.append(f"内部人卖出{sell_count}笔 > 买入{buy_count}笔")

    # 4. Main force money flow (use fund_flow engine if available)
    try:
        from engines.fund_flow import run_fund_flow
        flow_data = run_fund_flow({"action": "stock", "symbol": symbol})
        flows = flow_data.get("flows", [])
        if flows:
            recent_net = sum(_safe_float(f.get("net", 0)) or 0 for f in flows[-5:])
            signals["main_force_flow_5d"] = round(recent_net, 2)
            if recent_net > 0:
                score += 15
                details.append(f"近5日主力净流入 {recent_net:.0f} 万")
            elif recent_net < 0:
                score -= 10
                details.append(f"近5日主力净流出 {abs(recent_net):.0f} 万")
    except Exception:
        pass

    # Normalize score to 0-100
    score = max(0, min(100, score + 50))

    if score >= 70:
        conclusion = "机构信心强，Smart Money 看多信号"
    elif score >= 50:
        conclusion = "机构态度中性，观望为主"
    elif score >= 30:
        conclusion = "机构谨慎，注意减仓风险"
    else:
        conclusion = "机构看空信号明显，建议回避"

    return {
        "symbol": symbol,
        "smart_money_score": score,
        "conclusion": conclusion,
        "signals": signals,
        "details": details,
    }


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN
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

        result = run_institutional_flow(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
