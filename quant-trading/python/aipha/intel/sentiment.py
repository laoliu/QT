"""情感信号引擎 — 新闻可信度评分 + 内容分析 + 多维信号检测。

移植自 QT sentiment_signal_engine.py，适配 AlphQ intel 模块。
核心逻辑：信源可信度(4 tier) × 内容实质性(high/med/low) → 有效权重。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aipha.intel.models import (
    CredibilityTier,
    NewsItem,
    SentimentLabel,
    SentimentScore,
    SubstanceLevel,
)

logger = logging.getLogger(__name__)

# -- 信源可信度映射 --
_SOURCE_TIER: dict[str, CredibilityTier] = {}
_SOURCE_SCORE: dict[str, float] = {}

_TIER_1_SOURCES = [
    "上交所", "深交所", "巨潮资讯", "证监会", "央行",
    "CCTV", "新华社", "中国政府网", "国务院",
]
_TIER_2_SOURCES = [
    "东方财富", "同花顺", "万得", "Wind", "第一财经",
    "Bloomberg", "Reuters", "路透",
]
_TIER_3_SOURCES = [
    "新浪财经", "网易财经", "搜狐财经", "腾讯财经",
]
_TIER_4_SOURCES = [
    "股吧", "微博", "雪球", "论坛", "传闻",
]

for _s in _TIER_1_SOURCES:
    _SOURCE_TIER[_s] = CredibilityTier.TIER_1
    _SOURCE_SCORE[_s] = 0.95
for _s in _TIER_2_SOURCES:
    _SOURCE_TIER[_s] = CredibilityTier.TIER_2
    _SOURCE_SCORE[_s] = 0.78
for _s in _TIER_3_SOURCES:
    _SOURCE_TIER[_s] = CredibilityTier.TIER_3
    _SOURCE_SCORE[_s] = 0.6
for _s in _TIER_4_SOURCES:
    _SOURCE_TIER[_s] = CredibilityTier.TIER_4
    _SOURCE_SCORE[_s] = 0.25


def score_source_credibility(
    source: str, title: str = "", content: str = "",
) -> tuple[float, CredibilityTier]:
    """评估信源可信度。返回 (score, tier)。"""
    # 精确匹配
    for name, tier in _SOURCE_TIER.items():
        if name in source:
            return _SOURCE_SCORE[name], tier
    # 传闻/谣言降权
    rumor_kw = ["据传", "传闻", "小道消息", "未经证实", "消息人士"]
    text = title + content
    if any(kw in text for kw in rumor_kw):
        return 0.15, CredibilityTier.TIER_4
    return 0.5, CredibilityTier.TIER_3


# -- 内容实质性分析 --
_HIGH_SUBSTANCE_KW = [
    "净利润增长", "中标", "量产", "认证通过", "获批",
    "签约", "交付", "并购完成", "回购", "增持",
    "分红", "业绩预增", "营收增长", "订单",
]
_MEDIUM_SUBSTANCE_KW = [
    "战略合作", "研发突破", "产品发布", "备案", "试点",
    "框架协议", "合作意向", "产品上线",
]
_LOW_SUBSTANCE_KW = [
    "概念", "布局", "有望", "预计", "或将",
    "蹭热点", "关注", "题材",
]


def analyze_substance(title: str, content: str = "") -> SubstanceLevel:
    """分析新闻实质性。"""
    text = title + content
    for kw in _HIGH_SUBSTANCE_KW:
        if kw in text:
            return SubstanceLevel.HIGH
    for kw in _MEDIUM_SUBSTANCE_KW:
        if kw in text:
            return SubstanceLevel.MEDIUM
    for kw in _LOW_SUBSTANCE_KW:
        if kw in text:
            return SubstanceLevel.LOW
    return SubstanceLevel.MEDIUM


# -- 看多/看空关键词 --
_BULLISH_KW = [
    "利好", "涨停", "大涨", "突破", "新高",
    "增持", "回购", "业绩预增", "超预期",
    "获批", "中标", "量产", "签约",
    "政策支持", "降准", "降息", "放水",
]
_BEARISH_KW = [
    "利空", "跌停", "暴跌", "破位", "新低",
    "减持", "质押", "业绩预亏", "不达预期",
    "退市", "ST", "立案", "处罚", "违规",
    "加息", "收紧", "去杠杆",
]


def detect_signals(
    news_items: list[NewsItem],
) -> tuple[list[str], list[str]]:
    """从新闻中检测看多/看空信号。返回 (bullish, bearish)。"""
    bullish: list[str] = []
    bearish: list[str] = []
    for item in news_items:
        text = item.title + item.content
        for kw in _BULLISH_KW:
            if kw in text and kw not in bullish:
                bullish.append(kw)
        for kw in _BEARISH_KW:
            if kw in text and kw not in bearish:
                bearish.append(kw)
    return bullish, bearish


def compute_sentiment(
    news_items: list[NewsItem],
    symbol: str = "",
) -> SentimentScore:
    """综合分析新闻列表，生成情感评分。"""
    if not news_items:
        return SentimentScore(
            symbol=symbol,
            label=SentimentLabel.NEUTRAL,
            score=0.0,
            source="sentiment_engine",
        )

    total_weight = 0.0
    weighted_score = 0.0

    for item in news_items:
        cred_score, cred_tier = score_source_credibility(
            item.source, item.title, item.content,
        )
        substance = analyze_substance(item.title, item.content)
        substance_w = {
            SubstanceLevel.HIGH: 1.0,
            SubstanceLevel.MEDIUM: 0.6,
            SubstanceLevel.LOW: 0.2,
        }[substance]
        effective_w = cred_score * substance_w

        # 计算单篇情感分
        text = item.title + item.content
        bull_cnt = sum(1 for kw in _BULLISH_KW if kw in text)
        bear_cnt = sum(1 for kw in _BEARISH_KW if kw in text)
        if bull_cnt + bear_cnt == 0:
            item_score = 0.0
        else:
            item_score = (bull_cnt - bear_cnt) / (bull_cnt + bear_cnt)

        weighted_score += item_score * effective_w
        total_weight += effective_w

    final_score = weighted_score / total_weight if total_weight > 0 else 0.0
    final_score = max(-1.0, min(1.0, final_score))

    bullish_signals, bearish_signals = detect_signals(news_items)

    label = _score_to_label(final_score)

    avg_cred = sum(
        score_source_credibility(it.source)[0] for it in news_items
    ) / len(news_items)

    return SentimentScore(
        symbol=symbol,
        label=label,
        score=final_score,
        confidence=min(1.0, total_weight / max(len(news_items), 1)),
        source="sentiment_engine",
        bullish_signals=bullish_signals,
        bearish_signals=bearish_signals,
        credibility_score=avg_cred,
        substance_level=(
            SubstanceLevel.HIGH if abs(final_score) > 0.5
            else SubstanceLevel.MEDIUM if abs(final_score) > 0.2
            else SubstanceLevel.LOW
        ),
    )


def _score_to_label(score: float) -> SentimentLabel:
    if score >= 0.5:
        return SentimentLabel.STRONG_BULL
    if score >= 0.15:
        return SentimentLabel.BULL
    if score <= -0.5:
        return SentimentLabel.STRONG_BEAR
    if score <= -0.15:
        return SentimentLabel.BEAR
    return SentimentLabel.NEUTRAL
