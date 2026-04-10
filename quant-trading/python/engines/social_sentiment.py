"""
social_sentiment — Social/community sentiment analysis for stocks.

Aggregates hotness rankings, community sentiment from Eastmoney, Baidu,
and Xueqiu-backed sources via akshare.

Input (JSON via stdin / QUANT_CONFIG):
  {
    "action": "hot_stocks" | "stock_sentiment" | "sentiment_history"
             | "retail_flow",
    "symbol": "600519",
    "market": "cn",
    "limit": 20
  }

Output: JSON with sentiment data.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from typing import Any

# ── Proxy bypass ──────────────────────────────────────────────────────────────
# Clear env-based proxies
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

# Also override macOS system-level proxy (read by urllib/requests via getproxies)
import urllib.request
urllib.request.getproxies = lambda: {}  # type: ignore[assignment]

_log = lambda msg: print(f"[social_sentiment] {msg}", file=sys.stderr)

# Circuit breaker: skip APIs that fail persistently within one process lifetime
_BROKEN_APIS: set[str] = set()


def run_social_sentiment(config: dict) -> dict:
    """Dispatch social sentiment actions."""
    action = config.get("action", "hot_stocks")
    market = config.get("market", "cn")
    limit = config.get("limit", 20)

    if action == "hot_stocks":
        return _hot_stocks(market, limit)
    elif action == "stock_sentiment":
        symbol = config.get("symbol", "")
        if not symbol:
            return {"error": "symbol is required for stock_sentiment"}
        return _stock_sentiment(symbol)
    elif action == "sentiment_history":
        symbol = config.get("symbol", "")
        if not symbol:
            return {"error": "symbol is required for sentiment_history"}
        return _sentiment_history(symbol, limit)
    elif action == "retail_flow":
        return _retail_flow(market, limit)
    else:
        return {"error": f"δ֪ action: {action}"}


# ── Hot stocks ────────────────────────────────────────────────────────────────

def _hot_stocks(market: str, limit: int) -> dict:
    """Get trending/hot stocks by popularity ranking."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    hot_list: list[dict] = []
    sources_used: list[str] = []

    # Source 1: Eastmoney hot stock ranking (push2 API)
    try:
        df = ak.stock_hot_rank_em()
        if df is not None and not df.empty:
            sources_used.append("eastmoney")
            cols = set(df.columns)
            for _, row in df.head(limit).iterrows():
                hot_list.append({
                    "rank": int(row.get("当前排名", row.get("序号", 0))) if row.get("当前排名", row.get("序号")) else 0,
                    "symbol": str(_first_col(row, cols, ("代码", "股票代码"), "")),
                    "name": str(_first_col(row, cols, ("股票名称", "股票简称"), "")),
                    "price": _safe_float(_first_col(row, cols, ("最新价",), None)),
                    "change_pct": _safe_float(_first_col(row, cols, ("涨跌幅",), None)),
                    "source": "eastmoney",
                })
    except Exception as e:
        _log(f"eastmoney hot_rank failed: {type(e).__name__}: {e}")

    # Source 1b: Fallback — Eastmoney 千股千评 (stock_comment_em), sorted by
    # attention index. Uses a different API domain that is more reliable.
    if not hot_list:
        try:
            df_cm = ak.stock_comment_em()
            if df_cm is not None and not df_cm.empty:
                sources_used.append("eastmoney-comment")
                # Sort by attention index descending to approximate "hotness"
                sort_col = "关注指数" if "关注指数" in df_cm.columns else "综合得分" if "综合得分" in df_cm.columns else None
                if sort_col:
                    df_cm = df_cm.sort_values(sort_col, ascending=False)
                for _, row in df_cm.head(limit).iterrows():
                    cols_cm = set(df_cm.columns)
                    hot_list.append({
                        "rank": len(hot_list) + 1,
                        "symbol": str(_first_col(row, cols_cm, ("代码", "股票代码"), "")),
                        "name": str(_first_col(row, cols_cm, ("名称", "股票名称", "股票简称"), "")),
                        "price": _safe_float(_first_col(row, cols_cm, ("最新价",), None)),
                        "change_pct": _safe_float(_first_col(row, cols_cm, ("涨跌幅",), None)),
                        "attention_score": _safe_float(_first_col(row, cols_cm, ("关注指数",), None)),
                        "composite_score": _safe_float(_first_col(row, cols_cm, ("综合得分",), None)),
                        "source": "eastmoney-comment",
                    })
        except Exception as e:
            _log(f"eastmoney comment fallback failed: {type(e).__name__}: {e}")

    # Source 2: Baidu hot search for stocks
    if len(hot_list) < limit:
        try:
            df2 = ak.stock_hot_search_baidu(symbol="A股")
            if df2 is not None and not df2.empty:
                sources_used.append("baidu")
                cols2 = set(df2.columns)
                # Build name->code lookup when Baidu lacks a code column
                name_to_code: dict[str, str] = {}
                if "代码" not in cols2 and "股票代码" not in cols2:
                    try:
                        info_df = ak.stock_info_a_code_name()
                        name_to_code = dict(zip(info_df["name"], info_df["code"]))
                    except Exception:
                        pass
                seen_names = {h["name"] for h in hot_list}
                for _, row in df2.head(limit).iterrows():
                    # Column may be "名称/代码" (combined), "代码", or "股票代码"
                    raw_name = str(_first_col(row, cols2, ("名称/代码", "名称", "股票名称", "股票简称"), ""))
                    name = raw_name.strip()
                    sym = str(_first_col(row, cols2, ("代码", "股票代码"), ""))
                    if not sym and name:
                        sym = name_to_code.get(name, "")
                    # Parse change_pct from string like "-5.65%"
                    raw_chg = _first_col(row, cols2, ("涨跌幅",), None)
                    change_pct = _parse_pct(raw_chg)
                    hot_score = _safe_float(_first_col(row, cols2, ("综合热度", "热搜指数", "搜索量"), None))
                    if name and name not in seen_names:
                        hot_list.append({
                            "rank": len(hot_list) + 1,
                            "symbol": sym,
                            "name": name,
                            "change_pct": change_pct,
                            "hot_score": hot_score,
                            "source": "baidu",
                        })
                        seen_names.add(name)
        except Exception as e:
            _log(f"baidu hot_search failed: {type(e).__name__}: {e}")

    return {
        "market": market,
        "count": min(len(hot_list), limit),
        "sources": sources_used,
        "hot_stocks": hot_list[:limit],
        "timestamp": datetime.now().isoformat()[:19],
    }


