"""
sector_rotation — Multi-period sector momentum ranking and rotation signal detection.

Computes 1-week, 4-week, and 12-week momentum for all industry sectors,
identifies sectors entering or exiting leadership, and checks portfolio
positions against sector strength.

Input (JSON via stdin / QUANT_CONFIG):
  {
    "action": "momentum" | "rotation_signal" | "portfolio_check",
    "limit": 20,
    "symbols": "600519,000858"
  }

Output: JSON with sector rotation data.
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


def run_sector_rotation(config: dict) -> dict:
    """Dispatch sector rotation actions."""
    action = config.get("action", "momentum")

    if action == "momentum":
        limit = config.get("limit", 20)
        return _sector_momentum(limit)
    elif action == "rotation_signal":
        limit = config.get("limit", 20)
        return _rotation_signal(limit)
    elif action == "portfolio_check":
        symbols = config.get("symbols", "").split(",")
        symbols = [s.strip() for s in symbols if s.strip()]
        return _portfolio_sector_check(symbols)
    else:
        return {"error": f"Unknown action: {action}"}


# ── Helper: fetch sector history ─────────────────────────────────────────────

def _get_sector_history() -> list[dict]:
    """Fetch sector index data with multi-period returns."""
    try:
        import akshare as ak
    except ImportError:
        return []

    sectors: list[dict] = []

    try:
        # Get today's sector ranking
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return []

        sector_names = df["板块名称"].tolist()[:60]  # Top 60 sectors

        today = datetime.now()
        end_date = today.strftime("%Y%m%d")

        for name in sector_names:
            try:
                hist = ak.stock_board_industry_hist_em(
                    symbol=name,
                    period="日k",
                    start_date=(today - timedelta(days=120)).strftime("%Y%m%d"),
                    end_date=end_date,
                    adjust="",
                )
                if hist is None or hist.empty or len(hist) < 5:
                    continue

                closes = hist["收盘"].tolist()
                current = closes[-1]

                # Compute multi-period returns
                change_1w = _pct_change(closes, 5)
                change_4w = _pct_change(closes, 20)
                change_12w = _pct_change(closes, 60)

                # Weighted momentum score: short-term weight=0.3, mid=0.4, long=0.3
                score = (
                    (change_1w or 0) * 0.3 +
                    (change_4w or 0) * 0.4 +
                    (change_12w or 0) * 0.3
                )

                # Determine trend
                if change_1w is not None and change_4w is not None:
                    if change_1w > 2 and change_4w > 5:
                        trend = "rising"
                    elif change_1w < -2 and change_4w < -5:
                        trend = "falling"
                    else:
                        trend = "stable"
                else:
                    trend = "unknown"

                # Get leader stock from today's ranking
                leader = ""
                try:
                    detail = ak.stock_board_industry_cons_em(symbol=name)
                    if detail is not None and not detail.empty:
                        # Sort by change pct descending
                        if "涨跌幅" in detail.columns:
                            detail = detail.sort_values("涨跌幅", ascending=False)
                        leader = str(detail.iloc[0].get("名称", "")) if len(detail) > 0 else ""
                except Exception:
                    pass

                sectors.append({
                    "name": name,
                    "current_price": round(current, 2),
                    "change_1w": round(change_1w, 2) if change_1w is not None else None,
                    "change_4w": round(change_4w, 2) if change_4w is not None else None,
                    "change_12w": round(change_12w, 2) if change_12w is not None else None,
                    "momentum_score": round(score, 2),
                    "trend": trend,
                    "leader_stock": leader,
                })

            except Exception:
                continue

    except Exception:
        pass

    return sectors


def _pct_change(closes: list[float], lookback: int) -> float | None:
    """Compute percentage change over lookback period."""
    if len(closes) <= lookback:
        return None
    old = closes[-(lookback + 1)]
    new = closes[-1]
    if old <= 0:
        return None
    return round((new - old) / old * 100, 2)


# ── Sector momentum ranking ─────────────────────────────────────────────────

def _sector_momentum(limit: int) -> dict:
    """Get multi-period sector momentum ranking."""
    sectors = _get_sector_history()
    if not sectors:
        return {"error": "无法获取板块数据", "sectors": []}

    # Sort by momentum score descending
    sectors.sort(key=lambda x: x.get("momentum_score", 0), reverse=True)

    return {"sectors": sectors[:limit]}


# ── Rotation signal detection ────────────────────────────────────────────────

def _rotation_signal(limit: int) -> dict:
    """Identify sectors entering or exiting leadership."""
    sectors = _get_sector_history()
    if not sectors:
        return {"error": "无法获取板块数据"}

    entering: list[dict] = []
    exiting: list[dict] = []

    for s in sectors:
        change_1w = s.get("change_1w")
        change_4w = s.get("change_4w")
        change_12w = s.get("change_12w")

        if change_1w is None or change_4w is None:
            continue

        # Entering leadership: short-term strong, improving relative to long-term
        if change_1w > 3 and (change_12w is None or change_1w > change_12w / 12 * 2):
            signal = "加速走强" if change_4w and change_4w > 5 else "新起动量"
            entering.append({
                "name": s["name"],
                "signal": signal,
                "short_momentum": change_1w,
                "long_momentum": change_12w if change_12w is not None else 0,
                "momentum_score": s.get("momentum_score", 0),
            })

        # Exiting leadership: was strong long-term but short-term weakening
        elif change_12w is not None and change_12w > 10 and change_1w < 0:
            signal = "动量衰减" if change_4w and change_4w > 0 else "趋势反转"
            exiting.append({
                "name": s["name"],
                "signal": signal,
                "short_momentum": change_1w,
                "long_momentum": change_12w,
                "momentum_score": s.get("momentum_score", 0),
            })

    # Sort by momentum
    entering.sort(key=lambda x: x.get("short_momentum", 0), reverse=True)
    exiting.sort(key=lambda x: x.get("short_momentum", 0))

    # Summary
    if entering and not exiting:
        summary = f"{len(entering)}个板块正在走强，市场轮动活跃"
    elif exiting and not entering:
        summary = f"{len(exiting)}个前期强势板块出现衰退信号，注意风格切换"
    elif entering and exiting:
        summary = f"板块轮动进行中: {len(entering)}个新强势板块, {len(exiting)}个衰退板块"
    else:
        summary = "当前无明显板块轮动信号"

    return {
        "entering_leadership": entering[:limit],
        "exiting_leadership": exiting[:limit],
        "summary": summary,
    }


# ── Portfolio sector check ───────────────────────────────────────────────────

def _portfolio_sector_check(symbols: list[str]) -> dict:
    """Check which sectors the user's holdings belong to and their strength."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    sectors = _get_sector_history()
    sector_map = {s["name"]: s for s in sectors}

    # Build a map of sector → rank
    sorted_sectors = sorted(sectors, key=lambda x: x.get("momentum_score", 0), reverse=True)
    sector_rank = {s["name"]: i + 1 for i, s in enumerate(sorted_sectors)}

    positions: list[dict] = []
    leading_count = 0
    lagging_count = 0
    neutral_count = 0

    for symbol in symbols:
        try:
            # Find which sector this stock belongs to
            stock_sector = None
            stock_name = symbol

            # Try to find the stock's sector
            try:
                individual = ak.stock_board_industry_name_em()
                if individual is not None and not individual.empty:
                    # Check each sector for this stock
                    for _, row in individual.iterrows():
                        sector_name = str(row.get("板块名称", ""))
                        try:
                            cons = ak.stock_board_industry_cons_em(symbol=sector_name)
                            if cons is not None and not cons.empty:
                                match = cons[cons["代码"] == symbol]
                                if not match.empty:
                                    stock_sector = sector_name
                                    stock_name = str(match.iloc[0].get("名称", symbol))
                                    break
                        except Exception:
                            continue
            except Exception:
                pass

            if stock_sector and stock_sector in sector_map:
                sector_info = sector_map[stock_sector]
                rank = sector_rank.get(stock_sector, 0)
                total = len(sorted_sectors)
                momentum = sector_info.get("momentum_score", 0)
                trend = sector_info.get("trend", "unknown")

                if rank <= total * 0.3:
                    suggestion = "所在板块强势，可继续持有"
                    leading_count += 1
                elif rank >= total * 0.7:
                    suggestion = "所在板块偏弱，注意风险"
                    lagging_count += 1
                else:
                    suggestion = "所在板块中性"
                    neutral_count += 1

                positions.append({
                    "symbol": symbol,
                    "name": stock_name,
                    "sector": stock_sector,
                    "sector_momentum": momentum,
                    "sector_trend": trend,
                    "sector_rank": rank,
                    "total_sectors": total,
                    "suggestion": suggestion,
                })
            else:
                neutral_count += 1
                positions.append({
                    "symbol": symbol,
                    "name": stock_name,
                    "sector": stock_sector or "未知",
                    "sector_momentum": 0,
                    "sector_trend": "unknown",
                    "sector_rank": 0,
                    "suggestion": "未能确定板块归属",
                })

        except Exception:
            positions.append({
                "symbol": symbol,
                "sector": "未知",
                "suggestion": "查询失败",
            })
            neutral_count += 1

    return {
        "positions": positions,
        "summary": {
            "leading_count": leading_count,
            "lagging_count": lagging_count,
            "neutral_count": neutral_count,
        },
    }


# ── Main entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    raw = os.environ.get("QUANT_CONFIG", "")
    if not raw:
        raw = sys.stdin.read()
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        cfg = {}

    try:
        result = run_sector_rotation(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"error": str(e), "traceback": traceback.format_exc()},
                         ensure_ascii=False))
