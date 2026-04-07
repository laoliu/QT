"""
sector_rotation — Sector rotation tracking and analysis (板块轮动分析).

Provides:
  - Multi-day sector performance tracking (板块N日涨跌排名)
  - Sector momentum scoring (板块动量评分)
  - Sector rotation detection (轮动信号)
  - Hot sector identification (热点板块识别)

Called from TypeScript sector_analysis tool via python-bridge.
Input:
  { "action": "rotation" | "momentum" | "hot", "days": 5, "limit": 20 }

Output varies by action:
  rotation  → { rotation_signal, leading, lagging, sectors: [...] }
  momentum  → { sectors: [{name, scores, trend}] }
  hot       → { hot_sectors: [{name, reason, strength}] }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

# Proxy bypass
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def run_sector_rotation(config: dict) -> dict:
    """Dispatch sector rotation analysis."""
    action = config.get("action", "rotation")
    days = config.get("days", 5)
    limit = config.get("limit", 20)
    errors: list[str] = []

    if action == "rotation":
        return _sector_rotation(days, limit, errors)
    elif action == "momentum":
        return _sector_momentum(days, limit, errors)
    elif action == "hot":
        return _hot_sectors(limit, errors)
    else:
        return {"error": f"未知action: {action}"}


def _sector_rotation(days: int, limit: int, errors: list[str]) -> dict:
    """Analyze sector rotation over N days.

    Compares short-term vs medium-term sector performance to detect rotation.
    """
    try:
        import akshare as ak
        import pandas as pd
    except ImportError:
        return {"error": "akshare 或 pandas 未安装"}

    sectors: list[dict] = []

    try:
        # Get current sector data (industry boards)
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return {"error": "无法获取板块数据", "errors": errors}

        for _, row in df.iterrows():
            name = str(row.get("板块名称", ""))
            code = str(row.get("板块代码", ""))
            if not name or not code:
                continue

            # Get historical data for this sector
            try:
                hist = ak.stock_board_industry_hist_em(
                    symbol=name,
                    period="日k",
                    start_date=(datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d"),
                    end_date=datetime.now().strftime("%Y%m%d"),
                    adjust="",
                )
                if hist is None or hist.empty or len(hist) < 2:
                    continue

                hist = hist.sort_values("日期").tail(days + 1)
                closes = hist["收盘"].astype(float).tolist()

                if len(closes) < 2:
                    continue

                # N-day change
                n_day_change = round((closes[-1] / closes[0] - 1) * 100, 2)

                # Recent 1-day change
                one_day_change = round((closes[-1] / closes[-2] - 1) * 100, 2) if len(closes) >= 2 else 0

                # Short-term momentum (last 3 days vs prior)
                if len(closes) >= 4:
                    mid = len(closes) // 2
                    first_half = (closes[mid] / closes[0] - 1) * 100 if closes[0] > 0 else 0
                    second_half = (closes[-1] / closes[mid] - 1) * 100 if closes[mid] > 0 else 0
                    acceleration = round(second_half - first_half, 2)
                else:
                    acceleration = 0

                sectors.append({
                    "name": name,
                    "code": code,
                    "nDayChange": n_day_change,
                    "oneDayChange": one_day_change,
                    "acceleration": acceleration,
                    "latestClose": round(closes[-1], 2),
                })
            except Exception:
                continue

    except Exception as e:
        errors.append(f"板块轮动分析失败: {e}")
        traceback.print_exc(file=sys.stderr)

    if not sectors:
        return {"error": "无法获取板块轮动数据", "errors": errors}

    # Sort by N-day change
    sectors.sort(key=lambda s: s.get("nDayChange", 0), reverse=True)

    # Identify rotation signals
    leading = sectors[:5]
    lagging = sectors[-5:]

    # Rotation strength: how different are top vs bottom performers
    top_avg = sum(s["nDayChange"] for s in leading) / len(leading) if leading else 0
    bottom_avg = sum(s["nDayChange"] for s in lagging) / len(lagging) if lagging else 0
    spread = round(top_avg - bottom_avg, 2)

    # Accelerating sectors (gaining momentum)
    accelerating = sorted(
        [s for s in sectors if s.get("acceleration", 0) > 1],
        key=lambda s: s["acceleration"],
        reverse=True,
    )[:5]

    # Decelerating sectors (losing momentum)
    decelerating = sorted(
        [s for s in sectors if s.get("acceleration", 0) < -1],
        key=lambda s: s["acceleration"],
    )[:5]

    rotation_signal = "明显" if spread > 5 else ("温和" if spread > 2 else "不明显")

    return {
        "action": "rotation",
        "days": days,
        "rotationSignal": rotation_signal,
        "spread": spread,
        "topAvgChange": round(top_avg, 2),
        "bottomAvgChange": round(bottom_avg, 2),
        "leading": leading[:limit],
        "lagging": lagging[:limit],
        "accelerating": accelerating,
        "decelerating": decelerating,
        "totalSectors": len(sectors),
        "allSectors": sectors[:limit],
        "errors": errors if errors else None,
    }


def _sector_momentum(days: int, limit: int, errors: list[str]) -> dict:
    """Score sectors by momentum (combining price change, volume, and trend)."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    sectors: list[dict] = []

    try:
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return {"error": "无法获取板块数据"}

        for _, row in df.iterrows():
            name = str(row.get("板块名称", ""))
            if not name:
                continue

            try:
                hist = ak.stock_board_industry_hist_em(
                    symbol=name,
                    period="日k",
                    start_date=(datetime.now() - timedelta(days=days * 3)).strftime("%Y%m%d"),
                    end_date=datetime.now().strftime("%Y%m%d"),
                    adjust="",
                )
                if hist is None or hist.empty or len(hist) < 3:
                    continue

                hist = hist.sort_values("日期").tail(days + 1)
                closes = hist["收盘"].astype(float).tolist()
                volumes = hist["成交量"].astype(float).tolist()

                if len(closes) < 2:
                    continue

                # Price momentum
                price_change = (closes[-1] / closes[0] - 1) * 100 if closes[0] > 0 else 0

                # Volume trend (recent vs older)
                if len(volumes) >= 4:
                    mid = len(volumes) // 2
                    old_vol = sum(volumes[:mid]) / mid if mid > 0 else 1
                    new_vol = sum(volumes[mid:]) / (len(volumes) - mid) if (len(volumes) - mid) > 0 else 1
                    vol_ratio = new_vol / old_vol if old_vol > 0 else 1
                else:
                    vol_ratio = 1.0

                # Trend consistency: count positive days
                day_changes = [(closes[i] / closes[i - 1] - 1) for i in range(1, len(closes)) if closes[i - 1] > 0]
                positive_days = sum(1 for c in day_changes if c > 0)
                consistency = positive_days / len(day_changes) if day_changes else 0.5

                # Composite momentum score (0-100)
                # Price component (40%), volume component (30%), consistency (30%)
                price_score = min(max(price_change + 5, 0), 10) * 4  # normalize to 0-40
                vol_score = min(max(vol_ratio - 0.5, 0), 1.5) * 20  # normalize to 0-30
                cons_score = consistency * 30  # 0-30
                momentum = round(price_score + vol_score + cons_score, 1)

                trend = "上升" if price_change > 1 and consistency > 0.6 else (
                    "下降" if price_change < -1 and consistency < 0.4 else "震荡"
                )

                sectors.append({
                    "name": name,
                    "nDayChange": round(price_change, 2),
                    "volumeRatio": round(vol_ratio, 2),
                    "consistency": round(consistency, 2),
                    "momentumScore": momentum,
                    "trend": trend,
                })
            except Exception:
                continue

    except Exception as e:
        errors.append(f"板块动量分析失败: {e}")

    sectors.sort(key=lambda s: s.get("momentumScore", 0), reverse=True)

    return {
        "action": "momentum",
        "days": days,
        "count": len(sectors),
        "sectors": sectors[:limit],
        "errors": errors if errors else None,
    }


