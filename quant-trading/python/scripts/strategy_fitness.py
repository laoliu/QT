#!/usr/bin/env python3
"""
Strategy-Stock Fitness Analysis — 策略适配分析

对中证1000每只股票遍历所有A股策略做回测，按综合得分保留Top 5策略，
再跨所有股票累加每个策略的综合得分倒排取Top 10全局最优策略。

Actions:
  run          — 执行完整适配分析（异步，可能耗时数小时）
  status       — 查询当前进度
  query_stock  — 查询某股票的最优策略
  query_global — 查询全局策略排名

Input (JSON via stdin / QUANT_CONFIG):
  { "action": "run",
    "universe": "csi1000",          // csi300 | csi500 | csi1000 | custom
    "custom_symbols": [],           // action=run + universe=custom 时使用
    "backtest_years": 5,            // 回测年数
    "top_n_per_stock": 5,           // 每只股票保留 top N 策略
    "top_n_global": 10,             // 全局保留 top N 策略
    "workers": 8,                   // 并行 worker 数量
    "initial_capital": 1000000,     // 回测初始资金
    "symbol": "600519",             // action=query_stock 时使用
    "batch_id": ""                  // 可选：指定批次
  }
"""

import json
import math
import os
import sqlite3
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any

# Proxy bypass
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

import urllib.request
urllib.request.getproxies = lambda: {}  # type: ignore[assignment]
try:
    import requests, requests.utils  # noqa: E401
    requests.utils.getproxies = lambda: {}  # type: ignore[assignment]
except ImportError:
    pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PYTHON_ROOT)

# ── Progress file ────────────────────────────────────────────────

_LOGS_DIR = os.path.join(os.path.dirname(_PYTHON_ROOT), "logs")
_PROGRESS_FILE = os.path.join(_LOGS_DIR, "fitness-progress.json")


