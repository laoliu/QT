"""Intel 模块数据模型 — 新闻、研报、情感分析。

定义 intel 模块的核心数据结构，供 providers / sentiment / store / API 共用。
遵循 docs/design/20_specs/intel_advisory_qmt_architecture.md §2。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any


class IntelType(str, Enum):
    """情报类型。"""
    NEWS = "news"
    REPORT = "report"
    ANNOUNCEMENT = "announcement"
    EVENT = "event"


class SentimentLabel(str, Enum):
    """情感标签。"""
    STRONG_BULL = "strong_bull"
    BULL = "bull"
    NEUTRAL = "neutral"
    BEAR = "bear"
    STRONG_BEAR = "strong_bear"


class IntelProviderPriority(IntEnum):
    """Provider 优先级（数值越小优先级越高）。"""
    P0 = 0  # 财联社
    P1 = 1  # 新浪 7x24
    P2 = 2  # 东方财富
    P3 = 3  # TuShare
    P4 = 4  # QMT XtData
    P5 = 5  # Wind/Choice（预留）


class CredibilityTier(IntEnum):
    """信源可信度层级。"""
    TIER_1 = 1  # 官方: 交易所/证监会/央行/新华社/CCTV
    TIER_2 = 2  # 主流财经: 东财/同花顺/Wind/第一财经
    TIER_3 = 3  # 一般财经: 新浪/网易/搜狐
    TIER_4 = 4  # 社交/传闻: 股吧/微博/论坛


class SubstanceLevel(str, Enum):
    """新闻实质性分类。"""
    HIGH = "high"      # 真金白银: 净利润/中标/量产
    MEDIUM = "medium"  # 有进展: 合作/研发/产品
    LOW = "low"        # 概念炒作: 概念/布局/有望


@dataclass(frozen=True)
class NewsItem:
    """新闻条目。"""
    title: str
    source: str
    published_at: datetime
    content: str = ""
    url: str = ""
    intel_type: IntelType = IntelType.NEWS
    symbols: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    credibility_tier: CredibilityTier = CredibilityTier.TIER_3
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        """基于标题+来源的内容指纹（用于去重）。"""
        raw = f"{self.title}|{self.source}".encode("utf-8")
        return hashlib.md5(raw).hexdigest()  # noqa: S324


@dataclass(frozen=True)
class ResearchReport:
    """研究报告。"""
    title: str
    source: str
    published_at: datetime
    abstract: str = ""
    url: str = ""
    symbols: tuple[str, ...] = ()
    rating: str = ""  # 买入/增持/中性/减持/卖出
    target_price: float | None = None
    analyst: str = ""
    institution: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SentimentScore:
    """情感分析评分。"""
    symbol: str
    label: SentimentLabel
    score: float  # -1.0 ~ +1.0
    confidence: float = 0.0  # 0 ~ 1
    source: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
    bullish_signals: list[str] = field(default_factory=list)
    bearish_signals: list[str] = field(default_factory=list)
    credibility_score: float = 0.5
    substance_level: SubstanceLevel = SubstanceLevel.MEDIUM

    @property
    def effective_weight(self) -> float:
        """有效权重 = 可信度 × 实质性权重。"""
        substance_map = {
            SubstanceLevel.HIGH: 1.0,
            SubstanceLevel.MEDIUM: 0.6,
            SubstanceLevel.LOW: 0.2,
        }
        return self.credibility_score * substance_map.get(
            self.substance_level, 0.5
        )


@dataclass
class MarketSentimentSnapshot:
    """市场情绪快照。"""
    date: str  # YYYYMMDD
    zt_count: int = 0          # 涨停数
    dt_count: int = 0          # 跌停数
    highest_board: int = 0     # 最高板
    zt_dt_ratio: float = 0.0   # 涨停/跌停比
    failure_rate: float = 0.0  # 炸板率
    composite_score: float = 50.0  # 综合评分 0-100
    level: str = "neutral"     # 极弱/偏空/中性/偏多/强势
    suitable_for_relay: bool = False  # 适合接力打板

    @staticmethod
    def score_level(score: float) -> str:
        if score >= 80:
            return "strong_bull"
        if score >= 60:
            return "bull"
        if score >= 40:
            return "neutral"
        if score >= 25:
            return "bear"
        return "strong_bear"


@dataclass
class SocialHotStock:
    """社交热门股票。"""
    symbol: str
    name: str = ""
    hot_score: float = 0.0       # 0-100
    attention_index: float = 0.0
    participation_index: float = 0.0
    source: str = ""
    timestamp: datetime = field(default_factory=datetime.now)
