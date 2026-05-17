"""社交情绪分析 — 东财股吧/百度热搜/散户资金流。

移植自 QT social_sentiment.py，适配 AlphQ intel 模块。
核心功能：热门股票排行、个股社区情绪、散户资金流。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any

from aipha.intel.models import SocialHotStock

logger = logging.getLogger(__name__)

# 缓存：东财评论数据全市场表（5 分钟 TTL）
_comment_cache: tuple[float, Any] | None = None
_CACHE_TTL = 300

# 熔断集合：持续失败的 API 不再重试
_broken_apis: set[str] = set()


async def get_hot_stocks(
    market: str = "A", limit: int = 20,
) -> list[SocialHotStock]:
    """获取社交热门股票。"""
    items: list[SocialHotStock] = []
    # 尝试东财热度排行
    if "em_hot_rank" not in _broken_apis:
        try:
            items = await _em_hot_rank(limit)
            if items:
                return items
        except Exception:
            logger.debug("em_hot_rank failed, trying fallback")
            _broken_apis.add("em_hot_rank")

    # 回退到百度热搜
    if "baidu_hot" not in _broken_apis:
        try:
            items = await _baidu_hot(limit)
            if items:
                return items
        except Exception:
            logger.debug("baidu_hot failed")
            _broken_apis.add("baidu_hot")

    # 回退到东财评论数据
    try:
        items = await _em_comment_hot(limit)
    except Exception:
        logger.warning("all hot stock APIs failed")
    return items


async def get_stock_sentiment(symbol: str) -> dict[str, Any]:
    """获取个股社区情绪。"""
    code = symbol.split(".")[0]
    result: dict[str, Any] = {
        "symbol": symbol,
        "attention": 0.0,
        "participation": 0.0,
        "hot_score": 0.0,
        "level": "unknown",
    }
    try:
        df = await _get_comment_table()
        if df is not None and not df.empty:
            row = df[df["代码"] == code]
            if not row.empty:
                r = row.iloc[0]
                attn = float(r.get("关注指数", 0))
                part = float(r.get("参与指数", 0))
                hot = (attn + part) / 2 if (attn + part) > 0 else 0
                result.update({
                    "attention": attn,
                    "participation": part,
                    "hot_score": min(100, hot),
                    "level": _hot_level(hot),
                })
    except Exception:
        logger.debug("stock sentiment for %s failed", symbol)
    return result


def _hot_level(score: float) -> str:
    if score >= 70:
        return "high"  # 高热度 — 注意过热风险
    if score >= 40:
        return "medium"  # 中等
    return "low"  # 低热度 — 可能有预期差


async def _get_comment_table() -> Any:
    """获取东财评论表（带缓存）。"""
    global _comment_cache
    now = time.time()
    if _comment_cache and (now - _comment_cache[0]) < _CACHE_TTL:
        return _comment_cache[1]
    try:
        import akshare as ak
        df = ak.stock_comment_em()
        _comment_cache = (now, df)
        return df
    except Exception:
        logger.debug("stock_comment_em failed")
        return None


async def _em_hot_rank(limit: int) -> list[SocialHotStock]:
    """东财热度排行。"""
    import akshare as ak
    df = ak.stock_hot_rank_detail_em()
    if df is None or df.empty:
        return []
    items: list[SocialHotStock] = []
    for _, row in df.head(limit).iterrows():
        items.append(SocialHotStock(
            symbol=str(row.get("代码", "")),
            name=str(row.get("股票名称", "")),
            hot_score=float(row.get("热度", 0)),
            source="eastmoney_hot_rank",
        ))
    return items


async def _baidu_hot(limit: int) -> list[SocialHotStock]:
    """百度股市热搜。"""
    import akshare as ak
    df = ak.stock_hot_search_baidu(symbol="A股")
    if df is None or df.empty:
        return []
    items: list[SocialHotStock] = []
    for _, row in df.head(limit).iterrows():
        items.append(SocialHotStock(
            symbol=str(row.get("代码", "")),
            name=str(row.get("股票名称", row.get("关键词", ""))),
            hot_score=float(row.get("热搜指数", 0)),
            source="baidu_hot_search",
        ))
    return items


async def _em_comment_hot(limit: int) -> list[SocialHotStock]:
    """东财评论数据（回退方案）。"""
    df = await _get_comment_table()
    if df is None or df.empty:
        return []
    # 按关注指数排序
    col = "关注指数" if "关注指数" in df.columns else df.columns[2]
    df_sorted = df.sort_values(col, ascending=False)
    items: list[SocialHotStock] = []
    for _, row in df_sorted.head(limit).iterrows():
        items.append(SocialHotStock(
            symbol=str(row.get("代码", "")),
            name=str(row.get("名称", "")),
            attention_index=float(row.get("关注指数", 0)),
            participation_index=float(row.get("参与指数", 0)),
            hot_score=float(row.get("关注指数", 0)),
            source="eastmoney_comment",
        ))
    return items
