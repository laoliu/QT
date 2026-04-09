"""
Stock Recommendation Engine — Recommends initial positions based on
backtest results, real-time quotes, and fundamental scoring.

Input (stdin JSON):
  {
    "action": "recommend",
    "capital": 500000,
    "strategy": "mixed",
    "risk_tolerance": "high",
    "rebalance_days": 5,
    "symbols": ["600519","000858",...],  // stock pool
    "start_date": "2025-06-01",         // backtest period
    "end_date": "2026-03-01"
  }

Output: JSON with recommendations, allocation, and operation tips.
"""

import json
import sys
import urllib.request
import urllib.error
import os
from datetime import datetime, timedelta

# Import backtest engine for running the backtest
import importlib.util

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _get_lot_size(code: str) -> int:
    """Minimum order lot size: 科创板 688xxx = 200, others = 100."""
    bare = code.replace(".SH", "").replace(".SS", "").replace(".SZ", "").upper()
    if bare.startswith("688") and len(bare) == 6:
        return 200
    return 100
_PYTHON_ROOT = os.path.dirname(_SCRIPT_DIR)
sys.path.insert(0, _PYTHON_ROOT)

# Apply network timeout before any akshare/requests-based imports.
# akshare calls requests internally without timeout, causing infinite hangs.
try:
    import requests as _req
    if not getattr(_req.Session.request, "_timeout_patched", False):
        _orig_req = _req.Session.request
        def _patched_request(self, method, url, **kwargs):
            kwargs.setdefault("timeout", 20)
            return _orig_req(self, method, url, **kwargs)
        _patched_request._timeout_patched = True  # type: ignore[attr-defined]
        _req.Session.request = _patched_request  # type: ignore[assignment]
except ImportError:
    pass

from engines.chip_distribution import fetch_chip_distribution, score_chip_distribution

