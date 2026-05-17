#!/usr/bin/env python3
"""
Build Strategy Routing Table — run backtests grouped by stock profile.

For each stock in cache with sufficient data:
  1. Compute StockProfile
  2. Run top strategy combos (from AI exploration)
  3. Group results by profileKey (volatility|trend|marketCap)
  4. For each profile group, rank combos by score
  5. Output strategy_routing_table.json

Usage:
  python build_routing_table.py [--end-date 2026-03-20] [--min-bars 200]
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from typing import Any

_this_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_this_dir)
if _python_root not in sys.path:
    sys.path.insert(0, _python_root)

# Cache-based data fetching (same as _run_ai_exploration.py)
CACHE_DB = os.path.expanduser(
    "~/.openclaw/extensions/quant-trading/cache/stock_data.db"
)


def _fetch_from_cache(symbol, start_date, end_date, market=None):
    import pandas as pd
    try:
        conn = sqlite3.connect(CACHE_DB)
        df = pd.read_sql_query(
            "SELECT trade_date, open_price as open, high, low, "
            "close_price as close, volume "
            "FROM kline_data WHERE symbol = ? AND period = 'daily' "
            "AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
            conn, params=(symbol, start_date, end_date))
        conn.close()
        if df.empty:
            return None
        df.rename(columns={"trade_date": "date"}, inplace=True)
        df["date"] = pd.to_datetime(df["date"])
        df.set_index("date", inplace=True)
        return df
    except Exception:
        return None


# Monkey-patch for cache-based data
import engines.backtrader_engine as bt_engine
bt_engine._fetch_data = _fetch_from_cache

# Sub-backtest caching
_bt_cache: dict[str, dict] = {}
from engines import dynamic_composer as dc_mod
_orig_run_sub_backtest = dc_mod._run_sub_backtest


def _cached_run_sub_backtest(strategy_id, params, symbols, start_date,
                              end_date, initial_cash, commission=0.0003):
    cache_key = (f"{strategy_id}|{json.dumps(params, sort_keys=True)}"
                 f"|{','.join(symbols)}|{start_date}|{end_date}|{initial_cash}")
    if cache_key in _bt_cache:
        return _bt_cache[cache_key]
    result = _orig_run_sub_backtest(strategy_id, params, symbols,
                                    start_date, end_date, initial_cash, commission)
    _bt_cache[cache_key] = result
    return result


dc_mod._run_sub_backtest = _cached_run_sub_backtest
from aipha.engines.dynamic_composer import compose_backtest
from aipha.engines.stock_profiler import profile_stock, list_stocks_with_data

# ── Top combos from AI exploration to test ────────────────────────

TOP_COMBOS: list[dict] = [
    {
        "name": "MACD+KDJ+多因子 三投票",
        "method": "voting",
        "strategies": [
            {"strategyId": "macd", "weight": 1},
            {"strategyId": "kdj", "weight": 1},
            {"strategyId": "multi-factor", "weight": 1},
        ],
        "minVotes": 2,
    },
    {
        "name": "KDJ+竞价 ͶƱ",
        "method": "voting",
        "strategies": [
            {"strategyId": "kdj", "weight": 1},
            {"strategyId": "auction", "weight": 1},
        ],
        "minVotes": 1,
    },
    {
        "name": "KDJ→竞价 门控",
        "method": "gated",
        "strategies": [
            {"strategyId": "kdj", "weight": 1, "role": "primary"},
            {"strategyId": "auction", "weight": 1, "role": "filter"},
        ],
    },
    {
        "name": "海龟+RSI+多因子 三投票",
        "method": "voting",
        "strategies": [
            {"strategyId": "turtle", "weight": 1},
            {"strategyId": "rsi", "weight": 1},
            {"strategyId": "multi-factor", "weight": 1},
        ],
        "minVotes": 2,
    },
    {
        "name": "MACD+KDJ+网格 三投票",
        "method": "voting",
        "strategies": [
            {"strategyId": "macd", "weight": 1},
            {"strategyId": "kdj", "weight": 1},
            {"strategyId": "grid", "weight": 1},
        ],
        "minVotes": 2,
    },
    {
        "name": "MACD+KDJ+均值回归 三投票",
        "method": "voting",
        "strategies": [
            {"strategyId": "macd", "weight": 1},
            {"strategyId": "kdj", "weight": 1},
            {"strategyId": "mean-reversion-cn", "weight": 1},
        ],
        "minVotes": 2,
    },
    {
        "name": "MACD+KDJ ͶƱ",
        "method": "voting",
        "strategies": [
            {"strategyId": "macd", "weight": 1},
            {"strategyId": "kdj", "weight": 1},
        ],
        "minVotes": 2,
    },
    {
        "name": "MACD→KDJ 级联",
        "method": "cascade",
        "strategies": [
            {"strategyId": "macd", "weight": 1},
            {"strategyId": "kdj", "weight": 1},
        ],
    },
    {
        "name": "布林+均值回归 门控",
        "method": "gated",
        "strategies": [
            {"strategyId": "bollinger", "weight": 1, "role": "primary"},
            {"strategyId": "mean-reversion-cn", "weight": 1, "role": "filter"},
        ],
    },
    {
        "name": "海龟+MACD 级联",
        "method": "cascade",
        "strategies": [
            {"strategyId": "turtle", "weight": 1},
            {"strategyId": "macd", "weight": 1},
        ],
    },
    {
        "name": "网格 单策略",
        "method": "voting",
        "strategies": [{"strategyId": "grid", "weight": 1}],
        "minVotes": 1,
    },
    {
        "name": "四投票 MACD+KDJ+布林+均值回归",
        "method": "voting",
        "strategies": [
            {"strategyId": "macd", "weight": 1},
            {"strategyId": "kdj", "weight": 1},
            {"strategyId": "bollinger", "weight": 1},
            {"strategyId": "mean-reversion-cn", "weight": 1},
        ],
        "minVotes": 3,
    },
]


def _run_combo_on_stock(combo: dict, symbol: str,
                         start_date: str, end_date: str,
                         initial_cash: float) -> dict:
    """Run a single combo on a single stock, return combined metrics."""
    config = {
        "action": "compose_backtest",
        "method": combo["method"],
        "strategies": combo["strategies"],
        "symbols": [symbol],
        "startDate": start_date,
        "endDate": end_date,
        "initialCash": initial_cash,
    }
    if "minVotes" in combo:
        config["minVotes"] = combo["minVotes"]
    if "scoreThreshold" in combo:
        config["scoreThreshold"] = combo["scoreThreshold"]

    result = compose_backtest(config)
    cm = result.get("combinedMetrics", {})
    return {
        "totalReturn": cm.get("totalReturn", 0),
        "sharpeRatio": cm.get("sharpeRatio", 0),
        "maxDrawdown": cm.get("maxDrawdown", 0),
        "winRate": cm.get("winRate", 0),
        "totalTrades": cm.get("totalTrades", 0),
    }


def build_routing_table(
    end_date: str = "2026-03-20",
    start_date: str = "2024-10-08",
    min_bars: int = 200,
    initial_cash: float = 100000,
    max_stocks: int = 100,
) -> dict:
    """Build the full routing table."""
    t0 = time.time()

    # Get all stocks with sufficient data
    all_symbols = list_stocks_with_data(min_bars, start_date)
    if len(all_symbols) > max_stocks:
        # Sample diversely by prefix
        import random
        random.seed(42)
        groups: dict[str, list[str]] = {}
        for s in all_symbols:
            groups.setdefault(s[:2], []).append(s)
        selected: list[str] = []
        keys = sorted(groups.keys())
        idx = 0
        while len(selected) < max_stocks and idx < len(keys) * 20:
            k = keys[idx % len(keys)]
            if groups[k]:
                selected.append(groups[k].pop(0))
            idx += 1
        all_symbols = selected[:max_stocks]

    print(f"Building routing table: {len(all_symbols)} stocks × "
          f"{len(TOP_COMBOS)} combos")
    print(f"Period: {start_date} ~ {end_date}\n")

    # Profile all stocks + run all combos
    # Group: profileKey → [(combo_name, metrics)]
    profile_groups: dict[str, dict] = {}
    # profileKey → { combo_name → [metrics_per_stock] }

    for i, sym in enumerate(all_symbols, 1):
        profile = profile_stock(sym, end_date)
        if not profile.get("ok"):
            continue

        pkey = profile.get("profileKey", "unknown")
        if pkey not in profile_groups:
            profile_groups[pkey] = {
                "stocks": [],
                "combos": {c["name"]: [] for c in TOP_COMBOS},
            }
        profile_groups[pkey]["stocks"].append(sym)

        for combo in TOP_COMBOS:
            metrics = _run_combo_on_stock(combo, sym, start_date, end_date,
                                          initial_cash)
            profile_groups[pkey]["combos"][combo["name"]].append(metrics)

        if i % 10 == 0:
            elapsed = time.time() - t0
            print(f"  [{i}/{len(all_symbols)}] {sym} → {pkey}  ({elapsed:.0f}s)")

    # Aggregate: for each profile group, rank combos by composite score
    routes: dict[str, dict] = {}
    for pkey, group_data in profile_groups.items():
        combo_scores: list[dict] = []
        for combo in TOP_COMBOS:
            metrics_list = group_data["combos"][combo["name"]]
            if not metrics_list:
                continue
            n = len(metrics_list)
            avg_ret = sum(m["totalReturn"] for m in metrics_list) / n
            avg_sharpe = sum(m["sharpeRatio"] for m in metrics_list) / n
            avg_dd = sum(m["maxDrawdown"] for m in metrics_list) / n
            avg_wr = sum(m["winRate"] for m in metrics_list) / n
            positive = sum(1 for m in metrics_list if m["totalReturn"] > 0)

            # Composite score (same as exploration)
            ret_s = max(min(avg_ret / 30, 1), -1) * 0.35
            sharpe_s = max(min(avg_sharpe / 5, 1), -1) * 0.25
            dd_s = max(1 - avg_dd / 25, 0) * 0.2
            pos_s = (positive / n) * 0.1
            wr_s = min(avg_wr / 100, 1) * 0.1
            score = ret_s + sharpe_s + dd_s + pos_s + wr_s

            combo_scores.append({
                "combo": {
                    "name": combo["name"],
                    "method": combo["method"],
                    "strategies": combo["strategies"],
                    **({"minVotes": combo["minVotes"]}
                       if "minVotes" in combo else {}),
                },
                "score": round(score, 4),
                "performance": {
                    "avgReturn": round(avg_ret, 2),
                    "avgSharpe": round(avg_sharpe, 2),
                    "avgMaxDD": round(avg_dd, 2),
                    "avgWinRate": round(avg_wr, 1),
                    "positiveRate": f"{positive}/{n}",
                    "sampleSize": n,
                },
            })

        combo_scores.sort(key=lambda x: x["score"], reverse=True)
        if combo_scores:
            best = combo_scores[0]
            parts = pkey.split("|")
            vol_cls = parts[0] if len(parts) > 0 else "?"
            trend_cls = parts[1] if len(parts) > 1 else "?"
            mcap_cls = parts[2] if len(parts) > 2 else "?"

            routes[pkey] = {
                "profileKey": pkey,
                "volatility": vol_cls,
                "trend": trend_cls,
                "marketCap": mcap_cls,
                "stockCount": len(group_data["stocks"]),
                "combo": best["combo"],
                "score": best["score"],
                "performance": best["performance"],
                "reason": (f"数据驱动: {best['performance']['sampleSize']}ֻ"
                           f"{vol_cls}波动/{trend_cls}/{mcap_cls}盘股回测最优"),
                "alternatives": [
                    {"name": c["combo"]["name"], "score": c["score"],
                     "avgReturn": c["performance"]["avgReturn"]}
                    for c in combo_scores[1:4]
                ],
            }

    elapsed_total = time.time() - t0

    # Print summary
    print(f"\n{'='*70}")
    print(f"  Routing Table Summary ({len(routes)} profile groups)")
    print(f"{'='*70}")
    for pkey, route in sorted(routes.items()):
        perf = route["performance"]
        print(f"  {pkey:<30} → {route['combo']['name']:<30} "
              f"score={route['score']:.3f}  ret={perf['avgReturn']:>5.1f}%  "
              f"stocks={route['stockCount']}")

    # Save
    output = {
        "builtAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "period": {"start": start_date, "end": end_date},
        "totalStocks": len(all_symbols),
        "profileGroups": len(routes),
        "routes": routes,
    }

    os.makedirs(os.path.dirname(ROUTING_TABLE_PATH), exist_ok=True)
    with open(ROUTING_TABLE_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\nRouting table saved: {ROUTING_TABLE_PATH}")
    print(f"Total time: {elapsed_total:.0f}s ({elapsed_total/60:.1f}min)")
    return output


ROUTING_TABLE_PATH = os.path.expanduser(
    "~/.openclaw/quant-trading/strategy_routing_table.json"
)

if __name__ == "__main__":
    # Support JSON mode (python-bridge): reads QUANT_CONFIG env var
    if "--json" in sys.argv:
        config_str = os.environ.get("QUANT_CONFIG", "{}")
        config = json.loads(config_str)
        try:
            result = build_routing_table(
                end_date=config.get("endDate", "2026-03-20"),
                start_date=config.get("startDate", "2024-10-08"),
                min_bars=int(config.get("minBars", 200)),
                max_stocks=int(config.get("maxStocks", 80)),
            )
            output = {
                "ok": True,
                "totalStocks": result.get("totalStocks", 0),
                "profileGroups": result.get("profileGroups", 0),
                "routingEntries": result.get("profileGroups", 0),
                "elapsed": time.time(),
            }
            print(json.dumps(output, ensure_ascii=False))
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(0)

    # CLI mode with argparse
    import argparse
    parser = argparse.ArgumentParser(description="Build strategy routing table")
    parser.add_argument("--end-date", default="2026-03-20")
    parser.add_argument("--start-date", default="2024-10-08")
    parser.add_argument("--min-bars", type=int, default=200)
    parser.add_argument("--max-stocks", type=int, default=80)
    args = parser.parse_args()

    build_routing_table(
        end_date=args.end_date,
        start_date=args.start_date,
        min_bars=args.min_bars,
        max_stocks=args.max_stocks,
    )
