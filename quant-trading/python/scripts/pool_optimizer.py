#!/usr/bin/env python3
"""
Stock Pool Optimizer — Automatically tests multiple stock pool + strategy + risk
combinations via multi-round backtesting to find the optimal stock pool.

Supports two modes:
  1. Preset pools: tests curated pools (bluechip, tech, mixed, etc.)
  2. Auto-screen: fetches ~2000+ A-shares from Sina Finance, groups them by
     market-cap / PE / momentum / sector, creates dynamic pools, then backtests.

Input (stdin JSON):
  {
    "action": "optimize",
    "capital": 500000,
    "start_date": "2025-06-01",
    "end_date": "",
    "max_rounds": 12,
    "auto_screen": true       // true = scan market first; false = preset pools only
  }

Output: JSON with all rounds, best combination, and recommended pool.
"""

import json
import math
import os
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

# ── Proxy bypass ──
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"
urllib.request.getproxies = lambda: {}  # type: ignore[assignment]
try:
    import requests, requests.utils  # noqa: E401
    requests.utils.getproxies = lambda: {}  # type: ignore[assignment]
except ImportError:
    pass

import importlib.util
import sqlite3

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_DIR = os.path.dirname(_SCRIPT_DIR)

# Ensure parent python/ dir is on sys.path so we can import cache modules
if _PYTHON_DIR not in sys.path:
    sys.path.insert(0, _PYTHON_DIR)


def _load_backtest_engine():
    spec = importlib.util.spec_from_file_location(
        "backtest_engine", os.path.join(_SCRIPT_DIR, "backtest_engine.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default


# ═══════════════════════════════════════════════════════════════════
# Cache-aware full-market data fetching
# ═══════════════════════════════════════════════════════════════════

# -- Cache layer: persist parsed optimizer data to SQLite so repeated
#    runs within the TTL window skip the network entirely. -----------

def _optimizer_cache_db_path() -> str:
    home = os.path.expanduser("~")
    cache_dir = os.path.join(home, ".openclaw", "extensions", "quant-trading", "cache")
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, "stock_data.db")


def _optimizer_cache_get() -> list[dict] | None:
    """Return cached parsed stocks if fresh (≤10 min during trading, ≤18h otherwise)."""
    try:
        db_path = _optimizer_cache_db_path()
        if not os.path.exists(db_path):
            return None
        conn = sqlite3.connect(db_path, timeout=5)
        row = conn.execute(
            "SELECT value, updated_at FROM cache_meta WHERE key = 'optimizer_stocks'"
        ).fetchone()
        conn.close()
        if not row:
            return None
        updated_str, updated_at = row
        from datetime import timezone, timedelta as _td
        updated_dt = datetime.fromisoformat(updated_at)
        age_s = (datetime.utcnow() - updated_dt).total_seconds()
        # During trading hours: 10 min; otherwise: 18 hours
        now_cst = datetime.utcnow() + _td(hours=8)
        is_trading = (now_cst.weekday() < 5
                      and 915 <= now_cst.hour * 100 + now_cst.minute <= 1505)
        ttl = 600 if is_trading else 18 * 3600
        if age_s > ttl:
            return None
        data = json.loads(updated_str)
        if isinstance(data, list) and len(data) >= 30:
            print(f"[pool_optimizer] 命中缓存: {len(data)} 只股票 (age {age_s:.0f}s)", file=sys.stderr)
            return data
        return None
    except Exception as exc:
        print(f"[pool_optimizer] 缓存读取失败: {exc}", file=sys.stderr)
        return None


def _optimizer_cache_save(parsed: list[dict]) -> None:
    """Save parsed optimizer stocks to cache."""
    try:
        db_path = _optimizer_cache_db_path()
        conn = sqlite3.connect(db_path, timeout=5)
        conn.execute("CREATE TABLE IF NOT EXISTS cache_meta (key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
        now = datetime.utcnow().isoformat()
        conn.execute(
            "INSERT OR REPLACE INTO cache_meta (key, value, updated_at) VALUES (?, ?, ?)",
            ("optimizer_stocks", json.dumps(parsed, ensure_ascii=False), now),
        )
        conn.commit()
        conn.close()
        print(f"[pool_optimizer] 已缓存 {len(parsed)} 只股票到 SQLite", file=sys.stderr)
    except Exception as exc:
        print(f"[pool_optimizer] 缓存写入失败: {exc}", file=sys.stderr)


# -- Sina direct HTTP fetching (fallback) --------------------------

def _fetch_sina_page(page: int, num: int = 80, sort: str = "changepercent",
                     asc: int = 0) -> list[dict]:
    """Fetch one page of A-share stocks from Sina Finance."""
    url = (
        f"https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"Market_Center.getHQNodeData?page={page}&num={num}&sort={sort}"
        f"&asc={asc}&node=hs_a&symbol=&_s_r_a=sort"
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn",
    })
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            fixed = re.sub(r'(\{|,)\s*([a-zA-Z_]\w*)\s*:', r'\1"\2":', raw)
            return json.loads(fixed)
    except Exception as exc:
        print(f"[pool_optimizer] Sina page {page} fetch failed: {exc}", file=sys.stderr)
        return []


def _fetch_all_sina(max_pages: int = 30, per_page: int = 80) -> tuple[list[dict], int]:
    """Fetch many pages from Sina (default ~2400 stocks).
    Sort by market cap descending to get the most liquid/important first.
    Uses concurrent fetching for speed.
    Returns (stocks, failed_pages).
    """
    results: dict[int, list[dict]] = {}
    failed = 0
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_fetch_sina_page, page, per_page, "mktcap", 0): page
            for page in range(1, max_pages + 1)
        }
        for fut in as_completed(futures):
            page = futures[fut]
            try:
                stocks = fut.result()
            except Exception:
                stocks = []
            if stocks:
                results[page] = stocks
            else:
                failed += 1
    if failed >= max_pages:
        return [], failed
    # Merge in page order
    all_stocks: list[dict] = []
    for page in sorted(results.keys()):
        all_stocks.extend(results[page])
    return all_stocks, failed


