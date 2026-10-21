"""Read-only sector heat and rotation analysis using the existing data sources."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

from .sector_analysis import _sector_history, _sector_ranking


def run_sector_rotation(config: dict) -> dict:
    action = config.get("action", "rotation")
    if action not in ("hot", "rotation", "momentum"):
        return {"error": f"未知 action: {action}"}
    limit = max(1, min(int(config.get("limit", 20)), 50))
    days = max(2, min(int(config.get("days", 20)), 120))
    errors: list[str] = []
    ranking = _sector_ranking(500, errors)
    if ranking.get("error"):
        return {"action": action, **ranking}
    sectors = ranking["sectors"]
    metadata = {
        "source": "sina",
        "dataTime": None,
        "retrievedAt": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "note": "来源未提供行情时间；抓取时间不是行情时间。成交额不是主力净流入，资金需另查 fund_flow。",
    }
    if action == "hot":
        hot = sorted(
            (s for s in sectors if s.get("changePct") is not None and s["changePct"] > 0),
            key=lambda s: s["changePct"], reverse=True,
        )[:limit]
        # Sina supplies price/turnover amount but no comparable turnover rate
        # or rising-stock breadth. Do not invent a composite heat score.
        return {
            "action": action, "count": len(hot), "hotSectors": hot, "sectors": hot,
            "rankingBasis": "板块平均涨幅降序，非综合热度评分",
            "unavailableMetrics": ["turnoverRate", "upCount", "mainNetIn"],
            "errors": errors or None, **metadata,
        }

    # Bound historical I/O. Explicitly describe the candidate sample instead
    # of presenting it as an exhaustive market-wide historical ranking.
    candidates = [s for s in sectors if s.get("type") == "industry"][:min(limit, 20)]

    def analyze(sector: dict) -> tuple[dict | None, list[str]]:
        local_errors: list[str] = []
        try:
            history = _sector_history(sector["name"], days + 1, local_errors)
        except Exception as exc:
            return None, [f"{sector['name']}: 历史数据获取失败: {exc}"]
        rows = history.get("history", [])
        if history.get("error"):
            local_errors.append(f"{sector['name']}: {history['error']}")
        # Match prices and volumes by date; invalid prices must not silently
        # shorten the requested return window.
        rows = sorted(rows, key=lambda r: r.get("date", ""))[-(days + 1):]
        if (len(rows) != days + 1 or len({r.get("date") for r in rows}) != len(rows)
                or any(not r.get("date") or r.get("close") is None or r["close"] <= 0 for r in rows)):
            local_errors.append(f"{sector['name']}: 不足 {days + 1} 条有效日线，未计算 {days} 日收益")
            return None, local_errors
        closes = [r["close"] for r in rows]
        changes = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        mid = max(1, days // 2)
        early_daily = (closes[mid] / closes[0] - 1) * 100 / mid
        recent_daily = (closes[-1] / closes[mid] - 1) * 100 / (days - mid)
        volumes = [r.get("volume") for r in rows]
        volume_ratio = None
        if all(v is not None and v >= 0 for v in volumes) and sum(volumes[:mid]) > 0:
            volume_ratio = (sum(volumes[mid:]) / len(volumes[mid:])) / (sum(volumes[:mid]) / mid)
        item = {
            "name": sector["name"], "code": sector["code"],
            "nDayChange": round((closes[-1] / closes[0] - 1) * 100, 4),
            "oneDayChange": round(changes[-1] * 100, 4),
            "acceleration": round(recent_daily - early_daily, 4),
            "consistency": round(sum(c > 0 for c in changes) / days, 4),
            "volumeRatio": round(volume_ratio, 4) if volume_ratio is not None else None,
            "startDate": rows[0]["date"], "dataTime": rows[-1]["date"], "source": "eastmoney",
        }
        return item, local_errors

    with ThreadPoolExecutor(max_workers=4) as pool:
        analyzed = list(pool.map(analyze, candidates))
    items = []
    for item, local_errors in analyzed:
        errors.extend(local_errors)
        if item:
            items.append(item)
    if not items:
        return {"action": action, "error": "未取得足够的板块历史数据", "errors": errors, **metadata}
    # Compare only observations with the same date window.
    window = max((s["dataTime"], s["startDate"]) for s in items)
    aligned = [s for s in items if (s["dataTime"], s["startDate"]) == window]
    if len(aligned) != len(items):
        errors.append("日期区间不一致的板块已排除，避免跨期比较")
    items = aligned
    items.sort(key=lambda s: s["nDayChange"], reverse=True)
    result = {
        "action": action, "days": days, "count": len(items), "sectors": items,
        "scope": f"当日异动排名靠前的 {len(candidates)} 个行业板块，非全市场历史排名",
        "status": "partial" if errors else "complete", "errors": errors or None,
        **metadata, "source": "eastmoney", "dataTime": window[0],
        "note": "按来源日线日期比较收益，包含当日日线时可能尚未收盘。成交额和成交量均不是主力净流入。",
    }
    if action == "rotation":
        n = min(5, len(items) // 2)
        leading, lagging = (items[:n], items[-n:]) if n else (items, [])
        spread = (sum(s["nDayChange"] for s in leading) / n - sum(s["nDayChange"] for s in lagging) / n) if n else None
        result.update({
            "leading": leading, "lagging": lagging, "spread": round(spread, 4) if spread is not None else None,
            "rotationSignal": ("明显" if spread > 5 else "温和" if spread > 2 else "不明显") if spread is not None else "样本不足",
            "accelerating": [s for s in items if s["acceleration"] > 0],
            "decelerating": [s for s in items if s["acceleration"] < 0],
        })
    else:
        for item in items:
            if item["volumeRatio"] is None:
                item["momentumScore"] = None
                continue
            price = min(max(item["nDayChange"] + 5, 0), 10) * 4
            volume = min(max(item["volumeRatio"] - 0.5, 0), 1.5) * 20
            item["momentumScore"] = round(price + volume + item["consistency"] * 30, 2)
        items.sort(key=lambda s: s["momentumScore"] if s["momentumScore"] is not None else -1, reverse=True)
        result["scoringBasis"] = "价格40分、量能30分、上涨日占比30分；缺少成交量不计算综合分"
    return result
