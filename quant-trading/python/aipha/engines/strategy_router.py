"""
Strategy Router — map stock profiles to optimal strategy combinations.

Two routing modes:
  1. Rule-based: expert rules mapping (volatility, trend, market_cap) → combo
  2. Data-driven: pre-built routing table from historical backtests

Entry via stdin JSON:
  {"action": "recommend", "symbols": ["000001"], "endDate": "2026-03-20"}
  {"action": "route_portfolio", "symbols": [...], "endDate": "2026-03-20"}
  {"action": "get_routing_table"}
  {"action": "profile_route_backtest", "symbols": ["600519"], "startDate": "...", "endDate": "..."}
  {"action": "enrich_with_routing", "candidates": [...], "endDate": "..."}
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

_this_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_this_dir)
if _python_root not in sys.path:
    sys.path.insert(0, _python_root)

from aipha.engines.stock_profiler import profile_stock

ROUTING_TABLE_PATH = os.path.expanduser(
    "~/.openclaw/quant-trading/strategy_routing_table.json"
)

# ── Rule-Based Routing Defaults ──────────────────────────────────
# Each rule: { profileKey pattern → combo config }
# profileKey format: "volatility|trend|marketCap"
# Uses prefix matching: "high|uptrend|*" matches any market cap

RULE_BASED_DEFAULTS: list[dict] = [
    # High volatility + uptrend → MACD+KDJ strict voting (momentum confirmation)
    {
        "match": {"volatility": "high", "trend": "uptrend"},
        "combo": {
            "name": "趋势确认(高波动)",
            "method": "voting",
            "strategies": [
                {"strategyId": "macd", "weight": 1},
                {"strategyId": "kdj", "weight": 1},
            ],
            "minVotes": 2,
        },
        "reason": "高波动趋势市场，MACD+KDJ 共振确认减少假信号",
    },
    # High volatility + sideways → Bollinger+MeanReversion gated
    {
        "match": {"volatility": "high", "trend": "sideways"},
        "combo": {
            "name": "均值回归(高波动震荡)",
            "method": "gated",
            "strategies": [
                {"strategyId": "bollinger", "weight": 1, "role": "primary"},
                {"strategyId": "mean-reversion-cn", "weight": 1, "role": "filter"},
            ],
        },
        "reason": "高波动震荡市场，布林带回归配合均值回归过滤",
    },
    # Low volatility + uptrend → Turtle+MACD cascade (trend following)
    {
        "match": {"volatility": "low", "trend": "uptrend"},
        "combo": {
            "name": "趋势跟踪(低波动)",
            "method": "cascade",
            "strategies": [
                {"strategyId": "turtle", "weight": 1},
                {"strategyId": "macd", "weight": 1},
            ],
        },
        "reason": "低波动上升趋势，海龟突破+MACD 级联顺势跟踪",
    },
    # Low volatility + sideways → Grid trading
    {
        "match": {"volatility": "low", "trend": "sideways"},
        "combo": {
            "name": "网格交易(低波动震荡)",
            "method": "voting",
            "strategies": [{"strategyId": "grid", "weight": 1}],
            "minVotes": 1,
        },
        "reason": "低波动震荡适合网格交易，在价格区间内高抛低吸",
    },
    # Medium vol + uptrend + large cap → AI exploration #1
    {
        "match": {"volatility": "medium", "trend": "uptrend", "marketCap": "large"},
        "combo": {
            "name": "MACD+KDJ+多因子(大盘趋势)",
            "method": "voting",
            "strategies": [
                {"strategyId": "macd", "weight": 1},
                {"strategyId": "kdj", "weight": 1},
                {"strategyId": "multi-factor", "weight": 1},
            ],
            "minVotes": 2,
        },
        "reason": "大盘上涨趋势，三因子投票(AI探索#1, 23.4%平均收益)",
    },
    # Medium vol + uptrend + small/micro cap → KDJ+竞价
    {
        "match": {"volatility": "medium", "trend": "uptrend", "marketCap": "small"},
        "combo": {
            "name": "KDJ+竞价(小盘趋势)",
            "method": "voting",
            "strategies": [
                {"strategyId": "kdj", "weight": 1},
                {"strategyId": "auction", "weight": 1},
            ],
            "minVotes": 1,
        },
        "reason": "小盘趋势股，KDJ+竞价投票(AI探索#2, 21.1%平均收益)",
    },
    # Medium vol + uptrend (mid cap, default)
    {
        "match": {"volatility": "medium", "trend": "uptrend"},
        "combo": {
            "name": "MACD+KDJ+均值回归(中盘趋势)",
            "method": "voting",
            "strategies": [
                {"strategyId": "macd", "weight": 1},
                {"strategyId": "kdj", "weight": 1},
                {"strategyId": "mean-reversion-cn", "weight": 1},
            ],
            "minVotes": 2,
        },
        "reason": "中波动上涨趋势，MACD+KDJ+均值回归三投票(Phase1 #1, Sharpe 0.99)",
    },
    # Medium vol + sideways → MACD+KDJ+网格
    {
        "match": {"volatility": "medium", "trend": "sideways"},
        "combo": {
            "name": "MACD+KDJ+网格(震荡市)",
            "method": "voting",
            "strategies": [
                {"strategyId": "macd", "weight": 1},
                {"strategyId": "kdj", "weight": 1},
                {"strategyId": "grid", "weight": 1},
            ],
            "minVotes": 2,
        },
        "reason": "中波动震荡市场，网格捕捉区间+MACD/KDJ过滤(Phase2 #5)",
    },
    # Any downtrend → Conservative: MACD+KDJ+布林+均值回归 strict voting
    {
        "match": {"trend": "downtrend"},
        "combo": {
            "name": "保守四投票(下跌市)",
            "method": "voting",
            "strategies": [
                {"strategyId": "macd", "weight": 1},
                {"strategyId": "kdj", "weight": 1},
                {"strategyId": "bollinger", "weight": 1},
                {"strategyId": "mean-reversion-cn", "weight": 1},
            ],
            "minVotes": 3,
        },
        "reason": "下跌趋势，严格四投票3/4(仅2.2%收益但回撤仅3.6%，风控优先)",
    },
]

# Catch-all fallback
FALLBACK_COMBO = {
    "name": "MACD+KDJ 默认",
    "method": "voting",
    "strategies": [
        {"strategyId": "macd", "weight": 1},
        {"strategyId": "kdj", "weight": 1},
    ],
    "minVotes": 2,
}
FALLBACK_REASON = "无匹配规则，使用 MACD+KDJ 默认组合(回测 6.1% 收益, Sharpe 0.45)"


# ── Routing Logic ─────────────────────────────────────────────────


def _load_routing_table() -> dict | None:
    """Load pre-built routing table from disk."""
    try:
        with open(ROUTING_TABLE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _match_rule(profile: dict, rule: dict) -> bool:
    """Check if a stock profile matches a rule's conditions."""
    match = rule["match"]
    for key, expected in match.items():
        if key == "volatility":
            actual = profile.get("volatility", {}).get("class", "")
        elif key == "trend":
            actual = profile.get("trend", {}).get("class", "")
        elif key == "marketCap":
            actual = profile.get("marketCap", {}).get("class", "")
        elif key == "liquidity":
            actual = profile.get("liquidity", {}).get("class", "")
        elif key == "momentum":
            actual = profile.get("momentum", {}).get("class", "")
        elif key == "sector":
            actual = profile.get("sector", "")
        else:
            continue
        if actual != expected:
            return False
    return True


