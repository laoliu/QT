"""
News engine — financial news aggregation & sentiment analysis.

Fetches news from akshare (CCTV, Sina, Eastmoney) for CN markets,
and provides sentiment scoring.

Input (JSON via stdin):
  {
    "action": "market_news" | "stock_news" | "sentiment" | "events",
    "symbol": "600519",
    "market": "cn" | "us" | "hk" | "crypto" | "global",
    "limit": 10,
    "days": 3,
    "keyword": ""
  }

Output: JSON with news data.
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


def _market_news(config: dict) -> dict:
    """Fetch market-wide news headlines."""
    market = config.get("market", "cn")
    limit = config.get("limit", 10)
    keyword = config.get("keyword", "")

    news_items: list[dict] = []

    if market in ("cn", "global"):
        news_items.extend(_get_cn_news(limit, keyword))

    if market in ("us", "global"):
        news_items.extend(_get_us_news(limit, keyword))

    # Sort by time, newest first
    news_items.sort(key=lambda x: x.get("time", ""), reverse=True)

    return {
        "market": market,
        "count": len(news_items[:limit]),
        "news": news_items[:limit],
    }


def _stock_news(config: dict) -> dict:
    """Fetch news for a specific stock."""
    symbol = config.get("symbol", "")
    limit = config.get("limit", 10)

    if not symbol:
        return {"error": "symbol is required for stock_news"}

    news_items: list[dict] = []

    try:
        import akshare as ak

        # Individual stock news from Eastmoney
        try:
            df = ak.stock_news_em(symbol=symbol)
            if df is not None and not df.empty:
                for _, row in df.head(limit).iterrows():
                    news_items.append({
                        "title": str(row.get("新闻标题", "")),
                        "content": str(row.get("新闻内容", ""))[:200],
                        "source": str(row.get("文章来源", "")),
                        "time": str(row.get("发布时间", "")),
                        "url": str(row.get("新闻链接", "")),
                    })
        except Exception:
            pass

    except ImportError:
        return {"error": "akshare not installed"}

    return {
        "symbol": symbol,
        "count": len(news_items),
        "news": news_items[:limit],
    }


def _sentiment(config: dict) -> dict:
    """Analyze sentiment from recent news."""
    symbol = config.get("symbol", "")
    market = config.get("market", "cn")

    # Get recent news
    if symbol:
        news_data = _stock_news(config)
    else:
        news_data = _market_news(config)

    if "error" in news_data:
        return news_data

    news_list = news_data.get("news", [])

    if not news_list:
        return {
            "symbol": symbol,
            "sentiment": "neutral",
            "score": 0,
            "note": "无足够新闻数据进行情绪分析",
        }

    # Simple keyword-based sentiment scoring
    positive_words = [
        "涨", "上涨", "大涨", "暴涨", "利好", "突破", "创新高", "增长",
        "盈利", "超预期", "买入", "推荐", "强势", "反弹", "上调",
        "rally", "surge", "gain", "bullish", "upgrade", "buy",
        "breakout", "growth", "beat", "outperform",
    ]
    negative_words = [
        "跌", "下跌", "大跌", "暴跌", "利空", "跌破", "创新低", "下滑",
        "亏损", "不及预期", "卖出", "减持", "弱势", "回调", "下调",
        "decline", "drop", "fall", "bearish", "downgrade", "sell",
        "breakdown", "loss", "miss", "underperform",
    ]

    pos_count = 0
    neg_count = 0
    total = len(news_list)

    for news in news_list:
        text = (news.get("title", "") + " " + news.get("content", "")).lower()
        pos = sum(1 for w in positive_words if w in text)
        neg = sum(1 for w in negative_words if w in text)
        pos_count += pos
        neg_count += neg

    net = pos_count - neg_count
    if total > 0:
        score = net / (total * 2)  # normalized to [-1, 1]
    else:
        score = 0

    sentiment_label = (
        "强烈看多" if score > 0.5 else
        "看多" if score > 0.15 else
        "中性" if score > -0.15 else
        "看空" if score > -0.5 else
        "强烈看空"
    )

    return {
        "symbol": symbol or market,
        "sentiment": sentiment_label,
        "score": round(score, 3),
        "positiveSignals": pos_count,
        "negativeSignals": neg_count,
        "newsCount": total,
        "topNews": [n.get("title", "") for n in news_list[:5]],
    }


def _events(config: dict) -> dict:
    """Fetch upcoming economic calendar events."""
    market = config.get("market", "cn")
    limit = config.get("limit", 10)

    events: list[dict] = []

    try:
        import akshare as ak

        if market in ("cn", "global"):
            try:
                # Macro events from Eastmoney
                df = ak.news_economic_baidu()
                if df is not None and not df.empty:
                    for _, row in df.head(limit).iterrows():
                        events.append({
                            "date": str(row.get("日期", "")),
                            "event": str(row.get("事件", "")),
                            "country": str(row.get("国家", "")),
                            "importance": str(row.get("重要性", "")),
                            "previous": str(row.get("前值", "")),
                            "forecast": str(row.get("预测值", "")),
                            "actual": str(row.get("公布值", "")),
                        })
            except Exception:
                pass

    except ImportError:
        return {"error": "akshare not installed"}

    return {
        "market": market,
        "count": len(events),
        "events": events[:limit],
    }


def _get_cn_news(limit: int, keyword: str) -> list[dict]:
    """Fetch Chinese market news."""
    items: list[dict] = []

    try:
        import akshare as ak

        # CCTV news
        try:
            df = ak.news_cctv(date=datetime.now().strftime("%Y%m%d"))
            if df is not None and not df.empty:
                for _, row in df.head(limit).iterrows():
                    title = str(row.get("title", ""))
                    if keyword and keyword.lower() not in title.lower():
                        continue
                    items.append({
                        "title": title,
                        "content": str(row.get("content", ""))[:200],
                        "source": "CCTV",
                        "time": str(row.get("date", "")),
                    })
        except Exception:
            pass

        # Eastmoney market news  
        try:
            df = ak.stock_info_global_em()
            if df is not None and not df.empty:
                for _, row in df.head(limit).iterrows():
                    title = str(row.get("标题", ""))
                    if keyword and keyword.lower() not in title.lower():
                        continue
                    items.append({
                        "title": title,
                        "content": str(row.get("摘要", str(row.get("内容", ""))))[:200],
                        "source": "东方财富",
                        "time": str(row.get("发布时间", "")),
                    })
        except Exception:
            pass

    except ImportError:
        pass

    return items


def _get_us_news(limit: int, keyword: str) -> list[dict]:
    """Fetch US market news from multiple sources."""
    items: list[dict] = []

    try:
        import akshare as ak

        # Global financial news (Eastmoney)
        try:
            df = ak.stock_info_global_em()
            if df is not None and not df.empty:
                for _, row in df.head(limit).iterrows():
                    title = str(row.get("标题", ""))
                    if keyword and keyword.lower() not in title.lower():
                        continue
                    items.append({
                        "title": title,
                        "content": str(row.get("摘要", ""))[:200],
                        "source": "Global",
                        "time": str(row.get("发布时间", "")),
                    })
        except Exception:
            pass

    except ImportError:
        pass

    # yfinance-based US tech stock news
    try:
        import yfinance as yf
        tech_tickers = ["NVDA", "TSLA", "AAPL", "MSFT", "GOOGL", "META", "AMD", "AVGO"]
        for sym in tech_tickers:
            try:
                ticker = yf.Ticker(sym)
                news = ticker.news or []
                for n in news[:3]:
                    title = n.get("title", "")
                    if keyword and keyword.lower() not in title.lower():
                        continue
                    pub_time = ""
                    if n.get("providerPublishTime"):
                        pub_time = datetime.fromtimestamp(
                            n["providerPublishTime"]
                        ).strftime("%Y-%m-%d %H:%M")
                    items.append({
                        "title": title,
                        "content": "",
                        "source": f"US-{sym}",
                        "time": pub_time,
                        "link": n.get("link", ""),
                    })
            except Exception:
                continue
    except ImportError:
        pass

    # Sort by time, newest first
    items.sort(key=lambda x: x.get("time", ""), reverse=True)
    return items[:limit]


def _us_tech_events(config: dict) -> dict:
    """Scan US tech giants for significant events and map to A-share themes.

    This is a convenience wrapper that calls the event_catalyst_engine
    for news-oriented consumers.
    """
    limit = config.get("limit", 10)
    us_symbols = config.get("us_symbols", [
        "NVDA", "TSLA", "AAPL", "MSFT", "GOOGL", "META", "AMD", "AVGO", "ASML", "TSM",
    ])

    results: list[dict] = []
    event_keywords = {
        "AI": ["ai", "artificial intelligence", "machine learning", "neural", "llm",
               "gpt", "large language model", "generative", "copilot"],
        "芯片": ["chip", "semiconductor", "gpu", "cpu", "processor", "silicon",
                 "wafer", "foundry", "lithography", "asic"],
        "自动驾驶": ["autonomous", "self-driving", "autopilot", "fsd", "robotaxi"],
        "机器人": ["robot", "humanoid", "optimus"],
        "VR/MR": ["vision pro", "quest", "vr", "mr", "mixed reality"],
        "电动车": ["ev", "electric vehicle", "battery", "charging"],
        "云计算": ["cloud", "azure", "aws", "data center"],
        "大模型": ["foundation model", "gemini", "claude", "gpt-5", "llama"],
    }

    try:
        import yfinance as yf
        for sym in us_symbols:
            try:
                ticker = yf.Ticker(sym)
                news = ticker.news or []
                hist = ticker.history(period="2d")

                change_pct = 0.0
                if hist is not None and len(hist) >= 2:
                    last = float(hist["Close"].iloc[-1])
                    prev = float(hist["Close"].iloc[-2])
                    change_pct = round((last - prev) / prev * 100, 2) if prev > 0 else 0

                for n in news[:5]:
                    title = n.get("title", "")
                    text_lower = title.lower()

                    # Detect themes from headline
                    detected_themes = []
                    for theme, keywords in event_keywords.items():
                        if any(kw in text_lower for kw in keywords):
                            detected_themes.append(theme)

                    if detected_themes or abs(change_pct) >= 2:
                        pub_time = ""
                        if n.get("providerPublishTime"):
                            pub_time = datetime.fromtimestamp(
                                n["providerPublishTime"]
                            ).strftime("%Y-%m-%d %H:%M")
                        results.append({
                            "usSymbol": sym,
                            "title": title,
                            "source": n.get("publisher", ""),
                            "time": pub_time,
                            "changePct": change_pct,
                            "themes": detected_themes,
                            "link": n.get("link", ""),
                        })
            except Exception:
                continue
    except ImportError:
        return {"error": "yfinance not installed"}

    results.sort(key=lambda x: x.get("time", ""), reverse=True)

    return {
        "action": "us_tech_events",
        "count": len(results[:limit]),
        "events": results[:limit],
        "scannedSymbols": us_symbols,
    }


# ── Main ─────────────────────────────────────────────────────────

ACTIONS = {
    "market_news": _market_news,
    "stock_news": _stock_news,
    "sentiment": _sentiment,
    "events": _events,
    "us_tech_events": _us_tech_events,
}


def main():
    config = json.loads(sys.stdin.read())
    action = config.get("action", "market_news")

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