# ── Individual stock sentiment ────────────────────────────────────────────────

# Cache full-market comment table (fetched once, reused for all stocks)
_COMMENT_EM_CACHE: dict[str, Any] = {}  # {"df": DataFrame, "ts": float}


def _get_stock_comment_em():
    """Cached fetch of stock_comment_em (whole market table)."""
    import time
    cached = _COMMENT_EM_CACHE.get("df")
    ts = _COMMENT_EM_CACHE.get("ts", 0)
    if cached is not None and (time.time() - ts) < 300:  # 5min TTL
        return cached
    try:
        import akshare as ak
        df = ak.stock_comment_em()
        if df is not None and not df.empty:
            _COMMENT_EM_CACHE["df"] = df
            _COMMENT_EM_CACHE["ts"] = time.time()
            return df
    except Exception as e:
        _log(f"stock_comment_em failed: {type(e).__name__}: {e}")
    return None


def _stock_sentiment(symbol: str) -> dict:
    """Analyze sentiment for a specific stock from community discussions."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    sentiment: dict[str, Any] = {
        "symbol": symbol,
        "timestamp": datetime.now().isoformat()[:19],
    }

    # Eastmoney stock comment sentiment (cached full-market table)
    try:
        df = _get_stock_comment_em()
        if df is not None and not df.empty:
            stock_row = df[df["代码"].astype(str) == str(symbol)]
            if not stock_row.empty:
                row = stock_row.iloc[0]
                sentiment["rank"] = int(row.get("排名", 0)) if row.get("排名") else None
                sentiment["attention_score"] = _safe_float(row.get("关注指数", None))
                sentiment["participation_score"] = _safe_float(row.get("参与指数", None))
    except Exception as e:
        _log(f"stock_comment_em lookup failed: {type(e).__name__}: {e}")

    # Eastmoney stock hot rank detail (skip if API is known broken this session)
    if "stock_hot_rank_detail_em" not in _BROKEN_APIS:
        try:
            df2 = ak.stock_hot_rank_detail_em(symbol=symbol)
            if df2 is not None and not df2.empty:
                latest = df2.iloc[-1] if len(df2) > 0 else None
                if latest is not None:
                    sentiment["hot_rank"] = int(latest.get("排名", 0)) if latest.get("排名") else None
                    sentiment["hot_score"] = _safe_float(latest.get("热度", None))
        except (ValueError, TypeError) as e:
            _log(f"stock_hot_rank_detail_em broken (disabling): {e}")
            _BROKEN_APIS.add("stock_hot_rank_detail_em")
        except Exception as e:
            _log(f"stock_hot_rank_detail_em failed: {type(e).__name__}: {e}")

    # Compute composite sentiment score (0-100)
    scores = []
    if sentiment.get("attention_score") is not None:
        scores.append(min(100, max(0, sentiment["attention_score"])))
    if sentiment.get("participation_score") is not None:
        scores.append(min(100, max(0, sentiment["participation_score"])))
    if sentiment.get("hot_score") is not None:
        # Normalize hot score to 0-100 range (raw values vary)
        scores.append(min(100, max(0, sentiment["hot_score"] / 10)))

    if scores:
        composite = sum(scores) / len(scores)
        sentiment["composite_score"] = round(composite, 1)
        if composite >= 70:
            sentiment["level"] = "高热度"
            sentiment["interpretation"] = "市场关注度极高，注意过热风险"
        elif composite >= 40:
            sentiment["level"] = "中等热度"
            sentiment["interpretation"] = "市场适度关注"
        else:
            sentiment["level"] = "低热度"
            sentiment["interpretation"] = "市场关注度偏低，可能存在预期差机会"
    else:
        sentiment["composite_score"] = None
        sentiment["level"] = "unknown"
        sentiment["interpretation"] = "舆情数据不足"

    return sentiment


# ── Sentiment history ─────────────────────────────────────────────────────────

def _sentiment_history(symbol: str, limit: int) -> dict:
    """Historical sentiment trend for a stock."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    history: list[dict] = []

    if "stock_hot_rank_detail_em" not in _BROKEN_APIS:
        try:
            df = ak.stock_hot_rank_detail_em(symbol=symbol)
            if df is not None and not df.empty:
                for _, row in df.tail(limit).iterrows():
                    history.append({
                        "datetime": str(row.get("时间", row.get("日期", "")))[:16],
                        "rank": int(row.get("排名", 0)) if row.get("排名") else None,
                        "hot_score": _safe_float(row.get("热度", None)),
                    })
        except (ValueError, TypeError) as e:
            _log(f"stock_hot_rank_detail_em broken (disabling): {e}")
            _BROKEN_APIS.add("stock_hot_rank_detail_em")
        except Exception as e:
            _log(f"sentiment_history failed: {type(e).__name__}: {e}")

    # Compute trend
    trend = "stable"
    if len(history) >= 3:
        recent_scores = [h["hot_score"] for h in history[-3:] if h.get("hot_score")]
        if len(recent_scores) >= 2:
            if recent_scores[-1] > recent_scores[0] * 1.2:
                trend = "rising"
            elif recent_scores[-1] < recent_scores[0] * 0.8:
                trend = "falling"

    return {
        "symbol": symbol,
        "count": len(history),
        "trend": trend,
        "history": history,
    }


