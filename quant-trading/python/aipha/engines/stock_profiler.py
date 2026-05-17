"""
Stock Profiler — extract feature profiles from cached price + fundamental data.

Produces a StockProfile dict per symbol with classified features:
  - volatility_class: low / medium / high
  - trend_regime: uptrend / downtrend / sideways
  - market_cap_class: micro / small / mid / large
  - liquidity_class: low / medium / high
  - sector: industry string
  - momentum: positive / neutral / negative
  - volume_trend: shrinking / normal / expanding

All data comes from the local SQLite cache — no network required.

Entry via stdin JSON:
  {"action": "profile", "symbols": ["000001","300750"], "endDate": "2026-03-20"}
  {"action": "profile_all", "minBars": 200, "endDate": "2026-03-20"}
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys
from typing import Any

_this_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_this_dir)
if _python_root not in sys.path:
    sys.path.insert(0, _python_root)

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

CACHE_DB = os.path.expanduser(
    "~/.openclaw/extensions/quant-trading/cache/stock_data.db"
)

# ── Feature Classification Thresholds ─────────────────────────────

# Annualized volatility (daily vol * sqrt(244))
VOL_LOW = 0.25       # < 25% annualized
VOL_HIGH = 0.50      # > 50% annualized

# Market cap in yuan (total_market_val field)
MCAP_MICRO = 5e9     # < 5B
MCAP_SMALL = 20e9    # < 20B
MCAP_MID = 100e9     # < 100B
# >= 100B is large

# Average daily turnover rate
LIQ_LOW = 1.0        # < 1%
LIQ_HIGH = 5.0       # > 5%

# Momentum (20-day return %)
MOM_POS = 3.0        # > 3%
MOM_NEG = -3.0       # < -3%

# ── Data Access ───────────────────────────────────────────────────


def _get_klines(symbol: str, start_date: str, end_date: str) -> list[dict]:
    """Fetch daily klines from SQLite cache."""
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT trade_date, open_price, high, low, close_price, volume, amount "
            "FROM kline_data WHERE symbol = ? AND period = 'daily' "
            "AND trade_date >= ? AND trade_date <= ? ORDER BY trade_date",
            (symbol, start_date, end_date),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _get_fundamentals(symbol: str) -> dict | None:
    """Fetch latest fundamentals from SQLite cache, with on-demand fallback."""
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM stock_fundamentals WHERE code = ? "
            "ORDER BY fetch_date DESC LIMIT 1",
            (symbol,),
        ).fetchone()
        conn.close()
        if not row:
            # On-demand: fetch from Xueqiu and cache
            fetched = _fetch_fundamentals_on_demand(symbol)
            if fetched:
                return fetched
            return None
        d = dict(row)
        # Merge data_json if present — it often has richer data than columns
        raw_json = d.pop("data_json", None)
        if raw_json:
            try:
                extra = json.loads(raw_json)
                for k, v in extra.items():
                    if v is not None and (k not in d or d[k] is None or d[k] == 0):
                        d[k] = v
            except (json.JSONDecodeError, TypeError):
                pass
        return d
    except Exception:
        return None


def _fetch_fundamentals_on_demand(symbol: str) -> dict | None:
    """Fetch fundamentals with multi-source fallback: 东财个股 → 东财快照 → 雪球."""
    sources = [
        ("东财个股", _fetch_via_em_individual),
        ("东财快照", _fetch_via_em_spot),
        ("雪球", _fetch_via_xueqiu),
    ]
    for name, fetcher in sources:
        try:
            result = fetcher(symbol)
            if result and (result.get("total_market_val") or result.get("total_market_val_yi")):
                # Cache it for future use
                from cache.stock_cache import get_cache
                cache = get_cache()
                cache.save_fundamentals({symbol: result})
                print(f"[profiler] 按需获取 {symbol} 基本面 (来源: {name}) 并缓存", file=sys.stderr)
                return result
        except Exception as e:
            print(f"[profiler] {name} 获取 {symbol} 失败: {e}", file=sys.stderr)
            continue
    return None


def _fetch_via_em_individual(symbol: str) -> dict | None:
    """东方财富个股信息 stock_individual_info_em — 稳定，轻量."""
    import akshare as ak

    df = ak.stock_individual_info_em(symbol=symbol)
    if df is None or df.empty:
        return None
    info_map = dict(zip(df["item"], df["value"]))
    result: dict[str, Any] = {}

    _EM_INFO_MAP = {
        "总市值": "total_market_val",
        "流通市值": "circulating_market_val",
        "行业": "industry",
        "上市时间": "listing_date",
        "总股本": "total_shares",
        "流通股": "float_shares",
    }
    for cn_key, en_key in _EM_INFO_MAP.items():
        val = info_map.get(cn_key)
        if val is not None:
            try:
                fval = float(val)
                result[en_key] = fval
                if en_key == "total_market_val":
                    result["total_market_val_yi"] = fval / 1e8
                elif en_key == "circulating_market_val":
                    result["circulating_market_val_yi"] = fval / 1e8
            except (ValueError, TypeError):
                result[en_key] = str(val)
    return result if result else None


def _fetch_via_em_spot(symbol: str) -> dict | None:
    """东方财富全量快照 stock_zh_a_spot_em — 稳定，数据全，但拉取较重."""
    import akshare as ak

    df = ak.stock_zh_a_spot_em()
    if df is None or df.empty:
        return None
    row = df[df["代码"] == symbol]
    if row.empty:
        return None
    r = row.iloc[0]

    result: dict[str, Any] = {}
    _EM_SPOT_MAP = {
        "总市值": "total_market_val",
        "流通市值": "circulating_market_val",
        "市盈率-动态": "pe",
        "市净率": "pb",
        "换手率": "turnover_rate",
    }
    for cn_key, en_key in _EM_SPOT_MAP.items():
        val = r.get(cn_key)
        if val is not None and str(val) not in ("", "nan", "-"):
            try:
                fval = float(val)
                result[en_key] = fval
                if en_key == "total_market_val":
                    result["total_market_val_yi"] = fval / 1e8
                elif en_key == "circulating_market_val":
                    result["circulating_market_val_yi"] = fval / 1e8
            except (ValueError, TypeError):
                pass
    return result if result else None


def _fetch_via_xueqiu(symbol: str) -> dict | None:
    """雪球 stock_individual_spot_xq — 数据最全(PE/PB/EPS/股息率)，但不够稳定."""
    import akshare as ak

    if symbol.startswith("6"):
        xq_sym = f"SH{symbol}"
    elif symbol.startswith(("0", "3")):
        xq_sym = f"SZ{symbol}"
    elif symbol.startswith(("4", "8", "9")):
        xq_sym = f"BJ{symbol}"
    else:
        xq_sym = f"SH{symbol}"

    _XQ_ITEM_MAP = {
        "市盈率(动)": "pe", "市盈率(TTM)": "pe_ttm", "市盈率(静)": "pe_static",
        "市净率": "pb", "每股收益": "eps", "每股净资产": "bvps",
        "股息率(TTM)": "dividend_yield", "总市值": "total_market_val",
        "流通值": "circulating_market_val", "52周最高": "week52_high",
        "52周最低": "week52_low", "周转率": "turnover_rate",
        "行业": "industry", "上市时间": "listing_date",
    }

    df = ak.stock_individual_spot_xq(symbol=xq_sym)
    if df is None or df.empty:
        return None

    result: dict[str, Any] = {}
    for _, row in df.iterrows():
        item = str(row.get("item", ""))
        value = row.get("value")
        if item in _XQ_ITEM_MAP and value is not None:
            en_key = _XQ_ITEM_MAP[item]
            try:
                fval = float(value)
                result[en_key] = fval
                if en_key == "total_market_val":
                    result["total_market_val_yi"] = fval / 1e8
                elif en_key == "circulating_market_val":
                    result["circulating_market_val_yi"] = fval / 1e8
            except (ValueError, TypeError):
                result[en_key] = str(value)
    return result if result else None


def _get_stock_name(symbol: str) -> str:
    """Get stock name from cache."""
    try:
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            "SELECT name FROM stock_code_name WHERE code = ? LIMIT 1",
            (symbol,),
        ).fetchone()
        conn.close()
        return row[0] if row else symbol
    except Exception:
        return symbol


# ── Feature Computation ──────────────────────────────────────────


def _classify_volatility(klines: list[dict]) -> tuple[str, float]:
    """Compute 20-day realized volatility → classify."""
    if len(klines) < 20:
        return "medium", 0.0
    closes = [k["close_price"] for k in klines]
    # Daily returns for last 60 days (or all available)
    lookback = min(60, len(closes))
    returns = [
        (closes[i] / closes[i - 1] - 1)
        for i in range(len(closes) - lookback, len(closes))
        if closes[i - 1] > 0
    ]
    if not returns:
        return "medium", 0.0
    daily_vol = (sum(r ** 2 for r in returns) / len(returns)) ** 0.5
    ann_vol = daily_vol * math.sqrt(TRADING_DAYS_PER_YEAR)
    if ann_vol < VOL_LOW:
        cls = "low"
    elif ann_vol > VOL_HIGH:
        cls = "high"
    else:
        cls = "medium"
    return cls, round(ann_vol * 100, 1)


def _classify_trend(klines: list[dict]) -> tuple[str, dict]:
    """Classify trend regime using MA crossover + recent return."""
    if len(klines) < 20:
        return "sideways", {}
    closes = [k["close_price"] for k in klines]
    current = closes[-1]

    def _sma(data: list[float], period: int) -> float | None:
        if len(data) < period:
            return None
        return sum(data[-period:]) / period

    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60)
    recent_ret = (closes[-1] / closes[-min(20, len(closes))]) - 1

    # Regime classification (same logic as data_analysis_engine._regime)
    if ma20 and ma60 and current > ma20 > ma60:
        regime = "uptrend"
    elif ma20 and ma60 and current < ma20 < ma60:
        regime = "downtrend"
    else:
        regime = "sideways"

    return regime, {
        "ma20": round(ma20, 2) if ma20 else None,
        "ma60": round(ma60, 2) if ma60 else None,
        "recentReturn": round(recent_ret * 100, 2),
    }


def _classify_market_cap(fundamentals: dict | None) -> tuple[str, float]:
    """Classify market cap from fundamentals."""
    if not fundamentals:
        return "unknown", 0.0
    mcap = fundamentals.get("total_market_val") or 0
    if mcap <= 0:
        # Try _yi variant (in 亿)
        mcap_yi = fundamentals.get("total_market_val_yi") or 0
        mcap = mcap_yi * 1e8
    if mcap <= 0:
        # Fall back to circulating market cap
        mcap = fundamentals.get("circulating_market_val") or 0
        if mcap <= 0:
            mcap_yi = fundamentals.get("circulating_market_val_yi") or 0
            mcap = mcap_yi * 1e8
    if mcap <= 0:
        return "unknown", 0.0
    if mcap < MCAP_MICRO:
        cls = "micro"
    elif mcap < MCAP_SMALL:
        cls = "small"
    elif mcap < MCAP_MID:
        cls = "mid"
    else:
        cls = "large"
    return cls, round(mcap / 1e8, 1)  # in 亿


def _classify_liquidity(fundamentals: dict | None, klines: list[dict]) -> tuple[str, float]:
    """Classify liquidity from turnover rate."""
    # Try fundamentals first
    turnover = 0.0
    if fundamentals:
        turnover = fundamentals.get("turnover_rate") or 0.0
    # Fall back to computing from klines
    if turnover <= 0 and len(klines) >= 5:
        # Volume / assumed float (rough proxy)
        # We don't have float data, so just classify by relative volume
        volumes = [k.get("volume", 0) for k in klines[-20:] if k.get("volume")]
        amounts = [k.get("amount", 0) for k in klines[-20:] if k.get("amount")]
        if amounts:
            # Use amount as proxy; classify by relative magnitude
            avg_amount = sum(amounts) / len(amounts)
            # Very rough: >500M daily = high liquidity, <50M = low
            if avg_amount > 5e8:
                return "high", round(avg_amount / 1e8, 1)
            elif avg_amount < 5e7:
                return "low", round(avg_amount / 1e8, 1)
            else:
                return "medium", round(avg_amount / 1e8, 1)
    if turnover <= 0:
        return "medium", 0.0
    if turnover < LIQ_LOW:
        cls = "low"
    elif turnover > LIQ_HIGH:
        cls = "high"
    else:
        cls = "medium"
    return cls, round(turnover, 2)


def _classify_momentum(klines: list[dict]) -> tuple[str, float]:
    """Classify momentum from 20-day return."""
    if len(klines) < 20:
        return "neutral", 0.0
    closes = [k["close_price"] for k in klines]
    ret_20d = (closes[-1] / closes[-20] - 1) * 100
    if ret_20d > MOM_POS:
        cls = "positive"
    elif ret_20d < MOM_NEG:
        cls = "negative"
    else:
        cls = "neutral"
    return cls, round(ret_20d, 2)


def _classify_volume_trend(klines: list[dict]) -> tuple[str, float]:
    """Classify volume trend: 5-day avg vs 20-day avg."""
    if len(klines) < 20:
        return "normal", 1.0
    volumes = [k.get("volume", 0) for k in klines if k.get("volume", 0) > 0]
    if len(volumes) < 20:
        return "normal", 1.0
    vol_5 = sum(volumes[-5:]) / 5
    vol_20 = sum(volumes[-20:]) / 20
    if vol_20 == 0:
        return "normal", 1.0
    ratio = vol_5 / vol_20
    if ratio < 0.8:
        cls = "shrinking"
    elif ratio > 1.5:
        cls = "expanding"
    else:
        cls = "normal"
    return cls, round(ratio, 2)


# ── Main Profile Function ────────────────────────────────────────


def profile_stock(symbol: str, end_date: str, lookback_days: int = 365) -> dict:
    """Compute full profile for a single stock."""
    # Compute start date for lookback
    from datetime import datetime, timedelta
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    start_date = (end_dt - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    klines = _get_klines(symbol, start_date, end_date)
    fundamentals = _get_fundamentals(symbol)
    name = _get_stock_name(symbol)

    if len(klines) < 20:
        return {
            "symbol": symbol,
            "name": name,
            "ok": False,
            "error": f"数据不足: 仅 {len(klines)} 根K线",
        }

    vol_cls, vol_val = _classify_volatility(klines)
    trend_cls, trend_detail = _classify_trend(klines)
    mcap_cls, mcap_val = _classify_market_cap(fundamentals)
    liq_cls, liq_val = _classify_liquidity(fundamentals, klines)
    mom_cls, mom_val = _classify_momentum(klines)
    voltrend_cls, voltrend_val = _classify_volume_trend(klines)
    sector = (fundamentals.get("industry") or "δ֪") if fundamentals else "δ֪"

    return {
        "symbol": symbol,
        "name": name,
        "ok": True,
        "endDate": end_date,
        "dataPoints": len(klines),
        "volatility": {"class": vol_cls, "annualized": vol_val},
        "trend": {"class": trend_cls, **trend_detail},
        "marketCap": {"class": mcap_cls, "value_yi": mcap_val},
        "liquidity": {"class": liq_cls, "value": liq_val},
        "momentum": {"class": mom_cls, "return20d": mom_val},
        "volumeTrend": {"class": voltrend_cls, "ratio": voltrend_val},
        "sector": sector,
        # Compact key for routing table lookup
        "profileKey": f"{vol_cls}|{trend_cls}|{mcap_cls}",
    }


def profile_stocks(symbols: list[str], end_date: str) -> list[dict]:
    """Batch-profile multiple stocks."""
    return [profile_stock(s, end_date) for s in symbols]


def list_stocks_with_data(min_bars: int, start_date: str) -> list[str]:
    """List all symbols with sufficient kline data."""
    try:
        conn = sqlite3.connect(CACHE_DB)
        rows = conn.execute(
            "SELECT symbol FROM kline_data "
            "WHERE period = 'daily' AND trade_date >= ? "
            "GROUP BY symbol HAVING COUNT(*) >= ?",
            (start_date, min_bars),
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


# ── CLI / stdin entry ─────────────────────────────────────────────

def main():
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        config = json.loads(config_str)
    else:
        config = json.loads(sys.stdin.read())

    action = config.get("action", "profile")
    end_date = config.get("endDate", "2026-03-20")

    if action == "profile":
        symbols = config.get("symbols", [])
        if isinstance(symbols, str):
            symbols = [s.strip() for s in symbols.split(",") if s.strip()]
        results = profile_stocks(symbols, end_date)
        print(json.dumps({"ok": True, "profiles": results}, ensure_ascii=False))

    elif action == "profile_all":
        min_bars = config.get("minBars", 200)
        start_date = config.get("startDate", "2024-06-01")
        symbols = list_stocks_with_data(min_bars, start_date)
        results = profile_stocks(symbols, end_date)
        print(json.dumps({
            "ok": True,
            "totalStocks": len(symbols),
            "profiles": results,
        }, ensure_ascii=False))

    elif action == "list_stocks":
        min_bars = config.get("minBars", 200)
        start_date = config.get("startDate", "2024-06-01")
        symbols = list_stocks_with_data(min_bars, start_date)
        print(json.dumps({"ok": True, "symbols": symbols, "count": len(symbols)}))

    else:
        print(json.dumps({"ok": False, "error": f"Unknown action: {action}"}))


if __name__ == "__main__":
    main()
