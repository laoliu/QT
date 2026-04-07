"""
market_breadth — Market breadth analysis for A-share market.

Tracks broad market health indicators:
- Advance/decline counts and ratio (涨跌家数)
- Up/down volume ratio
- New highs vs new lows
- Limit-up/limit-down statistics
- Breadth momentum indicator

Input:
  {
    "action": "overview" | "advance_decline" | "new_highs_lows" | "breadth_trend",
    "days": 5
  }

Output:
  { "breadth": {...}, "sentiment": "..." }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def run_market_breadth(config: dict) -> dict:
    """Dispatch market breadth analysis."""
    action = config.get("action", "overview")

    if action == "overview":
        return _market_overview()
    elif action == "advance_decline":
        days = config.get("days", 5)
        return _advance_decline_history(days)
    elif action == "new_highs_lows":
        return _new_highs_lows()
    elif action == "breadth_trend":
        days = config.get("days", 20)
        return _breadth_trend(days)
    else:
        return {"error": f"未知action: {action}"}


def _market_overview() -> dict:
    """Comprehensive market breadth overview for today."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    errors: list[str] = []
    result: dict[str, Any] = {"action": "overview"}

    # Get all A-share stocks current data
    try:
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return {"error": "无法获取A股实时行情"}

        total = len(df)
        change_col = "涨跌幅"
        price_col = "最新价"
        volume_col = "成交量"
        amount_col = "成交额"
        high_col = "最高"
        low_col = "最低"

        # Filter out stocks with no trading (suspended)
        active = df[df[price_col] > 0].copy()
        active_count = len(active)

        # Advance / Decline / Flat
        advancing = len(active[active[change_col] > 0])
        declining = len(active[active[change_col] < 0])
        flat = active_count - advancing - declining

        # Limit up / Limit down (涨停/跌停, approx >=9.8% or <=-9.8% for main board)
        limit_up = len(active[active[change_col] >= 9.8])
        limit_down = len(active[active[change_col] <= -9.8])

        # 20% limit for ChiNext (300xxx) and STAR (688xxx)
        special = active[active["代码"].str.startswith(("300", "688"))]
        if not special.empty:
            limit_up_20 = len(special[special[change_col] >= 19.5])
            limit_down_20 = len(special[special[change_col] <= -19.5])
            limit_up += limit_up_20
            limit_down += limit_down_20

        # Up volume vs down volume
        up_stocks = active[active[change_col] > 0]
        down_stocks = active[active[change_col] < 0]
        up_volume = float(up_stocks[amount_col].sum()) if not up_stocks.empty else 0
        down_volume = float(down_stocks[amount_col].sum()) if not down_stocks.empty else 0

        ad_ratio = round(advancing / max(declining, 1), 2)
        vol_ratio = round(up_volume / max(down_volume, 1), 2)

        # Distribution by change range
        distribution = {
            "涨幅>7%": int(len(active[active[change_col] > 7])),
            "涨幅3~7%": int(len(active[(active[change_col] > 3) & (active[change_col] <= 7)])),
            "涨幅0~3%": int(len(active[(active[change_col] > 0) & (active[change_col] <= 3)])),
            "平盘": int(flat),
            "跌幅0~3%": int(len(active[(active[change_col] < 0) & (active[change_col] >= -3)])),
            "跌幅3~7%": int(len(active[(active[change_col] < -3) & (active[change_col] >= -7)])),
            "跌幅>7%": int(len(active[active[change_col] < -7])),
        }

        # Median and mean change
        median_change = round(float(active[change_col].median()), 2)
        mean_change = round(float(active[change_col].mean()), 2)

        # Market sentiment scoring (0-100)
        sentiment_score = _calc_sentiment_score(
            advancing, declining, limit_up, limit_down, ad_ratio, median_change
        )

        result["breadth"] = {
            "total_stocks": total,
            "active_stocks": active_count,
            "advancing": advancing,
            "declining": declining,
            "flat": flat,
            "ad_ratio": ad_ratio,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "up_amount": round(up_volume / 1e8, 2),
            "down_amount": round(down_volume / 1e8, 2),
            "volume_ratio": vol_ratio,
            "median_change": median_change,
            "mean_change": mean_change,
            "distribution": distribution,
        }
        result["sentiment_score"] = sentiment_score
        result["sentiment_label"] = _sentiment_label(sentiment_score)

        # Total market turnover
        total_amount = float(active[amount_col].sum())
        result["market_turnover"] = {
            "total_amount_yi": round(total_amount / 1e8, 2),
            "unit": "亿元",
        }

    except Exception as e:
        errors.append(f"行情数据获取失败: {str(e)[:100]}")

    if errors:
        result["errors"] = errors

    return result


