"""
chip_accumulation — 主力吸筹检测引擎

Detects institutional accumulation ("主力吸筹") by combining multiple signals:
  1. 资金流向 (fund flow): sustained main-force net inflows
  2. 筹码分布 (chip distribution): concentration tightening, low winner ratio
  3. 量价结构 (volume-price structure): volume shrinks on drops, rises on pumps
  4. 换手率 (turnover rate): low/moderate turnover during sideways
  5. 大单净量 (large order net): persistent large-order buying

Scoring:
  Each dimension contributes 0-20 points → total 0-100.
  >= 70: strong accumulation signal ("明显吸筹")
  50-69: moderate signal ("疑似吸筹")
  30-49: weak / neutral
  < 30: no accumulation signal / possible distribution

Input (JSON via stdin / QUANT_CONFIG):
  {
        "action": "detect" | "scan" | "detail" | "ambush_scan",
    "symbol": "600519",       # required for detect/detail
    "symbols": "600519,...",   # optional for scan (comma-separated, or empty=top active)
    "lookback": 30,            # days to analyze
        "min_confidence": 40,      # minimum score to include
        "as_of_date": "2026-03-20" # optional historical replay date (YYYY-MM-DD)
  }

Output: JSON with accumulation analysis.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import time
import traceback
import urllib.request
from datetime import datetime, timedelta
from typing import Any

# ── Smart proxy handling for Chinese financial data hosts ─────────────────────
# On macOS with Clash/ClashX, the system proxy (127.0.0.1:7890) may be picked up
# by Python.  Depending on Clash mode (system proxy vs TUN/enhanced mode), either
# the direct connection or the proxied connection may work.
#
# Strategy: detect proxy, try multiple paths, keep what works in session cache.

_PROXY_PORT = 7890
_PROXY_HOST = "127.0.0.1"
_CACHED_PROXY_MODE: str | None = None  # "direct" | "proxy" | None


def _probe_proxy() -> bool:
    """Check if local Clash-like proxy is listening."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect((_PROXY_HOST, _PROXY_PORT))
        s.close()
        return True
    except Exception:
        return False


def _setup_direct_mode():
    """Configure for direct connections (bypass all proxies)."""
    for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
              "all_proxy", "ALL_PROXY"):
        os.environ.pop(k, None)
    os.environ["no_proxy"] = "*"
    os.environ["NO_PROXY"] = "*"
    urllib.request.getproxies = lambda: {}  # type: ignore[assignment]
    try:
        import requests.utils
        requests.utils.getproxies = lambda: {}  # type: ignore[assignment]
        requests.utils.get_environ_proxies = lambda *a, **kw: {}  # type: ignore[assignment]
    except Exception:
        pass


def _setup_proxy_mode():
    """Configure to use explicit local proxy."""
    proxy = f"http://{_PROXY_HOST}:{_PROXY_PORT}"
    os.environ["http_proxy"] = proxy
    os.environ["https_proxy"] = proxy
    os.environ["HTTP_PROXY"] = proxy
    os.environ["HTTPS_PROXY"] = proxy
    os.environ.pop("no_proxy", None)
    os.environ.pop("NO_PROXY", None)
    try:
        import requests.utils as _ru
        _ru.getproxies = lambda: {"http": proxy, "https": proxy}  # type: ignore[assignment]
        _ru.get_environ_proxies = lambda *a, **kw: {"http": proxy, "https": proxy}  # type: ignore[assignment]
    except Exception:
        pass


# Initial setup: try both modes — prefer proxy if available (Clash TUN mode),
# fallback to direct.  _retry_call will flip modes on network failure.
if _probe_proxy():
    _setup_proxy_mode()
    _CACHED_PROXY_MODE = "proxy"
else:
    _setup_direct_mode()
    _CACHED_PROXY_MODE = "direct"


def _retry_call(fn, *args, retries: int = 3, base_delay: float = 1.5, **kwargs):
    """Call *fn* with exponential-backoff retry on transient network errors.

    On the first network failure, if a local proxy is available and we haven't
    tried it yet, switch proxy mode and retry immediately (dual-path fallback).
    """
    global _CACHED_PROXY_MODE
    last_err: Exception | None = None
    switched = False

    for attempt in range(retries):
        try:
            result = fn(*args, **kwargs)
            # Remember what worked
            if _CACHED_PROXY_MODE is None:
                _CACHED_PROXY_MODE = "proxy" if os.environ.get("http_proxy") else "direct"
            return result
        except Exception as e:
            last_err = e
            err_str = str(e)
            transient = any(kw in err_str for kw in [
                "RemoteDisconnected", "Connection aborted",
                "ConnectionReset", "timed out", "Too Many Requests",
                "HTTPSConnectionPool", "Read timed out",
                "ProxyError",
            ])
            if not transient:
                raise

            # On first network failure, try flipping proxy mode
            if not switched and attempt < retries - 1:
                switched = True
                using_proxy = bool(os.environ.get("http_proxy"))
                if using_proxy:
                    _setup_direct_mode()
                    _CACHED_PROXY_MODE = None
                elif _probe_proxy():
                    _setup_proxy_mode()
                    _CACHED_PROXY_MODE = None
                # Force urllib3 to drop cached proxy connection pools
                try:
                    import urllib3.poolmanager
                    # Clear any default PoolManager caches in requests
                    import requests.adapters
                    for adapter in [requests.adapters.HTTPAdapter]:
                        if hasattr(adapter, 'poolmanager'):
                            del adapter.poolmanager
                except Exception:
                    pass
                continue  # retry immediately after switching

            if attempt < retries - 1:
                time.sleep(base_delay * (2 ** attempt))
    raise last_err  # type: ignore[misc]


def run_chip_accumulation(config: dict) -> dict:
    """Dispatch chip accumulation analysis."""
    action = config.get("action", "detect")
    symbol = config.get("symbol", "")
    lookback = int(config.get("lookback", 30))
    min_confidence = int(config.get("min_confidence", 40))
    as_of_date = _normalize_as_of_date(config.get("as_of_date") or config.get("asOfDate"))

    if action == "detect":
        if not symbol:
            return {"error": "请提供股票代码 (symbol)"}
        return _detect_accumulation(symbol, lookback, as_of_date)
    elif action == "scan":
        symbols_str = config.get("symbols", "")
        symbols = [s.strip() for s in symbols_str.split(",") if s.strip()] if symbols_str else []
        return _scan_accumulation(symbols, lookback, min_confidence, as_of_date)
    elif action == "detail":
        if not symbol:
            return {"error": "请提供股票代码 (symbol)"}
        return _detail_accumulation(symbol, lookback, as_of_date)
    elif action == "ambush_scan":
        symbols_str = config.get("symbols", "")
        symbols = [s.strip() for s in symbols_str.split(",") if s.strip()] if symbols_str else []
        return _scan_ambush_accumulation(symbols, lookback, min_confidence, config, as_of_date)
    else:
        return {"error": f"未知 action: {action}"}