def recommend_for_stock(symbol: str, end_date: str) -> dict:
    """Recommend best strategy combo for a single stock."""
    profile = profile_stock(symbol, end_date)
    if not profile.get("ok"):
        return {"symbol": symbol, "ok": False, "error": profile.get("error", "画像失败")}

    # Try data-driven routing table first
    routing_table = _load_routing_table()
    if routing_table:
        profile_key = profile.get("profileKey", "")
        entry = routing_table.get("routes", {}).get(profile_key)
        if entry:
            return {
                "symbol": symbol,
                "ok": True,
                "profile": profile,
                "combo": entry["combo"],
                "reason": entry.get("reason", "数据驱动路由"),
                "source": "data-driven",
                "performance": entry.get("performance"),
            }

    # Fall back to rule-based matching (most specific first)
    for rule in RULE_BASED_DEFAULTS:
        if _match_rule(profile, rule):
            return {
                "symbol": symbol,
                "ok": True,
                "profile": profile,
                "combo": rule["combo"],
                "reason": rule["reason"],
                "source": "rule-based",
            }

    # Final fallback
    return {
        "symbol": symbol,
        "ok": True,
        "profile": profile,
        "combo": FALLBACK_COMBO,
        "reason": FALLBACK_REASON,
        "source": "fallback",
    }


