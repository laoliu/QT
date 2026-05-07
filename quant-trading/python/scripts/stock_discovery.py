#!/usr/bin/env python3
"""
Stock Discovery — 多维度选股引擎

通过多种算法在全市场或指定股票池中寻找潜力品种。

支持的选股策略:
  low_position    — 低位潜力股 (52周低位 + 基本面健康 + 资金流入)
  turnaround      — 困境反转股 (EPS扭亏 + 毛利率改善 + 营收复苏)
  relative_strength — 相对强度RS (欧奈尔RS评级 >= 75)
  sector_laggard  — 板块补涨股 (板块强势但个股滞涨)
  value_quality   — 价值质量股 (Magic Formula + F-Score)
  growth_momentum — 成长动量股 (CAN SLIM)
  volume_anomaly  — 量价异动股 (机构建仓迹象)
  combo           — 综合组合 (以上全部运行，去重合并)

Input (stdin JSON):
  {
    "action": "discover",
    "strategy": "low_position",      // 选股策略ID 或 "combo"
    "universe": "csi500",            // csi300 | csi500 | csi1000 | all | custom
    "symbols": ["600519", "000001"], // custom universe时提供
    "limit": 20,                     // 最多返回数量
    "with_klines": true,             // 是否加载K线数据 (较慢但信号更准确)
    "kline_days": 252,               // K线加载天数
    "params": {}                     // 传给 screener 的额外参数
  }

Output:
  {
    "ok": true,
    "strategy": "low_position",
    "universe_count": 500,
    "screened_count": 18,
    "results": [
      {
        "rank": 1,
        "stock_id": "600519",
        "name": "贵州茅台",
        "score": 8.73,
        "factors": {...},
        "signals": ["低位: 距52周高点打42.3%折扣", "F-Score=7/9", ...]
      }
    ],
    "elapsed_seconds": 12.3
  }
"""

import json
import math
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta

# ── Proxy bypass ──────────────────────────────────────────────────
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"
urllib.request.getproxies = lambda: {}  # type: ignore[assignment]
try:
    import requests, requests.utils  # noqa: E401
    requests.utils.getproxies = lambda: {}  # type: ignore[assignment]
    if not getattr(requests.Session.request, "_timeout_patched", False):
        _orig_req = requests.Session.request
        def _patched_request(self, method, url, **kwargs):
            kwargs.setdefault("timeout", 20)
            return _orig_req(self, method, url, **kwargs)
        _patched_request._timeout_patched = True  # type: ignore[attr-defined]
        requests.Session.request = _patched_request  # type: ignore[assignment]
except ImportError:
    pass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PYTHON_ROOT)


def _safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default


def _sanitize(obj):
    if isinstance(obj, float):
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


# ── Universe loaders ──────────────────────────────────────────────