# ── Single stock accumulation detection ───────────────────────────

def _detect_accumulation(symbol: str, lookback: int, as_of_date: str | None = None) -> dict:
    """Detect accumulation signals for a single stock."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    errors: list[str] = []
    signals: list[dict] = []
    scores: dict[str, float] = {}
    details: dict[str, list[str]] = {
        "fund_flow": [],
        "chip": [],
        "volume_price": [],
        "turnover": [],
        "large_order": [],
    }

    # Fetch daily K-line data (extra days for CYQ estimation which needs 90+ days)
    fetch_days = max(lookback + 10, 120)
    klines = _fetch_klines(ak, symbol, fetch_days, errors, as_of_date=as_of_date)
    if not klines or len(klines) < max(10, lookback // 2):
        # Provide a clear error message — network issue is the most common cause
        net_err = any("RemoteDisconnected" in e or "Connection" in e or "timed out" in e
                      for e in errors)
        if net_err:
            return {
                "error": f"网络连接失败，无法获取 {symbol} 的行情数据。"
                         "请检查网络连接和代理设置 (如 Clash 需要开启 '系统代理' 或关闭 TUN 模式)。",
                "errors": errors,
            }
        return {"error": f"K线数据不足 (需要至少 {lookback} 日)", "errors": errors}

    # Keep full history for CYQ estimation, clip for other dimensions
    all_klines = klines  # full history for chip distribution
    klines = klines[-(lookback + 5):]
    name = _fetch_stock_name(ak, symbol)
    current_price = klines[-1]["close"]

    # Enrich klines with calculated turnover rate if missing
    _enrich_turnover_rate(ak, symbol, klines, errors)

    # ── Dimension 1: Fund Flow (资金流向) — 0~20 ──
    scores["fund_flow"] = _score_fund_flow(ak, symbol, lookback, details["fund_flow"], errors)

    # ── Dimension 2: Chip Distribution (筹码分布) — 0~20 ──
    scores["chip"] = _score_chip_distribution(ak, symbol, current_price, details["chip"], errors, all_klines)

    # ── Dimension 3: Volume-Price Structure (量价结构) — 0~20 ──
    scores["volume_price"] = _score_volume_price(klines, details["volume_price"])

    # ── Dimension 4: Turnover Rate (换手率) — 0~20 ──
    scores["turnover"] = _score_turnover(klines, details["turnover"])

    # ── Dimension 5: Large Order Net (大单净量) — 0~20 ──
    scores["large_order"] = _score_large_order(ak, symbol, lookback, details["large_order"], errors)

    total_score = sum(scores.values())
    total_score = max(0, min(100, total_score))

    # Build signal list
    for dim, detail_list in details.items():
        for d in detail_list:
            signals.append({"dimension": dim, "description": d, "score": scores.get(dim, 0)})

    # Conclusion
    if total_score >= 70:
        conclusion = "明显吸筹"
        level = "strong"
        advice = "主力吸筹迹象明显，可关注后续放量突破信号"
    elif total_score >= 50:
        conclusion = "疑似吸筹"
        level = "moderate"
        advice = "存在一定吸筹特征，建议继续观察确认"
    elif total_score >= 30:
        conclusion = "吸筹信号弱"
        level = "weak"
        advice = "吸筹信号不明确，建议多维度验证"
    else:
        conclusion = "未发现吸筹"
        level = "none"
        if total_score >= 5:
            advice = f"综合评分偏低({total_score:.1f}/100)，吸筹特征不足，建议结合扫描筛选条件综合判断"
        else:
            advice = "未检测到主力吸筹特征，可能处于派发或自由交易状态"

    return {
        "action": "detect",
        "symbol": symbol,
        "name": name,
        "price": current_price,
        "lookback": lookback,
        "asOfDate": as_of_date,
        "total_score": round(total_score, 1),
        "level": level,
        "conclusion": conclusion,
        "advice": advice,
        "dimension_scores": {k: round(v, 1) for k, v in scores.items()},
        "signals": signals,
        "errors": errors if errors else None,
    }


# ── Batch scan ────────────────────────────────────────────────────

def _scan_accumulation(symbols: list[str], lookback: int, min_confidence: int,
                       as_of_date: str | None = None) -> dict:
    """Scan multiple stocks for accumulation signals."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    # If no symbols provided, get today's most active stocks
    if not symbols:
        symbols = _get_active_stocks(ak, limit=30)

    results: list[dict] = []
    errors: list[str] = []

    for sym in symbols[:50]:  # limit to 50 stocks max
        try:
            r = _detect_accumulation(sym, lookback, as_of_date)
            if r.get("error"):
                errors.append(f"{sym}: {r['error']}")
                continue
            score = r.get("total_score", 0)
            if score >= min_confidence:
                results.append({
                    "symbol": r.get("symbol"),
                    "name": r.get("name", ""),
                    "price": r.get("price"),
                    "total_score": score,
                    "level": r.get("level"),
                    "conclusion": r.get("conclusion"),
                    "dimension_scores": r.get("dimension_scores"),
                })
        except Exception as e:
            errors.append(f"{sym}: {e}")

    results.sort(key=lambda x: x.get("total_score", 0), reverse=True)

    return {
        "action": "scan",
        "count": len(results),
        "min_confidence": min_confidence,
        "lookback": lookback,
        "asOfDate": as_of_date,
        "stocks": results,
        "errors": errors if errors else None,
    }


# ── Detailed analysis with chart data ─────────────────────────────

