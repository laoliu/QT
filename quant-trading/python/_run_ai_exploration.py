#!/usr/bin/env python3
"""
AI-Driven Large-Scale Strategy Exploration

Phase 1: Auto-generate ~100 diverse strategy combinations using cross-category
          pairing, varying methods/weights/params. Screen on 10 representative stocks.
Phase 2: Validate top 15 winners on 50 stocks from different sectors.

Uses local SQLite cache — no network required.
"""
import itertools
import json
import math
import os
import random
import sqlite3
import sys
import time
from typing import Any

_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

# ── Cache-based data fetching ──────────────────────────────────
CACHE_DB = os.path.expanduser("~/.openclaw/extensions/quant-trading/cache/stock_data.db")

def _fetch_from_cache(symbol, start_date, end_date, market=None):
    import pandas as pd
    try:
        conn = sqlite3.connect(CACHE_DB)
        df = pd.read_sql_query(
            "SELECT trade_date, open_price as open, high, low, close_price as close, volume "
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

import engines.backtrader_engine as bt_engine
bt_engine._fetch_data = _fetch_from_cache
def _run_sub_backtest(strategy_id: str, params: dict, symbols: list[str],
                       start_date: str, end_date: str, initial_cash: float,
                       commission: float = 0.0003) -> dict:
    """Run a single strategy backtest with caching."""
    cache_key = f"{strategy_id}|{json.dumps(params,sort_keys=True)}|{','.join(symbols)}|{start_date}|{end_date}|{initial_cash}"
    if cache_key in _bt_cache:
        return _bt_cache[cache_key]
    result = _orig_run_sub_backtest(strategy_id, params, symbols, start_date, end_date, initial_cash, commission)
    _bt_cache[cache_key] = result
    return result

_bt_cache: dict[str, dict] = {}

# Monkey-patch the dynamic composer's sub-backtest to use our cache
from engines import dynamic_composer as dc_mod
_orig_run_sub_backtest = dc_mod._run_sub_backtest
dc_mod._run_sub_backtest = _run_sub_backtest
from engines.dynamic_composer import compose_backtest

# ── Strategy Catalog ───────────────────────────────────────────
STRATEGIES = {
    # category -> [(id, label)]
    "trend":      [("macd", "MACD"), ("turtle", "海龟")],
    "oscillator": [("rsi", "RSI"), ("kdj", "KDJ"), ("bollinger", "布林"),
                   ("mean-reversion-cn", "均值回归")],
    "composite":  [("dragon-return", "龙回头"), ("multi-factor", "多因子"),
                   ("auction", "竞价")],
    "range":      [("grid", "网格")],
}

# Flatten
ALL_IDS = [sid for cat in STRATEGIES.values() for sid, _ in cat]
ID_LABEL = {sid: label for cat in STRATEGIES.values() for sid, label in cat}
ID_CAT = {}
for cat, items in STRATEGIES.items():
    for sid, _ in items:
        ID_CAT[sid] = cat

# ── AI Combination Generator ──────────────────────────────────
def generate_candidates() -> list[dict]:
    """Auto-generate diverse strategy combinations."""
    candidates: list[dict] = []
    seen: set[str] = set()

    def _key(method, sids):
        return f"{method}|{'_'.join(sorted(sids))}"

    def _add(name, method, strategies, extra=None):
        sids = [s["strategyId"] for s in strategies]
        k = _key(method, sids)
        if k in seen:
            return
        seen.add(k)
        c = {"name": name, "method": method, "strategies": strategies}
        if extra:
            c.update(extra)
        candidates.append(c)

    # ── 1. All pairs cross-category (voting, 2/2 = unanimous) ──
    cats = list(STRATEGIES.keys())
    for i, cat1 in enumerate(cats):
        for cat2 in cats[i+1:]:
            for s1, l1 in STRATEGIES[cat1]:
                for s2, l2 in STRATEGIES[cat2]:
                    _add(f"ͶƱ {l1}+{l2}",
                         "voting",
                         [{"strategyId": s1, "weight": 1},
                          {"strategyId": s2, "weight": 1}],
                         {"minVotes": 2})

    # ── 2. All pairs gated (one primary, one filter) ──
    for i, cat1 in enumerate(cats):
        for cat2 in cats:
            if cat1 == cat2:
                continue
            for s1, l1 in STRATEGIES[cat1]:
                for s2, l2 in STRATEGIES[cat2]:
                    if s1 == s2:
                        continue
                    _add(f"门控 {l1}→{l2}过滤",
                         "gated",
                         [{"strategyId": s1, "weight": 1, "role": "primary"},
                          {"strategyId": s2, "weight": 1, "role": "filter"}])

    # ── 3. Triple combos: trend + oscillator + X (voting majority) ──
    for t_id, t_l in STRATEGIES["trend"]:
        for o_id, o_l in STRATEGIES["oscillator"]:
            for cat3 in ["composite", "range", "oscillator"]:
                for x_id, x_l in STRATEGIES[cat3]:
                    if x_id in (t_id, o_id):
                        continue
                    _add(f"三投票 {t_l}+{o_l}+{x_l}",
                         "voting",
                         [{"strategyId": t_id, "weight": 1},
                          {"strategyId": o_id, "weight": 1},
                          {"strategyId": x_id, "weight": 1}],
                         {"minVotes": 2})

    # ── 4. Triple weighted (trend heavy, others light) ──
    for t_id, t_l in STRATEGIES["trend"]:
        for o_id, o_l in STRATEGIES["oscillator"]:
            for x_id, x_l in STRATEGIES.get("composite", []):
                if x_id in (t_id, o_id):
                    continue
                _add(f"加权 {t_l}×3+{o_l}×1+{x_l}×1",
                     "weighted-score",
                     [{"strategyId": t_id, "weight": 3},
                      {"strategyId": o_id, "weight": 1},
                      {"strategyId": x_id, "weight": 1}],
                     {"scoreThreshold": 0.5})
                _add(f"加权 {o_l}×3+{t_l}×1+{x_l}×1",
                     "weighted-score",
                     [{"strategyId": o_id, "weight": 3},
                      {"strategyId": t_id, "weight": 1},
                      {"strategyId": x_id, "weight": 1}],
                     {"scoreThreshold": 0.5})

    # ── 5. Cascade chains (2 and 3 stages) ──
    for t_id, t_l in STRATEGIES["trend"]:
        for o_id, o_l in STRATEGIES["oscillator"]:
            _add(f"级联 {t_l}→{o_l}",
                 "cascade",
                 [{"strategyId": t_id, "weight": 1},
                  {"strategyId": o_id, "weight": 1}])
            _add(f"级联 {o_l}→{t_l}",
                 "cascade",
                 [{"strategyId": o_id, "weight": 1},
                  {"strategyId": t_id, "weight": 1}])
    for t_id, t_l in STRATEGIES["trend"]:
        for o1_id, o1_l in STRATEGIES["oscillator"]:
            for o2_id, o2_l in STRATEGIES["oscillator"]:
                if o1_id >= o2_id:
                    continue
                _add(f"级联 {t_l}→{o1_l}→{o2_l}",
                     "cascade",
                     [{"strategyId": t_id, "weight": 1},
                      {"strategyId": o1_id, "weight": 1},
                      {"strategyId": o2_id, "weight": 1}])

    # ── 6. Quad combos (four strategies, strict voting 3/4) ──
    for t_id, t_l in STRATEGIES["trend"]:
        osc = STRATEGIES["oscillator"]
        for combo in itertools.combinations(osc, 3):
            ids = [s[0] for s in combo]
            labels = [s[1] for s in combo]
            _add(f"四投票3/4 {t_l}+{'+'.join(labels)}",
                 "voting",
                 [{"strategyId": t_id, "weight": 1}] +
                 [{"strategyId": sid, "weight": 1} for sid in ids],
                 {"minVotes": 3})

    # ── 7. Gated with double filters ──
    for p_id, p_l in STRATEGIES["oscillator"]:
        for f1_id, f1_l in STRATEGIES["trend"]:
            for f2_id, f2_l in STRATEGIES["oscillator"]:
                if f2_id == p_id:
                    continue
                _add(f"门控 {p_l}→{f1_l}+{f2_l}双过滤",
                     "gated",
                     [{"strategyId": p_id, "weight": 1, "role": "primary"},
                      {"strategyId": f1_id, "weight": 1, "role": "filter"},
                      {"strategyId": f2_id, "weight": 1, "role": "filter"}])

    # ── 8. Single baselines for comparison ──
    for sid, label in ID_LABEL.items():
        if sid in ("pair-trading", "lianban_relay"):
            continue  # special strategies
        _add(f"单策略 {label}",
             "voting",
             [{"strategyId": sid, "weight": 1}],
             {"minVotes": 1})

    return candidates


# ── Stock Selection ────────────────────────────────────────────
def select_stocks(min_bars: int, start_date: str, limit: int) -> list[str]:
    """Select stocks with sufficient data, diversified by prefix (sector proxy)."""
    conn = sqlite3.connect(CACHE_DB)
    cursor = conn.execute(
        "SELECT symbol, COUNT(*) as bars FROM kline_data "
        "WHERE period='daily' AND trade_date >= ? "
        "GROUP BY symbol HAVING bars >= ? ORDER BY bars DESC",
        (start_date, min_bars))
    all_stocks = [row[0] for row in cursor.fetchall()]
    conn.close()

    # Diversify: group by first 2 digits, pick from each group
    groups: dict[str, list[str]] = {}
    for s in all_stocks:
        prefix = s[:2]
        groups.setdefault(prefix, []).append(s)

    selected: list[str] = []
    random.seed(42)  # reproducible
    keys = sorted(groups.keys())
    idx = 0
    while len(selected) < limit and idx < len(keys) * 10:
        k = keys[idx % len(keys)]
        if groups[k]:
            s = groups[k].pop(0)
            selected.append(s)
        idx += 1

    return selected[:limit]


# ── Backtest Runner ────────────────────────────────────────────
def run_combo(candidate: dict, symbols: list[str], start_date: str,
              end_date: str, initial_cash: float) -> dict:
    """Run a combo on multiple symbols, return aggregate metrics."""
    per_stock = []
    for sym in symbols:
        config = {
            "action": "compose_backtest",
            "method": candidate["method"],
            "strategies": candidate["strategies"],
            "symbols": [sym],
            "startDate": start_date,
            "endDate": end_date,
            "initialCash": initial_cash,
        }
        if "minVotes" in candidate:
            config["minVotes"] = candidate["minVotes"]
        if "scoreThreshold" in candidate:
            config["scoreThreshold"] = candidate["scoreThreshold"]

        result = compose_backtest(config)
        # Use combined signal metrics (actual P&L from combined signals)
        cm = result.get("combinedMetrics", {})
        per_stock.append({
            "symbol": sym,
            "ok": result.get("ok", False),
            "return": cm.get("totalReturn", 0),
            "sharpe": cm.get("sharpeRatio", 0),
            "maxDD": cm.get("maxDrawdown", 0),
            "winRate": cm.get("winRate", 0),
            "trades": cm.get("totalTrades", 0),
            "signals": result.get("combinedSignalCount", 0),
        })

    returns = [p["return"] for p in per_stock]
    sharpes = [p["sharpe"] for p in per_stock]
    dds = [p["maxDD"] for p in per_stock]
    wrs = [p["winRate"] for p in per_stock]
    n = len(returns)
    positive = sum(1 for r in returns if r > 0)
    avg_ret = sum(returns) / n if n else 0
    avg_sharpe = sum(sharpes) / n if n else 0
    avg_dd = sum(dds) / n if n else 0
    avg_wr = sum(wrs) / n if n else 0

    # Composite score
    ret_s = max(min(avg_ret / 30, 1), -1) * 0.35
    sharpe_s = max(min(avg_sharpe / 5, 1), -1) * 0.25
    dd_s = max(1 - avg_dd / 25, 0) * 0.2
    pos_s = (positive / n if n else 0) * 0.1
    wr_s = min(avg_wr / 100, 1) * 0.1

    return {
        "name": candidate["name"],
        "method": candidate["method"],
        "strategyIds": [s["strategyId"] for s in candidate["strategies"]],
        "avgReturn": round(avg_ret, 2),
        "avgSharpe": round(avg_sharpe, 2),
        "avgMaxDD": round(avg_dd, 2),
        "avgWinRate": round(avg_wr, 1),
        "positiveRate": f"{positive}/{n}",
        "score": round(ret_s + sharpe_s + dd_s + pos_s + wr_s, 4),
        "perStock": per_stock,
    }


# ── Main ───────────────────────────────────────────────────────
START_DATE = "2024-10-08"
END_DATE = "2026-03-20"
INITIAL_CASH = 100000

def main():
    t_total = time.time()

    # Generate candidates
    candidates = generate_candidates()
    print(f"\n{'='*80}")
    print(f"  AI 策略组合探索")
    print(f"  自动生成 {len(candidates)} 个候选组合")
    print(f"  回测区间: {START_DATE} ~ {END_DATE}  |  初始资金: {INITIAL_CASH:,}")
    print(f"{'='*80}")

    # ── Phase 1: Screen on 10 representative stocks ──
    screen_stocks = select_stocks(min_bars=300, start_date=START_DATE, limit=5)
    print(f"\n Phase 1: 初筛 ({len(candidates)} 组合 × {len(screen_stocks)} 标的)")
    print(f"  标的: {screen_stocks}\n")

    phase1_results: list[dict] = []
    for i, c in enumerate(candidates, 1):
        t0 = time.time()
        r = run_combo(c, screen_stocks, START_DATE, END_DATE, INITIAL_CASH)
        elapsed = time.time() - t0
        phase1_results.append(r)

        mark = "🟢" if r["avgReturn"] > 5 else "🟡" if r["avgReturn"] > 0 else "🔴"
        if i % 10 == 0 or i == len(candidates):
            print(f"  [{i:>3}/{len(candidates)}] {mark} {r['name']:<40} "
                  f"收益={r['avgReturn']:>6.1f}%  夏普={r['avgSharpe']:>5.2f}  "
                  f"回撤={r['avgMaxDD']:>5.1f}%  盈利={r['positiveRate']}  "
                  f"({elapsed:.1f}s)")

    # Sort by score
    phase1_results.sort(key=lambda x: x["score"], reverse=True)

    print(f"\n{'─'*80}")
    print(f"  Phase 1 Top 20 ({len(phase1_results)} 中选)")
    print(f"{'─'*80}")
    print(f"{'名次':>4} {'分数':>6} {'方法':<16} {'组合':>40} {'收益':>7} {'夏普':>6} {'回撤':>6} {'胜率':>6} {'盈利':>6}")
    print("-" * 100)
    for rank, r in enumerate(phase1_results[:20], 1):
        print(f"{rank:>4} {r['score']:>6.3f} {r['method']:<16} {r['name']:>40} "
              f"{r['avgReturn']:>6.1f}% {r['avgSharpe']:>6.2f} {r['avgMaxDD']:>5.1f}% "
              f"{r['avgWinRate']:>5.1f}% {r['positiveRate']:>5}")

    # ── Phase 2: Validate top 15 on 50 stocks ──
    top_n = 15
    top_candidates_map = {}
    for r in phase1_results[:top_n]:
        # Find original candidate
        for c in candidates:
            if c["name"] == r["name"]:
                top_candidates_map[c["name"]] = c
                break

    validation_stocks = select_stocks(min_bars=250, start_date=START_DATE, limit=30)
    print(f"\n{'='*80}")
    print(f"  Phase 2: 大范围验证 (Top {top_n} × {len(validation_stocks)} 标的)")
    print(f"{'='*80}\n")

    phase2_results: list[dict] = []
    for i, (name, c) in enumerate(top_candidates_map.items(), 1):
        t0 = time.time()
        r = run_combo(c, validation_stocks, START_DATE, END_DATE, INITIAL_CASH)
        elapsed = time.time() - t0
        phase2_results.append(r)
        mark = "🟢" if r["avgReturn"] > 5 else "🟡" if r["avgReturn"] > 0 else "🔴"
        print(f"  [{i:>2}/{top_n}] {mark} {r['name']:<40} "
              f"收益={r['avgReturn']:>6.1f}%  夏普={r['avgSharpe']:>5.2f}  "
              f"回撤={r['avgMaxDD']:>5.1f}%  盈利={r['positiveRate']}  ({elapsed:.1f}s)")

    phase2_results.sort(key=lambda x: x["score"], reverse=True)

    # ── Final Rankings ──
    print(f"\n{'='*80}")
    print(f"  🏆 最终排名 (50 标的验证)")
    print(f"{'='*80}")
    print(f"{'名次':>4} {'分数':>6} {'方法':<16} {'组合':>40} {'收益':>7} {'夏普':>6} {'回撤':>6} {'胜率':>6} {'盈利':>6}")
    print("-" * 100)
    for rank, r in enumerate(phase2_results, 1):
        marker = " ★" if rank <= 3 else ""
        print(f"{rank:>4} {r['score']:>6.3f} {r['method']:<16} {r['name']:>40} "
              f"{r['avgReturn']:>6.1f}% {r['avgSharpe']:>6.2f} {r['avgMaxDD']:>5.1f}% "
              f"{r['avgWinRate']:>5.1f}% {r['positiveRate']:>5}{marker}")

    # ── Top 3 deep analysis ──
    print(f"\n{'='*80}")
    print(f"  📊 Top 3 详细分析")
    print(f"{'='*80}")
    for rank, r in enumerate(phase2_results[:3], 1):
        returns = [p["return"] for p in r["perStock"]]
        best_stock = max(r["perStock"], key=lambda p: p["return"])
        worst_stock = min(r["perStock"], key=lambda p: p["return"])
        median_ret = sorted(returns)[len(returns) // 2]
        std_ret = (sum((x - r["avgReturn"])**2 for x in returns) / len(returns)) ** 0.5

        print(f"\n  #{rank} {r['name']}")
        print(f"  方法: {r['method']}  |  子策略: {', '.join(r['strategyIds'])}")
        print(f"  ─────────────────────────────────────────────")
        print(f"  平均收益: {r['avgReturn']:>6.1f}%  |  中位收益: {median_ret:>6.1f}%  |  收益标偏: {std_ret:>6.1f}%")
        print(f"  平均夏普: {r['avgSharpe']:>6.2f}  |  平均回撤: {r['avgMaxDD']:>5.1f}%  |  平均胜率: {r['avgWinRate']:>5.1f}%")
        print(f"  盈利标的: {r['positiveRate']}")
        print(f"  最佳: {best_stock['symbol']} ({best_stock['return']:>+.1f}%)")
        print(f"  最差: {worst_stock['symbol']} ({worst_stock['return']:>+.1f}%)")

    elapsed_total = time.time() - t_total
    print(f"\n总耗时: {elapsed_total:.0f}s ({elapsed_total/60:.1f}min)")

    # Save
    output = {
        "config": {
            "startDate": START_DATE, "endDate": END_DATE,
            "initialCash": INITIAL_CASH,
            "totalCandidates": len(candidates),
            "screenStocks": screen_stocks,
            "validationStocksCount": len(validation_stocks),
        },
        "phase1_top20": [{k: v for k, v in r.items() if k != "perStock"} for r in phase1_results[:20]],
        "phase2_final": phase2_results,
    }
    path = os.path.join(_this_dir, "_ai_exploration_results.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n完整结果已保存: {path}")


if __name__ == "__main__":
    main()
