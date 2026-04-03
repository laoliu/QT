"""
market_sentiment — A-share market sentiment engine for short-term strategies.

Computes:
  - 连板梯队 (limit-up board ladder): highest board, count per level
  - N进N+1 晋级率 (promotion rate): how many N-boards become (N+1)-boards
  - 涨停/跌停比 (limit-up vs limit-down ratio)
  - 炸板率 (board failure rate)
  - 综合情绪评分 (composite sentiment score 0-100)

All data sourced from akshare public APIs (daily frequency).
"""

from __future__ import annotations

import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

from .lianban import (
    _safe_float,
    _safe_int,
    fetch_dt_pool,
    fetch_zt_pool,
    fetch_zt_pool_zbgc,
)


def lianban_ladder(date: str | None = None) -> dict[str, Any]:
    """Build the limit-up board ladder for a given date.

    Returns:
        {
          "date": "20260315",
          "total_zt": 60,        # total limit-up stocks
          "highest_board": 8,    # highest consecutive board count
          "ladder": {1: 40, 2: 12, 3: 5, 4: 2, 5: 1},  # count by level
          "stocks_by_level": {
            2: [{code, name, industry, ...}],
            3: [{...}],
          },
          "error": ...
        }
    """
    pool = fetch_zt_pool(date)
    if pool.get("error"):
        return {"error": pool["error"], "ladder": {}, "total_zt": 0, "highest_board": 0}

    stocks = pool.get("stocks", [])
    if not stocks:
        return {
            "date": date or datetime.now().strftime("%Y%m%d"),
            "total_zt": 0, "highest_board": 0, "ladder": {},
            "stocks_by_level": {},
        }

    ladder: dict[int, int] = {}
    stocks_by_level: dict[int, list[dict[str, Any]]] = {}
    highest = 0

    for s in stocks:
        n = max(_safe_int(s.get("lian_ban_count")), 1)
        ladder[n] = ladder.get(n, 0) + 1
        highest = max(highest, n)
        if n >= 2:
            stocks_by_level.setdefault(n, []).append(s)

    return {
        "date": pool.get("date", ""),
        "total_zt": len(stocks),
        "highest_board": highest,
        "ladder": dict(sorted(ladder.items())),
        "stocks_by_level": {k: v for k, v in sorted(stocks_by_level.items())},
    }


def promotion_rate(
    today_pool: list[dict[str, Any]],
    yesterday_pool: list[dict[str, Any]],
    from_n: int = 2,
) -> dict[str, Any]:
    """Compute promotion rate: how many N-board stocks became (N+1)-board.

    Args:
        today_pool: Today's limit-up pool stocks (with lian_ban_count).
        yesterday_pool: Yesterday's limit-up pool stocks.
        from_n: Count transitions from N to N+1 (default: 2→3).

    Returns:
        {
          "from_level": 2, "to_level": 3,
          "yesterday_count": 12,  # stocks at level N yesterday
          "promoted_count": 5,    # became N+1 today
          "failed_count": 7,
          "rate": 0.417,          # promotion rate
          "promoted": [{code, name}],
          "failed": [{code, name}],
        }
    """
    # Yesterday's stocks at board level `from_n`
    yesterday_at_n = {
        s["code"]: s for s in yesterday_pool
        if _safe_int(s.get("lian_ban_count")) == from_n
    }

    # Today's stocks at level from_n + 1
    today_at_n1 = {
        s["code"]: s for s in today_pool
        if _safe_int(s.get("lian_ban_count")) == from_n + 1
    }

    promoted = []
    failed = []
    for code, s in yesterday_at_n.items():
        if code in today_at_n1:
            promoted.append({"code": code, "name": s.get("name", "")})
        else:
            failed.append({"code": code, "name": s.get("name", "")})

    total = len(yesterday_at_n)
    return {
        "from_level": from_n,
        "to_level": from_n + 1,
        "yesterday_count": total,
        "promoted_count": len(promoted),
        "failed_count": len(failed),
        "rate": round(len(promoted) / total, 3) if total > 0 else 0,
        "promoted": promoted,
        "failed": failed,
    }


def zhangting_dieting_ratio(date: str | None = None) -> dict[str, Any]:
    """Compute limit-up vs limit-down ratio.

    Returns:
        {
          "zt_count": 60,
          "dt_count": 5,
          "ratio": 12.0,   # zt/dt (higher = more bullish)
          "tone": "强势",
        }
    """
    zt = fetch_zt_pool(date)
    dt = fetch_dt_pool(date)

    zt_count = len(zt.get("stocks", []))
    dt_count = len(dt.get("stocks", []))

    if dt_count > 0:
        ratio = round(zt_count / dt_count, 2)
    else:
        ratio = float(zt_count) if zt_count > 0 else 0

    if ratio >= 5:
        tone = "强势"
    elif ratio >= 2:
        tone = "偏多"
    elif ratio >= 1:
        tone = "中性"
    elif ratio >= 0.5:
        tone = "偏空"
    else:
        tone = "弱势"

    return {
        "zt_count": zt_count,
        "dt_count": dt_count,
        "ratio": ratio,
        "tone": tone,
    }


