"""
zhaban_predictor — 炸板风险预测引擎。

基于涨停池各维度数据的关联分析，对当前涨停板个股的炸板概率进行评估。

核心因子:
  1. 开板次数 (open_count)        — 多次开板意味封板不稳      权重 25%
  2. 封单比 (seal_ratio)          — 封单/成交额，低则买方不足  权重 20%
  3. 封板时间 (board_time)        — 越晚封板越不稳定          权重 15%
  4. 换手率异常 (turnover)        — 过高说明投机严重          权重 15%
  5. 行业孤立度 (industry_isolation) — 非主线题材更易炸      权重 10%
  6. 连板高度衰退 (height_decay)  — 高位连板衰退概率大       权重 10%
  7. 市值适配度 (market_cap_fit)  — 极小/极大市值均不利      权重  5%

输出:
  risk_score 0-100 (越高越可能炸板)
  risk_level: 低/中/高/极高
  risk_factors: 每个因子的详细说明
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .lianban import _safe_float, _safe_int, fetch_zt_pool, fetch_zt_pool_zbgc


def _parse_board_time(time_str: str) -> float | None:
    """Parse board time string to hours since 09:30.

    Handles formats: "09:35:12", "093512", "9:35" etc.
    Returns None if parsing fails.
    """
    if not time_str or time_str == "--":
        return None
    try:
        clean = str(time_str).strip().replace(":", "")
        if len(clean) >= 4:
            h = int(clean[:2])
            m = int(clean[2:4])
            s = int(clean[4:6]) if len(clean) >= 6 else 0
            total_min = h * 60 + m + s / 60
            open_min = 9 * 60 + 30  # 09:30
            return (total_min - open_min) / 60  # hours since open
    except (ValueError, IndexError):
        pass
    return None


def _industry_concentration(stocks: list[dict[str, Any]]) -> dict[str, int]:
    """Count how many stocks share each industry in today's ZT pool."""
    counts: dict[str, int] = {}
    for s in stocks:
        ind = s.get("industry", "").strip()
        if ind:
            counts[ind] = counts.get(ind, 0) + 1
    return counts


