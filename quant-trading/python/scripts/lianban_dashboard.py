#!/usr/bin/env python3
"""
Lianban Dashboard — 连板梯队总览与二进三候选分析。

类似 institution_activity.py 的独立分析脚本。
输出 JSON 供 TypeScript AI 工具消费。

Actions:
  scan   — 输出当日连板梯队全景 (最高板、各层级股票、晋级率、炸板率、情绪评分)
  detail — 单只股票的连板轨迹分析 (板质评分、量能变化、机构参与度)
  candidates — 输出二进三候选列表 (评分排序)
  zhaban — 炸板风险预测，对当前涨停股进行多因子评分排序

Usage:
  python lianban_dashboard.py scan
  python lianban_dashboard.py scan --date 20260315
  python lianban_dashboard.py detail --symbol 600519
  python lianban_dashboard.py candidates --min-boards 2 --limit 20
  python lianban_dashboard.py zhaban --min-risk 30
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from typing import Any

# Proxy bypass
for _k in (
    "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
    "all_proxy", "ALL_PROXY",
):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PYTHON_ROOT)

from engines.lianban import (
    _safe_float,
    _safe_int,
    board_quality_score,
    count_consecutive_boards,
    fetch_zt_pool,
    fetch_zt_pool_previous,
    fetch_zt_pool_zbgc,
    get_board_history,
)
from engines.market_sentiment import (
    lianban_ladder,
    promotion_rate,
    sentiment_score,
    zbgc_rate,
    zhangting_dieting_ratio,
)
from engines.zhaban_predictor import predict_zhaban


def _output(data: dict[str, Any]) -> None:
    """Print JSON to stdout for TypeScript consumption."""
    print(json.dumps(data, ensure_ascii=False, indent=2))


def action_scan(date: str | None = None) -> dict[str, Any]:
    """连板梯队全景扫描。"""
    warnings: list[str] = []

    # 1. Ladder
    ladder = lianban_ladder(date)
    if ladder.get("error"):
        warnings.append(f"连板梯队: {ladder['error']}")

    # 2. Sentiment
    env = sentiment_score(date)

    # 3. ZT/DT ratio
    zd = zhangting_dieting_ratio(date)

    # 4. Failure rate
    failure = zbgc_rate(date)

    # 5. Promotion rates (2→3, 3→4)
    promo_2to3 = None
    promo_3to4 = None
    try:
        today_pool = fetch_zt_pool(date).get("stocks", [])
        yesterday_pool = fetch_zt_pool_previous(date).get("stocks", [])
        if today_pool and yesterday_pool:
            promo_2to3 = promotion_rate(today_pool, yesterday_pool, from_n=2)
            promo_3to4 = promotion_rate(today_pool, yesterday_pool, from_n=3)
    except Exception as e:
        warnings.append(f"晋级率计算: {e}")

    # 6. Failed boards detail
    zbgc = fetch_zt_pool_zbgc(date)
    zbgc_stocks = zbgc.get("stocks", [])[:10]  # Top 10

    # 7. Stocks by level (from ladder)
    stocks_by_level = ladder.get("stocks_by_level", {})
    # Format for display
    level_display: dict[str, list[dict[str, str]]] = {}
    for level, stocks in sorted(stocks_by_level.items(), reverse=True):
        level_display[f"{level}连板"] = [
            {
                "code": s.get("code", ""),
                "name": s.get("name", ""),
                "industry": s.get("industry", ""),
                "turnover_rate": f"{_safe_float(s.get('turnover_rate')):.1f}%",
                "amount_yi": f"{_safe_float(s.get('amount')) / 1e8:.2f}亿",
            }
            for s in stocks[:15]
        ]

    result = {
        "action": "scan",
        "date": ladder.get("date", date or datetime.now().strftime("%Y%m%d")),
        "summary": {
            "total_zt": ladder.get("total_zt", 0),
            "highest_board": ladder.get("highest_board", 0),
            "ladder": ladder.get("ladder", {}),
            "sentiment_score": env.get("score", 0),
            "sentiment_level": env.get("level", ""),
            "suitable_for_relay": env.get("suitable_for_relay", False),
            "zt_dt_ratio": zd.get("ratio", 0),
            "zt_dt_tone": zd.get("tone", ""),
            "failure_rate": failure.get("failure_rate", 0),
            "failure_tone": failure.get("tone", ""),
        },
        "promotion": {},
        "stocks_by_level": level_display,
        "failed_boards": [
            {
                "code": s.get("code", ""),
                "name": s.get("name", ""),
                "change_pct": f"{_safe_float(s.get('change_pct')):.2f}%",
                "open_count": _safe_int(s.get("open_count")),
                "industry": s.get("industry", ""),
            }
            for s in zbgc_stocks
        ],
        "detail": env.get("detail", ""),
        "warnings": warnings,
    }

    if promo_2to3:
        result["promotion"]["2进3"] = {
            "rate": f"{promo_2to3['rate']*100:.1f}%",
            "promoted": promo_2to3["promoted_count"],
            "failed": promo_2to3["failed_count"],
            "total": promo_2to3["yesterday_count"],
            "promoted_stocks": promo_2to3.get("promoted", []),
            "failed_stocks": promo_2to3.get("failed", []),
        }
    if promo_3to4:
        result["promotion"]["3进4"] = {
            "rate": f"{promo_3to4['rate']*100:.1f}%",
            "promoted": promo_3to4["promoted_count"],
            "failed": promo_3to4["failed_count"],
            "total": promo_3to4["yesterday_count"],
        }

    return result


def action_detail(symbol: str, date: str | None = None) -> dict[str, Any]:
    """单只股票连板轨迹分析。"""
    warnings: list[str] = []

    if not symbol:
        return {"error": "请提供股票代码 (--symbol)", "action": "detail"}

    # Fetch K-lines for board history
    try:
        from data_providers.akshare_provider import get_klines
        from datetime import timedelta

        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")
        klines_result = get_klines(symbol, "1d", start_date, end_date)
        if isinstance(klines_result, dict) and klines_result.get("error"):
            warnings.append(f"K线: {klines_result['error']}")
            klines = []
        elif isinstance(klines_result, list):
            klines = klines_result
        else:
            klines = klines_result.get("klines", []) if isinstance(klines_result, dict) else []
    except Exception as e:
        warnings.append(f"K线获取: {e}")
        klines = []

    # Board quality scoring
    quality = {}
    board_count = 0
    boards = []
    if klines:
        board_count = count_consecutive_boards(klines, symbol)
        quality = board_quality_score(klines, symbol)
        boards = get_board_history(klines, symbol)

    # Check if stock is in today's pool
    pool_info = None
    pool = fetch_zt_pool(date)
    for s in pool.get("stocks", []):
        if s.get("code") == symbol or s.get("code") == symbol.split(".")[0]:
            pool_info = s
            break

    # If not in ZT pool and no boards detected, check failed-board pool (炸板)
    zbgc_info = None
    if board_count == 0 and pool_info is None:
        zbgc = fetch_zt_pool_zbgc(date)
        for s in zbgc.get("stocks", []):
            if s.get("code") == symbol or s.get("code") == symbol.split(".")[0]:
                zbgc_info = s
                break

    # Check dragon tiger list
    dragon_tiger_info = None
    try:
        from engines.dragon_tiger import run_dragon_tiger

        dt_result = run_dragon_tiger({"action": "stock", "symbol": symbol, "limit": 5})
        if not dt_result.get("error"):
            dragon_tiger_info = dt_result
    except Exception as e:
        warnings.append(f"龙虎榜: {e}")

    result = {
        "action": "detail",
        "symbol": symbol,
        "board_count": board_count,
        "quality": quality,
        "boards": boards,
        "pool_info": pool_info,
        "zbgc_info": zbgc_info,
        "dragon_tiger": dragon_tiger_info,
        "warnings": warnings,
    }

    return result


def action_candidates(
    min_boards: int = 2,
    limit: int = 20,
    date: str | None = None,
) -> dict[str, Any]:
    """输出二进三候选列表 (评分排序)。"""
    warnings: list[str] = []

    # Get pool
    pool = fetch_zt_pool(date)
    if pool.get("error"):
        return {"error": pool["error"], "action": "candidates", "candidates": []}

    stocks = pool.get("stocks", [])
    env = sentiment_score(date)

    # Filter candidates
    candidates: list[dict[str, Any]] = []
    for s in stocks:
        code = s.get("code", "")
        name = s.get("name", "")
        lian_ban = _safe_int(s.get("lian_ban_count"))

        if lian_ban < min_boards:
            continue
        if "ST" in name.upper():
            continue

        # Simple scoring (without full kline data)
        score = 50.0
        signals: list[str] = [f"{lian_ban}连板"]

        # Turnover rate
        turnover = _safe_float(s.get("turnover_rate"))
        if 5 <= turnover <= 15:
            score += 15
            signals.append(f"换手率{turnover:.1f}%(优)")
        elif 3 <= turnover <= 25:
            score += 8
            signals.append(f"换手率{turnover:.1f}%(中)")
        elif turnover > 25:
            score -= 5
            signals.append(f"换手率{turnover:.1f}%(高)")

        # Seal amount
        seal = _safe_float(s.get("seal_amount"))
        if seal >= 5e8:
            score += 10
            signals.append(f"封单{seal/1e8:.1f}亿(强)")
        elif seal >= 1e8:
            score += 5
            signals.append(f"封单{seal/1e8:.1f}亿")

        # Open count penalty
        open_count = _safe_int(s.get("open_count"))
        if open_count > 2:
            score -= 10
            signals.append(f"开板{open_count}次(多)")
        elif open_count > 0:
            score -= 3
            signals.append(f"开板{open_count}次")

        # Market cap
        flow = _safe_float(s.get("flow_value"))
        if 30e8 <= flow <= 150e8:
            score += 8
        elif flow > 0:
            score += 3

        # Board count bonus
        score += min(lian_ban * 3, 12)

        # Environment
        score += (env.get("score", 50) / 100) * 10

        candidates.append({
            "code": code,
            "name": name,
            "lian_ban_count": lian_ban,
            "score": round(min(score, 100), 1),
            "price": _safe_float(s.get("price")),
            "turnover_rate": round(turnover, 1),
            "amount_yi": round(_safe_float(s.get("amount")) / 1e8, 2),
            "flow_value_yi": round(flow / 1e8, 1) if flow > 0 else None,
            "seal_amount_yi": round(seal / 1e8, 2) if seal > 0 else None,
            "open_count": open_count,
            "industry": s.get("industry", ""),
            "signals": signals,
        })

    candidates.sort(key=lambda c: c["score"], reverse=True)
    candidates = candidates[:limit]
    for i, c in enumerate(candidates):
        c["rank"] = i + 1

    result = {
        "action": "candidates",
        "date": pool.get("date", ""),
        "min_boards": min_boards,
        "total_candidates": len(candidates),
        "environment": {
            "score": env.get("score", 0),
            "level": env.get("level", ""),
            "suitable": env.get("suitable_for_relay", False),
            "detail": env.get("detail", ""),
        },
        "candidates": candidates,
        "warnings": warnings,
    }

    return result


def action_zhaban(
    min_risk: int = 0,
    date: str | None = None,
) -> dict[str, Any]:
    """炸板风险预测 — 对当前涨停股按炸板概率排序。"""
    result = predict_zhaban(date=date, min_risk=min_risk)
    result["action"] = "zhaban"
    return result


# ── CLI Entry ────────────────────────────────────────────────────

def main():
    import argparse

    # Support QUANT_CONFIG env var (used by TypeScript bridge via runPythonScript)
    quant_config = os.environ.get("QUANT_CONFIG")
    if quant_config:
        try:
            cfg = json.loads(quant_config)
        except json.JSONDecodeError:
            cfg = {}
        action = cfg.get("action", "scan")
        date = cfg.get("date")
        symbol = cfg.get("symbol")
        min_boards = int(cfg.get("min_boards", 2))
        limit = int(cfg.get("limit", 20))
        min_risk = int(cfg.get("min_risk", 0))
    else:
        parser = argparse.ArgumentParser(description="连板梯队总览与二进三候选分析")
        parser.add_argument("action", choices=["scan", "detail", "candidates", "zhaban"],
                            help="scan=梯队全景, detail=单股分析, candidates=候选列表, zhaban=炸板预测")
        parser.add_argument("--date", default=None, help="日期 YYYYMMDD (默认今天)")
        parser.add_argument("--symbol", default=None, help="股票代码 (detail模式)")
        parser.add_argument("--min-boards", type=int, default=2, help="最低连板数 (默认2)")
        parser.add_argument("--limit", type=int, default=20, help="返回数量 (默认20)")
        parser.add_argument("--min-risk", type=int, default=0, help="最低风险分 (默认0)")
        args = parser.parse_args()
        action = args.action
        date = args.date
        symbol = args.symbol
        min_boards = args.min_boards
        limit = args.limit
        min_risk = args.min_risk

    try:
        if action == "scan":
            result = action_scan(date)
        elif action == "detail":
            result = action_detail(symbol, date)
        elif action == "candidates":
            result = action_candidates(min_boards, limit, date)
        elif action == "zhaban":
            result = action_zhaban(min_risk, date)
        else:
            result = {"error": f"未知 action: {action}"}

        _output(result)
    except Exception as e:
        _output({
            "error": str(e),
            "traceback": traceback.format_exc(),
        })
        sys.exit(1)


if __name__ == "__main__":
    main()
