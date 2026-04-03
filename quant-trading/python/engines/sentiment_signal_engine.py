# coding: utf-8
"""
Sentiment Signal Engine — multi-dimensional bullish/bearish signal analysis.

Implements the framework:
  1. News source credibility (官方公告 > 权威媒体 > 小道消息)
  2. Substantive content (real orders/revenue vs concept hype)
  3. Market position context (low position + good news = reversal opportunity;
     high position + good news = possible trap / "利好出尽是利空")
  4. Macro/company/market-level bullish & bearish signal detection
  5. Tech frontier tracking (自主可控, CPO, 液冷, 硅光, etc.)

Input (JSON via stdin):
  {
    "action": "analyze" | "scan_signals" | "check_position" | "tech_frontier",
    "symbol": "600519",
    "symbols": ["600519", "002281"],
    "news": [{"title": "...", "source": "...", "content": "..."}],
    "limit": 20
  }

Output: JSON with signal analysis results.
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
from datetime import datetime
from typing import Any

# ── Proxy bypass ──
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

# ── Data directory ──
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


# ═══════════════════════════════════════════════════════════════════
# 1. NEWS SOURCE CREDIBILITY SCORING
# ═══════════════════════════════════════════════════════════════════

# Credibility tiers: score 0-1, higher = more authoritative
_SOURCE_CREDIBILITY: dict[str, float] = {
    # Tier 1: Official announcements (0.9-1.0)
    "上交所": 1.0, "深交所": 1.0, "巨潮资讯": 1.0, "上海证券交易所": 1.0,
    "深圳证券交易所": 1.0, "证监会": 1.0, "央行": 1.0, "国务院": 1.0,
    "交易所公告": 1.0, "公司公告": 0.95, "SEC": 1.0, "CSRC": 1.0,
    "CCTV": 0.90, "新华社": 0.90, "人民日报": 0.90,

    # Tier 2: Authoritative financial media (0.7-0.85)
    "东方财富": 0.80, "同花顺": 0.75, "万得": 0.85, "Wind": 0.85,
    "证券时报": 0.80, "中国证券报": 0.80, "上海证券报": 0.80,
    "第一财经": 0.78, "财新": 0.82, "经济观察报": 0.75,
    "Reuters": 0.85, "Bloomberg": 0.85, "CNBC": 0.80,
    "Wall Street Journal": 0.85, "WSJ": 0.85,
    "Financial Times": 0.83, "FT": 0.83,

    # Tier 3: General financial media (0.5-0.7)
    "新浪财经": 0.65, "网易财经": 0.60, "搜狐财经": 0.55,
    "金融界": 0.60, "和讯网": 0.55, "雪球": 0.55,
    "Global": 0.60,  # generic tag from news_engine

    # Tier 4: Social/rumor sources (0.1-0.4)
    "股吧": 0.20, "论坛": 0.15, "贴吧": 0.10, "微博": 0.30,
    "小道消息": 0.10, "传闻": 0.10,
}

# Keywords that indicate official/authoritative source within content
_OFFICIAL_KEYWORDS = [
    "公告", "公司公告", "交易所", "证监会", "央行", "国务院",
    "年报", "季报", "业绩预告", "业绩快报", "中标公告",
    "SEC filing", "8-K", "10-Q", "10-K", "earnings report",
]

# Keywords that indicate rumor/speculation
_RUMOR_KEYWORDS = [
    "据传", "传闻", "消息称", "知情人士", "市场传言", "坊间",
    "据悉", "或将", "有望", "可能", "预计", "据说",
    "rumor", "reportedly", "allegedly", "sources say",
]


def _score_source_credibility(source: str, title: str = "", content: str = "") -> dict:
    """Score the credibility of a news item based on source and content.

    Returns: {score: 0-1, tier: str, reason: str}
    """
    text = (title + " " + content).lower()
    source_lower = source.lower() if source else ""

    # Try to match source name
    best_score = 0.5  # default: unknown source
    matched_source = "未知来源"
    for name, score in _SOURCE_CREDIBILITY.items():
        if name.lower() in source_lower or name.lower() in text[:100]:
            if score > best_score:
                best_score = score
                matched_source = name

    # Boost for official keywords in content
    official_hits = sum(1 for kw in _OFFICIAL_KEYWORDS if kw in text)
    if official_hits >= 2:
        best_score = min(best_score + 0.1, 1.0)

    # Penalize for rumor keywords
    rumor_hits = sum(1 for kw in _RUMOR_KEYWORDS if kw in text)
    if rumor_hits >= 2:
        best_score = max(best_score - 0.2, 0.1)
    elif rumor_hits == 1:
        best_score = max(best_score - 0.1, 0.1)

    # Tier label
    if best_score >= 0.85:
        tier = "官方权威"
    elif best_score >= 0.7:
        tier = "权威媒体"
    elif best_score >= 0.5:
        tier = "一般媒体"
    elif best_score >= 0.3:
        tier = "社交媒体"
    else:
        tier = "传闻/小道消息"

    return {
        "score": round(best_score, 2),
        "tier": tier,
        "matchedSource": matched_source,
        "officialKeywords": official_hits,
        "rumorKeywords": rumor_hits,
    }


# ═══════════════════════════════════════════════════════════════════
# 2. SUBSTANTIVE CONTENT ANALYSIS
# ═══════════════════════════════════════════════════════════════════

# Real substance: involves actual revenue, orders, earnings
_SUBSTANCE_KEYWORDS = {
    "high": {
        "keywords": [
            "净利润增长", "营收增长", "业绩大增", "超预期", "中标",
            "签订合同", "获得订单", "量产", "投产", "产能扩张",
            "认证通过", "进入供应链", "批量出货", "收购完成",
            "回购", "增持", "分红", "送转", "样品验证通过",
            "小批量产", "大批量出货", "独家供应", "唯一供应商",
            "revenue growth", "earnings beat", "contract awarded",
            "mass production", "certification", "supply chain",
        ],
        "label": "真金白银",
        "weight": 1.0,
    },
    "medium": {
        "keywords": [
            "战略合作", "框架协议", "签署意向", "联合研发",
            "研发突破", "技术验证", "送样成功", "流片成功",
            "获批", "备案", "许可证", "产品发布",
            "partnership", "MOU", "R&D breakthrough",
            "product launch", "approval",
        ],
        "label": "有进展",
        "weight": 0.6,
    },
    "low": {
        "keywords": [
            "概念", "题材", "布局", "有望", "或将", "计划",
            "意向性", "探索", "研究", "关注", "涉足",
            "concept", "exploring", "planning", "intend",
        ],
        "label": "概念炒作",
        "weight": 0.2,
    },
}


def _analyze_substance(title: str, content: str = "") -> dict:
    """Analyze whether news involves real substance or just concept hype.

    Returns: {level: str, label: str, weight: float, hits: list}
    """
    text = (title + " " + content).lower()
    hits: list[str] = []

    for level in ("high", "medium", "low"):
        info = _SUBSTANCE_KEYWORDS[level]
        matched = [kw for kw in info["keywords"] if kw.lower() in text]
        if matched:
            return {
                "level": level,
                "label": info["label"],
                "weight": info["weight"],
                "hits": matched[:3],
            }

    return {"level": "unknown", "label": "信息不足", "weight": 0.3, "hits": []}


# ═══════════════════════════════════════════════════════════════════
# 3. MARKET POSITION CONTEXT
# ═══════════════════════════════════════════════════════════════════

def _check_position_context(code: str) -> dict:
    """Analyze stock's current market position for good/bad news interpretation.

    Key insight:
    - Low position + good news → likely reversal ("利空出尽是利好")
    - High position + good news → may be trap ("利好出尽是利空 / 诱多")
    - Low position + bad news → may be capitulation/bottom
    - High position + bad news → likely beginning of downtrend

    Returns position analysis with interpretation signals.
    """
    result: dict[str, Any] = {
        "code": code,
        "position": "unknown",  # low / mid / high
        "relativePosition": 0.0,  # 0-1 within 60d range
        "ma_alignment": "unknown",
        "interpretation": {},
    }

    try:
        import akshare as ak
        mkt = "sh" if code.startswith("6") else "sz"
        full_code = f"{mkt}{code}"

        df = ak.stock_zh_a_hist(symbol=code, period="daily",
                                start_date=(datetime.now().replace(day=1) - __import__("datetime").timedelta(days=90)).strftime("%Y%m%d"),
                                adjust="qfq")
        if df is None or df.empty or len(df) < 20:
            return result

        closes = [float(x) for x in df["收盘"].tolist()]
        highs = [float(x) for x in df["最高"].tolist()]
        lows = [float(x) for x in df["最低"].tolist()]
        volumes = [float(x) for x in df["成交量"].tolist()]

        current = closes[-1]
        high_60d = max(highs[-60:]) if len(highs) >= 60 else max(highs)
        low_60d = min(lows[-60:]) if len(lows) >= 60 else min(lows)
        range_60d = high_60d - low_60d

        # Relative position within 60-day range (0 = at bottom, 1 = at top)
        if range_60d > 0:
            rel_pos = (current - low_60d) / range_60d
        else:
            rel_pos = 0.5

        # MA alignment
        ma5 = sum(closes[-5:]) / min(len(closes), 5)
        ma10 = sum(closes[-10:]) / min(len(closes), 10)
        ma20 = sum(closes[-20:]) / min(len(closes), 20)
        ma60 = sum(closes[-60:]) / min(len(closes), 60)

        if current > ma5 > ma10 > ma20:
            ma_align = "多头排列"
        elif current < ma5 < ma10 < ma20:
            ma_align = "空头排列"
        else:
            ma_align = "震荡"

        # Volume trend (recent 5d vs previous 20d)
        vol_recent = sum(volumes[-5:]) / max(len(volumes[-5:]), 1)
        vol_prev = sum(volumes[-25:-5]) / max(len(volumes[-25:-5]), 1) if len(volumes) > 25 else vol_recent
        vol_ratio = vol_recent / vol_prev if vol_prev > 0 else 1.0

        # Distance from 60-day high
        drop_from_high = (high_60d - current) / high_60d * 100 if high_60d > 0 else 0

        # Position classification
        if rel_pos <= 0.25:
            position = "low"
            position_label = "低位"
        elif rel_pos >= 0.75:
            position = "high"
            position_label = "高位"
        else:
            position = "mid"
            position_label = "中位"

        # Build interpretation for different news scenarios
        interpretation = {
            "bullish_news": _interpret_news_at_position(position, "bullish", ma_align, vol_ratio, drop_from_high),
            "bearish_news": _interpret_news_at_position(position, "bearish", ma_align, vol_ratio, drop_from_high),
        }

        result.update({
            "position": position,
            "positionLabel": position_label,
            "relativePosition": round(rel_pos, 3),
            "currentPrice": current,
            "high60d": high_60d,
            "low60d": low_60d,
            "dropFromHigh": round(drop_from_high, 2),
            "ma_alignment": ma_align,
            "volumeRatio": round(vol_ratio, 2),
            "ma5": round(ma5, 2),
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
            "interpretation": interpretation,
        })

    except Exception as e:
        result["error"] = str(e)

    return result


def _interpret_news_at_position(
    position: str, news_type: str,
    ma_align: str, vol_ratio: float, drop_from_high: float,
) -> dict:
    """Interpret how news should be read given the stock's position.

    Implements: 利好出尽是利空, 利空出尽是利好, 预期差决定力度
    """
    if news_type == "bullish":
        if position == "high":
            # High position + good news = caution
            signal = "caution"
            label = "高位利好 ⚠️ 诱多风险"
            advice = "高位出利好可能是主力借机出货，关注量价背离"
            score_adj = -0.3  # reduce bullish signal
            if ma_align == "空头排列":
                label = "高位利好 + 空头排列 ⚠️ 反弹出货"
                score_adj = -0.5
        elif position == "low":
            # Low position + good news = potential reversal
            signal = "opportunity"
            label = "低位利好 ✅ 反转信号"
            advice = "低位出利好是反转信号，尤其关注缩量筑底后放量"
            score_adj = 0.5
            if drop_from_high > 30:
                label = "深跌后利好 ✅ 强反转信号"
                score_adj = 0.7
        else:
            signal = "neutral"
            label = "中位利好 → 关注突破方向"
            advice = "中位利好需关注能否突破前高，量能配合是关键"
            score_adj = 0.1
    else:  # bearish
        if position == "low":
            # Low position + bad news = potential bottom ("利空出尽")
            signal = "opportunity"
            label = "低位利空 ✅ 利空出尽"
            advice = "股价已在低位，利空落地反而可能是底部信号"
            score_adj = 0.3
            if vol_ratio < 0.7:
                label = "低位利空 + 缩量 ✅ 底部特征"
                score_adj = 0.5
        elif position == "high":
            # High position + bad news = danger
            signal = "danger"
            label = "高位利空 ❌ 下跌初期"
            advice = "高位出利空往往是下跌的开始，规避或减仓"
            score_adj = -0.7
            if ma_align == "空头排列":
                label = "高位利空 + 空头排列 ❌❌ 趋势反转"
                score_adj = -1.0
        else:
            signal = "neutral"
            label = "中位利空 → 关注支撑位"
            advice = "中位利空需观察是否跌破关键均线支撑"
            score_adj = -0.2

    return {
        "signal": signal,
        "label": label,
        "advice": advice,
        "scoreAdj": score_adj,
    }


# ═══════════════════════════════════════════════════════════════════
# 4. BULLISH / BEARISH SIGNAL DETECTION
# ═══════════════════════════════════════════════════════════════════

_BULLISH_SIGNALS = {
    "macro": {
        "label": "宏观利好",
        "keywords": [
            "降准", "降息", "逆回购", "MLF", "LPR下调", "宽松",
            "刺激政策", "产业政策", "新能源", "高端制造", "乡村振兴",
            "GDP超预期", "PMI扩张", "CPI温和", "经济复苏",
            "人民币升值", "外资流入",
        ],
    },
    "company": {
        "label": "公司利好",
        "keywords": [
            "业绩大增", "净利润增长", "超预期", "中标", "大订单",
            "并购", "重组", "资产注入", "技术突破", "专利",
            "量产", "流片成功", "认证通过", "进入供应链",
            "回购", "增持", "高管买入", "分红", "送转",
            "自主可控", "国产替代", "唯一供应商",
        ],
    },
    "capital": {
        "label": "资金利好",
        "keywords": [
            "北向资金", "外资净买入", "MSCI", "富时罗素",
            "社保基金", "险资入市", "公募加仓", "融资余额增加",
            "大宗交易溢价", "龙虎榜买入",
        ],
    },
}

_BEARISH_SIGNALS = {
    "macro": {
        "label": "宏观利空",
        "keywords": [
            "加息", "提高准备金", "收紧", "监管加强", "反垄断",
            "贸易战", "关税", "地缘紧张", "油价飙升",
            "衰退", "失业率上升", "消费低迷",
        ],
    },
    "company": {
        "label": "公司利空",
        "keywords": [
            "业绩暴雷", "亏损", "利润下滑", "财务造假",
            "高管辞职", "被调查", "立案", "违规",
            "解禁", "限售股", "大股东减持", "清仓式减持",
            "诉讼", "巨额赔偿", "行政处罚",
            "技术淘汰", "产品迭代", "设备更新淘汰",
        ],
    },
    "sentiment": {
        "label": "情绪利空",
        "keywords": [
            "美股大跌", "中概股暴跌", "港股大跌",
            "原材料暴涨", "成本压力", "利润挤压",
            "恐慌", "踩踏", "闪崩",
        ],
    },
}


def _detect_signals(news_items: list[dict]) -> dict:
    """Detect bullish and bearish signals from a batch of news.

    Returns categorized signal analysis.
    """
    bullish: list[dict] = []
    bearish: list[dict] = []

    for news in news_items:
        text = (news.get("title", "") + " " + news.get("content", "")).lower()
        source = news.get("source", "")
        cred = _score_source_credibility(source, news.get("title", ""), news.get("content", ""))

        # Check bullish signals
        for category, info in _BULLISH_SIGNALS.items():
            matched = [kw for kw in info["keywords"] if kw.lower() in text]
            if matched:
                substance = _analyze_substance(news.get("title", ""), news.get("content", ""))
                bullish.append({
                    "title": news.get("title", ""),
                    "source": source,
                    "time": news.get("time", ""),
                    "link": news.get("link", ""),
                    "category": category,
                    "categoryLabel": info["label"],
                    "matchedKeywords": matched[:3],
                    "credibility": cred,
                    "substance": substance,
                    # Effective weight = credibility × substance weight
                    "effectiveWeight": round(
                        cred["score"] * substance["weight"], 3
                    ),
                })

        # Check bearish signals
        for category, info in _BEARISH_SIGNALS.items():
            matched = [kw for kw in info["keywords"] if kw.lower() in text]
            if matched:
                bearish.append({
                    "title": news.get("title", ""),
                    "source": source,
                    "time": news.get("time", ""),
                    "link": news.get("link", ""),
                    "category": category,
                    "categoryLabel": info["label"],
                    "matchedKeywords": matched[:3],
                    "credibility": cred,
                    "effectiveWeight": round(cred["score"], 3),
                })

    # Compute aggregate scores
    bull_score = sum(s["effectiveWeight"] for s in bullish)
    bear_score = sum(s["effectiveWeight"] for s in bearish)
    net = bull_score - bear_score
    total = bull_score + bear_score

    if total > 0:
        normalized = net / total  # -1 to 1
    else:
        normalized = 0.0

    if normalized > 0.4:
        verdict = "强烈利好"
    elif normalized > 0.15:
        verdict = "偏利好"
    elif normalized > -0.15:
        verdict = "中性"
    elif normalized > -0.4:
        verdict = "偏利空"
    else:
        verdict = "强烈利空"

    return {
        "verdict": verdict,
        "normalizedScore": round(normalized, 3),
        "bullishScore": round(bull_score, 3),
        "bearishScore": round(bear_score, 3),
        "bullishCount": len(bullish),
        "bearishCount": len(bearish),
        "bullishSignals": sorted(bullish, key=lambda x: -x["effectiveWeight"])[:10],
        "bearishSignals": sorted(bearish, key=lambda x: -x["effectiveWeight"])[:10],
    }


# ═══════════════════════════════════════════════════════════════════
# 5. TECH FRONTIER TRACKING
# ═══════════════════════════════════════════════════════════════════

_TECH_FRONTIERS = {
    "硅光技术": {
        "keywords": ["硅光", "silicon photonics", "光电融合", "photonic",
                     "光计算", "光芯片", "光互连"],
        "a_shares": ["002281", "300308", "300502"],  # 光迅、中际旭创、新易盛
        "catalysts": ["英伟达NVLink互连", "800G/1.6T光模块需求", "AI集群互连"],
    },
    "CPO封装": {
        "keywords": ["CPO", "co-packaged optics", "共封装光学", "光电共封装",
                     "on-chip photonics"],
        "a_shares": ["002281", "300308", "300502", "002156"],
        "catalysts": ["数据中心带宽瓶颈", "功耗降低50%", "3.2T光模块"],
    },
    "液冷散热": {
        "keywords": ["液冷", "liquid cooling", "冷板式", "浸没式", "精密温控",
                     "数据中心散热", "AI算力散热"],
        "a_shares": ["002837", "603019"],  # 英维克、中科曙光
        "catalysts": ["AI算力密度提升", "风冷无法满足", "PUE要求"],
    },
    "自主可控材料": {
        "keywords": ["自主可控", "国产替代", "新材料", "唯一替代", "进入认证",
                     "供应链安全", "卡脖子", "去美化"],
        "a_shares": ["300236", "300346", "688012", "002371"],
        "catalysts": ["制裁倒逼", "供应链安全", "政策补贴"],
    },
    "先进封装": {
        "keywords": ["先进封装", "Chiplet", "2.5D封装", "3D封装", "CoWoS",
                     "HBM封装", "TSV", "扇出封装"],
        "a_shares": ["002156", "688012"],  # 通富微电、中微公司
        "catalysts": ["AI芯片封装需求", "HBM产能瓶颈", "NVIDIA Blackwell"],
    },
    "HBM高带宽内存": {
        "keywords": ["HBM", "高带宽内存", "HBM3", "HBM3e", "HBM4",
                     "high bandwidth memory"],
        "a_shares": ["688008"],  # 澜起科技
        "catalysts": ["AI GPU配套", "SK海力士/三星产能", "NVIDIA供应链"],
    },
    "人形机器人": {
        "keywords": ["人形机器人", "humanoid", "optimus", "figure", "特斯拉机器人",
                     "具身智能", "运动控制", "机器人关节"],
        "a_shares": ["002527", "002475"],  # 新时达、立讯精密
        "catalysts": ["特斯拉Optimus进展", "量产预期", "核心零部件国产化"],
    },
    "卫星互联网": {
        "keywords": ["卫星互联网", "星链", "Starlink", "低轨卫星", "LEO",
                     "卫星通信", "天地一体化"],
        "a_shares": ["000063"],  # 中兴通讯
        "catalysts": ["6G预研", "国家卫星组网", "手机直连卫星"],
    },
}


def _scan_tech_frontier(news_items: list[dict] | None = None) -> dict:
    """Scan news for tech frontier mentions and map to A-share targets.

    If no news provided, returns the static frontier database.
    """
    frontiers_detected: list[dict] = []

    if news_items:
        for frontier_name, info in _TECH_FRONTIERS.items():
            for news in news_items:
                text = (news.get("title", "") + " " + news.get("content", "")).lower()
                matched = [kw for kw in info["keywords"] if kw.lower() in text]
                if matched:
                    cred = _score_source_credibility(
                        news.get("source", ""), news.get("title", ""), news.get("content", ""),
                    )
                    substance = _analyze_substance(news.get("title", ""), news.get("content", ""))
                    frontiers_detected.append({
                        "frontier": frontier_name,
                        "newsTitle": news.get("title", ""),
                        "source": news.get("source", ""),
                        "matchedKeywords": matched[:3],
                        "credibility": cred,
                        "substance": substance,
                        "aShares": info["a_shares"],
                        "catalysts": info["catalysts"],
                    })

    # Always return full frontier database
    frontier_db = []
    for name, info in _TECH_FRONTIERS.items():
        frontier_db.append({
            "name": name,
            "keywords": info["keywords"][:5],
            "aShares": info["a_shares"],
            "catalysts": info["catalysts"],
        })

    return {
        "action": "tech_frontier",
        "detected": frontiers_detected,
        "detectedCount": len(frontiers_detected),
        "database": frontier_db,
        "frontierCount": len(frontier_db),
    }


# ═══════════════════════════════════════════════════════════════════
# 6. COMPOSITE ANALYZE ACTION
# ═══════════════════════════════════════════════════════════════════

def _analyze(config: dict) -> dict:
    """Full multi-dimensional analysis for a stock or news batch.

    Combines: credibility + substance + position + signals + tech frontier
    """
    symbol = config.get("symbol", "")
    news_items = config.get("news", [])

    # If symbol provided but no news, fetch recent news
    if symbol and not news_items:
        try:
            import akshare as ak
            df = ak.stock_news_em(symbol=symbol)
            if df is not None and not df.empty:
                for _, row in df.head(15).iterrows():
                    news_items.append({
                        "title": str(row.get("新闻标题", "")),
                        "content": str(row.get("新闻内容", ""))[:300],
                        "source": str(row.get("文章来源", "")),
                        "time": str(row.get("发布时间", "")),
                        "link": str(row.get("新闻链接", "")),
                    })
        except Exception:
            pass

    # 1. Score credibility for each news item
    scored_news = []
    for n in news_items:
        cred = _score_source_credibility(n.get("source", ""), n.get("title", ""), n.get("content", ""))
        substance = _analyze_substance(n.get("title", ""), n.get("content", ""))
        scored_news.append({
            "title": n.get("title", ""),
            "source": n.get("source", ""),
            "time": n.get("time", ""),
            "link": n.get("link", ""),
            "credibility": cred,
            "substance": substance,
        })

    # 2. Detect bullish/bearish signals
    signals = _detect_signals(news_items)

    # 3. Check market position (if symbol provided)
    position = None
    if symbol:
        position = _check_position_context(symbol)

    # 4. Tech frontier scan
    tech = _scan_tech_frontier(news_items)

    # 5. Composite verdict
    # Adjust signal score based on position context
    composite_score = signals["normalizedScore"]
    position_warning = None

    if position and position.get("position") != "unknown":
        pos = position["position"]
        interp = position.get("interpretation", {})

        if composite_score > 0 and pos == "high":
            # Bullish signal at high position: reduce confidence
            adj = interp.get("bullish_news", {}).get("scoreAdj", 0)
            composite_score += adj
            position_warning = interp.get("bullish_news", {}).get("label", "")
        elif composite_score < 0 and pos == "low":
            # Bearish signal at low position: may be "利空出尽"
            adj = interp.get("bearish_news", {}).get("scoreAdj", 0)
            composite_score += adj
            position_warning = interp.get("bearish_news", {}).get("label", "")
        elif composite_score > 0 and pos == "low":
            adj = interp.get("bullish_news", {}).get("scoreAdj", 0)
            composite_score += adj
            position_warning = interp.get("bullish_news", {}).get("label", "")
        elif composite_score < 0 and pos == "high":
            adj = interp.get("bearish_news", {}).get("scoreAdj", 0)
            composite_score += adj
            position_warning = interp.get("bearish_news", {}).get("label", "")

    # Clamp to [-1, 1]
    composite_score = max(-1.0, min(1.0, composite_score))

    if composite_score > 0.4:
        final_verdict = "强烈看多"
    elif composite_score > 0.15:
        final_verdict = "偏多"
    elif composite_score > -0.15:
        final_verdict = "中性"
    elif composite_score > -0.4:
        final_verdict = "偏空"
    else:
        final_verdict = "强烈看空"

    return {
        "action": "analyze",
        "symbol": symbol,
        "date": datetime.now().strftime("%Y-%m-%d"),
        "compositeScore": round(composite_score, 3),
        "verdict": final_verdict,
        "positionWarning": position_warning,
        "signals": signals,
        "position": position,
        "techFrontier": tech,
        "scoredNews": sorted(scored_news, key=lambda x: -x["credibility"]["score"])[:10],
        "newsCount": len(news_items),
    }


def _scan_signals(config: dict) -> dict:
    """Scan signals for multiple stocks and rank by composite score."""
    symbols = config.get("symbols", [])
    limit = config.get("limit", 20)

    results: list[dict] = []
    for sym in symbols[:limit]:
        analysis = _analyze({"symbol": sym})
        results.append({
            "symbol": sym,
            "compositeScore": analysis.get("compositeScore", 0),
            "verdict": analysis.get("verdict", "中性"),
            "positionWarning": analysis.get("positionWarning"),
            "bullishCount": analysis.get("signals", {}).get("bullishCount", 0),
            "bearishCount": analysis.get("signals", {}).get("bearishCount", 0),
            "position": analysis.get("position", {}).get("positionLabel", ""),
            "newsCount": analysis.get("newsCount", 0),
        })

    results.sort(key=lambda x: -x["compositeScore"])

    return {
        "action": "scan_signals",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "results": results,
        "count": len(results),
    }


# ── Main ──

ACTIONS = {
    "analyze": _analyze,
    "scan_signals": _scan_signals,
    "check_position": lambda c: _check_position_context(c.get("symbol", "")),
    "tech_frontier": lambda c: _scan_tech_frontier(c.get("news")),
}


def main():
    config = json.loads(sys.stdin.read())
    action = config.get("action", "analyze")

    handler = ACTIONS.get(action)
    if not handler:
        print(json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False))
        return

    try:
        result = handler(config)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({
            "error": str(e),
            "traceback": traceback.format_exc(),
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
