#!/usr/bin/env python3
"""
Large-Scale Adaptive Strategy Routing Backtest

Runs 12 strategy combos across 200 stocks (sampled diversely from 5192),
groups results by stock profile (volatility|trend|marketCap), ranks combos
per profile, and produces a comprehensive analysis report.

Output: /tmp/large_scale_routing_results.json + console summary
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from collections import defaultdict
from typing import Any

_this_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_this_dir)
if _python_root not in sys.path:
    sys.path.insert(0, _python_root)

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
from engines.dynamic_composer import compose_backtest
from engines.stock_profiler import profile_stock, list_stocks_with_data

# ── Strategy Combos ──────────────────────────────────────────────

COMBOS: list[dict] = [
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
        "name": "KDJ+竞价 投票",
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
        "name": "MACD+KDJ 投票",
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


def _run_combo(combo: dict, symbol: str,
               start_date: str, end_date: str,
               initial_cash: float) -> dict:
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
    result = compose_backtest(config)
    cm = result.get("combinedMetrics", {})
    return {
        "totalReturn": cm.get("totalReturn", 0),
        "sharpeRatio": cm.get("sharpeRatio", 0),
        "maxDrawdown": cm.get("maxDrawdown", 0),
        "winRate": cm.get("winRate", 0),
        "totalTrades": cm.get("totalTrades", 0),
    }


def _composite_score(avg_ret, avg_sharpe, avg_dd, avg_wr, pos_rate):
    ret_s = max(min(avg_ret / 30, 1), -1) * 0.35
    sharpe_s = max(min(avg_sharpe / 5, 1), -1) * 0.25
    dd_s = max(1 - avg_dd / 25, 0) * 0.2
    pos_s = pos_rate * 0.1
    wr_s = min(avg_wr / 100, 1) * 0.1
    return ret_s + sharpe_s + dd_s + pos_s + wr_s


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-stocks", type=int, default=200)
    parser.add_argument("--min-bars", type=int, default=120)
    parser.add_argument("--start-date", default="2024-10-08")
    parser.add_argument("--end-date", default="2026-03-20")
    parser.add_argument("--initial-cash", type=float, default=100000)
    args = parser.parse_args()

    t0 = time.time()

    # ── 1. Sample stocks ──────────────────────────────────────────
    all_syms = list_stocks_with_data(args.min_bars, args.start_date)
    print(f"Total eligible stocks: {len(all_syms)}")

    import random
    random.seed(42)
    if len(all_syms) > args.max_stocks:
        # Stratified sampling by prefix (exchange bucket)
        buckets: dict[str, list[str]] = {}
        for s in all_syms:
            buckets.setdefault(s[:2], []).append(s)
        for v in buckets.values():
            random.shuffle(v)
        selected: list[str] = []
        keys = sorted(buckets.keys())
        idx = 0
        while len(selected) < args.max_stocks:
            k = keys[idx % len(keys)]
            if buckets[k]:
                selected.append(buckets[k].pop(0))
            else:
                keys.remove(k)
                if not keys:
                    break
            idx += 1
        stocks = selected
    else:
        stocks = all_syms

    print(f"Sampled {len(stocks)} stocks for backtest")
    print(f"Combos: {len(COMBOS)}")
    print(f"Total backtests: ~{len(stocks) * len(COMBOS)}")
    print(f"Period: {args.start_date} ~ {args.end_date}\n")

    # ── 2. Profile + Backtest ─────────────────────────────────────
    # per-stock results: { symbol: { profile, combo_results: {name: metrics} } }
    stock_results: dict[str, dict] = {}
    # profile groups: { profileKey: { stocks: [], combos: {name: [metrics]} } }
    profile_groups: dict[str, dict] = {}
    errors = 0

    for i, sym in enumerate(stocks, 1):
        try:
            profile = profile_stock(sym, args.end_date)
            if not profile.get("ok"):
                errors += 1
                continue

            pkey = profile.get("profileKey", "unknown")
            if pkey not in profile_groups:
                profile_groups[pkey] = {
                    "stocks": [],
                    "combos": {c["name"]: [] for c in COMBOS},
                }
            profile_groups[pkey]["stocks"].append(sym)

            combo_results = {}
            for combo in COMBOS:
                try:
                    metrics = _run_combo(combo, sym, args.start_date,
                                         args.end_date, args.initial_cash)
                    combo_results[combo["name"]] = metrics
                    profile_groups[pkey]["combos"][combo["name"]].append(metrics)
                except Exception:
                    combo_results[combo["name"]] = {"totalReturn": 0, "sharpeRatio": 0,
                                                    "maxDrawdown": 0, "winRate": 0, "totalTrades": 0}

            stock_results[sym] = {
                "profile": {
                    "profileKey": pkey,
                    "volatility": profile.get("volatility", {}).get("class", "?"),
                    "trend": profile.get("trend", {}).get("class", "?"),
                    "marketCap": profile.get("marketCap", {}).get("class", "?"),
                    "name": profile.get("name", sym),
                },
                "combos": combo_results,
            }
        except Exception as e:
            errors += 1
            continue

        if i % 20 == 0:
            elapsed = time.time() - t0
            cache_hits = len(_bt_cache)
            print(f"  [{i:>3}/{len(stocks)}]  {elapsed:>5.0f}s  "
                  f"cache={cache_hits}  groups={len(profile_groups)}  "
                  f"errors={errors}  last={sym}")

    elapsed_total = time.time() - t0
    print(f"\n{'='*80}")
    print(f"  BACKTEST COMPLETE: {len(stock_results)} stocks, "
          f"{len(stock_results) * len(COMBOS)} backtests, "
          f"{elapsed_total:.0f}s ({elapsed_total/60:.1f}min)")
    print(f"  Cache entries: {len(_bt_cache)}, Errors: {errors}")
    print(f"{'='*80}\n")

    # ── 3. Aggregate per profile group ────────────────────────────
    print("=" * 80)
    print("  ROUTING TABLE: Best combo per stock profile")
    print("=" * 80)
    print(f"{'Profile':<35} {'Best Combo':<30} {'Score':>6} "
          f"{'Ret%':>7} {'Sharpe':>7} {'MaxDD%':>7} {'Win%':>6} "
          f"{'Pos':>6} {'N':>4}")
    print("-" * 80)

    routes = {}
    for pkey in sorted(profile_groups.keys()):
        gd = profile_groups[pkey]
        combo_scores = []
        for combo in COMBOS:
            mlist = gd["combos"][combo["name"]]
            if not mlist:
                continue
            n = len(mlist)
            avg_ret = sum(m["totalReturn"] for m in mlist) / n
            avg_sharpe = sum(m["sharpeRatio"] for m in mlist) / n
            avg_dd = sum(m["maxDrawdown"] for m in mlist) / n
            avg_wr = sum(m["winRate"] for m in mlist) / n
            pos = sum(1 for m in mlist if m["totalReturn"] > 0)
            score = _composite_score(avg_ret, avg_sharpe, avg_dd, avg_wr, pos / n)
            combo_scores.append({
                "name": combo["name"],
                "method": combo["method"],
                "strategies": combo["strategies"],
                **({"minVotes": combo["minVotes"]} if "minVotes" in combo else {}),
                "score": round(score, 4),
                "avgReturn": round(avg_ret, 2),
                "avgSharpe": round(avg_sharpe, 2),
                "avgMaxDD": round(avg_dd, 2),
                "avgWinRate": round(avg_wr, 1),
                "positiveRate": f"{pos}/{n}",
                "sampleSize": n,
            })

        combo_scores.sort(key=lambda x: x["score"], reverse=True)
        if combo_scores:
            b = combo_scores[0]
            print(f"  {pkey:<33} {b['name']:<30} {b['score']:>5.3f} "
                  f"{b['avgReturn']:>6.1f}% {b['avgSharpe']:>6.2f} "
                  f"{b['avgMaxDD']:>6.1f}% {b['avgWinRate']:>5.1f} "
                  f"{b['positiveRate']:>5} {b['sampleSize']:>4}")
            routes[pkey] = {
                "best": b,
                "all": combo_scores,
                "stockCount": len(gd["stocks"]),
            }

    # ── 4. Cross-profile combo ranking ────────────────────────────
    print(f"\n{'='*80}")
    print("  GLOBAL COMBO RANKING (across all stocks)")
    print("=" * 80)

    global_combo: dict[str, list[dict]] = defaultdict(list)
    for sym, sd in stock_results.items():
        for cname, metrics in sd["combos"].items():
            global_combo[cname].append(metrics)

    global_ranking = []
    for cname, mlist in global_combo.items():
        n = len(mlist)
        avg_ret = sum(m["totalReturn"] for m in mlist) / n
        avg_sharpe = sum(m["sharpeRatio"] for m in mlist) / n
        avg_dd = sum(m["maxDrawdown"] for m in mlist) / n
        avg_wr = sum(m["winRate"] for m in mlist) / n
        pos = sum(1 for m in mlist if m["totalReturn"] > 0)
        median_ret = sorted(m["totalReturn"] for m in mlist)[n // 2]
        score = _composite_score(avg_ret, avg_sharpe, avg_dd, avg_wr, pos / n)
        global_ranking.append({
            "name": cname,
            "score": round(score, 4),
            "avgReturn": round(avg_ret, 2),
            "medianReturn": round(median_ret, 2),
            "avgSharpe": round(avg_sharpe, 2),
            "avgMaxDD": round(avg_dd, 2),
            "avgWinRate": round(avg_wr, 1),
            "positiveRate": f"{pos}/{n} ({100*pos/n:.0f}%)",
            "sampleSize": n,
        })

    global_ranking.sort(key=lambda x: x["score"], reverse=True)
    print(f"{'Rank':>4} {'Combo':<35} {'Score':>6} {'AvgRet%':>8} "
          f"{'MedRet%':>8} {'Sharpe':>7} {'MaxDD%':>7} {'Win%':>6} {'Pos':>10}")
    print("-" * 100)
    for i, g in enumerate(global_ranking, 1):
        print(f"  {i:>2}. {g['name']:<35} {g['score']:>5.3f} "
              f"{g['avgReturn']:>7.1f}% {g['medianReturn']:>7.1f}% "
              f"{g['avgSharpe']:>6.2f} {g['avgMaxDD']:>6.1f}% "
              f"{g['avgWinRate']:>5.1f} {g['positiveRate']:>10}")

    # ── 5. Volatility dimension analysis ──────────────────────────
    print(f"\n{'='*80}")
    print("  DIMENSION ANALYSIS: Best combo by single feature")
    print("=" * 80)

    for dim_name, dim_idx in [("Volatility", 0), ("Trend", 1), ("MarketCap", 2)]:
        # Group profiles by this dimension
        dim_groups: dict[str, list[dict]] = defaultdict(list)
        for sym, sd in stock_results.items():
            parts = sd["profile"]["profileKey"].split("|")
            if len(parts) > dim_idx:
                dim_val = parts[dim_idx]
            else:
                dim_val = "?"
            for cname, metrics in sd["combos"].items():
                dim_groups[(dim_val, cname)].append(metrics)

        # For each dim value, rank combos
        dim_vals = sorted(set(k[0] for k in dim_groups.keys()))
        print(f"\n  ── {dim_name} ──")
        for dv in dim_vals:
            best_score = -1
            best_combo = ""
            best_ret = 0
            best_sharpe = 0
            for cname in set(c["name"] for c in COMBOS):
                mlist = dim_groups.get((dv, cname), [])
                if not mlist:
                    continue
                n = len(mlist)
                ar = sum(m["totalReturn"] for m in mlist) / n
                ash = sum(m["sharpeRatio"] for m in mlist) / n
                add = sum(m["maxDrawdown"] for m in mlist) / n
                awr = sum(m["winRate"] for m in mlist) / n
                pos = sum(1 for m in mlist if m["totalReturn"] > 0)
                sc = _composite_score(ar, ash, add, awr, pos / n)
                if sc > best_score:
                    best_score = sc
                    best_combo = cname
                    best_ret = ar
                    best_sharpe = ash
            n_stocks = len(set(sym for sym in stock_results
                              if stock_results[sym]["profile"]["profileKey"]
                              .split("|")[dim_idx] == dv)) if dim_idx < 3 else 0
            print(f"    {dv:<15} → {best_combo:<35} "
                  f"score={best_score:.3f}  ret={best_ret:>5.1f}%  "
                  f"sharpe={best_sharpe:.2f}  stocks={n_stocks}")

    # ── 6. Top/bottom individual stocks ───────────────────────────
    print(f"\n{'='*80}")
    print("  TOP 10 STOCKS (best avg return across all combos)")
    print("=" * 80)

    stock_avg = []
    for sym, sd in stock_results.items():
        rets = [m["totalReturn"] for m in sd["combos"].values()]
        if rets:
            stock_avg.append({
                "symbol": sym,
                "name": sd["profile"]["name"],
                "profileKey": sd["profile"]["profileKey"],
                "avgReturn": sum(rets) / len(rets),
                "bestCombo": max(sd["combos"].items(), key=lambda x: x[1]["totalReturn"]),
            })

    stock_avg.sort(key=lambda x: x["avgReturn"], reverse=True)
    for i, s in enumerate(stock_avg[:10], 1):
        bc_name, bc_metrics = s["bestCombo"]
        print(f"  {i:>2}. {s['symbol']} {s['name']:<12} "
              f"[{s['profileKey']}]  avg={s['avgReturn']:>6.1f}%  "
              f"best={bc_name} ({bc_metrics['totalReturn']:.1f}%)")

    print(f"\n  BOTTOM 10 STOCKS")
    print("-" * 80)
    for i, s in enumerate(stock_avg[-10:], 1):
        bc_name, bc_metrics = s["bestCombo"]
        print(f"  {i:>2}. {s['symbol']} {s['name']:<12} "
              f"[{s['profileKey']}]  avg={s['avgReturn']:>6.1f}%  "
              f"best={bc_name} ({bc_metrics['totalReturn']:.1f}%)")

    # ── 7. Adaptive vs uniform comparison ─────────────────────────
    print(f"\n{'='*80}")
    print("  ADAPTIVE vs UNIFORM: Does routing help?")
    print("=" * 80)

    # Uniform: use the global #1 combo for all stocks
    best_global = global_ranking[0]["name"]
    uniform_rets = [sd["combos"][best_global]["totalReturn"]
                    for sd in stock_results.values()
                    if best_global in sd["combos"]]

    # Adaptive: use the best per-profile combo for each stock
    adaptive_rets = []
    for sym, sd in stock_results.items():
        pkey = sd["profile"]["profileKey"]
        if pkey in routes:
            best_name = routes[pkey]["best"]["name"]
            if best_name in sd["combos"]:
                adaptive_rets.append(sd["combos"][best_name]["totalReturn"])
                continue
        # fallback to global best
        if best_global in sd["combos"]:
            adaptive_rets.append(sd["combos"][best_global]["totalReturn"])

    def _stats(rets):
        if not rets:
            return {}
        n = len(rets)
        avg = sum(rets) / n
        pos = sum(1 for r in rets if r > 0)
        med = sorted(rets)[n // 2]
        return {
            "avg": round(avg, 2),
            "median": round(med, 2),
            "positive": f"{pos}/{n} ({100*pos/n:.0f}%)",
            "min": round(min(rets), 2),
            "max": round(max(rets), 2),
        }

    u_stats = _stats(uniform_rets)
    a_stats = _stats(adaptive_rets)

    print(f"  {'':20} {'Uniform':>12} {'Adaptive':>12} {'Delta':>10}")
    print(f"  {'Avg Return':20} {u_stats['avg']:>11.1f}% {a_stats['avg']:>11.1f}% "
          f"{a_stats['avg'] - u_stats['avg']:>+9.1f}%")
    print(f"  {'Median Return':20} {u_stats['median']:>11.1f}% {a_stats['median']:>11.1f}% "
          f"{a_stats['median'] - u_stats['median']:>+9.1f}%")
    print(f"  {'Positive Rate':20} {u_stats['positive']:>12} {a_stats['positive']:>12}")
    print(f"  {'Best Single':20} {u_stats['max']:>11.1f}% {a_stats['max']:>11.1f}%")
    print(f"  {'Worst Single':20} {u_stats['min']:>11.1f}% {a_stats['min']:>11.1f}%")
    print(f"\n  Global best combo: {best_global}")
    print(f"  Adaptive routing: per-profile best from {len(routes)} groups")

    # ── 8. Save full results ──────────────────────────────────────
    output = {
        "config": {
            "stocks": len(stock_results),
            "combos": len(COMBOS),
            "period": f"{args.start_date} ~ {args.end_date}",
            "elapsed": round(elapsed_total, 1),
        },
        "globalRanking": global_ranking,
        "routingTable": {k: {"best": v["best"], "stockCount": v["stockCount"],
                             "top3": [c["name"] for c in v["all"][:3]]}
                         for k, v in routes.items()},
        "comparison": {
            "uniform": u_stats,
            "adaptive": a_stats,
            "delta_avg": round(a_stats["avg"] - u_stats["avg"], 2),
            "delta_median": round(a_stats["median"] - u_stats["median"], 2),
        },
        "profileDistribution": {k: len(v["stocks"])
                                for k, v in sorted(profile_groups.items())},
        "top10stocks": [{"symbol": s["symbol"], "name": s["name"],
                         "profileKey": s["profileKey"],
                         "avgReturn": round(s["avgReturn"], 2)}
                        for s in stock_avg[:10]],
        "bottom10stocks": [{"symbol": s["symbol"], "name": s["name"],
                            "profileKey": s["profileKey"],
                            "avgReturn": round(s["avgReturn"], 2)}
                           for s in stock_avg[-10:]],
    }

    out_path = "/tmp/large_scale_routing_results.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nFull results saved: {out_path}")

    # Also save the routing table
    rt_path = os.path.expanduser(
        "~/.openclaw/quant-trading/strategy_routing_table.json"
    )
    rt_output = {
        "builtAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "period": {"start": args.start_date, "end": args.end_date},
        "totalStocks": len(stock_results),
        "profileGroups": len(routes),
        "routes": {},
    }
    for pkey, rd in routes.items():
        b = rd["best"]
        parts = pkey.split("|")
        rt_output["routes"][pkey] = {
            "profileKey": pkey,
            "volatility": parts[0] if len(parts) > 0 else "?",
            "trend": parts[1] if len(parts) > 1 else "?",
            "marketCap": parts[2] if len(parts) > 2 else "?",
            "stockCount": rd["stockCount"],
            "combo": {
                "name": b["name"],
                "method": b["method"],
                "strategies": b["strategies"],
                **({"minVotes": b["minVotes"]} if "minVotes" in b else {}),
            },
            "score": b["score"],
            "performance": {
                "avgReturn": b["avgReturn"],
                "avgSharpe": b["avgSharpe"],
                "avgMaxDD": b["avgMaxDD"],
                "avgWinRate": b["avgWinRate"],
                "positiveRate": b["positiveRate"],
                "sampleSize": b["sampleSize"],
            },
            "alternatives": [
                {"name": c["name"], "score": c["score"], "avgReturn": c["avgReturn"]}
                for c in rd["all"][1:4]
            ],
        }
    os.makedirs(os.path.dirname(rt_path), exist_ok=True)
    with open(rt_path, "w", encoding="utf-8") as f:
        json.dump(rt_output, f, ensure_ascii=False, indent=2)
    print(f"Routing table updated: {rt_path}")
    print(f"\nTotal time: {elapsed_total:.0f}s ({elapsed_total/60:.1f}min)")


if __name__ == "__main__":
    main()
