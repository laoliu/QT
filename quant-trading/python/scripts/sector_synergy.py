#!/usr/bin/env python3
"""
sector_synergy — 板块协同追涨分析脚本 (TypeScript bridge 入口)

供 AI 工具调用，输出 JSON。

Actions:
  scan      — 扫描当前板块协同状态（各板块涨停数、热门板块排名）
  analyze   — 对指定股票列表进行板块协同+大单+斜率综合评分
  hot       — 热门板块（涨停数≥N的板块及其成分股）
  config    — 返回策略参数配置说明

Usage:
  python -m scripts.sector_synergy --json  (via TypeScript bridge)
  python scripts/sector_synergy.py scan
  python scripts/sector_synergy.py analyze --symbols 002378,600519
  python scripts/sector_synergy.py hot --min-zt 2
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from typing import Any

# Proxy bypass (CN financial APIs blocked via proxy)
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

from engines.lianban import _safe_float, _safe_int, fetch_zt_pool
from engines.market_sentiment import lianban_ladder, sentiment_score


def _output(data: dict) -> None:
    """Print JSON output for TypeScript bridge consumption."""
    print(json.dumps(data, ensure_ascii=False, indent=2))


# ── Actions ──────────────────────────────────────────────────────

def action_scan(date: str | None = None) -> dict[str, Any]:
    """Scan current sector synergy status.

    Returns per-sector limit-up counts, hot sector ranking, and
    comprehensive market sentiment context.
    """
    pool = fetch_zt_pool(date)
    if pool.get("error"):
        return {"error": pool["error"]}

    stocks = pool.get("stocks", [])
    if not stocks:
        return {
            "date": date or datetime.now().strftime("%Y%m%d"),
            "total_zt": 0,
            "hot_sectors": [],
            "all_sectors": {},
            "sentiment": {},
        }

    # Build per-sector counts
    sector_counts: dict[str, dict[str, Any]] = {}
    for s in stocks:
        industry = s.get("industry", "") or "δ֪"
        if industry not in sector_counts:
            sector_counts[industry] = {
                "name": industry,
                "count": 0,
                "stocks": [],
                "total_amount_yi": 0.0,
            }
        info = sector_counts[industry]
        info["count"] += 1
        info["stocks"].append({
            "code": s.get("code", ""),
            "name": s.get("name", ""),
            "lian_ban_count": _safe_int(s.get("lian_ban_count")),
            "change_pct": _safe_float(s.get("change_pct")),
            "turnover_rate": _safe_float(s.get("turnover_rate")),
            "amount_yi": round(_safe_float(s.get("amount")) / 1e8, 2),
        })
        info["total_amount_yi"] += _safe_float(s.get("amount")) / 1e8

    # Sort by count desc
    all_sectors = sorted(sector_counts.values(), key=lambda x: -x["count"])

    # Hot sectors (count >= 2)
    hot_sectors = [s for s in all_sectors if s["count"] >= 2]

    # Market sentiment context
    try:
        sent = sentiment_score(date)
    except Exception:
        sent = {}

    return {
        "date": pool.get("date", date or datetime.now().strftime("%Y%m%d")),
        "total_zt": len(stocks),
        "hot_sector_count": len(hot_sectors),
        "hot_sectors": [
            {
                "name": s["name"],
                "zt_count": s["count"],
                "total_amount_yi": round(s["total_amount_yi"], 2),
                "stocks": s["stocks"][:8],  # top 8 per sector
            }
            for s in hot_sectors
        ],
        "all_sectors": {
            s["name"]: s["count"] for s in all_sectors
        },
        "sentiment": sent,
    }


def action_hot(min_zt: int = 2, date: str | None = None) -> dict[str, Any]:
    """Hot sectors with detailed stock lists.

    Shows sectors with >= min_zt limit-up stocks, with full stock details
    for each sector.
    """
    result = action_scan(date)
    if result.get("error"):
        return result

    # Filter to hot sectors only
    hot = [s for s in result.get("hot_sectors", []) if s["zt_count"] >= min_zt]

    return {
        "date": result.get("date", ""),
        "total_zt": result.get("total_zt", 0),
        "min_zt_threshold": min_zt,
        "hot_sector_count": len(hot),
        "hot_sectors": hot,
        "sentiment": result.get("sentiment", {}),
    }


def action_analyze(symbols: list[str], date: str | None = None) -> dict[str, Any]:
    """Analyze specified stocks for sector synergy suitability.

    Checks each stock's sector against the hot sectors map to determine
    if it has sector synergy support. Also reports basic eligibility
    for the sector synergy chase strategy.

    Args:
        symbols: list of stock codes (e.g. ["002378", "600519"])
        date: optional date string YYYYMMDD
    """
    scan_result = action_scan(date)
    if scan_result.get("error"):
        return scan_result

    all_sectors = scan_result.get("all_sectors", {})
    hot_sectors = {s["name"]: s for s in scan_result.get("hot_sectors", [])}

    # Try to look up each stock's sector from the ZT pool
    pool = fetch_zt_pool(date)
    stock_sector_map: dict[str, str] = {}
    stock_info_map: dict[str, dict] = {}
    for s in pool.get("stocks", []):
        code = s.get("code", "")
        pure = code.split(".")[0] if "." in code else code
        stock_sector_map[code] = s.get("industry", "")
        stock_sector_map[pure] = s.get("industry", "")
        stock_info_map[code] = s
        stock_info_map[pure] = s

    analysis: list[dict] = []
    for sym in symbols:
        pure = sym.split(".")[0] if "." in sym else sym
        sector = stock_sector_map.get(pure, "") or stock_sector_map.get(sym, "")
        info = stock_info_map.get(pure, {}) or stock_info_map.get(sym, {})

        sector_zt_count = all_sectors.get(sector, 0) if sector else 0
        in_hot_sector = sector in hot_sectors
        change_pct = _safe_float(info.get("change_pct"))
        turnover = _safe_float(info.get("turnover_rate"))

        # Basic eligibility check
        eligible_reasons = []
        ineligible_reasons = []

        if 6.0 <= change_pct <= 9.0:
            eligible_reasons.append(f"涨幅{change_pct:.1f}%在6-9%区间")
        elif change_pct > 0:
            ineligible_reasons.append(f"涨幅{change_pct:.1f}%不在6-9%区间")

        if sector_zt_count >= 2:
            eligible_reasons.append(f"板块[{sector}]有{sector_zt_count}只涨停")
        elif sector:
            ineligible_reasons.append(f"板块[{sector}]仅{sector_zt_count}只涨停")
        else:
            ineligible_reasons.append("无板块数据")

        if 5.0 <= turnover <= 20.0:
            eligible_reasons.append(f"换手率{turnover:.1f}%合适")
        elif turnover > 0:
            ineligible_reasons.append(f"换手率{turnover:.1f}%不在5-20%区间")

        analysis.append({
            "symbol": sym,
            "name": info.get("name", ""),
            "sector": sector,
            "sector_zt_count": sector_zt_count,
            "in_hot_sector": in_hot_sector,
            "change_pct": change_pct,
            "turnover_rate": turnover,
            "eligible_reasons": eligible_reasons,
            "ineligible_reasons": ineligible_reasons,
            "is_zt": change_pct >= 9.5,
            "lian_ban_count": _safe_int(info.get("lian_ban_count")),
        })

    return {
        "date": scan_result.get("date", ""),
        "total_zt": scan_result.get("total_zt", 0),
        "hot_sector_count": scan_result.get("hot_sector_count", 0),
        "analysis": analysis,
    }


def action_config() -> dict[str, Any]:
    """Return strategy parameter documentation."""
    return {
        "strategy_id": "sector-synergy-chase",
        "name": "板块协同大单追涨策略",
        "description": "在封板前6%~9%区间介入，结合板块协同、大单净额、攻击斜率等6个条件综合确认",
        "buy_conditions": [
            {"name": "涨幅区间", "param": "CHANGE_LOW/HIGH", "default": "6%~9%",
             "description": "股价涨幅须在此区间内(vs昨收)"},
            {"name": "板块协同", "param": "SECTOR_LIMIT_MIN", "default": 2,
             "description": "该股所属板块内涨停股数量>=此值"},
            {"name": "换手率", "param": "TURNOVER_LOW/HIGH", "default": "5%~20%",
             "description": "当日实际换手率须在合理区间"},
            {"name": "攻击斜率", "param": "SLOPE_ANGLE_MIN", "default": "60°",
             "description": "5分钟价格斜率角度>此值,确认急速拉升"},
            {"name": "大单净额", "param": "NET_AMOUNT_MIN", "default": "5000万",
             "description": "累计主力大单(>=100万/tick)净买入>此值"},
            {"name": "扫单信号", "param": "SWEEP_VOLUME", "default": "1万手",
             "description": "在8%~9%涨幅区间出现单笔>=1万手主动买入"},
        ],
        "sell_rules": [
            {"rule": "止盈全卖", "trigger": "盈利>=10%", "action": "全部卖出"},
            {"rule": "止盈半卖", "trigger": "盈利>=5%", "action": "卖出一半(仅一次)"},
            {"rule": "止损", "trigger": "亏损>=3%", "action": "全部卖出"},
        ],
        "complementary_strategy": "qmt_lianban_monitor (封板后确认)",
        "engines_used": [
            "engines/tick_flow.py — tick级大单追踪",
            "engines/attack_slope.py — 攻击斜率计算",
        ],
        "note": "此策略需在QMT tick驱动模式下运行, 此处仅提供板块协同分析和参数查看",
    }


# ── CLI Entry ────────────────────────────────────────────────────

def main():
    import argparse

    # Support QUANT_CONFIG env var (used by TypeScript bridge)
    quant_config = os.environ.get("QUANT_CONFIG")
    if quant_config:
        try:
            cfg = json.loads(quant_config)
        except json.JSONDecodeError:
            cfg = {}
        action = cfg.get("action", "scan")
        date = cfg.get("date")
        symbols_str = cfg.get("symbols", "")
        min_zt = int(cfg.get("min_zt", 2))
    else:
        parser = argparse.ArgumentParser(description="板块协同追涨分析")
        parser.add_argument("action", choices=["scan", "hot", "analyze", "config"],
                            help="scan=板块协同概览, hot=热门板块, analyze=个股分析, config=策略说明")
        parser.add_argument("--date", default=None, help="日期 YYYYMMDD")
        parser.add_argument("--symbols", default="", help="股票代码(逗号分隔)")
        parser.add_argument("--min-zt", type=int, default=2, help="热门板块最低涨停数(默认2)")
        args = parser.parse_args()
        action = args.action
        date = args.date
        symbols_str = args.symbols
        min_zt = args.min_zt

    try:
        if action == "scan":
            result = action_scan(date)
        elif action == "hot":
            result = action_hot(min_zt, date)
        elif action == "analyze":
            symbols = [s.strip() for s in symbols_str.split(",") if s.strip()] if symbols_str else []
            if not symbols:
                result = {"error": "请提供股票代码 (symbols)"}
            else:
                result = action_analyze(symbols, date)
        elif action == "config":
            result = action_config()
        else:
            result = {"error": f"δ֪ action: {action}"}

        _output(result)
    except Exception as e:
        _output({
            "error": str(e),
            "traceback": traceback.format_exc(),
        })
        sys.exit(1)


if __name__ == "__main__":
    main()