def _hot_sectors(limit: int, errors: list[str]) -> dict:
    """Identify hot sectors (combining price, volume, and fund flow signals)."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    hot: list[dict] = []

    try:
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return {"error": "无法获取板块数据"}

        for _, row in df.iterrows():
            name = str(row.get("板块名称", ""))
            change_pct = _safe_float(row.get("涨跌幅"))
            turnover = _safe_float(row.get("换手率"))
            stock_count = int(row.get("上涨家数", 0) or 0)
            total_count = int(row.get("总家数", 0) or 0)

            if change_pct is None:
                continue

            # Hot criteria
            reasons = []
            strength = 0

            if change_pct and change_pct > 2:
                reasons.append(f"涨幅{change_pct:.1f}%")
                strength += 30

            if turnover and turnover > 3:
                reasons.append(f"换手率{turnover:.1f}%")
                strength += 20

            if total_count and total_count > 0:
                up_ratio = stock_count / total_count
                if up_ratio > 0.7:
                    reasons.append(f"上涨{stock_count}/{total_count}家")
                    strength += 25

            if change_pct and change_pct > 4:
                strength += 25

            if reasons and strength >= 30:
                hot.append({
                    "name": name,
                    "changePct": change_pct,
                    "turnoverRate": turnover,
                    "upCount": stock_count,
                    "totalCount": total_count,
                    "reasons": reasons,
                    "strength": min(strength, 100),
                })

    except Exception as e:
        errors.append(f"热点板块获取失败: {e}")

    hot.sort(key=lambda s: s.get("strength", 0), reverse=True)

    return {
        "action": "hot",
        "count": len(hot),
        "hotSectors": hot[:limit],
        "errors": errors if errors else None,
    }


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:
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

        result = run_sector_rotation(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