# ── Retail flow / debate ─────────────────────────────────────────────────────

def _retail_flow(market: str, limit: int) -> dict:
    """Retail investor activity: most-discussed, most-traded by retail."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    retail_hot: list[dict] = []

    # Eastmoney popularity changes (retail signal)
    try:
        df = ak.stock_hot_rank_em()
        if df is not None and not df.empty:
            # Stocks with biggest rank jumps = sudden retail interest
            if "排名变化" in df.columns:
                df_sorted = df.sort_values("排名变化", ascending=True)
            else:
                df_sorted = df
            for _, row in df_sorted.head(limit).iterrows():
                retail_hot.append({
                    "symbol": str(row.get("代码", row.get("股票代码", ""))),
                    "name": str(row.get("股票名称", row.get("股票简称", ""))),
                    "rank": int(row.get("当前排名", 0)) if row.get("当前排名") else 0,
                    "rank_change": _safe_float(row.get("排名变化", None)),
                    "price": _safe_float(row.get("最新价", None)),
                    "change_pct": _safe_float(row.get("涨跌幅", None)),
                })
    except Exception as e:
        _log(f"retail_flow hot_rank failed: {type(e).__name__}: {e}")

    # Fallback: use stock_comment_em attention data as a proxy for retail flow
    if not retail_hot:
        try:
            df_cm = ak.stock_comment_em()
            if df_cm is not None and not df_cm.empty:
                # Sort by 上升 (rank change) descending = biggest attention jumps
                sort_col = "上升" if "上升" in df_cm.columns else None
                if sort_col:
                    df_cm = df_cm.sort_values(sort_col, ascending=False)
                for _, row in df_cm.head(limit).iterrows():
                    retail_hot.append({
                        "symbol": str(row.get("代码", "")),
                        "name": str(row.get("名称", "")),
                        "rank": int(row.get("目前排名", 0)) if row.get("目前排名") else 0,
                        "rank_change": _safe_float(row.get("上升", None)),
                        "price": _safe_float(row.get("最新价", None)),
                        "change_pct": _safe_float(row.get("涨跌幅", None)),
                    })
        except Exception as e:
            _log(f"retail_flow comment fallback failed: {type(e).__name__}: {e}")

    return {
        "market": market,
        "count": len(retail_hot),
        "retail_hot_stocks": retail_hot,
        "note": "排名变化越大，散户关注度突增越明显",
    }


def _first_col(row: Any, cols: set, candidates: tuple, default: Any = None) -> Any:
    """Return the value of the first matching column name found in *cols*."""
    for c in candidates:
        if c in cols:
            v = row.get(c)
            if v is not None:
                return v
    return default


def _parse_pct(val: Any) -> float | None:
    """Parse a percentage string like '-5.65%' into a float (-5.65)."""
    if val is None:
        return None
    s = str(val).strip().replace("%", "").replace("％", "")
    try:
        f = float(s)
        return round(f, 4) if f == f else None
    except (ValueError, TypeError):
        return None


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN
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

        result = run_social_sentiment(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