def _write_progress(data: dict) -> None:
    os.makedirs(_LOGS_DIR, exist_ok=True)
    try:
        with open(_PROGRESS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


def _read_progress() -> dict:
    try:
        with open(_PROGRESS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": "idle"}


# ── Safe float helper ────────────────────────────────────────────

def _sf(v, default=0.0):
    try:
        f = float(v) if v is not None else default
        return f if math.isfinite(f) else default
    except (ValueError, TypeError):
        return default


# ── Composite Score (同 daily-pick.ts 公式) ───────────────────────

def composite_score(metrics: dict) -> float:
    """计算综合得分，与 TypeScript daily-pick.ts 的 strategyComposite 公式一致。"""
    total_return = _sf(metrics.get("total_return"))
    ann_return = _sf(metrics.get("annualized_return"))
    sharpe = _sf(metrics.get("sharpe_ratio"))
    win_rate = _sf(metrics.get("win_rate"))
    profit_factor = _sf(metrics.get("profit_loss_ratio"))
    max_dd = _sf(metrics.get("max_drawdown"))
    total_trades = _sf(metrics.get("total_trades"))

    score = (
        total_return * 1.0
        + ann_return * 0.35
        + sharpe * 12
        + win_rate * 0.28
        + min(profit_factor, 4) * 4
        - max_dd * 0.85
        + min(total_trades, 120) * 0.03
    )
    return round(score, 4)


# ── Get DB path (same as TS sqlite-storage) ──────────────────────

def _get_db_path() -> str:
    """与 TypeScript sqlite-storage.ts 使用同一数据库文件。"""
    home = os.path.expanduser("~")
    data_dir = os.path.join(home, ".openclaw", "extensions", "quant-trading", "cache")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "quant-trading.db")


# ── Fetch stock universe ─────────────────────────────────────────

def _fetch_universe(universe: str, custom_symbols: list[str] | None = None) -> list[str]:
    """获取指定范围的股票代码列表。"""
    if universe == "custom" and custom_symbols:
        return list(custom_symbols)

    # 尝试 akshare 获取指数成分股
    index_map = {
        "csi300": "000300",
        "csi500": "000905",
        "csi1000": "000852",
    }
    index_code = index_map.get(universe, "000852")

    symbols: list[str] = []

    # 方法1: akshare
    try:
        import akshare as ak
        df = ak.index_stock_cons(symbol=index_code)
        if df is not None and not df.empty:
            col = "品种代码" if "品种代码" in df.columns else df.columns[0]
            symbols = df[col].astype(str).str.strip().tolist()
            # 规范化为6位
            symbols = [s.zfill(6) for s in symbols if s.strip()]
            print(f"[Universe] akshare获取 {universe} 成分股: {len(symbols)} 只", file=sys.stderr)
    except Exception as e:
        print(f"[Universe] akshare获取失败: {e}", file=sys.stderr)

    # 方法2: tushare fallback
    if not symbols:
        try:
            from aipha.data.tushare_equity_provider import TuShareEquityProvider
            provider = TuShareEquityProvider()
            tushare_code = f"{index_code}.SH"
            df = provider.load_index_members(index_code=tushare_code)
            if df is not None and not df.empty:
                col = "con_code" if "con_code" in df.columns else df.columns[0]
                raw = df[col].astype(str).tolist()
                symbols = [s.split(".")[0].zfill(6) for s in raw if s.strip()]
                print(f"[Universe] tushare获取 {universe} 成分股: {len(symbols)} 只", file=sys.stderr)
        except Exception as e2:
            print(f"[Universe] tushare fallback失败: {e2}", file=sys.stderr)

    # 方法3: 从现有 stock_cache 中获取
    if not symbols:
        try:
            from cache.stock_cache import get_cache
            cache = get_cache()
            all_stocks = cache.get_stock_list()
            if all_stocks:
                symbols = [s["code"] for s in all_stocks if s.get("code")]
                # 对于中证1000范围，取市值较小的1000只
                if universe == "csi1000" and len(symbols) > 1000:
                    symbols = symbols[:1000]
                elif universe == "csi500" and len(symbols) > 500:
                    symbols = symbols[:500]
                elif universe == "csi300" and len(symbols) > 300:
                    symbols = symbols[:300]
                print(f"[Universe] stock_cache fallback: {len(symbols)} 只", file=sys.stderr)
        except Exception as e3:
            print(f"[Universe] stock_cache fallback失败: {e3}", file=sys.stderr)

    # 过滤 ST、退市
    symbols = [s for s in symbols if s and len(s) == 6 and s[0] in ("0", "3", "6")]
    return symbols


# ── Get A-stock strategies (exclude CB) ──────────────────────────

def _get_astock_strategies() -> list[str]:
    """获取所有A股策略ID（排除可转债 cb_ 开头的）。"""
    try:
        import aipha.strategy.register_all  # noqa: F401 — triggers registration
        from aipha.strategy.registry import StrategyRegistry
        reg = StrategyRegistry()
        all_ids = reg.list_strategies()
        a_stock = [sid for sid in all_ids if not sid.startswith("cb_")
                    and sid not in ("double_low", "conversion_arbitrage",
                                    "double_low_plus", "double_low_percentile",
                                    "double_low_rank", "double_low_zscore",
                                    "forced_redeem_play", "low_premium", "low_price",
                                    "negative_premium", "new_bond", "new_bond_ensemble",
                                    "new_small", "new_small_ensemble", "pseudo_ytm",
                                    "triple_low", "turnover_surge", "ytm_plus")]
        print(f"[Strategies] 可用A股策略: {len(a_stock)}", file=sys.stderr)
        return a_stock
    except Exception as e:
        print(f"[Strategies] 获取策略列表失败: {e}", file=sys.stderr)
        return []


# ── Load kline data ──────────────────────────────────────────────

def _load_klines(symbols: list[str], start_date: str, end_date: str) -> dict[str, list[dict]]:
    """批量加载K线数据。"""
    try:
        from data_providers.akshare_provider import get_klines_batch
        return get_klines_batch(symbols, start_date, end_date, market="cn_stock", kline_days=0)
    except Exception as e:
        print(f"[Klines] 加载失败: {e}", file=sys.stderr)
        return {}


# ── Single stock-strategy backtest ───────────────────────────────

def _run_single_backtest(
    symbol: str,
    strategy_id: str,
    klines: list[dict],
    initial_capital: float,
) -> dict | None:
    """对单只股票运行单个策略的回测，返回 metrics dict 或 None。"""
    try:
        from aipha.strategy.registry import StrategyRegistry
        from aipha.backtest.engine import BacktestEngine, BacktestConfig
        from aipha.domain.models import Bar
        from aipha.utils.metrics import compute_all_metrics

        reg = StrategyRegistry()
        strategy = reg.create(strategy_id)

        # 将 kline dicts 转换为 {date: {symbol: Bar}} 格式
        bars_by_date: dict[date, dict[str, Bar]] = {}
        for bar_dict in klines:
            ts = bar_dict.get("timestamp", 0)
            if isinstance(ts, (int, float)) and ts > 1e12:
                dt = datetime.fromtimestamp(ts / 1000).date()
            elif isinstance(ts, (int, float)):
                dt = datetime.fromtimestamp(ts).date()
            elif isinstance(ts, str):
                dt = datetime.strptime(ts[:10], "%Y-%m-%d").date()
            else:
                continue

            bar = Bar(
                symbol=symbol,
                timestamp=int(ts) if isinstance(ts, (int, float)) else 0,
                open=_sf(bar_dict.get("open")),
                high=_sf(bar_dict.get("high")),
                low=_sf(bar_dict.get("low")),
                close=_sf(bar_dict.get("close")),
                volume=_sf(bar_dict.get("volume")),
                amount=_sf(bar_dict.get("amount")),
            )
            if dt not in bars_by_date:
                bars_by_date[dt] = {}
            bars_by_date[dt][symbol] = bar

        if len(bars_by_date) < 60:  # 至少60个交易日
            return None

        config = BacktestConfig(initial_capital=initial_capital)
        engine = BacktestEngine(config=config, strategy=strategy)
        result = engine.run(bars_by_date)

        # 计算指标
        if not result.daily_snapshots:
            return None

        nav_series = [s.nav for s in result.daily_snapshots]
        metrics = compute_all_metrics(nav_series)
        metrics["total_trades"] = result.total_trades
        metrics["strategy_id"] = strategy_id
        metrics["symbol"] = symbol
        metrics["backtest_start"] = str(result.start_date)
        metrics["backtest_end"] = str(result.end_date)
        metrics["composite_score"] = composite_score(metrics)

        return metrics
    except Exception as e:
        print(f"[Backtest] {symbol}/{strategy_id} 失败: {e}", file=sys.stderr)
        return None


# ── Worker function for multiprocessing ──────────────────────────

def _worker_backtest(args: tuple) -> list[dict]:
    """Worker: 对一只股票运行所有策略的回测。

    返回该股票所有策略的 metrics list。
    """
    symbol, strategy_ids, klines, initial_capital = args

    # 确保子进程中策略已注册
    try:
        import aipha.strategy.register_all  # noqa: F401
    except Exception:
        pass

    results = []
    for sid in strategy_ids:
        m = _run_single_backtest(symbol, sid, klines, initial_capital)
        if m and math.isfinite(m.get("composite_score", 0)):
            results.append(m)
    return results


# ── Save results to DB ───────────────────────────────────────────

def _save_to_db(
    batch_id: str,
    stock_fitness: list[dict],
    global_ranking: list[dict],
) -> None:
    """将结果写入 SQLite 数据库（与 TypeScript 共用）。"""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    try:
        # 确保表存在
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS stock_strategy_fitness (
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              symbol          TEXT NOT NULL,
              stock_name      TEXT,
              strategy_id     TEXT NOT NULL,
              strategy_name   TEXT,
              rank            INTEGER NOT NULL,
              composite_score REAL NOT NULL,
              total_return    REAL,
              annualized_return REAL,
              sharpe_ratio    REAL,
              sortino_ratio   REAL,
              max_drawdown    REAL,
              win_rate        REAL,
              profit_loss_ratio REAL,
              calmar_ratio    REAL,
              total_trades    INTEGER,
              backtest_start  TEXT,
              backtest_end    TEXT,
              batch_id        TEXT NOT NULL,
              created_at      TEXT NOT NULL DEFAULT (datetime('now')),
              UNIQUE (symbol, strategy_id, batch_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ssf_symbol ON stock_strategy_fitness(symbol);
            CREATE INDEX IF NOT EXISTS idx_ssf_strategy ON stock_strategy_fitness(strategy_id);
            CREATE INDEX IF NOT EXISTS idx_ssf_batch ON stock_strategy_fitness(batch_id);

            CREATE TABLE IF NOT EXISTS global_strategy_ranking (
              id              INTEGER PRIMARY KEY AUTOINCREMENT,
              strategy_id     TEXT NOT NULL,
              strategy_name   TEXT,
              rank            INTEGER NOT NULL,
              total_score     REAL NOT NULL,
              stock_count     INTEGER NOT NULL,
              avg_score       REAL,
              max_score       REAL,
              min_score       REAL,
              batch_id        TEXT NOT NULL,
              created_at      TEXT NOT NULL DEFAULT (datetime('now')),
              UNIQUE (strategy_id, batch_id)
            );
            CREATE INDEX IF NOT EXISTS idx_gsr_strategy ON global_strategy_ranking(strategy_id);
            CREATE INDEX IF NOT EXISTS idx_gsr_batch ON global_strategy_ranking(batch_id);
        """)

        # 批量插入 stock_strategy_fitness
        for row in stock_fitness:
            conn.execute("""
                INSERT OR REPLACE INTO stock_strategy_fitness
                  (symbol, stock_name, strategy_id, strategy_name, rank, composite_score,
                   total_return, annualized_return, sharpe_ratio, sortino_ratio, max_drawdown,
                   win_rate, profit_loss_ratio, calmar_ratio, total_trades,
                   backtest_start, backtest_end, batch_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row["symbol"], row.get("stock_name"),
                row["strategy_id"], row.get("strategy_name"),
                row["rank"], row["composite_score"],
                row.get("total_return"), row.get("annualized_return"),
                row.get("sharpe_ratio"), row.get("sortino_ratio"),
                row.get("max_drawdown"), row.get("win_rate"),
                row.get("profit_loss_ratio"), row.get("calmar_ratio"),
                row.get("total_trades"),
                row.get("backtest_start"), row.get("backtest_end"),
                batch_id,
            ))

        # 批量插入 global_strategy_ranking
        for row in global_ranking:
            conn.execute("""
                INSERT OR REPLACE INTO global_strategy_ranking
                  (strategy_id, strategy_name, rank, total_score, stock_count,
                   avg_score, max_score, min_score, batch_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row["strategy_id"], row.get("strategy_name"),
                row["rank"], row["total_score"], row["stock_count"],
                row.get("avg_score"), row.get("max_score"),
                row.get("min_score"), batch_id,
            ))

        conn.commit()
        print(f"[DB] 已保存 {len(stock_fitness)} 条 fitness + {len(global_ranking)} 条 ranking", file=sys.stderr)
    finally:
        conn.close()


# ── Query helpers ─────────────────────────────────────────────────

def _query_stock(symbol: str, batch_id: str = "") -> dict:
    """查询某股票的最优策略。"""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not batch_id:
            row = conn.execute(
                "SELECT batch_id FROM stock_strategy_fitness ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            batch_id = row["batch_id"] if row else ""
        if not batch_id:
            return {"symbol": symbol, "strategies": [], "error": "无可用数据"}

        rows = conn.execute(
            "SELECT * FROM stock_strategy_fitness WHERE symbol = ? AND batch_id = ? ORDER BY rank ASC",
            (symbol, batch_id),
        ).fetchall()
        return {
            "symbol": symbol,
            "batch_id": batch_id,
            "strategies": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def _query_global(batch_id: str = "") -> dict:
    """查询全局策略排名。"""
    db_path = _get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not batch_id:
            row = conn.execute(
                "SELECT batch_id FROM global_strategy_ranking ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            batch_id = row["batch_id"] if row else ""
        if not batch_id:
            return {"strategies": [], "error": "无可用数据"}

        rows = conn.execute(
            "SELECT * FROM global_strategy_ranking WHERE batch_id = ? ORDER BY rank ASC",
            (batch_id,),
        ).fetchall()
        return {
            "batch_id": batch_id,
            "strategies": [dict(r) for r in rows],
        }
    finally:
        conn.close()


# ── Main run action ──────────────────────────────────────────────

def run_fitness_analysis(
    universe: str = "csi1000",
    custom_symbols: list[str] | None = None,
    backtest_years: int = 5,
    top_n_per_stock: int = 5,
    top_n_global: int = 10,
    workers: int = 8,
    initial_capital: float = 1_000_000,
) -> dict:
    """执行策略适配分析主流程。"""
    batch_id = f"fitness_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    t0 = time.time()

    _write_progress({
        "batch_id": batch_id,
        "status": "starting",
        "total_stocks": 0,
        "completed_stocks": 0,
        "elapsed_seconds": 0,
    })

    # Step 1: 获取股票列表
    print(f"[Step 1] 获取 {universe} 成分股...", file=sys.stderr)
    symbols = _fetch_universe(universe, custom_symbols)
    if not symbols:
        return {"error": "无法获取股票列表", "batch_id": batch_id}
    print(f"[Step 1] 共 {len(symbols)} 只股票", file=sys.stderr)

    # Step 2: 获取策略列表
    print("[Step 2] 获取A股策略列表...", file=sys.stderr)
    strategy_ids = _get_astock_strategies()
    if not strategy_ids:
        return {"error": "无可用策略", "batch_id": batch_id}
    print(f"[Step 2] 共 {len(strategy_ids)} 个策略", file=sys.stderr)

    # Step 3: 计算回测时间范围
    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=backtest_years * 365)).strftime("%Y-%m-%d")
    print(f"[Step 3] 回测区间: {start_date} → {end_date}", file=sys.stderr)

    # Step 4: 批量加载K线数据
    print(f"[Step 4] 加载K线数据 (分批，每批50只)...", file=sys.stderr)
    all_klines: dict[str, list[dict]] = {}
    batch_size = 50
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        batch_klines = _load_klines(batch, start_date, end_date)
        all_klines.update(batch_klines)
        print(f"  K线加载进度: {min(i + batch_size, len(symbols))}/{len(symbols)}", file=sys.stderr)

    # 过滤掉无数据的股票
    valid_symbols = [s for s in symbols if s in all_klines and len(all_klines[s]) >= 60]
    print(f"[Step 4] 有效股票: {len(valid_symbols)}/{len(symbols)}", file=sys.stderr)

    if not valid_symbols:
        return {"error": "所有股票K线数据不足", "batch_id": batch_id}

    _write_progress({
        "batch_id": batch_id,
        "status": "running",
        "total_stocks": len(valid_symbols),
        "completed_stocks": 0,
        "total_strategies": len(strategy_ids),
        "current_stock": "",
        "elapsed_seconds": int(time.time() - t0),
        "estimated_remaining": None,
    })

    # Step 5: 并行回测
    print(f"[Step 5] 开始回测 (workers={workers})...", file=sys.stderr)

    # 收集所有结果: {symbol: [metrics_dict, ...]}
    all_results: dict[str, list[dict]] = {}
    completed = 0

    # 使用 ProcessPoolExecutor 并行
    tasks = [
        (sym, strategy_ids, all_klines[sym], initial_capital)
        for sym in valid_symbols
    ]

    # 限制 workers 数量
    actual_workers = min(workers, len(tasks), os.cpu_count() or 4)

    try:
        with ProcessPoolExecutor(max_workers=actual_workers) as executor:
            futures = {
                executor.submit(_worker_backtest, task): task[0]
                for task in tasks
            }
            for future in as_completed(futures):
                sym = futures[future]
                completed += 1
                try:
                    results = future.result(timeout=600)  # 10分钟超时
                    if results:
                        all_results[sym] = results
                except Exception as e:
                    print(f"  [Error] {sym}: {e}", file=sys.stderr)

                # 更新进度
                if completed % 10 == 0 or completed == len(tasks):
                    elapsed = time.time() - t0
                    rate = completed / elapsed if elapsed > 0 else 0
                    remaining = (len(tasks) - completed) / rate if rate > 0 else None
                    _write_progress({
                        "batch_id": batch_id,
                        "status": "running",
                        "total_stocks": len(valid_symbols),
                        "completed_stocks": completed,
                        "total_strategies": len(strategy_ids),
                        "current_stock": sym,
                        "elapsed_seconds": int(elapsed),
                        "estimated_remaining": int(remaining) if remaining else None,
                    })
                    print(f"  进度: {completed}/{len(tasks)} ({completed*100//len(tasks)}%)", file=sys.stderr)
    except Exception as e:
        # 如果 multiprocessing 失败，fallback 到单线程
        print(f"[Step 5] 多进程失败，降级为单线程: {e}", file=sys.stderr)
        for task in tasks:
            sym = task[0]
            completed += 1
            try:
                results = _worker_backtest(task)
                if results:
                    all_results[sym] = results
            except Exception as e2:
                print(f"  [Error] {sym}: {e2}", file=sys.stderr)
            if completed % 10 == 0:
                print(f"  进度: {completed}/{len(tasks)}", file=sys.stderr)

    print(f"[Step 5] 回测完成: {len(all_results)} 只股票有结果", file=sys.stderr)

    # Step 6: 每只股票取 Top N
    print(f"[Step 6] 计算每只股票 Top {top_n_per_stock} 策略...", file=sys.stderr)
    stock_fitness: list[dict] = []
    strategy_scores: dict[str, list[float]] = {}  # {strategy_id: [scores...]}

    for sym, metrics_list in all_results.items():
        # 按 composite_score 降序排序
        sorted_m = sorted(metrics_list, key=lambda m: m.get("composite_score", 0), reverse=True)
        top = sorted_m[:top_n_per_stock]
        for rank_idx, m in enumerate(top, start=1):
            sid = m["strategy_id"]
            score = m["composite_score"]
            stock_fitness.append({
                "symbol": sym,
                "strategy_id": sid,
                "rank": rank_idx,
                "composite_score": score,
                "total_return": m.get("total_return"),
                "annualized_return": m.get("annualized_return"),
                "sharpe_ratio": m.get("sharpe_ratio"),
                "sortino_ratio": m.get("sortino_ratio"),
                "max_drawdown": m.get("max_drawdown"),
                "win_rate": m.get("win_rate"),
                "profit_loss_ratio": m.get("profit_loss_ratio"),
                "calmar_ratio": m.get("calmar_ratio"),
                "total_trades": m.get("total_trades"),
                "backtest_start": m.get("backtest_start"),
                "backtest_end": m.get("backtest_end"),
            })

            # 累加到全局策略得分
            if sid not in strategy_scores:
                strategy_scores[sid] = []
            strategy_scores[sid].append(score)

    print(f"[Step 6] 共 {len(stock_fitness)} 条 stock-fitness 记录", file=sys.stderr)

    # Step 7: 全局策略排名
    print(f"[Step 7] 计算全局 Top {top_n_global} 策略...", file=sys.stderr)
    strategy_agg = []
    for sid, scores in strategy_scores.items():
        strategy_agg.append({
            "strategy_id": sid,
            "total_score": round(sum(scores), 4),
            "stock_count": len(scores),
            "avg_score": round(sum(scores) / len(scores), 4) if scores else 0,
            "max_score": round(max(scores), 4) if scores else 0,
            "min_score": round(min(scores), 4) if scores else 0,
        })

    # 按 total_score 降序排序
    strategy_agg.sort(key=lambda x: x["total_score"], reverse=True)
    global_ranking = strategy_agg[:top_n_global]
    for rank_idx, item in enumerate(global_ranking, start=1):
        item["rank"] = rank_idx

    print(f"[Step 7] 全局策略排名:", file=sys.stderr)
    for item in global_ranking:
        print(f"  #{item['rank']}: {item['strategy_id']} "
              f"(total_score={item['total_score']:.2f}, "
              f"stocks={item['stock_count']}, "
              f"avg={item['avg_score']:.2f})", file=sys.stderr)

    # Step 8: 保存到数据库
    print("[Step 8] 保存结果到数据库...", file=sys.stderr)
    _save_to_db(batch_id, stock_fitness, global_ranking)

    elapsed = time.time() - t0
    _write_progress({
        "batch_id": batch_id,
        "status": "done",
        "total_stocks": len(valid_symbols),
        "completed_stocks": completed,
        "total_strategies": len(strategy_ids),
        "elapsed_seconds": int(elapsed),
        "results": {
            "fitness_records": len(stock_fitness),
            "global_ranking_count": len(global_ranking),
        },
    })

    return {
        "batch_id": batch_id,
        "universe": universe,
        "total_stocks": len(valid_symbols),
        "total_strategies": len(strategy_ids),
        "fitness_records": len(stock_fitness),
        "elapsed_seconds": int(elapsed),
        "global_top_strategies": global_ranking,
        "sample_stock_fitness": stock_fitness[:10],  # 返回前10条预览
    }


# ── Entry point ──────────────────────────────────────────────────

if __name__ == "__main__":
    _raw = os.environ.get("QUANT_CONFIG") or sys.stdin.read()
    config = json.loads(_raw)
    action = config.get("action", "run")

    try:
        if action == "run":
            result = run_fitness_analysis(
                universe=config.get("universe", "csi1000"),
                custom_symbols=config.get("custom_symbols"),
                backtest_years=config.get("backtest_years", 5),
                top_n_per_stock=config.get("top_n_per_stock", 5),
                top_n_global=config.get("top_n_global", 10),
                workers=config.get("workers", 8),
                initial_capital=config.get("initial_capital", 1_000_000),
            )
        elif action == "status":
            result = _read_progress()
        elif action == "query_stock":
            symbol = config.get("symbol", "")
            if not symbol:
                result = {"error": "需要 symbol 参数"}
            else:
                result = _query_stock(symbol, config.get("batch_id", ""))
        elif action == "query_global":
            result = _query_global(config.get("batch_id", ""))
        else:
            result = {"error": f"未知操作: {action}"}
    except Exception as e:
        result = {"error": str(e), "traceback": traceback.format_exc()}

    print(json.dumps(result, ensure_ascii=False, default=str))