def _load_universe_from_spot(universe: str) -> list[dict]:
    """Fetch all A-share quotes via akshare stock_zh_a_spot_em.

    Returns list of {code, name, price, pe, pb, total_market_val_yi, industry, ...}
    Falls back to cached symbols from SQLite when network is unavailable.
    """
    last_err = None
    for attempt in range(2):
        try:
            import akshare as ak
            print(f"[discovery] 加载行情快照 ({universe}, 尝试 {attempt+1})...",
                  file=sys.stderr, flush=True)
            df = ak.stock_zh_a_spot_em()
            if df is None or df.empty:
                return []

            # Filter by universe
            records = []
            for _, row in df.iterrows():
                code = str(row.get("代码", "")).zfill(6)
                name = str(row.get("名称", ""))
                price = _safe_float(row.get("最新价"))
                pe = _safe_float(row.get("市盈率-动态"))
                pb = _safe_float(row.get("市净率"))
                total_mv_yi = _safe_float(row.get("总市值")) / 1e8  # 元→亿

                # Skip ST stocks and obviously invalid entries
                if not code or not name:
                    continue
                if "ST" in name or "*" in name:
                    continue
                if price <= 0:
                    continue

                # Universe filter
                if universe == "csi300" and total_mv_yi < 100:
                    continue
                elif universe == "csi500" and (total_mv_yi < 30 or total_mv_yi > 600):
                    continue
                elif universe == "csi1000" and (total_mv_yi < 10 or total_mv_yi > 500):
                    continue
                # "all" = no filter

                records.append({
                    "code": code,
                    "name": name,
                    "price": price,
                    "pe": pe if pe > 0 else None,
                    "pb": pb if pb > 0 else None,
                    "total_market_val_yi": total_mv_yi,
                    "change_pct": _safe_float(row.get("涨跌幅")),
                    "volume": _safe_float(row.get("成交量")),
                    "turnover_rate": _safe_float(row.get("换手率")),
                    "amplitude": _safe_float(row.get("振幅")),
                })

            print(f"[discovery] 行情快照加载完成，{len(records)} 只股票",
                  file=sys.stderr, flush=True)
            return records
        except Exception as e:
            last_err = e
            print(f"[discovery] 行情快照加载失败 (尝试{attempt+1}): {e}",
                  file=sys.stderr, flush=True)
            if attempt == 0:
                import time as _t
                _t.sleep(2)

    # ── Fallback: load symbols from local SQLite cache ────────────
    print("[discovery] akshare 不可用，尝试从本地缓存加载...", file=sys.stderr, flush=True)
    try:
        import sqlite3
        data_dir = os.environ.get(
            "QUANT_DATA_DIR",
            os.path.join(os.path.expanduser("~"), ".openclaw", "quant-data"),
        )
        db_path = os.path.join(data_dir, "quant-trading.db")
        if not os.path.exists(db_path):
            print(f"[discovery] 缓存数据库不存在: {db_path}", file=sys.stderr, flush=True)
            return []

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Derive universe from stock_strategy_fitness (has symbol + stock_name + perf data)
        # Use composite_score as a rough proxy to estimate universe tier
        rows = conn.execute(
            """
            SELECT DISTINCT symbol, stock_name,
                   AVG(composite_score) AS avg_score,
                   COUNT(*) AS strategy_count
            FROM stock_strategy_fitness
            WHERE symbol IS NOT NULL AND symbol != ''
            GROUP BY symbol
            ORDER BY avg_score DESC
            """
        ).fetchall()
        conn.close()

        if not rows:
            print("[discovery] 缓存中无股票数据", file=sys.stderr, flush=True)
            return []

        records = []
        for r in rows:
            code = str(r["symbol"]).zfill(6)
            name = str(r["stock_name"] or "")
            if not code or "ST" in name:
                continue

            records.append({
                "code": code,
                "name": name,
                "price": 0.0,       # unknown — screeners must tolerate this
                "pe": None,
                "pb": None,
                "total_market_val_yi": None,
                "change_pct": None,
                "volume": None,
                "turnover_rate": None,
                "_from_cache": True,
            })

        # Universe size heuristic (cache has no market-cap, take top slice)
        if universe == "csi300":
            records = records[:300]
        elif universe == "csi500":
            records = records[:500]
        elif universe == "csi1000":
            records = records[:1000]

        print(f"[discovery] 从缓存加载 {len(records)} 只股票 (离线模式)",
              file=sys.stderr, flush=True)
        return records

    except Exception as e2:
        print(f"[discovery] 缓存加载也失败: {e2}", file=sys.stderr, flush=True)
        return []


def _load_klines_from_cache(code: str, days: int) -> list[dict]:
    """Load klines from SQLite cache (same DB as investment_planner)."""
    try:
        import sqlite3
        data_dir = os.environ.get("QUANT_DATA_DIR",
                                   os.path.join(os.path.expanduser("~"), ".openclaw", "quant-data"))
        db_path = os.path.join(data_dir, "quant-trading.db")
        if not os.path.exists(db_path):
            return []

        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y-%m-%d")

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT trade_date, open_price as open, high, low, close_price as close, volume, amount "
            "FROM kline_data WHERE symbol = ? AND period = 'daily' "
            "AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
            (code, start_date, end_date),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows[-days:]] if rows else []
    except Exception:
        return []


