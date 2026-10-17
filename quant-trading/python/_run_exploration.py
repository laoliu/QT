#!/usr/bin/env python3
"""
AI Strategy Exploration — batch-test multiple strategy combinations.
Reads from local SQLite cache to avoid network issues.
Run from: extensions/quant-trading/python/
"""
import json
import os
import sys
import time
import sqlite3

_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

# Patch data fetching to use SQLite cache first
_CACHE_DB = os.path.expanduser("~/data/cache/stock_data.db")

def _fetch_from_cache(symbol, start_date, end_date, market=None):
    """Read kline data from local SQLite cache."""
    import pandas as pd
    try:
        conn = sqlite3.connect(_CACHE_DB)
        query = """
            SELECT trade_date, open_price, high, low, close_price, volume
            FROM kline_data
            WHERE symbol = ? AND period = 'daily'
              AND trade_date >= ? AND trade_date <= ?
            ORDER BY trade_date
        """
        df = pd.read_sql_query(query, conn, params=(symbol, start_date, end_date))
        conn.close()
        if df.empty:
            return None
        df = df.rename(columns={
            "trade_date": "date",
            "open_price": "open",
            "close_price": "close",
        })
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date")
        df = df[["open", "high", "low", "close", "volume"]]
        return df
    except Exception as e:
        print(f"[cache] Error reading {symbol}: {e}", file=sys.stderr)
        return None

# Monkey-patch the backtrader engine's data fetching to use cache
import engines.backtrader_engine as bt_engine
bt_engine._fetch_data = _fetch_from_cache

from engines.dynamic_composer import compose_backtest

# ── Configuration ──────────────────────────────────────────────
# Use symbols with the most cached data
SYMBOLS = ["600519"]  # Moutai — 534 bars cached
START_DATE = "2024-06-01"
END_DATE = "2025-12-31"
INITIAL_CASH = 100000

# ── Candidate Combinations ─────────────────────────────────────
# Cross-category pairing: trend × oscillator × mean-reversion × volume
CANDIDATES = [
    # ── Voting combos (majority agreement) ──
    {
        "name": "趋势+振荡投票 (MACD+RSI+KDJ)",
        "method": "voting",
        "minVotes": 2,
        "strategies": [
            {"strategyId": "macd", "weight": 1},
            {"strategyId": "rsi", "weight": 1},
            {"strategyId": "kdj", "weight": 1},
        ],
    },
    {
        "name": "趋势+均值回归投票 (MACD+Bollinger+均值回归)",
        "method": "voting",
        "minVotes": 2,
        "strategies": [
            {"strategyId": "macd", "weight": 1},
            {"strategyId": "bollinger", "weight": 1},
            {"strategyId": "mean-reversion-cn", "weight": 1},
        ],
    },
    {
        "name": "全指标投票 (MACD+RSI+Bollinger+KDJ)",
        "method": "voting",
        "minVotes": 3,
        "strategies": [
            {"strategyId": "macd", "weight": 1},
            {"strategyId": "rsi", "weight": 1},
            {"strategyId": "bollinger", "weight": 1},
            {"strategyId": "kdj", "weight": 1},
        ],
    },

    # ── Weighted-score combos ──
    {
        "name": "加权评分 (MACD×2 + RSI×1 + Bollinger×1)",
        "method": "weighted-score",
        "scoreThreshold": 0.5,
        "strategies": [
            {"strategyId": "macd", "weight": 2},
            {"strategyId": "rsi", "weight": 1},
            {"strategyId": "bollinger", "weight": 1},
        ],
    },
    {
        "name": "趋势加权 (Turtle×3 + MACD×2 + RSI×1)",
        "method": "weighted-score",
        "scoreThreshold": 0.5,
        "strategies": [
            {"strategyId": "turtle", "weight": 3},
            {"strategyId": "macd", "weight": 2},
            {"strategyId": "rsi", "weight": 1},
        ],
    },

    # ── Gated combos (primary + filters) ──
    {
        "name": "MACD主导+RSI过滤",
        "method": "gated",
        "strategies": [
            {"strategyId": "macd", "weight": 1, "role": "primary"},
            {"strategyId": "rsi", "weight": 1, "role": "filter"},
        ],
    },
    {
        "name": "Bollinger主导+MACD+KDJ过滤",
        "method": "gated",
        "strategies": [
            {"strategyId": "bollinger", "weight": 1, "role": "primary"},
            {"strategyId": "macd", "weight": 1, "role": "filter"},
            {"strategyId": "kdj", "weight": 1, "role": "filter"},
        ],
    },
    {
        "name": "均值回归主导+RSI过滤",
        "method": "gated",
        "strategies": [
            {"strategyId": "mean-reversion-cn", "weight": 1, "role": "primary"},
            {"strategyId": "rsi", "weight": 1, "role": "filter"},
        ],
    },

    # ── Cascade combos (sequential filtering) ──
    {
        "name": "级联: MACD→RSI→Bollinger",
        "method": "cascade",
        "strategies": [
            {"strategyId": "macd", "weight": 1},
            {"strategyId": "rsi", "weight": 1},
            {"strategyId": "bollinger", "weight": 1},
        ],
    },
    {
        "name": "级联: Turtle→MACD→KDJ",
        "method": "cascade",
        "strategies": [
            {"strategyId": "turtle", "weight": 1},
            {"strategyId": "macd", "weight": 1},
            {"strategyId": "kdj", "weight": 1},
        ],
    },

    # ── Single strategy baselines ──
    {
        "name": "单策略基准: MACD",
        "method": "voting",
        "minVotes": 1,
        "strategies": [{"strategyId": "macd", "weight": 1}],
    },
    {
        "name": "单策略基准: RSI",
        "method": "voting",
        "minVotes": 1,
        "strategies": [{"strategyId": "rsi", "weight": 1}],
    },
    {
        "name": "单策略基准: Bollinger",
        "method": "voting",
        "minVotes": 1,
        "strategies": [{"strategyId": "bollinger", "weight": 1}],
    },
]