def zbgc_rate(date: str | None = None) -> dict[str, Any]:
    """Compute board failure (炸板) rate.

    Returns:
        {
          "zt_count": 60,       # successful limit-up
          "zbgc_count": 15,     # failed boards
          "total_attempts": 75,
          "failure_rate": 0.20,
          "tone": "正常",
        }
    """
    zt = fetch_zt_pool(date)
    zb = fetch_zt_pool_zbgc(date)

    zt_count = len(zt.get("stocks", []))
    zb_count = len(zb.get("stocks", []))
    total = zt_count + zb_count

    rate = round(zb_count / total, 3) if total > 0 else 0

    if rate >= 0.5:
        tone = "极差"
    elif rate >= 0.35:
        tone = "偏差"
    elif rate >= 0.2:
        tone = "正常"
    else:
        tone = "良好"

    return {
        "zt_count": zt_count,
        "zbgc_count": zb_count,
        "total_attempts": total,
        "failure_rate": rate,
        "tone": tone,
    }


def sentiment_score(date: str | None = None) -> dict[str, Any]:
    """Compute composite market sentiment score (0-100).

    Factors:
      - 涨停数量 (25%): more limit-ups = more bullish
      - 连板高度 (25%): higher max board = stronger trend
      - 涨跌停比 (25%): zt/dt ratio
      - 炸板率 (25%): lower failure rate = healthier environment

    Returns:
        {
          "score": 72.5,
          "level": "偏多",  # 极弱/偏空/中性/偏多/强势
          "factors": { ... },
          "suitable_for_relay": True,  # go/no-go for 二进三
          "detail": "...",
        }
    """
    ladder = lianban_ladder(date)
    zd_ratio = zhangting_dieting_ratio(date)
    failure = zbgc_rate(date)

    total_zt = ladder.get("total_zt", 0)
    highest = ladder.get("highest_board", 0)
    ratio = zd_ratio.get("ratio", 0)
    fail_rate = failure.get("failure_rate", 0)

    # Factor 1: ZT count score (25 pts)
    if total_zt >= 80:
        zt_score = 25
    elif total_zt >= 50:
        zt_score = 20
    elif total_zt >= 30:
        zt_score = 15
    elif total_zt >= 15:
        zt_score = 10
    else:
        zt_score = 5

    # Factor 2: Highest board score (25 pts)
    if highest >= 7:
        height_score = 25
    elif highest >= 5:
        height_score = 20
    elif highest >= 3:
        height_score = 15
    elif highest >= 2:
        height_score = 10
    else:
        height_score = 3

    # Factor 3: ZT/DT ratio score (25 pts)
    if ratio >= 5:
        ratio_score = 25
    elif ratio >= 3:
        ratio_score = 20
    elif ratio >= 2:
        ratio_score = 15
    elif ratio >= 1:
        ratio_score = 10
    else:
        ratio_score = 5

    # Factor 4: Failure rate score (25 pts, lower failure = higher score)
    if fail_rate <= 0.1:
        fail_score = 25
    elif fail_rate <= 0.2:
        fail_score = 20
    elif fail_rate <= 0.35:
        fail_score = 15
    elif fail_rate <= 0.5:
        fail_score = 10
    else:
        fail_score = 5

    total = zt_score + height_score + ratio_score + fail_score

    if total >= 80:
        level = "强势"
    elif total >= 60:
        level = "偏多"
    elif total >= 40:
        level = "中性"
    elif total >= 25:
        level = "偏空"
    else:
        level = "极弱"

    # Go / no-go for relay trading (二进三)
    # Requires: highest >= 3, not too many failures, decent zt count
    suitable = highest >= 3 and fail_rate < 0.5 and total_zt >= 15

    detail = (
        f"涨停{total_zt}家(最高{highest}板), "
        f"涨跌停比{ratio:.1f}, "
        f"炸板率{fail_rate*100:.0f}%, "
        f"情绪{level}"
    )

    return {
        "score": round(total, 1),
        "level": level,
        "suitable_for_relay": suitable,
        "total_zt": total_zt,
        "highest_board": highest,
        "factors": {
            "zt_count_score": zt_score,
            "height_score": height_score,
            "ratio_score": ratio_score,
            "failure_rate_score": fail_score,
        },
        "detail": detail,
        "ladder": ladder.get("ladder", {}),
    }