def _load_fundamentals_from_cache(code: str) -> dict:
    """Load fundamentals from SQLite cache."""
    try:
        import sqlite3
        data_dir = os.environ.get("QUANT_DATA_DIR",
                                   os.path.join(os.path.expanduser("~"), ".openclaw", "quant-data"))
        db_path = os.path.join(data_dir, "quant-trading.db")
        if not os.path.exists(db_path):
            return {}

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM stock_fundamentals WHERE code = ? ORDER BY fetch_date DESC LIMIT 1",
            (code,),
        ).fetchone()
        conn.close()
        if not row:
            return {}
        d = dict(row)
        raw_json = d.pop("data_json", None)
        if raw_json:
            try:
                extra = json.loads(raw_json)
                for k, v in extra.items():
                    if v is not None and d.get(k) in (None, 0):
                        d[k] = v
            except (json.JSONDecodeError, TypeError):
                pass
        return d
    except Exception:
        return {}


def _build_stock_data(
    spot_records: list[dict],
    with_klines: bool,
    kline_days: int,
) -> dict[str, dict]:
    """Build the stock_data dict expected by screeners."""
    print(f"[discovery] 构建数据集 (K线: {with_klines}, {len(spot_records)} 只)...",
          file=sys.stderr, flush=True)
    t0 = time.time()
    stock_data: dict[str, dict] = {}

    for rec in spot_records:
        code = rec["code"]
        fundamentals = _load_fundamentals_from_cache(code)

        data: dict = {
            "name": rec["name"],
            "price": rec["price"],
            "close": rec["price"],
            "pe": rec.get("pe") or fundamentals.get("pe") or fundamentals.get("pe_ttm"),
            "pb": rec.get("pb") or fundamentals.get("pb"),
            "total_market_val_yi": rec.get("total_market_val_yi"),
        }
        # Merge fundamentals
        for k, v in fundamentals.items():
            if k not in ("code", "name") and v is not None:
                data.setdefault(k, v)

        # Load klines
        if with_klines:
            klines = _load_klines_from_cache(code, kline_days)
            data["klines"] = klines
            if klines:
                closes = [float(k["close"]) for k in klines if k.get("close")]
                if closes:
                    data["high_52w"] = max(closes[-252:]) if len(closes) >= 252 else max(closes)
                    data["low_52w"] = min(closes[-252:]) if len(closes) >= 252 else min(closes)

        stock_data[code] = data

    elapsed = time.time() - t0
    print(f"[discovery] 数据集构建完成 ({elapsed:.1f}s, {len(stock_data)} 只)", file=sys.stderr, flush=True)
    return stock_data


# ── Screener selector ─────────────────────────────────────────────

_SCREENER_MAP = {
    "low_position":       "screeners.low_position_screener",
    "turnaround":         "screeners.turnaround_screener",
    "relative_strength":  "screeners.relative_strength_screener",
    "sector_laggard":     "screeners.sector_laggard_screener",
    "value_quality":      "screeners.magic_formula",       # existing
    "growth_momentum":    "screeners.canslim",              # existing
    "volume_anomaly":     "screeners.volume_price",         # existing
    "fscore":             "screeners.fscore",               # existing
}

_SCREENER_LABELS = {
    "low_position":      "低位潜力股",
    "turnaround":        "困境反转股",
    "relative_strength": "相对强度(RS)筛选",
    "sector_laggard":    "板块补涨滞涨股",
    "value_quality":     "Magic Formula 价值质量",
    "growth_momentum":   "CAN SLIM 成长动量",
    "volume_anomaly":    "量价异动",
    "fscore":            "F-Score 价值质量",
}

# Combo mode runs all strategies with different weights
_COMBO_STRATEGIES = [
    ("low_position",      0.30),
    ("turnaround",        0.25),
    ("relative_strength", 0.20),
    ("sector_laggard",    0.15),
    ("value_quality",     0.10),
]


def _get_screener(strategy: str, params: dict):
    """Import and instantiate the screener for a strategy ID."""
    if strategy not in _SCREENER_MAP:
        raise ValueError(f"未知策略: {strategy}")

    module_path = _SCREENER_MAP[strategy]
    from screeners.base import get_screener
    return get_screener(strategy.replace("_", "_"), params)