def compute_zhaban_risk(
    stock: dict[str, Any],
    industry_counts: dict[str, int],
    total_zt: int,
    env_failure_rate: float,
) -> dict[str, Any]:
    """Compute 炸板风险 score for a single stock.

    Args:
        stock: Stock dict from fetch_zt_pool() with code/name/open_count/
               seal_amount/amount/first_board_time/last_board_time/
               turnover_rate/lian_ban_count/flow_value/industry etc.
        industry_counts: {industry: count} for all stocks in today's ZT pool.
        total_zt: Total number of limit-up stocks today.
        env_failure_rate: Today's overall failure rate (0-1).

    Returns:
        {
          "code", "name", "risk_score" (0-100), "risk_level",
          "risk_factors": [...],
          "factor_scores": {name: {score, max, detail}},
          "signals_positive": [...], "signals_negative": [...]
        }
    """
    factors: dict[str, dict[str, Any]] = {}
    pos_signals: list[str] = []
    neg_signals: list[str] = []

    code = stock.get("code", "")
    name = stock.get("name", "")

    # ────────────────────────────────────────────────────────────
    # Factor 1: 开板次数 (max 25 pts → higher = more risk)
    # ────────────────────────────────────────────────────────────
    open_count = _safe_int(stock.get("open_count"))
    if open_count == 0:
        f1_score = 0
        pos_signals.append("零开板，封板坚实")
    elif open_count == 1:
        f1_score = 12
        neg_signals.append(f"开板{open_count}次，封板稍松")
    elif open_count == 2:
        f1_score = 18
        neg_signals.append(f"开板{open_count}次，封板不稳")
    else:
        f1_score = 25
        neg_signals.append(f"开板{open_count}次，封板极弱")

    factors["open_count"] = {
        "score": f1_score, "max": 25,
        "detail": f"开板{open_count}次",
    }

    # ────────────────────────────────────────────────────────────
    # Factor 2: 封单比 (max 20 pts)
    #   seal_ratio = seal_amount / amount
    #   > 1.0: very strong → risk 0
    #   0.5-1.0: moderate → risk 6
    #   0.2-0.5: weak → risk 14
    #   < 0.2: very weak → risk 20
    # ────────────────────────────────────────────────────────────
    seal_amount = _safe_float(stock.get("seal_amount"))
    amount = _safe_float(stock.get("amount"))
    if amount > 0 and seal_amount > 0:
        seal_ratio = seal_amount / amount
        if seal_ratio >= 1.0:
            f2_score = 0
            pos_signals.append(f"封单比{seal_ratio:.2f}，买方充足")
        elif seal_ratio >= 0.5:
            f2_score = 6
            pos_signals.append(f"封单比{seal_ratio:.2f}，尚可")
        elif seal_ratio >= 0.2:
            f2_score = 14
            neg_signals.append(f"封单比{seal_ratio:.2f}，偏弱")
        else:
            f2_score = 20
            neg_signals.append(f"封单比{seal_ratio:.2f}，极弱")
    elif seal_amount <= 0 and amount > 0:
        # No seal data (might already be open / no seal)
        f2_score = 18
        neg_signals.append("无封单数据")
        seal_ratio = 0
    else:
        f2_score = 10
        seal_ratio = 0

    factors["seal_ratio"] = {
        "score": f2_score, "max": 20,
        "detail": f"封单比{seal_ratio:.2f}" if amount > 0 else "无数据",
    }

    # ────────────────────────────────────────────────────────────
    # Factor 3: 封板时间 (max 15 pts)
    #   Before 10:00: strong (risk 0-3)
    #   10:00-11:30: moderate (risk 5-8)
    #   13:00-14:00: weak (risk 10-12)
    #   After 14:00: very weak (risk 13-15)
    # ────────────────────────────────────────────────────────────
    first_time_str = str(stock.get("first_board_time", ""))
    last_time_str = str(stock.get("last_board_time", ""))
    first_hours = _parse_board_time(first_time_str)
    last_hours = _parse_board_time(last_time_str)

    if first_hours is not None:
        if first_hours <= 0.5:       # Before 10:00
            f3_score = 2
            pos_signals.append(f"早盘封板({first_time_str})")
        elif first_hours <= 2.0:     # Before 11:30
            f3_score = 6
        elif first_hours <= 3.5:     # Before 13:00 (lunch break counted)
            f3_score = 10
            neg_signals.append(f"午后封板({first_time_str})")
        elif first_hours <= 4.5:     # Before 14:00
            f3_score = 12
            neg_signals.append(f"尾盘封板({first_time_str})")
        else:                        # After 14:00
            f3_score = 15
            neg_signals.append(f"临近收盘封板({first_time_str})")
    else:
        f3_score = 8

    # Bonus: large gap between first and last board time
    if first_hours is not None and last_hours is not None:
        time_gap = last_hours - first_hours
        if time_gap > 2.0:
            f3_score = min(f3_score + 3, 15)
            neg_signals.append(f"首末封板间隔{time_gap:.1f}h")

    factors["board_time"] = {
        "score": f3_score, "max": 15,
        "detail": f"首封{first_time_str or '--'}，末封{last_time_str or '--'}",
    }

    # ────────────────────────────────────────────────────────────
    # Factor 4: 换手率异常 (max 15 pts)
    #   主板: >15% high, >25% extreme
    #   创业板/科创板 (stock begins with 30/688): thresholds 2x
    # ────────────────────────────────────────────────────────────
    turnover = _safe_float(stock.get("turnover_rate"))
    is_gem = code.startswith("30") or code.startswith("688")  # 创/科
    t_high = 30 if is_gem else 15
    t_extreme = 50 if is_gem else 25

    if turnover <= 0:
        f4_score = 8
    elif turnover >= t_extreme:
        f4_score = 15
        neg_signals.append(f"换手率{turnover:.1f}%极高")
    elif turnover >= t_high:
        f4_score = 10
        neg_signals.append(f"换手率{turnover:.1f}%偏高")
    elif turnover >= 5:
        f4_score = 3
    else:
        # Very low turnover (一字板) — different risk profile
        f4_score = 2
        pos_signals.append(f"换手率{turnover:.1f}%（一字封板级）")

    factors["turnover"] = {
        "score": f4_score, "max": 15,
        "detail": f"换手率{turnover:.1f}%",
    }

    # ────────────────────────────────────────────────────────────
    # Factor 5: 行业孤立度 (max 10 pts)
    #   If industry has many peers in ZT pool → theme-driven → safer
    #   If isolated (only 1 stock) → higher risk
    # ────────────────────────────────────────────────────────────
    industry = stock.get("industry", "").strip()
    peers = industry_counts.get(industry, 1) if industry else 1

    if peers >= 5:
        f5_score = 0
        pos_signals.append(f"主线题材({industry}，{peers}只涨停)")
    elif peers >= 3:
        f5_score = 3
    elif peers >= 2:
        f5_score = 6
    else:
        f5_score = 10
        neg_signals.append(f"题材孤立({industry or '未知'}，仅{peers}只)")

    factors["industry_isolation"] = {
        "score": f5_score, "max": 10,
        "detail": f"{industry or '未知'}({peers}只同行业涨停)",
    }

    # ────────────────────────────────────────────────────────────
    # Factor 6: 连板高度衰退 (max 10 pts)
    #   Higher boards have higher attrition; 1板 also risky (not proven)
    # ────────────────────────────────────────────────────────────
    lian_ban = _safe_int(stock.get("lian_ban_count"))
    if lian_ban <= 1:
        f6_score = 5  # First board, neutral
    elif lian_ban == 2:
        f6_score = 3  # 2-board proven
    elif lian_ban == 3:
        f6_score = 5
    elif lian_ban == 4:
        f6_score = 7
    else:
        f6_score = 10
        neg_signals.append(f"高位{lian_ban}板，高度衰退风险大")

    factors["height_decay"] = {
        "score": f6_score, "max": 10,
        "detail": f"{lian_ban}连板",
    }

    # ────────────────────────────────────────────────────────────
    # Factor 7: 市值适配度 (max 5 pts)
    #   30亿-150亿 optimal, very small or very large adds risk
    # ────────────────────────────────────────────────────────────
    flow_value = _safe_float(stock.get("flow_value"))
    flow_yi = flow_value / 1e8 if flow_value > 0 else 0

    if flow_yi <= 0:
        f7_score = 3
    elif flow_yi < 20:
        f7_score = 4
        neg_signals.append(f"流通市值{flow_yi:.0f}亿偏小")
    elif flow_yi <= 150:
        f7_score = 0
    elif flow_yi <= 300:
        f7_score = 3
    else:
        f7_score = 5
        neg_signals.append(f"流通市值{flow_yi:.0f}亿偏大")

    factors["market_cap"] = {
        "score": f7_score, "max": 5,
        "detail": f"流通市值{flow_yi:.1f}亿" if flow_yi > 0 else "无数据",
    }

    # ────────────────────────────────────────────────────────────
    # Environment modifier: 如果整体炸板率高，风险上浮
    # ────────────────────────────────────────────────────────────
    env_modifier = 0
    if env_failure_rate >= 0.4:
        env_modifier = 5
        neg_signals.append(f"市场炸板率{env_failure_rate*100:.0f}%偏高")
    elif env_failure_rate >= 0.3:
        env_modifier = 3

    # ── Total ──
    raw_score = (
        f1_score + f2_score + f3_score + f4_score
        + f5_score + f6_score + f7_score + env_modifier
    )
    risk_score = min(raw_score, 100)

    if risk_score >= 70:
        risk_level = "极高"
    elif risk_score >= 50:
        risk_level = "高"
    elif risk_score >= 30:
        risk_level = "中"
    else:
        risk_level = "低"

    return {
        "code": code,
        "name": name,
        "risk_score": round(risk_score, 1),
        "risk_level": risk_level,
        "lian_ban_count": lian_ban,
        "price": _safe_float(stock.get("price")),
        "change_pct": _safe_float(stock.get("change_pct")),
        "turnover_rate": round(turnover, 1),
        "amount_yi": round(amount / 1e8, 2) if amount > 0 else 0,
        "flow_value_yi": round(flow_yi, 1) if flow_yi > 0 else None,
        "seal_amount_yi": round(seal_amount / 1e8, 2) if seal_amount > 0 else None,
        "seal_ratio": round(seal_ratio, 2) if amount > 0 else None,
        "open_count": open_count,
        "first_board_time": first_time_str,
        "last_board_time": last_time_str,
        "industry": industry,
        "factor_scores": factors,
        "signals_positive": pos_signals,
        "signals_negative": neg_signals,
    }