def run_exploration():
    results = []
    print(f"\n{'='*70}")
    print(f"  策略组合探索  |  {len(CANDIDATES)} 个候选  |  标的: {SYMBOLS}")
    print(f"  回测区间: {START_DATE} ~ {END_DATE}  |  初始资金: {INITIAL_CASH:,}")
    print(f"{'='*70}\n")

    for i, candidate in enumerate(CANDIDATES, 1):
        name = candidate["name"]
        print(f"[{i}/{len(CANDIDATES)}] 正在回测: {name} ...", end=" ", flush=True)
        t0 = time.time()

        config = {
            "action": "compose_backtest",
            "method": candidate["method"],
            "strategies": candidate["strategies"],
            "symbols": SYMBOLS,
            "startDate": START_DATE,
            "endDate": END_DATE,
            "initialCash": INITIAL_CASH,
        }
        if "minVotes" in candidate:
            config["minVotes"] = candidate["minVotes"]
        if "scoreThreshold" in candidate:
            config["scoreThreshold"] = candidate["scoreThreshold"]

        result = compose_backtest(config)
        elapsed = time.time() - t0

        # Collect sub-strategy metrics
        sub_summaries = result.get("subStrategySummaries", [])
        best_sub = max(sub_summaries, key=lambda s: s.get("totalReturn", -999), default={})

        entry = {
            "rank": 0,
            "name": name,
            "method": candidate["method"],
            "ok": result.get("ok", False),
            "combinedSignalCount": result.get("combinedSignalCount", 0),
            "subStrategies": len(candidate["strategies"]),
            "bestSubReturn": best_sub.get("totalReturn", 0),
            "bestSubSharpe": best_sub.get("sharpeRatio", 0),
            "bestSubMaxDD": best_sub.get("maxDrawdown", 0),
            "bestSubWinRate": best_sub.get("winRate", 0),
            "bestSubTrades": best_sub.get("totalTrades", 0),
            "allSubReturns": [s.get("totalReturn", 0) for s in sub_summaries],
            "elapsed": round(elapsed, 1),
        }
        results.append(entry)

        status = "✓" if result.get("ok") else "✗"
        print(f"{status} ({elapsed:.1f}s) 信号={entry['combinedSignalCount']}  "
              f"子策略最佳收益={entry['bestSubReturn']:.1f}%")

    # ── Rank by composite score ──
    for r in results:
        # Score: return contribution (40%) + sharpe (30%) + low drawdown (20%) + signal count (10%)
        ret_score = max(min(r["bestSubReturn"] / 50, 1), -1)  # normalize to [-1, 1]
        sharpe_score = max(min(r["bestSubSharpe"] / 3, 1), -1)
        dd_score = max(1 - r["bestSubMaxDD"] / 30, 0)  # lower DD = better
        sig_score = min(r["combinedSignalCount"] / 20, 1)  # some signals = good
        r["compositeScore"] = round(ret_score * 0.4 + sharpe_score * 0.3 + dd_score * 0.2 + sig_score * 0.1, 3)

    results.sort(key=lambda x: x["compositeScore"], reverse=True)
    for rank, r in enumerate(results, 1):
        r["rank"] = rank

    # ── Print ranking table ──
    print(f"\n{'='*70}")
    print(f"  排名结果 (综合评分 = 收益40% + 夏普30% + 回撤20% + 信号10%)")
    print(f"{'='*70}")
    print(f"{'排名':>4} {'综合分':>6} {'方法':<16} {'组合名称':<35} {'信号':>4} {'最佳收益':>8} {'夏普':>6} {'回撤':>6}")
    print("-" * 90)
    for r in results:
        print(f"{r['rank']:>4} {r['compositeScore']:>6.3f} {r['method']:<16} {r['name']:<35} {r['combinedSignalCount']:>4} {r['bestSubReturn']:>7.1f}% {r['bestSubSharpe']:>6.2f} {r['bestSubMaxDD']:>5.1f}%")

    print(f"\n{'='*70}")
    print(f"  ǰ 3 名详细信息")
    print(f"{'='*70}")
    for r in results[:3]:
        print(f"\n  #{r['rank']} {r['name']}")
        print(f"  方法: {r['method']}  |  组合信号数: {r['combinedSignalCount']}  |  耗时: {r['elapsed']}s")
        print(f"  各子策略收益: {r['allSubReturns']}")
        print(f"  最佳子策略: 收益={r['bestSubReturn']:.1f}%  夏普={r['bestSubSharpe']:.2f}  回撤={r['bestSubMaxDD']:.1f}%  胜率={r['bestSubWinRate']:.1f}%  交易={r['bestSubTrades']}")

    # Save results
    output_path = os.path.join(_this_dir, "_exploration_results.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存: {output_path}")


if __name__ == "__main__":
    run_exploration()
