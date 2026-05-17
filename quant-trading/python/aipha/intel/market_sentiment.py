"""市场情绪引擎 — 涨停/跌停/连板/炸板分析。

移植自 QT market_sentiment.py + lianban.py，适配 AlphQ intel 模块。
核心功能：连板天梯、晋级率、涨跌停比、炸板率、综合情绪评分。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aipha.intel.models import MarketSentimentSnapshot

logger = logging.getLogger(__name__)


async def lianban_ladder(date: str = "") -> dict[str, Any]:
    """连板天梯 — 统计各板高度分布。

    Returns:
        {"highest_board": int, "ladder": {板数: [codes]}, "total_zt": int}
    """
    pool = await _fetch_zt_pool(date)
    if not pool:
        return {"highest_board": 0, "ladder": {}, "total_zt": 0}

    ladder: dict[int, list[str]] = {}
    for item in pool:
        board_count = int(item.get("连板数", item.get("lian_ban_count", 1)))
        code = str(item.get("代码", item.get("code", "")))
        ladder.setdefault(board_count, []).append(code)

    highest = max(ladder.keys()) if ladder else 0
    return {
        "highest_board": highest,
        "ladder": {k: v for k, v in sorted(ladder.items(), reverse=True)},
        "total_zt": len(pool),
    }


async def promotion_rate(
    today_pool: list[dict], yesterday_pool: list[dict],
    from_n: int = 2,
) -> float:
    """计算 N 板晋级率 — 昨日 N 板中今日成功 N+1 板的比例。"""
    yesterday_n = [
        str(p.get("代码", p.get("code", "")))
        for p in yesterday_pool
        if int(p.get("连板数", p.get("lian_ban_count", 0))) == from_n
    ]
    if not yesterday_n:
        return 0.0
    today_np1 = {
        str(p.get("代码", p.get("code", "")))
        for p in today_pool
        if int(p.get("连板数", p.get("lian_ban_count", 0))) >= from_n + 1
    }
    promoted = sum(1 for c in yesterday_n if c in today_np1)
    return promoted / len(yesterday_n)


async def sentiment_score(date: str = "") -> MarketSentimentSnapshot:
    """计算综合市场情绪评分（0-100）。

    组成: 涨停数(25%) + 最高板(25%) + 涨跌停比(25%) + 炸板率(25%)
    """
    if not date:
        date = datetime.now().strftime("%Y%m%d")

    zt_pool = await _fetch_zt_pool(date)
    dt_pool = await _fetch_dt_pool(date)
    zbgc_pool = await _fetch_zbgc_pool(date)

    zt_count = len(zt_pool)
    dt_count = len(dt_pool)
    zbgc_count = len(zbgc_pool)

    # 最高板
    highest = 0
    for item in zt_pool:
        bc = int(item.get("连板数", item.get("lian_ban_count", 1)))
        highest = max(highest, bc)

    # 涨跌停比
    zt_dt_ratio = zt_count / max(dt_count, 1)

    # 炸板率
    failure_rate = zbgc_count / max(zt_count + zbgc_count, 1)

    # 分项评分 (各 25 分满分)
    s1 = min(25, zt_count / 80 * 25)            # ≥80 涨停 → 满分
    s2 = min(25, highest / 7 * 25)               # ≥7 板 → 满分
    s3 = min(25, zt_dt_ratio / 5 * 25)           # ≥5 比 → 满分
    s4 = max(0, 25 * (1 - failure_rate / 0.3))   # ≤10% → 满分

    total = s1 + s2 + s3 + s4
    level = MarketSentimentSnapshot.score_level(total)

    return MarketSentimentSnapshot(
        date=date,
        zt_count=zt_count,
        dt_count=dt_count,
        highest_board=highest,
        zt_dt_ratio=round(zt_dt_ratio, 2),
        failure_rate=round(failure_rate, 3),
        composite_score=round(total, 1),
        level=level,
        suitable_for_relay=(total >= 60 and highest >= 3),
    )


# -- 内部函数 --

async def _fetch_zt_pool(date: str) -> list[dict[str, Any]]:
    """获取涨停池。"""
    try:
        import akshare as ak
        df = ak.stock_zt_pool_em(date=date)
        if df is None or df.empty:
            return []
        return df.to_dict("records")
    except Exception:
        logger.debug("fetch zt pool failed for %s", date)
        return []


async def _fetch_dt_pool(date: str) -> list[dict[str, Any]]:
    """获取跌停池。"""
    try:
        import akshare as ak
        df = ak.stock_zt_pool_dtgc_em(date=date)
        if df is None or df.empty:
            return []
        return df.to_dict("records")
    except Exception:
        logger.debug("fetch dt pool failed for %s", date)
        return []


async def _fetch_zbgc_pool(date: str) -> list[dict[str, Any]]:
    """获取炸板池。"""
    try:
        import akshare as ak
        df = ak.stock_zt_pool_zbgc_em(date=date)
        if df is None or df.empty:
            return []
        return df.to_dict("records")
    except Exception:
        logger.debug("fetch zbgc pool failed for %s", date)
        return []