def _run_strategy(strategy: str, stock_data: dict[str, dict], limit: int, params: dict):
    """Run a single screener and return results."""
    from screeners.base import get_screener
    # Map our IDs to screener_ids
    id_map = {
        "low_position":      "low_position",
        "turnaround":        "turnaround",
        "relative_strength": "relative_strength",
        "sector_laggard":    "sector_laggard",
        "value_quality":     "magic_formula",
        "growth_momentum":   "canslim",
        "volume_anomaly":    "volume_price",
        "fscore":            "fscore",
    }
    screener_id = id_map.get(strategy, strategy)
    screener = get_screener(screener_id, params)
    if screener is None:
        print(f"[discovery] 策略 {strategy} 未找到", file=sys.stderr, flush=True)
        return []
    results = screener.screen(stock_data, market="cn_stock", limit=limit)
    return results


def _discover_offline(strategy: str, universe: str, limit: int, t_start: float) -> dict:
    """Fallback discovery using historical fitness data from SQLite cache.

    Returns stocks ranked by composite backtest score for the closest matching strategy.
    """
    print("[discovery] 使用历史回测缓存模式...", file=sys.stderr, flush=True)
    # Map our strategy IDs to fitness strategy_id patterns
    strategy_map = {
        "low_position":      ["swing", "mean_reversion"],
        "turnaround":        ["turnaround", "value", "fscore"],
        "relative_strength": ["trend", "momentum", "canslim"],
        "sector_laggard":    ["swing", "trend"],
        "value_quality":     ["magic_formula", "value", "fscore"],
        "growth_momentum":   ["canslim", "growth", "momentum"],
        "volume_anomaly":    ["volume", "swing"],
        "fscore":            ["fscore", "value"],
        "combo":             [],  # all strategies
    }
    keywords = strategy_map.get(strategy, [])

    try:
        import sqlite3
        data_dir = os.environ.get(
            "QUANT_DATA_DIR",
            os.path.join(os.path.expanduser("~"), ".openclaw", "quant-data"),
        )
        db_path = os.path.join(data_dir, "quant-trading.db")
        if not os.path.exists(db_path):
            return {
                "ok": False,
                "error": "无法加载行情数据（网络不可用），且本地缓存不存在。请检查网络连接。",
                "universe_count": 0, "screened_count": 0, "results": [],
            }

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # Build WHERE clause to filter by strategy type
        if keywords and strategy != "combo":
            placeholders = " OR ".join([f"LOWER(strategy_id) LIKE ?" for _ in keywords])
            like_vals = [f"%{kw}%" for kw in keywords]
            rows = conn.execute(
                f"""
                SELECT symbol, stock_name, strategy_id, strategy_name,
                       AVG(composite_score) as avg_score,
                       AVG(total_return) as avg_return,
                       AVG(sharpe_ratio) as avg_sharpe,
                       AVG(max_drawdown) as avg_drawdown,
                       AVG(win_rate) as avg_winrate,
                       COUNT(*) as strategy_count
                FROM stock_strategy_fitness
                WHERE ({placeholders})
                GROUP BY symbol
                ORDER BY avg_score DESC
                LIMIT ?
                """,
                [*like_vals, limit],
            ).fetchall()
        else:
            # Combo or unknown: take best stocks across all strategies
            rows = conn.execute(
                """
                SELECT symbol, stock_name,
                       AVG(composite_score) as avg_score,
                       AVG(total_return) as avg_return,
                       AVG(sharpe_ratio) as avg_sharpe,
                       AVG(max_drawdown) as avg_drawdown,
                       AVG(win_rate) as avg_winrate,
                       COUNT(*) as strategy_count
                FROM stock_strategy_fitness
                GROUP BY symbol
                ORDER BY avg_score DESC
                LIMIT ?
                """,
                [limit],
            ).fetchall()

        total = conn.execute("SELECT COUNT(DISTINCT symbol) FROM stock_strategy_fitness").fetchone()[0]
        conn.close()

        results = []
        for i, r in enumerate(rows):
            results.append({
                "rank": i + 1,
                "stock_id": str(r["symbol"]).zfill(6),
                "name": str(r["stock_name"] or ""),
                "score": round(_safe_float(r["avg_score"]), 3),
                "signals": [
                    f"历史回测综合评分: {round(_safe_float(r['avg_score']), 2)}",
                    f"历史年化收益: {round(_safe_float(r['avg_return']), 1)}%",
                    f"Sharpe: {round(_safe_float(r['avg_sharpe']), 2)}",
                    f"最大回撤: {round(_safe_float(r['avg_drawdown']), 1)}%",
                    f"胜率: {round(_safe_float(r['avg_winrate']), 1)}%",
                ],
                "factors": _sanitize({
                    "avg_score": _safe_float(r["avg_score"]),
                    "avg_return_pct": round(_safe_float(r["avg_return"]), 2),
                    "avg_sharpe": _safe_float(r["avg_sharpe"]),
                    "avg_drawdown_pct": round(_safe_float(r["avg_drawdown"]), 2),
                    "avg_winrate_pct": round(_safe_float(r["avg_winrate"]), 1),
                    "strategy_count": _safe_float(r["strategy_count"]),
                }),
            })

        elapsed = time.time() - t_start
        strategy_label = _SCREENER_LABELS.get(strategy, "综合选股" if strategy == "combo" else strategy)
        print(f"[discovery] 离线模式返回 {len(results)} 只 (来自历史回测)",
              file=sys.stderr, flush=True)
        return {
            "ok": True,
            "strategy": strategy,
            "strategy_label": strategy_label,
            "universe": universe,
            "universe_count": total,
            "screened_count": len(results),
            "results": results,
            "elapsed_seconds": round(elapsed, 1),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "is_offline": True,
            "warning": "⚠️ 实时行情不可用，结果来自历史回测评分缓存，不反映当前市场状态",
        }

    except Exception as e:
        print(f"[discovery] 离线回测缓存读取失败: {e}", file=sys.stderr, flush=True)
        return {
            "ok": False,
            "error": f"无法加载行情数据（网络不可用）。建议检查网络连接后重试。",
            "universe_count": 0, "screened_count": 0, "results": [],
        }


