"""
dragon_master — 真龙战法核心引擎 (主线龙头识别 + 龙一/龙二/龙三定位)

实现《真龙战法·终极完整版》的六步核心逻辑：
  第一步：确认大主线（板块≥10只涨停连续2天 + 有连板高度）
  第二步：确认龙一（首封、封单最大、最稳、第二天最早2连板）
  第三步：确认买入时机（3板打板=最佳买点、第一次放量）
  第四步：打板买法（涨停价封单确认后挂涨停价买入）
  第五步：买不到龙一→做同主线龙二、龙三
  第六步：一句万能口诀流程

Data sources:
  - akshare 涨停池 (盘后): first_board_time, seal_amount, lian_ban_count
  - miniQMT data_bridge (盘中实时): 通过 fetch_realtime_quote 接口获取

Used by: dragon_master_screener, dragon_master_strategy
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

# Proxy bypass
for _k in (
    "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
    "all_proxy", "ALL_PROXY",
):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

_ENGINES_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(_ENGINES_DIR)
sys.path.insert(0, _PYTHON_ROOT)

from aipha.engines.lianban import (
    _safe_float,
    _safe_int,
    classify_board_type,
    count_consecutive_boards,
    detect_limit_up,
    fetch_zt_pool,
    fetch_zt_pool_previous,
    is_one_word_board,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 第一步：确认【真正的大主线】
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def identify_major_theme(
    today_pool: dict[str, Any] | None = None,
    yesterday_pool: dict[str, Any] | None = None,
    *,
    min_zt_count: int = 10,
    min_consecutive_days: int = 2,
    min_board_height: int = 2,
) -> dict[str, Any]:
    """识别真正的大主线。

    条件（三条全满足 = 超级大主线，走5~15天甚至更久）：
      1. 板块涨停数 ≥ min_zt_count（默认10只）
      2. 连续 ≥ min_consecutive_days 天（默认2天）
      3. 有连板高度（出现了 ≥ min_board_height 连板的股票）

    Args:
        today_pool:    今日涨停池数据 (fetch_zt_pool 格式)
        yesterday_pool: 昨日涨停池数据 (fetch_zt_pool_previous 格式)
        min_zt_count:  板块内至少多少只涨停
        min_consecutive_days: 至少连续几天涨停
        min_board_height: 最低连板高度

    Returns:
        {
            "themes": [{
                "sector": str,
                "today_zt_count": int,
                "yesterday_zt_count": int,
                "consecutive_days": int,
                "max_board_height": int,         # 最高连板数
                "height_stocks": [{code, name, lian_ban_count}],  # 连板股
                "is_super_theme": bool,          # 三条全满足
                "score": float,                  # 0-100 主线强度评分
                "today_stocks": [pool_stock...],
                "yesterday_stocks": [pool_stock...],
            }],
            "market_summary": {
                "total_zt_today": int,
                "total_zt_yesterday": int,
                "super_theme_count": int,
            },
        }
    """
    if today_pool is None:
        today_pool = fetch_zt_pool()
    if yesterday_pool is None:
        yesterday_pool = fetch_zt_pool_previous()

    today_stocks = today_pool.get("stocks", [])
    yesterday_stocks = yesterday_pool.get("stocks", [])

    # Group by industry
    today_sectors: dict[str, list[dict]] = {}
    yesterday_sectors: dict[str, list[dict]] = {}

    for s in today_stocks:
        ind = s.get("industry", "") or "δ֪"
        today_sectors.setdefault(ind, []).append(s)

    for s in yesterday_stocks:
        ind = s.get("industry", "") or "δ֪"
        yesterday_sectors.setdefault(ind, []).append(s)

    # Evaluate each sector
    all_sectors = set(today_sectors.keys()) | set(yesterday_sectors.keys())
    themes: list[dict[str, Any]] = []

    for sector in all_sectors:
        today_list = today_sectors.get(sector, [])
        yest_list = yesterday_sectors.get(sector, [])
        today_count = len(today_list)
        yest_count = len(yest_list)

        # Condition 1: today ≥ N limit-ups in this sector
        has_enough_zt = today_count >= min_zt_count

        # Condition 2: consecutive days (yesterday also had ≥ N)
        consecutive_days = 0
        if today_count >= min_zt_count and yest_count >= min_zt_count:
            consecutive_days = 2  # At least 2 days with both populated
        elif today_count >= min_zt_count:
            consecutive_days = 1

        # Condition 3: board height (连板高度)
        max_height = 0
        height_stocks: list[dict] = []
        for s in today_list:
            lb = _safe_int(s.get("lian_ban_count"))
            if lb >= min_board_height:
                height_stocks.append({
                    "code": s.get("code", ""),
                    "name": s.get("name", ""),
                    "lian_ban_count": lb,
                })
                max_height = max(max_height, lb)

        has_height = max_height >= min_board_height
        is_super = (
            has_enough_zt
            and consecutive_days >= min_consecutive_days
            and has_height
        )

        # Score: 0-100
        score = _calc_theme_score(
            today_count, yest_count, max_height, consecutive_days,
            min_zt_count,
        )

        # Only include sectors that have some relevance
        if today_count >= 3 or score >= 30:
            themes.append({
                "sector": sector,
                "today_zt_count": today_count,
                "yesterday_zt_count": yest_count,
                "consecutive_days": consecutive_days,
                "max_board_height": max_height,
                "height_stocks": sorted(
                    height_stocks, key=lambda x: -x["lian_ban_count"],
                ),
                "is_super_theme": is_super,
                "score": round(score, 1),
                "today_stocks": today_list,
                "yesterday_stocks": yest_list,
            })

    # Sort by score
    themes.sort(key=lambda t: -t["score"])

    super_count = sum(1 for t in themes if t["is_super_theme"])
    return {
        "themes": themes,
        "market_summary": {
            "total_zt_today": len(today_stocks),
            "total_zt_yesterday": len(yesterday_stocks),
            "super_theme_count": super_count,
        },
    }


def _calc_theme_score(
    today_count: int,
    yest_count: int,
    max_height: int,
    consecutive_days: int,
    min_zt: int,
) -> float:
    """Calculate theme strength score (0-100).

    Weights:
      - 今日涨停数 (40%): 越多越强
      - 连续天数 (25%):  2天 = 满分
      - 连板高度 (25%):  2连板基本, 3连板优, 5连板极强
      - 昨日延续 (10%):  昨日也有足够涨停
    """
    # 今日涨停数 (40 pts)
    if today_count >= 15:
        zt_score = 40
    elif today_count >= min_zt:
        zt_score = 25 + 15 * (today_count - min_zt) / max(15 - min_zt, 1)
    elif today_count >= 5:
        zt_score = 15 + 10 * (today_count - 5) / max(min_zt - 5, 1)
    else:
        zt_score = today_count * 3

    # 连续天数 (25 pts)
    if consecutive_days >= 2:
        day_score = 25
    elif consecutive_days == 1:
        day_score = 12
    else:
        day_score = 0

    # 连板高度 (25 pts)
    if max_height >= 5:
        height_score = 25
    elif max_height >= 3:
        height_score = 20
    elif max_height >= 2:
        height_score = 15
    elif max_height >= 1:
        height_score = 5
    else:
        height_score = 0

    # 昨日延续 (10 pts)
    if yest_count >= min_zt:
        carry_score = 10
    elif yest_count >= 5:
        carry_score = 5
    else:
        carry_score = 0

    return min(zt_score + day_score + height_score + carry_score, 100)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 第二步：确认【主线龙一】
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def rank_dragons(
    theme_stocks: list[dict[str, Any]],
    yesterday_stocks: list[dict[str, Any]] | None = None,
    *,
    realtime_quotes: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """在同一板块主线内，对所有涨停股进行龙一/龙二/龙三排名。

    龙一判定标准（Day 1）：
      1. 封板时间最早 (first_board_time)
      2. 封单金额最大 (seal_amount)
      3. 最稳定（炸板次数最少 open_count）
      4. 不开板 (open_count == 0 最佳)

    龙一确认加分（Day 2）：
      - 第二天最早2连板 = 100%龙一
      - 第二天9:30前封住2连板 = 100%龙一

    Args:
        theme_stocks: 同板块今日涨停股列表 (pool stock format)
        yesterday_stocks: 昨日该板块涨停股列表 (可选, 用于确认龙一连板)
        realtime_quotes: 实时行情 {code: {price, bids, asks, ...}}
                         留给 miniQMT data_bridge 调接口

    Returns:
        [{
            "code": str,
            "name": str,
            "dragon_rank": 1|2|3|...,   # 龙几
            "dragon_score": float,       # 0-100 龙头评分
            "factors": {
                "first_board_time": str,
                "seal_amount_yi": float,
                "open_count": int,
                "lian_ban_count": int,
                "day2_confirmed": bool,   # 第二天确认龙一
                "seal_ratio": float,      # 封单/成交额
                "board_type": str,
            },
            "signals": [str],
        }]
    """
    if not theme_stocks:
        return []

    # Build yesterday lookup by code
    yest_map: dict[str, dict] = {}
    if yesterday_stocks:
        for s in yesterday_stocks:
            yest_map[s.get("code", "")] = s

    scored: list[dict[str, Any]] = []

    for s in theme_stocks:
        code = s.get("code", "")
        name = s.get("name", "")
        lian_ban = _safe_int(s.get("lian_ban_count"))
        first_time = str(s.get("first_board_time", ""))
        last_time = str(s.get("last_board_time", ""))
        seal_amount = _safe_float(s.get("seal_amount"))
        amount = _safe_float(s.get("amount"))
        open_count = _safe_int(s.get("open_count"))
        turnover = _safe_float(s.get("turnover_rate"))

        factors: dict[str, Any] = {
            "first_board_time": first_time,
            "seal_amount_yi": round(seal_amount / 1e8, 2) if seal_amount > 0 else 0,
            "open_count": open_count,
            "lian_ban_count": lian_ban,
            "turnover_rate": round(turnover, 2),
            "board_type": "换手板",  # Default; override if data available
        }
        signals: list[str] = []

        # ── Factor 1: 封板时间 (30 pts) ──
        # 越早越好，9:30-9:35 最强
        time_score = _score_board_time(first_time)

        # ── Factor 2: 封单金额 (25 pts) ──
        seal_score = _score_seal_amount(seal_amount)
        factors["seal_ratio"] = round(seal_amount / amount, 4) if amount > 0 else 0

        # ── Factor 3: 封板稳定性 (20 pts) ──
        # open_count == 0 最佳
        stability_score = _score_stability(open_count)

        # ── Factor 4: 连板高度 (15 pts) ──
        height_score = _score_board_height(lian_ban)

        # ── Factor 5: Day 2 龙一确认 (10 pts) ──
        day2_score = 0.0
        day2_confirmed = False
        if lian_ban >= 2:
            # Already multi-board: check if was yesterday's earliest
            yest = yest_map.get(code)
            if yest:
                yest_first = str(yest.get("first_board_time", ""))
                yest_open = _safe_int(yest.get("open_count"))
                # Day 2 confirmed: was earliest yesterday AND has 2+ boards today
                if yest_first and _is_early_board(yest_first):
                    day2_score = 10
                    day2_confirmed = True
                    signals.append("Day2确认龙一(昨日首封+今日连板)")
                elif yest_open == 0:
                    day2_score = 7
                    signals.append("昨日未开板+今日连板")
            else:
                # 2连板 but wasn't in yesterday pool (newly promoted)
                if lian_ban >= 2:
                    day2_score = 5
        factors["day2_confirmed"] = day2_confirmed

        # ── Realtime overlay (盘中实时数据) ──
        if realtime_quotes and code in realtime_quotes:
            rt = realtime_quotes[code]
            rt_factors = _score_realtime(rt, s)
            factors.update(rt_factors.get("factors", {}))
            signals.extend(rt_factors.get("signals", []))
            # Adjust seal score with real-time bid data
            if rt_factors.get("rt_seal_bonus", 0) > 0:
                seal_score = min(25, seal_score + rt_factors["rt_seal_bonus"])

        # Build signals
        if time_score >= 25:
            signals.insert(0, f"早盘首封({first_time})")
        elif first_time:
            signals.insert(0, f"封板时间{first_time}")
        if seal_score >= 20:
            signals.append(f"大封单{seal_amount/1e8:.1f}亿")
        if open_count == 0:
            signals.append("全天未开板")
        elif open_count > 0:
            signals.append(f"开板{open_count}次")
        if lian_ban >= 2:
            signals.append(f"{lian_ban}连板")

        total = time_score + seal_score + stability_score + height_score + day2_score
        total = min(total, 100)

        scored.append({
            "code": code,
            "name": name,
            "dragon_score": round(total, 1),
            "factors": factors,
            "signals": signals,
        })

    # Sort by score desc → assign dragon_rank
    scored.sort(key=lambda x: -x["dragon_score"])
    for i, item in enumerate(scored):
        item["dragon_rank"] = i + 1

    return scored


def _score_board_time(first_time: str) -> float:
    """Score by first board time (30 pts).

    最早封板 = 最强。
    9:25-9:35 (集合竞价/开盘2分钟) → 满分
    9:35-10:00 → 高分
    10:00-11:00 → 中等
    13:00 以后 → 低分
    """
    if not first_time:
        return 15  # 未知时间, 中性

    # Parse time string like "09:25:03" or "09:25"
    try:
        parts = first_time.replace("：", ":").split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        total_min = hour * 60 + minute
    except (ValueError, IndexError):
        return 15

    if total_min <= 9 * 60 + 35:    # ≤ 09:35
        return 30
    elif total_min <= 10 * 60:       # ≤ 10:00
        return 25
    elif total_min <= 10 * 60 + 30:  # ≤ 10:30
        return 20
    elif total_min <= 11 * 60 + 30:  # ≤ 11:30 (上午)
        return 15
    elif total_min <= 13 * 60 + 30:  # ≤ 13:30 (下午早盘)
        return 10
    elif total_min <= 14 * 60 + 30:  # ≤ 14:30
        return 5
    else:
        return 2  # 尾盘封板, 最弱


def _score_seal_amount(seal_amount: float) -> float:
    """Score seal amount (25 pts).

    封单越大 = 做多意愿越强。
    ≥ 10亿 → 满分
    ≥ 5亿  → 高分
    ≥ 2亿  → 中等
    ≥ 1亿  → 基本
    """
    if seal_amount >= 10e8:
        return 25
    elif seal_amount >= 5e8:
        return 20
    elif seal_amount >= 2e8:
        return 15
    elif seal_amount >= 1e8:
        return 10
    elif seal_amount >= 5000e4:
        return 7
    elif seal_amount > 0:
        return 4
    return 2  # 无封单数据


def _score_stability(open_count: int) -> float:
    """Score board stability (20 pts).

    0次开板 = 最稳
    1次 = 可接受
    ≥3次 = 不稳定
    """
    if open_count == 0:
        return 20
    elif open_count == 1:
        return 14
    elif open_count == 2:
        return 8
    elif open_count == 3:
        return 4
    return 2  # ≥4次, 极不稳定


def _score_board_height(lian_ban: int) -> float:
    """Score board height (15 pts).

    连板越高 → 龙头辨识度越高。
    ≥5板 → 满分
    3板 → 高
    2板 → 中等
    1板 → 基础 (首板, 还不确定龙一)
    """
    if lian_ban >= 5:
        return 15
    elif lian_ban >= 3:
        return 12
    elif lian_ban >= 2:
        return 10
    elif lian_ban >= 1:
        return 5
    return 0


def _is_early_board(time_str: str) -> bool:
    """Check if board time is early (before 10:00)."""
    try:
        parts = time_str.replace("：", ":").split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return hour * 60 + minute <= 10 * 60
    except (ValueError, IndexError):
        return False


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 第三步：龙一什么时候买？(3板打板 = 最佳买点)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def evaluate_buy_timing(
    stock: dict[str, Any],
    klines: list[dict[str, Any]] | None = None,
    *,
    realtime_quote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """评估龙头股的最佳买入时机。

    核心逻辑（真龙战法）：
      1板不知道是不是龙一 → 不买
      2板确认是龙一，但秒板/一字板 → 99%买不进
      3板第一次放量、第一次给机会 → 最佳买点！

    结论：散户最完美、最安全、最容易买到的位置 = 3板打板

    Args:
        stock: 涨停池股票数据
        klines: 历史K线 (可选, 用于判断放量)
        realtime_quote: 实时行情 (miniQMT 接口, 可选)

    Returns:
        {
            "action": "buy" | "wait" | "skip",
            "reason": str,
            "confidence": float,     # 0-100
            "board_number": int,     # 当前第几板
            "is_best_timing": bool,  # 是否3板最佳时机
            "volume_signal": str,    # "放量" | "缩量" | "平量" | "δ֪"
            "buy_method": str,       # 打板方法说明
            "risk_level": str,       # "low" | "medium" | "high"
        }
    """
    lian_ban = _safe_int(stock.get("lian_ban_count"))
    first_time = str(stock.get("first_board_time", ""))
    open_count = _safe_int(stock.get("open_count"))
    turnover = _safe_float(stock.get("turnover_rate"))
    code = stock.get("code", "")

    result: dict[str, Any] = {
        "board_number": lian_ban,
        "is_best_timing": False,
        "volume_signal": "δ֪",
        "buy_method": "",
        "risk_level": "high",
    }

    # ── 1板: 不知道是不是龙一 → 不买 ──
    if lian_ban <= 1:
        result.update({
            "action": "wait",
            "reason": "首板，无法确认龙一身份，观望",
            "confidence": 10,
        })
        return result

    # ── 2板: 确认是龙一，但能否买到? ──
    if lian_ban == 2:
        # Check if it's a 一字板/秒板 (can't buy)
        is_yizi = _is_early_board(first_time) and open_count == 0
        if is_yizi or turnover < 1.0:
            result.update({
                "action": "wait",
                "reason": "2板确认龙一，但秒板/一字板，99%买不进，等3板",
                "confidence": 20,
                "risk_level": "high",
            })
        else:
            result.update({
                "action": "wait",
                "reason": "2板确认龙一，换手板有机会但非最佳时机，等3板更安全",
                "confidence": 35,
                "risk_level": "medium",
            })
        return result

    # ── 3板: 最佳买入时机! ──
    if lian_ban == 3:
        vol_signal = _check_volume_signal(klines) if klines else "δ֪"
        result["volume_signal"] = vol_signal

        if vol_signal == "放量":
            result.update({
                "action": "buy",
                "reason": "3板打板！第一次放量、第一次给机会，散户最佳买点",
                "confidence": 85,
                "is_best_timing": True,
                "buy_method": _get_buy_method(stock, realtime_quote),
                "risk_level": "low",
            })
        elif vol_signal == "缩量":
            result.update({
                "action": "buy",
                "reason": "3板打板，缩量一字风险存在但位置正确，可小仓试",
                "confidence": 60,
                "is_best_timing": True,
                "buy_method": _get_buy_method(stock, realtime_quote),
                "risk_level": "medium",
            })
        else:
            # Volume unknown (no klines)
            result.update({
                "action": "buy",
                "reason": "3板打板最佳位置（无K线数据，未确认放量）",
                "confidence": 70,
                "is_best_timing": True,
                "buy_method": _get_buy_method(stock, realtime_quote),
                "risk_level": "medium",
            })
        return result

    # ── 4板+: 仍可做但风险递增 ──
    if lian_ban == 4:
        result.update({
            "action": "buy",
            "reason": "4板仍可做龙一，但风险高于3板，仓位宜减半",
            "confidence": 45,
            "is_best_timing": False,
            "buy_method": _get_buy_method(stock, realtime_quote),
            "risk_level": "high",
        })
        return result

    # 5板以上
    result.update({
        "action": "skip",
        "reason": f"{lian_ban}板高位，追高风险极大，不建议介入",
        "confidence": 20,
        "risk_level": "high",
    })
    return result


def _check_volume_signal(klines: list[dict[str, Any]]) -> str:
    """Check volume trend in recent bars.

    放量: latest volume > 1.5x average of previous 3 bars
    缩量: latest volume < 0.7x average of previous 3 bars
    平量: otherwise
    """
    if not klines or len(klines) < 3:
        return "δ֪"

    volumes = [_safe_float(k.get("volume")) for k in klines[-4:]]
    volumes = [v for v in volumes if v > 0]
    if len(volumes) < 3:
        return "δ֪"

    latest = volumes[-1]
    prev_avg = sum(volumes[:-1]) / len(volumes[:-1])

    if prev_avg <= 0:
        return "δ֪"

    ratio = latest / prev_avg
    if ratio >= 1.5:
        return "放量"
    elif ratio <= 0.7:
        return "缩量"
    return "平量"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 第四步：怎么买？（唯一正确买法 = 涨停打板）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _get_buy_method(
    stock: dict[str, Any],
    realtime_quote: dict[str, Any] | None = None,
) -> str:
    """Generate buy method instruction.

    只做一种：涨停打板买！
    1. 等龙一冲到涨停价
    2. 等封单出现、稳稳封住一瞬间
    3. 直接挂涨停价买入

    为什么必须打板？不涨停 = 不确定强 → 封住涨停 = 资金最强表态
    → 打板 = 买确定性，不猜、不赌
    """
    code = stock.get("code", "")
    price = _safe_float(stock.get("price"))
    seal = _safe_float(stock.get("seal_amount"))

    parts = [
        "【打板买法】只做涨停打板",
        f"1. 等{code}冲到涨停价({price:.2f})" if price > 0 else "1. 等冲到涨停价",
        "2. 等封单出现，看封单稳定（不反复开板）",
        "3. 确认稳稳封住后，直接挂涨停价买入",
    ]

    if seal > 0:
        parts.append(f"当前封单: {seal/1e8:.1f}亿")

    if realtime_quote:
        rt_price = _safe_float(realtime_quote.get("price"))
        bids = realtime_quote.get("bids", [])
        if rt_price > 0:
            parts.append(f"实时价: {rt_price:.2f}")
        if bids:
            total_bid_vol = sum(b.get("volume", 0) for b in bids)
            parts.append(f"买盘挂单: {total_bid_vol}手")

    parts.append("⚠ 不涨停不买，封住才是确定性")
    return " | ".join(parts)


def generate_board_buy_signal(
    stock: dict[str, Any],
    realtime_quote: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """封单实时分析（打板信号）。

    盘中实时数据由 miniQMT data_bridge 提供。
    此函数分析实时行情判断是否满足打板条件。

    Args:
        stock: 涨停池股票基本信息
        realtime_quote: miniQMT 实时行情 {price, prevClose, bids, asks, volume, amount}

    Returns:
        {
            "ready": bool,           # 是否满足打板条件
            "signal": str,           # "板上封稳" | "冲板中" | "开板" | "未涨停"
            "seal_strength": str,    # "极强" | "ǿ" | "中" | "弱"
            "seal_amount_yi": float, # 实时封单金额(亿)
            "advice": str,
        }
    """
    code = stock.get("code", "")
    result: dict[str, Any] = {
        "ready": False,
        "signal": "未涨停",
        "seal_strength": "弱",
        "seal_amount_yi": 0,
        "advice": "等待涨停",
    }

    if not realtime_quote:
        # No realtime data — use pool data as fallback
        seal = _safe_float(stock.get("seal_amount"))
        if seal > 0:
            result["seal_amount_yi"] = round(seal / 1e8, 2)
            result["signal"] = "盘后数据(非实时)"
            strength = _classify_seal_strength(seal)
            result["seal_strength"] = strength
            result["advice"] = f"盘后封单{seal/1e8:.1f}亿({strength}), 盘中需实时确认"
        return result

    # ── Realtime analysis ──
    rt_price = _safe_float(realtime_quote.get("price"))
    prev_close = _safe_float(realtime_quote.get("prevClose"))

    if rt_price <= 0 or prev_close <= 0:
        return result

    # Calculate limit-up price
    from aipha.engines.lianban import _limit_up_ratio
    ratio = _limit_up_ratio(code)
    limit_price = round(prev_close * (1 + ratio), 2)

    # Is at limit-up?
    at_limit = abs(rt_price - limit_price) < 0.01

    if not at_limit:
        pct = (rt_price / prev_close - 1) * 100 if prev_close > 0 else 0
        if pct >= (ratio * 100 - 2):
            result["signal"] = "冲板中"
            result["advice"] = f"当前涨{pct:.1f}%，距涨停{limit_price:.2f}还差{(limit_price-rt_price):.2f}元，等封板"
        else:
            result["signal"] = "未涨停"
            result["advice"] = f"未到涨停价，当前{rt_price:.2f}(涨{pct:.1f}%)，待观察"
        return result

    # At limit-up — analyze seal
    bids = realtime_quote.get("bids", [])
    asks = realtime_quote.get("asks", [])

    # Calculate seal amount from bid side at limit price
    seal_vol = 0
    for bid in bids:
        if abs(_safe_float(bid.get("price")) - limit_price) < 0.01:
            seal_vol += int(bid.get("volume", 0))

    seal_amount = seal_vol * limit_price * 100  # 手→股→金额
    result["seal_amount_yi"] = round(seal_amount / 1e8, 2)
    strength = _classify_seal_strength(seal_amount)
    result["seal_strength"] = strength

    # Has sell orders at limit price? (开板信号)
    has_asks_at_limit = any(
        abs(_safe_float(a.get("price")) - limit_price) < 0.01
        for a in (asks or [])
    )

    if has_asks_at_limit:
        result["signal"] = "开板"
        result["advice"] = f"涨停价有卖单，板面松动，暂不打板"
        return result

    # Sealed at limit!
    result["signal"] = "板上封稳"
    result["ready"] = strength in ("极强", "ǿ")

    if result["ready"]:
        result["advice"] = (
            f"涨停封稳！封单{seal_amount/1e8:.1f}亿({strength})，"
            f"可挂涨停价{limit_price:.2f}排队买入"
        )
    else:
        result["advice"] = (
            f"已涨停但封单{strength}({seal_amount/1e8:.1f}亿)，"
            f"封单不够厚实，打板风险偏高"
        )
    return result


def _classify_seal_strength(seal_amount: float) -> str:
    """Classify seal strength by amount."""
    if seal_amount >= 10e8:
        return "极强"
    elif seal_amount >= 5e8:
        return "ǿ"
    elif seal_amount >= 2e8:
        return "中"
    elif seal_amount >= 1e8:
        return "弱"
    return "极弱"


def _score_realtime(
    rt_quote: dict[str, Any],
    pool_stock: dict[str, Any],
) -> dict[str, Any]:
    """Score realtime quote data as overlay on pool-based dragon ranking.

    Returns extra factors and signals from realtime data.
    """
    factors: dict[str, Any] = {}
    signals: list[str] = []
    rt_seal_bonus = 0.0

    rt_price = _safe_float(rt_quote.get("price"))
    prev_close = _safe_float(rt_quote.get("prevClose"))
    bids = rt_quote.get("bids", [])
    asks = rt_quote.get("asks", [])

    if rt_price > 0:
        factors["rt_price"] = rt_price

    if prev_close > 0 and rt_price > 0:
        code = pool_stock.get("code", "")
        from aipha.engines.lianban import _limit_up_ratio
        ratio = _limit_up_ratio(code)
        limit_price = round(prev_close * (1 + ratio), 2)

        at_limit = abs(rt_price - limit_price) < 0.01
        factors["at_limit"] = at_limit

        if at_limit and bids:
            # Calculate real-time seal from bids at limit price
            total_bid = sum(
                int(b.get("volume", 0))
                for b in bids
                if abs(_safe_float(b.get("price")) - limit_price) < 0.01
            )
            rt_seal = total_bid * limit_price * 100
            factors["rt_seal_yi"] = round(rt_seal / 1e8, 2)
            if rt_seal >= 5e8:
                rt_seal_bonus = 5
                signals.append(f"实时封单{rt_seal/1e8:.1f}亿(ǿ)")
            elif rt_seal >= 2e8:
                rt_seal_bonus = 3
                signals.append(f"实时封单{rt_seal/1e8:.1f}亿")

    return {
        "factors": factors,
        "signals": signals,
        "rt_seal_bonus": rt_seal_bonus,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 第五步：龙一买不到 → 做同主线龙二、龙三
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_fallback_dragons(
    dragons: list[dict[str, Any]],
    max_rank: int = 3,
) -> dict[str, Any]:
    """龙一买不到时的降级推荐。

    规则:
      - 龙一买不到 → 做龙二
      - 龙二买不到 → 做龙三
      - 龙四以后全部不看！

    Args:
        dragons: rank_dragons 的输出（已排序）
        max_rank: 最多看到龙几（默认3）

    Returns:
        {
            "dragon_1": {...} | None,
            "dragon_2": {...} | None,
            "dragon_3": {...} | None,
            "recommendation": str,
            "buyable_dragons": [{...}],   # 可买入的龙（排除一字板）
        }
    """
    d1 = dragons[0] if len(dragons) >= 1 else None
    d2 = dragons[1] if len(dragons) >= 2 else None
    d3 = dragons[2] if len(dragons) >= 3 else None

    buyable: list[dict] = []
    for d in dragons[:max_rank]:
        # Filter out unbuyable (一字板 / turnover too low)
        turnover = _safe_float(d.get("factors", {}).get("turnover_rate"))
        if turnover > 1.0:  # At least some turnover = has trading opportunity
            buyable.append(d)

    # Build recommendation
    if buyable:
        top = buyable[0]
        rank = top.get("dragon_rank", 1)
        name = top.get("name", "")
        if rank == 1:
            rec = f"首选龙一 {name}，3板打板买入"
        elif rank == 2:
            rec = f"龙一买不到，做同主线龙二 {name}"
        else:
            rec = f"龙一龙二均买不到，做龙三 {name}（龙四以后不看）"
    else:
        rec = "龙一~龙三均为一字板/无法买入，放弃该主线，等下一个主线"

    return {
        "dragon_1": d1,
        "dragon_2": d2,
        "dragon_3": d3,
        "recommendation": rec,
        "buyable_dragons": buyable,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 第六步：完整流程一句万能口诀
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def run_full_analysis(
    *,
    today_pool: dict[str, Any] | None = None,
    yesterday_pool: dict[str, Any] | None = None,
    klines_fetcher: Callable[[str], list[dict[str, Any]]] | None = None,
    realtime_fetcher: Callable[[list[str]], dict[str, dict[str, Any]]] | None = None,
    min_theme_score: float = 50,
    top_themes: int = 3,
    max_dragon_rank: int = 3,
) -> dict[str, Any]:
    """真龙战法完整分析流程。

    口诀：
    主线看两天，涨停要十只；
    龙一看最先，二板定真龙；
    一板不买，二板难买；
    三板打板，最稳最安全；
    只做龙一、龙二、龙至，做同主线龙二、龙三。

    Args:
        today_pool: 今日涨停池（None则自动获取）
        yesterday_pool: 昨日涨停池（None则自动获取）
        klines_fetcher: K线获取函数 code → klines（可选，用于放量判断）
        realtime_fetcher: 实时行情获取函数 codes → {code: quote}
                         接口预留给 miniQMT data_bridge
        min_theme_score: 主线最低得分
        top_themes: 最多分析几个主线
        max_dragon_rank: 最多看到龙几

    Returns:
        {
            "timestamp": str,
            "summary": str,                    # 一句话总结
            "market_summary": {...},
            "themes": [{
                "sector": str,
                "theme_score": float,
                "is_super_theme": bool,
                "dragons": [{dragon ranked}],
                "fallback": {dragon_1, dragon_2, dragon_3, recommendation},
                "buy_signals": [{timing evaluation}],
            }],
            "top_picks": [{best candidates across themes}],
        }
    """
    # ── Step 1: Identify major themes ──
    theme_result = identify_major_theme(today_pool, yesterday_pool)
    themes = theme_result.get("themes", [])
    market_summary = theme_result.get("market_summary", {})

    # Filter to strong themes
    strong_themes = [
        t for t in themes if t["score"] >= min_theme_score
    ][:top_themes]

    if not strong_themes:
        # Relax to top available if no super themes
        strong_themes = themes[:top_themes]

    # ── Step 2-5: For each theme, rank dragons + evaluate timing ──
    analyzed_themes: list[dict[str, Any]] = []
    all_picks: list[dict[str, Any]] = []

    # Collect all codes for batch realtime fetch
    all_codes: list[str] = []
    for theme in strong_themes:
        for s in theme.get("today_stocks", []):
            code = s.get("code", "")
            if code:
                all_codes.append(code)

    # Fetch realtime quotes in batch (if available)
    rt_quotes: dict[str, dict[str, Any]] = {}
    if realtime_fetcher and all_codes:
        try:
            rt_quotes = realtime_fetcher(all_codes) or {}
        except Exception:
            rt_quotes = {}

    for theme in strong_themes:
        sector = theme["sector"]
        today_stocks = theme.get("today_stocks", [])
        yest_stocks = theme.get("yesterday_stocks", [])

        # Step 2: Rank dragons
        dragons = rank_dragons(
            today_stocks, yest_stocks,
            realtime_quotes=rt_quotes,
        )

        # Step 3: Evaluate buy timing for top dragons
        buy_signals: list[dict[str, Any]] = []
        for d in dragons[:max_dragon_rank]:
            code = d["code"]
            # Find source stock data
            src_stock = next(
                (s for s in today_stocks if s.get("code") == code), {}
            )
            klines = None
            if klines_fetcher:
                try:
                    klines = klines_fetcher(code)
                except Exception:
                    pass

            timing = evaluate_buy_timing(
                src_stock, klines,
                realtime_quote=rt_quotes.get(code),
            )
            timing["code"] = code
            timing["name"] = d.get("name", "")
            timing["dragon_rank"] = d["dragon_rank"]
            timing["dragon_score"] = d["dragon_score"]
            buy_signals.append(timing)

            # Collect top picks
            if timing.get("action") == "buy" and timing.get("confidence", 0) >= 50:
                all_picks.append({
                    **timing,
                    "sector": sector,
                    "theme_score": theme["score"],
                })

        # Step 5: Fallback recommendation
        fallback = get_fallback_dragons(dragons, max_dragon_rank)

        # Step 4: Board buy signals for top dragons
        board_signals: list[dict[str, Any]] = []
        for d in dragons[:max_dragon_rank]:
            code = d["code"]
            src_stock = next(
                (s for s in today_stocks if s.get("code") == code), {}
            )
            board_sig = generate_board_buy_signal(
                src_stock, rt_quotes.get(code),
            )
            board_sig["code"] = code
            board_sig["name"] = d.get("name", "")
            board_signals.append(board_sig)

        analyzed_themes.append({
            "sector": sector,
            "theme_score": theme["score"],
            "is_super_theme": theme.get("is_super_theme", False),
            "today_zt_count": theme.get("today_zt_count", 0),
            "max_board_height": theme.get("max_board_height", 0),
            "dragons": dragons[:max_dragon_rank],
            "fallback": fallback,
            "buy_signals": buy_signals,
            "board_signals": board_signals,
        })

    # Sort top picks by confidence
    all_picks.sort(key=lambda p: -p.get("confidence", 0))

    # ── Step 6: One-line summary ──
    summary = _build_summary(analyzed_themes, all_picks, market_summary)

    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "summary": summary,
        "market_summary": market_summary,
        "themes": analyzed_themes,
        "top_picks": all_picks[:5],
    }


def _build_summary(
    themes: list[dict],
    picks: list[dict],
    market: dict,
) -> str:
    """Build one-line summary (口诀式总结)."""
    total_zt = market.get("total_zt_today", 0)
    super_count = market.get("super_theme_count", 0)

    parts: list[str] = []
    parts.append(f"今日{total_zt}只涨停")

    if super_count > 0:
        parts.append(f"发现{super_count}条超级主线")
    elif themes:
        parts.append(f"关注{len(themes)}条潜力主线")
    else:
        parts.append("暂无明确主线")

    if picks:
        top = picks[0]
        parts.append(
            f"首选: {top.get('name','')}({top.get('code','')}) "
            f"龙{top.get('dragon_rank', '?')} "
            f"{top.get('board_number',0)}板 "
            f"置信度{top.get('confidence',0):.0f}%"
        )
    else:
        parts.append("暂无打板信号")

    return " | ".join(parts)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# CLI / TypeScript bridge entry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import json


def _output(data: dict) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    """CLI entry point, supports QUANT_CONFIG env var for TypeScript bridge."""
    import argparse

    quant_config = os.environ.get("QUANT_CONFIG")
    if quant_config:
        cfg = json.loads(quant_config)
        action = cfg.get("action", "full")
    else:
        parser = argparse.ArgumentParser(description="真龙战法核心引擎")
        parser.add_argument(
            "action",
            nargs="?",
            default="full",
            choices=["full", "themes", "dragons", "timing", "config"],
            help="分析动作",
        )
        parser.add_argument("--sector", default="", help="指定板块")
        parser.add_argument("--code", default="", help="指定股票代码")
        parser.add_argument("--min-score", type=float, default=50, help="主线最低评分")
        parser.add_argument("--top", type=int, default=3, help="最多分析几条主线")
        args = parser.parse_args()
        cfg = {
            "action": args.action,
            "sector": args.sector,
            "code": args.code,
            "min_score": args.min_score,
            "top": args.top,
        }
        action = cfg["action"]

    try:
        if action == "full":
            result = run_full_analysis(
                min_theme_score=cfg.get("min_score", 50),
                top_themes=cfg.get("top", 3),
            )
            _output(result)

        elif action == "themes":
            result = identify_major_theme()
            _output(result)

        elif action == "dragons":
            sector = cfg.get("sector", "")
            pool = fetch_zt_pool()
            stocks = pool.get("stocks", [])
            if sector:
                stocks = [
                    s for s in stocks
                    if s.get("industry", "") == sector
                ]
            dragons = rank_dragons(stocks)
            fallback = get_fallback_dragons(dragons)
            _output({
                "sector": sector or "全市场",
                "dragons": dragons,
                "fallback": fallback,
            })

        elif action == "timing":
            code = cfg.get("code", "")
            if not code:
                _output({"error": "需要指定 --code 股票代码"})
                return
            pool = fetch_zt_pool()
            stock = next(
                (s for s in pool.get("stocks", []) if s.get("code") == code),
                None,
            )
            if not stock:
                _output({"error": f"{code} 不在今日涨停池中"})
                return
            result = evaluate_buy_timing(stock)
            _output(result)

        elif action == "config":
            _output({
                "name": "真龙战法",
                "description": "主线龙头识别+3板打板",
                "steps": [
                    "第一步: 确认大主线(板块≥10只涨停,连续2天,有连板高度)",
                    "第二步: 确认龙一(首封最早,封单最大,最稳不开板)",
                    "第三步: 3板打板=散户最佳买点(第一次放量+第一次给机会)",
                    "第四步: 只做涨停打板买(等封单稳→挂涨停价)",
                    "第五步: 龙一买不到→龙二→龙三(龙四以后不看)",
                    "第六步: 口诀:主线看两天,龙一看最先,三板打板最安全",
                ],
                "risk_rules": [
                    "一板不买(不确定龙一)",
                    "二板难买(秒板/一字板99%买不进)",
                    "三板打板(最佳买点,第一次给机会)",
                    "龙四以后不看",
                    "不涨停不买(买确定性,不猜不赌)",
                ],
                "parameters": {
                    "min_zt_count": 10,
                    "min_consecutive_days": 2,
                    "min_board_height": 2,
                    "best_buy_board": 3,
                    "max_dragon_rank": 3,
                },
            })

    except Exception as e:
        import traceback
        _output({
            "error": f"分析异常: {e}",
            "traceback": traceback.format_exc(),
        })


if __name__ == "__main__":
    main()