def predict_zhaban(date: str | None = None, min_risk: int = 0) -> dict[str, Any]:
    """Predict which limit-up stocks are most likely to fail (炸板).

    Returns:
        {
          "date": "...",
          "total_zt": N,
          "total_zbgc": N,     # already-failed stocks
          "env_failure_rate": 0.xx,
          "predictions": [...],  # sorted by risk_score desc
          "risk_summary": {
            "极高": N, "高": N, "中": N, "低": N
          },
          "pattern_insights": [...]  # discovered patterns
        }
    """
    zt_pool = fetch_zt_pool(date)
    zbgc_pool = fetch_zt_pool_zbgc(date)

    zt_stocks = zt_pool.get("stocks", [])
    zbgc_stocks = zbgc_pool.get("stocks", [])

    total_zt = len(zt_stocks)
    total_zbgc = len(zbgc_stocks)
    total_attempts = total_zt + total_zbgc
    env_failure_rate = total_zbgc / total_attempts if total_attempts > 0 else 0

    # Build industry concentration map
    industry_counts = _industry_concentration(zt_stocks)

    # Score each stock in ZT pool
    predictions: list[dict[str, Any]] = []
    for stock in zt_stocks:
        result = compute_zhaban_risk(
            stock, industry_counts, total_zt, env_failure_rate
        )
        if result["risk_score"] >= min_risk:
            predictions.append(result)

    # Sort by risk descending
    predictions.sort(key=lambda p: p["risk_score"], reverse=True)

    # Assign rank
    for i, p in enumerate(predictions):
        p["rank"] = i + 1

    # Risk level summary
    risk_summary: dict[str, int] = {"极高": 0, "高": 0, "中": 0, "低": 0}
    for p in predictions:
        lvl = p.get("risk_level", "中")
        risk_summary[lvl] = risk_summary.get(lvl, 0) + 1

    # Pattern insights — cross-stock correlations
    insights = _discover_patterns(predictions, zbgc_stocks, industry_counts)

    return {
        "date": zt_pool.get("date", date or datetime.now().strftime("%Y%m%d")),
        "total_zt": total_zt,
        "total_zbgc": total_zbgc,
        "env_failure_rate": round(env_failure_rate, 3),
        "risk_summary": risk_summary,
        "predictions": predictions,
        "pattern_insights": insights,
    }


