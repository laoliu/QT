"""
板块热点竞价候选选股 (Sector Hotspot Auction Candidate Screener).

Strategy logic:
  1. Fetch yesterday's limit-up pool and group by industry sector.
  2. Identify hotspot sectors (≥2 limit-up stocks).
  3. Fetch all stocks in hotspot sectors.
  4. Exclude yesterday's limit-up stocks (they are likely to gap-up/一字板,
     can't buy), ST stocks, and out-of-range market cap.
  5. Score remaining candidates on multiple factors:
     - Sector heat (板块热度): how many limit-ups in the sector
     - Yesterday's momentum (昨日表现): change % in the sweet spot
     - Capital flow (资金流向): main-force net inflow positive
     - Technical alignment (技术面): MA alignment, volume trend
     - Market cap fit (市值适配): 30-150B optimal for 游资
     - Market environment (市场情绪): sentiment score gating
  6. Output ranked candidates for call-auction (集合竞价) entry next morning.

Data flow:
  昨日涨停池 → 按行业分组 → 热点板块(≥2涨停)
       ↓
  板块成员股 → 排除涨停/ST/停牌
       ↓
  候选 + K线 + 资金流 + 技术面 → 多因子评分 → Top N
"""

from __future__ import annotations

import traceback
from datetime import datetime, timedelta
from typing import Any

from aipha.engines.lianban import (
    _safe_float,
    _safe_int,
)
from aipha.engines.market_sentiment import sentiment_score

from data_providers.sector_data import (
    fetch_fund_flow_multi,
    fetch_sector_members_multi,
    fetch_zt_pool_multi,
)

from .base import BaseScreener, ScreenerResult, register_screener


def _prev_trading_date() -> str:
    """Return the most recent trading date (skip weekends)."""
    today = datetime.now()
    # If before 15:30, use yesterday; otherwise use today
    if today.hour < 15 or (today.hour == 15 and today.minute < 30):
        d = today - timedelta(days=1)
    else:
        d = today
    # Skip weekends
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def _fetch_sector_members(industry: str) -> list[dict[str, Any]]:
    """Fetch all stocks in an industry board via multi-source cascade.

    Uses: XTQuant bridge → Eastmoney direct → AKShare → Tushare.
    Returns list of {code, name, changePct, marketCap, pe, pb}.
    """
    members, source = fetch_sector_members_multi(industry)
    return members