def _discover(req: dict) -> dict:
    """Main discovery logic."""
    t_start = time.time()

    strategy = req.get("strategy", "low_position")
    universe = req.get("universe", "csi500")
    custom_symbols = req.get("symbols", [])
    limit = int(req.get("limit", 20))
    with_klines = bool(req.get("with_klines", True))
    kline_days = int(req.get("kline_days", 252))
    params = req.get("params") or {}

    # ── Load universe ──────────────────────────────────────────────
    if custom_symbols:
        # Custom pool: fetch quotes for specific symbols only
        spot_records = _load_universe_from_spot("all")
        code_set = {str(s).zfill(6) for s in custom_symbols}
        spot_records = [r for r in spot_records if r["code"] in code_set]
    else:
        spot_records = _load_universe_from_spot(universe)

    if not spot_records:
        # No data at all — try offline historical fitness fallback
        return _discover_offline(strategy, universe, limit, t_start)

    is_offline = any(r.get("_from_cache") for r in spot_records[:5])

    # If all records are from cache (no real price data), use fitness fallback too
    if is_offline:
        return _discover_offline(strategy, universe, limit, t_start)

    universe_count = len(spot_records)

    # ── Build stock_data dict ──────────────────────────────────────
    stock_data = _build_stock_data(spot_records, with_klines, kline_days)

    # ── Run screener(s) ───────────────────────────────────────────
    if strategy == "combo":
        # Run all strategies, merge with weight
        code_scores: dict[str, dict] = {}

        for strat, weight in _COMBO_STRATEGIES:
            try:
                t1 = time.time()
                results = _run_strategy(strat, stock_data, limit * 2, params)
                n = len(results)
                print(f"[discovery] {strat}: {n} 只 ({time.time()-t1:.1f}s)",
                      file=sys.stderr, flush=True)
                # Merge scores
                for r in results:
                    cid = r.stock_id
                    if cid not in code_scores:
                        code_scores[cid] = {
                            "stock_id": cid,
                            "name": r.name,
                            "combo_score": 0.0,
                            "strategies": [],
                            "all_signals": [],
                            "all_factors": {},
                        }
                    normalized = r.score / max(1.0, results[0].score if results else 1.0)
                    code_scores[cid]["combo_score"] += normalized * weight * 10
                    code_scores[cid]["strategies"].append(strat)
                    code_scores[cid]["all_signals"].extend(
                        [f"[{_SCREENER_LABELS.get(strat, strat)}] {s}" for s in r.signals[:2]]
                    )
                    code_scores[cid]["all_factors"].update(r.factors)
            except Exception as e:
                print(f"[discovery] 策略 {strat} 执行失败: {e}", file=sys.stderr, flush=True)

        # Sort and format
        sorted_items = sorted(code_scores.values(),
                              key=lambda x: x["combo_score"], reverse=True)[:limit]
        final_results = []
        for i, item in enumerate(sorted_items):
            final_results.append({
                "rank": i + 1,
                "stock_id": item["stock_id"],
                "name": item["name"],
                "score": round(item["combo_score"], 3),
                "strategies_hit": item["strategies"],
                "signals": item["all_signals"][:6],
                "factors": _sanitize(item["all_factors"]),
            })
        screened_count = len(final_results)
        strategy_used = "combo"
        strategy_label = "综合选股 (多策略组合)"

    else:
        # Single strategy
        print(f"[discovery] 执行策略: {strategy}...", file=sys.stderr, flush=True)
        try:
            results = _run_strategy(strategy, stock_data, limit, params)
        except Exception as e:
            return {
                "ok": False,
                "error": f"策略执行失败: {e}",
                "universe_count": universe_count,
                "screened_count": 0,
                "results": [],
            }

        final_results = []
        for r in results:
            final_results.append({
                "rank": r.rank,
                "stock_id": r.stock_id,
                "name": r.name,
                "score": round(r.score, 3),
                "signals": r.signals,
                "factors": _sanitize(r.factors),
            })
        screened_count = len(final_results)
        strategy_used = strategy
        strategy_label = _SCREENER_LABELS.get(strategy, strategy)

    elapsed = time.time() - t_start

    return {
        "ok": True,
        "strategy": strategy_used,
        "strategy_label": strategy_label,
        "universe": universe,
        "universe_count": universe_count,
        "screened_count": screened_count,
        "results": final_results,
        "elapsed_seconds": round(elapsed, 1),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "is_offline": is_offline,
        "warning": "数据来自本地缓存（无实时行情），价格信号可能不准确" if is_offline else None,
    }