def _parse_sina_stocks(raw: list[dict]) -> list[dict]:
    """Parse raw Sina stock data into clean dicts, filtering junk."""
    parsed = []
    seen = set()
    for s in raw:
        sym = str(s.get("symbol", ""))
        code = sym[2:] if len(sym) > 2 and sym[:2] in ("sh", "sz") else sym
        if not code or code in seen:
            continue
        name = str(s.get("name", ""))
        # Skip ST, delisted, Beijing exchange, new-third-board
        if "ST" in name or "退" in name:
            continue
        if code.startswith("8") or code.startswith("4"):
            continue

        price = _safe_float(s.get("trade"))
        if price <= 0:
            continue

        pe = _safe_float(s.get("per"))
        pb = _safe_float(s.get("pb"))
        mkt_cap = _safe_float(s.get("mktcap")) * 1e4  # Sina returns 万元
        change_pct = _safe_float(s.get("changepercent"))
        turnover = _safe_float(s.get("turnoverratio"))
        volume = _safe_float(s.get("volume"))
        amount = _safe_float(s.get("amount"))

        # Min liquidity: at least 1亿 market cap, some volume
        if mkt_cap < 1e9 or volume <= 0:
            continue

        seen.add(code)
        parsed.append({
            "code": code, "name": name, "price": price,
            "pe": pe, "pb": pb, "mktCap": mkt_cap,
            "changePct": change_pct, "turnover": turnover,
            "volume": volume, "amount": amount,
        })
    return parsed


