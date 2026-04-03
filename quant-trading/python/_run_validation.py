#!/usr/bin/env python3
"""
Phase 2: Validate top strategy combos across multiple stocks.
Uses SQLite cache for data.
"""
import json
import os
import sys
import time
import sqlite3

_this_dir = os.path.dirname(os.path.abspath(__file__))
if _this_dir not in sys.path:
    sys.path.insert(0, _this_dir)

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
from engines.dynamic_composer import compose_backtest

# ── Configuration ──────────────────────────────────────────────
# Top stocks with 300+ bars in cache
STOCKS = {
    "600519": "贵州茅台",
    "000858": "五粮液",
    "300750": "宁德时代",
    "601318": "中国平安",
    "000001": "平安银行",
    "000651": "格力电器",
    "600036": "招商银行",
    "600276": "恒瑞医药",
}
START_DATE = "2024-10-08"  # Common start date for most cached stocks
END_DATE = "2025-12-31"
INITIAL_CASH = 100000

# Top 5 strategies from Phase 1
TOP_STRATEGIES = [
    {
        "name": "Bollinger 单策略",
        "method": "voting", "minVotes": 1,
        "strategies": [{"strategyId": "bollinger", "weight": 1}],
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
        "name": "趋势+均值回归投票 (MACD+Bollinger+均值回归)",
        "method": "voting", "minVotes": 2,
        "strategies": [
            {"strategyId": "macd", "weight": 1},
            {"strategyId": "bollinger", "weight": 1},
            {"strategyId": "mean-reversion-cn", "weight": 1},
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
    {
        "name": "加权评分 (MACD×2+RSI+Bollinger)",
        "method": "weighted-score", "scoreThreshold": 0.5,
        "strategies": [
            {"strategyId": "macd", "weight": 2},
            {"strategyId": "rsi", "weight": 1},
            {"strategyId": "bollinger", "weight": 1},
        ],
    },
]


def run_validation():
    print(f"\n{'='*80}")
    print(f"  多标的验证  |  {len(TOP_STRATEGIES)} 策略 × {len(STOCKS)} 标的")
    print(f"  回测区间: {START_DATE} ~ {END_DATE}  |  初始资金: {INITIAL_CASH:,}")
    print(f"{'='*80}\n")

    # results[strategy_name] = {stock: metrics}
    all_results: dict[str, dict[str, dict]] = {}

    for strat in TOP_STRATEGIES:
        sname = strat["name"]
        all_results[sname] = {}
        print(f"\n── {sname} ──")
        for code, stock_name in STOCKS.items():
            config = {
                "action": "compose_backtest",
                "method": strat["method"],
                "strategies": strat["strategies"],
                "symbols": [code],
                "startDate": START_DATE,
                "endDate": END_DATE,
                "initialCash": INITIAL_CASH,
            }
            if "minVotes" in strat:
                config["minVotes"] = strat["minVotes"]
            if "scoreThreshold" in strat:
                config["scoreThreshold"] = strat["scoreThreshold"]

            result = compose_backtest(config)
            subs = result.get("subStrategySummaries", [])
            best_sub = max(subs, key=lambda s: s.get("totalReturn", -999), default={})

            m = {
                "return": best_sub.get("totalReturn", 0),
                "sharpe": best_sub.get("sharpeRatio", 0),
                "maxDD": best_sub.get("maxDrawdown", 0),
                "winRate": best_sub.get("winRate", 0),
                "trades": best_sub.get("totalTrades", 0),
                "signals": result.get("combinedSignalCount", 0),
            }
            all_results[sname][code] = m
            ret = m["return"]
            mark = "🟢" if ret > 5 else "🟡" if ret > 0 else "🔴"
            print(f"  {mark} {code} {stock_name:<6} 收益={ret:>7.1f}%  夏普={m['sharpe']:>6.2f}  回撤={m['maxDD']:>5.1f}%  胜率={m['winRate']:>5.1f}%  信号={m['signals']}")

    # ── Aggregate summary ──
    print(f"\n{'='*80}")
    print(f"  综合排名 (平均收益 + 平均夏普 + 胜率)")
    print(f"{'='*80}")
    summaries = []
    for sname, stocks in all_results.items():
        returns = [v["return"] for v in stocks.values()]
        sharpes = [v["sharpe"] for v in stocks.values()]
        dds = [v["maxDD"] for v in stocks.values()]
        wrs = [v["winRate"] for v in stocks.values()]
        positive = sum(1 for r in returns if r > 0)
        avg_ret = sum(returns) / len(returns) if returns else 0
        avg_sharpe = sum(sharpes) / len(sharpes) if sharpes else 0
        avg_dd = sum(dds) / len(dds) if dds else 0
        avg_wr = sum(wrs) / len(wrs) if wrs else 0
        # Composite: 40% return + 30% sharpe + 20% low-DD + 10% positive-rate
        score = (min(avg_ret / 30, 1) * 0.4 +
                 min(avg_sharpe / 5, 1) * 0.3 +
                 max(1 - avg_dd / 25, 0) * 0.2 +
                 (positive / len(returns)) * 0.1)
        summaries.append({
            "name": sname,
            "avgReturn": round(avg_ret, 2),
            "avgSharpe": round(avg_sharpe, 2),
            "avgMaxDD": round(avg_dd, 2),
            "avgWinRate": round(avg_wr, 1),
            "positiveStocks": positive,
            "totalStocks": len(returns),
            "score": round(score, 3),
        })

    summaries.sort(key=lambda x: x["score"], reverse=True)
    print(f"\n{'排名':>4} {'综合分':>6} {'策略':>35} {'平均收益':>8} {'平均夏普':>8} {'平均回撤':>8} {'平均胜率':>8} {'盈利数':>6}")
    print("-" * 100)
    for i, s in enumerate(summaries, 1):
        print(f"{i:>4} {s['score']:>6.3f} {s['name']:>35} {s['avgReturn']:>7.1f}% {s['avgSharpe']:>8.2f} {s['avgMaxDD']:>7.1f}% {s['avgWinRate']:>7.1f}% {s['positiveStocks']:>3}/{s['totalStocks']}")

    # Save
    out = {"summaries": summaries, "details": {k: v for k, v in all_results.items()}}
    path = os.path.join(_this_dir, "_validation_results.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"\n详细结果已保存: {path}")


if __name__ == "__main__":
    run_validation()
