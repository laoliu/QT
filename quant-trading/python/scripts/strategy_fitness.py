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
import tempfile
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
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

_LOGS_DIR = os.path.join(_PYTHON_ROOT, "logs")
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
    data_dir = os.environ.get("QUANT_DATA_DIR", "")
    if not data_dir:
        home = os.path.expanduser("~")
        data_dir = os.path.join(home, ".openclaw", "quant-data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, "quant-trading.db")


# ── Fetch stock universe ─────────────────────────────────────────

def _fetch_universe(universe: str, custom_symbols: list[str] | None = None) -> list[str]:
    """获取指定范围的股票代码列表。"""
    if universe == "custom" and custom_symbols:
        return list(custom_symbols)

    # 全部A股
    if universe == "all_a":
        return _fetch_all_a_stocks()

    # 港股宇宙
    if universe in ("hsi", "hscei", "hstech", "all_hk"):
        return _fetch_hk_universe(universe)

    # 尝试 akshare 获取指数成分股
    index_map = {
        "csi300": "000300",
        "csi500": "000905",
        "csi1000": "000852",
    }
    index_code = index_map.get(universe, "000852")

    symbols: list[str] = []

    # 方法1: akshare (with retry)
    try:
        import akshare as ak
        for _attempt in range(3):
            try:
                df = ak.index_stock_cons(symbol=index_code)
                if df is not None and not df.empty:
                    col = "品种代码" if "品种代码" in df.columns else df.columns[0]
                    symbols = df[col].astype(str).str.strip().tolist()
                    # 规范化为6位
                    symbols = [s.zfill(6) for s in symbols if s.strip()]
                    print(f"[Universe] akshare获取 {universe} 成分股: {len(symbols)} 只", file=sys.stderr)
                    break
            except Exception:
                if _attempt < 2:
                    time.sleep(2)
                    continue
                raise
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


def _fetch_hk_universe(universe: str) -> list[str]:
    """获取港股宇宙 (恒生指数/国企指数/恒生科技/全部港股)。"""
    symbols: list[str] = []

    if universe == "all_hk":
        # 全部港股
        try:
            import akshare as ak
            df = ak.stock_hk_spot_em()
            if df is not None and not df.empty:
                col = "代码" if "代码" in df.columns else df.columns[0]
                symbols = df[col].astype(str).str.strip().tolist()
                symbols = [s.zfill(5) for s in symbols if s.strip()]
                print(f"[Universe] 全部港股: {len(symbols)} 只", file=sys.stderr)
        except Exception as e:
            print(f"[Universe] 全部港股获取失败: {e}", file=sys.stderr)
        return symbols

    # 港股指数成分股
    hk_index_map = {
        "hsi": "恒生指数",
        "hscei": "国企指数",
        "hstech": "恒生科技指数",
    }
    index_name = hk_index_map.get(universe, "恒生指数")

    try:
        import akshare as ak
        # 方法1: akshare 港股指数成分
        df = ak.index_stock_cons_csindex(symbol="HSI") if universe == "hsi" else None
        # 通用方法: 通过东财获取港股实时行情，再按市值筛选
        if df is None or df.empty:
            df = ak.stock_hk_spot_em()
            if df is not None and not df.empty:
                # 按总市值排序取头部
                if "总市值" in df.columns:
                    df = df.sort_values("总市值", ascending=False)
                col = "代码" if "代码" in df.columns else df.columns[0]
                all_codes = df[col].astype(str).str.strip().tolist()
                all_codes = [s.zfill(5) for s in all_codes if s.strip()]
                # 按宇宙大小截取
                size_map = {"hsi": 80, "hscei": 50, "hstech": 30}
                limit = size_map.get(universe, 80)
                symbols = all_codes[:limit]
                print(f"[Universe] {index_name} (市值Top{limit}): {len(symbols)} 只", file=sys.stderr)
            else:
                print(f"[Universe] {index_name} 东财数据为空", file=sys.stderr)
        else:
            col = "品种代码" if "品种代码" in df.columns else df.columns[0]
            symbols = df[col].astype(str).str.strip().tolist()
            symbols = [s.zfill(5) for s in symbols if s.strip()]
            print(f"[Universe] {index_name}: {len(symbols)} 只", file=sys.stderr)
    except Exception as e:
        print(f"[Universe] {index_name} 获取失败: {e}", file=sys.stderr)

    # Fallback: 常用港股蓝筹列表
    if not symbols:
        symbols = [
            "00700", "09988", "09618", "03690", "01810", "00005", "01299",
            "02318", "00941", "00388", "02020", "00027", "01211", "00175",
            "02382", "00992", "00981", "09999", "03968", "01024", "00669",
            "02269", "00241", "00883", "01038", "02628", "00016", "00006",
            "01109", "02313", "01177", "00002", "00003", "00011", "00012",
            "00017", "00066", "00101", "00267", "00288", "00386", "00688",
            "00762", "00823", "00857", "00939", "00960", "01044", "01093",
            "01113", "01398", "01928", "01997", "02007", "02018", "02196",
            "02319", "02331", "02388", "02601", "02688", "06098", "06862",
            "09626", "09698", "09888", "09961", "09999",
        ]
        print(f"[Universe] {index_name} 使用fallback蓝筹列表: {len(symbols)} 只", file=sys.stderr)

    return symbols


def _fetch_all_a_stocks() -> list[str]:
    """获取全部A股代码列表。"""
    symbols: list[str] = []

    # 方法1: akshare (with retry)
    try:
        import akshare as ak
        for _attempt in range(3):
            try:
                df = ak.stock_zh_a_spot_em()
                if df is not None and not df.empty:
                    col = "代码" if "代码" in df.columns else df.columns[0]
                    symbols = df[col].astype(str).str.strip().tolist()
                    symbols = [s.zfill(6) for s in symbols if s.strip()]
                    print(f"[Universe] akshare全部A股: {len(symbols)} 只", file=sys.stderr)
                    break
            except Exception:
                if _attempt < 2:
                    time.sleep(2)
                    continue
                raise
    except Exception as e:
        print(f"[Universe] akshare全A获取失败: {e}", file=sys.stderr)

    # 方法2: stock_cache fallback
    if not symbols:
        try:
            from cache.stock_cache import get_cache
            cache = get_cache()
            all_stocks = cache.get_stock_list()
            if all_stocks:
                symbols = [s["code"] for s in all_stocks if s.get("code")]
                print(f"[Universe] stock_cache全A: {len(symbols)} 只", file=sys.stderr)
        except Exception as e2:
            print(f"[Universe] stock_cache全A失败: {e2}", file=sys.stderr)

    # 方法3: tushare fallback
    if not symbols:
        try:
            from aipha.data.tushare_equity_provider import TuShareEquityProvider
            provider = TuShareEquityProvider()
            df = provider.load_stock_basic()
            if df is not None and not df.empty:
                col = "ts_code" if "ts_code" in df.columns else df.columns[0]
                raw = df[col].astype(str).tolist()
                symbols = [s.split(".")[0].zfill(6) for s in raw if s.strip()]
                print(f"[Universe] tushare全A: {len(symbols)} 只", file=sys.stderr)
        except Exception as e3:
            print(f"[Universe] tushare全A失败: {e3}", file=sys.stderr)

    # 方法4: 降级到指数成分股合集 (CSI1000+500+300)
    if not symbols:
        try:
            import akshare as ak
            combined: set[str] = set()
            for idx_code in ("000852", "000905", "000300"):
                try:
                    df = ak.index_stock_cons(symbol=idx_code)
                    if df is not None and not df.empty:
                        col = "品种代码" if "品种代码" in df.columns else df.columns[0]
                        combined.update(df[col].astype(str).str.strip().tolist())
                except Exception:
                    pass
            if combined:
                symbols = [s.zfill(6) for s in combined if s.strip()]
                print(f"[Universe] 降级使用指数成分股合集: {len(symbols)} 只", file=sys.stderr)
        except Exception as e4:
            print(f"[Universe] 指数合集fallback失败: {e4}", file=sys.stderr)

    # 过滤: 只保留主板+创业板+科创板, 去除ST标记
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


_STRATEGY_DISPLAY_NAMES: dict[str, str] = {
    "a_stock_event_pead": "A股事件PEAD",
    "behavioral_sleeve": "行为金融子组合",
    "csi500_quality_sleeve": "中证500质量子组合",
    "overreaction": "过度反应因子",
    "pair_trading_industry_neutral": "行业中性配对",
    "quality_lowvol": "质量低波",
    "three_sleeve_equity": "三子组合权益",
    "microcap_ml_csi2000": "微盘ML-CSI2000",
}


def _list_available_strategies() -> dict:
    """返回所有可用的AIPHA A股策略ID和名称（排除可转债策略）。"""
    try:
        import aipha.strategy.register_all  # noqa: F401
        from aipha.strategy.registry import StrategyRegistry
        reg = StrategyRegistry()
        a_stock_ids = _get_astock_strategies()
        strategies = []
        for sid in a_stock_ids:
            cls = reg.get(sid)
            raw_name = getattr(cls, "name", sid) if cls else sid
            # Use display name override, or fall back to sid if name is generic
            if raw_name in ("BaseStrategy", "BaseTickStrategy"):
                label = _STRATEGY_DISPLAY_NAMES.get(sid, sid)
            else:
                label = _STRATEGY_DISPLAY_NAMES.get(sid, raw_name)
            strategies.append({"id": sid, "name": label})
        return {"strategies": strategies, "count": len(strategies)}
    except Exception as e:
        return {"strategies": [], "count": 0, "error": str(e)}


# ── Load kline data ──────────────────────────────────────────────

def _load_klines(symbols: list[str], start_date: str, end_date: str) -> dict[str, list[dict]]:
    """批量加载K线数据。"""
    try:
        from data_providers.akshare_provider import get_klines_batch
        return get_klines_batch(symbols, start_date, end_date, market="cn_stock", kline_days=0)
    except Exception as e:
        print(f"[Klines] 加载失败: {e}", file=sys.stderr)
        return {}


def _save_klines_temp(klines: dict[str, list[dict]]) -> str:
    """Save klines to a NDJSON temp file for TS to read line-by-line.

    Returns the temp file path, or "" on failure.
    Format: one line per symbol, each line is: symbol\tJSON_array_of_bars
    This avoids Node.js string length limits that occur with a single giant JSON.
    """
    if not klines:
        return ""
    try:
        fd, fpath = tempfile.mkstemp(prefix="fitness_klines_", suffix=".ndjson")
        count = 0
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            for sym, bars in klines.items():
                minimal = [
                    {
                        "t": b.get("timestamp", 0),
                        "o": b.get("open", 0),
                        "h": b.get("high", 0),
                        "l": b.get("low", 0),
                        "c": b.get("close", 0),
                        "v": b.get("volume", 0),
                    }
                    for b in bars
                    if b.get("close", 0) > 0
                ]
                if minimal:
                    f.write(sym)
                    f.write("\t")
                    f.write(json.dumps(minimal, separators=(",", ":")))
                    f.write("\n")
                    count += 1
        size_mb = os.path.getsize(fpath) / (1024 * 1024)
        print(f"[Klines] 保存 {count} 只股票K线到临时文件 ({size_mb:.1f}MB)", file=sys.stderr)
        return fpath
    except Exception as e:
        print(f"[Klines] 保存临时文件失败: {e}", file=sys.stderr)
        return ""


# ── Single stock-strategy backtest ───────────────────────────────

# ── Load fundamental metadata ────────────────────────────────────

def _load_fundamentals(symbols: list[str]) -> dict[str, dict]:
    """批量加载基本面数据 (PE/PB/ROE/DV_ratio 等) 用于策略 metadata。

    主要数据源: 腾讯财经 API (免费、无认证、支持批量)。
    返回 {symbol: {pe_ttm, pb, roe, dv_ratio, total_mv, turnover_rate, ...}}
    """
    result = _fetch_tencent_fundamentals(symbols)

    # 对仍然缺失的，设置空
    for sym in symbols:
        if sym not in result:
            result[sym] = {}

    filled = sum(1 for v in result.values() if v.get("pe_ttm") is not None)
    print(f"[Fundamentals] 总获取: {filled}/{len(symbols)}", file=sys.stderr)
    return result


def _fetch_tencent_fundamentals(codes: list[str]) -> dict[str, dict]:
    """通过腾讯财经批量 API 拉取 PE/PB/turnover 等数据。

    优点: 免费、无 proxy 问题 (HTTP)、支持批量 (~50 per request)。
    """
    import urllib.request as _urlreq

    result: dict[str, dict] = {}

    # 构建腾讯代码映射
    tencent_codes = []
    code_map: dict[str, str] = {}
    for c in codes:
        prefix = "sh" if c.startswith("6") else "sz"
        tc = f"{prefix}{c}"
        tencent_codes.append(tc)
        code_map[tc] = c

    batch_size = 50
    for i in range(0, len(tencent_codes), batch_size):
        batch = tencent_codes[i:i + batch_size]
        query = ",".join(batch)
        url = f"http://qt.gtimg.cn/q={query}"
        try:
            resp = _urlreq.urlopen(url, timeout=15)
            data = resp.read().decode("gbk")
            for line in data.strip().split("\n"):
                line = line.strip()
                if not line or "~" not in line:
                    continue
                eq_pos = line.find("=")
                if eq_pos < 0:
                    continue
                var_name = line[:eq_pos].strip()
                # Extract tencent code from var name: v_sh600519="..."
                tc_key = var_name.split("_")[-1] if "_" in var_name else ""
                code = code_map.get(tc_key, "")
                if not code:
                    continue

                parts = line[eq_pos + 1:].strip('" ;\r').split("~")
                if len(parts) < 47:
                    continue

                try:
                    price_str = parts[3]
                    price = float(price_str) if price_str else None
                    pe = float(parts[39]) if parts[39] else None
                    pb = float(parts[46]) if len(parts) > 46 and parts[46] else None
                    turnover = float(parts[38]) if parts[38] else None
                    name = parts[1] if len(parts) > 1 else ""

                    # 计算 ROE ≈ PB / PE (杜邦恒等式)
                    roe = None
                    if pe and pb and pe > 0:
                        roe = round(pb / pe, 4)

                    # 估算 dv_ratio (无直接数据):
                    # 低 PE + 高 ROE 公司通常有较高分红
                    dv_ratio = 0.01  # 保守默认 1%
                    if pe and roe:
                        if pe < 15 and roe > 0.08:
                            dv_ratio = 0.03  # 大概 3%
                        elif pe < 25 and roe > 0.05:
                            dv_ratio = 0.015  # 大概 1.5%

                    # 估算增长率和财务比率(无直接数据):
                    # 用 ROE 和 PE 做粗略估计
                    dt_netprofit_yoy = 0.05  # 默认 5% 增长
                    revenue_yoy = 0.05  # 默认 5%
                    debt_to_assets = 0.30  # 默认 30%
                    gross_margin = 0.35  # 默认 35%
                    fcfy = 0.0  # 默认 0
                    if pe and roe:
                        # 高 ROE + 低 PE 通常意味着更高增长
                        if roe > 0.15 and pe < 20:
                            dt_netprofit_yoy = 0.15
                            revenue_yoy = 0.10
                            gross_margin = 0.45
                            fcfy = 0.04
                        elif roe > 0.10 and pe < 30:
                            dt_netprofit_yoy = 0.10
                            revenue_yoy = 0.08
                            gross_margin = 0.40
                            fcfy = 0.03

                    result[code] = {
                        "pe_ttm": pe,
                        "pb": pb,
                        "roe": roe,
                        "dv_ratio": dv_ratio,
                        "dt_netprofit_yoy": dt_netprofit_yoy,
                        "revenue_yoy": revenue_yoy,
                        "debt_to_assets": debt_to_assets,
                        "gross_margin": gross_margin,
                        "fcfy": fcfy,
                        "turnover_rate": turnover,
                        "total_mv": None,  # 腾讯的市值字段单位不确定，暂不使用
                        "name": name,
                    }
                except (ValueError, IndexError):
                    pass
        except Exception as e:
            print(f"  [Tencent] 批次 {i // batch_size + 1} 失败: {e}", file=sys.stderr)

        if (i // batch_size + 1) % 5 == 0 or i + batch_size >= len(tencent_codes):
            print(f"  [Tencent] 进度: {min(i + batch_size, len(codes))}/{len(codes)}", file=sys.stderr)

    return result


# ── Build bars_by_date with metadata ─────────────────────────────

def _build_bars_by_date(
    all_klines: dict[str, list[dict]],
    fundamentals: dict[str, dict],
) -> dict[date, dict[str, Any]]:
    """将原始 kline dicts + 基本面数据 → {date: {symbol: Bar}} 带 metadata。"""
    from aipha.domain.models import Bar

    bars_by_date: dict[date, dict[str, Bar]] = {}

    for symbol, klines in all_klines.items():
        meta = fundamentals.get(symbol, {})
        # 过滤 None 值
        metadata = {k: v for k, v in meta.items() if v is not None} if meta else {}

        for bar_dict in klines:
            ts = bar_dict.get("timestamp", 0)
            if isinstance(ts, (int, float)) and ts > 1e12:
                dt_obj = datetime.fromtimestamp(ts / 1000)
            elif isinstance(ts, (int, float)):
                dt_obj = datetime.fromtimestamp(ts)
            elif isinstance(ts, str):
                dt_obj = datetime.strptime(ts[:10], "%Y-%m-%d")
            else:
                continue

            d = dt_obj.date()
            bar = Bar(
                symbol=symbol,
                start_ts=dt_obj,
                end_ts=dt_obj.replace(hour=15, minute=0, second=0),
                open=_sf(bar_dict.get("open")),
                high=_sf(bar_dict.get("high")),
                low=_sf(bar_dict.get("low")),
                close=_sf(bar_dict.get("close")),
                volume=_sf(bar_dict.get("volume")),
                metadata=metadata if metadata else None,
            )
            if d not in bars_by_date:
                bars_by_date[d] = {}
            bars_by_date[d][symbol] = bar

    return bars_by_date


# ── Run one strategy on the full universe ────────────────────────

def _run_strategy_backtest(
    strategy_id: str,
    bars_by_date: dict[date, dict[str, Any]],
    initial_capital: float,
) -> dict[str, dict] | None:
    """运行单个策略在全部股票上的回测，返回 {symbol: metrics_dict}。"""
    try:
        import aipha.strategy.register_all  # noqa: F401
        from aipha.strategy.registry import StrategyRegistry
        from aipha.backtest.engine import BacktestEngine, BacktestConfig
        from aipha.domain.models import Side

        reg = StrategyRegistry()
        strategy = reg.create(strategy_id)

        config = BacktestConfig(initial_capital=initial_capital)
        engine = BacktestEngine(config=config, strategy=strategy)
        result = engine.run(bars_by_date)

        if not result.trades:
            return None

        # 提取每只股票的交易表现
        # 按 symbol 汇总: 买入成本、卖出收益、持仓市值
        stock_trades: dict[str, list] = {}
        for t in result.trades:
            if t.symbol not in stock_trades:
                stock_trades[t.symbol] = []
            stock_trades[t.symbol].append(t)

        # 获取最后一天价格用于计算未平仓市值
        sorted_dates = sorted(bars_by_date.keys())
        last_bars = bars_by_date[sorted_dates[-1]] if sorted_dates else {}

        per_stock: dict[str, dict] = {}
        for sym, trades in stock_trades.items():
            buy_cost = 0.0
            buy_qty = 0
            sell_proceeds = 0.0
            sell_qty = 0
            holding_days = set()

            for t in trades:
                if t.side == Side.BUY:
                    buy_cost += t.notional + t.fees.total
                    buy_qty += t.qty
                else:
                    sell_proceeds += t.notional - t.fees.total
                    sell_qty += t.qty
                holding_days.add(t.trade_date)

            # 未平仓部分按最后价格计算
            remaining_qty = buy_qty - sell_qty
            remaining_value = 0.0
            if remaining_qty > 0 and sym in last_bars:
                remaining_value = remaining_qty * last_bars[sym].close

            total_pnl = sell_proceeds + remaining_value - buy_cost
            total_return_dec = (total_pnl / buy_cost) if buy_cost > 0 else 0

            total_trades_count = len(trades)

            # 年化收益 (decimal: 0.10 = 10%)
            trading_days = len(sorted_dates)
            ann_return = 0.0
            if buy_cost > 0 and trading_days > 0:
                total_r = (sell_proceeds + remaining_value) / buy_cost
                years = trading_days / 245
                ann_return = (total_r ** (1 / years)) - 1 if years > 0 and total_r > 0 else 0

            # ── 计算 per-stock 每日净值、最大回撤、夏普 ──
            # 以买入成本为基准，构建组合价值曲线（而非 P&L 曲线）
            daily_equity = []
            pos_qty = 0
            cash_for_sym = buy_cost  # 从投入资金开始，而非 0
            trade_idx = 0
            sym_trades_sorted = sorted(trades, key=lambda t: t.trade_date)

            for d in sorted_dates:
                # 处理该日的所有交易
                while trade_idx < len(sym_trades_sorted) and sym_trades_sorted[trade_idx].trade_date <= d:
                    t = sym_trades_sorted[trade_idx]
                    if t.side == Side.BUY:
                        cash_for_sym -= (t.notional + t.fees.total)
                        pos_qty += t.qty
                    else:
                        cash_for_sym += (t.notional - t.fees.total)
                        pos_qty -= t.qty
                    trade_idx += 1

                # 只在有持仓或有过交易后才记录净值
                if pos_qty > 0 or cash_for_sym != buy_cost or trade_idx > 0:
                    price = bars_by_date[d][sym].close if d in bars_by_date and sym in bars_by_date[d] else 0
                    equity = cash_for_sym + pos_qty * price
                    daily_equity.append(equity)

            # 最大回撤 (基于净值序列)
            max_dd = 0.0
            if len(daily_equity) >= 2:
                peak = daily_equity[0]
                for eq in daily_equity[1:]:
                    if eq > peak:
                        peak = eq
                    if peak > 0:
                        dd = (peak - eq) / peak
                        if dd > max_dd:
                            max_dd = dd
                    elif buy_cost > 0:
                        # fallback: 用初始投入作为基准计算回撤
                        dd = (buy_cost - eq) / buy_cost
                        if dd > max_dd:
                            max_dd = dd

            # 夏普比率 (基于日收益率)
            sharpe = 0.0
            daily_returns = []
            if len(daily_equity) >= 2:
                for i in range(1, len(daily_equity)):
                    prev = daily_equity[i - 1]
                    curr = daily_equity[i]
                    if prev > 0:
                        daily_returns.append(curr / prev - 1)
                    elif buy_cost > 0:
                        # 权益接近 0 时用初始投入归一化，避免负分母反转
                        daily_returns.append((curr - prev) / buy_cost)
                if daily_returns:
                    avg_ret = sum(daily_returns) / len(daily_returns)
                    n = len(daily_returns)
                    std_ret = (sum((r - avg_ret) ** 2 for r in daily_returns) / max(1, n - 1)) ** 0.5
                    if std_ret > 0:
                        sharpe = (avg_ret - 0.03 / 252) / std_ret * (252 ** 0.5)

            # Sortino (只用负收益计算下行波动率)
            sortino = 0.0
            if daily_returns:
                neg_returns = [r for r in daily_returns if r < 0]
                if neg_returns:
                    avg_ret = sum(daily_returns) / len(daily_returns)
                    down_std = (sum(r ** 2 for r in neg_returns) / len(neg_returns)) ** 0.5
                    if down_std > 0:
                        sortino = (avg_ret - 0.03 / 252) / down_std * (252 ** 0.5)

            # Calmar
            calmar = ann_return / max_dd if max_dd > 0 else 0.0

            # Win rate & Profit/loss ratio: 基于买卖配对计算实际盈亏
            buy_trades = sorted([t for t in trades if t.side == Side.BUY], key=lambda t: t.trade_date)
            sell_trades = sorted([t for t in trades if t.side != Side.BUY], key=lambda t: t.trade_date)
            round_trips = min(len(buy_trades), len(sell_trades))
            wins_pnl = []
            losses_pnl = []
            for i in range(round_trips):
                bt = buy_trades[i]
                st = sell_trades[i]
                pnl = (st.notional - st.fees.total) - (bt.notional + bt.fees.total)
                if pnl > 0:
                    wins_pnl.append(pnl)
                else:
                    losses_pnl.append(pnl)

            # 如果有未平仓且浮盈，将浮盈计为一笔赢利
            remaining_qty = buy_qty - sell_qty
            if remaining_qty > 0 and remaining_value > 0:
                # 未平仓部分的买入成本（取最后几笔买入）
                unmatched_buys = buy_trades[round_trips:]
                unmatched_cost = sum(b.notional + b.fees.total for b in unmatched_buys) if unmatched_buys else 0
                if unmatched_cost > 0:
                    unrealized_pnl = remaining_value - unmatched_cost
                    if unrealized_pnl > 0:
                        wins_pnl.append(unrealized_pnl)
                    else:
                        losses_pnl.append(unrealized_pnl)

            total_round_trips = round_trips + (1 if remaining_qty > 0 and remaining_value > 0 and (buy_qty - sell_qty) > 0 else 0)
            if total_round_trips > 0:
                win_rate = len(wins_pnl) / total_round_trips
            else:
                win_rate = 1.0 if total_pnl > 0 else 0.0

            if wins_pnl or losses_pnl:
                avg_win = sum(wins_pnl) / len(wins_pnl) if wins_pnl else 0
                avg_loss = abs(sum(losses_pnl) / len(losses_pnl)) if losses_pnl else 0
                profit_loss_ratio = avg_win / avg_loss if avg_loss > 0 else (avg_win if avg_win > 0 else 0)
            else:
                profit_loss_ratio = max(total_return_dec * 10, 0) if total_return_dec > 0 else 0

            cs = composite_score({
                "total_return": total_return_dec * 100,  # 转为百分比传入评分函数
                "annualized_return": ann_return * 100,
                "sharpe_ratio": sharpe,
                "win_rate": win_rate * 100,  # 转为百分比，与 TS builtin-engine 一致
                "profit_loss_ratio": profit_loss_ratio,
                "max_drawdown": max_dd * 100,
                "total_trades": total_trades_count,
            })

            per_stock[sym] = {
                "strategy_id": strategy_id,
                "symbol": sym,
                "composite_score": cs,
                "total_return": round(total_return_dec, 6),
                "annualized_return": round(ann_return, 6),
                "sharpe_ratio": round(sharpe, 4),
                "sortino_ratio": round(sortino, 4),
                "max_drawdown": round(max_dd, 6),
                "win_rate": round(win_rate, 4),
                "profit_loss_ratio": round(profit_loss_ratio, 4),
                "calmar_ratio": round(calmar, 4),
                "total_trades": total_trades_count,
                "backtest_start": str(result.start_date) if result.start_date else "",
                "backtest_end": str(result.end_date) if result.end_date else "",
            }

        return per_stock
    except Exception as e:
        print(f"[Strategy] {strategy_id} 回测失败: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return None


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
    """查询全局策略排名，并聚合每个策略的平均指标。"""
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
            """
            SELECT
                g.*,
                AVG(s.composite_score)    AS composite_score,
                AVG(s.annualized_return)  AS annualized_return,
                AVG(s.sharpe_ratio)       AS sharpe_ratio,
                AVG(s.max_drawdown)       AS max_drawdown,
                AVG(s.win_rate)           AS win_rate,
                AVG(s.total_return)       AS total_return,
                AVG(s.sortino_ratio)      AS sortino_ratio,
                AVG(s.calmar_ratio)       AS calmar_ratio
            FROM global_strategy_ranking g
            LEFT JOIN stock_strategy_fitness s
              ON g.strategy_id = s.strategy_id AND g.batch_id = s.batch_id
            WHERE g.batch_id = ?
            GROUP BY g.strategy_id
            ORDER BY g.rank ASC
            """,
            (batch_id,),
        ).fetchall()
        return {
            "batch_id": batch_id,
            "strategies": [dict(r) for r in rows],
        }
    finally:
        conn.close()


def _list_batches(limit: int = 20) -> dict:
    """列出历史分析批次。"""
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return {"batches": []}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT batch_id, MIN(created_at) as created_at,
                   COUNT(DISTINCT symbol) as stock_count,
                   COUNT(DISTINCT strategy_id) as strategy_count
            FROM stock_strategy_fitness
            GROUP BY batch_id
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return {
            "batches": [
                {
                    "batchId": r["batch_id"],
                    "createdAt": r["created_at"],
                    "stockCount": r["stock_count"],
                    "strategyCount": r["strategy_count"],
                }
                for r in rows
            ]
        }
    finally:
        conn.close()


def _query_all_stocks(batch_id: str = "", limit: int = 100, offset: int = 0) -> dict:
    """查询所有股票的 rank=1 最优策略，分页返回。"""
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return {"stocks": [], "total": 0}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if not batch_id:
            row = conn.execute(
                "SELECT batch_id FROM stock_strategy_fitness ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            batch_id = row["batch_id"] if row else ""
        if not batch_id:
            return {"stocks": [], "total": 0}

        count_row = conn.execute(
            "SELECT COUNT(DISTINCT symbol) as cnt FROM stock_strategy_fitness WHERE batch_id = ? AND rank = 1",
            (batch_id,),
        ).fetchone()
        total = count_row["cnt"] if count_row else 0

        rows = conn.execute(
            "SELECT * FROM stock_strategy_fitness WHERE batch_id = ? AND rank = 1 ORDER BY composite_score DESC LIMIT ? OFFSET ?",
            (batch_id, limit, offset),
        ).fetchall()

        # Resolve missing stock names
        name_map: dict[str, str] = {}
        missing = [r["symbol"] for r in rows if not r["stock_name"]]
        if missing:
            try:
                from cache.stock_cache import get_cache
                cache = get_cache()
                for sym in missing:
                    n = cache.get_stock_name(sym)
                    if n:
                        name_map[sym] = n
            except Exception:
                pass

        stocks = []
        for r in rows:
            d = dict(r)
            d["stockName"] = d.get("stock_name") or name_map.get(d["symbol"]) or d["symbol"]
            d["strategyId"] = d.get("strategy_id", "")
            d["compositeScore"] = d.get("composite_score", 0)
            d["totalReturn"] = d.get("total_return", 0)
            d["annualizedReturn"] = d.get("annualized_return", 0)
            d["sharpeRatio"] = d.get("sharpe_ratio")
            d["maxDrawdown"] = d.get("max_drawdown", 0)
            d["winRate"] = d.get("win_rate", 0)
            d["totalTrades"] = d.get("total_trades", 0)
            stocks.append(d)

        return {"stocks": stocks, "total": total, "batch_id": batch_id, "limit": limit, "offset": offset}
    finally:
        conn.close()


def _delete_batch(batch_id: str) -> dict:
    """删除指定批次的所有数据。"""
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return {"deleted": 0}
    conn = sqlite3.connect(db_path)
    try:
        c1 = conn.execute("DELETE FROM stock_strategy_fitness WHERE batch_id = ?", (batch_id,))
        c2 = conn.execute("DELETE FROM global_strategy_ranking WHERE batch_id = ?", (batch_id,))
        conn.commit()
        total = (c1.rowcount or 0) + (c2.rowcount or 0)
        print(f"[DB] 已删除批次 {batch_id}: {total} 条记录", file=sys.stderr)
        return {"deleted": total, "batch_id": batch_id}
    finally:
        conn.close()


def _aggregate_global(top_n: int = 10) -> dict:
    """跨所有批次聚合全局策略排名。

    计算逻辑：取所有批次的 stock_strategy_fitness 数据，
    按 strategy_id 聚合 composite_score 的总和、平均值等。
    """
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return {"strategies": [], "batch_count": 0}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # 获取批次总数
        batch_count_row = conn.execute(
            "SELECT COUNT(DISTINCT batch_id) as cnt FROM stock_strategy_fitness"
        ).fetchone()
        batch_count = batch_count_row["cnt"] if batch_count_row else 0

        # 聚合：每个策略在所有批次中的表现
        rows = conn.execute(
            """
            SELECT strategy_id,
                   SUM(composite_score) as total_score,
                   COUNT(*) as appearance_count,
                   COUNT(DISTINCT symbol) as stock_count,
                   COUNT(DISTINCT batch_id) as batch_count,
                   AVG(composite_score) as avg_score,
                   MAX(composite_score) as max_score,
                   MIN(composite_score) as min_score
            FROM stock_strategy_fitness
            GROUP BY strategy_id
            ORDER BY total_score DESC
            LIMIT ?
            """,
            (top_n,),
        ).fetchall()

        strategies = []
        for i, r in enumerate(rows):
            strategies.append({
                "strategy_id": r["strategy_id"],
                "rank": i + 1,
                "total_score": round(r["total_score"], 4) if r["total_score"] else 0,
                "appearance_count": r["appearance_count"],
                "stock_count": r["stock_count"],
                "batch_count": r["batch_count"],
                "avg_score": round(r["avg_score"], 4) if r["avg_score"] else 0,
                "max_score": round(r["max_score"], 4) if r["max_score"] else 0,
                "min_score": round(r["min_score"], 4) if r["min_score"] else 0,
            })

        return {
            "strategies": strategies,
            "total_batch_count": batch_count,
        }
    finally:
        conn.close()


def _batch_detail(batch_id: str) -> dict:
    """返回指定批次的股票列表和策略列表。"""
    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return {"stocks": [], "strategies": []}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        stock_rows = conn.execute(
            """
            SELECT symbol, stock_name, COUNT(*) as strategy_count,
                   MAX(composite_score) as best_score
            FROM stock_strategy_fitness
            WHERE batch_id = ?
            GROUP BY symbol
            ORDER BY best_score DESC
            """,
            (batch_id,),
        ).fetchall()
        strat_rows = conn.execute(
            """
            SELECT strategy_id, strategy_name, COUNT(*) as stock_count,
                   AVG(composite_score) as avg_score
            FROM stock_strategy_fitness
            WHERE batch_id = ?
            GROUP BY strategy_id
            ORDER BY avg_score DESC
            """,
            (batch_id,),
        ).fetchall()

        # Resolve missing stock names via stock_cache
        name_map: dict[str, str] = {}
        missing = [r["symbol"] for r in stock_rows if not r["stock_name"]]
        if missing:
            try:
                from cache.stock_cache import get_cache
                cache = get_cache()
                for sym in missing:
                    n = cache.get_stock_name(sym)
                    if n:
                        name_map[sym] = n
            except Exception:
                pass

        return {
            "batch_id": batch_id,
            "stocks": [
                {
                    "symbol": r["symbol"],
                    "stockName": r["stock_name"] or name_map.get(r["symbol"]) or r["symbol"],
                    "strategyCount": r["strategy_count"],
                    "bestScore": round(r["best_score"], 4) if r["best_score"] else 0,
                }
                for r in stock_rows
            ],
            "strategies": [
                {
                    "strategyId": r["strategy_id"],
                    "strategyName": r["strategy_name"] or r["strategy_id"],
                    "stockCount": r["stock_count"],
                    "avgScore": round(r["avg_score"], 4) if r["avg_score"] else 0,
                }
                for r in strat_rows
            ],
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
    include_alpha: bool = True,
    has_builtin: bool = False,
) -> dict:
    """执行策略适配分析主流程。

    新架构: 每个策略用全部股票一起跑（轮动策略需要多股票做排名筛选），
    然后从交易记录中提取每只股票的表现。
    """
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
    strategy_ids: list[str] = []
    if include_alpha:
        print("[Step 2] 获取A股策略列表...", file=sys.stderr)
        strategy_ids = _get_astock_strategies()
        print(f"[Step 2] 共 {len(strategy_ids)} 个Alpha策略", file=sys.stderr)
    else:
        print("[Step 2] 跳过Alpha策略 (include_alpha=False)", file=sys.stderr)

    if not strategy_ids and not has_builtin:
        return {"error": "无可用策略", "batch_id": batch_id}

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
        loaded = min(i + batch_size, len(symbols))
        print(f"  K线加载进度: {loaded}/{len(symbols)}", file=sys.stderr)
        _write_progress({
            "batch_id": batch_id,
            "status": "loading_klines",
            "phase": f"加载K线数据 {loaded}/{len(symbols)}",
            "total_stocks": len(symbols),
            "completed_stocks": 0,
            "klines_loaded": loaded,
            "elapsed_seconds": int(time.time() - t0),
        })

    # 过滤掉无数据的股票
    valid_symbols = [s for s in symbols if s in all_klines and len(all_klines[s]) >= 60]
    print(f"[Step 4] 有效股票: {len(valid_symbols)}/{len(symbols)}", file=sys.stderr)

    if not valid_symbols:
        return {"error": "所有股票K线数据不足", "batch_id": batch_id}

    # Step 5: 加载基本面数据 (PE/PB/ROE 等)
    print(f"[Step 5] 加载基本面数据...", file=sys.stderr)
    _write_progress({
        "batch_id": batch_id,
        "status": "loading_fundamentals",
        "phase": "加载基本面数据...",
        "total_stocks": len(valid_symbols),
        "completed_stocks": 0,
        "elapsed_seconds": int(time.time() - t0),
    })
    fundamentals = _load_fundamentals(valid_symbols)
    fund_count = sum(1 for v in fundamentals.values() if v.get("pe_ttm") is not None)
    print(f"[Step 5] 基本面: {fund_count}/{len(valid_symbols)} 股票有数据", file=sys.stderr)

    # Step 6: 构建 bars_by_date (所有股票 + metadata)
    print(f"[Step 6] 构建全量 bars_by_date...", file=sys.stderr)
    _write_progress({
        "batch_id": batch_id,
        "status": "building_bars",
        "phase": "构建回测数据...",
        "total_stocks": len(valid_symbols),
        "completed_stocks": 0,
        "elapsed_seconds": int(time.time() - t0),
    })
    # 只保留有效股票的klines
    filtered_klines = {s: all_klines[s] for s in valid_symbols}
    bars_by_date = _build_bars_by_date(filtered_klines, fundamentals)
    print(f"[Step 6] bars_by_date: {len(bars_by_date)} 交易日, "
          f"每日平均 {sum(len(v) for v in bars_by_date.values()) / len(bars_by_date):.0f} 只股票",
          file=sys.stderr)

    # Step 7: 逐策略回测 (每个策略用全量股票)
    # {symbol: [metrics_dict, ...]} — 每只股票关联多个策略结果
    all_results: dict[str, list[dict]] = {}
    completed_strategies = 0

    if strategy_ids:
        print(f"[Step 7] 开始策略回测 ({len(strategy_ids)} 个策略)...", file=sys.stderr)
        _write_progress({
            "batch_id": batch_id,
            "status": "running",
            "phase": "Alpha策略回测中...",
            "total_stocks": len(valid_symbols),
            "total_strategies": len(strategy_ids),
            "completed_stocks": 0,
            "completed_strategies": 0,
            "elapsed_seconds": int(time.time() - t0),
        })

    for sid in strategy_ids:
        completed_strategies += 1
        print(f"  [{completed_strategies}/{len(strategy_ids)}] 运行策略: {sid} ...", file=sys.stderr)
        try:
            per_stock = _run_strategy_backtest(sid, bars_by_date, initial_capital)
            if per_stock:
                for sym, metrics in per_stock.items():
                    if sym not in all_results:
                        all_results[sym] = []
                    if math.isfinite(metrics.get("composite_score", 0)):
                        all_results[sym].append(metrics)
                print(f"    → {sid}: {len(per_stock)} 只股票有交易", file=sys.stderr)
            else:
                print(f"    → {sid}: 无交易", file=sys.stderr)
        except Exception as e:
            print(f"    → {sid}: 错误 {e}", file=sys.stderr)

        # 更新进度
        elapsed = time.time() - t0
        rate = completed_strategies / elapsed if elapsed > 0 else 0
        remaining = (len(strategy_ids) - completed_strategies) / rate if rate > 0 else None
        _write_progress({
            "batch_id": batch_id,
            "status": "running",
            "phase": f"策略回测: {completed_strategies}/{len(strategy_ids)}",
            "total_stocks": len(valid_symbols),
            "total_strategies": len(strategy_ids),
            "completed_stocks": len(all_results),
            "completed_strategies": completed_strategies,
            "current_strategy": sid,
            "elapsed_seconds": int(elapsed),
            "estimated_remaining": int(remaining) if remaining else None,
        })

    if strategy_ids:
        print(f"[Step 7] Alpha回测完成: {len(all_results)} 只股票有结果", file=sys.stderr)
    else:
        print("[Step 7] 无Alpha策略，跳过", file=sys.stderr)

    # 如果有 builtin 策略待 TS 侧执行，先返回中间结果 (不做 Step 8-10)
    if has_builtin and strategy_ids:
        # 返回 alpha 原始结果 + 元数据，由 TS 侧合并后统一排名和保存
        alpha_raw: list[dict] = []
        for sym, metrics_list in all_results.items():
            for m in metrics_list:
                alpha_raw.append(m)
        elapsed = time.time() - t0
        _write_progress({
            "batch_id": batch_id,
            "status": "alpha_done",
            "phase": "Alpha策略完成，等待Builtin策略...",
            "total_stocks": len(valid_symbols),
            "total_strategies": len(strategy_ids),
            "completed_strategies": len(strategy_ids),
            "elapsed_seconds": int(elapsed),
        })
        # Save klines to temp file so TS can reuse without re-fetching from bridge
        klines_file = _save_klines_temp(filtered_klines)
        if klines_file:
            print(f"[Step 7] K线数据已保存到临时文件: {klines_file}", file=sys.stderr)
        return {
            "batch_id": batch_id,
            "universe": universe,
            "total_stocks": len(valid_symbols),
            "total_strategies": len(strategy_ids),
            "elapsed_seconds": int(elapsed),
            "valid_symbols": valid_symbols,
            "start_date": start_date,
            "end_date": end_date,
            "alpha_results": alpha_raw,
            "klines_file": klines_file,
            "_phase": "alpha_only",
        }

    if has_builtin and not strategy_ids:
        # 只跑 builtin，返回元数据让 TS 侧处理
        elapsed = time.time() - t0
        # Save klines to temp file so TS can reuse without re-fetching from bridge
        klines_file = _save_klines_temp(filtered_klines)
        if klines_file:
            print(f"[Step 7] K线数据已保存到临时文件: {klines_file}", file=sys.stderr)
        return {
            "batch_id": batch_id,
            "universe": universe,
            "total_stocks": len(valid_symbols),
            "total_strategies": 0,
            "elapsed_seconds": int(elapsed),
            "valid_symbols": valid_symbols,
            "start_date": start_date,
            "end_date": end_date,
            "alpha_results": [],
            "klines_file": klines_file,
            "_phase": "metadata_only",
        }

    # Step 8: 每只股票取 Top N
    print(f"[Step 8] 计算每只股票 Top {top_n_per_stock} 策略...", file=sys.stderr)
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
                "stock_name": fundamentals.get(sym, {}).get("name", ""),
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

    print(f"[Step 8] 共 {len(stock_fitness)} 条 stock-fitness 记录", file=sys.stderr)

    # Step 9: 全局策略排名
    print(f"[Step 9] 计算全局 Top {top_n_global} 策略...", file=sys.stderr)
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

    print(f"[Step 9] 全局策略排名:", file=sys.stderr)
    for item in global_ranking:
        print(f"  #{item['rank']}: {item['strategy_id']} "
              f"(total_score={item['total_score']:.2f}, "
              f"stocks={item['stock_count']}, "
              f"avg={item['avg_score']:.2f})", file=sys.stderr)

    # Step 10: 保存到数据库
    print("[Step 10] 保存结果到数据库...", file=sys.stderr)
    _save_to_db(batch_id, stock_fitness, global_ranking)

    elapsed = time.time() - t0
    _write_progress({
        "batch_id": batch_id,
        "status": "done",
        "total_stocks": len(valid_symbols),
        "completed_stocks": len(all_results),
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
                include_alpha=config.get("include_alpha", True),
                has_builtin=config.get("has_builtin", False),
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
        elif action == "list_batches":
            result = _list_batches(config.get("limit", 20))
        elif action == "query_all_stocks":
            result = _query_all_stocks(
                config.get("batch_id", ""),
                config.get("limit", 100),
                config.get("offset", 0),
            )
        elif action == "batch_detail":
            bid = config.get("batch_id", "")
            if not bid:
                result = {"error": "需要 batch_id 参数"}
            else:
                result = _batch_detail(bid)
        elif action == "delete_batch":
            bid = config.get("batch_id", "")
            if not bid:
                result = {"error": "需要 batch_id 参数"}
            else:
                result = _delete_batch(bid)
        elif action == "aggregate_global":
            result = _aggregate_global(config.get("top_n", 10))
        elif action == "list_strategies":
            result = _list_available_strategies()
        else:
            result = {"error": f"未知操作: {action}"}
    except Exception as e:
        result = {"error": str(e), "traceback": traceback.format_exc()}

    print(json.dumps(result, ensure_ascii=False, default=str))