def _build_auto_pools(stocks: list[dict]) -> dict[str, dict]:
    """Build dynamic stock pools from full-market data.

    Strategy:
    1. Score each stock on multiple dimensions (market cap, PE, momentum, liquidity)
    2. Build category pools: mega-cap, large-cap, growth, value, momentum, mixed-best
    3. Each pool has 10-15 stocks — manageable for backtesting
    """
    # Sort by market cap
    by_cap = sorted(stocks, key=lambda x: x["mktCap"], reverse=True)

    # ── Mega-cap blue chips (top 30 by market cap, PE < 40) ──
    mega_candidates = [s for s in by_cap[:60] if 0 < s["pe"] < 40 and s["turnover"] > 0.1]
    mega_pool = [s["code"] for s in mega_candidates[:12]]

    # ── Large-cap value (PE < 20, market cap > 200亿, PB < 3) ──
    value_candidates = sorted(
        [s for s in stocks if 0 < s["pe"] < 20 and s["mktCap"] > 200e8 and 0 < s["pb"] < 3],
        key=lambda x: x["pe"]
    )
    value_pool = [s["code"] for s in value_candidates[:12]]

    # ── Growth (PE 20-60, high turnover, positive change) ──
    growth_candidates = sorted(
        [s for s in stocks if 20 <= s["pe"] < 60 and s["turnover"] > 1.5
         and s["mktCap"] > 100e8 and s["changePct"] > 0],
        key=lambda x: x["turnover"], reverse=True
    )
    growth_pool = [s["code"] for s in growth_candidates[:12]]

    # ── Momentum (today's top gainers with good liquidity) ──
    momentum_candidates = sorted(
        [s for s in stocks if s["changePct"] > 1 and s["mktCap"] > 100e8
         and s["amount"] > 3e8 and s["turnover"] > 1],
        key=lambda x: x["changePct"], reverse=True
    )
    momentum_pool = [s["code"] for s in momentum_candidates[:12]]

    # ── Tech/创业板/科创板 (code starts with 300/688) ──
    tech_candidates = sorted(
        [s for s in stocks if (s["code"].startswith("300") or s["code"].startswith("688"))
         and s["mktCap"] > 200e8 and s["turnover"] > 0.5 and s["amount"] > 1e8],
        key=lambda x: x["mktCap"], reverse=True
    )
    tech_pool = [s["code"] for s in tech_candidates[:12]]

    # ── Dip-buy (today's dippers with big cap — contrarian) ──
    dip_candidates = sorted(
        [s for s in stocks if s["changePct"] < -1 and s["mktCap"] > 300e8
         and s["amount"] > 2e8 and 0 < s["pe"] < 40],
        key=lambda x: x["changePct"]
    )
    dip_pool = [s["code"] for s in dip_candidates[:12]]

    # ── Mixed-best (top scorers across dimensions) ──
    for s in stocks:
        cap_score = min(s["mktCap"] / 1e10, 10)  # Up to 10 for 1000亿+
        pe_score = max(0, 10 - s["pe"] / 5) if 0 < s["pe"] < 50 else 0
        liq_score = min(s["turnover"], 5)
        mom_score = max(0, min(s["changePct"] * 2, 10))
        s["_optScore"] = cap_score + pe_score + liq_score + mom_score

    best_mixed = sorted(stocks, key=lambda x: x.get("_optScore", 0), reverse=True)
    mixed_pool = [s["code"] for s in best_mixed[:15]]

    # ── Blue+Tech hybrid (top 6 mega + top 6 tech) ──
    hybrid_pool = list(dict.fromkeys(
        [s["code"] for s in mega_candidates[:6]] +
        [s["code"] for s in tech_candidates[:8]]
    ))[:14]

    pools = {}
    _add = lambda key, label, desc, syms: pools.update(
        {key: {"label": label, "desc": desc, "symbols": syms, "auto": True}}
    ) if len(syms) >= 5 else None

    _add("auto_mega",     "全市场·大盘龙头",
         f"市值前60ɸѡPE<40，共{len(mega_pool)}ֻ", mega_pool)
    _add("auto_value",    "全市场·低估价值",
         f"PE<20+市值>200亿+PB<3，共{len(value_pool)}ֻ", value_pool)
    _add("auto_growth",   "全市场·活跃成长",
         f"PE 20-60+高换手+上涨，共{len(growth_pool)}ֻ", growth_pool)
    _add("auto_momentum", "全市场·强势动量",
         f"今日涨幅>1%+大成交额，共{len(momentum_pool)}ֻ", momentum_pool)
    _add("auto_tech",     "全市场·科创精选",
         f"创业板/科创板+市值>200亿，共{len(tech_pool)}ֻ", tech_pool)
    _add("auto_dip",      "全市场·回调低吸",
         f"今日下跌+大盘低PE，共{len(dip_pool)}ֻ", dip_pool)
    _add("auto_mixed",    "全市场·综合最优",
         f"多维评分Top15，共{len(mixed_pool)}ֻ", mixed_pool)
    _add("auto_hybrid",   "全市场·蓝筹科技混合",
         f"大盘龙头+科创龙头，共{len(hybrid_pool)}ֻ", hybrid_pool)

    return pools


