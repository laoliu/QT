#!/usr/bin/env python3
"""
Investment Planner — given initial capital, target, and timeframe, generates a
comprehensive trading plan with stock selection, position sizing, and monitoring rules.

Actions:
  plan        — generate a full investment plan
  screen      — screen stocks based on strategy criteria
  monitor     — check current portfolio against plan, generate signals
  update_plan — update an existing plan with new market data

Input (JSON via stdin):
  { "action": "plan",
    "initial_capital": 500000,
    "target_capital": 1000000,
    "timeframe_months": 12,
    "risk_tolerance": "medium",    // low | medium | high | aggressive
    "strategy_preference": "auto"  // auto | momentum | value | growth | swing | mixed
  }
"""

import json
import math
import os
import sys
import urllib.request
from datetime import datetime, timedelta

# Proxy bypass
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

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PYTHON_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PYTHON_ROOT)


def _safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (ValueError, TypeError):
        return default


# ── Cross-module integration caches (loaded once per process) ──


def _clean_stock_code(raw: str) -> str:
    """Extract a 6-digit A-share code from potentially prefixed strings (e.g. 'sh600519' → '600519')."""
    s = str(raw).strip().lower()
    for prefix in ("sh", "sz", "bj"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    # Remove trailing suffix like '.SH', '.SZ'
    if "." in s:
        s = s.split(".")[0]
    return s.zfill(6) if s.isdigit() else ""


_EVENT_CATALYST_CACHE: dict | None = None
_LIANBAN_CACHE: dict | None = None
_MACRO_REGIME_CACHE: dict | None = None
_HOT_STOCKS_CACHE: dict | None = None
_EARNINGS_UPCOMING_CACHE: dict | None = None


def _get_lianban_data() -> dict:
    """Fetch today's limit-up pool and cache. Used for lianban scoring in planner."""
    global _LIANBAN_CACHE  # noqa: PLW0603
    if _LIANBAN_CACHE is not None:
        return _LIANBAN_CACHE
    try:
        from engines.lianban import fetch_zt_pool
        pool = fetch_zt_pool()
        stocks = pool.get("stocks", [])
        # Build lookup: code → pool entry
        code_map: dict[str, dict] = {}
        for s in stocks:
            code = _clean_stock_code(s.get("code", ""))
            if code:
                code_map[code] = s
        _LIANBAN_CACHE = {"stocks": stocks, "code_map": code_map, "count": len(stocks)}
    except Exception as e:
        print(f"[planner] lianban pool fetch failed: {e}", file=sys.stderr, flush=True)
        _LIANBAN_CACHE = {"stocks": [], "code_map": {}, "count": 0}
    return _LIANBAN_CACHE


def _get_macro_regime() -> dict:
    """Fetch macro regime analysis and cache."""
    global _MACRO_REGIME_CACHE  # noqa: PLW0603
    if _MACRO_REGIME_CACHE is not None:
        return _MACRO_REGIME_CACHE
    try:
        from engines.macro_economic import run_macro_economic
        result = run_macro_economic({"action": "regime", "market": "cn"})
        _MACRO_REGIME_CACHE = result
    except Exception as e:
        print(f"[planner] macro regime fetch failed: {e}", file=sys.stderr, flush=True)
        _MACRO_REGIME_CACHE = {"regime": "unknown", "description": ""}
    return _MACRO_REGIME_CACHE


def _get_hot_stocks() -> dict:
    """Fetch hot stocks ranking and cache. Returns {code_set, code_rank}."""
    global _HOT_STOCKS_CACHE  # noqa: PLW0603
    if _HOT_STOCKS_CACHE is not None:
        return _HOT_STOCKS_CACHE
    try:
        from engines.social_sentiment import run_social_sentiment
        result = run_social_sentiment({"action": "hot_stocks", "market": "cn", "limit": 50})
        hot_list = result.get("hot_stocks", [])
        code_set: set[str] = set()
        code_rank: dict[str, int] = {}
        for item in hot_list:
            code = _clean_stock_code(item.get("symbol", ""))
            if code and len(code) == 6:
                code_set.add(code)
                rank = item.get("rank", 0)
                if rank:
                    code_rank[code] = rank
        _HOT_STOCKS_CACHE = {"code_set": code_set, "code_rank": code_rank, "count": len(hot_list)}
    except Exception as e:
        print(f"[planner] hot stocks fetch failed: {e}", file=sys.stderr, flush=True)
        _HOT_STOCKS_CACHE = {"code_set": set(), "code_rank": {}, "count": 0}
    return _HOT_STOCKS_CACHE


def _get_upcoming_earnings() -> dict:
    """Fetch upcoming earnings dates and cache. Returns {code_set, code_date}."""
    global _EARNINGS_UPCOMING_CACHE  # noqa: PLW0603
    if _EARNINGS_UPCOMING_CACHE is not None:
        return _EARNINGS_UPCOMING_CACHE
    try:
        from engines.earnings_calendar import run_earnings_calendar
        result = run_earnings_calendar({"action": "upcoming", "market": "cn", "days": 14, "limit": 200})
        earnings = result.get("earnings", [])
        code_set: set[str] = set()
        code_date: dict[str, str] = {}
        for item in earnings:
            code = _clean_stock_code(item.get("symbol", ""))
            date = item.get("disclosure_date", "")
            if code and len(code) == 6:
                code_set.add(code)
                if date:
                    code_date[code] = date
        _EARNINGS_UPCOMING_CACHE = {"code_set": code_set, "code_date": code_date, "count": len(earnings)}
    except Exception as e:
        print(f"[planner] earnings calendar fetch failed: {e}", file=sys.stderr, flush=True)
        _EARNINGS_UPCOMING_CACHE = {"code_set": set(), "code_date": {}, "count": 0}
    return _EARNINGS_UPCOMING_CACHE


def _get_event_catalyst_data() -> dict:
    """Load and cache US-A mapping + scarcity tags for event_catalyst strategy."""
    global _EVENT_CATALYST_CACHE
    if _EVENT_CATALYST_CACHE is not None:
        return _EVENT_CATALYST_CACHE

    data_dir = os.path.join(_PYTHON_ROOT, "data")

    # Load US-A mapping
    mapping: dict = {}
    mapping_path = os.path.join(data_dir, "us_a_mapping.json")
    if os.path.exists(mapping_path):
        with open(mapping_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        mapping = {k: v for k, v in raw.items() if not k.startswith("_")}

    # Load scarcity tags
    scarcity: dict = {}
    scarcity_path = os.path.join(data_dir, "scarcity_tags.json")
    if os.path.exists(scarcity_path):
        with open(scarcity_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        scarcity = raw.get("stocks", {})

    # Build reverse index: code → {us_symbols, sectors}
    code_info: dict[str, dict] = {}
    for us_sym, entry in mapping.items():
        a_shares = entry.get("a_shares", {})
        for sector, codes in a_shares.items():
            for code in codes:
                if code not in code_info:
                    code_info[code] = {"us_symbols": set(), "sectors": set()}
                code_info[code]["us_symbols"].add(us_sym)
                code_info[code]["sectors"].add(sector)

    _EVENT_CATALYST_CACHE = {
        "mapping": mapping,
        "scarcity": scarcity,
        "code_info": code_info,
    }
    return _EVENT_CATALYST_CACHE


_US_MOVERS_CACHE: dict | None = None


def _get_us_movers() -> dict:
    """Fetch real-time US tech movers via event catalyst engine and cache.

    Returns: {triggered: bool, movers: [{symbol, changePct, themes}],
              triggered_codes: set[str]} — A-share codes with active US catalysts.
    """
    global _US_MOVERS_CACHE  # noqa: PLW0603
    if _US_MOVERS_CACHE is not None:
        return _US_MOVERS_CACHE
    try:
        from engines.event_catalyst_engine import _check_us, _load_us_a_mapping
        result = _check_us({"min_change_pct": 2.0})
        significant = result.get("significantMovers", [])
        mapping = _load_us_a_mapping()

        # Build set of A-share codes with active US catalysts
        triggered_codes: dict[str, dict] = {}  # code → {us_symbols, max_change}
        for mover in significant:
            us_sym = mover.get("symbol", "")
            change = abs(mover.get("changePct", 0))
            entry = mapping.get(us_sym, {})
            for _sector, codes in entry.get("a_shares", {}).items():
                for code in codes:
                    if code not in triggered_codes:
                        triggered_codes[code] = {"us_symbols": [], "max_change": 0}
                    triggered_codes[code]["us_symbols"].append(us_sym)
                    triggered_codes[code]["max_change"] = max(
                        triggered_codes[code]["max_change"], change)

        _US_MOVERS_CACHE = {
            "triggered": len(significant) > 0,
            "significant": significant,
            "triggered_codes": triggered_codes,
            "count": len(significant),
        }
    except Exception as e:
        print(f"[planner] US movers fetch failed: {e}", file=sys.stderr, flush=True)
        _US_MOVERS_CACHE = {"triggered": False, "significant": [], "triggered_codes": {}, "count": 0}
    return _US_MOVERS_CACHE


_INSTITUTIONAL_CACHE: dict[str, dict] = {}


def _get_institutional_signal(symbol: str) -> dict | None:
    """Fetch smart money signal for a stock (cached per symbol)."""
    if symbol in _INSTITUTIONAL_CACHE:
        return _INSTITUTIONAL_CACHE[symbol]
    try:
        from engines.institutional_flow import run_institutional_flow
        result = run_institutional_flow({"action": "smart_money_signal", "symbol": symbol})
        _INSTITUTIONAL_CACHE[symbol] = result
        return result
    except Exception as e:
        print(f"[planner] institutional signal failed for {symbol}: {e}", file=sys.stderr, flush=True)
        _INSTITUTIONAL_CACHE[symbol] = {}
        return None


_SENTIMENT_CACHE: dict[str, dict] = {}


def _get_stock_sentiment(symbol: str) -> dict | None:
    """Fetch per-stock social sentiment (cached per symbol).

    Returns: {composite_score: 0-100, level: "高热度"|..., interpretation: str} or None.
    """
    if symbol in _SENTIMENT_CACHE:
        return _SENTIMENT_CACHE[symbol]
    try:
        from engines.social_sentiment import run_social_sentiment
        result = run_social_sentiment({"action": "stock_sentiment", "symbol": symbol})
        _SENTIMENT_CACHE[symbol] = result
        return result
    except Exception as e:
        print(f"[planner] sentiment failed for {symbol}: {e}", file=sys.stderr, flush=True)
        _SENTIMENT_CACHE[symbol] = {}
        return None


_ANALYST_CACHE: dict[str, dict] = {}


def _get_analyst_consensus(symbol: str) -> dict | None:
    """Fetch analyst ratings consensus for a stock (cached per symbol).

    Returns: {distribution: {buy, overweight, hold, underweight, sell}, ratings: [...]} or None.
    """
    if symbol in _ANALYST_CACHE:
        return _ANALYST_CACHE[symbol]
    try:
        from engines.earnings_calendar import run_earnings_calendar
        result = run_earnings_calendar({
            "action": "analyst_ratings", "symbol": symbol, "market": "cn", "limit": 10,
        })
        _ANALYST_CACHE[symbol] = result
        return result
    except Exception as e:
        print(f"[planner] analyst ratings failed for {symbol}: {e}", file=sys.stderr, flush=True)
        _ANALYST_CACHE[symbol] = {}
        return None


# ── Macro Regime Position Adjustment ──

REGIME_ADJUSTMENTS: dict[str, dict] = {
    "recovery": {
        "position_scale": 1.0,   # full allocation
        "score_bonus": 2,        # mild tailwind for all candidates
        "description": "复苏期：资产配置正常，风险偏好可适当提升",
    },
    "overheating": {
        "position_scale": 0.8,   # reduce allocation 20%
        "score_bonus": 0,
        "description": "过热期：适当降低仓位，警惕回调风险",
    },
    "stagflation": {
        "position_scale": 0.6,   # reduce allocation 40%
        "score_bonus": -2,       # headwind for all candidates
        "description": "滞胀期：大幅降低仓位，优选低估值防御品种",
    },
    "recession": {
        "position_scale": 0.5,   # half allocation
        "score_bonus": -3,       # significant headwind
        "description": "衰退期：仓位减半，防御为主，保留现金",
    },
}


def _tencent_market_prefix(symbol: str) -> str:
    if symbol.startswith("6") or symbol.startswith("5") or symbol.startswith("9"):
        return "sh"
    return "sz"


def _fetch_tencent_batch(symbols: list[str]) -> dict[str, dict]:
    """Fetch real-time quotes for multiple symbols via Tencent API."""
    results: dict[str, dict] = {}
    # Tencent supports comma-separated list
    codes = ",".join(_tencent_market_prefix(s) + s for s in symbols)
    url = f"http://qt.gtimg.cn/q={codes}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            for enc in ("utf-8", "gbk", "gb2312"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                return results
        # Parse multiple v_shXXXXXX="..." lines
        for line in text.strip().split(";"):
            line = line.strip()
            if not line or "=" not in line:
                continue
            eq = line.find("=")
            inner = line[eq + 1:].strip().strip('"')
            fields = inner.split("~")
            if len(fields) < 50:
                continue
            code = fields[2]
            results[code] = {
                "name": fields[1],
                "price": _safe_float(fields[3]),
                "prevClose": _safe_float(fields[4]),
                "open": _safe_float(fields[5]),
                "high": _safe_float(fields[33]),
                "low": _safe_float(fields[34]),
                "change": _safe_float(fields[31]),
                "changePct": _safe_float(fields[32]),
                "volume": _safe_float(fields[6]) * 100,  # 手→股
                "amount": _safe_float(fields[37]) * 10000,  # 万→元
                "turnoverRate": _safe_float(fields[38]),
                "pe": _safe_float(fields[39]),
                "pb": _safe_float(fields[46]),
                "totalMarketCap": _safe_float(fields[45]) * 1e8,  # 亿→元
                "circulatingMarketCap": _safe_float(fields[44]) * 1e8,
                "amplitude": _safe_float(fields[43]),
                "volumeRatio": _safe_float(fields[49]),
            }
    except Exception:
        pass
    return results


# ── Risk Profile Definitions ──────────────────────────────────────

RISK_PROFILES = {
    "low": {
        "label": "稳健型",
        "max_single_position_pct": 0.15,
        "max_sector_pct": 0.30,
        "stop_loss_pct": 0.05,
        "take_profit_pct": 0.15,
        "max_positions": 10,
        "min_positions": 5,
        "preferred_pe_range": (0, 30),
        "preferred_pb_range": (0, 5),
        "min_market_cap": 50e8,  # 50亿
        "rebalance_days": 30,
        "strategy_weights": {"value": 0.5, "growth": 0.3, "dividend": 0.2},
    },
    "medium": {
        "label": "均衡型",
        "max_single_position_pct": 0.20,
        "max_sector_pct": 0.35,
        "stop_loss_pct": 0.08,
        "take_profit_pct": 0.25,
        "max_positions": 8,
        "min_positions": 4,
        "preferred_pe_range": (0, 50),
        "preferred_pb_range": (0, 8),
        "min_market_cap": 20e8,  # 20亿
        "rebalance_days": 14,
        "strategy_weights": {"momentum": 0.3, "growth": 0.4, "swing": 0.3},
    },
    "high": {
        "label": "进取型",
        "max_single_position_pct": 0.25,
        "max_sector_pct": 0.40,
        "stop_loss_pct": 0.10,
        "take_profit_pct": 0.35,
        "max_positions": 6,
        "min_positions": 3,
        "preferred_pe_range": (0, 80),
        "preferred_pb_range": (0, 15),
        "min_market_cap": 10e8,  # 10亿
        "rebalance_days": 7,
        "strategy_weights": {"momentum": 0.5, "swing": 0.3, "growth": 0.2},
    },
    "aggressive": {
        "label": "激进型",
        "max_single_position_pct": 0.30,
        "max_sector_pct": 0.50,
        "stop_loss_pct": 0.12,
        "take_profit_pct": 0.50,
        "max_positions": 5,
        "min_positions": 2,
        "preferred_pe_range": (0, 200),
        "preferred_pb_range": (0, 30),
        "min_market_cap": 5e8,  # 5亿
        "rebalance_days": 5,
        "strategy_weights": {"momentum": 0.6, "swing": 0.3, "speculative": 0.1},
    },
}


def _compute_technicals_from_klines(klines: list[dict]) -> dict:
    """Compute MA, RSI, MACD from kline data."""
    if len(klines) < 20:
        return {}
    closes = [b["close"] for b in klines]
    n = len(closes)

    def _sma(data, period):
        if len(data) < period:
            return None
        return sum(data[-period:]) / period

    def _ema(data, period):
        if len(data) < period:
            return None
        k = 2.0 / (period + 1)
        ema_val = sum(data[:period]) / period
        for v in data[period:]:
            ema_val = v * k + ema_val * (1 - k)
        return ema_val

    def _rsi(data, period=14):
        if len(data) < period + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(data)):
            d = data[i] - data[i - 1]
            gains.append(max(d, 0))
            losses.append(max(-d, 0))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    ma5 = _sma(closes, 5)
    ma10 = _sma(closes, 10)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60) if n >= 60 else None
    rsi14 = _rsi(closes, 14)

    # Trend score: higher = more bullish
    trend_score = 0
    if ma5 and ma10 and ma5 > ma10:
        trend_score += 1
    if ma10 and ma20 and ma10 > ma20:
        trend_score += 1
    if ma20 and closes[-1] > ma20:
        trend_score += 1
    if ma60 and closes[-1] > ma60:
        trend_score += 1

    # Momentum: recent price change rate
    if len(closes) >= 20:
        momentum_20d = (closes[-1] / closes[-20] - 1) * 100
    else:
        momentum_20d = 0

    if len(closes) >= 5:
        momentum_5d = (closes[-1] / closes[-5] - 1) * 100
    else:
        momentum_5d = 0

    # Volatility (20-day)
    if len(closes) >= 20:
        returns = [(closes[i] / closes[i - 1] - 1) for i in range(max(1, len(closes) - 20), len(closes))]
        volatility = (sum(r ** 2 for r in returns) / len(returns)) ** 0.5
    else:
        volatility = 0

    # Volume analysis (matching stock_recommend.py)
    volumes = [b.get("volume", 0) for b in klines]
    vol_5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else None
    vol_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
    volume_ratio = round(vol_5 / vol_20, 2) if vol_5 and vol_20 and vol_20 > 0 else 1.0

    # Price position within 20-day range
    if len(closes) >= 20:
        high_20 = max(closes[-20:])
        low_20 = min(closes[-20:])
        price_position = round((closes[-1] - low_20) / (high_20 - low_20), 2) if high_20 > low_20 else 0.5
    else:
        price_position = 0.5

    return {
        "ma5": round(ma5, 2) if ma5 else None,
        "ma10": round(ma10, 2) if ma10 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma60": round(ma60, 2) if ma60 else None,
        "rsi14": round(rsi14, 2) if rsi14 else None,
        "trend_score": trend_score,
        "momentum_5d": round(momentum_5d, 2),
        "momentum_20d": round(momentum_20d, 2),
        "volatility": round(volatility, 4),
        "volume_ratio": volume_ratio,
        "price_position": price_position,
    }


def _classify_volume_ratio(vr: float) -> tuple[str, str]:
    """Classify volume ratio into tier and market meaning.

    Returns (tier, description):
      - 'extreme_low'  (< 0.5):  极度缩量 — 无人关注或吸筹末期
      - 'mild_shrink'  (0.5-0.8): 温和缩量 — 正常回调/洗盘
      - 'normal'       (0.8-1.2): 常态量 — 正常交易
      - 'mild_expand'  (1.2-2.0): 温和放量 — 正常异动
      - 'strong_expand' (2.0-3.0): 明显放量 — 突破/转折信号
      - 'surge'        (3.0-5.0): 急剧放量 — 主力动作
      - 'extreme_high' (> 5.0):  极端量比 — 几乎必有庄家行为
    """
    if vr < 0.5:
        return "extreme_low", "极度缩量"
    if vr < 0.8:
        return "mild_shrink", "温和缩量"
    if vr < 1.2:
        return "normal", "常态量"
    if vr < 2.0:
        return "mild_expand", "温和放量"
    if vr < 3.0:
        return "strong_expand", "明显放量"
    if vr < 5.0:
        return "surge", "急剧放量"
    return "extreme_high", f"极端量比({vr:.1f})"


def _detect_manipulation_patterns(klines: list[dict]) -> dict:
    """Detect manipulation patterns from kline data (Wyckoff/VSA).

    Returns:
        dict with 'pattern' (accumulation/distribution/churning/neutral),
        'score_adj' (float), 'signals' (list[str]), 'confidence' (float 0-1)
    """
    result = {"pattern": "neutral", "score_adj": 0.0, "signals": [], "confidence": 0.0}
    if len(klines) < 30:
        return result

    closes = [b["close"] for b in klines]
    volumes = [b.get("volume", 0) for b in klines]
    highs = [b["high"] for b in klines]
    lows = [b["low"] for b in klines]
    opens = [b.get("open", b["close"]) for b in klines]
    n = len(closes)

    avg_vol_20 = sum(volumes[-20:]) / 20
    avg_vol_60 = sum(volumes[-60:]) / min(60, n) if n >= 20 else avg_vol_20
    if avg_vol_20 <= 0:
        return result

    # Price position: where is current price in 60-day range
    lookback = min(60, n)
    high_60 = max(highs[-lookback:])
    low_60 = min(lows[-lookback:])
    price_range = high_60 - low_60
    if price_range <= 0:
        return result
    price_pos = (closes[-1] - low_60) / price_range  # 0=bottom, 1=top

    # ── Accumulation Detection (吸筹) ──
    acc_signals = []
    acc_score = 0.0

    # Pattern 1: Volume shrinks on dips, expands on ups (缩量回调放量上涨)
    up_vols, down_vols = [], []
    for i in range(-20, 0):
        if closes[i] >= closes[i - 1]:
            up_vols.append(volumes[i])
        else:
            down_vols.append(volumes[i])
    if up_vols and down_vols:
        avg_up_vol = sum(up_vols) / len(up_vols)
        avg_down_vol = sum(down_vols) / len(down_vols)
        if avg_up_vol > avg_down_vol * 1.3:
            acc_score += 2.0
            acc_signals.append("缩量回调放量上涨(主力承接)")

    # Pattern 2: Low volatility consolidation at bottom (底部横盘整理)
    if price_pos < 0.3:
        recent_range = (max(highs[-10:]) - min(lows[-10:])) / closes[-10] if closes[-10] > 0 else 1
        if recent_range < 0.06 and avg_vol_20 > avg_vol_60 * 0.8:
            acc_score += 2.5
            acc_signals.append("底部横盘整理(低位窄幅震荡)")

    # Pattern 3: Multiple lower shadows at support (下影线=有承接)
    shadow_count = 0
    for i in range(-10, 0):
        body_low = min(opens[i], closes[i])
        lower_shadow = body_low - lows[i]
        body_size = abs(closes[i] - opens[i])
        if body_size > 0 and lower_shadow > body_size * 1.5:
            shadow_count += 1
    if shadow_count >= 3:
        acc_score += 1.5
        acc_signals.append(f"连续下影线({shadow_count}根，有承接力)")

    # Pattern 4: Price near 60-day low but volume increasing (低位放量)
    if price_pos < 0.25 and avg_vol_20 > avg_vol_60 * 1.3:
        acc_score += 2.0
        acc_signals.append("低位放量(主力可能建仓)")

    # ── Distribution Detection (出货) ──
    dist_signals = []
    dist_score = 0.0

    # Pattern 1: Volume climax at highs (天量滞涨)
    if price_pos > 0.7:
        max_vol_idx = max(range(-10, 0), key=lambda i: volumes[i])
        if volumes[max_vol_idx] > avg_vol_20 * 2.5:
            price_change_that_day = abs(closes[max_vol_idx] - opens[max_vol_idx]) / opens[max_vol_idx] if opens[max_vol_idx] > 0 else 0
            if price_change_that_day < 0.02:
                dist_score += 3.0
                dist_signals.append("高位天量滞涨(主力对倒出货)")
            elif closes[max_vol_idx] < opens[max_vol_idx]:
                dist_score += 2.5
                dist_signals.append("高位放量收阴(出货信号)")

    # Pattern 2: Upper shadows at highs (上影线多=抛压重)
    upper_shadow_count = 0
    if price_pos > 0.6:
        for i in range(-10, 0):
            body_high = max(opens[i], closes[i])
            upper_shadow = highs[i] - body_high
            body_size = abs(closes[i] - opens[i])
            if body_size > 0 and upper_shadow > body_size * 1.5:
                upper_shadow_count += 1
        if upper_shadow_count >= 3:
            dist_score += 2.0
            dist_signals.append(f"高位多根上影线({upper_shadow_count}根，抛压沉重)")

    # Pattern 3: Volume increasing but price stalling (量增价不涨)
    if price_pos > 0.5:
        price_5d = (closes[-1] / closes[-6] - 1) * 100 if n >= 6 else 0
        vol_increasing = avg_vol_20 > avg_vol_60 * 1.4
        if vol_increasing and abs(price_5d) < 2:
            dist_score += 2.0
            dist_signals.append("量增价不涨(资金分歧)")

    # Pattern 4: Gap up + close low pattern (高开低走)
    gap_down_close_count = 0
    for i in range(-5, 0):
        if opens[i] > closes[i - 1] * 1.01 and closes[i] < opens[i] * 0.99:
            gap_down_close_count += 1
    if gap_down_close_count >= 2:
        dist_score += 2.5
        dist_signals.append(f"连续高开低走({gap_down_close_count}次，主力出货)")

    # ── Churning Detection (对倒) ──
    churn_signals = []
    churn_score = 0.0

    # Very high volume + small price move
    for i in range(-5, 0):
        if volumes[i] > avg_vol_20 * 2:
            price_move = abs(closes[i] - opens[i]) / opens[i] if opens[i] > 0 else 0
            amplitude = (highs[i] - lows[i]) / lows[i] if lows[i] > 0 else 0
            if price_move < 0.01 and amplitude > 0.03:
                churn_score += 2.0
                churn_signals.append("大幅振荡量大价不动(对倒)")
                break

    # ── Volume Ratio Tier Analysis (量比分级与庄家行为结合) ──
    # Compute real-time-like volume ratio: last bar volume vs 5-day avg
    vol_today = volumes[-1]
    vol_5d_avg = sum(volumes[-6:-1]) / 5 if n >= 6 else avg_vol_20
    vr = vol_today / vol_5d_avg if vol_5d_avg > 0 else 1.0
    vr_tier, vr_desc = _classify_volume_ratio(vr)

    # Extreme volume ratio (>5): almost always manipulator behavior
    if vr_tier == "extreme_high":
        price_change = (closes[-1] / closes[-2] - 1) * 100 if n >= 2 else 0
        if price_pos > 0.7 and price_change < 3:
            # High position + extreme volume + not surging = distribution dump
            dist_score += 4.0
            dist_signals.append(f"{vr_desc}+高位滞涨(主力对倒出货)")
        elif price_pos > 0.7 and price_change > 8:
            # High position + extreme volume + big rise = pump-and-dump lure
            dist_score += 3.0
            dist_signals.append(f"{vr_desc}+急拉涨停(诱多出货可能)")
        elif price_pos < 0.3 and price_change > 0:
            # Low position + extreme volume + rising = aggressive accumulation
            acc_score += 3.5
            acc_signals.append(f"{vr_desc}+低位放量拉升(主力强力建仓)")
        else:
            # Extreme volume in any other context = high risk
            churn_score += 3.0
            churn_signals.append(f"{vr_desc}+异常(高度警惕庄家行为)")

    # Surge volume ratio (3-5): significant manipulator action
    elif vr_tier == "surge":
        price_change = (closes[-1] / closes[-2] - 1) * 100 if n >= 2 else 0
        if price_pos > 0.7:
            # High + surge volume = distribution or test
            upper_shadow = highs[-1] - max(opens[-1], closes[-1])
            body = abs(closes[-1] - opens[-1])
            if body > 0 and upper_shadow > body * 1.5:
                dist_score += 2.5
                dist_signals.append(f"量比{vr:.1f}+高位长上影线(试盘或出货)")
            elif price_change < 1:
                dist_score += 2.0
                dist_signals.append(f"量比{vr:.1f}+高位滞涨(出货信号)")
        elif price_pos < 0.3 and price_change > 0:
            # Low + surge + rising = accumulation
            acc_score += 2.5
            acc_signals.append(f"量比{vr:.1f}+低位放量上涨(主力建仓)")
        elif abs(price_change) < 1:
            # Surge + no move = churning
            churn_score += 2.0
            churn_signals.append(f"量比{vr:.1f}+价格不动(对倒嫌疑)")

    # Strong expand (2-3): breakout or turning point
    elif vr_tier == "strong_expand":
        price_change = (closes[-1] / closes[-2] - 1) * 100 if n >= 2 else 0
        if price_pos < 0.3 and price_change > 0:
            acc_score += 1.5
            acc_signals.append(f"量比{vr:.1f}+低位突破(关注建仓)")
        elif price_pos > 0.8 and price_change < 0:
            dist_score += 1.5
            dist_signals.append(f"量比{vr:.1f}+高位放量下跌(出货)")

    # Extreme low volume (<0.5): dead volume — could be end of washout
    elif vr_tier == "extreme_low":
        if price_pos < 0.3:
            # Bottom + no volume = selling exhaustion, accumulation end-stage
            acc_score += 2.0
            acc_signals.append("极度缩量+低位(卖压枯竭,吸筹末期)")
        elif price_pos > 0.7:
            # High + no volume = momentum exhaustion
            dist_score += 1.0
            dist_signals.append("极度缩量+高位(上涨动力不足)")

    # Determine dominant pattern
    patterns = [
        ("accumulation", acc_score, acc_signals),
        ("distribution", dist_score, dist_signals),
        ("churning", churn_score, churn_signals),
    ]
    best = max(patterns, key=lambda p: p[1])
    pattern_name, pattern_score, pattern_signals = best

    if pattern_score < 1.5:
        return result

    # Calculate score adjustment
    if pattern_name == "accumulation":
        # Reward accumulation: this is a follow signal
        score_adj = min(pattern_score, 8.0)
        confidence = min(pattern_score / 8.0, 1.0)
    elif pattern_name == "distribution":
        # Penalize distribution: avoid these stocks
        score_adj = -min(pattern_score, 8.0)
        confidence = min(pattern_score / 8.0, 1.0)
    elif pattern_name == "churning":
        # Penalize churning
        score_adj = -min(pattern_score, 4.0)
        confidence = min(pattern_score / 4.0, 1.0)
    else:
        score_adj = 0
        confidence = 0

    return {
        "pattern": pattern_name,
        "score_adj": round(score_adj, 1),
        "signals": pattern_signals,
        "confidence": round(confidence, 2),
    }


_sina_available: bool | None = None  # cached probe result


def _probe_sina(timeout: int = 4) -> bool:
    """Quick health probe: fetch 1 stock from Sina to check if API is reachable."""
    global _sina_available
    if _sina_available is not None:
        return _sina_available
    url = (
        "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        "Market_Center.getHQNodeData?page=1&num=1&sort=changepercent&asc=0"
        "&node=hs_a&symbol=&_s_r_a=sort"
    )
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 200 and len(resp.read()) > 10:
                _sina_available = True
                return True
    except Exception:
        pass
    _sina_available = False
    return False


def _fetch_sina_stock_list(page: int = 1, num: int = 80, sort: str = "changepercent", asc: int = 0) -> list[dict]:
    """Fetch A-share stock list from Sina Finance API.

    Reliable alternative to akshare when push2.eastmoney.com is unavailable.
    Returns list of dicts with: symbol, name, trade, changepercent, settlement,
    open, high, low, volume, amount, per(PE), pb, mktcap, nmc, turnoverratio.
    """
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
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            # Sina returns JS-style JSON with unquoted keys; use eval-safe parsing
            # The format is: [{symbol:"sh600519", ...}, ...]
            # Convert to proper JSON by adding quotes around keys
            import re
            # Fix unquoted keys: word: -> "word":
            fixed = re.sub(r'(\{|,)\s*([a-zA-Z_]\w*)\s*:', r'\1"\2":', raw)
            return json.loads(fixed)
    except Exception:
        return []


def _fetch_sina_all_pages(max_pages: int = 6, num_per_page: int = 80) -> list[dict]:
    """Fetch multiple pages from Sina using varied sort criteria for a diverse universe.

    Instead of always sorting by changepercent (which gives the same top gainers),
    we use multiple sort keys to get stocks from different angles:
    - changepercent (涨跌幅) — momentum leaders
    - turnoverratio (换手率) — active traded stocks
    - amount (成交额) — high liquidity stocks
    - volume (成交量) — volume leaders
    """
    import random
    # 6 dimensions covering momentum, activity, liquidity, valuation, and size
    sort_configs = [
        ("changepercent", 0),   # 涨跌幅 — 动量领涨股
        ("turnoverratio", 0),   # 换手率 — 市场关注度高
        ("amount", 0),          # 成交额 — 流动性好
        ("per", 1),             # 市盈率 — 升序=低估值价值股
        ("pb", 1),              # 市净率 — 升序=低PB资产低估
        ("mktcap", 0),          # 总市值 — 降序=大盘蓝筹
    ]
    random.shuffle(sort_configs)

    seen_symbols: set[str] = set()
    all_stocks: list[dict] = []

    pages_per_sort = max(1, max_pages // len(sort_configs))
    for sort_key, default_asc in sort_configs:
        # Slight asc/desc variation, but respect the default direction
        asc = default_asc if random.random() < 0.7 else (1 - default_asc)
        for page in range(1, pages_per_sort + 1):
            stocks = _fetch_sina_stock_list(page=page, num=num_per_page, sort=sort_key, asc=asc)
            if not stocks:
                break
            for s in stocks:
                sym = str(s.get("symbol", ""))
                if sym and sym not in seen_symbols:
                    seen_symbols.add(sym)
                    all_stocks.append(s)
    return all_stocks


# Pre-defined universe of important A-share stocks (CSI 300 major components)
# Used as ultimate fallback when all APIs fail
_FALLBACK_UNIVERSE = [
    "600519", "601318", "600036", "000858", "000333", "600276", "601166",
    "600900", "601398", "600048", "000651", "601012", "600887", "600030",
    "601888", "000002", "600585", "000568", "600309", "601601", "000725",
    "600104", "601688", "300750", "603259", "002415", "600031", "000001",
    "600690", "002304", "601899", "600438", "002714", "601857", "600050",
    "601225", "600089", "000538", "002352", "601668", "300059", "002142",
    "002475", "600570", "601633", "000661", "600809", "601138", "600016",
    "002027", "000063", "600346", "603501", "601236", "002230", "002049",
    "600588", "601390", "002032", "600763", "000876", "601728", "002841",
    "600019", "002594", "600741", "601669", "300760", "002460", "603986",
    "002466", "001979", "600176", "603288", "600703", "601111", "002007",
    "600000", "601919", "000776", "000166", "300015", "601628", "600009",
    "002241", "600132", "002601", "002938", "600406", "003816", "002311",
    "000977", "601088", "000895", "002555", "600760", "601816", "600196",
    "688981",
]


def screen_stocks(
    strategy: str = "auto",
    risk_profile: dict | None = None,
    top_n: int = 20,
) -> list[dict]:
    """Multi-round funnel screening for A-share stocks.

    Round 1: Broad market fetch (~800-1000 stocks via Sina multi-sort)
    Round 2: Basic filter (PE/PB/market cap/liquidity hard cutoffs)
    Round 3: Strategy scoring + ranking → top 50 candidates
    Round 4: Technical validation with kline data → final top_n

    Falls back to AKShare or Tencent curated universe if Sina fails.
    """
    if risk_profile is None:
        risk_profile = RISK_PROFILES["medium"]

    pe_min, pe_max = risk_profile.get("preferred_pe_range", (0, 50))
    pb_min, pb_max = risk_profile.get("preferred_pb_range", (0, 8))
    min_cap = risk_profile.get("min_market_cap", 20e8)

    # ── Round 1: Broad market fetch ──
    # Fast-probe Sina first to avoid long hangs when API is blocked
    if _probe_sina(timeout=4):
        sina_stocks = _fetch_sina_all_pages(max_pages=12, num_per_page=80)
        if sina_stocks:
            # Round 2+3: filter + score, get top 50 for technical validation
            round3 = _score_sina_stocks(sina_stocks, strategy, risk_profile, top_n=50)
            # Round 4: technical validation with klines
            return _technical_validation(round3, top_n)

    # ── Fallback: AKShare ──
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is not None and not df.empty:
            round3 = _score_akshare_stocks(df, strategy, risk_profile, top_n=50)
            return _technical_validation(round3, top_n)
    except Exception:
        pass

    # ── Fallback: Tencent batch quotes on curated universe ──
    return _screen_from_tencent_universe(strategy, risk_profile, top_n)


def _fetch_klines_via_bridge(symbols: list[str], count: int = 90) -> dict[str, list[dict]]:
    """Batch-fetch klines through the RECEIVER/BRIDGE pipeline.

    Returns {symbol: [bar_dicts]} where each bar has the same shape as
    akshare_provider.get_klines() output (timestamp in ms, OHLCV).
    Returns empty dict if bridge is unreachable or Windows QMT is offline.
    """
    if not symbols:
        return {}

    bridge_url = os.environ.get("WINDOWS_XTDATA_URL", "http://127.0.0.1:58088")

    # Convert plain codes to QMT format for bridge
    qmt_codes = []
    plain_map: dict[str, str] = {}  # qmt_code → plain
    for sym in symbols:
        sym = str(sym).strip()
        if "." in sym:
            qmt = sym
            plain = sym.split(".")[0]
        elif sym.startswith("6"):
            qmt = sym + ".SH"
            plain = sym
        elif sym.startswith(("0", "3")):
            qmt = sym + ".SZ"
            plain = sym
        else:
            qmt = sym + ".SH"
            plain = sym
        qmt_codes.append(qmt)
        plain_map[qmt] = plain
        plain_map[plain] = plain  # also map plain→plain for direct lookup

    body = json.dumps({
        "codes": qmt_codes,
        "period": "1d",
        "count": count,
        "timeout": 60,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        f"{bridge_url}/klines",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=70) as resp:
            resp_data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return {}

    if resp_data.get("status") != "ok":
        return {}

    result: dict[str, list[dict]] = {}
    raw_data = resp_data.get("data", {})
    for code, bars in raw_data.items():
        plain = plain_map.get(code, code.split(".")[0])
        converted = []
        for bar in bars:
            date_str = str(bar.get("date", ""))
            if len(date_str) >= 8:
                try:
                    ts = int(datetime.strptime(date_str[:8], "%Y%m%d").timestamp() * 1000)
                except Exception:
                    continue
            else:
                continue
            converted.append({
                "timestamp": ts,
                "open": bar.get("open", 0),
                "high": bar.get("high", 0),
                "low": bar.get("low", 0),
                "close": bar.get("close", 0),
                "volume": bar.get("volume", 0),
            })
        if converted:
            result[plain] = converted
    return result


def _technical_validation(candidates: list[dict], top_n: int) -> list[dict]:
    """Round 4: Validate top candidates with kline technical analysis.

    Fetches 90-day klines for each candidate, computes MA/RSI/trend,
    and adjusts scores based on technical health. Eliminates stocks
    with bearish technicals (e.g. all MAs declining, RSI overbought).

    Data source priority:
      1. Bridge (RECEIVER→BRIDGE→QMT) — batch, fast when Windows QMT is online
      2. akshare (eastmoney→baidu fallback) — per-stock, slower
    """
    top_n = int(top_n)
    subset = candidates[:min(50, len(candidates))]
    symbols = [s["symbol"] for s in subset]

    # ── Try bridge batch fetch first ──
    bridge_klines = _fetch_klines_via_bridge(symbols, count=90)
    if bridge_klines:
        print("[planner] bridge returned klines for %d/%d stocks" % (len(bridge_klines), len(symbols)),
              file=sys.stderr, flush=True)

    validated = []
    for stock in subset:
        try:
            sym = stock["symbol"]
            klines = bridge_klines.get(sym)

            # Fallback to akshare if bridge didn't have this stock
            if not klines or len(klines) < 20:
                from data_providers.akshare_provider import get_klines
                end_date = datetime.now().strftime("%Y%m%d")
                start_dt = datetime.now() - timedelta(days=90)
                start_date = start_dt.strftime("%Y%m%d")
                klines = get_klines(sym, "1d", start_date, end_date, "cn_stock")

            if klines and len(klines) >= 20:
                techs = _compute_technicals_from_klines(klines)
                stock["technicals"] = techs
                trend = techs.get("trend_score", 0)
                rsi = techs.get("rsi14")

                # Boost for strong technicals
                if trend >= 3:
                    stock["score"] += 8
                    stock["reasons"].append(f"趋势强势(均线{trend}线多头)")
                elif trend >= 2:
                    stock["score"] += 4
                    stock["reasons"].append("趋势偏多")

                # RSI health check
                if rsi is not None:
                    if rsi > 80:
                        stock["score"] -= 8
                        stock["reasons"].append(f"RSI超买({rsi:.0f})")
                    elif rsi < 25:
                        stock["score"] -= 3
                        stock["reasons"].append(f"RSI超卖({rsi:.0f})")
                    elif 40 < rsi < 65:
                        stock["score"] += 3
                        stock["reasons"].append("RSI健康区间")

                # Momentum validation
                m20 = techs.get("momentum_20d", 0)
                if m20 > 15:
                    stock["score"] += 5
                    stock["reasons"].append(f"20日涨{m20:.1f}%")
                elif m20 < -15:
                    stock["score"] -= 5
                    stock["reasons"].append(f"20日跌{m20:.1f}%")

                # Volume/volatility check
                vol = techs.get("volatility", 0)
                if vol > 0.05:
                    stock["reasons"].append("高波动")
                    stock["score"] -= 2

                # Volume-price confirmation with tier analysis
                vr = techs.get("volume_ratio", 1.0)
                m5 = techs.get("momentum_5d", 0)
                pp = techs.get("price_position", 0.5)
                vr_tier, vr_desc = _classify_volume_ratio(vr)

                if vr_tier == "extreme_high":
                    # 量比>5: almost always manipulator
                    if pp > 0.7 and m5 < 3:
                        stock["score"] -= 6
                        stock["reasons"].append(f"量比{vr:.1f}极端+高位滞涨(庄家出货)")
                    elif pp < 0.3 and m5 > 0:
                        stock["score"] += 4
                        stock["reasons"].append(f"量比{vr:.1f}极端+低位上涨(主力强建仓)")
                    else:
                        stock["score"] -= 3
                        stock["reasons"].append(f"量比{vr:.1f}极端异常(高度警惕)")
                elif vr_tier == "surge":
                    # 量比3-5: significant action
                    if m5 > 0 and pp < 0.5:
                        stock["score"] += 4
                        stock["reasons"].append(f"量比{vr:.1f}急剧放量+上涨确认")
                    elif m5 < -2 or pp > 0.7:
                        stock["score"] -= 4
                        stock["reasons"].append(f"量比{vr:.1f}急剧放量+高位/下跌(出货风险)")
                    else:
                        stock["score"] += 1
                        stock["reasons"].append(f"量比{vr:.1f}急剧放量(关注方向)")
                elif vr_tier == "strong_expand":
                    # 量比2-3: breakout/turning
                    if m5 > 0:
                        stock["score"] += 4
                        stock["reasons"].append("明显放量上涨确认")
                    elif m5 < -2:
                        stock["score"] -= 3
                        stock["reasons"].append("明显放量下跌警惕")
                elif vr_tier == "mild_expand":
                    # 量比1.2-2: normal strengthening
                    if m5 > 0:
                        stock["score"] += 2
                        stock["reasons"].append("温和放量上涨")
                elif vr_tier == "extreme_low":
                    # 量比<0.5: dead volume
                    if pp < 0.3:
                        stock["score"] += 2
                        stock["reasons"].append("极度缩量+低位(卖压枯竭)")
                    elif pp > 0.7:
                        stock["score"] -= 2
                        stock["reasons"].append("极度缩量+高位(动力不足)")
                    else:
                        stock["score"] -= 1
                        stock["reasons"].append("极度缩量(关注度低)")
                elif vr_tier == "mild_shrink" and m5 < 0:
                    stock["score"] -= 1
                    stock["reasons"].append("缩量阴跌")

                # Price position
                if pp < 0.2:
                    stock["score"] += 2
                    stock["reasons"].append("处于20日低位")
                elif pp > 0.9:
                    stock["score"] -= 2
                    stock["reasons"].append("接近20日高点")

                # ── Manipulation pattern detection (跟庄/避庄) ──
                manip = _detect_manipulation_patterns(klines)
                if manip["pattern"] != "neutral":
                    stock["score"] += manip["score_adj"]
                    stock["reasons"].extend(manip["signals"])
                    stock["manipulation"] = {
                        "pattern": manip["pattern"],
                        "confidence": manip["confidence"],
                        "signals": manip["signals"],
                    }

                # ── Lianban (连板) scoring ──
                try:
                    from engines.lianban import (
                        board_quality_score,
                        count_consecutive_boards,
                    )
                    lb_count = count_consecutive_boards(klines, sym)
                    if lb_count >= 2:
                        bqs = board_quality_score(klines, sym)
                        quality = bqs.get("total_score", 0)
                        stock["lianban"] = {
                            "board_count": lb_count,
                            "quality_score": quality,
                            "warnings": bqs.get("warnings", []),
                        }
                        # Score boost based on quality and board count
                        if quality >= 70:
                            stock["score"] += min(lb_count, 4) * 4
                            stock["reasons"].append(
                                f"连板{lb_count}板(质量{quality:.0f}分)")
                        elif quality >= 40:
                            stock["score"] += min(lb_count, 4) * 2
                            stock["reasons"].append(
                                f"连板{lb_count}板(质量一般)")
                        else:
                            # Low quality boards = risky
                            stock["score"] -= 2
                            stock["reasons"].append(
                                f"连板{lb_count}板(质量差，风险高)")
                    elif lb_count == 1:
                        # Single board: mild boost for momentum/swing
                        stock["score"] += 2
                        stock["reasons"].append("首板涨停")
                except Exception:
                    pass

                # ── Lianban pool enrichment (今日涨停池) ──
                try:
                    lb_pool = _get_lianban_data()
                    pool_entry = lb_pool["code_map"].get(sym)
                    if pool_entry:
                        lb_today = pool_entry.get("lian_ban_count", 0)
                        seal_amt = pool_entry.get("seal_amount", 0)
                        open_cnt = pool_entry.get("open_count", 0)
                        if lb_today >= 2:
                            stock["score"] += lb_today * 3
                            stock["reasons"].append(
                                f"涨停池{lb_today}连板")
                        if seal_amt > 1e8:
                            stock["score"] += 3
                            stock["reasons"].append(
                                f"封单{seal_amt / 1e8:.1f}亿")
                        if open_cnt >= 3:
                            stock["score"] -= 4
                            stock["reasons"].append(
                                f"炸板{open_cnt}次(封板不牢)")
                except Exception:
                    pass
        except Exception:
            pass

        # ── Cross-module enrichment (Round 5) ──
        try:
            # Hot stocks: boost stocks with high social heat
            hot = _get_hot_stocks()
            if sym in hot["code_set"]:
                rank = hot["code_rank"].get(sym, 50)
                if rank <= 10:
                    stock["score"] += 5
                    stock["reasons"].append(f"热股榜第{rank}名")
                elif rank <= 30:
                    stock["score"] += 2
                    stock["reasons"].append(f"热股榜第{rank}名")

            # Upcoming earnings: flag stocks with imminent earnings release
            earnings = _get_upcoming_earnings()
            if sym in earnings["code_set"]:
                date_str = earnings["code_date"].get(sym, "")
                stock["score"] -= 3
                stock["reasons"].append(f"财报将于{date_str}披露(不确定性)")
                stock["earningsWarning"] = date_str

            # Macro regime adjustment
            regime = _get_macro_regime()
            regime_type = regime.get("regime", "unknown")
            if regime_type == "recession":
                # Defensive: penalize high-beta momentum plays
                if stock.get("changePct", 0) > 5:
                    stock["score"] -= 3
                    stock["reasons"].append("宏观衰退期，高波动标的风险加大")
            elif regime_type == "stagflation":
                if stock.get("pe", 0) > 40:
                    stock["score"] -= 2
                    stock["reasons"].append("滞胀期，高估值承压")
            elif regime_type == "recovery":
                if stock.get("changePct", 0) > 0:
                    stock["score"] += 2
                    stock["reasons"].append("复苏期，顺势加分")
        except Exception:
            pass

        stock["score"] = round(stock["score"], 1)
        validated.append(stock)

    # Re-sort after technical adjustments
    validated.sort(key=lambda x: x["score"], reverse=True)
    return validated[:top_n]


def _score_sina_stocks(
    stocks: list[dict],
    strategy: str,
    risk_profile: dict,
    top_n: int,
) -> list[dict]:
    """Score stocks from Sina Finance data."""
    import random
    pe_min, pe_max = risk_profile.get("preferred_pe_range", (0, 50))
    pb_min, pb_max = risk_profile.get("preferred_pb_range", (0, 8))
    min_cap = risk_profile.get("min_market_cap", 20e8)

    candidates = []
    for s in stocks:
        symbol_raw = str(s.get("symbol", ""))
        # Extract code from "sh600519" or "sz000001"
        code = symbol_raw[2:] if len(symbol_raw) > 2 and symbol_raw[:2] in ("sh", "sz") else symbol_raw
        name = str(s.get("name", ""))

        # Skip Beijing exchange (less liquid), ST, delisted stocks
        if symbol_raw.startswith("bj"):
            continue
        if "ST" in name or "退" in name or not code:
            continue
        if code.startswith("8") or code.startswith("4"):
            continue

        price = _safe_float(s.get("trade"))
        pe = _safe_float(s.get("per"))
        pb = _safe_float(s.get("pb"))
        mkt_cap = _safe_float(s.get("mktcap")) * 1e4  # Sina returns 万元
        nmc = _safe_float(s.get("nmc")) * 1e4  # 流通市值(万元)
        change_pct = _safe_float(s.get("changepercent"))
        turnover = _safe_float(s.get("turnoverratio"))
        volume = _safe_float(s.get("volume"))
        amount = _safe_float(s.get("amount"))
        high = _safe_float(s.get("high"))
        low = _safe_float(s.get("low"))
        open_price = _safe_float(s.get("open"))
        settlement = _safe_float(s.get("settlement"))  # prev close

        if price <= 0:
            continue
        if mkt_cap < min_cap and mkt_cap > 0:
            continue
        if pe_max > 0 and pe > 0 and (pe < pe_min or pe > pe_max):
            continue
        if pb_max > 0 and pb > 0 and (pb < pb_min or pb > pb_max):
            continue
        # During pre-market / opening auction turnoverRate may be 0; only
        # skip if volume is also 0 (truly no data).
        pre_market = turnover <= 0
        if pre_market and volume <= 0:
            continue

        # Calculate amplitude
        amplitude = ((high - low) / settlement * 100) if settlement > 0 else 0

        # Score based on strategy (with slight randomization to diversify results)
        score = random.uniform(0, 1.5)  # base jitter to prevent identical rankings
        reasons = []

        if strategy in ("momentum", "auto", "mixed"):
            if change_pct > 2:
                score += min(change_pct, 10) * 1.0
                reasons.append(f"涨{change_pct:.1f}%")
            # Turnover bell-curve: healthy 2-8%, penalize extremes
            if 2 < turnover <= 8:
                score += 4
                reasons.append(f"换手率健康({turnover:.1f}%)")
            elif 8 < turnover <= 15:
                score += 1
                reasons.append(f"换手较高({turnover:.1f}%)")
            elif turnover > 15:
                score -= 4
                reasons.append(f"换手率过高({turnover:.0f}%)投机风险")
            elif turnover <= 0.5:
                score -= 2
                reasons.append("流动性差")
            if amount > 5e8:  # >5亿成交额
                score += 3
                reasons.append("活跃")

        # Small-cap + high turnover = manipulation risk
        if nmc > 0 and nmc < 50e8 and turnover > 10:
            score -= 3
            reasons.append(f"小盘高换手({turnover:.0f}%)疑似庄股")
        elif nmc > 0 and nmc < 30e8 and turnover > 8:
            score -= 2
            reasons.append(f"小盘换手偏高({turnover:.0f}%)")

        if strategy in ("value", "auto", "mixed"):
            if 5 < pe < 20:
                score += (20 - pe) * 0.5
                reasons.append(f"PE={pe:.1f}(低估)")
            if 0 < pb < 2:
                score += (2 - pb) * 3
                reasons.append(f"PB={pb:.1f}(低估)")
            if mkt_cap > 100e8:
                score += 2
                reasons.append("大市值")

        if strategy in ("growth", "auto", "mixed"):
            if pe > 20 and pe < 50 and change_pct > 0:
                score += 3
                reasons.append("成长性PE")
            if amount > 3e8:
                score += 1

        if strategy in ("swing", "auto"):
            if amplitude > 3:
                score += min(amplitude, 10) * 0.5
                reasons.append(f"振幅{amplitude:.1f}%")
            if -5 < change_pct < -1:
                score += 4
                reasons.append("回调买入机会")

        if strategy in ("manipulation",):
            # Pre-screen for manipulation detection: pass candidates to Round 4
            # where _detect_manipulation_patterns() does Wyckoff/VSA analysis
            score += 5  # base score to ensure candidates reach Round 4
            if 2 < turnover <= 12:
                score += 3
                reasons.append(f"换手率适中({turnover:.1f}%)")
            elif turnover > 12:
                score += 1
                reasons.append(f"换手率偏高({turnover:.0f}%)")
            if amount > 2e8:
                score += 2
                reasons.append("成交活跃")
            # Mid-small cap preferred (manipulation more common)
            if 0 < nmc < 100e8:
                score += 2
                reasons.append("中小盘(跟庄标的)")
            if amplitude > 2:
                score += min(amplitude, 8) * 0.3
                reasons.append(f"振幅{amplitude:.1f}%")

        if strategy in ("event_catalyst",):
            # Event-driven: boost stocks mapped to US tech counterparts
            ec = _get_event_catalyst_data()
            cat_info = ec["code_info"].get(code)
            sc_info = ec["scarcity"].get(code, {})
            if cat_info:
                us_count = len(cat_info["us_symbols"])
                score += us_count * 12
                us_list = ", ".join(sorted(cat_info["us_symbols"])[:3])
                reasons.append(f"美股对标: {us_list}")
                score += len(cat_info["sectors"]) * 3
                reasons.append(f"覆盖{len(cat_info['sectors'])}个细分领域")
            if sc_info:
                sc_val = sc_info.get("scarcity_score", 0)
                if sc_val >= 0.8:
                    score += sc_val * 15
                    tags = sc_info.get("tags", [])[:2]
                    reasons.append(f"高稀缺性: {', '.join(tags)}")
                elif sc_val > 0:
                    score += sc_val * 8
            # Real-time US movers: extra boost for actively triggered catalysts
            us_movers = _get_us_movers()
            triggered_info = us_movers["triggered_codes"].get(code)
            if triggered_info:
                max_chg = triggered_info["max_change"]
                us_list_rt = ", ".join(triggered_info["us_symbols"][:3])
                score += max_chg * 3
                reasons.append(f"实时催化: {us_list_rt}异动{max_chg:.1f}%")
            # Prefer moderate change and healthy turnover
            if 1 < change_pct < 7:
                score += 3
                reasons.append(f"涨幅适中({change_pct:.1f}%)")
            if 2 < turnover <= 10:
                score += 2
                reasons.append(f"换手率{turnover:.1f}%")
            if amount > 3e8:
                score += 2
                reasons.append("成交活跃")

        # Churning detection: high turnover + high amplitude + small net change (对倒)
        if turnover > 8 and amplitude > 4 and abs(change_pct) < 1:
            score -= 3
            reasons.append(f"量大振荡价不动(疑似对倒: 换手{turnover:.0f}%振幅{amplitude:.1f}%)")

        if score > 0:
            candidates.append({
                "symbol": code,
                "name": name,
                "price": price,
                "pe": pe,
                "pb": pb,
                "totalMarketCap": mkt_cap,
                "changePct": change_pct,
                "change5d": 0,
                "change20d": 0,
                "change60d": 0,
                "turnoverRate": turnover,
                "volumeRatio": 0,
                "amplitude": round(amplitude, 2),
                "score": round(score, 2),
                "reasons": reasons[:5],
            })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_n]


def _score_akshare_stocks(
    df,
    strategy: str,
    risk_profile: dict,
    top_n: int,
) -> list[dict]:
    """Score stocks from akshare DataFrame (original logic)."""
    import random
    pe_min, pe_max = risk_profile.get("preferred_pe_range", (0, 50))
    pb_min, pb_max = risk_profile.get("preferred_pb_range", (0, 8))
    min_cap = risk_profile.get("min_market_cap", 20e8)

    candidates = []
    for _, row in df.iterrows():
        code = str(row.get("代码", ""))
        name = str(row.get("名称", ""))
        if "ST" in name or "退" in name or not code:
            continue
        if code.startswith("8") or code.startswith("4"):
            continue

        price = _safe_float(row.get("最新价"))
        pe = _safe_float(row.get("市盈率-动态"))
        pb = _safe_float(row.get("市净率"))
        total_cap = _safe_float(row.get("总市值"))
        change_pct = _safe_float(row.get("涨跌幅"))
        turnover = _safe_float(row.get("换手率"))
        volume_ratio = _safe_float(row.get("量比"))
        amplitude = _safe_float(row.get("振幅"))
        change_5d = _safe_float(row.get("5日涨跌幅"))
        change_20d = _safe_float(row.get("20日涨跌幅"))
        change_60d = _safe_float(row.get("60日涨跌幅"))
        circ_cap = _safe_float(row.get("流通市值"))

        # During pre-market / opening auction turnoverRate may be 0;
        # only skip if volume is also 0 (truly no data).
        if price <= 0:
            continue
        if turnover <= 0 and _safe_float(row.get("成交量")) <= 0:
            continue
        if total_cap < min_cap:
            continue
        if pe_max > 0 and (pe <= pe_min or pe > pe_max):
            continue
        if pb_max > 0 and (pb <= pb_min or pb > pb_max):
            continue

        score = random.uniform(0, 1.5)  # base jitter
        reasons = []

        if strategy in ("momentum", "auto", "mixed"):
            if change_20d > 5:
                score += min(change_20d, 30) * 0.3
                reasons.append(f"20日涨{change_20d:.1f}%")
            if change_5d > 0:
                score += min(change_5d, 15) * 0.5
                reasons.append(f"5日涨{change_5d:.1f}%")
            # Volume ratio tier scoring
            vr_tier, _ = _classify_volume_ratio(volume_ratio) if volume_ratio > 0 else ("normal", "")
            if vr_tier == "extreme_high":
                # 量比>5: extreme — check if pump or distribution
                if change_pct > 5:
                    score += 3
                    reasons.append(f"量比{volume_ratio:.1f}极端+大涨(可能急拉)")
                elif abs(change_pct) < 2:
                    score -= 4
                    reasons.append(f"量比{volume_ratio:.1f}极端+滞涨(疑似对倒)")
                else:
                    score -= 2
                    reasons.append(f"量比{volume_ratio:.1f}极端异常")
            elif vr_tier == "surge":
                if change_pct > 0:
                    score += min(volume_ratio, 5) * 1.5
                    reasons.append(f"量比{volume_ratio:.1f}急剧放量+上涨")
                else:
                    score -= 2
                    reasons.append(f"量比{volume_ratio:.1f}急剧放量+下跌(出货)")
            elif vr_tier in ("strong_expand", "mild_expand"):
                if change_pct > 0:
                    score += min(volume_ratio, 5) * 2
                    reasons.append(f"量比{volume_ratio:.1f}")
            elif vr_tier == "extreme_low":
                score -= 1
                reasons.append(f"量比{volume_ratio:.1f}极度缩量")
            # Turnover bell-curve: healthy 2-8%, penalize extremes
            if 2 < turnover <= 8:
                score += 4
                reasons.append(f"换手率健康({turnover:.1f}%)")
            elif 8 < turnover <= 15:
                score += 1
                reasons.append(f"换手较高({turnover:.1f}%)")
            elif turnover > 15:
                score -= 4
                reasons.append(f"换手率过高({turnover:.0f}%)投机风险")
            elif turnover <= 0.5:
                score -= 2
                reasons.append("流动性差")

        # Small-cap + high turnover = manipulation risk
        cap_for_check = circ_cap if circ_cap > 0 else total_cap
        if cap_for_check > 0 and cap_for_check < 50e8 and turnover > 10:
            score -= 3
            reasons.append(f"小盘高换手({turnover:.0f}%)疑似庄股")
        elif cap_for_check > 0 and cap_for_check < 30e8 and turnover > 8:
            score -= 2
            reasons.append(f"小盘换手偏高({turnover:.0f}%)")

        if strategy in ("value", "auto", "mixed"):
            if 5 < pe < 20:
                score += (20 - pe) * 0.5
                reasons.append(f"PE={pe:.1f}(低估)")
            if 0 < pb < 2:
                score += (2 - pb) * 3
                reasons.append(f"PB={pb:.1f}(低估)")
            if total_cap > 100e8:
                score += 2
                reasons.append("大市值")

        if strategy in ("growth", "auto", "mixed"):
            if change_60d > 10:
                score += min(change_60d, 50) * 0.2
                reasons.append(f"60日涨{change_60d:.1f}%")
            if pe > 20 and pe < 50 and change_20d > 0:
                score += 3
                reasons.append("成长性PE")

        if strategy in ("swing", "auto"):
            if amplitude > 3:
                score += min(amplitude, 10) * 0.5
                reasons.append(f"振幅{amplitude:.1f}%")
            if -5 < change_pct < -1 and change_20d > 0:
                score += 5
                reasons.append("回调买入机会")
            # Swing: volume ratio tier for timing
            vr_tier_s, _ = _classify_volume_ratio(volume_ratio) if volume_ratio > 0 else ("normal", "")
            if vr_tier_s in ("strong_expand", "surge") and change_pct > 0:
                score += 3
                reasons.append(f"量比{volume_ratio:.1f}放量(波段启动)")
            elif vr_tier_s == "extreme_high":
                score -= 2
                reasons.append(f"量比{volume_ratio:.1f}极端(波段风险)")

        if strategy in ("manipulation",):
            # Pre-screen for manipulation detection: pass candidates to Round 4
            # where _detect_manipulation_patterns() does Wyckoff/VSA analysis
            score += 5  # base score to ensure candidates reach Round 4
            if 2 < turnover <= 12:
                score += 3
                reasons.append(f"换手率适中({turnover:.1f}%)")
            elif turnover > 12:
                score += 1
                reasons.append(f"换手率偏高({turnover:.0f}%)")
            # Volume ratio tier for manipulation detection
            vr_tier_m, _ = _classify_volume_ratio(volume_ratio) if volume_ratio > 0 else ("normal", "")
            if vr_tier_m in ("strong_expand", "surge", "extreme_high"):
                score += 3
                reasons.append(f"量比{volume_ratio:.1f}放量(关注庄家)")
            # Mid-small cap preferred (manipulation more common)
            cap_for_manip = circ_cap if circ_cap > 0 else total_cap
            if 0 < cap_for_manip < 100e8:
                score += 2
                reasons.append("中小盘(跟庄标的)")
            if amplitude > 2:
                score += min(amplitude, 8) * 0.3
                reasons.append(f"振幅{amplitude:.1f}%")

        if strategy in ("event_catalyst",):
            # Event-driven: boost stocks mapped to US tech counterparts
            ec = _get_event_catalyst_data()
            cat_info = ec["code_info"].get(code)
            sc_info = ec["scarcity"].get(code, {})
            if cat_info:
                us_count = len(cat_info["us_symbols"])
                score += us_count * 12
                us_list = ", ".join(sorted(cat_info["us_symbols"])[:3])
                reasons.append(f"美股对标: {us_list}")
                score += len(cat_info["sectors"]) * 3
                reasons.append(f"覆盖{len(cat_info['sectors'])}个细分领域")
            if sc_info:
                sc_val = sc_info.get("scarcity_score", 0)
                if sc_val >= 0.8:
                    score += sc_val * 15
                    tags = sc_info.get("tags", [])[:2]
                    reasons.append(f"高稀缺性: {', '.join(tags)}")
                elif sc_val > 0:
                    score += sc_val * 8
            # Real-time US movers: extra boost for actively triggered catalysts
            us_movers = _get_us_movers()
            triggered_info = us_movers["triggered_codes"].get(code)
            if triggered_info:
                max_chg = triggered_info["max_change"]
                us_list_rt = ", ".join(triggered_info["us_symbols"][:3])
                score += max_chg * 3
                reasons.append(f"实时催化: {us_list_rt}异动{max_chg:.1f}%")
            # Prefer moderate change and healthy turnover
            if 1 < change_pct < 7:
                score += 3
                reasons.append(f"涨幅适中({change_pct:.1f}%)")
            if 2 < turnover <= 10:
                score += 2
                reasons.append(f"换手率{turnover:.1f}%")
            # Volume ratio for event catalyst
            vr_tier_e, _ = _classify_volume_ratio(volume_ratio) if volume_ratio > 0 else ("normal", "")
            if vr_tier_e in ("mild_expand", "strong_expand", "surge"):
                score += 3
                reasons.append(f"量比{volume_ratio:.1f}放量(资金进场)")

        # Churning detection: high turnover + high amplitude + small net change (对倒)
        if turnover > 8 and amplitude > 4 and abs(change_pct) < 1:
            score -= 3
            reasons.append(f"量大振荡价不动(疑似对倒: 换手{turnover:.0f}%振幅{amplitude:.1f}%)")

        if score > 0:
            candidates.append({
                "symbol": code,
                "name": name,
                "price": price,
                "pe": pe,
                "pb": pb,
                "totalMarketCap": total_cap,
                "changePct": change_pct,
                "change5d": change_5d,
                "change20d": change_20d,
                "change60d": change_60d,
                "turnoverRate": turnover,
                "volumeRatio": volume_ratio,
                "amplitude": amplitude,
                "score": round(score, 2),
                "reasons": reasons[:5],
            })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates[:top_n]


def _screen_from_tencent_universe(
    strategy: str,
    risk_profile: dict,
    top_n: int,
) -> list[dict]:
    """Ultimate fallback: batch-fetch Tencent quotes for curated stock universe."""
    pe_min, pe_max = risk_profile.get("preferred_pe_range", (0, 50))
    pb_min, pb_max = risk_profile.get("preferred_pb_range", (0, 8))
    min_cap = risk_profile.get("min_market_cap", 20e8)

    # Try cache symbols first, then fallback universe
    universe = list(_FALLBACK_UNIVERSE)
    try:
        from cache.stock_cache import get_cache
        cache = get_cache()
        cached = cache.get_all_symbols("1d")
        if cached and len(cached) > 50:
            universe = cached[:300]
    except Exception:
        pass

    # Fetch Tencent quotes in batches of 50
    all_quotes: dict[str, dict] = {}
    for i in range(0, len(universe), 50):
        batch = universe[i:i + 50]
        all_quotes.update(_fetch_tencent_batch(batch))

    import random
    candidates = []
    for sym, q in all_quotes.items():
        if q["price"] <= 0:
            continue
        pe = q.get("pe", 0)
        pb = q.get("pb", 0)
        mkt_cap = q.get("totalMarketCap", 0)
        if mkt_cap > 0 and mkt_cap < min_cap:
            continue
        if pe_max > 0 and pe > 0 and (pe < pe_min or pe > pe_max):
            continue
        if pb_max > 0 and pb > 0 and (pb < pb_min or pb > pb_max):
            continue

        score = random.uniform(0, 1.5)  # base jitter
        reasons = []
        change_pct = q.get("changePct", 0)
        turnover = q.get("turnoverRate", 0)
        volume_ratio = q.get("volumeRatio", 0)
        amplitude = q.get("amplitude", 0)

        # During pre-market / opening auction turnoverRate may be 0 even
        # though volume > 0.  Only skip if both are zero (truly no data).
        pre_market = turnover <= 0
        if pre_market and q.get("volume", 0) <= 0:
            continue

        if strategy in ("momentum", "auto", "mixed"):
            if change_pct > 2:
                score += min(change_pct, 10) * 1.0
                reasons.append(f"涨{change_pct:.1f}%")
            # Volume ratio tier scoring
            vr_tier_t, _ = _classify_volume_ratio(volume_ratio) if volume_ratio > 0 else ("normal", "")
            if vr_tier_t == "extreme_high":
                if abs(change_pct) < 2:
                    score -= 4
                    reasons.append(f"量比{volume_ratio:.1f}极端+滞涨(疑似对倒)")
                elif change_pct > 5:
                    score += 3
                    reasons.append(f"量比{volume_ratio:.1f}极端+大涨")
                else:
                    score -= 2
                    reasons.append(f"量比{volume_ratio:.1f}极端异常")
            elif vr_tier_t == "surge":
                if change_pct > 0:
                    score += min(volume_ratio, 5) * 1.5
                    reasons.append(f"量比{volume_ratio:.1f}急剧放量+上涨")
                else:
                    score -= 2
                    reasons.append(f"量比{volume_ratio:.1f}急剧放量+下跌")
            elif vr_tier_t in ("strong_expand", "mild_expand") and change_pct > 0:
                score += min(volume_ratio, 5) * 2
                reasons.append(f"量比{volume_ratio:.1f}")
            elif vr_tier_t == "extreme_low":
                score -= 1
                reasons.append(f"量比{volume_ratio:.1f}极度缩量")
            # Turnover bell-curve: healthy 2-8%, penalize extremes
            if 2 < turnover <= 8:
                score += 4
                reasons.append(f"换手率健康({turnover:.1f}%)")
            elif 8 < turnover <= 15:
                score += 1
                reasons.append(f"换手较高({turnover:.1f}%)")
            elif turnover > 15:
                score -= 4
                reasons.append(f"换手率过高({turnover:.0f}%)投机风险")
            elif turnover <= 0.5:
                score -= 2
                reasons.append("流动性差")

        # Small-cap + high turnover = manipulation risk
        circ_cap_t = q.get("circulatingMarketCap", 0)
        cap_for_check = circ_cap_t if circ_cap_t > 0 else mkt_cap
        if cap_for_check > 0 and cap_for_check < 50e8 and turnover > 10:
            score -= 3
            reasons.append(f"小盘高换手({turnover:.0f}%)疑似庄股")
        elif cap_for_check > 0 and cap_for_check < 30e8 and turnover > 8:
            score -= 2
            reasons.append(f"小盘换手偏高({turnover:.0f}%)")

        if strategy in ("value", "auto", "mixed"):
            if 5 < pe < 20:
                score += (20 - pe) * 0.5
                reasons.append(f"PE={pe:.1f}(低估)")
            if 0 < pb < 2:
                score += (2 - pb) * 3
                reasons.append(f"PB={pb:.1f}(低估)")

        if strategy in ("growth", "auto", "mixed"):
            if pe > 20 and pe < 50 and change_pct > 0:
                score += 3
                reasons.append("成长性PE")

        if strategy in ("swing", "auto"):
            if amplitude > 3:
                score += min(amplitude, 10) * 0.5
                reasons.append(f"振幅{amplitude:.1f}%")
            if -5 < change_pct < -1:
                score += 4
                reasons.append("回调机会")

        if strategy in ("manipulation",):
            # Pre-screen for manipulation detection: pass candidates to Round 4
            score += 5  # base score to ensure candidates reach Round 4
            if 2 < turnover <= 12:
                score += 3
                reasons.append(f"换手率适中({turnover:.1f}%)")
            elif turnover > 12:
                score += 1
                reasons.append(f"换手率偏高({turnover:.0f}%)")
            vr_tier_m, _ = _classify_volume_ratio(volume_ratio) if volume_ratio > 0 else ("normal", "")
            if vr_tier_m in ("strong_expand", "surge", "extreme_high"):
                score += 3
                reasons.append(f"量比{volume_ratio:.1f}放量(关注庄家)")
            cap_for_manip = circ_cap_t if circ_cap_t > 0 else mkt_cap
            if 0 < cap_for_manip < 100e8:
                score += 2
                reasons.append("中小盘(跟庄标的)")
            if amplitude > 2:
                score += min(amplitude, 8) * 0.3
                reasons.append(f"振幅{amplitude:.1f}%")

        if strategy in ("event_catalyst",):
            ec = _get_event_catalyst_data()
            cat_info = ec["code_info"].get(sym)
            sc_info = ec["scarcity"].get(sym, {})
            if cat_info:
                us_count = len(cat_info["us_symbols"])
                score += us_count * 12
                us_list = ", ".join(sorted(cat_info["us_symbols"])[:3])
                reasons.append(f"美股对标: {us_list}")
                score += len(cat_info["sectors"]) * 3
                reasons.append(f"覆盖{len(cat_info['sectors'])}个细分领域")
            if sc_info:
                sc_val = sc_info.get("scarcity_score", 0)
                if sc_val >= 0.8:
                    score += sc_val * 15
                    tags = sc_info.get("tags", [])[:2]
                    reasons.append(f"高稀缺性: {', '.join(tags)}")
                elif sc_val > 0:
                    score += sc_val * 8
            # Real-time US movers: extra boost for actively triggered catalysts
            us_movers = _get_us_movers()
            triggered_info = us_movers["triggered_codes"].get(sym)
            if triggered_info:
                max_chg = triggered_info["max_change"]
                us_list_rt = ", ".join(triggered_info["us_symbols"][:3])
                score += max_chg * 3
                reasons.append(f"实时催化: {us_list_rt}异动{max_chg:.1f}%")
            if 1 < change_pct < 7:
                score += 3
                reasons.append(f"涨幅适中({change_pct:.1f}%)")
            if 2 < turnover <= 10:
                score += 2
                reasons.append(f"换手率{turnover:.1f}%")
            vr_tier_e, _ = _classify_volume_ratio(volume_ratio) if volume_ratio > 0 else ("normal", "")
            if vr_tier_e in ("mild_expand", "strong_expand", "surge"):
                score += 3
                reasons.append(f"量比{volume_ratio:.1f}放量(资金进场)")

        # Churning detection: high turnover + high amplitude + small net change (对倒)
        if turnover > 8 and amplitude > 4 and abs(change_pct) < 1:
            score -= 3
            reasons.append(f"量大振荡价不动(疑似对倒: 换手{turnover:.0f}%振幅{amplitude:.1f}%)")

        if score > 0:
            candidates.append({
                "symbol": sym,
                "name": q["name"],
                "price": q["price"],
                "pe": pe,
                "pb": pb,
                "totalMarketCap": mkt_cap,
                "changePct": change_pct,
                "turnoverRate": turnover,
                "volumeRatio": volume_ratio,
                "amplitude": amplitude,
                "score": round(score, 2),
                "reasons": reasons[:5],
            })

    candidates.sort(key=lambda x: x["score"], reverse=True)
    # Round 4: technical validation (same as primary path)
    return _technical_validation(candidates[:50], top_n)




def generate_plan(
    initial_capital: float,
    target_capital: float,
    timeframe_months: int = 12,
    risk_tolerance: str = "medium",
    strategy_preference: str = "auto",
) -> dict:
    """Generate a comprehensive investment plan."""
    profile = RISK_PROFILES.get(risk_tolerance, RISK_PROFILES["medium"])

    # Calculate required return
    required_return = (target_capital / initial_capital - 1) * 100
    monthly_return_needed = ((target_capital / initial_capital) ** (1.0 / timeframe_months) - 1) * 100
    annual_return = required_return * (12.0 / timeframe_months) if timeframe_months != 12 else required_return

    # Determine strategy mix based on required return & risk
    if strategy_preference == "auto":
        if annual_return <= 30:
            strategy = "value"
            strategy_label = "价值投资"
            trade_frequency = "低频 (月度调仓)"
        elif annual_return <= 60:
            strategy = "growth"
            strategy_label = "成长投资"
            trade_frequency = "中频 (双周调仓)"
        elif annual_return <= 100:
            strategy = "momentum"
            strategy_label = "趋势动量"
            trade_frequency = "中高频 (周度调仓)"
        else:
            strategy = "swing"
            strategy_label = "波段交易"
            trade_frequency = "高频 (日度监控，3-5日换仓)"
    else:
        strategy = strategy_preference
        labels = {"momentum": "趋势动量", "value": "价值投资", "growth": "成长投资",
                  "swing": "波段交易", "mixed": "混合策略",
                  "event_catalyst": "事件催化"}
        strategy_label = labels.get(strategy, strategy)
        freq_map = {"value": "低频 (月度调仓)", "growth": "中频 (双周调仓)",
                    "momentum": "中高频 (周度调仓)", "swing": "高频 (日度监控)",
                    "mixed": "中频 (双周调仓)",
                    "event_catalyst": "中高频 (尾盘选股, T+1介入)"}
        trade_frequency = freq_map.get(strategy, "中频")

    # Risk assessment
    if annual_return > 100:
        risk_level = "极高"
        risk_warning = "年化收益目标超过100%，属于极高风险。建议降低目标或延长时间。大部分基金经理年化20-30%已属优秀。"
        feasibility = "困难"
    elif annual_return > 50:
        risk_level = "高"
        risk_warning = "年化收益目标较高，需要精准的选股和择时能力。建议多样化投资并严格止损。"
        feasibility = "有挑战"
    elif annual_return > 20:
        risk_level = "中"
        risk_warning = "年化收益目标合理，通过优质成长股+适度波段操作有望实现。"
        feasibility = "可行"
    else:
        risk_level = "低"
        risk_warning = "年化收益目标保守，通过优质蓝筹+红利策略稳健可实现。"
        feasibility = "很可能"

    # Screen stocks
    candidates = screen_stocks(strategy, profile, top_n=30)

    # Build portfolio allocation
    max_pos = profile["max_positions"]
    min_pos = profile["min_positions"]
    num_positions = min(max_pos, max(min_pos, len(candidates)))
    selected = candidates[:num_positions]

    # Position sizing: score-weighted allocation
    # Filter out stocks with score <= 0 (not investment-worthy)
    selected = [s for s in selected if s["score"] > 0]
    num_positions = len(selected)
    total_score = sum(s["score"] for s in selected) or 1
    portfolio = []
    for stock in selected:
        weight = stock["score"] / total_score
        # Clamp to max single position
        weight = min(weight, profile["max_single_position_pct"])
        allocation = initial_capital * weight
        shares = int(allocation / stock["price"] / 100) * 100  # Round to 100 shares
        if shares < 100:
            shares = 100
        actual_cost = shares * stock["price"]
        portfolio.append({
            "symbol": stock["symbol"],
            "name": stock["name"],
            "price": stock["price"],
            "shares": shares,
            "cost": round(actual_cost, 2),
            "weight": round(weight * 100, 2),
            "pe": stock["pe"],
            "pb": stock.get("pb", 0),
            "score": round(stock["score"], 1),
            "reasons": stock.get("reasons", []),
            "stopLoss": round(stock["price"] * (1 - profile["stop_loss_pct"]), 2),
            "takeProfit": round(stock["price"] * (1 + profile["take_profit_pct"]), 2),
        })

    total_invested = sum(p["cost"] for p in portfolio)
    cash_reserve = initial_capital - total_invested

    # Monthly milestones
    milestones = []
    for m in range(1, timeframe_months + 1):
        target_at_month = initial_capital * ((1 + monthly_return_needed / 100) ** m)
        milestones.append({
            "month": m,
            "targetCapital": round(target_at_month, 2),
            "targetReturn": round((target_at_month / initial_capital - 1) * 100, 2),
        })

    # Trading rules
    rules = {
        "stopLoss": f"单股止损 {profile['stop_loss_pct'] * 100:.0f}%，总账户回撤超 {profile['stop_loss_pct'] * 200:.0f}% 暂停交易",
        "takeProfit": f"单股止盈 {profile['take_profit_pct'] * 100:.0f}%，分批止盈(先卖1/3，再卖1/3)",
        "positionSize": f"单只股票不超过总资金 {profile['max_single_position_pct'] * 100:.0f}%",
        "rebalance": f"ÿ {profile['rebalance_days']} 天重新审视持仓，必要时调仓",
        "cashReserve": "始终保留 10-20% 现金作为补仓/抄底资金",
        "entryRule": "均线多头排列 + 量能放大 + RSI不超买时介入",
        "exitRule": "跌破20日均线 + 缩量阴跌 + RSI低于30考虑减仓",
    }

    # Monitoring schedule
    monitoring = {
        "preMarket": "09:00 - 检查重要新闻、板块异动、隔夜外盘",
        "openWatch": "09:30-10:00 - 观察开盘竞价和前30分钟走势",
        "midDay": "11:00 - 检查持仓股价格&止损位",
        "afternoon": "14:00-14:30 - 判断尾盘趋势，决定是否操作",
        "close": "15:00 - 收盘复盘，记录当日盈亏",
        "evening": "20:00 - 分析板块轮动，筛选次日候选股",
    }

    return {
        "plan": {
            "initialCapital": initial_capital,
            "targetCapital": target_capital,
            "timeframeMonths": timeframe_months,
            "requiredReturn": round(required_return, 2),
            "monthlyReturnNeeded": round(monthly_return_needed, 2),
            "annualReturn": round(annual_return, 2),
            "riskTolerance": risk_tolerance,
            "riskLevel": risk_level,
            "riskWarning": risk_warning,
            "feasibility": feasibility,
            "strategy": strategy,
            "strategyLabel": strategy_label,
            "tradeFrequency": trade_frequency,
            "riskProfile": profile["label"],
        },
        "portfolio": portfolio,
        "summary": {
            "totalPositions": len(portfolio),
            "totalInvested": round(total_invested, 2),
            "cashReserve": round(cash_reserve, 2),
            "cashReservePct": round(cash_reserve / initial_capital * 100, 2),
        },
        "milestones": milestones,
        "rules": rules,
        "monitoring": monitoring,
        "candidatePool": candidates[num_positions:num_positions + 10],
        "generatedAt": datetime.now().isoformat(),
    }


def monitor_portfolio(
    plan: dict,
) -> dict:
    """Monitor current portfolio against the plan, generate signals.

    Input plan should contain 'portfolio' with symbol/shares/cost/stopLoss/takeProfit.
    """
    portfolio = plan.get("portfolio", [])
    if not portfolio:
        return {"error": "无持仓数据", "signals": []}

    symbols = [p["symbol"] for p in portfolio]
    quotes = _fetch_tencent_batch(symbols)

    signals: list[dict] = []
    positions: list[dict] = []
    total_cost = 0.0
    total_value = 0.0

    for pos in portfolio:
        sym = pos["symbol"]
        q = quotes.get(sym, {})
        current_price = q.get("price", 0)
        if current_price <= 0:
            continue

        cost = pos.get("cost", 0)
        shares = pos.get("shares", 0)
        buy_price = pos.get("price", 0)
        stop_loss = pos.get("stopLoss", 0)
        take_profit = pos.get("takeProfit", 0)

        current_value = current_price * shares
        pnl = current_value - cost
        pnl_pct = (current_price / buy_price - 1) * 100 if buy_price > 0 else 0

        total_cost += cost
        total_value += current_value

        position_info = {
            "symbol": sym,
            "name": q.get("name", pos.get("name", sym)),
            "shares": shares,
            "buyPrice": buy_price,
            "currentPrice": current_price,
            "cost": cost,
            "currentValue": round(current_value, 2),
            "pnl": round(pnl, 2),
            "pnlPct": round(pnl_pct, 2),
            "stopLoss": stop_loss,
            "takeProfit": take_profit,
            "changePct": q.get("changePct", 0),
            "turnoverRate": q.get("turnoverRate", 0),
            "volumeRatio": q.get("volumeRatio", 0),
        }
        positions.append(position_info)

        # Generate signals
        if stop_loss > 0 and current_price <= stop_loss:
            signals.append({
                "symbol": sym,
                "name": position_info["name"],
                "type": "STOP_LOSS",
                "action": "卖出",
                "urgency": "紧急",
                "currentPrice": current_price,
                "triggerPrice": stop_loss,
                "message": f"触及止损位 {stop_loss}，建议立即卖出",
                "pnlPct": round(pnl_pct, 2),
            })
        elif take_profit > 0 and current_price >= take_profit:
            signals.append({
                "symbol": sym,
                "name": position_info["name"],
                "type": "TAKE_PROFIT",
                "action": "减仓",
                "urgency": "重要",
                "currentPrice": current_price,
                "triggerPrice": take_profit,
                "message": f"达到止盈位 {take_profit}，建议分批止盈",
                "pnlPct": round(pnl_pct, 2),
            })
        elif pnl_pct < -3:
            signals.append({
                "symbol": sym,
                "name": position_info["name"],
                "type": "WARNING",
                "action": "关注",
                "urgency": "注意",
                "currentPrice": current_price,
                "triggerPrice": stop_loss,
                "message": f"浮亏 {pnl_pct:.1f}%，距止损位还有 {((current_price / stop_loss - 1) * 100):.1f}%",
                "pnlPct": round(pnl_pct, 2),
            })

        # Volume anomaly detection with tier analysis
        vol_ratio = q.get("volumeRatio", 0)
        turnover = q.get("turnoverRate", 0)
        amplitude = q.get("amplitude", 0)
        change_pct = q.get("changePct", 0)

        if vol_ratio > 0:
            vr_tier_m, vr_desc_m = _classify_volume_ratio(vol_ratio)
            if vr_tier_m == "extreme_high":
                # 量比>5: extreme alert with context
                if abs(change_pct) < 2:
                    signals.append({
                        "symbol": sym,
                        "name": position_info["name"],
                        "type": "VOLUME_EXTREME",
                        "action": "警惕",
                        "urgency": "紧急",
                        "currentPrice": current_price,
                        "message": f"量比{vol_ratio:.1f}极端+滞涨(高度疑似庄家对倒，考虑减仓)",
                    })
                elif change_pct > 5:
                    signals.append({
                        "symbol": sym,
                        "name": position_info["name"],
                        "type": "VOLUME_EXTREME",
                        "action": "关注",
                        "urgency": "重要",
                        "currentPrice": current_price,
                        "message": f"量比{vol_ratio:.1f}极端+大涨{change_pct:.1f}%(可能急拉诱多，注意出货风险)",
                    })
                else:
                    signals.append({
                        "symbol": sym,
                        "name": position_info["name"],
                        "type": "VOLUME_EXTREME",
                        "action": "警惕",
                        "urgency": "重要",
                        "currentPrice": current_price,
                        "message": f"量比{vol_ratio:.1f}极端异常(庄家行为，密切关注)",
                    })
            elif vr_tier_m == "surge":
                signals.append({
                    "symbol": sym,
                    "name": position_info["name"],
                    "type": "VOLUME_SPIKE",
                    "action": "关注",
                    "urgency": "注意",
                    "currentPrice": current_price,
                    "message": f"量比{vol_ratio:.1f}急剧放量，涨跌{change_pct:.1f}%，关注主力动向",
                })
            elif vr_tier_m == "extreme_low" and pnl_pct > 5:
                signals.append({
                    "symbol": sym,
                    "name": position_info["name"],
                    "type": "VOLUME_DRIED",
                    "action": "关注",
                    "urgency": "注意",
                    "currentPrice": current_price,
                    "message": f"量比{vol_ratio:.1f}极度缩量+盈利{pnl_pct:.1f}%(上涨动力不足，注意保护利润)",
                })

        # ── Intraday manipulation detection (盘中庄家行为识别) ──
        # Churning: high volume + high turnover + small net move (对倒)
        if turnover > 10 and vol_ratio > 2 and abs(change_pct) < 1 and amplitude > 3:
            signals.append({
                "symbol": sym,
                "name": position_info["name"],
                "type": "CHURNING_ALERT",
                "action": "警惕",
                "urgency": "重要",
                "currentPrice": current_price,
                "message": f"疑似对倒: 换手{turnover:.1f}%+量比{vol_ratio:.1f}+振幅{amplitude:.1f}%但涨跌仅{change_pct:.1f}%",
            })

        # Distribution: high turnover + price at profit + large amplitude (出货)
        if turnover > 8 and pnl_pct > 10 and amplitude > 4:
            signals.append({
                "symbol": sym,
                "name": position_info["name"],
                "type": "DISTRIBUTION_ALERT",
                "action": "减仓",
                "urgency": "重要",
                "currentPrice": current_price,
                "message": f"主力出货信号: 换手{turnover:.1f}%+振幅{amplitude:.1f}%，盈利{pnl_pct:.1f}%建议分批止盈",
                "pnlPct": round(pnl_pct, 2),
            })

        # Kline-based manipulation pattern detection (if kline data available)
        try:
            from data_providers.akshare_provider import get_klines
            end_date = datetime.now().strftime("%Y%m%d")
            start_dt = datetime.now() - timedelta(days=90)
            start_date = start_dt.strftime("%Y%m%d")
            klines = get_klines(sym, "1d", start_date, end_date, "cn_stock")
            if klines and len(klines) >= 30:
                manip = _detect_manipulation_patterns(klines)
                if manip["pattern"] == "distribution" and manip["confidence"] >= 0.5:
                    signals.append({
                        "symbol": sym,
                        "name": position_info["name"],
                        "type": "DISTRIBUTION_PATTERN",
                        "action": "减仓",
                        "urgency": "重要",
                        "currentPrice": current_price,
                        "message": "K线检测到主力出货形态: " + "; ".join(manip["signals"]),
                        "confidence": manip["confidence"],
                    })
                elif manip["pattern"] == "churning" and manip["confidence"] >= 0.5:
                    signals.append({
                        "symbol": sym,
                        "name": position_info["name"],
                        "type": "CHURNING_PATTERN",
                        "action": "警惕",
                        "urgency": "注意",
                        "currentPrice": current_price,
                        "message": "K线检测到对倒迹象: " + "; ".join(manip["signals"]),
                        "confidence": manip["confidence"],
                    })
                elif manip["pattern"] == "accumulation" and manip["confidence"] >= 0.5:
                    signals.append({
                        "symbol": sym,
                        "name": position_info["name"],
                        "type": "ACCUMULATION_SIGNAL",
                        "action": "持有",
                        "urgency": "参考",
                        "currentPrice": current_price,
                        "message": "K线检测到主力建仓: " + "; ".join(manip["signals"]),
                        "confidence": manip["confidence"],
                    })
        except Exception:
            pass

    total_pnl = total_value - total_cost
    total_pnl_pct = (total_value / total_cost - 1) * 100 if total_cost > 0 else 0

    return {
        "positions": positions,
        "signals": signals,
        "summary": {
            "totalCost": round(total_cost, 2),
            "totalValue": round(total_value, 2),
            "totalPnl": round(total_pnl, 2),
            "totalPnlPct": round(total_pnl_pct, 2),
            "positionCount": len(positions),
            "signalCount": len(signals),
            "urgentSignals": len([s for s in signals if s.get("urgency") == "紧急"]),
        },
        "timestamp": datetime.now().isoformat(),
    }


def update_screening(
    strategy: str = "auto",
    risk_tolerance: str = "medium",
    top_n: int = 20,
) -> dict:
    """Run stock screening and return ranked candidates with technical data."""
    profile = RISK_PROFILES.get(risk_tolerance, RISK_PROFILES["medium"])
    candidates = screen_stocks(strategy, profile, top_n)

    # Enhance top candidates with kline technicals
    enhanced = []
    for stock in candidates[:top_n]:
        try:
            from data_providers.akshare_provider import get_klines
            end_date = datetime.now().strftime("%Y%m%d")
            start_dt = datetime.now() - timedelta(days=90)
            start_date = start_dt.strftime("%Y%m%d")
            klines = get_klines(stock["symbol"], "1d", start_date, end_date, "cn_stock")
            if klines and len(klines) >= 20:
                techs = _compute_technicals_from_klines(klines)
                stock["technicals"] = techs
                # Boost score based on technicals
                if techs.get("trend_score", 0) >= 3:
                    stock["score"] += 5
                    stock["reasons"].append("趋势强势")
                if techs.get("rsi14") and 30 < techs["rsi14"] < 70:
                    stock["score"] += 2
                    stock["reasons"].append("RSI健康")
        except Exception:
            pass
        enhanced.append(stock)

    # Re-sort after technical boost
    enhanced.sort(key=lambda x: x["score"], reverse=True)

    # Institutional + sentiment + analyst enrichment for top 10 (parallel)
    top_symbols = [s["symbol"] for s in enhanced[:10]]
    sm_results: dict[str, dict | None] = {}
    sent_results: dict[str, dict | None] = {}
    analyst_results: dict[str, dict | None] = {}
    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        with ThreadPoolExecutor(max_workers=8) as pool:
            # Submit all three types of enrichment in one pool
            sm_futures = {pool.submit(_get_institutional_signal, sym): ("sm", sym) for sym in top_symbols}
            sent_futures = {pool.submit(_get_stock_sentiment, sym): ("sent", sym) for sym in top_symbols}
            analyst_futures = {pool.submit(_get_analyst_consensus, sym): ("analyst", sym) for sym in top_symbols}
            all_futures = {**sm_futures, **sent_futures, **analyst_futures}

            for future in as_completed(all_futures, timeout=20):
                kind, sym = all_futures[future]
                try:
                    result = future.result()
                    if kind == "sm":
                        sm_results[sym] = result
                    elif kind == "sent":
                        sent_results[sym] = result
                    else:
                        analyst_results[sym] = result
                except Exception:
                    pass
    except Exception as e:
        print(f"[planner] enrichment parallel fetch failed: {e}", file=sys.stderr, flush=True)

    for stock in enhanced[:10]:
        sym = stock["symbol"]
        # ── Institutional smart money ──
        try:
            sm = sm_results.get(sym)
            if sm and sm.get("smart_money_score") is not None:
                sm_score = sm["smart_money_score"]
                stock["smartMoney"] = {
                    "score": sm_score,
                    "conclusion": sm.get("conclusion", ""),
                    "details": sm.get("details", []),
                }
                if sm_score >= 70:
                    stock["score"] += 5
                    stock["reasons"].append(f"机构看多(Smart Money {sm_score}分)")
                elif sm_score <= 30:
                    stock["score"] -= 3
                    stock["reasons"].append(f"机构看空(Smart Money {sm_score}分)")
        except Exception:
            pass

        # ── Per-stock social sentiment ──
        try:
            sent = sent_results.get(sym)
            if sent and sent.get("composite_score") is not None:
                cs = sent["composite_score"]
                level = sent.get("level", "unknown")
                stock["sentiment"] = {
                    "score": cs,
                    "level": level,
                    "interpretation": sent.get("interpretation", ""),
                }
                if cs >= 70:
                    stock["score"] += 3
                    stock["reasons"].append(f"舆情高热(情绪{cs:.0f}分)")
                elif cs <= 20:
                    stock["score"] -= 2
                    stock["reasons"].append(f"舆情冷淡(情绪{cs:.0f}分)")
        except Exception:
            pass

        # ── Analyst consensus ──
        try:
            ar = analyst_results.get(sym)
            if ar and ar.get("distribution"):
                dist = ar["distribution"]
                buy_cnt = dist.get("buy", 0) + dist.get("overweight", 0)
                sell_cnt = dist.get("underweight", 0) + dist.get("sell", 0)
                total = buy_cnt + dist.get("hold", 0) + sell_cnt
                stock["analystConsensus"] = {
                    "buy": buy_cnt,
                    "hold": dist.get("hold", 0),
                    "sell": sell_cnt,
                    "total": total,
                }
                if total >= 3:
                    buy_ratio = buy_cnt / total
                    if buy_ratio >= 0.7:
                        stock["score"] += 4
                        stock["reasons"].append(f"分析师看好({buy_cnt}/{total})")
                    elif buy_ratio <= 0.3 and sell_cnt > buy_cnt:
                        stock["score"] -= 3
                        stock["reasons"].append(f"分析师看空({sell_cnt}卖出/{total})")
        except Exception:
            pass

    # Final re-sort after enrichment
    enhanced.sort(key=lambda x: x["score"], reverse=True)

    # ── Macro regime context + position guidance ──
    regime = _get_macro_regime()
    regime_type = regime.get("regime", "unknown")
    adj = REGIME_ADJUSTMENTS.get(regime_type, REGIME_ADJUSTMENTS["recovery"])
    position_scale = adj["position_scale"]

    # Compute regime-adjusted position guidance
    base_max_pos_pct = profile.get("max_single_position_pct", 0.20)
    adjusted_max_pos_pct = round(base_max_pos_pct * position_scale, 3)
    base_max_positions = profile.get("max_positions", 8)
    # In recession/stagflation: reduce number of positions to concentrate on quality
    if position_scale < 0.8:
        adjusted_max_positions = max(2, int(base_max_positions * position_scale))
    else:
        adjusted_max_positions = base_max_positions

    return {
        "candidates": enhanced,
        "strategy": strategy,
        "riskProfile": profile["label"],
        "macroRegime": regime.get("regime", "unknown"),
        "macroDescription": regime.get("description", ""),
        "lianbanPoolCount": _get_lianban_data().get("count", 0),
        "hotStocksCount": _get_hot_stocks().get("count", 0),
        "positionGuidance": {
            "regimeAdjustment": adj["description"],
            "positionScale": position_scale,
            "maxSinglePositionPct": adjusted_max_pos_pct,
            "maxPositions": adjusted_max_positions,
            "baseMaxSinglePositionPct": base_max_pos_pct,
            "baseMaxPositions": base_max_positions,
        },
        "timestamp": datetime.now().isoformat(),
    }


def rebalance_portfolio(
    plan: dict,
    new_candidates: list[str] | None = None,
) -> dict:
    """Compare current portfolio against market data + new candidates,
    generate actionable buy/sell/add/reduce/hold recommendations.

    Input:
      plan: Same format as monitor, with portfolio[].symbol/shares/cost/stopLoss/takeProfit
      new_candidates: Optional list of new screener codes to consider buying
    """
    portfolio = plan.get("portfolio", [])
    if not portfolio:
        return {"error": "无持仓数据", "actions": [], "summary": {}}

    held_symbols = [p["symbol"] for p in portfolio]
    # Fetch quotes for held + candidates
    all_symbols = list(set(held_symbols + (new_candidates or [])))
    quotes = _fetch_tencent_batch(all_symbols)

    initial_capital = plan.get("initialCapital", 0)
    # Calculate current total portfolio value
    total_value = 0.0
    for pos in portfolio:
        sym = pos["symbol"]
        q = quotes.get(sym, {})
        price = q.get("price", pos.get("price", 0))
        total_value += price * pos.get("shares", 0)

    if total_value <= 0:
        total_value = initial_capital or 500000

    actions: list[dict] = []
    held_set = set(held_symbols)

    for pos in portfolio:
        sym = pos["symbol"]
        q = quotes.get(sym, {})
        current_price = q.get("price", 0)
        if current_price <= 0:
            continue

        buy_price = pos.get("price", 0)
        shares = pos.get("shares", 0)
        stop_loss = pos.get("stopLoss", 0)
        take_profit = pos.get("takeProfit", 0)
        position_value = current_price * shares
        current_weight = position_value / total_value * 100 if total_value > 0 else 0
        pnl_pct = (current_price / buy_price - 1) * 100 if buy_price > 0 else 0
        change_pct = q.get("changePct", 0)
        name = q.get("name", pos.get("name", sym))

        # Decision logic
        action_type = "hold"
        reason = "持仓状态正常"
        target_weight = current_weight
        suggested_shares = 0

        # SELL: stop-loss triggered or severe deterioration
        if stop_loss > 0 and current_price <= stop_loss:
            action_type = "sell"
            reason = f"触及止损位 {stop_loss}，建议清仓"
            suggested_shares = shares
            target_weight = 0
        elif pnl_pct <= -15:
            action_type = "sell"
            reason = f"亏损 {pnl_pct:.1f}% 超过15%，建议止损清仓"
            suggested_shares = shares
            target_weight = 0
        # REDUCE: take-profit or overweight
        elif take_profit > 0 and current_price >= take_profit:
            action_type = "reduce"
            reduce_shares = max(100, int(shares * 0.5 / 100) * 100)
            reason = f"达止盈位 {take_profit}，建议减仓50%"
            suggested_shares = reduce_shares
            target_weight = current_weight * 0.5
        elif current_weight > 25:
            action_type = "reduce"
            # Reduce to ~15% weight
            target_shares = int(total_value * 0.15 / current_price / 100) * 100
            reduce_shares = max(0, shares - target_shares)
            reason = f"仓位占比 {current_weight:.1f}% 偏高，建议减至15%"
            suggested_shares = reduce_shares
            target_weight = 15
        # ADD: strong momentum + underweight
        elif pnl_pct > 5 and current_weight < 10 and change_pct > 0:
            action_type = "add"
            # Add to ~12% weight
            target_shares = int(total_value * 0.12 / current_price / 100) * 100
            add_shares = max(100, target_shares - shares)
            reason = f"盈利 {pnl_pct:.1f}% 趋势良好，仓位偏低可加仓"
            suggested_shares = add_shares
            target_weight = 12
        else:
            reason = f"盈亏 {pnl_pct:.1f}%，仓位 {current_weight:.1f}%，状态正常"

        actions.append({
            "action": action_type,
            "symbol": sym,
            "name": name,
            "currentPrice": current_price,
            "changePct": round(change_pct, 2),
            "pnlPct": round(pnl_pct, 2),
            "currentWeight": round(current_weight, 2),
            "targetWeight": round(target_weight, 2),
            "suggestedShares": suggested_shares,
            "reason": reason,
        })

    # NEW BUY: candidates not already held
    for sym in (new_candidates or []):
        if sym in held_set:
            continue
        q = quotes.get(sym, {})
        price = q.get("price", 0)
        if price <= 0:
            continue
        name = q.get("name", sym)
        change_pct = q.get("changePct", 0)
        # Suggest ~5% of portfolio for new positions
        target_value = total_value * 0.05
        suggested_shares = max(100, int(target_value / price / 100) * 100)
        actions.append({
            "action": "buy",
            "symbol": sym,
            "name": name,
            "currentPrice": price,
            "changePct": round(change_pct, 2),
            "pnlPct": 0,
            "currentWeight": 0,
            "targetWeight": 5,
            "suggestedShares": suggested_shares,
            "reason": f"选股新入选，建议试探性建仓 {suggested_shares} 股",
        })

    # Sort: sell > reduce > add > buy > hold
    order = {"sell": 0, "reduce": 1, "add": 2, "buy": 3, "hold": 4}
    actions.sort(key=lambda a: order.get(a["action"], 5))

    summary = {
        "sellCount": len([a for a in actions if a["action"] == "sell"]),
        "reduceCount": len([a for a in actions if a["action"] == "reduce"]),
        "holdCount": len([a for a in actions if a["action"] == "hold"]),
        "addCount": len([a for a in actions if a["action"] == "add"]),
        "buyCount": len([a for a in actions if a["action"] == "buy"]),
        "totalValue": round(total_value, 2),
    }

    return {
        "actions": actions,
        "summary": summary,
        "timestamp": datetime.now().isoformat(),
    }


if __name__ == "__main__":
    _raw = os.environ.get("QUANT_CONFIG") or sys.stdin.read()
    config = json.loads(_raw)
    action = config.get("action", "plan")

    try:
        if action == "plan":
            result = generate_plan(
                initial_capital=config.get("initial_capital", 500000),
                target_capital=config.get("target_capital", 1000000),
                timeframe_months=config.get("timeframe_months", 12),
                risk_tolerance=config.get("risk_tolerance", "medium"),
                strategy_preference=config.get("strategy_preference", "auto"),
            )
        elif action == "screen":
            result = update_screening(
                strategy=config.get("strategy", "auto"),
                risk_tolerance=config.get("risk_tolerance", "medium"),
                top_n=int(config.get("top_n", 20)),
            )
        elif action == "monitor":
            result = monitor_portfolio(plan=config.get("plan", {}))
        elif action == "rebalance":
            result = rebalance_portfolio(
                plan=config.get("plan", {}),
                new_candidates=config.get("newCandidates", []),
            )
        else:
            result = {"error": f"未知操作: {action}"}
    except Exception as e:
        import traceback
        result = {"error": str(e), "traceback": traceback.format_exc()}

    print(json.dumps(result, ensure_ascii=False))
