"""
execution_analysis — Trade execution quality analysis engine.

Analyzes executed trades against market benchmarks:
  - VWAP / TWAP comparison
  - Slippage measurement
  - Market impact estimation
  - Fill rate analysis
  - Execution timing analysis
  - Best execution reporting

Input:
  { "action": "analyze_order" | "slippage_report" | "vwap_compare" | "execution_summary",
    "trades": [...],     # trade records
    "orderId": "...",    # specific order to analyze
    "from": "2024-01-01",
    "to": "2024-12-31" }

Output: execution quality metrics and recommendations.
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


def run_execution_analysis(config: dict) -> dict:
    """Dispatch execution analysis based on action."""
    action = config.get("action", "execution_summary")
    errors: list[str] = []

    if action == "analyze_order":
        return _analyze_single_order(config, errors)
    elif action == "slippage_report":
        return _slippage_report(config, errors)
    elif action == "vwap_compare":
        return _vwap_compare(config, errors)
    elif action == "execution_summary":
        return _execution_summary(config, errors)
    else:
        return {"error": f"未知操作: {action}"}


def _analyze_single_order(config: dict, errors: list[str]) -> dict:
    """Analyze a single order's execution quality."""
    trade = config.get("trade", {})
    if not trade:
        return {"error": "需要提供 trade 参数（交易记录）"}

    symbol = trade.get("symbol", "")
    fill_price = trade.get("fillPrice") or trade.get("price", 0)
    fill_time = trade.get("fillTime") or trade.get("timestamp", "")
    side = trade.get("side", "buy")
    quantity = trade.get("quantity", 0)
    order_price = trade.get("orderPrice") or trade.get("limitPrice")

    if not symbol or not fill_price:
        return {"error": "交易记录缺少 symbol 或 fillPrice"}

    # Get intraday data for VWAP calculation
    vwap = _calc_vwap_for_symbol(symbol, fill_time)
    twap = _calc_twap_for_symbol(symbol, fill_time)

    # Slippage calculation
    slippage_vs_vwap = None
    slippage_vs_order = None
    slippage_direction = 1 if side == "buy" else -1

    if vwap:
        slippage_vs_vwap = (fill_price - vwap) * slippage_direction
        slippage_vs_vwap_bps = slippage_vs_vwap / vwap * 10000

    if order_price and order_price > 0:
        slippage_vs_order = (fill_price - order_price) * slippage_direction
        slippage_vs_order_bps = slippage_vs_order / order_price * 10000

    # Market impact estimation
    impact = _estimate_market_impact(symbol, quantity, fill_price)

    result: dict[str, Any] = {
        "action": "analyze_order",
        "symbol": symbol,
        "side": side,
        "fillPrice": fill_price,
        "quantity": quantity,
        "fillTime": fill_time,
    }

    if vwap:
        result["vwap"] = round(vwap, 4)
        result["slippageVsVwap"] = round(slippage_vs_vwap, 4) if slippage_vs_vwap is not None else None
        result["slippageVsVwapBps"] = round(slippage_vs_vwap_bps, 2) if slippage_vs_vwap is not None else None
        result["vwapQuality"] = _classify_slippage(slippage_vs_vwap_bps if slippage_vs_vwap is not None else 0)

    if twap:
        result["twap"] = round(twap, 4)

    if order_price:
        result["orderPrice"] = order_price
        result["slippageVsOrder"] = round(slippage_vs_order, 4) if slippage_vs_order is not None else None
        result["slippageVsOrderBps"] = round(slippage_vs_order_bps, 2) if slippage_vs_order is not None else None

    if impact:
        result["estimatedImpact"] = impact

    if errors:
        result["errors"] = errors
    return result


def _slippage_report(config: dict, errors: list[str]) -> dict:
    """Generate a slippage report across multiple trades."""
    trades = config.get("trades", [])
    if not trades:
        return {"error": "需要提供 trades 参数（交易记录列表）"}

    total_slippage = 0
    total_trades = len(trades)
    positive_slippage = 0  # favorable
    negative_slippage = 0  # unfavorable
    slippage_details = []

    for trade in trades:
        symbol = trade.get("symbol", "")
        fill_price = trade.get("fillPrice") or trade.get("price", 0)
        side = trade.get("side", "buy")
        quantity = trade.get("quantity", 0)
        order_price = trade.get("orderPrice") or trade.get("limitPrice")

        if not fill_price or not order_price or order_price <= 0:
            continue

        direction = 1 if side == "buy" else -1
        slippage = (fill_price - order_price) * direction
        slippage_bps = slippage / order_price * 10000
        slippage_cost = slippage * quantity

        total_slippage += slippage_cost
        if slippage <= 0:
            positive_slippage += 1
        else:
            negative_slippage += 1

        slippage_details.append({
            "symbol": symbol,
            "side": side,
            "orderPrice": order_price,
            "fillPrice": fill_price,
            "slippageBps": round(slippage_bps, 2),
            "slippageCost": round(slippage_cost, 2),
        })

    avg_slippage_bps = (
        sum(d["slippageBps"] for d in slippage_details) / len(slippage_details)
        if slippage_details else 0
    )

    result: dict[str, Any] = {
        "action": "slippage_report",
        "totalTrades": total_trades,
        "analyzedTrades": len(slippage_details),
        "totalSlippageCost": round(total_slippage, 2),
        "avgSlippageBps": round(avg_slippage_bps, 2),
        "favorableCount": positive_slippage,
        "unfavorableCount": negative_slippage,
        "favorableRate": round(positive_slippage / len(slippage_details) * 100, 1) if slippage_details else 0,
        "details": slippage_details[:20],  # limit output
        "interpretation": _interpret_slippage(avg_slippage_bps),
    }
    if errors:
        result["errors"] = errors
    return result