def _list_strategies() -> dict:
    return {
        "ok": True,
        "strategies": [
            {
                "id": sid,
                "name": label,
                "description": _get_description(sid),
                "requires_klines": sid in ("low_position", "relative_strength",
                                            "sector_laggard", "volume_anomaly"),
            }
            for sid, label in _SCREENER_LABELS.items()
        ] + [{
            "id": "combo",
            "name": "综合选股",
            "description": "同时运行所有策略，综合打分后排名，发现多维度重叠的高质量品种",
            "requires_klines": True,
        }],
    }


def _get_description(sid: str) -> str:
    descs = {
        "low_position":      "52周历史低位（打65折以下）且基本面未恶化，下行空间有限，上行弹性大",
        "turnaround":        "净利润扭亏或高速反弹（≥20%），毛利率改善，营收止跌，困境反转高赔率",
        "relative_strength": "欧奈尔RS评级≥75，12个月涨幅位于前25%，强者恒强，找市场真正龙头",
        "sector_laggard":    "板块整体上涨8%+但个股涨幅明显落后，基本面优质，预期补涨到位",
        "value_quality":     "Greenblatt魔法公式：高ROIC+低EV/EBIT双因子，便宜的好公司",
        "growth_momentum":   "欧奈尔CAN SLIM: 盈利增长≥25%+营收增长+相对强度，成长动量选股",
        "volume_anomaly":    "量价异动：机构建仓(温和放量上涨)、底部放量、突破信号",
        "fscore":            "Piotroski F-Score 9分制：盈利+杠杆+效率，高分≥7为优质价值股",
    }
    return descs.get(sid, "")


# ── Main entry ────────────────────────────────────────────────────

def main():
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        req = json.loads(config_str)
    else:
        req = json.loads(sys.stdin.read())

    action = req.get("action", "discover")

    if action == "list_strategies":
        result = _list_strategies()
    elif action == "discover":
        result = _discover(req)
    else:
        result = {"ok": False, "error": f"未知 action: {action}"}

    print(json.dumps(_sanitize(result), ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