def _advance_decline_history(days: int) -> dict:
    """Historical advance/decline data."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    try:
        # Use Shanghai index as proxy for overall breadth
        df = ak.stock_zh_index_daily_em(symbol="sh000001")
        if df is None or df.empty:
            return {"error": "无法获取上证指数数据"}

        df = df.tail(days)
        history = []
        for _, row in df.iterrows():
            history.append({
                "date": str(row.get("date", ""))[:10],
                "close": _safe_float(row.get("close")),
                "change_pct": _safe_float(row.get("close")) and _safe_float(row.get("open")) and
                    round((_safe_float(row.get("close")) - _safe_float(row.get("open"))) / _safe_float(row.get("open")) * 100, 2)
                    if _safe_float(row.get("open")) else None,
                "volume": _safe_float(row.get("volume")),
            })

        return {
            "action": "advance_decline",
            "index": "上证指数",
            "days": days,
            "history": history,
        }

    except Exception as e:
        return {"error": f"获取历史涨跌数据失败: {str(e)[:100]}"}


def _new_highs_lows() -> dict:
    """Count stocks making new 52-week highs and lows."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    try:
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return {"error": "无法获取A股行情"}

        active = df[df["最新价"] > 0].copy()

        # Use 60-day high/low as proxy (actual 52w requires historical data)
        new_highs = len(active[active["最新价"] >= active["60日最高"]]) if "60日最高" in active.columns else 0
        new_lows = len(active[active["最新价"] <= active["60日最低"]]) if "60日最低" in active.columns else 0

        return {
            "action": "new_highs_lows",
            "new_60d_highs": int(new_highs),
            "new_60d_lows": int(new_lows),
            "highs_lows_ratio": round(new_highs / max(new_lows, 1), 2),
            "total_active": int(len(active)),
            "note": "使用60日新高/新低作为近似指标",
        }

    except Exception as e:
        return {"error": f"获取新高新低数据失败: {str(e)[:100]}"}


def _breadth_trend(days: int) -> dict:
    """Market breadth trend over N days — tracks if broad market participation is improving or weakening."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    try:
        # Get major indices for trend context
        indices = [
            ("sh000001", "上证指数"),
            ("sz399001", "深证成指"),
            ("sz399006", "创业板指"),
        ]

        trends = []
        for code, name in indices:
            try:
                df = ak.stock_zh_index_daily_em(symbol=code)
                if df is not None and not df.empty:
                    df = df.tail(days)
                    closes = df["close"].tolist()
                    if len(closes) >= 2:
                        change_total = round((closes[-1] / closes[0] - 1) * 100, 2)
                        trends.append({
                            "index": name,
                            "code": code,
                            "latest": closes[-1],
                            "change_pct": change_total,
                            "trend": "上涨" if change_total > 1 else "下跌" if change_total < -1 else "震荡",
                        })
            except Exception:
                pass

        return {
            "action": "breadth_trend",
            "days": days,
            "indices": trends,
        }

    except Exception as e:
        return {"error": f"获取宽度趋势失败: {str(e)[:100]}"}


def _calc_sentiment_score(
    advancing: int, declining: int, limit_up: int, limit_down: int,
    ad_ratio: float, median_change: float,
) -> int:
    """Calculate market sentiment score (0-100)."""
    score = 50

    # AD ratio contribution (+/- 20)
    if ad_ratio > 2:
        score += 20
    elif ad_ratio > 1.5:
        score += 15
    elif ad_ratio > 1:
        score += 5
    elif ad_ratio < 0.5:
        score -= 20
    elif ad_ratio < 0.67:
        score -= 15
    elif ad_ratio < 1:
        score -= 5

    # Limit up/down contribution (+/- 15)
    net_limits = limit_up - limit_down
    if net_limits > 30:
        score += 15
    elif net_limits > 10:
        score += 10
    elif net_limits < -30:
        score -= 15
    elif net_limits < -10:
        score -= 10

    # Median change contribution (+/- 15)
    if median_change > 2:
        score += 15
    elif median_change > 0.5:
        score += 8
    elif median_change < -2:
        score -= 15
    elif median_change < -0.5:
        score -= 8

    return max(0, min(100, score))


def _sentiment_label(score: int) -> str:
    if score >= 80:
        return "极度乐观"
    elif score >= 65:
        return "偏多"
    elif score >= 50:
        return "中性偏多"
    elif score >= 35:
        return "中性偏空"
    elif score >= 20:
        return "偏空"
    else:
        return "极度悲观"


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return round(float(val), 4)
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

        result = run_market_breadth(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