# ── Candidate stock pools ──────────────────────────────────────────
POOLS = {
    "bluechip": {
        "label": "蓝筹价值",
        "desc": "大盘蓝筹，消费+金融+家电",
        "symbols": [
            "600519", "000858", "601318", "000333", "600036",
            "000651", "600900", "601166", "600276", "600887",
        ],
    },
    "tech": {
        "label": "科技成长",
        "desc": "半导体+新能源+AI+消费电子",
        "symbols": [
            "300750", "002594", "002475", "300124", "688981",
            "300059", "002230", "601012", "002415", "600570",
        ],
    },
    "mixed": {
        "label": "蓝筹+科技混合",
        "desc": "4蓝筹龙头+8科技成长",
        "symbols": [
            "600519", "000858", "601318", "000333",
            "300750", "002594", "002475", "300124",
            "688981", "300059", "002230", "601012",
        ],
    },
    "wide": {
        "label": "宽基覆盖",
        "desc": "消费+金融+科技+新能源+ҽҩ",
        "symbols": [
            "600519", "000858", "601318", "000333", "300750",
            "002594", "688981", "002230", "600276", "000651",
            "300059", "601012", "600036", "002475", "300124",
        ],
    },
    "new_energy": {
        "label": "新能源赛道",
        "desc": "光伏+电池+新能车+风电",
        "symbols": [
            "300750", "002594", "601012", "300274", "002709",
            "600438", "688599", "300763", "300014", "002129",
        ],
    },
    "consumer_plus": {
        "label": "大消费+",
        "desc": "白酒+家电+ʳƷ+ҽҩ+消费电子",
        "symbols": [
            "600519", "000858", "000333", "000651", "600887",
            "603288", "300059", "002475", "601888", "000568",
        ],
    },
}

STRATEGIES = ["momentum", "value", "growth", "swing", "mixed"]
STRATEGY_LABELS = {
    "momentum": "趋势动量", "value": "价值投资", "growth": "成长投资",
    "swing": "波段交易", "mixed": "混合策略",
}

RISK_LEVELS = ["low", "medium", "high", "aggressive"]
RISK_LABELS = {"low": "稳健", "medium": "均衡", "high": "进取", "aggressive": "激进"}

REBALANCE_OPTIONS = [5, 10, 14, 20]


def _run_single(bt_engine: Any, symbols: list[str], strategy: str,
                risk: str, rebalance: int,
                capital: float, start: str, end: str) -> dict:
    """Run one backtest and return a summary row."""
    try:
        result = bt_engine.run_backtest(
            initial_capital=capital,
            start_date=start,
            end_date=end,
            strategy=strategy,
            risk_tolerance=risk,
            symbols=symbols,
            rebalance_days=rebalance,
        )
        if "error" in result:
            return {"error": result["error"]}
        s = result.get("summary", {})
        return {
            "totalReturn": s.get("totalReturn", 0),
            "annualizedReturn": s.get("annualizedReturn", 0),
            "maxDrawdown": s.get("maxDrawdown", 0),
            "sharpeRatio": s.get("sharpeRatio", 0),
            "winRate": s.get("winRate", 0),
            "profitFactor": s.get("profitFactor", 0),
            "totalTrades": s.get("totalTrades", 0),
            "finalPositionCount": len(result.get("finalPositions", [])),
            "finalPositionSymbols": [p["symbol"] for p in result.get("finalPositions", [])],
        }
    except Exception as e:
        return {"error": str(e)}


def _composite_score(row: dict) -> float:
    """Compute a composite score for ranking results.
    Heavily weights Sharpe ratio for risk-adjusted returns, penalizes drawdown.
    """
    if "error" in row:
        return -9999
    ret = row.get("totalReturn", 0)
    sharpe = row.get("sharpeRatio", 0)
    dd = abs(row.get("maxDrawdown", 0))
    pf = row.get("profitFactor", 0)

    # Weighted composite: Sharpe is king, then return, penalize drawdown
    score = (sharpe * 40) + (ret * 1.0) + (pf * 5) - (dd * 2)
    return round(score, 2)