def _detail_accumulation(symbol: str, lookback: int, as_of_date: str | None = None) -> dict:
    """Detailed accumulation analysis with trend data for charting."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    errors: list[str] = []
    base = _detect_accumulation(symbol, lookback, as_of_date)
    if base.get("error"):
        return base

    # Add daily fund flow series for chart
    fund_flow_series = []
    try:
        market = "sh" if symbol.startswith("6") else "sz"
        df = _retry_call(ak.stock_individual_fund_flow, stock=symbol, market=market)
        if df is not None and not df.empty:
            if as_of_date:
                as_of_compact = _as_compact_date(as_of_date)
                if as_of_compact:
                    df = df[df["日期"].astype(str).str.replace("-", "").str.slice(0, 8) <= as_of_compact]
            for _, row in df.tail(lookback).iterrows():
                fund_flow_series.append({
                    "date": str(row.get("日期", ""))[:10],
                    "mainNetIn": _safe_float(row.get("主力净流入-净额")),
                    "superLargeNetIn": _safe_float(row.get("超大单净流入-净额")),
                    "largeNetIn": _safe_float(row.get("大单净流入-净额")),
                })
    except Exception as e:
        errors.append(f"资金流向序列获取失败: {e}")

    # Add K-line with volume for chart
    kline_series = []
    try:
        klines = _fetch_klines(ak, symbol, lookback + 5, errors, as_of_date=as_of_date)
        if klines:
            for k in klines[-(lookback):]:
                kline_series.append({
                    "date": k["date"],
                    "open": k["open"],
                    "high": k["high"],
                    "low": k["low"],
                    "close": k["close"],
                    "volume": k["volume"],
                    "turnover_rate": k.get("turnover_rate"),
                })
    except Exception:
        pass

    base["fund_flow_series"] = fund_flow_series
    base["kline_series"] = kline_series
    base["action"] = "detail"
    if errors:
        base.setdefault("errors", []).extend(errors)

    return base


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1)
    out = [values[0]]
    for idx in range(1, len(values)):
        out.append(alpha * values[idx] + (1 - alpha) * out[idx - 1])
    return out


def _calc_zlxc(klines: list[dict]) -> dict[str, list[float]] | None:
    if len(klines) < 26:
        return None
    closes = [float(k.get("close") or 0) for k in klines]
    if any(v <= 0 for v in closes):
        return None
    var1 = _ema(_ema(closes, 13), 13)
    var2 = [((c - v1) / v1 * 100) if v1 else 0 for c, v1 in zip(closes, var1)]
    zljc = _ema(var2, 1)
    xp_base = _ema(var2, 1)
    xp_slow = _ema(var2, 3)
    xp = [a - b for a, b in zip(xp_base, xp_slow)]
    var6 = _ema(xp, 10)
    return {"zljc": zljc, "xp": xp, "var6": var6}


def _series_net_bias(values: list[float]) -> tuple[float, float, float]:
    """Return (net_total, absolute_total, signed_bias) for a money-flow series."""
    total_net = sum(values)
    total_abs = sum(abs(v) for v in values)
    if total_abs <= 0:
        return total_net, total_abs, 0.0
    return total_net, total_abs, total_net / total_abs


def _scan_ambush_accumulation(symbols: list[str], lookback: int, min_confidence: int,
                              config: dict, as_of_date: str | None = None) -> dict:
    """潜伏吸筹扫描: 前期吸筹 + 位置合理 + 尚未爆发。"""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    cfg = {
        "volume_ratio": max(1.0, min(4.0, _safe_float(config.get("ambush_volume_ratio"), 1.3) or 1.3)),
        "pre_days": max(8, min(30, int(_safe_float(config.get("ambush_pre_days"), 15) or 15))),
        "pre_hits": max(1, min(8, int(_safe_float(config.get("ambush_pre_hits"), 1) or 1))),
        "change_max": max(0.5, min(12.0, _safe_float(config.get("ambush_change_max"), 5.0) or 5.0)),
        "change_min": max(-12.0, min(2.0, _safe_float(config.get("ambush_change_min"), -4.0) or -4.0)),
        "turnover_min": max(0.0, min(20.0, _safe_float(config.get("ambush_turnover_min"), 0.2) or 0.2)),
        "turnover_max": max(3.0, min(60.0, _safe_float(config.get("ambush_turnover_max"), 20.0) or 20.0)),
        "ma_period": max(10, min(60, int(_safe_float(config.get("ambush_ma_period"), 20) or 20))),
        "ma_deviation_max": max(2.0, min(20.0, _safe_float(config.get("ambush_ma_deviation_max"), 12.0) or 12.0)),
        "shrink_ratio": max(0.5, min(1.0, _safe_float(config.get("ambush_shrink_ratio"), 0.95) or 0.95)),
        "stop_volatility": max(0.5, min(5.0, _safe_float(config.get("ambush_stop_volatility"), 1.5) or 1.5)),
        "near_low_max": max(1.0, min(20.0, _safe_float(config.get("ambush_near_low_max"), 8.0) or 8.0)),
        "box_days": max(6, min(30, int(_safe_float(config.get("ambush_box_days"), 10) or 10))),
        "box_amplitude": max(6.0, min(35.0, _safe_float(config.get("ambush_box_amplitude"), 22.0) or 22.0)),
        "ma_convergence": max(1.0, min(10.0, _safe_float(config.get("ambush_ma_convergence"), 5.0) or 5.0)),
    }

    history_days = max(lookback + 30, 70)
    if not symbols:
        symbols = _get_active_stocks(ak, limit=60)

    stocks: list[dict] = []
    errors: list[str] = []

    for symbol in symbols[:80]:
        try:
            klines = _fetch_klines(ak, symbol, history_days, errors, as_of_date=as_of_date)
            if not klines or len(klines) < max(30, cfg["ma_period"] + 5):
                continue

            _enrich_turnover_rate(ak, symbol, klines, errors)

            closes = [float(k.get("close") or 0) for k in klines]
            highs = [float(k.get("high") or 0) for k in klines]
            lows = [float(k.get("low") or 0) for k in klines]
            volumes = [float(k.get("volume") or 0) for k in klines]
            turnover = _safe_float(klines[-1].get("turnover_rate"))

            if len(closes) < cfg["ma_period"] + 5 or closes[-1] <= 0 or closes[-2] <= 0:
                continue

            day_change = (closes[-1] / closes[-2] - 1) * 100
            day_change_ok = cfg["change_min"] <= day_change <= cfg["change_max"]

            turnover_ok = turnover is None or (
                cfg["turnover_min"] <= turnover <= cfg["turnover_max"]
            )

            ma_period = cfg["ma_period"]
            ma_now = sum(closes[-ma_period:]) / ma_period
            ma_prev = sum(closes[-ma_period - 1:-1]) / ma_period
            if ma_now <= 0:
                continue
            ma_deviation = abs(closes[-1] - ma_now) / ma_now * 100
            ma_deviation_ok = ma_deviation <= cfg["ma_deviation_max"]

            pre_days = cfg["pre_days"]
            pre_hits = 0
            start_idx = max(4, len(volumes) - pre_days)
            for i in range(start_idx, len(volumes) - 1):
                ma5 = sum(volumes[i - 4:i + 1]) / 5
                if ma5 > 0 and volumes[i] >= ma5 * cfg["volume_ratio"]:
                    pre_hits += 1
            pre_accum_ok = pre_hits >= cfg["pre_hits"] and ma_now > ma_prev

            zlxc = _calc_zlxc(klines)
            if not zlxc:
                continue
            zljc = zlxc["zljc"]
            xp = zlxc["xp"]
            var6 = zlxc["var6"]
            if len(zljc) < 3 or len(xp) < 5 or len(var6) < 3:
                continue

            main_force_ok = not (
                zljc[-1] <= 0 and zljc[-1] <= zljc[-2]
            ) and (
                sum(1 for v in xp[-5:] if v > 0) >= 2 or var6[-1] > var6[-3]
            )

            # 底部埋伏: 缩量 + 止跌 + 贴近前低 + MA走平
            ma5_last = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else 0
            shrink_ok = ma5_last > 0 and volumes[-1] < ma5_last * cfg["shrink_ratio"]
            stop_ok = abs(day_change) <= cfg["stop_volatility"]
            recent_low = min(lows[-ma_period:])
            near_low = (closes[-1] - recent_low) / recent_low * 100 if recent_low > 0 else 999
            near_low_ok = 0 <= near_low <= cfg["near_low_max"]
            ma_slope = abs(ma_now - ma_prev) / ma_prev * 100 if ma_prev > 0 else 999
            flat_ma_ok = ma_slope <= 0.8
            bottom_ok = shrink_ok and stop_ok and near_low_ok and flat_ma_ok

            # 箱体整理: 均线收敛 + 箱体振幅 + 区间位置不过高
            if len(closes) >= max(20, cfg["box_days"]):
                ma5p = sum(closes[-5:]) / 5
                ma10p = sum(closes[-10:]) / 10
                ma20p = sum(closes[-20:]) / 20
                min_ma = min(ma5p, ma10p, ma20p)
                convergence = ((max(ma5p, ma10p, ma20p) - min_ma) / min_ma * 100) if min_ma > 0 else 999
                box_high = max(highs[-cfg["box_days"]:])
                box_low = min(lows[-cfg["box_days"]:])
                amplitude = ((box_high - box_low) / box_low * 100) if box_low > 0 else 999
                amp_limit = cfg["box_amplitude"] * (1.5 if convergence < 2.0 else 1.0)
                hi60 = max(highs[-60:]) if len(highs) >= 60 else max(highs)
                lo60 = min(lows[-60:]) if len(lows) >= 60 else min(lows)
                position60 = ((closes[-1] - lo60) / (hi60 - lo60) * 100) if hi60 > lo60 else 999
                consolidation_ok = convergence <= cfg["ma_convergence"] and amplitude <= amp_limit and position60 <= 60
            else:
                consolidation_ok = False

            mode = None
            if pre_accum_ok and main_force_ok and day_change_ok and ma_deviation_ok and turnover_ok:
                mode = "潜伏"
            elif bottom_ok:
                mode = "底部"
            elif consolidation_ok:
                mode = "箱体"
            elif pre_hits >= max(1, cfg["pre_hits"] - 1) and (zljc[-1] > -0.3 or var6[-1] > -0.05):
                mode = "观察"

            if mode is None:
                continue

            name = _fetch_stock_name(ak, symbol)
            score = 40.0
            comp_pre_hits = min(20.0, pre_hits * 4.0)
            comp_zljc = max(0.0, min(20.0, zljc[-1] * 1.8))
            comp_var6 = max(0.0, min(12.0, var6[-1] * 10.0))
            comp_ma_dev = max(0.0, 8.0 - ma_deviation)
            comp_day_chg = max(0.0, 4.0 - abs(day_change))
            score += comp_pre_hits
            score += comp_zljc
            score += comp_var6
            score += comp_ma_dev
            score += comp_day_chg
            if not day_change_ok:
                score -= 4.0
            if not ma_deviation_ok:
                score -= 4.0
            if not turnover_ok:
                score -= 3.0
            if mode == "底部":
                score += 6.0
            if mode == "箱体":
                score += 4.0
            if mode == "观察":
                score -= 2.0

            if score < min_confidence:
                continue

            reasons: list[str] = []
            if pre_accum_ok:
                reasons.append(f"前期放量命中 {pre_hits} 次")
            elif pre_hits > 0:
                reasons.append(f"前期放量命中 {pre_hits} 次（未达预设）")
            if main_force_ok:
                reasons.append("主力线维持改善")
            if day_change_ok:
                reasons.append("当日涨跌幅处于温和区间")
            if ma_deviation_ok:
                reasons.append("价格偏离均线较小")
            if turnover_ok:
                reasons.append("换手结构处于可接受区间")
            if mode == "底部":
                reasons.append("底部止跌与缩量特征成立")
            elif mode == "箱体":
                reasons.append("箱体整理与均线收敛成立")

            risk_flags: list[str] = []
            if not day_change_ok:
                risk_flags.append("日涨跌幅偏离温和区间")
            if not ma_deviation_ok:
                risk_flags.append("价格偏离均线过大")
            if not turnover_ok:
                risk_flags.append("换手率超出阈值")
            if mode == "观察":
                risk_flags.append("主升段确认不足，当前仅观察")

            stocks.append({
                "symbol": symbol,
                "name": name,
                "price": round(closes[-1], 3),
                "total_score": round(score, 1),
                "level": "strong" if score >= 70 else ("moderate" if score >= 50 else "weak"),
                "conclusion": f"{mode}吸筹候选",
                "signal_type": mode,
                "day_change_pct": round(day_change, 2),
                "turnover_rate": turnover,
                "pre_accum_hits": pre_hits,
                "ma_deviation_pct": round(ma_deviation, 2),
                "zljc": round(zljc[-1], 3),
                "var6": round(var6[-1], 3),
                "score_components": {
                    "base": 40.0,
                    "pre_accum_hits": round(comp_pre_hits, 2),
                    "main_force_zljc": round(comp_zljc, 2),
                    "main_force_var6": round(comp_var6, 2),
                    "ma_deviation": round(comp_ma_dev, 2),
                    "day_change": round(comp_day_chg, 2),
                },
                "reasons": reasons,
                "risk_flags": risk_flags if risk_flags else None,
            })
        except Exception as e:
            errors.append(f"{symbol}: {e}")

    stocks.sort(key=lambda x: x.get("total_score", 0), reverse=True)

    return {
        "action": "ambush_scan",
        "count": len(stocks),
        "lookback": lookback,
        "min_confidence": min_confidence,
        "asOfDate": as_of_date,
        "config": cfg,
        "stocks": stocks,
        "errors": errors if errors else None,
    }


# ── Scoring Dimensions ────────────────────────────────────────────

def _score_fund_flow(ak: Any, symbol: str, lookback: int,
                     details: list[str], errors: list[str]) -> float:
    """Score fund flow dimension (0-20). Sustained main-force inflows = high score."""
    score = 0.0
    try:
        market = "sh" if symbol.startswith("6") else "sz"
        df = _retry_call(ak.stock_individual_fund_flow, stock=symbol, market=market)
        if df is None or df.empty:
            details.append("资金流向数据不可用")
            return 0.0

        recent = df.tail(lookback)
        if recent.empty:
            return 0.0

        # Net main-force inflow over the period
        main_net_series = [_safe_float(row.get("主力净流入-净额")) or 0
                           for _, row in recent.iterrows()]
        total_main_net, _total_main_abs, main_bias = _series_net_bias(main_net_series)
        positive_days = sum(1 for x in main_net_series if x > 0)
        total_days = len(main_net_series)

        if total_days == 0:
            return 0.0

        positive_ratio = positive_days / total_days

        # Score: net inflow direction and directional strength.
        if total_main_net > 0:
            score += max(1.0, min(8.0, main_bias * 8.0))
            details.append(
                f"近{lookback}日主力净流入 {total_main_net/10000:.1f}万，净流强度 {main_bias:.0%}"
            )
        elif total_main_net < 0:
            score -= min(4.0, abs(main_bias) * 4.0)
            details.append(
                f"近{lookback}日主力净流出 {abs(total_main_net)/10000:.1f}万，净流强度 {abs(main_bias):.0%}"
            )
        else:
            details.append(f"近{lookback}日主力资金基本均衡")

        # Score: consistency (many positive days)
        if positive_ratio >= 0.7:
            score += 6
            details.append(f"资金流入天数占比 {positive_ratio:.0%}，高度一致")
        elif positive_ratio >= 0.55:
            score += 3
            details.append(f"资金流入天数占比 {positive_ratio:.0%}")

        # Score: trend (recent 5 days vs older days)
        if total_days >= 10:
            recent_5d = sum(main_net_series[-5:])
            older_5d = sum(main_net_series[-10:-5])
            if recent_5d > 0 and recent_5d > older_5d:
                score += 4
                details.append("近5日资金流入加速")
            elif recent_5d > 0:
                score += 2
                details.append("近5日维持净流入")

        # Super-large order (超大单) signal
        super_large_series = [_safe_float(row.get("超大单净流入-净额")) or 0
                              for _, row in recent.iterrows()]
        total_super, _total_super_abs, super_bias = _series_net_bias(super_large_series)
        if total_super > 0:
            score += min(2.0, max(0.5, super_bias * 2.0))
            details.append(f"超大单累计净买入 {total_super/10000:.1f}万")

    except Exception as e:
        errors.append(f"资金流向评分失败: {e}")

    return max(0, min(20, score))


def _score_chip_distribution(ak: Any, symbol: str, current_price: float,
                             details: list[str], errors: list[str],
                             klines: list[dict] | None = None) -> float:
    """Score chip distribution dimension (0-20). Tight concentration + low winner ratio = accumulation."""
    score = 0.0
    try:
        from engines.chip_distribution import fetch_chip_distribution, score_chip_distribution
        cyq = fetch_chip_distribution(symbol)
        if not cyq:
            # Fallback: estimate CYQ from historical OHLCV data
            if klines and current_price > 0:
                cyq = _estimate_chip_distribution(klines, current_price)
                if cyq:
                    details.append("筹码分布使用本地估算 (基于历史量价)")
            if not cyq:
                details.append("筹码分布数据不可用")
                return 0.0

        chip_score, chip_reasons, evidence = score_chip_distribution(cyq, current_price)

        # Only positive chip evidence should add accumulation points.
        if chip_score > 0:
            score = max(0, min(20, chip_score / 12 * 20))
        else:
            score = 0.0

        for r in chip_reasons:
            details.append(r)

        if evidence == "build":
            details.append("筹码结构偏向建仓期")
        elif evidence == "distribution":
            details.append("筹码结构偏向派发期")

        conc_70 = cyq.get("concentration70", 0)
        if conc_70 and conc_70 < 0.08:
            details.append(f"筹码集中度高 ({conc_70:.2%})，控盘明显")

    except Exception as e:
        errors.append(f"筹码分布评分失败: {e}")

    return max(0, min(20, score))


def _score_volume_price(klines: list[dict], details: list[str]) -> float:
    """Score volume-price structure (0-20).
    Classic accumulation pattern: volume shrinks on price drops, rises on price pumps.
    "缩量下跌,放量反弹" pattern.
    """
    score = 0.0
    if len(klines) < 10:
        details.append("K线数据不足以分析量价结构")
        return 0.0

    # Classify days into up/down
    up_volumes = []
    down_volumes = []
    flat_count = 0

    for i in range(1, len(klines)):
        vol = klines[i].get("volume", 0) or 0
        change = klines[i]["close"] - klines[i - 1]["close"]
        if change > 0:
            up_volumes.append(vol)
        elif change < 0:
            down_volumes.append(vol)
        else:
            flat_count += 1

    if not up_volumes or not down_volumes:
        details.append("涨跌日分布不均，无法判断量价结构")
        return 0.0

    avg_up_vol = sum(up_volumes) / len(up_volumes)
    avg_down_vol = sum(down_volumes) / len(down_volumes)

    # Higher volume on up days vs down days = accumulation
    if avg_up_vol > 0 and avg_down_vol > 0:
        vol_ratio = avg_up_vol / avg_down_vol
        if vol_ratio >= 1.5:
            score += 10
            details.append(f"上涨日均量是下跌日的 {vol_ratio:.1f} 倍，放量上攻缩量回调")
        elif vol_ratio >= 1.2:
            score += 6
            details.append(f"上涨日均量略高于下跌日 ({vol_ratio:.1f}x)")
        elif vol_ratio <= 0.7:
            score -= 2
            details.append(f"上涨缩量下跌放量，可能是出货 ({vol_ratio:.1f}x)")

    # Check for volume contraction over time (底部缩量整理)
    if len(klines) >= 20:
        first_half_vol = sum(k.get("volume", 0) or 0 for k in klines[:len(klines)//2])
        second_half_vol = sum(k.get("volume", 0) or 0 for k in klines[len(klines)//2:])
        first_half_len = len(klines) // 2
        second_half_len = len(klines) - first_half_len

        if first_half_len > 0 and second_half_len > 0:
            avg_first = first_half_vol / first_half_len
            avg_second = second_half_vol / second_half_len

            if avg_first > 0:
                shrink_ratio = avg_second / avg_first
                if shrink_ratio < 0.6:
                    score += 6
                    details.append(f"近期成交量显著萎缩 (缩至前期 {shrink_ratio:.0%})，底部缩量特征")
                elif shrink_ratio < 0.8:
                    score += 3
                    details.append(f"近期成交量温和萎缩 ({shrink_ratio:.0%})")

    # Check for price range contraction (横盘整理)
    if len(klines) >= 10:
        recent_highs = [k["high"] for k in klines[-10:]]
        recent_lows = [k["low"] for k in klines[-10:]]
        price_range = (max(recent_highs) - min(recent_lows)) / max(recent_highs) if max(recent_highs) > 0 else 0

        if price_range < 0.05:
            score += 4
            details.append(f"近10日价格波动仅 {price_range:.1%}，窄幅横盘整理")
        elif price_range < 0.10:
            score += 2
            details.append(f"近10日价格波动 {price_range:.1%}，较窄幅整理")

    return max(0, min(20, score))


def _score_turnover(klines: list[dict], details: list[str]) -> float:
    """Score turnover rate pattern (0-20).
    Accumulation: moderate/low turnover during sideways; occasional spikes.
    """
    score = 0.0
    turnover_rates = [k.get("turnover_rate") for k in klines if k.get("turnover_rate") is not None]

    if not turnover_rates:
        details.append("换手率数据不可用")
        return 0.0

    avg_turnover = sum(turnover_rates) / len(turnover_rates)
    max_turnover = max(turnover_rates)
    recent_turnover = sum(turnover_rates[-5:]) / min(5, len(turnover_rates[-5:])) if turnover_rates else 0

    # Low average turnover during sideways = accumulation
    if avg_turnover < 2.0:
        score += 8
        details.append(f"平均换手率 {avg_turnover:.2f}%，低换手吸筹特征")
    elif avg_turnover < 5.0:
        score += 5
        details.append(f"平均换手率 {avg_turnover:.2f}%，较低")
    elif avg_turnover < 10.0:
        score += 2
        details.append(f"平均换手率 {avg_turnover:.2f}%，正常水平")
    elif avg_turnover < 15.0:
        details.append(f"平均换手率 {avg_turnover:.2f}%，偏高")
    elif avg_turnover > 15.0:
        score -= 3
        details.append(f"平均换手率 {avg_turnover:.2f}%，过高可能是短线炒作")

    # Check for turnover variation (散户出清时高换手后变低)
    if len(turnover_rates) >= 10:
        first_half_avg = sum(turnover_rates[:len(turnover_rates)//2]) / (len(turnover_rates)//2)
        second_half_avg = sum(turnover_rates[len(turnover_rates)//2:]) / (len(turnover_rates) - len(turnover_rates)//2)

        if first_half_avg > 0 and second_half_avg < first_half_avg * 0.7:
            score += 6
            details.append("换手率由高渐低，散户换手后趋于沉淀")
        elif first_half_avg > 0 and second_half_avg < first_half_avg * 0.85:
            score += 3
            details.append("换手率温和递减")

    # Occasional spike (试盘/震仓)
    if max_turnover > avg_turnover * 3 and avg_turnover < 5:
        score += 4
        details.append(f"出现换手率异常放大 (最高 {max_turnover:.1f}% vs 均值 {avg_turnover:.1f}%)，可能是试盘")

    # Recent turnover is lower than average: quiet accumulation
    if recent_turnover < avg_turnover * 0.7 and recent_turnover < 3.0:
        score += 2
        details.append("近期换手率降至均值以下，安静吸筹")

    return max(0, min(20, score))


def _score_large_order(ak: Any, symbol: str, lookback: int,
                       details: list[str], errors: list[str]) -> float:
    """Score large order net flow (0-20). Persistent large buy orders = accumulation."""
    score = 0.0
    try:
        market = "sh" if symbol.startswith("6") else "sz"
        df = _retry_call(ak.stock_individual_fund_flow, stock=symbol, market=market)
        if df is None or df.empty:
            details.append("大单数据不可用")
            return 0.0

        recent = df.tail(lookback)

        # Super-large + large order net flow
        super_large_series = [_safe_float(row.get("超大单净流入-净额")) or 0
                              for _, row in recent.iterrows()]
        large_series = [_safe_float(row.get("大单净流入-净额")) or 0
                        for _, row in recent.iterrows()]

        combined = [s + l for s, l in zip(super_large_series, large_series)]
        total_net, total_abs, total_bias = _series_net_bias(combined)
        positive_days = sum(1 for x in combined if x > 0)
        total_days = len(combined)

        if total_days == 0:
            return 0.0

        # Net direction and directional strength.
        if total_net > 0:
            score += max(1.0, min(8.0, total_bias * 8.0))
            details.append(
                f"大单+超大单累计净买入 {total_net/10000:.1f}万，净流强度 {total_bias:.0%}"
            )
        elif total_net < 0:
            score -= min(4.0, abs(total_bias) * 4.0)
            details.append(
                f"大单+超大单累计净卖出 {abs(total_net)/10000:.1f}万，净流强度 {abs(total_bias):.0%}"
            )
        elif total_abs == 0:
            details.append("大单资金基本均衡")

        # Consistency
        pos_ratio = positive_days / total_days
        if pos_ratio >= 0.65:
            score += 6
            details.append(f"大单净买入天数占比 {pos_ratio:.0%}，持续性好")
        elif pos_ratio >= 0.5:
            score += 3
            details.append(f"大单净买入天数占比 {pos_ratio:.0%}")

        # Trend: increasing large orders
        if total_days >= 10:
            recent_5d = sum(combined[-5:])
            older_5d = sum(combined[-10:-5])
            if recent_5d > 0 and recent_5d > older_5d:
                score += 4
                details.append("近5日大单净买入加速")
            elif recent_5d > 0:
                score += 2
                details.append("近5日大单维持净买入")

    except Exception as e:
        errors.append(f"大单评分失败: {e}")

    return max(0, min(20, score))


# ── Helpers ───────────────────────────────────────────────────────

def _normalize_as_of_date(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw[:19], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


def _as_compact_date(date_str: str | None) -> str | None:
    if not date_str:
        return None
    normalized = _normalize_as_of_date(date_str)
    return normalized.replace("-", "") if normalized else None


def _clip_klines_to_as_of(klines: list[dict], as_of_date: str | None) -> list[dict]:
    compact = _as_compact_date(as_of_date)
    if not compact:
        return klines
    filtered = []
    for item in klines:
        date_compact = str(item.get("date", "")).replace("-", "")[:8]
        if date_compact and date_compact <= compact:
            filtered.append(item)
    return filtered


def _fetch_klines_bridge(symbol: str, days: int, as_of_date: str | None = None) -> list[dict]:
    """Fetch K-line from Windows QMT data bridge (fastest path)."""
    bridge_url = os.environ.get("WINDOWS_XTDATA_URL", "").rstrip("/")
    if not bridge_url:
        # Try reading from openclaw config
        try:
            cfg_path = os.path.expanduser("~/.openclaw/openclaw.json")
            with open(cfg_path) as f:
                cfg = json.load(f)
            bridge_url = (cfg.get("plugins", {}).get("entries", {})
                          .get("quant-trading", {}).get("config", {})
                          .get("tradingAccount", {}).get("qmt", {})
                          .get("windowsBridgeUrl", ""))
        except Exception:
            pass
    if not bridge_url:
        raise RuntimeError("no bridge URL configured")

    # Convert 6-digit symbol to QMT format: 600519 → 600519.SH, 300342 → 300342.SZ
    if symbol.startswith("6"):
        qmt_code = f"{symbol}.SH"
    else:
        qmt_code = f"{symbol}.SZ"

    payload = json.dumps({"codes": [qmt_code], "period": "1d", "count": days + 30}).encode()
    req = urllib.request.Request(
        f"{bridge_url}/klines", data=payload,
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read().decode())

    # Bridge response: {"status": "ok", "data": {"300342": [...]}, "count": N}
    data = resp.get("data", resp) if isinstance(resp, dict) else resp
    bars = data.get(symbol, [])
    if not bars:
        raise RuntimeError(f"bridge returned no data for {symbol}")

    klines = []
    for bar in bars:
        date_str = str(bar.get("date", ""))
        # Normalize date: "20260227" → "2026-02-27"
        if len(date_str) == 8 and "-" not in date_str:
            date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        klines.append({
            "date": date_str[:10],
            "open": _safe_float(bar.get("open")) or 0,
            "high": _safe_float(bar.get("high")) or 0,
            "low": _safe_float(bar.get("low")) or 0,
            "close": _safe_float(bar.get("close")) or 0,
            "volume": _safe_float(bar.get("volume")) or 0,
            "amount": _safe_float(bar.get("amount")) or 0,
            "turnover_rate": None,
        })
    clipped = _clip_klines_to_as_of(klines, as_of_date)
    return clipped[-days:]


def _fetch_klines_sina(symbol: str, days: int, as_of_date: str | None = None) -> list[dict]:
    """Fallback: fetch K-line from sina finance (works when push2his is down)."""
    import requests as _req

    prefix = "sz" if symbol.startswith("0") or symbol.startswith("3") else "sh"
    # sina API: scale=240 = daily, datalen = number of bars
    url = "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKLineData"
    s = _req.Session()
    s.trust_env = False
    r = s.get(url, params={
        "symbol": f"{prefix}{symbol}",
        "scale": "240",
        "ma": "no",
        "datalen": str(days + 30),
    }, timeout=15, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.sina.com.cn",
    })
    r.raise_for_status()
    data = r.json()
    if not data:
        return []
    klines = []
    for item in data:
        klines.append({
            "date": str(item.get("day", ""))[:10],
            "open": _safe_float(item.get("open")) or 0,
            "high": _safe_float(item.get("high")) or 0,
            "low": _safe_float(item.get("low")) or 0,
            "close": _safe_float(item.get("close")) or 0,
            "volume": _safe_float(item.get("volume")) or 0,
            "amount": 0,
            "turnover_rate": None,
        })
    clipped = _clip_klines_to_as_of(klines, as_of_date)
    return clipped[-days:]


def _fetch_klines(ak: Any, symbol: str, days: int, errors: list[str],
                  as_of_date: str | None = None) -> list[dict]:
    """Fetch daily K-line data. Priority: bridge → akshare → sina."""
    # 1) Try Windows QMT data bridge (fastest, local network)
    try:
        klines = _fetch_klines_bridge(symbol, days, as_of_date=as_of_date)
        if klines:
            return klines
    except Exception as e:
        errors.append(f"data_bridge K线: {e}")

    # 2) Try akshare (push2his.eastmoney.com)
    try:
        end_date = _as_compact_date(as_of_date) or datetime.now().strftime("%Y%m%d")
        df = _retry_call(
            ak.stock_zh_a_hist,
            symbol=symbol,
            period="daily",
            start_date=(datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d"),
            end_date=end_date,
            adjust="qfq",
        )
        if df is None or df.empty:
            raise ValueError("akshare returned empty data")

        klines = []
        for _, row in df.iterrows():
            klines.append({
                "date": str(row.get("日期", ""))[:10],
                "open": _safe_float(row.get("开盘")) or 0,
                "high": _safe_float(row.get("最高")) or 0,
                "low": _safe_float(row.get("最低")) or 0,
                "close": _safe_float(row.get("收盘")) or 0,
                "volume": _safe_float(row.get("成交量")) or 0,
                "amount": _safe_float(row.get("成交额")) or 0,
                "turnover_rate": _safe_float(row.get("换手率")),
            })
        clipped = _clip_klines_to_as_of(klines, as_of_date)
        return clipped[-days:]
    except Exception as e:
        errors.append(f"akshare K线 (push2his): {e}")

    # 3) Fallback to sina finance K-line API
    try:
        klines = _fetch_klines_sina(symbol, days, as_of_date=as_of_date)
        if klines:
            errors.append("已切换到 sina finance 备用数据源")
            return klines
    except Exception as e2:
        errors.append(f"sina 备用K线也失败: {e2}")

    return []


def _fetch_float_shares(ak: Any, symbol: str) -> float | None:
    """Fetch float shares (流通股) for turnover calculation.

    Uses stock_individual_info_em which hits a different eastmoney endpoint
    that is more reliable than push2his.  Falls back to Tencent stock API
    if eastmoney is unreachable.
    """
    # 1) Try akshare wrapper
    try:
        df = _retry_call(ak.stock_individual_info_em, symbol=symbol)
        if df is not None and not df.empty:
            row = df[df["item"] == "流通股"]
            if not row.empty:
                val = _safe_float(row.iloc[0]["value"])
                if val and val > 0:
                    return val
    except Exception:
        pass

    # 2) Tencent stock API (qt.gtimg.cn) — field 72 = float shares in 股
    try:
        import requests as _req
        prefix = "sz" if symbol.startswith("0") or symbol.startswith("3") else "sh"
        s = _req.Session()
        s.trust_env = False
        r = s.get(f"https://qt.gtimg.cn/q={prefix}{symbol}", timeout=10,
                  headers={"User-Agent": "Mozilla/5.0",
                           "Referer": "https://finance.qq.com"})
        text = r.text
        if "=" in text and '"' in text:
            fields = text.split('"')[1].split("~")
            if len(fields) > 72:
                val = _safe_float(fields[72])
                if val and val > 0:
                    return val
    except Exception:
        pass

    return None


def _enrich_turnover_rate(ak: Any, symbol: str, klines: list[dict],
                          errors: list[str]) -> None:
    """Fill in missing turnover_rate by calculating volume / float_shares * 100.

    Handles volume unit detection: bridge/akshare return volume in 手 (lots of 100),
    sina returns in 股 (shares).  We detect by comparing volume scale against float_shares.
    """
    has_turnover = any(k.get("turnover_rate") is not None for k in klines)
    if has_turnover:
        return

    float_shares = _fetch_float_shares(ak, symbol)
    if not float_shares:
        errors.append("无法获取流通股本，换手率将使用中性估值")
        return

    # Detect volume unit: if median volume << float_shares / 100, likely in lots (手)
    volumes = sorted([k.get("volume") or 0 for k in klines if (k.get("volume") or 0) > 0])
    if not volumes:
        return

    median_vol = volumes[len(volumes) // 2]
    # If median volume is < float_shares / 500, it's likely in lots (手 = 100 shares)
    vol_multiplier = 100 if median_vol < float_shares / 500 else 1

    for k in klines:
        vol = k.get("volume") or 0
        if vol > 0:
            k["turnover_rate"] = round(vol * vol_multiplier / float_shares * 100, 4)


def _estimate_chip_distribution(klines: list[dict], current_price: float) -> dict[str, Any] | None:
    """Estimate chip distribution (CYQ) from historical OHLCV data.

    Uses a simplified cost distribution model: each day's volume creates new
    chips at the day's average price (OHLC/4). Older chips decay (衰减因子)
    to model turnover replacing old holders. From the resulting distribution,
    derive benefitPart, avgCost, concentration, support/resistance.
    """
    if not klines or len(klines) < 20:
        return None

    # Build cost distribution: price_bin → chip_weight
    decay = 0.97  # daily decay factor for old chips
    price_bins: dict[float, float] = {}

    for k in klines:
        vol = k.get("volume") or 0
        if vol <= 0:
            continue
        # Typical price for the day (OHLC average)
        o, h, l, c = k.get("open", 0), k.get("high", 0), k.get("low", 0), k.get("close", 0)
        if o <= 0 or h <= 0 or l <= 0 or c <= 0:
            continue
        typical_price = round((o + h + l + c) / 4, 2)

        # Decay existing chips
        for p in list(price_bins.keys()):
            price_bins[p] *= decay

        # Add new chips at today's typical price
        # Spread volume across the day's range for a more realistic distribution
        spread_prices = set()
        step = max((h - l) / 10, 0.01)
        p = l
        while p <= h:
            spread_prices.add(round(p, 2))
            p += step
        spread_prices.add(round(typical_price, 2))

        vol_per_bin = vol / max(len(spread_prices), 1)
        for sp in spread_prices:
            price_bins[sp] = price_bins.get(sp, 0) + vol_per_bin

    if not price_bins:
        return None

    total_chips = sum(price_bins.values())
    if total_chips <= 0:
        return None

    # Sort by price
    sorted_prices = sorted(price_bins.keys())
    sorted_weights = [price_bins[p] for p in sorted_prices]

    # benefitPart: proportion of chips below current price (profitable)
    profitable = sum(w for p, w in price_bins.items() if p <= current_price)
    benefit_part = profitable / total_chips

    # avgCost: weighted average chip cost
    avg_cost = sum(p * w for p, w in price_bins.items()) / total_chips

    # Cumulative distribution for concentration
    cum_weights = []
    running = 0
    for w in sorted_weights:
        running += w
        cum_weights.append(running / total_chips)

    # Find 70% and 90% concentration ranges
    def _find_range(pct: float) -> tuple[float, float]:
        low_target = (1 - pct) / 2
        high_target = 1 - low_target
        low_price = sorted_prices[0]
        high_price = sorted_prices[-1]
        for i, cw in enumerate(cum_weights):
            if cw >= low_target:
                low_price = sorted_prices[i]
                break
        for i, cw in enumerate(cum_weights):
            if cw >= high_target:
                high_price = sorted_prices[i]
                break
        return low_price, high_price

    low_70, high_70 = _find_range(0.70)
    low_90, high_90 = _find_range(0.90)

    # Concentration = (high - low) / (high + low) / 2
    conc_70 = (high_70 - low_70) / ((high_70 + low_70) / 2) if (high_70 + low_70) > 0 else 0
    conc_90 = (high_90 - low_90) / ((high_90 + low_90) / 2) if (high_90 + low_90) > 0 else 0

    # costAbovePct: portion of chips above current price
    cost_above_pct = 1.0 - benefit_part

    return {
        "benefitPart": round(benefit_part, 4),
        "avgCost": round(avg_cost, 2),
        "concentration70": round(conc_70, 4),
        "concentration90": round(conc_90, 4),
        "support": round(low_90, 2),
        "resistance": round(high_90, 2),
        "chipDensityZone": [round(low_70, 2), round(high_70, 2)],
        "costAbovePct": round(cost_above_pct, 4),
        "estimated": True,  # flag: this is from local calculation, not eastmoney CYQ
    }


def _fetch_stock_name(ak: Any, symbol: str) -> str:
    """Fetch stock name. Falls back to sinajs if push2 is down."""
    # Try sinajs first (lightweight, doesn't use push2)
    try:
        import requests as _req
        prefix = "sz" if symbol.startswith("0") or symbol.startswith("3") else "sh"
        s = _req.Session()
        s.trust_env = False
        r = s.get(f"https://hq.sinajs.cn/list={prefix}{symbol}", timeout=5,
                  headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
        text = r.text
        if "=" in text and '"' in text:
            parts = text.split('"')[1].split(",")
            if parts and parts[0]:
                return parts[0]
    except Exception:
        pass
    # Try akshare spot
    try:
        spot = _retry_call(ak.stock_zh_a_spot_em)
        if spot is not None and not spot.empty:
            match = spot[spot["代码"] == symbol]
            if not match.empty:
                return str(match.iloc[0].get("名称", symbol))
    except Exception:
        pass
    return symbol


def _get_active_stocks(ak: Any, limit: int = 30) -> list[str]:
    """Get today's most actively traded stocks as default scan pool."""
    try:
        df = _retry_call(ak.stock_zh_a_spot_em)
        if df is not None and not df.empty:
            # Filter: price > 3, volume > 0, exclude ST
            filtered = df[
                (df.get("最新价", df.get("收盘", 0)).astype(float) > 3) &
                (df.get("成交量", 0).astype(float) > 0)
            ]
            # Exclude ST stocks
            if "名称" in filtered.columns:
                filtered = filtered[~filtered["名称"].str.contains("ST", na=False)]
            # Sort by turnover rate descending
            if "换手率" in filtered.columns:
                filtered = filtered.sort_values("换手率", ascending=False)
            codes = filtered.head(limit)["代码"].tolist()
            return [str(c) for c in codes]
    except Exception:
        pass
    return []


def _safe_float(val: Any, default: float = 0.0) -> float | None:
    """Convert value to float safely."""
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN
            return None
        return round(f, 4)
    except (TypeError, ValueError):
        return None


# ── CLI entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    try:
        if "--json" in sys.argv:
            raw = sys.stdin.read()
            cfg = json.loads(raw) if raw.strip() else {}
        else:
            cfg = json.loads(os.environ.get("QUANT_CONFIG", "{}"))

        result = run_chip_accumulation(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