def route_portfolio(symbols: list[str], end_date: str) -> dict:
    """Route strategy combos for a portfolio of stocks."""
    results = []
    combo_groups: dict[str, list[str]] = {}  # combo_name → [symbols]

    for sym in symbols:
        rec = recommend_for_stock(sym, end_date)
        results.append(rec)
        if rec.get("ok"):
            combo_name = rec["combo"]["name"]
            combo_groups.setdefault(combo_name, []).append(sym)

    # Summary stats
    sources = [r.get("source", "unknown") for r in results if r.get("ok")]
    return {
        "ok": True,
        "totalStocks": len(symbols),
        "routed": len([r for r in results if r.get("ok")]),
        "failed": len([r for r in results if not r.get("ok")]),
        "results": results,
        "comboGroups": combo_groups,
        "sourceBreakdown": {
            "data-driven": sources.count("data-driven"),
            "rule-based": sources.count("rule-based"),
            "fallback": sources.count("fallback"),
        },
    }


def get_routing_table() -> dict:
    """Return the current routing table (data-driven or defaults)."""
    table = _load_routing_table()
    if table:
        return {"ok": True, "source": "data-driven", **table}
    # Return rule-based defaults
    return {
        "ok": True,
        "source": "rule-based",
        "rules": [
            {
                "match": r["match"],
                "combo": r["combo"]["name"],
                "reason": r["reason"],
            }
            for r in RULE_BASED_DEFAULTS
        ],
        "fallback": {"combo": FALLBACK_COMBO["name"], "reason": FALLBACK_REASON},
    }


# ── Cache-first data fetching for backtest pipeline ──────────────

_cache_patched = False


def _ensure_cache_fetch():
    """Monkey-patch backtrader_engine._fetch_data to prefer SQLite cache.

    This avoids network calls for stocks already in our local cache DB,
    falling back to the original fetch when cache misses.
    """
    global _cache_patched  # noqa: PLW0603
    if _cache_patched:
        return

    try:
        import engines.backtrader_engine as bt_eng
        import pandas as pd

        _original_fetch = bt_eng._fetch_data
        _db_path = os.path.expanduser(
            "~/.openclaw/extensions/quant-trading/cache/stock_data.db"
        )

        def _cache_fetch(symbol, start_date, end_date, market=None):
            try:
                import sqlite3
                conn = sqlite3.connect(_db_path)
                df = pd.read_sql_query(
                    "SELECT trade_date as date, open_price as open, "
                    "high, low, close_price as close, volume "
                    "FROM kline_data WHERE symbol=? AND trade_date>=? AND trade_date<=? "
                    "ORDER BY trade_date",
                    conn,
                    params=(symbol, start_date, end_date),
                )
                conn.close()
                if not df.empty:
                    df["date"] = pd.to_datetime(df["date"])
                    df = df.set_index("date")
                    return df
            except Exception:
                pass
            return _original_fetch(symbol, start_date, end_date, market)

        bt_eng._fetch_data = _cache_fetch
        _cache_patched = True
    except Exception:
        pass


# ── Profile → Route → Backtest Pipeline ──────────────────────────