def optimize(
    capital: float = 500000,
    start_date: str = "2025-06-01",
    end_date: str | None = None,
    max_rounds: int = 12,
    auto_screen: bool = False,
) -> dict:
    """Run multiple rounds of backtesting across pools, strategies, risk levels,
    and rebalance periods. Return ranked results with the best combination.

    If auto_screen=True, first scans ~2000 A-shares from Sina Finance to build
    dynamic pools (full-market screening), then backtests those pools alongside
    preset ones.
    """
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    bt_engine = _load_backtest_engine()

    # ── Build the pool universe ──
    active_pools = dict(POOLS)  # start with presets
    screening_summary = None

    if auto_screen:
        # Try cache first → Sina API fallback → save to cache
        parsed = _optimizer_cache_get()
        cache_hit = parsed is not None
        failed_pages = 0

        if parsed is None:
            print("[pool_optimizer] 缓存未命中，从 Sina Finance API 获取…", file=sys.stderr)
            raw, failed_pages = _fetch_all_sina(max_pages=30, per_page=80)
            parsed = _parse_sina_stocks(raw)
            if parsed:
                _optimizer_cache_save(parsed)
        else:
            raw = []  # not needed when cache hit

        screening_summary = {
            "totalFetched": len(raw) if not cache_hit else len(parsed),
            "afterFilter": len(parsed),
            "failedPages": failed_pages,
            "source": "缓存" if cache_hit else "Sina Finance API",
        }
        if not parsed and failed_pages > 0:
            screening_summary["warning"] = f"Sina Finance API 请求全部失败（{failed_pages}页），请检查网络连接"
        if len(parsed) >= 30:
            auto_pools = _build_auto_pools(parsed)
            active_pools.update(auto_pools)
            screening_summary["autoPoolsCreated"] = len(auto_pools)
            screening_summary["autoPoolNames"] = [
                {"key": k, "label": v["label"], "count": len(v["symbols"])}
                for k, v in auto_pools.items()
            ]
        else:
            screening_summary["warning"] = f"仅获取{len(parsed)}只，不足以构建动态池，使用预设池"

    # ── Define test rounds ──
    pool_keys = list(active_pools.keys())

    if auto_screen and len(active_pools) > len(POOLS):
        # Auto mode: test all auto pools with mixed+momentum strategies, high risk
        test_configs = []
        auto_keys = [k for k in pool_keys if k.startswith("auto_")]
        preset_keys = [k for k in pool_keys if not k.startswith("auto_")]

        # Phase 1: All auto pools with mixed strategy
        for pk in auto_keys:
            test_configs.append({"pool": pk, "strategy": "mixed", "risk": "high", "rebalance": 5})

        # Phase 2: Top auto pools with momentum/swing
        for pk in auto_keys[:4]:
            test_configs.append({"pool": pk, "strategy": "momentum", "risk": "high", "rebalance": 5})

        # Phase 3: Include a few presets for comparison
        for pk in ["mixed", "tech", "bluechip"]:
            if pk in preset_keys:
                test_configs.append({"pool": pk, "strategy": "mixed", "risk": "high", "rebalance": 5})

        # Phase 4: Risk/rebalance variants on top candidates
        for pk in auto_keys[:2]:
            test_configs.append({"pool": pk, "strategy": "mixed", "risk": "medium", "rebalance": 10})
    else:
        # Preset mode (original logic)
        test_configs = [
            {"pool": "bluechip",     "strategy": "mixed",    "risk": "high", "rebalance": 5},
            {"pool": "tech",         "strategy": "mixed",    "risk": "high", "rebalance": 5},
            {"pool": "mixed",        "strategy": "mixed",    "risk": "high", "rebalance": 5},
            {"pool": "wide",         "strategy": "mixed",    "risk": "high", "rebalance": 5},
            {"pool": "new_energy",   "strategy": "mixed",    "risk": "high", "rebalance": 5},
            {"pool": "consumer_plus","strategy": "mixed",    "risk": "high", "rebalance": 5},
            {"pool": "mixed",        "strategy": "momentum", "risk": "high", "rebalance": 5},
            {"pool": "mixed",        "strategy": "swing",    "risk": "high", "rebalance": 5},
            {"pool": "tech",         "strategy": "momentum", "risk": "high", "rebalance": 5},
            {"pool": "tech",         "strategy": "swing",    "risk": "high", "rebalance": 5},
            {"pool": "mixed",        "strategy": "mixed",    "risk": "medium", "rebalance": 5},
            {"pool": "mixed",        "strategy": "mixed",    "risk": "high", "rebalance": 10},
            {"pool": "mixed",        "strategy": "momentum", "risk": "high", "rebalance": 10},
            {"pool": "tech",         "strategy": "mixed",    "risk": "high", "rebalance": 10},
            {"pool": "bluechip",     "strategy": "value",    "risk": "low",  "rebalance": 14},
            {"pool": "consumer_plus","strategy": "value",    "risk": "medium","rebalance": 14},
        ]

    # Filter out configs referencing pools that don't exist
    test_configs = [c for c in test_configs if c["pool"] in active_pools]
    test_configs = test_configs[:max_rounds]

    rounds = []
    for i, cfg in enumerate(test_configs):
        pool_key = cfg["pool"]
        pool_info = active_pools[pool_key]

        row = _run_single(
            bt_engine, pool_info["symbols"], cfg["strategy"],
            cfg["risk"], cfg["rebalance"],
            capital, start_date, end_date,
        )

        entry = {
            "round": i + 1,
            "poolKey": pool_key,
            "poolLabel": pool_info["label"],
            "poolDesc": pool_info["desc"],
            "symbols": pool_info["symbols"],
            "symbolCount": len(pool_info["symbols"]),
            "strategy": cfg["strategy"],
            "strategyLabel": STRATEGY_LABELS.get(cfg["strategy"], cfg["strategy"]),
            "riskLevel": cfg["risk"],
            "riskLabel": RISK_LABELS.get(cfg["risk"], cfg["risk"]),
            "rebalanceDays": cfg["rebalance"],
            **row,
        }
        entry["compositeScore"] = _composite_score(row)
        rounds.append(entry)

    # Sort by composite score descending
    rounds.sort(key=lambda x: x["compositeScore"], reverse=True)

    # Tag ranks
    for i, r in enumerate(rounds):
        r["rank"] = i + 1

    best = rounds[0] if rounds else None

    # Build recommendation text
    if best and "error" not in best:
        rec_text = (
            f"推荐方案：{best['poolLabel']}（{best['symbolCount']}只股票）"
            f" + {best['strategyLabel']} + {best['riskLabel']}风险"
            f" + {best['rebalanceDays']}天调仓\n"
            f"回测收益：{best['totalReturn']:.2f}%，"
            f"夏普比率：{best['sharpeRatio']:.2f}，"
            f"最大回撤：{abs(best['maxDrawdown']):.2f}%，"
            f"胜率：{best['winRate']:.1f}%"
        )
    else:
        rec_text = "所有测试轮次均失败，请检查数据源和参数"

    return {
        "rounds": rounds,
        "totalRounds": len(rounds),
        "best": best,
        "recommendText": rec_text,
        "pools": {k: {"label": v["label"], "desc": v["desc"],
                       "symbols": v["symbols"], "count": len(v["symbols"]),
                       "auto": v.get("auto", False)}
                  for k, v in active_pools.items()},
        "period": {"startDate": start_date, "endDate": end_date},
        "capital": capital,
        "autoScreen": auto_screen,
        "screeningSummary": screening_summary,
    }


if __name__ == "__main__":
    _raw = os.environ.get("QUANT_CONFIG") or sys.stdin.read()
    config = json.loads(_raw)
    try:
        result = optimize(
            capital=config.get("capital", 500000),
            start_date=config.get("start_date", "2025-06-01"),
            end_date=config.get("end_date"),
            max_rounds=config.get("max_rounds", 12),
            auto_screen=config.get("auto_screen", False),
        )
    except Exception as e:
        import traceback
        result = {"error": str(e), "traceback": traceback.format_exc()}

    print(json.dumps(result, ensure_ascii=False))
