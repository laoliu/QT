# coding: utf-8
"""
Event Catalyst Engine — US tech event → A-share counterpart mapping + capital flow validation.

Monitors US tech giants for significant moves or announcements, maps them
to A-share counterparts via us_a_mapping.json, and validates with capital
flow data to produce actionable catalyst signals.

Input (JSON via stdin):
  {
    "action": "scan" | "check_us" | "map_event" | "catalyst_picks",
    "us_symbols": ["NVDA", "TSLA"],     // optional: specific US tickers
    "themes": ["AI芯片", "光模块"],       // optional: filter by theme
    "min_change_pct": 3.0,               // US stock min move to trigger (%)
    "require_fund_flow": true,           // require positive capital flow
    "limit": 20,
    "top_n": 10
  }

Output: JSON with catalyst signals and A-share candidates.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from typing import Any
from urllib.request import getproxies

# ── Proxy management ──
# Detect system proxy BEFORE clearing env vars.
# On macOS, getproxies() reads the system proxy config (System Preferences).
# yfinance needs a proxy to reach Yahoo Finance from CN networks;
# akshare (domestic Eastmoney APIs) must NOT use a proxy.
_SYSTEM_PROXY: dict[str, str] = {}
_raw_proxies = getproxies()
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    _v = os.environ.pop(_k, None)
    if _v:
        _SYSTEM_PROXY[_k.lower()] = _v
# Also pick up macOS system proxy detected by urllib
if "http" in _raw_proxies and "http_proxy" not in _SYSTEM_PROXY:
    _SYSTEM_PROXY["http_proxy"] = _raw_proxies["http"]
if "https" in _raw_proxies and "https_proxy" not in _SYSTEM_PROXY:
    _SYSTEM_PROXY["https_proxy"] = _raw_proxies["https"]

os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

# ── Data directory ──
_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")


def _load_us_a_mapping() -> dict[str, Any]:
    """Load the US-A stock counterpart mapping table."""
    path = os.path.join(_DATA_DIR, "us_a_mapping.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Filter out metadata keys starting with _
    return {k: v for k, v in data.items() if not k.startswith("_")}


def _load_scarcity_tags() -> dict[str, dict]:
    """Load scarcity/uniqueness tag data."""
    path = os.path.join(_DATA_DIR, "scarcity_tags.json")
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("stocks", {})


# ── A-share name lookup (cached) ──
_a_share_name_cache: dict[str, str] | None = None


def _get_a_share_name(code: str, scarcity: dict[str, dict]) -> str:
    """Resolve A-share stock name: scarcity_tags first, then akshare batch lookup."""
    sc_name = scarcity.get(code, {}).get("name", "")
    if sc_name:
        return sc_name

    global _a_share_name_cache  # noqa: PLW0603
    if _a_share_name_cache is None:
        _a_share_name_cache = {}
        try:
            _clear_proxy()
            import akshare as ak
            df = ak.stock_info_a_code_name()
            for _, row in df.iterrows():
                _a_share_name_cache[str(row["code"]).zfill(6)] = str(row["name"])
        except Exception:
            pass
    return _a_share_name_cache.get(code, "")


# ── US market data via yfinance ──


def _restore_proxy():
    """Temporarily restore proxy env vars for yfinance (Yahoo Finance needs proxy from CN)."""
    for k, v in _SYSTEM_PROXY.items():
        os.environ[k] = v
    os.environ.pop("no_proxy", None)
    os.environ.pop("NO_PROXY", None)


def _clear_proxy():
    """Clear proxy env vars again for domestic API calls."""
    for k in _SYSTEM_PROXY:
        os.environ.pop(k, None)
    os.environ["no_proxy"] = "*"
    os.environ["NO_PROXY"] = "*"


def _fetch_us_quotes(symbols: list[str]) -> dict[str, dict]:
    """Fetch latest US stock quotes from yfinance (batch download to avoid rate limits).

    Returns: {symbol: {price, change, changePct, volume, marketCap, name}}
    """
    results: dict[str, dict] = {}
    _restore_proxy()
    try:
        import yfinance as yf
        # Batch download avoids per-ticker rate limiting from Yahoo Finance
        df = yf.download(symbols, period="2d", group_by="ticker", progress=False, threads=False)
        if df is None or df.empty:
            return results

        for sym in symbols:
            try:
                if len(symbols) == 1:
                    hist = df
                else:
                    hist = df[sym] if sym in df.columns.get_level_values(0) else None
                if hist is None or hist.empty or hist["Close"].dropna().empty:
                    continue
                closes = hist["Close"].dropna()
                if len(closes) < 1:
                    continue
                last_close = float(closes.iloc[-1])
                prev_close = float(closes.iloc[-2]) if len(closes) >= 2 else last_close
                change = last_close - prev_close
                change_pct = (change / prev_close * 100) if prev_close > 0 else 0.0
                vol = hist["Volume"].dropna()
                last_vol = int(vol.iloc[-1]) if not vol.empty else 0

                results[sym] = {
                    "symbol": sym,
                    "price": round(last_close, 2),
                    "change": round(change, 2),
                    "changePct": round(change_pct, 2),
                    "volume": last_vol,
                    "marketCap": 0,
                }
            except Exception as e:
                print(f"[event_catalyst] failed to parse {sym}: {e}", file=sys.stderr)
    except ImportError:
        print("[event_catalyst] yfinance not installed", file=sys.stderr)
    except Exception as e:
        print(f"[event_catalyst] batch download failed: {e}", file=sys.stderr)
    finally:
        _clear_proxy()
    return results


def _parse_news_item(n: dict) -> dict:
    """Parse a single news item from yfinance, handling both old and new (1.2.0+) formats.

    Old format: {title, publisher, providerPublishTime, link}
    New format (1.2.0+): {content: {title, provider: {displayName}, pubDate, clickThroughUrl: {url}}}
    """
    content = n.get("content", {}) if isinstance(n.get("content"), dict) else {}
    # Title: content.title (new) or top-level title (old)
    title = content.get("title") or n.get("title", "")
    # Source: content.provider.displayName (new) or publisher (old)
    provider = content.get("provider")
    source = (provider.get("displayName", "") if isinstance(provider, dict) else "") or n.get("publisher", "")
    # Time: content.pubDate ISO string (new) or providerPublishTime epoch (old)
    pub_date = content.get("pubDate", "")
    if pub_date and isinstance(pub_date, str):
        try:
            time_str = datetime.fromisoformat(pub_date.replace("Z", "+00:00")).strftime("%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            time_str = ""
    elif n.get("providerPublishTime"):
        try:
            time_str = datetime.fromtimestamp(n["providerPublishTime"]).strftime("%Y-%m-%d %H:%M")
        except (ValueError, OSError, TypeError):
            time_str = ""
    else:
        time_str = ""
    # Link: content.clickThroughUrl.url or content.canonicalUrl.url (new) or link (old)
    click_url = content.get("clickThroughUrl")
    canon_url = content.get("canonicalUrl")
    link = (
        (click_url.get("url", "") if isinstance(click_url, dict) else "")
        or (canon_url.get("url", "") if isinstance(canon_url, dict) else "")
        or n.get("link", "")
    )
    return {"title": title, "source": source, "time": time_str, "link": link}


def _fetch_us_news(symbols: list[str], limit: int = 5) -> dict[str, list[dict]]:
    """Fetch recent news for US tech stocks.

    Returns: {symbol: [{title, source, time, link}]}
    Handles yfinance 1.2.0+ rate limiting by stopping early on rate limit errors.
    """
    results: dict[str, list[dict]] = {}
    _restore_proxy()
    try:
        import yfinance as yf
        rate_limited = False
        for sym in symbols:
            if rate_limited:
                results[sym] = []
                continue
            try:
                ticker = yf.Ticker(sym)
                news = ticker.news or []
                items = [_parse_news_item(n) for n in news[:limit] if isinstance(n, dict)]
                results[sym] = items
            except Exception as e:
                # Stop hitting Yahoo if rate-limited; remaining symbols get empty news
                if "rate" in str(e).lower() or "429" in str(e) or "too many" in str(e).lower():
                    rate_limited = True
                    print(f"[event_catalyst] rate limited at {sym}, skipping remaining news", file=sys.stderr)
                results[sym] = []
    except ImportError:
        pass
    finally:
        _clear_proxy()
    return results


# ── A-share capital flow check ──

def _check_fund_flow(codes: list[str]) -> dict[str, dict]:
    """Check recent capital flow for A-share codes.

    Returns: {code: {mainNetIn, mainNetInPct, superLargeNetIn, trend}}
    """
    results: dict[str, dict] = {}
    try:
        import akshare as ak
        for code in codes:
            try:
                df = ak.stock_individual_fund_flow(stock=code, market="sh" if code.startswith("6") else "sz")
                if df is None or df.empty:
                    continue
                # Latest day
                latest = df.iloc[-1]
                main_net = float(latest.get("主力净流入-净额", 0))
                main_pct = float(latest.get("主力净流入-净占比", 0))
                super_large = float(latest.get("超大单净流入-净额", 0))

                # 3-day trend: count positive main net inflow days
                recent3 = df.tail(3)
                positive_days = sum(
                    1 for _, row in recent3.iterrows()
                    if float(row.get("主力净流入-净额", 0)) > 0
                )

                results[code] = {
                    "mainNetIn": round(main_net / 1e4, 2),  # 万元
                    "mainNetInPct": round(main_pct, 2),
                    "superLargeNetIn": round(super_large / 1e4, 2),
                    "positiveDays3": positive_days,
                    "trend": "strong" if positive_days >= 2 else ("weak" if positive_days == 1 else "outflow"),
                }
            except Exception:
                pass
    except ImportError:
        pass
    return results


def _get_a_share_quotes(codes: list[str]) -> dict[str, dict]:
    """Fetch A-share real-time quotes for price/change data."""
    results: dict[str, dict] = {}
    try:
        import akshare as ak
        for code in codes:
            try:
                mkt = "sh" if code.startswith("6") else "sz"
                df = ak.stock_zh_a_spot_em()
                if df is None or df.empty:
                    continue
                row = df[df["代码"] == code]
                if row.empty:
                    continue
                r = row.iloc[0]
                results[code] = {
                    "code": code,
                    "name": str(r.get("名称", "")),
                    "price": float(r.get("最新价", 0)),
                    "changePct": float(r.get("涨跌幅", 0)),
                    "volume": float(r.get("成交量", 0)),
                    "amount": float(r.get("成交额", 0)),
                    "turnover": float(r.get("换手率", 0)),
                }
            except Exception:
                pass
    except ImportError:
        pass
    return results


# ── Event keyword detection ──

_EVENT_KEYWORDS = {
    "AI": ["ai", "artificial intelligence", "machine learning", "neural", "llm", "gpt",
           "large language model", "generative", "copilot", "deep learning"],
    "芯片": ["chip", "semiconductor", "gpu", "cpu", "processor", "silicon", "wafer",
             "foundry", "fab", "lithography", "asic", "fpga"],
    "自动驾驶": ["autonomous", "self-driving", "autopilot", "fsd", "robotaxi", "lidar",
                "adas", "autonomous driving"],
    "机器人": ["robot", "humanoid", "optimus", "tesla bot", "figure"],
    "VR/MR": ["vision pro", "quest", "vr", "mr", "mixed reality", "spatial computing",
              "ar glasses", "metaverse"],
    "电动车": ["ev", "electric vehicle", "battery", "charging", "supercharger",
              "cybertruck", "model"],
    "云计算": ["cloud", "azure", "aws", "gcp", "data center", "saas", "iaas"],
    "大模型": ["foundation model", "gemini", "claude", "gpt-5", "llama", "mixtral",
              "open source model", "reasoning model"],
    "硅光/CPO": ["silicon photonics", "co-packaged optics", "cpo", "photonic",
                "optical interconnect", "800g", "1.6t", "光模块", "光互连"],
    "液冷散热": ["liquid cooling", "immersion cooling", "cold plate",
                "thermal management", "data center cooling"],
    "先进封装": ["chiplet", "cowos", "2.5d", "3d packaging", "hbm", "tsv",
               "advanced packaging", "fan-out"],
    "自主可控": ["domestic replacement", "self-reliant", "supply chain security",
               "de-risk", "onshoring", "国产替代", "自主可控"],
}


def _detect_event_themes(news_items: list[dict]) -> list[str]:
    """Detect which themes are mentioned in news headlines."""
    detected: set[str] = set()
    for item in news_items:
        text = (item.get("title", "") + " " + item.get("source", "")).lower()
        for theme, keywords in _EVENT_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                detected.add(theme)
    return sorted(detected)


# ── Actions ──

def _check_us(config: dict) -> dict:
    """Check US tech stock movements and detect significant events."""
    mapping = _load_us_a_mapping()
    us_symbols = config.get("us_symbols", list(mapping.keys()))
    min_change = float(config.get("min_change_pct", 2.0))

    quotes = _fetch_us_quotes(us_symbols)
    news = _fetch_us_news(us_symbols, limit=5)

    movers: list[dict] = []
    for sym in us_symbols:
        q = quotes.get(sym)
        if not q:
            continue
        abs_change = abs(q["changePct"])
        sym_news = news.get(sym, [])
        themes = _detect_event_themes(sym_news)

        entry = {
            "symbol": sym,
            "name": mapping.get(sym, {}).get("name", sym),
            "price": q["price"],
            "changePct": q["changePct"],
            "volume": q["volume"],
            "significant": abs_change >= min_change,
            "newsCount": len(sym_news),
            "topNews": [n["title"] for n in sym_news[:3]],
            "detectedThemes": themes,
            "mappedThemes": mapping.get(sym, {}).get("themes", []),
        }
        movers.append(entry)

    movers.sort(key=lambda x: abs(x["changePct"]), reverse=True)
    significant = [m for m in movers if m["significant"]]

    return {
        "action": "check_us",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "allMovers": movers,
        "significantMovers": significant,
        "significantCount": len(significant),
        "threshold": min_change,
    }


def _map_event(config: dict) -> dict:
    """Map a US tech event to A-share counterparts."""
    mapping = _load_us_a_mapping()
    scarcity = _load_scarcity_tags()

    us_symbol = config.get("us_symbol", "").upper()
    themes = config.get("themes", [])

    if us_symbol not in mapping:
        return {"error": f"未找到 {us_symbol} 的A股映射，请更新 us_a_mapping.json"}

    entry = mapping[us_symbol]
    a_shares_map = entry.get("a_shares", {})

    # Filter by themes if specified
    if themes:
        filtered = {}
        for sector, codes in a_shares_map.items():
            if any(t in sector for t in themes):
                filtered[sector] = codes
        if filtered:
            a_shares_map = filtered

    # Flatten all codes and enrich
    all_codes = []
    code_to_sector: dict[str, list[str]] = {}
    for sector, codes in a_shares_map.items():
        for code in codes:
            if code not in code_to_sector:
                code_to_sector[code] = []
                all_codes.append(code)
            code_to_sector[code].append(sector)

    # Enrich with scarcity scores
    candidates = []
    for code in all_codes:
        sc = scarcity.get(code, {})
        candidates.append({
            "code": code,
            "name": _get_a_share_name(code, scarcity),
            "sectors": code_to_sector.get(code, []),
            "scarcityScore": sc.get("scarcity_score", 0.0),
            "scarcityTags": sc.get("tags", []),
            "moat": sc.get("moat", ""),
        })

    # Sort: scarcity score first, then sector count
    candidates.sort(key=lambda x: (-x["scarcityScore"], -len(x["sectors"])))

    return {
        "action": "map_event",
        "usSymbol": us_symbol,
        "usName": entry.get("name", us_symbol),
        "usThemes": entry.get("themes", []),
        "filterThemes": themes or "all",
        "candidates": candidates,
        "candidateCount": len(candidates),
    }


def _catalyst_picks(config: dict) -> dict:
    """Full pipeline: scan US movers → map to A-shares → validate with capital flow.

    This is the main entry point for the daily event-catalyst strategy.
    """
    mapping = _load_us_a_mapping()
    scarcity = _load_scarcity_tags()

    us_symbols = config.get("us_symbols", list(mapping.keys()))
    min_change = float(config.get("min_change_pct", 2.0))
    require_flow = config.get("require_fund_flow", True)
    top_n = int(config.get("top_n", 10))
    themes_filter = config.get("themes", [])

    # Step 1: Check US movers
    quotes = _fetch_us_quotes(us_symbols)
    news = _fetch_us_news(us_symbols, limit=5)

    triggered_us: list[dict] = []
    for sym in us_symbols:
        q = quotes.get(sym)
        if not q:
            continue
        if abs(q["changePct"]) < min_change:
            continue
        sym_news = news.get(sym, [])
        detected_themes = _detect_event_themes(sym_news)

        triggered_us.append({
            "symbol": sym,
            "name": mapping.get(sym, {}).get("name", sym),
            "changePct": q["changePct"],
            "newsThemes": detected_themes,
            "topNews": [n["title"] for n in sym_news[:2]],
        })

    if not triggered_us:
        return {
            "action": "catalyst_picks",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "triggered": False,
            "message": f"无美股科技龙头涨跌超{min_change}%，今日无事件催化信号",
            "usChecked": len(quotes),
        }

    # Step 2: Map to A-share candidates
    all_candidates: dict[str, dict] = {}  # code -> candidate info
    for us in triggered_us:
        sym = us["symbol"]
        entry = mapping.get(sym, {})
        a_shares_map = entry.get("a_shares", {})

        for sector, codes in a_shares_map.items():
            if themes_filter and not any(t in sector for t in themes_filter):
                continue
            for code in codes:
                if code not in all_candidates:
                    sc = scarcity.get(code, {})
                    all_candidates[code] = {
                        "code": code,
                        "name": _get_a_share_name(code, scarcity),
                        "scarcityScore": sc.get("scarcity_score", 0.0),
                        "scarcityTags": sc.get("tags", []),
                        "moat": sc.get("moat", ""),
                        "catalysts": [],
                        "sectors": set(),
                        "catalystScore": 0.0,
                    }
                cand = all_candidates[code]
                cand["catalysts"].append({
                    "usSymbol": sym,
                    "usChangePct": us["changePct"],
                    "sector": sector,
                })
                cand["sectors"].add(sector)

                # Score contribution from this catalyst
                us_strength = abs(us["changePct"]) / 5.0  # normalize: 5% = 1.0
                cand["catalystScore"] += us_strength * 10

    if not all_candidates:
        return {
            "action": "catalyst_picks",
            "date": datetime.now().strftime("%Y-%m-%d"),
            "triggered": True,
            "triggeredUS": triggered_us,
            "message": "美股异动已检测但无匹配的A股对标",
            "candidates": [],
        }

    # Step 3: Check capital flow for all candidates
    all_codes = list(all_candidates.keys())
    flow_data = _check_fund_flow(all_codes) if require_flow else {}

    # Step 4: Compute final composite score
    scored_picks: list[dict] = []
    for code, cand in all_candidates.items():
        flow = flow_data.get(code, {})

        # Composite scoring
        catalyst_score = cand["catalystScore"]
        scarcity_bonus = cand["scarcityScore"] * 15  # 0-15 points for scarcity
        sector_breadth = len(cand["sectors"]) * 3    # bonus for multi-sector mapping

        # Capital flow factor
        flow_score = 0.0
        if flow:
            if flow.get("trend") == "strong":
                flow_score = 15
            elif flow.get("trend") == "weak":
                flow_score = 5
            else:
                flow_score = -5  # penalty for net outflow

            if flow.get("mainNetInPct", 0) > 3:
                flow_score += 5  # extra bonus for strong inflow

        total_score = catalyst_score + scarcity_bonus + sector_breadth + flow_score

        # Skip if require_flow and flow is negative
        if require_flow and flow and flow.get("trend") == "outflow":
            continue

        scored_picks.append({
            "code": code,
            "name": cand["name"],
            "totalScore": round(total_score, 2),
            "catalystScore": round(catalyst_score, 2),
            "scarcityScore": cand["scarcityScore"],
            "scarcityTags": cand["scarcityTags"],
            "moat": cand["moat"],
            "sectors": sorted(cand["sectors"]),
            "catalysts": cand["catalysts"],
            "fundFlow": flow or None,
            "flowScore": round(flow_score, 2),
            "signals": _build_signals(cand, flow),
        })

    scored_picks.sort(key=lambda x: -x["totalScore"])
    final_picks = scored_picks[:top_n]

    # Rank
    for i, pick in enumerate(final_picks):
        pick["rank"] = i + 1

    return {
        "action": "catalyst_picks",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "triggered": True,
        "triggeredUS": triggered_us,
        "triggeredCount": len(triggered_us),
        "candidateCount": len(scored_picks),
        "picks": final_picks,
        "pickCount": len(final_picks),
        "params": {
            "minChangePct": min_change,
            "requireFundFlow": require_flow,
            "topN": top_n,
        },
    }


def _build_signals(cand: dict, flow: dict) -> list[str]:
    """Build human-readable signal descriptions."""
    signals: list[str] = []

    for cat in cand.get("catalysts", []):
        direction = "大涨" if cat["usChangePct"] > 0 else "大跌"
        signals.append(
            f"美股{cat['usSymbol']}{direction}{abs(cat['usChangePct']):.1f}% → {cat['sector']}对标"
        )

    if cand.get("scarcityScore", 0) >= 0.8:
        tags = ", ".join(cand.get("scarcityTags", [])[:2])
        signals.append(f"高稀缺性标的: {tags}")

    if cand.get("moat"):
        signals.append(f"护城河: {cand['moat']}")

    if flow:
        if flow.get("trend") == "strong":
            signals.append(f"主力资金连续流入({flow.get('positiveDays3', 0)}天), 净流入{flow.get('mainNetIn', 0):.0f}万")
        elif flow.get("mainNetInPct", 0) > 3:
            signals.append(f"主力资金大幅流入, 占比{flow.get('mainNetInPct', 0):.1f}%")

    return signals


def _scan(config: dict) -> dict:
    """Full end-to-end scan: check US → detect events → produce catalyst picks.
    Convenience wrapper combining check_us + catalyst_picks."""
    return _catalyst_picks(config)


# ── Main ──

ACTIONS = {
    "scan": _scan,
    "check_us": _check_us,
    "map_event": _map_event,
    "catalyst_picks": _catalyst_picks,
}


def main():
    config = json.loads(sys.stdin.read())
    action = config.get("action", "scan")

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