def profile_route_backtest(
    symbols: list[str],
    start_date: str,
    end_date: str,
    initial_cash: float = 100_000,
    commission: float = 0.0003,
) -> dict:
    """Unified pipeline: profile each stock → route to combo → run composed backtest."""
    _ensure_cache_fetch()
    from aipha.engines.dynamic_composer import compose_backtest

    results = []
    for sym in symbols:
        rec = recommend_for_stock(sym, end_date)
        if not rec.get("ok"):
            results.append({
                "symbol": sym,
                "ok": False,
                "error": rec.get("error", "路由失败"),
            })
            continue

        combo = rec["combo"]
        bt_config = {
            "method": combo.get("method", "voting"),
            "strategies": combo.get("strategies", []),
            "symbols": [sym],
            "startDate": start_date,
            "endDate": end_date,
            "initialCash": initial_cash,
            "commission": commission,
            "minVotes": combo.get("minVotes", 1),
            "scoreThreshold": combo.get("scoreThreshold", 0.5),
        }

        try:
            bt_result = compose_backtest(bt_config)
        except Exception as e:
            bt_result = {"ok": False, "error": str(e)}

        metrics = bt_result.get("combinedMetrics", {}) if bt_result.get("ok") else {}
        results.append({
            "symbol": sym,
            "name": rec.get("profile", {}).get("name", sym),
            "ok": bt_result.get("ok", False),
            "profileKey": rec.get("profile", {}).get("profileKey", ""),
            "combo": {
                "name": combo.get("name", ""),
                "method": combo.get("method", ""),
                "strategies": [s.get("strategyId", "") for s in combo.get("strategies", [])],
            },
            "routingSource": rec.get("source", "unknown"),
            "reason": rec.get("reason", ""),
            "backtest": {
                "totalReturn": metrics.get("totalReturn", 0),
                "sharpeRatio": metrics.get("sharpeRatio", 0),
                "maxDrawdown": metrics.get("maxDrawdown", 0),
                "winRate": metrics.get("winRate", 0),
                "totalTrades": metrics.get("totalTrades", 0),
            } if metrics else None,
            "error": bt_result.get("error") if not bt_result.get("ok") else None,
        })

    return {"ok": True, "results": results, "total": len(results)}


def enrich_candidates_with_routing(
    candidates: list[dict],
    end_date: str,
) -> list[dict]:
    """Add routing info (profile + combo recommendation) to a list of stock candidates.

    Used by the daily-picks pipeline to annotate each final candidate with its
    adaptive routing recommendation without running additional backtests.
    """
    enriched = []
    for c in candidates:
        sym = c.get("symbol", c.get("code", ""))
        if not sym:
            enriched.append({**c, "routing": None})
            continue

        rec = recommend_for_stock(sym, end_date)
        if not rec.get("ok"):
            enriched.append({**c, "routing": None})
            continue

        profile = rec.get("profile", {})
        combo = rec.get("combo", {})
        perf = rec.get("performance")

        enriched.append({
            **c,
            "routing": {
                "profileKey": profile.get("profileKey", ""),
                "volatility": profile.get("volatility", {}).get("class", ""),
                "trend": profile.get("trend", {}).get("class", ""),
                "marketCap": profile.get("marketCap", {}).get("class", ""),
                "combo": combo.get("name", ""),
                "comboMethod": combo.get("method", ""),
                "strategies": [s.get("strategyId", "") for s in combo.get("strategies", [])],
                "source": rec.get("source", ""),
                "reason": rec.get("reason", ""),
                "performance": {
                    "avgReturn": perf.get("avgReturn", 0),
                    "avgSharpe": perf.get("avgSharpe", 0),
                    "avgMaxDD": perf.get("avgMaxDD", 0),
                } if perf else None,
            },
        })

    return enriched


# ── Market Regime Integration ─────────────────────────────────────


def get_market_regime() -> dict:
    """Get current macro regime from macro_economic engine."""
    try:
        from aipha.engines.macro_economic import run_macro_economic
        result = run_macro_economic({"action": "regime", "market": "cn"})
        return result
    except Exception:
        return {"regime": "unknown", "description": ""}