def _vwap_compare(config: dict, errors: list[str]) -> dict:
    """Compare trade fills against VWAP."""
    trades = config.get("trades", [])
    if not trades:
        return {"error": "需要提供 trades 参数（交易记录列表）"}

    comparisons = []
    total_cost_saving = 0

    for trade in trades:
        symbol = trade.get("symbol", "")
        fill_price = trade.get("fillPrice") or trade.get("price", 0)
        fill_time = trade.get("fillTime") or trade.get("timestamp", "")
        side = trade.get("side", "buy")
        quantity = trade.get("quantity", 0)

        if not fill_price or not symbol:
            continue

        vwap = _calc_vwap_for_symbol(symbol, fill_time)
        if not vwap:
            comparisons.append({
                "symbol": symbol, "side": side, "fillPrice": fill_price,
                "vwap": None, "note": "无法计算VWAP",
            })
            continue

        direction = 1 if side == "buy" else -1
        vs_vwap = (fill_price - vwap) * direction
        vs_vwap_bps = vs_vwap / vwap * 10000
        cost_impact = vs_vwap * quantity

        total_cost_saving -= cost_impact  # negative = saving for buys

        comparisons.append({
            "symbol": symbol,
            "side": side,
            "fillPrice": round(fill_price, 4),
            "vwap": round(vwap, 4),
            "vsVwapBps": round(vs_vwap_bps, 2),
            "costImpact": round(cost_impact, 2),
            "quality": _classify_slippage(vs_vwap_bps),
        })

    beats = sum(1 for c in comparisons if c.get("vsVwapBps") is not None and c["vsVwapBps"] <= 0)
    beat_rate = beats / len(comparisons) * 100 if comparisons else 0

    result: dict[str, Any] = {
        "action": "vwap_compare",
        "totalTrades": len(trades),
        "analyzedTrades": len(comparisons),
        "beatVwapRate": round(beat_rate, 1),
        "totalCostImpact": round(total_cost_saving, 2),
        "comparisons": comparisons[:20],
    }
    if errors:
        result["errors"] = errors
    return result


def _execution_summary(config: dict, errors: list[str]) -> dict:
    """Generate an overall execution quality summary."""
    trades = config.get("trades", [])
    if not trades:
        return {"error": "需要提供 trades 参数（交易记录列表）"}

    total = len(trades)
    buy_trades = [t for t in trades if t.get("side") == "buy"]
    sell_trades = [t for t in trades if t.get("side") == "sell"]

    # Commission analysis
    total_commission = sum(t.get("commission", 0) for t in trades)
    total_amount = sum((t.get("fillPrice", 0) or t.get("price", 0)) * t.get("quantity", 0) for t in trades)
    commission_rate = total_commission / total_amount * 10000 if total_amount > 0 else 0

    # Timing analysis: group by hour
    hour_distribution: dict[int, int] = {}
    for t in trades:
        ts = t.get("fillTime") or t.get("timestamp", "")
        if isinstance(ts, str) and len(ts) >= 13:
            try:
                hour = int(ts[11:13])
                hour_distribution[hour] = hour_distribution.get(hour, 0) + 1
            except ValueError:
                pass
        elif isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(ts / 1000 if ts > 1e12 else ts)
            hour = dt.hour
            hour_distribution[hour] = hour_distribution.get(hour, 0) + 1

    # Fill rate (orders that got fully filled)
    filled = sum(1 for t in trades if t.get("status") in ("filled", "executed", None))
    partial = sum(1 for t in trades if t.get("status") == "partial")
    cancelled = sum(1 for t in trades if t.get("status") in ("cancelled", "rejected"))

    result: dict[str, Any] = {
        "action": "execution_summary",
        "totalTrades": total,
        "buyTrades": len(buy_trades),
        "sellTrades": len(sell_trades),
        "totalAmount": round(total_amount, 2),
        "totalCommission": round(total_commission, 2),
        "commissionRateBps": round(commission_rate, 2),
        "fillRate": {
            "filled": filled,
            "partial": partial,
            "cancelled": cancelled,
            "fillPct": round(filled / total * 100, 1) if total > 0 else 0,
        },
        "timingDistribution": dict(sorted(hour_distribution.items())),
        "peakHour": max(hour_distribution, key=hour_distribution.get) if hour_distribution else None,
        "recommendations": _generate_recommendations(trades, commission_rate, hour_distribution),
    }
    if errors:
        result["errors"] = errors
    return result