def _discover_patterns(
    predictions: list[dict[str, Any]],
    zbgc_stocks: list[dict[str, Any]],
    industry_counts: dict[str, int],
) -> list[str]:
    """Discover cross-stock patterns from today's data."""
    insights: list[str] = []

    if not predictions:
        return insights

    # Pattern 1: High-risk concentration in specific industries
    high_risk = [p for p in predictions if p["risk_score"] >= 50]
    if high_risk:
        hr_industries: dict[str, int] = {}
        for p in high_risk:
            ind = p.get("industry", "")
            if ind:
                hr_industries[ind] = hr_industries.get(ind, 0) + 1
        for ind, cnt in sorted(hr_industries.items(), key=lambda x: x[1], reverse=True):
            if cnt >= 2:
                insights.append(
                    f"⚠️ {ind}板块有{cnt}只高风险股，板块可能整体走弱"
                )

    # Pattern 2: Late board time cluster
    late_boards = [p for p in predictions if _parse_board_time(p.get("first_board_time", "")) is not None
                   and _parse_board_time(p["first_board_time"]) > 3.5]  # after 13:00
    if len(late_boards) >= 3:
        insights.append(
            f"⚠️ {len(late_boards)}只股票午后封板，市场追高意愿弱"
        )

    # Pattern 3: Already-failed stocks in same industries as current ZT
    if zbgc_stocks:
        zbgc_industries: dict[str, int] = {}
        for s in zbgc_stocks:
            ind = s.get("industry", "").strip()
            if ind:
                zbgc_industries[ind] = zbgc_industries.get(ind, 0) + 1

        for ind, cnt in sorted(zbgc_industries.items(), key=lambda x: x[1], reverse=True):
            remaining_zt = industry_counts.get(ind, 0)
            if cnt >= 2 and remaining_zt > 0:
                insights.append(
                    f"💥 {ind}板块已炸板{cnt}只，剩余{remaining_zt}只涨停需警惕"
                )

    # Pattern 4: Overall low seal ratios
    seal_ratios = [p["seal_ratio"] for p in predictions if p.get("seal_ratio") and p["seal_ratio"] > 0]
    if seal_ratios:
        avg_seal = sum(seal_ratios) / len(seal_ratios)
        if avg_seal < 0.3:
            insights.append(
                f"⚠️ 整体封单比均值{avg_seal:.2f}偏低，市场封板意愿弱"
            )

    # Pattern 5: High turnover across board
    high_turnover = [p for p in predictions if p.get("turnover_rate", 0) > 20]
    if len(high_turnover) >= 5:
        insights.append(
            f"⚡ {len(high_turnover)}只股票换手率超20%，市场投机氛围浓"
        )

    return insights