_REGIME_COMBO_ADJUSTMENTS: dict[str, dict] = {
    # In recession/stagflation: prefer defensive combos
    "recession": {
        "preferConservative": True,
        "minVotesBoost": 1,  # require stricter confirmation
        "description": "衰退期: 提高投票门槛，倾向保守策略",
    },
    "stagflation": {
        "preferConservative": True,
        "minVotesBoost": 1,
        "description": "滞胀期: 防御为主，严格信号确认",
    },
    # In recovery: more aggressive, lower vote thresholds
    "recovery": {
        "preferConservative": False,
        "minVotesBoost": 0,
        "description": "复苏期: 对权益最友好，正常信号门槛",
    },
    # In overheating: moderate caution
    "overheating": {
        "preferConservative": False,
        "minVotesBoost": 0,
        "description": "过热期: 关注紧缩风险，维持正常门槛",
    },
}


def recommend_with_regime(symbol: str, end_date: str) -> dict:
    """Recommend with macro regime adjustment applied to combo parameters."""
    rec = recommend_for_stock(symbol, end_date)
    if not rec.get("ok"):
        return rec

    regime_info = get_market_regime()
    regime = regime_info.get("regime", "unknown")
    adjustment = _REGIME_COMBO_ADJUSTMENTS.get(regime)

    rec["macroRegime"] = regime
    rec["macroDescription"] = regime_info.get("description", "")

    if adjustment:
        rec["regimeAdjustment"] = adjustment["description"]
        combo = rec.get("combo", {})
        # Boost minVotes for defensive regimes
        boost = adjustment.get("minVotesBoost", 0)
        if boost > 0 and combo.get("method") == "voting":
            original_votes = combo.get("minVotes", 1)
            max_votes = len(combo.get("strategies", []))
            combo["minVotes"] = min(original_votes + boost, max_votes)
            rec["combo"] = combo

    return rec


# ── CLI / stdin entry ─────────────────────────────────────────────


def main():
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        config = json.loads(config_str)
    else:
        config = json.loads(sys.stdin.read())

    action = config.get("action", "recommend")
    end_date = config.get("endDate", "2026-03-20")

    if action == "recommend":
        symbols = config.get("symbols", [])
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",") if s.strip()]
        results = [recommend_for_stock(s, end_date) for s in symbols]
        print(json.dumps({"ok": True, "recommendations": results}, ensure_ascii=False))

    elif action == "recommend_with_regime":
        symbols = config.get("symbols", [])
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",") if s.strip()]
        results = [recommend_with_regime(s, end_date) for s in symbols]
        print(json.dumps({"ok": True, "recommendations": results}, ensure_ascii=False))

    elif action == "route_portfolio":
        symbols = config.get("symbols", [])
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",") if s.strip()]
        result = route_portfolio(symbols, end_date)
        print(json.dumps(result, ensure_ascii=False))

    elif action == "get_routing_table":
        result = get_routing_table()
        print(json.dumps(result, ensure_ascii=False))

    elif action == "profile_route_backtest":
        symbols = config.get("symbols", [])
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",") if s.strip()]
        start_date = config.get("startDate", "2024-10-08")
        initial_cash = float(config.get("initialCash", 100_000))
        commission = float(config.get("commission", 0.0003))
        result = profile_route_backtest(
            symbols, start_date, end_date, initial_cash, commission,
        )
        print(json.dumps(result, ensure_ascii=False))

    elif action == "enrich_with_routing":
        candidates = config.get("candidates", [])
        result = enrich_candidates_with_routing(candidates, end_date)
        print(json.dumps({"ok": True, "enriched": result}, ensure_ascii=False))

    elif action == "get_regime":
        result = get_market_regime()
        print(json.dumps({"ok": True, **result}, ensure_ascii=False))

    else:
        print(json.dumps({"ok": False, "error": f"Unknown action: {action}"}))


if __name__ == "__main__":
    main()