def _calc_vwap_for_symbol(symbol: str, fill_time: str = "") -> float | None:
    """Calculate VWAP for a symbol on the fill date."""
    try:
        import akshare as ak
        # Get daily data — use close * volume as approximation
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                 start_date=start_date, end_date=end_date, adjust="qfq")
        if df is None or df.empty:
            return None
        row = df.iloc[-1]
        # VWAP ≈ amount / volume (or use OHLC average as fallback)
        amount = float(row.get("成交额", 0))
        volume = float(row.get("成交量", 0))
        if amount > 0 and volume > 0:
            return amount / volume / 100  # akshare volume is in shares, amount in yuan
        # Fallback: typical price
        return (float(row["最高"]) + float(row["最低"]) + float(row["收盘"])) / 3
    except Exception:
        return None


def _calc_twap_for_symbol(symbol: str, fill_time: str = "") -> float | None:
    """Calculate TWAP (Time-Weighted Average Price) — approximation from OHLC."""
    try:
        import akshare as ak
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=5)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                 start_date=start_date, end_date=end_date, adjust="qfq")
        if df is None or df.empty:
            return None
        row = df.iloc[-1]
        return (float(row["开盘"]) + float(row["最高"]) + float(row["最低"]) + float(row["收盘"])) / 4
    except Exception:
        return None


def _estimate_market_impact(symbol: str, quantity: int, fill_price: float) -> dict | None:
    """Estimate market impact of the trade."""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return None
        row = df[df["代码"] == symbol]
        if row.empty:
            return None
        row = row.iloc[0]
        avg_volume = float(row.get("成交量", 0))
        if avg_volume <= 0:
            return None

        trade_pct = quantity / avg_volume * 100 if avg_volume > 0 else 0
        # Simple market impact model: impact ∝ sqrt(trade_pct)
        estimated_impact_bps = (trade_pct ** 0.5) * 5  # simplified model

        return {
            "avgDailyVolume": int(avg_volume),
            "tradePctOfVolume": round(trade_pct, 4),
            "estimatedImpactBps": round(estimated_impact_bps, 2),
            "impactLevel": "低" if estimated_impact_bps < 5 else "中" if estimated_impact_bps < 20 else "高",
        }
    except Exception:
        return None


def _classify_slippage(bps: float) -> str:
    """Classify execution quality based on slippage in bps."""
    if bps <= -5:
        return "优秀(有利滑点)"
    elif bps <= 0:
        return "良好"
    elif bps <= 5:
        return "正常"
    elif bps <= 15:
        return "偏差"
    else:
        return "较差"


def _interpret_slippage(avg_bps: float) -> str:
    """Interpret overall slippage performance."""
    if avg_bps <= 0:
        return "执行质量优秀，平均获得有利滑点"
    elif avg_bps <= 5:
        return "执行质量良好，滑点在正常范围内"
    elif avg_bps <= 15:
        return "执行质量一般，建议优化下单策略（如使用限价单或TWAP算法）"
    else:
        return "执行质量较差，滑点偏高。建议：1)避开开盘/收盘高波动时段 2)拆单执行 3)使用条件单"


def _generate_recommendations(trades: list, commission_rate: float, hour_dist: dict) -> list[str]:
    """Generate execution improvement recommendations."""
    recs = []

    if commission_rate > 5:
        recs.append(f"佣金费率({commission_rate:.1f}bps)偏高，建议与券商协商降佣")

    market_orders = sum(1 for t in trades if t.get("orderType") == "market")
    if market_orders > len(trades) * 0.5:
        recs.append("市价单占比过高，建议更多使用限价单减少滑点")

    # Check if trades are concentrated at open/close
    if hour_dist:
        open_close = hour_dist.get(9, 0) + hour_dist.get(14, 0)
        if open_close > sum(hour_dist.values()) * 0.5:
            recs.append("交易过度集中在开盘/收盘时段，建议分散执行时间")

    # Large single trades
    for t in trades:
        qty = t.get("quantity", 0)
        price = t.get("fillPrice", 0) or t.get("price", 0)
        if qty * price > 500000:
            recs.append("存在大额单笔交易(>50万)，建议使用TWAP/VWAP算法拆单")
            break

    if not recs:
        recs.append("执行质量良好，暂无改进建议")

    return recs


# ── NDJSON bridge entry point ──────────────────────────────────
if __name__ == "__main__":
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            config = json.loads(line)
            result = run_execution_analysis(config)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False), flush=True)
