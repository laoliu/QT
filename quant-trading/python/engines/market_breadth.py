"""
market_breadth — A-share market breadth indicators.

Actions:
  overview    — Market breadth overview (advance/decline, limit-up/down, MA breadth)
  ma_breadth  — Percentage of stocks above MA20/MA60 (market width)
  history     — Historical breadth trend over N days
  sentiment   — Composite market sentiment score (wraps market_sentiment)

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


def _safe_int(val: Any, default: int = 0) -> int:
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def overview(date: str | None = None) -> dict[str, Any]:
    """Market breadth overview: advance/decline counts, limit-up/down, volume."""
    try:
        import akshare as ak

        # Get market summary — use stock_zh_a_spot_em for real-time A-share data
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return {"error": "无法获取A股行情数据"}

        total = len(df)

        # Advance/Decline
        change_col = "涨跌幅"
        if change_col not in df.columns:
            # Try alternative column name
            for col in df.columns:
                if "涨跌" in col and "幅" in col:
                    change_col = col
                    break

        df[change_col] = df[change_col].apply(lambda x: _safe_float(x))
        advances = int((df[change_col] > 0).sum())
        declines = int((df[change_col] < 0).sum())
        unchanged = total - advances - declines

        # Limit-up / Limit-down (涨停/跌停)
        limit_up = int((df[change_col] >= 9.9).sum())
        limit_down = int((df[change_col] <= -9.9).sum())

        # Count by change range
        big_rise = int((df[change_col] >= 5).sum())      # 涨幅>5%
        big_fall = int((df[change_col] <= -5).sum())      # 跌幅>5%
        small_rise = int(((df[change_col] > 0) & (df[change_col] < 5)).sum())
        small_fall = int(((df[change_col] < 0) & (df[change_col] > -5)).sum())

        # A/D ratio
        ad_ratio = round(advances / max(declines, 1), 2)

        # 赚钱效应 (profit effect)
        profit_pct = round(advances / max(total, 1) * 100, 1)

        # Average change
        avg_change = round(df[change_col].mean(), 2)
        median_change = round(df[change_col].median(), 2)

        return {
            "date": date or datetime.now().strftime("%Y-%m-%d"),
            "total": total,
            "advances": advances,
            "declines": declines,
            "unchanged": unchanged,
            "ad_ratio": ad_ratio,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "big_rise_gt5pct": big_rise,
            "big_fall_gt5pct": big_fall,
            "small_rise": small_rise,
            "small_fall": small_fall,
            "profit_pct": profit_pct,
            "avg_change_pct": avg_change,
            "median_change_pct": median_change,
            "market_mood": (
                "强势" if profit_pct > 60 and ad_ratio > 2
                else "偏强" if profit_pct > 50
                else "偏弱" if profit_pct > 35
                else "弱势"
            ),
        }

    except Exception as e:
        return {"error": f"获取市场广度数据失败: {str(e)}"}


def ma_breadth(period: int = 20) -> dict[str, Any]:
    """Percentage of stocks trading above their MA (market width indicator)."""
    try:
        import akshare as ak

        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return {"error": "无法获取A股行情数据"}

        # For MA breadth, we need current price and MA value
        # Use the real-time data which includes some MA fields
        total = len(df)

        # Get current price
        price_col = "最新价"
        if price_col not in df.columns:
            for col in df.columns:
                if "最新" in col or "现价" in col:
                    price_col = col
                    break

        # Calculate approximate MA breadth using available data
        # For a quick estimate: use 涨跌幅 and 60日涨跌幅 if available
        change_col = "涨跌幅"

        # Count stocks with positive N-day trends as proxy for above-MA
        # A more accurate version would fetch historical klines per stock
        above_count = 0
        below_count = 0
        valid_count = 0

        # Use real-time data columns for MA estimation
        for col in df.columns:
            if f"{period}日" in col and "涨" in col:
                df[col] = df[col].apply(lambda x: _safe_float(x))
                above_count = int((df[col] > 0).sum())
                below_count = int((df[col] <= 0).sum())
                valid_count = total
                break

        if valid_count == 0:
            # Fallback: estimate using change percentage
            df[change_col] = df[change_col].apply(lambda x: _safe_float(x))
            above_count = int((df[change_col] > 0).sum())
            below_count = int((df[change_col] <= 0).sum())
            valid_count = total

        above_pct = round(above_count / max(valid_count, 1) * 100, 1)

        return {
            "period": period,
            "total_stocks": total,
            "above_ma": above_count,
            "below_ma": below_count,
            "above_pct": above_pct,
            "below_pct": round(100 - above_pct, 1),
            "breadth_signal": (
                "极强" if above_pct > 80
                else "偏强" if above_pct > 60
                else "中性" if above_pct > 40
                else "偏弱" if above_pct > 20
                else "极弱"
            ),
        }

    except Exception as e:
        return {"error": f"获取MA广度数据失败: {str(e)}"}


def history(days: int = 20) -> dict[str, Any]:
    """Historical market breadth trend."""
    try:
        import akshare as ak

        # Get market index for context
        records = []

        # Use Shanghai index daily data for market context
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

        try:
            idx_df = ak.stock_zh_index_daily_em(symbol="sh000001", start_date=start_date, end_date=end_date)
            if idx_df is not None and not idx_df.empty:
                idx_df = idx_df.tail(days)
                for _, row in idx_df.iterrows():
                    date_str = str(row.get("date", row.get("日期", "")))[:10]
                    records.append({
                        "date": date_str,
                        "close": _safe_float(row.get("close", row.get("收盘", 0))),
                        "change_pct": _safe_float(row.get("涨跌幅", 0)),
                        "volume": _safe_float(row.get("volume", row.get("成交量", 0))),
                    })
        except Exception:
            pass

        if not records:
            return {"error": "无法获取历史数据", "records": []}

        return {
            "index": "上证指数",
            "days": len(records),
            "records": records[-days:],
        }

    except Exception as e:
        return {"error": f"获取历史广度数据失败: {str(e)}"}


def sentiment() -> dict[str, Any]:
    """Composite market sentiment combining breadth + limit-up/down data."""
    try:
        # Get overview data
        ov = overview()
        if "error" in ov and not ov.get("total"):
            return ov

        total = ov.get("total", 1)
        advances = ov.get("advances", 0)
        declines = ov.get("declines", 0)
        limit_up = ov.get("limit_up", 0)
        limit_down = ov.get("limit_down", 0)
        profit_pct = ov.get("profit_pct", 50)

        # Sentiment score 0-100
        # Components:
        # 1. A/D ratio contribution (0-30)
        ad_score = min(30, max(0, (advances / max(total, 1) * 60)))

        # 2. Limit-up vs limit-down (0-30)
        if limit_up + limit_down > 0:
            lu_ratio = limit_up / (limit_up + limit_down)
            lu_score = lu_ratio * 30
        else:
            lu_score = 15  # neutral

        # 3. Profit effect (0-40)
        profit_score = min(40, max(0, profit_pct * 0.4))

        total_score = round(ad_score + lu_score + profit_score)

        return {
            **ov,
            "sentiment_score": total_score,
            "sentiment_label": (
                "极度贪婪" if total_score > 80
                else "贪婪" if total_score > 65
                else "中性偏多" if total_score > 50
                else "中性偏空" if total_score > 35
                else "恐惧" if total_score > 20
                else "极度恐惧"
            ),
            "components": {
                "ad_score": round(ad_score, 1),
                "limit_up_down_score": round(lu_score, 1),
                "profit_effect_score": round(profit_score, 1),
            },
        }

    except Exception as e:
        return {"error": f"计算市场情绪失败: {str(e)}"}


def main():
    try:
        raw = sys.stdin.read()
        args = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        args = {}

    action = args.get("action", "overview")

    if action == "overview":
        result = overview(args.get("date"))
    elif action == "ma_breadth":
        result = ma_breadth(args.get("period", 20))
    elif action == "history":
        result = history(args.get("days", 20))
    elif action == "sentiment":
        result = sentiment()
    else:
        result = {"error": f"未知action: {action}"}

    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