def _build_sector_members_from_universe(
    sector: str,
    zt_stocks_in_sector: list[dict],
    stock_data: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fallback: build sector member list from the stock_data universe.

    When AKShare board API is unavailable, we use a heuristic: look at
    all stocks in stock_data and consider them potential sector members.
    This is less precise but ensures the screener always returns results.

    Returns compatible member list.
    """
    members: list[dict[str, Any]] = []
    for code, data in stock_data.items():
        name = data.get("name", "")
        members.append({
            "code": code,
            "name": name,
            "changePct": _safe_float(data.get("changePct", data.get("change_pct", 0))),
            "price": _safe_float(data.get("price", data.get("trade", 0))),
            "marketCap": _safe_float(data.get("totalMarketCap", data.get("mktcap", 0))),
            "turnoverRate": _safe_float(data.get("turnoverRate", data.get("turnoverratio", 0))),
            "pe": _safe_float(data.get("pe", data.get("per", 0))),
            "pb": _safe_float(data.get("pb", data.get("pbnewmrq", 0))),
            "amount": _safe_float(data.get("amount", 0)),
        })
    return members


def _fetch_concept_boards_for_stock(code: str) -> list[str]:
    """Find concept boards that contain a given stock code.

    Uses the hot concept board list and checks membership.
    Returns list of concept board names.
    """
    # This is intentionally lightweight — we don't enumerate all concepts,
    # just check if the stock appears in trending concept boards.
    return []


def _fetch_hot_concept_boards(limit: int = 30) -> list[dict[str, Any]]:
    """Fetch today's hot concept boards ranked by change %.

    Tries Eastmoney direct first, falls back to AKShare.
    Returns [{name, changePct, leaderCode, leaderName}, ...].
    """
    # Try Eastmoney direct (concept boards: m:90+t:3)
    try:
        from aipha.data.providers.sector_data import _em_fetch_json

        data = _em_fetch_json(
            "https://push2.eastmoney.com/api/qt/clist/get",
            {
                "fid": "f3", "po": "1", "pz": str(limit), "pn": "1",
                "np": "1", "fltt": "2", "invt": "2",
                "fs": "m:90+t:3",
                "fields": "f14,f3,f128,f140",
            },
        )
        items = data.get("data", {}).get("diff", [])
        if items:
            boards: list[dict[str, Any]] = []
            for item in items:
                boards.append({
                    "name": str(item.get("f14", "")),
                    "changePct": _safe_float(item.get("f3")),
                    "leaderCode": str(item.get("f140", "")),
                    "leaderName": str(item.get("f128", "")),
                    "totalMarketCap": 0,
                })
            return boards
    except Exception:
        pass

    # Fallback to AKShare
    try:
        import akshare as ak

        df = ak.stock_board_concept_name_em()
        if df is None or df.empty:
            return []
        if "涨跌幅" in df.columns:
            df = df.sort_values("涨跌幅", ascending=False)
        boards = []
        for _, row in df.head(limit).iterrows():
            boards.append({
                "name": str(row.get("板块名称", "")),
                "changePct": _safe_float(row.get("涨跌幅")),
                "leaderCode": str(row.get("领涨股票代码", "")),
                "leaderName": str(row.get("领涨股票", "")),
                "totalMarketCap": _safe_float(row.get("总市值")),
            })
        return boards
    except Exception:
        return []


def _fetch_concept_members(concept: str) -> list[dict[str, Any]]:
    """Fetch all stocks in a concept board.

    Tries Eastmoney direct first, falls back to AKShare.
    Returns same structure as _fetch_sector_members.
    """
    # Try Eastmoney direct first (concept boards use m:90+t:3 filter)
    try:
        from aipha.data.providers.sector_data import _em_fetch_json, _safe_float as _sf

        # Resolve concept board code
        data = _em_fetch_json(
            "https://push2.eastmoney.com/api/qt/clist/get",
            {
                "fid": "f3", "po": "1", "pz": "500", "pn": "1",
                "np": "1", "fltt": "2", "invt": "2",
                "fs": "m:90+t:3",  # concept boards
                "fields": "f12,f14",
            },
        )
        bk_code = ""
        for item in data.get("data", {}).get("diff", []):
            name = str(item.get("f14", ""))
            if name == concept or concept in name or name in concept:
                bk_code = str(item.get("f12", ""))
                break
        if bk_code:
            data = _em_fetch_json(
                "https://push2.eastmoney.com/api/qt/clist/get",
                {
                    "fid": "f3", "po": "1", "pz": "500", "pn": "1",
                    "np": "1", "fltt": "2", "invt": "2",
                    "fs": f"b:{bk_code}+f:!50",
                    "fields": "f12,f14,f3,f2,f20,f8,f9,f23,f6",
                },
            )
            items = data.get("data", {}).get("diff", [])
            if items:
                return [
                    {
                        "code": str(it.get("f12", "")),
                        "name": str(it.get("f14", "")),
                        "changePct": _safe_float(it.get("f3")),
                        "price": _safe_float(it.get("f2")),
                        "marketCap": _safe_float(it.get("f20")),
                        "turnoverRate": _safe_float(it.get("f8")),
                        "pe": _safe_float(it.get("f9")),
                        "pb": _safe_float(it.get("f23")),
                        "amount": _safe_float(it.get("f6")),
                    }
                    for it in items
                ]
    except Exception:
        pass

    # Fallback to AKShare
    try:
        import akshare as ak

        df = ak.stock_board_concept_cons_em(symbol=concept)
        if df is None or df.empty:
            return []
        members: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            members.append({
                "code": str(row.get("代码", "")),
                "name": str(row.get("名称", "")),
                "changePct": _safe_float(row.get("涨跌幅")),
                "price": _safe_float(row.get("最新价")),
                "marketCap": _safe_float(row.get("总市值")),
                "turnoverRate": _safe_float(row.get("换手率")),
                "pe": _safe_float(row.get("市盈率-动态")),
                "pb": _safe_float(row.get("市净率")),
                "amount": _safe_float(row.get("成交额")),
            })
        return members
    except Exception:
        return []


def _fetch_fund_flow_rank() -> dict[str, dict[str, Any]]:
    """Fetch today's individual stock fund flow ranking via multi-source cascade.

    Returns {code: {mainNetIn, mainNetInPct, ...}}.
    """
    flow_map, source = fetch_fund_flow_multi()
    return flow_map


@register_screener
class SectorHotspotScreener(BaseScreener):
    """板块热点竞价候选选股器。

    通过昨日涨停池识别热点板块，筛选同板块中未涨停的候选股，
    多因子评分后输出竞价买入候选。
    """

    screener_id = "sector_hotspot"
    screener_name = "板块热点竞价候选"
    screener_description = (
        "基于昨日涨停池识别热点板块，筛选同板块候选股。"
        "核心逻辑：最热的龙头抢不上(涨停/一字板)，选同板块中尚未涨停的候选。"
        "多因子评分：板块热度(25%)、昨日表现(20%)、资金流向(20%)、"
        "技术面(15%)、市值适配(10%)、市场情绪(10%)。集合竞价介入。"
    )
    supported_markets = ["cn_stock"]

    _DEFAULTS: dict[str, Any] = {
        # Hotspot detection
        "min_sector_zt": 2,            # Min limit-up stocks to be a hotspot
        "use_concept_boards": True,    # Also scan concept boards for hotspots
        "concept_min_change_pct": 3.0, # Concept board must be up ≥3% to count
        "concept_max_boards": 5,       # Max concept boards to scan
        # Candidate filters
        "change_pct_min": -2.0,        # Min yesterday change % (not too weak)
        "change_pct_max": 7.0,         # Max yesterday change % (not already limit-up)
        "market_cap_min": 20e8,        # Min market cap (20亿)
        "market_cap_max": 500e8,       # Max market cap (500亿)
        "exclude_st": True,            # Exclude ST stocks
        "min_score": 40,               # Minimum total score to include
        # Scoring weights (sum = 100)
        "w_sector_heat": 25.0,         # Sector heat (板块热度)
        "w_yesterday_perf": 20.0,      # Yesterday performance (昨日表现)
        "w_fund_flow": 20.0,           # Capital flow (资金流向)
        "w_technical": 15.0,           # Technical alignment (技术面)
        "w_market_cap": 10.0,          # Market cap fit (市值适配)
        "w_environment": 10.0,         # Market environment (市场情绪)
        # Optimal ranges
        "optimal_cap_min": 30e8,       # Optimal market cap low (30亿, 游资偏好)
        "optimal_cap_max": 150e8,      # Optimal market cap high (150亿)
        "optimal_change_min": 3.0,     # Optimal yesterday change % low
        "optimal_change_max": 7.0,     # Optimal yesterday change % high
    }

    def screen(
        self,
        stock_data: dict[str, dict[str, Any]],
        market: str = "cn_stock",
        limit: int = 20,
    ) -> list[ScreenerResult]:
        """Run sector hotspot screening.

        Data sources cascade: XTQuant bridge → Eastmoney direct → AKShare → Tushare.
        stock_data may contain supplementary klines for technical scoring.
        """
        params = {**self._DEFAULTS, **self.params}
        results: list[ScreenerResult] = []
        warnings: list[str] = []

        # ── Step 1: Get yesterday's limit-up pool (multi-source) ──
        prev_date = _prev_trading_date()
        zt_pool = fetch_zt_pool_multi(prev_date, use_previous=True)

        if zt_pool.get("error"):
            warnings.append(f"昨日涨停池: {zt_pool['error']}")

        zt_stocks = zt_pool.get("stocks", [])
        if not zt_stocks:
            # Fallback: try today's pool (market might still be open)
            zt_pool = fetch_zt_pool_multi(prev_date, use_previous=False)
            zt_stocks = zt_pool.get("stocks", [])
            if not zt_stocks:
                return []

        # Build set of yesterday's limit-up codes (to exclude)
        zt_codes = {s["code"] for s in zt_stocks}

        # ── Step 2: Group by sector, identify hotspots ──
        sector_groups: dict[str, list[dict]] = {}
        for s in zt_stocks:
            ind = s.get("industry", "")
            if not ind:
                continue
            sector_groups.setdefault(ind, []).append(s)

        min_zt = int(params["min_sector_zt"])
        hotspots: dict[str, list[dict]] = {
            sector: stocks
            for sector, stocks in sector_groups.items()
            if len(stocks) >= min_zt
        }

        # ── Step 2b: Concept board hotspot detection (补充概念板块) ──
        concept_hotspots: dict[str, list[str]] = {}  # concept_name → [zt_code, ...]
        if params.get("use_concept_boards"):
            try:
                hot_concepts = _fetch_hot_concept_boards(
                    limit=params.get("concept_max_boards", 5),
                )
                min_chg = params.get("concept_min_change_pct", 3.0)
                for cb in hot_concepts:
                    cb_name = cb.get("name", "")
                    cb_chg = cb.get("changePct", 0)
                    if not cb_name or cb_chg < min_chg:
                        continue
                    # Check how many ZT stocks belong to this concept
                    concept_members = _fetch_concept_members(cb_name)
                    cm_codes = {m["code"] for m in concept_members}
                    overlap = zt_codes & cm_codes
                    if len(overlap) >= min_zt:
                        concept_hotspots[cb_name] = list(overlap)
            except Exception:
                pass  # concept board is supplementary, don't fail

        if not hotspots and not concept_hotspots:
            return []

        # Sort hotspots by ZT count descending
        hotspot_order = sorted(hotspots.keys(), key=lambda s: len(hotspots[s]), reverse=True)

        # Build leader info for each hotspot (龙头信息: which stocks are limit-up)
        sector_leaders: dict[str, list[dict]] = {}
        for sector, zt_list in hotspots.items():
            leaders = sorted(zt_list, key=lambda s: _safe_int(s.get("lian_ban_count")), reverse=True)
            sector_leaders[sector] = [
                {"code": s["code"], "name": s.get("name", ""), "boards": _safe_int(s.get("lian_ban_count"))}
                for s in leaders[:3]
            ]

        # ── Step 3: Get market sentiment ──
        env = sentiment_score()
        env_score_raw = env.get("score", 50)

        # ── Step 4: Get fund flow data (once for all candidates) ──
        fund_flow_map = _fetch_fund_flow_rank()

        # ── Step 5: Fetch sector members and score candidates ──
        seen_codes: set[str] = set()

        for sector in hotspot_order:
            zt_list = hotspots[sector]
            sector_zt_count = len(zt_list)

            # Fetch all stocks in this sector (multi-source cascade)
            members, source = fetch_sector_members_multi(sector, stock_data)
            if not members:
                continue
            if source != "universe_fallback":
                # Log which data source was used
                print(
                    f"[sector_hotspot] {sector}: {len(members)} members via {source}",
                    file=__import__("sys").stderr,
                )

            for m in members:
                code = m.get("code", "")
                if not code or code in seen_codes:
                    continue
                seen_codes.add(code)

                name = m.get("name", "")

                # ── Exclusion filters ──
                # Skip yesterday's limit-up stocks (can't buy them)
                if code in zt_codes:
                    continue

                # Skip ST stocks
                if params["exclude_st"] and ("ST" in name.upper() or "*ST" in name.upper()):
                    continue

                # Market cap filter
                cap = m.get("marketCap", 0)
                if cap > 0 and (cap < params["market_cap_min"] or cap > params["market_cap_max"]):
                    continue

                # Yesterday change filter
                change_pct = m.get("changePct", 0)
                if change_pct < params["change_pct_min"] or change_pct > params["change_pct_max"]:
                    continue

                # ── Multi-factor scoring ──
                score = 0.0
                factors: dict[str, Any] = {
                    "sector": sector,
                    "sectorZtCount": sector_zt_count,
                    "price": m.get("price", 0),
                    "changePct": change_pct,
                }
                signals: list[str] = []

                # Factor 1: Sector heat (25 pts)
                heat_score = self._score_sector_heat(
                    sector, sector_zt_count, hotspots, code, params,
                )
                score += heat_score
                factors["sectorHeatScore"] = round(heat_score, 1)
                # Show sector leader info (the stocks you can't buy)
                leaders = sector_leaders.get(sector, [])
                if leaders:
                    leader_desc = ", ".join(
                        f"{l['name']}({l['boards']}板)" if l["boards"] > 1 else l["name"]
                        for l in leaders[:2]
                    )
                    signals.append(
                        f"热点板块: {sector}({sector_zt_count}家涨停, 龙头: {leader_desc})"
                    )
                    factors["sectorLeaders"] = leaders
                else:
                    signals.append(
                        f"热点板块: {sector}({sector_zt_count}家涨停)"
                    )

                # Factor 2: Yesterday's performance (20 pts)
                perf_score, perf_signals = self._score_yesterday_perf(
                    change_pct, m.get("turnoverRate", 0), params,
                )
                score += perf_score
                factors["yesterdayPerfScore"] = round(perf_score, 1)
                signals.extend(perf_signals)

                # Factor 3: Fund flow (20 pts)
                flow_data = fund_flow_map.get(code, {})
                flow_score, flow_signals = self._score_fund_flow(flow_data, params)
                score += flow_score
                factors["fundFlowScore"] = round(flow_score, 1)
                if flow_data.get("mainNetIn"):
                    factors["mainNetIn"] = flow_data["mainNetIn"]
                signals.extend(flow_signals)

                # Factor 4: Technical (15 pts)
                extra = stock_data.get(code, {})
                klines = extra.get("klines", [])
                tech_score, tech_signals = self._score_technical(klines, params)
                score += tech_score
                factors["technicalScore"] = round(tech_score, 1)
                signals.extend(tech_signals)

                # Factor 5: Market cap fit (10 pts)
                cap_score = self._score_market_cap(cap, params)
                score += cap_score
                factors["marketCapScore"] = round(cap_score, 1)
                if cap > 0:
                    factors["marketCapYi"] = round(cap / 1e8, 1)

                # Factor 6: Market environment (10 pts)
                env_factor = (env_score_raw / 100) * params["w_environment"]
                score += env_factor
                factors["environmentScore"] = round(env_factor, 1)

                # Check if any other hotspot sectors also contain this stock
                multi_sector = self._check_multi_sector(code, hotspots, sector)
                if multi_sector:
                    bonus = min(len(multi_sector) * 3, 10)
                    score += bonus
                    factors["multiSectorBonus"] = bonus
                    factors["multiSectors"] = multi_sector
                    signals.append(f"多板块共振: {', '.join(multi_sector)}")

                total = min(score, 100)
                factors["totalScore"] = round(total, 1)

                # Add entry/exit strategy hints
                signals.append("买入: 集合竞价或开盘5分钟内介入")
                self._add_exit_hints(signals, factors, env_score_raw)

                if total >= params["min_score"]:
                    results.append(ScreenerResult(
                        stock_id=code,
                        name=name,
                        market=market,
                        score=total,
                        factors=factors,
                        signals=signals,
                    ))

        # ── Step 6: Scan concept board hotspots for additional candidates ──
        for concept_name, overlap_codes in concept_hotspots.items():
            concept_zt_count = len(overlap_codes)
            members = _fetch_concept_members(concept_name)
            if not members:
                continue

            for m in members:
                code = m.get("code", "")
                if not code or code in seen_codes:
                    continue
                seen_codes.add(code)

                name = m.get("name", "")
                if code in zt_codes:
                    continue
                if params["exclude_st"] and ("ST" in name.upper() or "*ST" in name.upper()):
                    continue
                cap = m.get("marketCap", 0)
                if cap > 0 and (cap < params["market_cap_min"] or cap > params["market_cap_max"]):
                    continue
                change_pct = m.get("changePct", 0)
                if change_pct < params["change_pct_min"] or change_pct > params["change_pct_max"]:
                    continue

                score = 0.0
                factors: dict[str, Any] = {
                    "sector": concept_name,
                    "sectorType": "concept",
                    "sectorZtCount": concept_zt_count,
                    "price": m.get("price", 0),
                    "changePct": change_pct,
                }
                signals: list[str] = []

                # Score concept heat similarly to industry heat
                heat_score = self._score_sector_heat(
                    concept_name, concept_zt_count, {concept_name: [{"code": c} for c in overlap_codes]}, code, params,
                )
                score += heat_score
                factors["sectorHeatScore"] = round(heat_score, 1)
                signals.append(f"概念热点: {concept_name}({concept_zt_count}家涨停)")

                perf_score, perf_signals = self._score_yesterday_perf(
                    change_pct, m.get("turnoverRate", 0), params,
                )
                score += perf_score
                factors["yesterdayPerfScore"] = round(perf_score, 1)
                signals.extend(perf_signals)

                flow_data = fund_flow_map.get(code, {})
                flow_score, flow_signals = self._score_fund_flow(flow_data, params)
                score += flow_score
                factors["fundFlowScore"] = round(flow_score, 1)
                if flow_data.get("mainNetIn"):
                    factors["mainNetIn"] = flow_data["mainNetIn"]
                signals.extend(flow_signals)

                extra = stock_data.get(code, {})
                klines = extra.get("klines", [])
                tech_score, tech_signals = self._score_technical(klines, params)
                score += tech_score
                factors["technicalScore"] = round(tech_score, 1)
                signals.extend(tech_signals)

                cap_score = self._score_market_cap(cap, params)
                score += cap_score
                factors["marketCapScore"] = round(cap_score, 1)
                if cap > 0:
                    factors["marketCapYi"] = round(cap / 1e8, 1)

                env_factor = (env_score_raw / 100) * params["w_environment"]
                score += env_factor
                factors["environmentScore"] = round(env_factor, 1)

                total = min(score, 100)
                factors["totalScore"] = round(total, 1)
                signals.append("买入: 集合竞价或开盘5分钟内介入")
                self._add_exit_hints(signals, factors, env_score_raw)

                if total >= params["min_score"]:
                    results.append(ScreenerResult(
                        stock_id=code,
                        name=name,
                        market=market,
                        score=total,
                        factors=factors,
                        signals=signals,
                    ))

        # Sort and rank
        results.sort(key=lambda r: r.score, reverse=True)
        for i, r in enumerate(results[:limit]):
            r.rank = i + 1
        return results[:limit]

    # ── Scoring sub-methods ──────────────────────────────────────

    def _score_sector_heat(
        self,
        sector: str,
        sector_zt_count: int,
        hotspots: dict[str, list[dict]],
        code: str,
        params: dict[str, Any],
    ) -> float:
        """Score sector hotness by limit-up density (0 to w_sector_heat)."""
        w = params["w_sector_heat"]

        if sector_zt_count >= 5:
            base = w * 1.0
        elif sector_zt_count >= 4:
            base = w * 0.85
        elif sector_zt_count >= 3:
            base = w * 0.7
        elif sector_zt_count >= 2:
            base = w * 0.5
        else:
            base = w * 0.2

        # Bonus if sector has high-board stocks (连板高度)
        max_board = max(
            (_safe_int(s.get("lian_ban_count")) for s in hotspots.get(sector, [])),
            default=0,
        )
        if max_board >= 3:
            base = min(base + w * 0.15, w)
        elif max_board >= 2:
            base = min(base + w * 0.08, w)

        return base

    def _score_yesterday_perf(
        self, change_pct: float, turnover_rate: float, params: dict[str, Any],
    ) -> tuple[float, list[str]]:
        """Score yesterday's stock performance (0 to w_yesterday_perf).

        Sweet spot: 3-7% rise (following the trend but not yet limit-up).
        """
        w = params["w_yesterday_perf"]
        signals: list[str] = []
        opt_min = params["optimal_change_min"]
        opt_max = params["optimal_change_max"]

        # Change % scoring
        if opt_min <= change_pct <= opt_max:
            # Optimal range: stock followed the sector trend
            pct_score = w * 0.7
            signals.append(f"昨日涨{change_pct:.1f}%(跟涨未涨停)")
        elif 1.0 <= change_pct < opt_min:
            pct_score = w * 0.4
            signals.append(f"昨日涨{change_pct:.1f}%(微涨)")
        elif 0 <= change_pct < 1.0:
            pct_score = w * 0.2
        elif change_pct < 0:
            pct_score = w * 0.1
            signals.append(f"昨日跌{change_pct:.1f}%(逆势)")
        else:
            pct_score = w * 0.5

        # Turnover bonus (active trading is good)
        if 5 <= turnover_rate <= 15:
            pct_score = min(pct_score + w * 0.3, w)
            signals.append(f"换手率{turnover_rate:.1f}%(活跃)")
        elif 3 <= turnover_rate < 5 or 15 < turnover_rate <= 25:
            pct_score = min(pct_score + w * 0.15, w)
        elif turnover_rate > 25:
            signals.append(f"换手率{turnover_rate:.1f}%(过高)")

        return pct_score, signals

    def _score_fund_flow(
        self, flow_data: dict[str, Any], params: dict[str, Any],
    ) -> tuple[float, list[str]]:
        """Score capital flow (0 to w_fund_flow)."""
        w = params["w_fund_flow"]
        signals: list[str] = []

        main_net = flow_data.get("mainNetIn", 0)
        main_pct = flow_data.get("mainNetInPct", 0)

        if not flow_data:
            # No flow data available, give partial credit
            return w * 0.3, []

        if main_net > 0:
            # Positive main force inflow
            ratio = min(abs(main_pct) / 5.0, 1.0)
            score = w * (0.4 + 0.6 * ratio)
            net_yi = main_net / 1e4 if abs(main_net) > 1e4 else main_net
            signals.append(f"主力净流入{net_yi:.0f}万, 占比{main_pct:.1f}%")
            return score, signals
        else:
            # Main force outflow — penalty but don't disqualify
            score = w * 0.15
            return score, signals

    def _score_technical(
        self, klines: list[dict], params: dict[str, Any],
    ) -> tuple[float, list[str]]:
        """Score technical alignment from K-line data (0 to w_technical)."""
        w = params["w_technical"]
        signals: list[str] = []

        if not klines or len(klines) < 5:
            return w * 0.3, []

        closes = [_safe_float(b.get("close")) for b in klines]
        closes = [c for c in closes if c > 0]
        if len(closes) < 5:
            return w * 0.3, []

        last = closes[-1]
        score = 0.0

        # MA alignment: close > MA5 > MA10 > MA20
        ma5 = sum(closes[-5:]) / 5
        if len(closes) >= 10:
            ma10 = sum(closes[-10:]) / 10
            if len(closes) >= 20:
                ma20 = sum(closes[-20:]) / 20
                if last > ma5 > ma10 > ma20:
                    score += w * 0.4
                    signals.append("均线多头排列(5>10>20)")
                elif last > ma5 > ma10:
                    score += w * 0.25
                    signals.append("均线多头(5>10)")
                elif last > ma5:
                    score += w * 0.1
            elif last > ma5 > ma10:
                score += w * 0.25
                signals.append("均线多头(5>10)")
            elif last > ma5:
                score += w * 0.1
        elif last > ma5:
            score += w * 0.1

        # 5-day return: moderate rise is good
        if len(closes) >= 6 and closes[-6] > 0:
            ret5 = (last - closes[-6]) / closes[-6]
            if 0.02 <= ret5 <= 0.15:
                score += w * 0.25
                signals.append(f"5日涨幅{ret5:.1%}")

        # Volume trend (increasing)
        volumes = [_safe_float(b.get("volume")) for b in klines[-5:]]
        volumes = [v for v in volumes if v > 0]
        if len(volumes) >= 3:
            if volumes[-1] > volumes[-2] > volumes[-3]:
                score += w * 0.25
                signals.append("成交量连续放大")
            # Volume ratio (today vs 5-day avg)
            avg_vol = sum(volumes) / len(volumes)
            if avg_vol > 0 and volumes[-1] / avg_vol > 1.2:
                score += w * 0.1

        return min(score, w), signals

    def _score_market_cap(self, cap: float, params: dict[str, Any]) -> float:
        """Score market cap fit for 游资 preference (0 to w_market_cap)."""
        w = params["w_market_cap"]
        if cap <= 0:
            return w * 0.3  # Unknown market cap, partial credit

        opt_min = params["optimal_cap_min"]
        opt_max = params["optimal_cap_max"]

        if opt_min <= cap <= opt_max:
            return w  # Perfect range
        elif cap < opt_min:
            # Too small but still acceptable
            ratio = cap / opt_min
            return w * max(ratio, 0.3)
        else:
            # Too large, less attractive for 游资
            ratio = opt_max / cap
            return w * max(ratio, 0.2)

    @staticmethod
    def _add_exit_hints(
        signals: list[str], factors: dict[str, Any], env_score: float,
    ) -> None:
        """Append actionable exit strategy hints based on market environment."""
        hints: list[str] = []
        # 4-layer exit framework (from auction_strategy)
        hints.append("止损: 开盘跳空低开>3%且继续下探>2%立即清仓")
        if env_score >= 60:
            hints.append("止盈: 涨幅>15%减半仓, 破MA5清仓")
            factors["exitMode"] = "积极"
        else:
            hints.append("止盈: 涨幅>10%减半仓, 14:55未创新高清仓")
            factors["exitMode"] = "保守"
        hints.append("持仓: 最多持有3天, 第3天尾盘无条件清仓")
        factors["exitHints"] = hints
        signals.extend([f"退出策略: {h}" for h in hints])

    def _check_multi_sector(
        self, code: str, hotspots: dict[str, list[dict]], primary_sector: str,
    ) -> list[str]:
        """Check if a stock appears in multiple hotspot sectors."""
        extra_sectors: list[str] = []
        for sector, stocks in hotspots.items():
            if sector == primary_sector:
                continue
            for s in stocks:
                if s.get("code") == code:
                    extra_sectors.append(sector)
                    break
        return extra_sectors