def _load_backtest_engine():
    spec = importlib.util.spec_from_file_location(
        "backtest_engine", os.path.join(_SCRIPT_DIR, "backtest_engine.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Default stock pool (Plan B: blue-chip + tech mix) ──
DEFAULT_POOL = [
    "600519", "000858", "601318", "000333", "300750", "002594",
    "002475", "300124", "300059", "002230", "601012",
]

STOCK_NAMES = {
    "600519": "贵州茅台", "000858": "五粮液", "601318": "中国平安",
    "000333": "美的集团", "300750": "宁德时代", "002594": "比亚迪",
    "002475": "立讯精密", "300124": "汇川技术",
    "300059": "东方财富", "002230": "科大讯飞", "601012": "隆基绿能",
}


def _get_prefix(code: str) -> str:
    if code.startswith("6"):
        return "sh"
    return "sz"


def _fetch_sina_quotes(codes: list[str]) -> dict:
    """Fetch real-time quotes from Sina Finance."""
    sina_codes = ",".join(_get_prefix(c) + c for c in codes)
    url = "https://hq.sinajs.cn/list=" + sina_codes
    req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
    try:
        resp = urllib.request.urlopen(req, timeout=15).read().decode("gbk")
    except Exception:
        return {}

    quotes = {}
    for line in resp.strip().split("\n"):
        if "=" not in line:
            continue
        var_part, data_part = line.split("=", 1)
        code = var_part[-6:]
        fields = data_part.strip('";').split(",")
        if len(fields) < 32:
            continue
        prev_close = float(fields[2]) if fields[2] else 0
        price = float(fields[3]) if fields[3] else 0
        change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
        quotes[code] = {
            "name": fields[0],
            "price": price,
            "open": float(fields[1]) if fields[1] else 0,
            "high": float(fields[4]) if fields[4] else 0,
            "low": float(fields[5]) if fields[5] else 0,
            "prevClose": prev_close,
            "changePct": round(change_pct, 2),
            "volume": float(fields[8]) if fields[8] else 0,
            "amount": float(fields[9]) if fields[9] else 0,
        }
    return quotes


def _fetch_tencent_fundamentals(codes: list[str]) -> dict:
    """Fetch PE/PB/market-cap from Tencent Finance."""
    tc_codes = ",".join(_get_prefix(c) + c for c in codes)
    url = "https://qt.gtimg.cn/q=" + tc_codes
    try:
        resp = urllib.request.urlopen(urllib.request.Request(url), timeout=15).read().decode("gbk")
    except Exception:
        return {}

    data = {}
    for line in resp.strip().split(";"):
        if "~" not in line:
            continue
        parts = line.split("~")
        if len(parts) < 50:
            continue
        code = parts[2]
        data[code] = {
            "pe": float(parts[39]) if parts[39] else 0,
            "pb": float(parts[46]) if parts[46] else 0,
            "marketCap": float(parts[45]) if parts[45] else 0,
            "circulatingMarketCap": float(parts[44]) if parts[44] else 0,
            "turnover": float(parts[38]) if parts[38] else 0,
        }
    return data


# ── Technical Analysis Helpers ──

def _compute_technicals(klines: list[dict]) -> dict:
    """Compute MA, RSI, momentum, volatility, volume analysis from kline data."""
    if len(klines) < 20:
        return {}
    closes = [b["close"] for b in klines]
    volumes = [b.get("volume", 0) for b in klines]
    n = len(closes)

    def _sma(data, period):
        if len(data) < period:
            return None
        return sum(data[-period:]) / period

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

    # Trend score: count bullish MA alignment (0-4)
    trend_score = 0
    if ma5 and ma10 and ma5 > ma10:
        trend_score += 1
    if ma10 and ma20 and ma10 > ma20:
        trend_score += 1
    if ma20 and closes[-1] > ma20:
        trend_score += 1
    if ma60 and closes[-1] > ma60:
        trend_score += 1

    # Momentum: 5-day and 20-day returns
    momentum_5d = (closes[-1] / closes[-5] - 1) * 100 if n >= 5 else 0
    momentum_20d = (closes[-1] / closes[-20] - 1) * 100 if n >= 20 else 0

    # Volatility: 20-day realized volatility
    if n >= 20:
        returns = [(closes[i] / closes[i - 1] - 1) for i in range(max(1, n - 20), n)]
        volatility = (sum(r ** 2 for r in returns) / len(returns)) ** 0.5
    else:
        volatility = 0

    # Volume trend: recent 5-day avg vs 20-day avg (volume expansion or contraction)
    vol_5d = _sma(volumes, 5) if len(volumes) >= 5 else None
    vol_20d = _sma(volumes, 20) if len(volumes) >= 20 else None
    volume_ratio = (vol_5d / vol_20d) if vol_5d and vol_20d and vol_20d > 0 else 1.0

    # Price position: current price vs 20-day range
    if n >= 20:
        high_20d = max(closes[-20:])
        low_20d = min(closes[-20:])
        price_position = (closes[-1] - low_20d) / (high_20d - low_20d) if high_20d > low_20d else 0.5
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
        "volume_ratio": round(volume_ratio, 2),
        "price_position": round(price_position, 3),
    }


def _fetch_klines_for_codes(codes: list[str]) -> dict[str, list[dict]]:
    """Fetch 90-day klines for a batch of stock codes concurrently. Returns {code: klines}."""
    result = {}
    try:
        from data_providers.akshare_provider import get_klines
    except ImportError:
        return result

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_dt = datetime.now() - timedelta(days=90)
    start_date = start_dt.strftime("%Y-%m-%d")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def _fetch_one(code: str):
        try:
            klines = get_klines(code, "1d", start_date, end_date, "cn_stock")
            if klines and len(klines) >= 20:
                return code, klines
        except Exception:
            pass
        return code, None

    with ThreadPoolExecutor(max_workers=min(len(codes), 4)) as pool:
        futures = {pool.submit(_fetch_one, c): c for c in codes}
        for fut in as_completed(futures):
            code, klines = fut.result()
            if klines:
                result[code] = klines
    return result


def _score_technicals(techs: dict) -> tuple[float, list[str]]:
    """Score a stock's technical indicators. Returns (score_delta, reasons)."""
    score = 0.0
    reasons = []

    # ── Trend alignment (max +12) ──
    trend = techs.get("trend_score", 0)
    if trend >= 4:
        score += 12
        reasons.append(f"多头排列(均线{trend}线)")
    elif trend >= 3:
        score += 8
        reasons.append(f"趋势强势({trend}线多头)")
    elif trend >= 2:
        score += 4
        reasons.append("趋势偏多")
    elif trend <= 0:
        score -= 4
        reasons.append("趋势偏空")

    # ── RSI health (max +8, min -8) ──
    rsi = techs.get("rsi14")
    if rsi is not None:
        if rsi > 80:
            score -= 8
            reasons.append(f"RSI超买({rsi:.0f})")
        elif rsi > 70:
            score -= 4
            reasons.append(f"RSI偏高({rsi:.0f})")
        elif 25 < rsi < 40:
            score += 6
            reasons.append(f"RSI超卖回升({rsi:.0f})")
        elif rsi <= 25:
            score += 3  # Deep oversold — risky, not full bonus
            reasons.append(f"RSI深度超卖({rsi:.0f})")
        elif 40 <= rsi <= 60:
            score += 8
            reasons.append("RSI健康区间")
        elif 60 < rsi <= 70:
            score += 4
            reasons.append("RSIƫǿ")

    # ── Momentum (max +10, min -6) ──
    m20 = techs.get("momentum_20d", 0)
    m5 = techs.get("momentum_5d", 0)
    if m20 > 15:
        score += 6
        reasons.append(f"20日涨{m20:.1f}%")
    elif m20 > 5:
        score += 4
        reasons.append(f"20日涨{m20:.1f}%")
    elif m20 < -15:
        score -= 6
        reasons.append(f"20日跌{m20:.1f}%")
    elif m20 < -5:
        score -= 3
        reasons.append(f"20日跌{m20:.1f}%")
    # Short-term momentum bonus (complementary, max +4)
    if m5 > 3 and m20 > 0:
        score += 4
        reasons.append(f"短期加速上涨{m5:.1f}%")
    elif m5 < -5:
        score -= 2

    # ── Volatility risk (max -5) ──
    vol = techs.get("volatility", 0)
    if vol > 0.06:
        score -= 5
        reasons.append("高波动风险")
    elif vol > 0.04:
        score -= 2
        reasons.append("波动偏大")
    elif vol < 0.02:
        score += 2
        reasons.append("低波动稳健")

    # ── Volume-price confirmation with tier analysis ──
    vol_ratio = techs.get("volume_ratio", 1.0)
    price_pos = techs.get("price_position", 0.5)

    # Volume ratio tier classification
    if vol_ratio >= 5.0:
        vr_tier = "extreme_high"
    elif vol_ratio >= 3.0:
        vr_tier = "surge"
    elif vol_ratio >= 2.0:
        vr_tier = "strong_expand"
    elif vol_ratio >= 1.2:
        vr_tier = "mild_expand"
    elif vol_ratio >= 0.8:
        vr_tier = "normal"
    elif vol_ratio >= 0.5:
        vr_tier = "mild_shrink"
    else:
        vr_tier = "extreme_low"

    if vr_tier == "extreme_high":
        # 量比>5: extreme, almost always manipulator
        if price_pos > 0.7 and m5 < 3:
            score -= 6
            reasons.append(f"量比{vol_ratio:.1f}极端+高位滞涨(庄家出货)")
        elif price_pos < 0.3 and m5 > 0:
            score += 5
            reasons.append(f"量比{vol_ratio:.1f}极端+低位上涨(主力强建仓)")
        else:
            score -= 3
            reasons.append(f"量比{vol_ratio:.1f}极端异常(高度警惕)")
    elif vr_tier == "surge":
        # 量比3-5: significant action
        if m5 > 0 and price_pos < 0.5:
            score += 5
            reasons.append(f"量比{vol_ratio:.1f}急剧放量+上涨确认")
        elif m5 < -2 or price_pos > 0.7:
            score -= 4
            reasons.append(f"量比{vol_ratio:.1f}急剧放量+高位/下跌(出货风险)")
        else:
            score += 1
            reasons.append(f"量比{vol_ratio:.1f}急剧放量(关注方向)")
    elif vr_tier == "strong_expand":
        if m5 > 0:
            score += 5
            reasons.append("明显放量上涨确认")
        elif m5 < -2:
            score -= 3
            reasons.append("明显放量下跌警惕")
    elif vr_tier == "mild_expand":
        if m5 > 0:
            score += 3
            reasons.append("温和放量上涨")
    elif vr_tier == "extreme_low":
        if price_pos < 0.3:
            score += 2
            reasons.append("极度缩量+低位(卖压枯竭)")
        elif price_pos > 0.7:
            score -= 2
            reasons.append("极度缩量+高位(动力不足)")
        else:
            score -= 1
            reasons.append("极度缩量(关注度低)")
    elif vr_tier == "mild_shrink" and m5 < 0:
        score -= 1
        reasons.append("缩量阴跌")

    # ── Price position within range ──
    if price_pos < 0.2:
        score += 3
        reasons.append("20日低位区间")
    elif price_pos > 0.9:
        score -= 2
        reasons.append("接近20日高点")

    # ── Manipulation pattern detection (跟庄/避庄) ──
    # Accumulation: low price position + volume shrinking (selling exhaustion)
    if price_pos < 0.3 and vr_tier in ("extreme_low", "mild_shrink"):
        score += 3
        reasons.append("低位缩量(卖压衰竭,可能吸筹)")
    # Accumulation: low position + volume picking up (building position)
    elif price_pos < 0.25 and vr_tier in ("mild_expand", "strong_expand"):
        score += 4
        reasons.append("低位放量(主力可能建仓)")
    # Distribution: high position + high volume + down (selling)
    if price_pos > 0.8 and vol_ratio > 1.5 and m5 < 0:
        score -= 5
        reasons.append("高位放量滞涨(出货信号)")
    # Distribution: high position + volume climax
    elif price_pos > 0.85 and vr_tier in ("strong_expand", "surge", "extreme_high"):
        score -= 4
        reasons.append("高位天量(主力可能出货)")

    return round(score, 1), reasons


def _detect_manipulation_patterns(klines: list[dict]) -> dict:
    """Detect manipulation patterns from kline data (Wyckoff/VSA).

    Returns dict with 'pattern', 'score_adj', 'signals', 'confidence'.
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

    lookback = min(60, n)
    high_60 = max(highs[-lookback:])
    low_60 = min(lows[-lookback:])
    price_range = high_60 - low_60
    if price_range <= 0:
        return result
    price_pos = (closes[-1] - low_60) / price_range

    # ── Accumulation (吸筹) ──
    acc_score = 0.0
    acc_signals = []
    up_vols, down_vols = [], []
    for i in range(-20, 0):
        if closes[i] >= closes[i - 1]:
            up_vols.append(volumes[i])
        else:
            down_vols.append(volumes[i])
    if up_vols and down_vols:
        if sum(up_vols) / len(up_vols) > sum(down_vols) / len(down_vols) * 1.3:
            acc_score += 2.0
            acc_signals.append("缩量回调放量上涨(主力承接)")
    if price_pos < 0.3:
        recent_range = (max(highs[-10:]) - min(lows[-10:])) / closes[-10] if closes[-10] > 0 else 1
        if recent_range < 0.06 and avg_vol_20 > avg_vol_60 * 0.8:
            acc_score += 2.5
            acc_signals.append("底部横盘整理")
    if price_pos < 0.25 and avg_vol_20 > avg_vol_60 * 1.3:
        acc_score += 2.0
        acc_signals.append("低位放量(主力可能建仓)")
    shadow_count = sum(1 for i in range(-10, 0)
        if abs(closes[i] - opens[i]) > 0 and (min(opens[i], closes[i]) - lows[i]) > abs(closes[i] - opens[i]) * 1.5)
    if shadow_count >= 3:
        acc_score += 1.5
        acc_signals.append(f"连续下影线({shadow_count}根，有承接)")

    # ── Distribution (出货) ──
    dist_score = 0.0
    dist_signals = []
    if price_pos > 0.7:
        max_vol_idx = max(range(-10, 0), key=lambda i: volumes[i])
        if volumes[max_vol_idx] > avg_vol_20 * 2.5:
            price_move = abs(closes[max_vol_idx] - opens[max_vol_idx]) / opens[max_vol_idx] if opens[max_vol_idx] > 0 else 0
            if price_move < 0.02:
                dist_score += 3.0
                dist_signals.append("高位天量滞涨(主力对倒出货)")
            elif closes[max_vol_idx] < opens[max_vol_idx]:
                dist_score += 2.5
                dist_signals.append("高位放量收阴(出货信号)")
    if price_pos > 0.6:
        upper_shadows = sum(1 for i in range(-10, 0)
            if abs(closes[i] - opens[i]) > 0 and (highs[i] - max(opens[i], closes[i])) > abs(closes[i] - opens[i]) * 1.5)
        if upper_shadows >= 3:
            dist_score += 2.0
            dist_signals.append(f"高位上影线({upper_shadows}根，抛压重)")
    gap_down = sum(1 for i in range(-5, 0) if opens[i] > closes[i - 1] * 1.01 and closes[i] < opens[i] * 0.99)
    if gap_down >= 2:
        dist_score += 2.5
        dist_signals.append(f"连续高开低走({gap_down}次)")

    # ── Churning (对倒) ──
    churn_score = 0.0
    churn_signals = []
    for i in range(-5, 0):
        if volumes[i] > avg_vol_20 * 2 and opens[i] > 0:
            if abs(closes[i] - opens[i]) / opens[i] < 0.01 and (highs[i] - lows[i]) / lows[i] > 0.03:
                churn_score += 2.0
                churn_signals.append("大幅振荡量大价不动(对倒)")
                break

    # ── Volume Ratio Tier Analysis (量比分级与庄家行为) ──
    vol_today = volumes[-1]
    vol_5d_avg = sum(volumes[-6:-1]) / 5 if n >= 6 else avg_vol_20
    vr = vol_today / vol_5d_avg if vol_5d_avg > 0 else 1.0

    if vr >= 5.0:
        price_chg = (closes[-1] / closes[-2] - 1) * 100 if n >= 2 else 0
        if price_pos > 0.7 and price_chg < 3:
            dist_score += 4.0
            dist_signals.append(f"量比{vr:.1f}极端+高位滞涨(对倒出货)")
        elif price_pos > 0.7 and price_chg > 8:
            dist_score += 3.0
            dist_signals.append(f"量比{vr:.1f}极端+急拉(诱多出货)")
        elif price_pos < 0.3 and price_chg > 0:
            acc_score += 3.5
            acc_signals.append(f"量比{vr:.1f}极端+低位上涨(强力建仓)")
        else:
            churn_score += 3.0
            churn_signals.append(f"量比{vr:.1f}极端异常(庄家行为)")
    elif vr >= 3.0:
        price_chg = (closes[-1] / closes[-2] - 1) * 100 if n >= 2 else 0
        if price_pos > 0.7 and price_chg < 1:
            dist_score += 2.5
            dist_signals.append(f"量比{vr:.1f}急剧放量+高位滞涨(出货)")
        elif price_pos < 0.3 and price_chg > 0:
            acc_score += 2.5
            acc_signals.append(f"量比{vr:.1f}急剧放量+低位上涨(建仓)")
        elif abs(price_chg) < 1:
            churn_score += 2.0
            churn_signals.append(f"量比{vr:.1f}急剧放量+价格不动(对倒)")
    elif vr < 0.5:
        if price_pos < 0.3:
            acc_score += 2.0
            acc_signals.append("极度缩量+低位(卖压枯竭,吸筹末期)")
        elif price_pos > 0.7:
            dist_score += 1.0
            dist_signals.append("极度缩量+高位(上涨动力不足)")

    patterns = [("accumulation", acc_score, acc_signals), ("distribution", dist_score, dist_signals), ("churning", churn_score, churn_signals)]
    best = max(patterns, key=lambda p: p[1])
    if best[1] < 1.5:
        return result

    if best[0] == "accumulation":
        score_adj = min(best[1], 8.0)
    elif best[0] == "distribution":
        score_adj = -min(best[1], 8.0)
    else:
        score_adj = -min(best[1], 4.0)

    return {"pattern": best[0], "score_adj": round(score_adj, 1), "signals": best[2], "confidence": round(min(best[1] / 8.0, 1.0), 2)}


def _enrich_decision_fields(
    allocations: list[dict],
    capital: float,
    target_capital: float,
    strategy: str,
    risk_tolerance: str,
    backtest_summary: dict,
) -> None:
    """Add entry/exit/hold/contribution fields to each allocation in-place."""
    # Stop-loss % by risk tolerance
    sl_pct_map = {"low": 0.05, "medium": 0.08, "high": 0.10, "aggressive": 0.12}
    sl_pct = sl_pct_map.get(risk_tolerance, 0.08)

    # Expected hold days by strategy
    hold_days_map = {
        "momentum": 10, "swing": 15, "mixed": 20,
        "growth": 60, "value": 90, "manipulation": 8,
    }
    base_hold = hold_days_map.get(strategy, 20)

    # Clamp win_rate to a realistic range — no strategy wins >70% or <35% consistently
    win_rate = max(0.35, min(0.70, (backtest_summary.get("winRate") or 50) / 100.0))

    # Annualized return clamped to [-30%, +60%] to prevent backtest overfitting
    # from inflating short-period expected returns (e.g. 9-month bull market → 400% annual)
    ann_return = max(-0.30, min(0.60, (backtest_summary.get("annualizedReturn") or 20) / 100.0))

    total_target_return = (target_capital - capital) if target_capital > capital else capital * 0.2
    total_invested = sum(a.get("amount", 0) for a in allocations if a.get("amount", 0) > 0)

    for a in allocations:
        price = a.get("price", 0)
        amount = a.get("amount", 0)
        shares = a.get("shares", 0)
        if price <= 0 or shares <= 0:
            continue

        # ── Entry prices (分批建仓) ──
        cyq = a.get("chipDistribution")
        support = cyq.get("support", 0) if cyq else 0
        entry_1 = round(price, 2)
        entry_2 = round(price * 0.98, 2)  # -2%
        entry_3 = round(price * 0.95, 2)  # -5%
        # Adjust 3rd entry to chip support if it's close
        if support > 0 and abs(entry_3 - support) / price < 0.04:
            entry_3 = round(support, 2)
        a["entryPrices"] = {
            "batch1": {"price": entry_1, "pct": 60, "label": "首批60%"},
            "batch2": {"price": entry_2, "pct": 25, "label": "回调-2%补仓25%"},
            "batch3": {"price": entry_3, "pct": 15, "label": f"回调-5%{'(筹码支撑)' if support > 0 and abs(entry_3 - support) / price < 0.04 else ''}补仓15%"},
        }

        # ── Exit targets (ֹӯ/止损) ──
        sl_price = round(price * (1 - sl_pct), 2)
        tp1 = round(price * 1.10, 2)
        tp2 = round(price * 1.20, 2)
        resistance = cyq.get("resistance", 0) if cyq else 0
        tp_chip = round(resistance, 2) if resistance > price else 0
        a["exitTargets"] = {
            "stopLoss": {"price": sl_price, "pct": round(-sl_pct * 100, 1), "label": "止损"},
            "takeProfit1": {"price": tp1, "pct": 10.0, "label": "ֹӯ1(+10%)"},
            "takeProfit2": {"price": tp2, "pct": 20.0, "label": "ֹӯ2(+20%)"},
        }
        if tp_chip > 0 and tp_chip != tp1 and tp_chip != tp2:
            a["exitTargets"]["chipResistance"] = {
                "price": tp_chip,
                "pct": round((tp_chip / price - 1) * 100, 1),
                "label": "筹码压力位",
            }

        # ── Max loss per position ──
        a["maxLossAmount"] = round(amount * sl_pct, 2)

        # ── Expected hold days ──
        turnover = a.get("turnover", 0)
        hold_adj = base_hold
        if turnover > 10:
            hold_adj = max(5, int(base_hold * 0.6))  # high turnover = shorter hold
        elif turnover < 1:
            hold_adj = int(base_hold * 1.4)  # low turnover = longer hold
        a["expectedHoldDays"] = hold_adj

        # ── Per-stock expected return (individualized, hold-period scaled) ──
        #
        # Three-step model:
        # 1. Base return: scale annualized backtest return to actual hold period
        #    (e.g., 60% annual × 8-day hold ≈ 1.6%, not 74%)
        base_hold_return = (1.0 + ann_return) ** (hold_adj / 252.0) - 1.0

        # 2. Score-based win rate: high-scoring stocks get a slightly better win rate
        #    than low-scoring ones (±10% adjustment around the global win rate)
        stock_score = a.get("score", 50)
        score_factor = (stock_score - 50.0) / 50.0  # roughly -1 to +1
        adj_win_rate = max(0.30, min(0.75, win_rate + score_factor * 0.10))

        # 3. Volatility-based upside spread:
        #    optimistic = base return + 1σ volatility over hold period
        #    Cap at 2.5× stop-loss so a strong bull-market backtest can't inflate it
        #    beyond a level that is achievable in one trade cycle
        techs = a.get("technicals") or {}
        vol_daily = techs.get("volatility") or 0.025  # realized daily volatility
        vol_hold = vol_daily * (hold_adj ** 0.5)      # scale to hold period
        optimistic = min(base_hold_return + vol_hold, sl_pct * 2.5)
        optimistic = max(optimistic, sl_pct * 0.5)    # floor: at least 0.5× stop-loss

        # E[R] = win_rate × upside − (1−win_rate) × downside
        expected_val = adj_win_rate * optimistic - (1.0 - adj_win_rate) * sl_pct

        a["expectedReturn"] = {
            "optimistic": round(optimistic * 100, 1),
            "expected": round(expected_val * 100, 1),
            "pessimistic": round(-sl_pct * 100, 1),
            "expectedAmount": round(amount * expected_val, 2),
        }

    # ── Goal contribution (second pass — weight by each stock's expected PnL) ──
    # Using position-size weighting loses information about which stock contributes more.
    # Instead: contrib_share = this_stock_expected_pnl / total_expected_pnl.
    # Stocks with negative or zero expected value contribute 0 to the target.
    if total_invested > 0 and total_target_return > 0:
        total_pos_pnl = sum(
            max(0.0, a.get("amount", 0) * (a.get("expectedReturn", {}).get("expected", 0) / 100.0))
            for a in allocations
            if a.get("amount", 0) > 0 and a.get("expectedReturn")
        )
        for a in allocations:
            if a.get("amount", 0) <= 0 or not a.get("expectedReturn"):
                continue
            amount = a["amount"]
            exp_pct = a["expectedReturn"]["expected"] / 100.0
            exp_pnl = amount * exp_pct

            weight_of_capital = amount / total_invested

            if total_pos_pnl > 0 and exp_pnl > 0:
                # Contribution share proportional to this stock's expected PnL
                contrib_share = exp_pnl / total_pos_pnl
            else:
                # Fallback: proportional to position size, but no positive contribution
                contrib_share = weight_of_capital if exp_pnl >= 0 else 0.0

            contrib_amount = round(total_target_return * contrib_share, 2)
            a["goalContribution"] = {
                "targetReturn": round(total_target_return, 2),
                "weight": round(weight_of_capital * 100, 1),
                "expectedContrib": contrib_amount,
                "expectedContribPct": round(contrib_amount / total_target_return * 100, 1),
            }


def recommend(
    capital: float = 500000,
    strategy: str = "mixed",
    risk_tolerance: str = "high",
    rebalance_days: int = 5,
    symbols: list[str] | None = None,
    start_date: str = "2025-06-01",
    end_date: str | None = None,
    target_capital: float = 0,
    exclude_star: bool = True,
) -> dict:
    pool = symbols if symbols and len(symbols) > 0 else DEFAULT_POOL

    # Filter out STAR Market (科创板 688xxx) stocks if account lacks permission
    if exclude_star:
        pool = [c for c in pool if not c.startswith("688")]

    # Default end_date = today
    if not end_date:
        end_date = datetime.now().strftime("%Y-%m-%d")

    # ── 1. Run backtest to get final positions ──
    bt_engine = _load_backtest_engine()
    bt_result = bt_engine.run_backtest(
        initial_capital=capital,
        start_date=start_date,
        end_date=end_date,
        strategy=strategy,
        risk_tolerance=risk_tolerance,
        symbols=pool,
        rebalance_days=rebalance_days,
        commission_rate=0.0003,
        stamp_tax=0.001,
    )

    if "error" in bt_result:
        return {"error": bt_result["error"]}

    final_positions = bt_result.get("finalPositions", [])
    summary = bt_result.get("summary", {})
    pos_map = {p["symbol"]: p for p in final_positions}

    # ── 2. Fetch real-time quotes + klines + chip data concurrently ──
    from concurrent.futures import ThreadPoolExecutor, as_completed

    _quotes_result = {}
    _fund_result = {}
    _klines_result = {}
    _cyq_results = {}

    def _fetch_quotes():
        return _fetch_sina_quotes(pool)

    def _fetch_fund():
        return _fetch_tencent_fundamentals(pool)

    def _fetch_klines():
        return _fetch_klines_for_codes(pool)

    def _fetch_cyq_one(code):
        try:
            return code, fetch_chip_distribution(code)
        except Exception:
            return code, None

    with ThreadPoolExecutor(max_workers=min(4 + len(pool), 10)) as executor:
        fq = executor.submit(_fetch_quotes)
        ff = executor.submit(_fetch_fund)
        fk = executor.submit(_fetch_klines)
        cyq_futures = {executor.submit(_fetch_cyq_one, c): c for c in pool}

        try:
            _quotes_result = fq.result(timeout=20)
        except Exception:
            _quotes_result = {}
        try:
            _fund_result = ff.result(timeout=20)
        except Exception:
            _fund_result = {}
        try:
            _klines_result = fk.result(timeout=90)
        except Exception:
            _klines_result = {}
        for fut in as_completed(cyq_futures, timeout=60):
            try:
                code, cyq = fut.result(timeout=20)
                if cyq:
                    _cyq_results[code] = cyq
            except Exception:
                pass

    quotes = _quotes_result
    fundamentals = _fund_result
    klines_map = _klines_result

    # ── 3. Multi-dimensional scoring ──
    scored = []
    for code in pool:
        q = quotes.get(code, {})
        tc = fundamentals.get(code, {})
        price = q.get("price", 0)
        if not price:
            continue

        score = 0.0
        reasons = []

        # --- (A) Backtest signal: position match + cost basis (max +27) ---
        if code in pos_map:
            score += 15
            bt_cost = pos_map[code].get("avgCost", 0)
            if bt_cost > 0:
                vs_cost = (price / bt_cost - 1) * 100
                if vs_cost <= -5:
                    score += 12
                    reasons.append(f"大幅低于成本{abs(vs_cost):.1f}%")
                elif vs_cost <= 0:
                    score += 10
                    reasons.append(f"低于回测成本{abs(vs_cost):.1f}%")
                elif vs_cost <= 5:
                    score += 5
                    reasons.append("接近回测成本")
                elif vs_cost <= 15:
                    reasons.append(f"高于成本{vs_cost:.1f}%")
                else:
                    score -= 3
                    reasons.append(f"远高于成本{vs_cost:.1f}%")
            reasons.append("策略持仓")

        # --- (B) PE valuation (max +15, min -8) ---
        pe = tc.get("pe", 0)
        if 0 < pe < 15:
            score += 15
            reasons.append(f"PE优秀({pe:.0f})")
        elif 0 < pe < 25:
            score += 12
            reasons.append(f"PE良好({pe:.0f})")
        elif 0 < pe < 40:
            score += 6
            reasons.append(f"PE适中({pe:.0f})")
        elif 0 < pe < 60:
            score += 2
            reasons.append(f"PE偏高({pe:.0f})")
        elif pe < 0:
            score -= 8
            reasons.append("PE亏损")
        elif pe >= 100:
            score -= 5
            reasons.append(f"PE极高({pe:.0f})")
        elif pe >= 60:
            score -= 2
            reasons.append(f"PE偏贵({pe:.0f})")

        # --- (C) PB valuation (max +6, min -3) ---
        pb = tc.get("pb", 0)
        if 0 < pb < 1.5:
            score += 6
            reasons.append(f"PB低估({pb:.1f})")
        elif 0 < pb < 3:
            score += 4
            reasons.append(f"PB合理({pb:.1f})")
        elif 0 < pb < 5:
            score += 1
        elif pb >= 10:
            score -= 3
            reasons.append(f"PB偏高({pb:.1f})")

        # --- (D) Market cap stability (max +8) ---
        mc = tc.get("marketCap", 0)
        if mc > 5000:
            score += 6
            reasons.append("超大盘龙头")
        elif mc > 1000:
            score += 8
            reasons.append("大盘股稳健")
        elif mc > 300:
            score += 5
            reasons.append("中盘成长")
        elif mc > 50:
            score += 2
            reasons.append("小盘股")
        elif mc > 0:
            score -= 2
            reasons.append("微盘高风险")

        # --- (E) Today's movement — hybrid logic (max +8, min -2) ---
        change = q.get("changePct", 0)
        if change < -3:
            score += 5
            reasons.append(f"今日急跌{change:.1f}%,关注低吸")
        elif change < -1:
            score += 3
            reasons.append("今日回调,可低吸")
        elif -1 <= change <= 1:
            score += 2
            reasons.append("今日平稳")
        elif change > 5:
            score -= 2
            reasons.append(f"今日大涨{change:.1f}%,追高风险")
        elif change > 2:
            score += 3
            reasons.append(f"今日强势{change:.1f}%")
        else:
            score += 1

        # --- (F) Turnover quality (max +6) ---
        turnover = tc.get("turnover", 0)
        if 2 < turnover < 8:
            score += 6
            reasons.append(f"换手率健康({turnover:.1f}%)")
        elif 1 < turnover <= 2:
            score += 3
            reasons.append("换手率偏低")
        elif 8 <= turnover < 15:
            score += 2
            reasons.append("换手较高")
        elif turnover >= 15:
            score -= 3
            reasons.append(f"换手率过高({turnover:.0f}%)投机风险")
        elif turnover <= 0.5 and turnover > 0:
            score -= 2
            reasons.append("流动性差")

        # Small-cap + high turnover = manipulation risk
        circ_cap = tc.get("circulatingMarketCap", 0)
        cap_for_check = circ_cap if circ_cap > 0 else mc
        if cap_for_check > 0 and cap_for_check < 50e8 and turnover > 10:
            score -= 4
            reasons.append(f"小盘高换手({turnover:.0f}%)疑似庄股")
        elif cap_for_check > 0 and cap_for_check < 30e8 and turnover > 8:
            score -= 3
            reasons.append(f"小盘换手偏高({turnover:.0f}%)")

        # --- (G) Technical analysis from klines (variable, see _score_technicals) ---
        techs = None
        klines = klines_map.get(code)
        if klines:
            techs = _compute_technicals(klines)
            tech_score, tech_reasons = _score_technicals(techs)
            score += tech_score
            reasons.extend(tech_reasons)

            # Manipulation pattern detection from klines
            if len(klines) >= 30:
                manip = _detect_manipulation_patterns(klines)
                if manip["pattern"] != "neutral":
                    score += manip["score_adj"]
                    reasons.extend(manip["signals"])

        # --- (H) Chip distribution (筹码分布 CYQ) ---
        cyq = _cyq_results.get(code)
        if cyq:
            cyq_score, cyq_reasons, _ = score_chip_distribution(cyq, price)
            score += cyq_score
            reasons.extend(cyq_reasons)

        score = round(score, 1)
        stock_entry = {
            "code": code,
            "name": q.get("name", STOCK_NAMES.get(code, code)),
            "price": price,
            "prevClose": q.get("prevClose", 0),
            "changePct": change,
            "pe": pe,
            "pb": pb,
            "marketCap": mc,
            "turnover": tc.get("turnover", 0),
            "score": score,
            "reasons": reasons,
            "inFinalPosition": code in pos_map,
            "btCost": pos_map.get(code, {}).get("avgCost", 0),
            "btShares": pos_map.get(code, {}).get("shares", 0),
        }
        if techs:
            stock_entry["technicals"] = techs
        if cyq:
            stock_entry["chipDistribution"] = cyq
        scored.append(stock_entry)

    scored.sort(key=lambda x: x["score"], reverse=True)

    # ── 4. Capital allocation ──
    cash_reserve_pct = 0.10
    cash_reserve = capital * cash_reserve_pct
    invest_capital = capital - cash_reserve

    # Handle expensive stocks (e.g. ę́ 1400+/share)
    expensive = [s for s in scored if s["price"] * 100 > invest_capital * 0.30 and s["score"] >= 50]
    # Keep all stocks from the user's pool (they were explicitly selected);
    # only apply the score >= 15 floor to extra/fallback stocks.
    pool_set = set(pool) if symbols and len(symbols) > 0 else set()
    normal = [s for s in scored if s not in expensive and (s["code"] in pool_set or s["score"] >= 15)]

    allocations = []
    remaining = invest_capital

    # Allocate expensive stocks first (1 lot each if score high enough)
    for s in expensive:
        lot = _get_lot_size(s["code"])
        lot_cost = s["price"] * lot
        if lot_cost <= remaining * 0.60:
            allocations.append({
                **s,
                "shares": lot,
                "amount": round(lot_cost, 2),
                "weight": round(lot_cost / capital * 100, 1),
                "category": "core",
            })
            remaining -= lot_cost

    # Allocate remaining capital to top normal stocks
    # If pool is user-specified, keep all of them; otherwise cap at 10
    top_count = min(len(normal), max(10, len(pool_set))) if pool_set else min(10, len(normal))
    top_picks = normal[:top_count]
    total_score = sum(s["score"] for s in top_picks if s["score"] > 0) or 1

    for i, s in enumerate(top_picks):
        # Skip capital allocation for stocks with very low score (keep in list for visibility)
        # Threshold: score <= 0 always skip; pool stocks with score < 15 also skip
        if s["score"] <= 0 or (s["score"] < 15 and s["code"] in pool_set):
            cat = "core" if i < 3 else ("growth" if i < 5 else "satellite")
            warn = (
                f"综合评分{s['score']:.0f}分，不建议建仓"
                if s["score"] <= 0
                else f"综合评分仅{s['score']:.0f}分（低于15分门槛），不建议建仓"
            )
            allocations.append({
                **s,
                "shares": 0,
                "amount": 0,
                "weight": 0,
                "category": cat,
                "riskWarning": warn,
                "riskLevel": "high",
            })
            continue
        w = s["score"] / total_score
        alloc = remaining * w
        lot = _get_lot_size(s["code"])
        shares = int(alloc / s["price"] / lot) * lot
        if shares < lot:
            shares = lot
        actual = shares * s["price"]
        cat = "core" if i < 3 else ("growth" if i < 5 else "satellite")
        allocations.append({
            **s,
            "shares": shares,
            "amount": round(actual, 2),
            "weight": round(actual / capital * 100, 1),
            "category": cat,
        })

    total_invested = sum(a["amount"] for a in allocations)
    remaining_cash = capital - total_invested

    # ── 4.5 Risk warnings for low-score stocks ──
    for a in allocations:
        if "riskLevel" in a:
            continue  # Already flagged (score <= 0)
        if a["score"] < 25:
            a["riskWarning"] = f"综合评分仅{a['score']:.0f}分，多维度评级偏弱，建议谨慎"
            a["riskLevel"] = "high"
        elif a["score"] < 40:
            a["riskWarning"] = f"综合评分{a['score']:.0f}分，偏低，注意仓位控制"
            a["riskLevel"] = "medium"

    # ── 4.6 Enrich with decision fields ──
    _enrich_decision_fields(
        allocations,
        capital=capital,
        target_capital=target_capital if target_capital > 0 else capital * 1.2,
        strategy=strategy,
        risk_tolerance=risk_tolerance,
        backtest_summary=summary,
    )

    # ── 5. Build response ──
    # Categorize for display
    core = [a for a in allocations if a["category"] == "core"]
    growth_pos = [a for a in allocations if a["category"] == "growth"]
    satellite = [a for a in allocations if a["category"] == "satellite"]

    return {
        "allocations": allocations,
        "categories": {
            "core": core,
            "growth": growth_pos,
            "satellite": satellite,
        },
        "summary": {
            "totalCapital": capital,
            "investedAmount": round(total_invested, 2),
            "cashReserve": round(remaining_cash, 2),
            "cashReservePct": round(remaining_cash / capital * 100, 1),
            "positionCount": len(allocations),
            "poolSize": len(pool),
        },
        "backtestSummary": {
            "strategy": summary.get("strategy", strategy),
            "strategyLabel": summary.get("strategyLabel", strategy),
            "totalReturn": summary.get("totalReturn", 0),
            "annualizedReturn": summary.get("annualizedReturn", 0),
            "maxDrawdown": summary.get("maxDrawdown", 0),
            "sharpeRatio": summary.get("sharpeRatio", 0),
            "winRate": summary.get("winRate", 0),
            "profitFactor": summary.get("profitFactor", 0),
            "totalTrades": summary.get("totalTrades", 0),
        },
        "tips": [
            "分批建仓: 先买入60%核心仓位,剩余40%等回调2-3%时补仓",
            "止损纪律: 单只亏损超过8%坚决止损",
            "止盈策略: 盈利超过20%后分批减仓(每涨5%减仓20%)",
            f"调仓复盘: ÿ{rebalance_days}个交易日,参考策略信号调整持仓",
            f"现金储备: 保持约{remaining_cash/10000:.0f}万现金,用于补仓或突发机会",
            "大盘风控: 如沪指跌破重要支撑位,减仓至50%以下",
        ],
        "pool": pool,
        "allStocks": scored,
        "period": {"startDate": start_date, "endDate": end_date},
    }


if __name__ == "__main__":
    _raw = os.environ.get("QUANT_CONFIG") or sys.stdin.read()
    config = json.loads(_raw)

    symbols = config.get("symbols")
    if isinstance(symbols, str):
        symbols = [s.strip() for s in symbols.split(",") if s.strip()]

    try:
        result = recommend(
            capital=config.get("capital", config.get("initial_capital", 500000)),
            strategy=config.get("strategy", "mixed"),
            risk_tolerance=config.get("risk_tolerance", "high"),
            rebalance_days=config.get("rebalance_days", 5),
            symbols=symbols,
            start_date=config.get("start_date", "2025-06-01"),
            end_date=config.get("end_date"),
            target_capital=config.get("target_capital", config.get("targetCapital", 0)),
            exclude_star=config.get("exclude_star", True),
        )
    except Exception as e:
        import traceback
        result = {"error": str(e), "traceback": traceback.format_exc()}

    print(json.dumps(result, ensure_ascii=False))
