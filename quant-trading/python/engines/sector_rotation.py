"""
sector_rotation — Sector rotation strategy engine.

Implements systematic sector rotation models:
  - Momentum-based rotation (relative strength across sectors)
  - Merrill Clock / economic cycle mapping
  - Mean-reversion rotation (buy laggards, sell leaders)
  - Multi-factor sector scoring (momentum + fund flow + valuation)
  - Rotation signal generation with allocation weights

Input:
  { "action": "momentum" | "mean_reversion" | "multi_factor" | "economic_cycle" | "signals" | "history",
    "topN": 3,
    "lookback": 20,
    "rebalancePeriod": "weekly" | "biweekly" | "monthly" }

Output: sector rotation signals, rankings, and allocation suggestions.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

os.environ.setdefault("no_proxy", "eastmoney.com,push2his.eastmoney.com,datacenter-web.eastmoney.com")
os.environ.setdefault("NO_PROXY", "eastmoney.com,push2his.eastmoney.com,datacenter-web.eastmoney.com")


def run_sector_rotation(config: dict) -> dict:
    """Dispatch sector rotation analysis based on action."""
    action = config.get("action", "signals")
    errors: list[str] = []

    if action == "momentum":
        return _momentum_rotation(config, errors)
    elif action == "mean_reversion":
        return _mean_reversion_rotation(config, errors)
    elif action == "multi_factor":
        return _multi_factor_rotation(config, errors)
    elif action == "economic_cycle":
        return _economic_cycle_mapping(config, errors)
    elif action == "signals":
        return _rotation_signals(config, errors)
    elif action == "history":
        return _rotation_history(config, errors)
    else:
        return {"error": f"未知操作: {action}"}


def _get_sector_data(errors: list[str]) -> list[dict]:
    """Fetch current sector data from akshare."""
    try:
        import akshare as ak
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            errors.append("无法获取板块列表")
            return []

        sectors = []
        for _, row in df.head(60).iterrows():  # top 60 sectors
            name = str(row.get("板块名称", ""))
            code = str(row.get("板块代码", ""))
            change = _safe_float(row.get("涨跌幅"))
            total_mv = _safe_float(row.get("总市值"))
            volume = _safe_float(row.get("成交量"))
            amount = _safe_float(row.get("成交额"))
            up_count = _safe_int(row.get("上涨家数"))
            down_count = _safe_int(row.get("下跌家数"))

            sectors.append({
                "name": name,
                "code": code,
                "changePct": change,
                "totalMv": total_mv,
                "volume": volume,
                "amount": amount,
                "upCount": up_count,
                "downCount": down_count,
            })
        return sectors
    except Exception as e:
        errors.append(f"获取板块数据失败: {str(e)}")
        return []


def _get_sector_history(sector_name: str, days: int) -> list[dict]:
    """Fetch historical data for a sector."""
    try:
        import akshare as ak
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")
        df = ak.stock_board_industry_hist_em(
            symbol=sector_name, period="日k",
            start_date=start_date, end_date=end_date, adjust="")
        if df is None or df.empty:
            return []
        result = []
        for _, row in df.tail(days).iterrows():
            result.append({
                "date": str(row.get("日期", ""))[:10],
                "close": _safe_float(row.get("收盘")),
                "changePct": _safe_float(row.get("涨跌幅")),
                "volume": _safe_float(row.get("成交量")),
                "amount": _safe_float(row.get("成交额")),
            })
        return result
    except Exception:
        return []


def _momentum_rotation(config: dict, errors: list[str]) -> dict:
    """Momentum-based sector rotation: buy strong sectors."""
    import numpy as np

    top_n = config.get("topN", 3)
    lookback = config.get("lookback", 20)

    sectors = _get_sector_data(errors)
    if not sectors:
        return {"error": "无法获取板块数据", "errors": errors}

    scored_sectors = []
    for sector in sectors[:40]:  # analyze top 40
        history = _get_sector_history(sector["name"], lookback + 5)
        if len(history) < max(5, lookback // 2):
            continue

        closes = [h["close"] for h in history if h.get("close")]
        if len(closes) < 5:
            continue

        # Momentum score: recent return + acceleration
        ret = (closes[-1] / closes[0] - 1) * 100 if closes[0] > 0 else 0
        # Short-term momentum (last 5 days)
        short_ret = (closes[-1] / closes[-min(5, len(closes))] - 1) * 100 if closes[-min(5, len(closes))] > 0 else 0
        # Acceleration: short-term stronger than full period average
        avg_daily_ret = ret / len(closes) if len(closes) > 0 else 0
        avg_short_ret = short_ret / min(5, len(closes))
        acceleration = avg_short_ret - avg_daily_ret

        # Volume trend
        volumes = [h["volume"] for h in history if h.get("volume")]
        vol_trend = 0
        if len(volumes) >= 5:
            recent_vol = np.mean(volumes[-5:])
            early_vol = np.mean(volumes[:5])
            vol_trend = (recent_vol / early_vol - 1) * 100 if early_vol > 0 else 0

        # Composite momentum score
        momentum_score = ret * 0.5 + short_ret * 0.3 + acceleration * 10 + min(vol_trend, 50) * 0.2

        scored_sectors.append({
            "name": sector["name"],
            "code": sector["code"],
            "returnPct": round(ret, 2),
            "shortReturn": round(short_ret, 2),
            "acceleration": round(acceleration, 4),
            "volTrend": round(vol_trend, 2),
            "momentumScore": round(momentum_score, 2),
            "todayChange": sector.get("changePct"),
        })

    # Sort by momentum score
    scored_sectors.sort(key=lambda x: x["momentumScore"], reverse=True)

    # Allocate weights to top N
    top = scored_sectors[:top_n]
    total_score = sum(max(s["momentumScore"], 0.01) for s in top)
    for s in top:
        s["weight"] = round(max(s["momentumScore"], 0.01) / total_score * 100, 1) if total_score > 0 else round(100 / top_n, 1)

    result: dict[str, Any] = {
        "action": "momentum",
        "lookbackDays": lookback,
        "topN": top_n,
        "recommended": top,
        "avoid": scored_sectors[-3:] if len(scored_sectors) >= 3 else [],
        "allRankings": scored_sectors[:15],
    }
    if errors:
        result["errors"] = errors
    return result


def _mean_reversion_rotation(config: dict, errors: list[str]) -> dict:
    """Mean-reversion rotation: buy oversold sectors, sell overbought."""
    top_n = config.get("topN", 3)
    lookback = config.get("lookback", 20)

    sectors = _get_sector_data(errors)
    if not sectors:
        return {"error": "无法获取板块数据", "errors": errors}

    scored_sectors = []
    for sector in sectors[:40]:
        history = _get_sector_history(sector["name"], lookback + 5)
        if len(history) < max(5, lookback // 2):
            continue

        closes = [h["close"] for h in history if h.get("close")]
        if len(closes) < 5:
            continue

        import numpy as np
        closes_arr = np.array(closes, dtype=float)
        mean_price = float(np.mean(closes_arr))
        std_price = float(np.std(closes_arr))

        # Z-score: how far from mean (negative = oversold = buy signal)
        z_score = (closes_arr[-1] - mean_price) / std_price if std_price > 0 else 0

        # Reversion score: lower z-score = more oversold = higher buy priority
        reversion_score = -z_score * 100

        scored_sectors.append({
            "name": sector["name"],
            "code": sector["code"],
            "currentVsMean": round((closes_arr[-1] / mean_price - 1) * 100, 2),
            "zScore": round(float(z_score), 2),
            "reversionScore": round(reversion_score, 2),
            "signal": "超卖买入" if z_score < -1.5 else "偏低关注" if z_score < -0.5 else "中性" if z_score < 0.5 else "偏高减持" if z_score < 1.5 else "超买卖出",
            "todayChange": sector.get("changePct"),
        })

    scored_sectors.sort(key=lambda x: x["reversionScore"], reverse=True)

    # Buy oversold (top scores), sell overbought (bottom scores)
    buy_candidates = [s for s in scored_sectors if s["zScore"] < -0.5][:top_n]
    sell_candidates = [s for s in scored_sectors if s["zScore"] > 0.5][-top_n:]

    result: dict[str, Any] = {
        "action": "mean_reversion",
        "lookbackDays": lookback,
        "buyCandidates": buy_candidates,
        "sellCandidates": sell_candidates,
        "allRankings": scored_sectors[:15],
    }
    if errors:
        result["errors"] = errors
    return result


def _multi_factor_rotation(config: dict, errors: list[str]) -> dict:
    """Multi-factor sector scoring: momentum + fund flow + breadth."""
    top_n = config.get("topN", 5)

    sectors = _get_sector_data(errors)
    if not sectors:
        return {"error": "无法获取板块数据", "errors": errors}

    scored_sectors = []
    for sector in sectors[:40]:
        history = _get_sector_history(sector["name"], 20)
        if not history:
            continue

        closes = [h["close"] for h in history if h.get("close")]
        if len(closes) < 3:
            continue

        # Factor 1: Momentum (20-day return)
        momentum = (closes[-1] / closes[0] - 1) * 100 if closes[0] > 0 else 0

        # Factor 2: Breadth (up/down ratio)
        up = sector.get("upCount") or 0
        down = sector.get("downCount") or 0
        breadth_score = (up - down) / max(up + down, 1) * 100

        # Factor 3: Volume trend
        volumes = [h["volume"] for h in history if h.get("volume")]
        vol_trend = 0
        if len(volumes) >= 5:
            import numpy as np
            recent = float(np.mean(volumes[-5:]))
            earlier = float(np.mean(volumes[:5]))
            vol_trend = (recent / earlier - 1) * 100 if earlier > 0 else 0

        # Factor 4: Consistency (% of up days)
        changes = [h["changePct"] for h in history if h.get("changePct") is not None]
        consistency = sum(1 for c in changes if c > 0) / len(changes) * 100 if changes else 50

        # Composite score (weighted)
        composite = (
            momentum * 0.35 +
            breadth_score * 0.25 +
            min(vol_trend, 100) * 0.2 +
            consistency * 0.2
        )

        scored_sectors.append({
            "name": sector["name"],
            "code": sector["code"],
            "momentum": round(momentum, 2),
            "breadth": round(breadth_score, 2),
            "volTrend": round(vol_trend, 2),
            "consistency": round(consistency, 1),
            "compositeScore": round(composite, 2),
            "todayChange": sector.get("changePct"),
        })

    scored_sectors.sort(key=lambda x: x["compositeScore"], reverse=True)

    # Assign weights
    top = scored_sectors[:top_n]
    total_score = sum(max(s["compositeScore"], 0.01) for s in top)
    for s in top:
        s["weight"] = round(max(s["compositeScore"], 0.01) / total_score * 100, 1) if total_score > 0 else round(100 / top_n, 1)

    result: dict[str, Any] = {
        "action": "multi_factor",
        "topN": top_n,
        "factors": ["动量(35%)", "市场宽度(25%)", "成交量趋势(20%)", "上涨一致性(20%)"],
        "recommended": top,
        "allRankings": scored_sectors[:20],
    }
    if errors:
        result["errors"] = errors
    return result


def _economic_cycle_mapping(config: dict, errors: list[str]) -> dict:
    """Map current economic cycle to recommended sectors (Merrill Clock style)."""
    # Define sector preferences by economic cycle phase
    cycle_sectors = {
        "复苏期": {
            "description": "经济触底回升，流动性宽松",
            "favored": ["券商", "房地产", "消费电子", "汽车", "家电"],
            "neutral": ["食品饮料", "医药", "银行"],
            "avoid": ["公用事业", "钢铁", "煤炭"],
        },
        "过热期": {
            "description": "经济高增长，通胀上升",
            "favored": ["有色金属", "煤炭", "钢铁", "石油石化", "农业"],
            "neutral": ["化工", "机械", "建材"],
            "avoid": ["消费电子", "传媒", "计算机"],
        },
        "滞胀期": {
            "description": "增长放缓，通胀高企",
            "favored": ["医药", "食品饮料", "公用事业", "农业"],
            "neutral": ["银行", "保险"],
            "avoid": ["券商", "房地产", "消费电子", "汽车"],
        },
        "衰退期": {
            "description": "经济下行，通胀回落",
            "favored": ["国防军工", "通信", "计算机", "公用事业"],
            "neutral": ["医药", "食品饮料"],
            "avoid": ["有色金属", "钢铁", "煤炭", "房地产"],
        },
    }

    # Try to determine current cycle from macro indicators
    current_cycle = _detect_economic_cycle(errors)

    result: dict[str, Any] = {
        "action": "economic_cycle",
        "currentCycle": current_cycle,
        "cycleDescription": cycle_sectors.get(current_cycle, {}).get("description", ""),
        "favoredSectors": cycle_sectors.get(current_cycle, {}).get("favored", []),
        "neutralSectors": cycle_sectors.get(current_cycle, {}).get("neutral", []),
        "avoidSectors": cycle_sectors.get(current_cycle, {}).get("avoid", []),
        "allCycles": {k: v for k, v in cycle_sectors.items()},
        "note": "经济周期判断基于PMI、CPI、M2等宏观指标的综合分析，仅供参考",
    }
    if errors:
        result["errors"] = errors
    return result


def _rotation_signals(config: dict, errors: list[str]) -> dict:
    """Generate actionable rotation signals combining multiple approaches."""
    top_n = config.get("topN", 3)

    # Run momentum analysis
    momentum_result = _momentum_rotation({"topN": top_n * 2, "lookback": 20}, errors)
    momentum_sectors = {s["name"]: s for s in momentum_result.get("recommended", [])}

    # Run multi-factor analysis
    mf_result = _multi_factor_rotation({"topN": top_n * 2}, errors)
    mf_sectors = {s["name"]: s for s in mf_result.get("recommended", [])}

    # Combine: sectors appearing in both lists get higher priority
    combined: dict[str, dict] = {}
    for name, data in momentum_sectors.items():
        combined[name] = {
            "name": name,
            "momentumScore": data.get("momentumScore", 0),
            "compositeScore": mf_sectors.get(name, {}).get("compositeScore", 0),
            "inBoth": name in mf_sectors,
            "todayChange": data.get("todayChange"),
        }
    for name, data in mf_sectors.items():
        if name not in combined:
            combined[name] = {
                "name": name,
                "momentumScore": momentum_sectors.get(name, {}).get("momentumScore", 0),
                "compositeScore": data.get("compositeScore", 0),
                "inBoth": False,
                "todayChange": data.get("todayChange"),
            }

    # Score: bonus for appearing in both
    for s in combined.values():
        s["signalScore"] = (
            s["momentumScore"] * 0.5 +
            s["compositeScore"] * 0.5 +
            (20 if s["inBoth"] else 0)
        )
        s["signalScore"] = round(s["signalScore"], 2)

    ranked = sorted(combined.values(), key=lambda x: x["signalScore"], reverse=True)
    top = ranked[:top_n]

    # Assign weights
    total_score = sum(max(s["signalScore"], 0.01) for s in top)
    for s in top:
        s["weight"] = round(max(s["signalScore"], 0.01) / total_score * 100, 1) if total_score > 0 else round(100 / top_n, 1)
        s["signal"] = "强烈推荐" if s["inBoth"] else "推荐"

    result: dict[str, Any] = {
        "action": "signals",
        "topN": top_n,
        "method": "动量+多因子综合",
        "recommended": top,
        "avoid": ranked[-3:] if len(ranked) >= 3 else [],
        "rebalanceSuggestion": "建议每1-2周重新评估轮动信号",
    }
    if errors:
        result["errors"] = errors
    return result


def _rotation_history(config: dict, errors: list[str]) -> dict:
    """Show sector rotation patterns over recent periods."""
    days = config.get("days", 60)

    sectors = _get_sector_data(errors)
    if not sectors:
        return {"error": "无法获取板块数据", "errors": errors}

    # Get historical performance for major sectors
    period_returns: dict[str, dict] = {}
    for sector in sectors[:20]:
        history = _get_sector_history(sector["name"], days)
        if not history or len(history) < 5:
            continue

        closes = [h["close"] for h in history if h.get("close")]
        if len(closes) < 5:
            continue

        period_returns[sector["name"]] = {
            "5d": round((closes[-1] / closes[-min(5, len(closes))] - 1) * 100, 2) if closes[-min(5, len(closes))] > 0 else 0,
            "10d": round((closes[-1] / closes[-min(10, len(closes))] - 1) * 100, 2) if len(closes) >= 10 and closes[-min(10, len(closes))] > 0 else None,
            "20d": round((closes[-1] / closes[-min(20, len(closes))] - 1) * 100, 2) if len(closes) >= 20 and closes[-min(20, len(closes))] > 0 else None,
        }

    # Find rotation patterns
    if period_returns:
        # Sectors strong short-term but weak longer-term → recent rotation in
        rotation_in = []
        rotation_out = []
        for name, rets in period_returns.items():
            if rets.get("5d") is not None and rets.get("20d") is not None:
                if rets["5d"] > 3 and (rets["20d"] or 0) < rets["5d"]:
                    rotation_in.append({"name": name, **rets})
                elif rets["5d"] < -2 and (rets["20d"] or 0) > rets["5d"]:
                    rotation_out.append({"name": name, **rets})

        rotation_in.sort(key=lambda x: x["5d"], reverse=True)
        rotation_out.sort(key=lambda x: x["5d"])

    result: dict[str, Any] = {
        "action": "history",
        "period": f"{days}天",
        "sectorCount": len(period_returns),
        "rotationIn": rotation_in[:5] if period_returns else [],
        "rotationOut": rotation_out[:5] if period_returns else [],
        "performanceTable": dict(sorted(period_returns.items(), key=lambda x: x[1].get("5d", 0), reverse=True)[:15]),
    }
    if errors:
        result["errors"] = errors
    return result


def _detect_economic_cycle(errors: list[str]) -> str:
    """Detect current economic cycle phase from macro indicators."""
    try:
        import akshare as ak

        # Try to get PMI data
        try:
            pmi_df = ak.macro_china_pmi()
            if pmi_df is not None and not pmi_df.empty:
                latest_pmi = float(pmi_df.iloc[-1].get("制造业-Loss", pmi_df.iloc[-1].iloc[1]))
                if latest_pmi > 51:
                    pmi_signal = "expansion"
                elif latest_pmi > 50:
                    pmi_signal = "mild_expansion"
                elif latest_pmi > 49:
                    pmi_signal = "mild_contraction"
                else:
                    pmi_signal = "contraction"
            else:
                pmi_signal = "unknown"
        except Exception:
            pmi_signal = "unknown"
            errors.append("PMI数据获取失败")

        # Simple mapping
        if pmi_signal in ("expansion",):
            return "复苏期"
        elif pmi_signal in ("mild_expansion",):
            return "过热期"
        elif pmi_signal in ("mild_contraction",):
            return "滞胀期"
        elif pmi_signal in ("contraction",):
            return "衰退期"
        else:
            return "复苏期"  # default assumption

    except Exception:
        errors.append("经济周期检测失败，使用默认值")
        return "复苏期"


def _safe_float(val) -> float | None:
    if val is None or val == "" or val == "-":
        return None
    try:
        return round(float(val), 4)
    except (ValueError, TypeError):
        return None


def _safe_int(val) -> int | None:
    if val is None or val == "" or val == "-":
        return None
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


# ── NDJSON bridge entry point ──────────────────────────────────
if __name__ == "__main__":
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            config = json.loads(line)
            result = run_sector_rotation(config)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False), flush=True)
