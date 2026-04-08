"""
Data analysis engine — general-purpose financial data analysis.

Statistical summaries, cross-market comparison, regression,
distribution analysis, anomaly detection, regime classification,
and macro indicator analysis.

Input (JSON via stdin):
  {
    "action": "statistics"|"cross_market"|"regression"|"distribution"|"anomaly"|"regime"|"macro",
    "symbols": "600519,000858",
    "indicator": "gdp"|"cpi"|"pmi"|"m2",
    "period": 252,
    "factors": "market,size,value",
    "startDate": "2024-01-01",
    "endDate": "2025-01-01"
  }

Output: JSON with analysis results.
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

# ── Proxy bypass ──────────────────────────────────────────────────────────────
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def _parse_float(val: Any) -> float:
    if val is None or val == "" or val == "--":
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _detect_market(symbol: str) -> str:
    s = symbol.upper().strip()
    if s.endswith(".HK"):
        return "hk"
    if any(c.isalpha() for c in s) and not s.startswith(("0", "3", "6")):
        return "us"
    return "cn"


def _get_price_series(symbol: str, start: str, end: str) -> list[dict]:
    """Get daily price series for a symbol."""
    market = _detect_market(symbol)

    if market == "cn":
        return _get_cn_prices(symbol, start, end)
    else:
        return _get_global_prices(symbol, start, end)


def _get_cn_prices(symbol: str, start: str, end: str) -> list[dict]:
    """Fetch A-share price history from akshare."""
    try:
        import akshare as ak
        df = ak.stock_zh_a_hist(
            symbol=symbol,
            period="daily",
            start_date=start.replace("-", ""),
            end_date=end.replace("-", ""),
            adjust="qfq",
        )
        if df is None or df.empty:
            return []

        result = []
        for _, row in df.iterrows():
            result.append({
                "date": str(row.get("日期", "")),
                "open": float(row.get("开盘", 0)),
                "high": float(row.get("最高", 0)),
                "low": float(row.get("最低", 0)),
                "close": float(row.get("收盘", 0)),
                "volume": float(row.get("成交量", 0)),
                "turnover": float(row.get("成交额", 0)),
            })
        return result

    except ImportError:
        return []
    except Exception:
        return []


def _get_global_prices(symbol: str, start: str, end: str) -> list[dict]:
    """Fetch global price history from yfinance."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start, end=end)
        if df is None or df.empty:
            return []

        result = []
        for date, row in df.iterrows():
            result.append({
                "date": str(date.date()) if hasattr(date, "date") else str(date),
                "open": float(row.get("Open", 0)),
                "high": float(row.get("High", 0)),
                "low": float(row.get("Low", 0)),
                "close": float(row.get("Close", 0)),
                "volume": float(row.get("Volume", 0)),
            })
        return result

    except ImportError:
        return []
    except Exception:
        return []


def _calc_returns(closes: list[float]) -> list[float]:
    """Calculate daily returns."""
    if len(closes) < 2:
        return []
    return [(closes[i] / closes[i-1] - 1) for i in range(1, len(closes))]


def _std(values: list[float]) -> float:
    if not values:
        return 0
    mean = sum(values) / len(values)
    variance = sum((x - mean) ** 2 for x in values) / len(values)
    return variance ** 0.5


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0
    sorted_v = sorted(values)
    idx = pct * (len(sorted_v) - 1)
    lower = sorted_v[int(idx)]
    upper = sorted_v[min(int(idx) + 1, len(sorted_v) - 1)]
    frac = idx - int(idx)
    return lower + frac * (upper - lower)


# ── Analysis Functions ─────────────────────────────────────────────

def _statistics(config: dict) -> dict:
    """Statistical summary of a stock's returns."""
    symbols_str = config.get("symbols", "")
    period = config.get("period", 252)

    if not symbols_str:
        return {"error": "symbols is required"}

    symbols = [s.strip() for s in str(symbols_str).split(",") if s.strip()]
    end = config.get("endDate") or datetime.now().strftime("%Y-%m-%d")
    start = config.get("startDate") or (datetime.now() - timedelta(days=period)).strftime("%Y-%m-%d")

    results = []
    for sym in symbols[:10]:  # limit to 10
        prices = _get_price_series(sym, start, end)
        if not prices:
            results.append({"symbol": sym, "error": "无数据"})
            continue

        closes = [p["close"] for p in prices if p["close"] > 0]
        if len(closes) < 5:
            results.append({"symbol": sym, "error": "数据不足"})
            continue

        returns = _calc_returns(closes)
        mean_ret = sum(returns) / len(returns)
        std_ret = _std(returns)

        # Skewness
        n = len(returns)
        if std_ret > 0:
            skew = sum((r - mean_ret) ** 3 for r in returns) / (n * std_ret ** 3)
            kurt = sum((r - mean_ret) ** 4 for r in returns) / (n * std_ret ** 4) - 3
        else:
            skew = 0
            kurt = 0

        total_return = (closes[-1] / closes[0] - 1) * 100
        ann_return = total_return * 252 / len(returns) if returns else 0
        ann_vol = std_ret * math.sqrt(252) * 100

        results.append({
            "symbol": sym,
            "dataPoints": len(closes),
            "totalReturn": round(total_return, 2),
            "annualizedReturn": round(ann_return, 2),
            "annualizedVolatility": round(ann_vol, 2),
            "dailyMean": round(mean_ret * 100, 4),
            "dailyStd": round(std_ret * 100, 4),
            "skewness": round(skew, 3),
            "kurtosis": round(kurt, 3),
            "maxPrice": round(max(closes), 2),
            "minPrice": round(min(closes), 2),
            "currentPrice": round(closes[-1], 2),
            "percentile25": round(_percentile(returns, 0.25) * 100, 4),
            "percentile75": round(_percentile(returns, 0.75) * 100, 4),
            "sharpe": round(ann_return / ann_vol, 3) if ann_vol > 0 else 0,
        })

    return {
        "period": f"{start} to {end}",
        "symbolCount": len(results),
        "statistics": results,
    }


def _cross_market(config: dict) -> dict:
    """Cross-market comparison."""
    symbols_str = config.get("symbols", "")
    period = config.get("period", 252)

    if not symbols_str:
        return {"error": "symbols is required (comma-separated, from different markets)"}

    symbols = [s.strip() for s in str(symbols_str).split(",") if s.strip()]
    end = config.get("endDate") or datetime.now().strftime("%Y-%m-%d")
    start = config.get("startDate") or (datetime.now() - timedelta(days=period)).strftime("%Y-%m-%d")

    comparisons = []
    for sym in symbols[:10]:
        prices = _get_price_series(sym, start, end)
        if not prices:
            comparisons.append({"symbol": sym, "market": _detect_market(sym), "error": "无数据"})
            continue

        closes = [p["close"] for p in prices if p["close"] > 0]
        if len(closes) < 5:
            comparisons.append({"symbol": sym, "market": _detect_market(sym), "error": "数据不足"})
            continue

        returns = _calc_returns(closes)
        total_return = (closes[-1] / closes[0] - 1) * 100
        vol = _std(returns) * math.sqrt(252) * 100

        comparisons.append({
            "symbol": sym,
            "market": _detect_market(sym),
            "totalReturn": round(total_return, 2),
            "annualizedVolatility": round(vol, 2),
            "sharpe": round(total_return / vol, 3) if vol > 0 else 0,
            "maxDrawdown": round(_max_drawdown(closes) * 100, 2),
            "dataPoints": len(closes),
        })

    # Rank by return
    ranked = sorted(
        [c for c in comparisons if "error" not in c],
        key=lambda x: x.get("totalReturn", 0),
        reverse=True,
    )
    for i, c in enumerate(ranked):
        c["rank"] = i + 1

    return {
        "period": f"{start} to {end}",
        "comparison": comparisons,
    }


def _max_drawdown(closes: list[float]) -> float:
    """Calculate max drawdown."""
    if len(closes) < 2:
        return 0
    peak = closes[0]
    max_dd = 0
    for c in closes:
        if c > peak:
            peak = c
        dd = (peak - c) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _regression(config: dict) -> dict:
    """Simple regression analysis."""
    symbols_str = config.get("symbols", "")
    period = config.get("period", 252)

    if not symbols_str:
        return {"error": "symbols is required (first symbol = dependent variable)"}

    symbols = [s.strip() for s in str(symbols_str).split(",") if s.strip()]
    if len(symbols) < 2:
        return {"error": "至少需要2个标的（第1个为因变量，其余为自变量）"}

    end = config.get("endDate") or datetime.now().strftime("%Y-%m-%d")
    start = config.get("startDate") or (datetime.now() - timedelta(days=period)).strftime("%Y-%m-%d")

    # Get price data for all symbols
    all_returns: dict[str, list[float]] = {}
    min_len = float("inf")

    for sym in symbols[:6]:
        prices = _get_price_series(sym, start, end)
        if prices:
            closes = [p["close"] for p in prices if p["close"] > 0]
            returns = _calc_returns(closes)
            all_returns[sym] = returns
            min_len = min(min_len, len(returns))

    if len(all_returns) < 2:
        return {"error": "数据不足，无法进行回归分析"}

    # Trim to same length
    for sym in all_returns:
        all_returns[sym] = all_returns[sym][:int(min_len)]

    dep_var = symbols[0]
    y = all_returns.get(dep_var, [])

    if not y:
        return {"error": f"{dep_var} 无数据"}

    # Simple linear regression against each independent variable
    regressions = []
    for sym in symbols[1:]:
        x = all_returns.get(sym, [])
        if not x:
            continue

        n = min(len(x), len(y))
        x_n = x[:n]
        y_n = y[:n]

        x_mean = sum(x_n) / n
        y_mean = sum(y_n) / n

        ss_xy = sum((x_n[i] - x_mean) * (y_n[i] - y_mean) for i in range(n))
        ss_xx = sum((x_n[i] - x_mean) ** 2 for i in range(n))
        ss_yy = sum((y_n[i] - y_mean) ** 2 for i in range(n))

        beta = ss_xy / ss_xx if ss_xx > 0 else 0
        alpha = y_mean - beta * x_mean
        r_squared = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_xx > 0 and ss_yy > 0 else 0
        correlation = (ss_xy / (ss_xx ** 0.5 * ss_yy ** 0.5)) if ss_xx > 0 and ss_yy > 0 else 0

        regressions.append({
            "independentVar": sym,
            "alpha": round(alpha, 6),
            "beta": round(beta, 4),
            "rSquared": round(r_squared, 4),
            "correlation": round(correlation, 4),
            "dataPoints": n,
        })

    return {
        "dependentVar": dep_var,
        "period": f"{start} to {end}",
        "regressions": regressions,
    }


def _distribution(config: dict) -> dict:
    """Return distribution analysis."""
    symbols_str = config.get("symbols", "")
    period = config.get("period", 252)

    if not symbols_str:
        return {"error": "symbols is required"}

    symbols = [s.strip() for s in str(symbols_str).split(",") if s.strip()]
    end = config.get("endDate") or datetime.now().strftime("%Y-%m-%d")
    start = config.get("startDate") or (datetime.now() - timedelta(days=period)).strftime("%Y-%m-%d")

    results = []
    for sym in symbols[:5]:
        prices = _get_price_series(sym, start, end)
        if not prices:
            results.append({"symbol": sym, "error": "无数据"})
            continue

        closes = [p["close"] for p in prices if p["close"] > 0]
        returns = _calc_returns(closes)

        if len(returns) < 10:
            results.append({"symbol": sym, "error": "数据不足"})
            continue

        mean = sum(returns) / len(returns)
        std = _std(returns)
        n = len(returns)

        skew = sum((r - mean) ** 3 for r in returns) / (n * std ** 3) if std > 0 else 0
        kurt = sum((r - mean) ** 4 for r in returns) / (n * std ** 4) - 3 if std > 0 else 0

        # Distribution histogram (10 bins)
        sorted_r = sorted(returns)
        min_r = sorted_r[0]
        max_r = sorted_r[-1]
        bin_width = (max_r - min_r) / 10 if max_r > min_r else 0.01
        bins = []
        for i in range(10):
            lo = min_r + i * bin_width
            hi = lo + bin_width
            count = sum(1 for r in returns if lo <= r < hi or (i == 9 and r == hi))
            bins.append({
                "range": f"{lo*100:.2f}% ~ {hi*100:.2f}%",
                "count": count,
                "frequency": round(count / n, 3),
            })

        # Jarque-Bera test approximation
        jb = (n / 6) * (skew ** 2 + (kurt ** 2) / 4)
        is_normal = jb < 5.99  # chi2(2) 5% critical value

        results.append({
            "symbol": sym,
            "dataPoints": n,
            "mean": round(mean * 100, 4),
            "std": round(std * 100, 4),
            "skewness": round(skew, 3),
            "kurtosis": round(kurt, 3),
            "jarqueBera": round(jb, 2),
            "isNormal": is_normal,
            "fatTail": kurt > 1,
            "var95": round(_percentile(returns, 0.05) * 100, 4),
            "var99": round(_percentile(returns, 0.01) * 100, 4),
            "histogram": bins,
        })

    return {
        "period": f"{start} to {end}",
        "distributions": results,
    }


def _anomaly(config: dict) -> dict:
    """Detect price/volume anomalies."""
    symbols_str = config.get("symbols", "")
    period = config.get("period", 60)

    if not symbols_str:
        return {"error": "symbols is required"}

    symbols = [s.strip() for s in str(symbols_str).split(",") if s.strip()]
    end = config.get("endDate") or datetime.now().strftime("%Y-%m-%d")
    start = config.get("startDate") or (datetime.now() - timedelta(days=period)).strftime("%Y-%m-%d")

    results = []
    for sym in symbols[:5]:
        prices = _get_price_series(sym, start, end)
        if len(prices) < 10:
            results.append({"symbol": sym, "error": "数据不足"})
            continue

        closes = [p["close"] for p in prices]
        volumes = [p.get("volume", 0) for p in prices]
        returns = _calc_returns(closes)

        mean_ret = sum(returns) / len(returns)
        std_ret = _std(returns)

        mean_vol = sum(volumes) / len(volumes) if volumes else 0
        std_vol = _std(volumes)

        anomalies = []
        for i, p in enumerate(prices):
            if i == 0:
                continue
            ret = returns[i - 1] if i - 1 < len(returns) else 0
            vol = p.get("volume", 0)

            is_price_anomaly = abs(ret - mean_ret) > 2.5 * std_ret if std_ret > 0 else False
            is_volume_anomaly = (vol - mean_vol) > 2.5 * std_vol if std_vol > 0 else False

            if is_price_anomaly or is_volume_anomaly:
                anomalies.append({
                    "date": p["date"],
                    "close": p["close"],
                    "return": round(ret * 100, 2),
                    "volume": vol,
                    "priceAnomaly": is_price_anomaly,
                    "volumeAnomaly": is_volume_anomaly,
                    "zScore": round((ret - mean_ret) / std_ret, 2) if std_ret > 0 else 0,
                })

        results.append({
            "symbol": sym,
            "anomalyCount": len(anomalies),
            "anomalies": anomalies[:20],
            "thresholds": {
                "priceZScore": 2.5,
                "volumeZScore": 2.5,
            },
        })

    return {
        "period": f"{start} to {end}",
        "results": results,
    }


def _regime(config: dict) -> dict:
    """Market regime classification (bull/bear/sideways)."""
    symbols_str = config.get("symbols", "")
    period = config.get("period", 252)

    # Default to index if no symbol
    if not symbols_str:
        symbols_str = "000001"  # Shanghai Composite

    symbols = [s.strip() for s in str(symbols_str).split(",") if s.strip()]
    end = config.get("endDate") or datetime.now().strftime("%Y-%m-%d")
    start = config.get("startDate") or (datetime.now() - timedelta(days=period)).strftime("%Y-%m-%d")

    results = []
    for sym in symbols[:3]:
        prices = _get_price_series(sym, start, end)
        if len(prices) < 20:
            results.append({"symbol": sym, "error": "数据不足"})
            continue

        closes = [p["close"] for p in prices]

        # Calculate moving averages
        ma20 = sum(closes[-20:]) / 20
        ma60 = sum(closes[-min(60, len(closes)):]) / min(60, len(closes))
        ma120 = sum(closes[-min(120, len(closes)):]) / min(120, len(closes))

        current = closes[-1]
        returns = _calc_returns(closes)

        # Recent trend (last 20 days)
        recent_return = (closes[-1] / closes[-min(20, len(closes))]) - 1
        vol = _std(returns[-20:]) * math.sqrt(252) * 100 if len(returns) >= 20 else 0

        # Regime classification
        if current > ma20 > ma60:
            if recent_return > 0.05:
                regime = "强势上涨"
            else:
                regime = "温和上涨"
        elif current < ma20 < ma60:
            if recent_return < -0.05:
                regime = "强势下跌"
            else:
                regime = "温和下跌"
        elif vol < 15:
            regime = "低波动震荡"
        else:
            regime = "高波动震荡"

        results.append({
            "symbol": sym,
            "regime": regime,
            "currentPrice": round(current, 2),
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
            "ma120": round(ma120, 2),
            "recentReturn": round(recent_return * 100, 2),
            "annualizedVolatility": round(vol, 2),
            "priceVsMa20": round((current / ma20 - 1) * 100, 2),
            "priceVsMa60": round((current / ma60 - 1) * 100, 2),
        })

    return {
        "period": f"{start} to {end}",
        "regimes": results,
    }


def _macro(config: dict) -> dict:
    """Macro indicator analysis."""
    indicator = config.get("indicator", "gdp")
    limit = config.get("limit", 20)

    try:
        import akshare as ak

        indicator_map = {
            "gdp": ("macro_china_gdp", "中国GDP"),
            "cpi": ("macro_china_cpi", "中国CPI"),
            "pmi": ("macro_china_pmi", "中国PMI"),
            "m2": ("macro_china_money_supply", "中国货币供应M2"),
        }

        func_name, label = indicator_map.get(indicator, (None, None))
        if not func_name:
            return {"error": f"不支持的宏观指标: {indicator}，支持: gdp, cpi, pmi, m2"}

        func = getattr(ak, func_name, None)
        if not func:
            return {"error": f"akshare 未提供函数: {func_name}"}

        df = func()
        if df is None or df.empty:
            return {"error": f"无法获取{label}数据"}

        # Convert to records
        records = []
        for _, row in df.tail(limit).iterrows():
            record = {}
            for col in df.columns:
                val = row[col]
                if hasattr(val, "item"):
                    record[str(col)] = val.item()
                else:
                    record[str(col)] = str(val) if val is not None else None
            records.append(record)

        return {
            "indicator": indicator,
            "label": label,
            "count": len(records),
            "data": records,
        }

    except ImportError:
        return {"error": "akshare not installed"}
    except Exception as e:
        return {"error": str(e)}


# ── Macro Correlation ────────────────────────────────────────────

def _macro_correlation(config: dict) -> dict:
    """Correlate macro indicator changes with stock/index returns."""
    indicator = config.get("indicator", "pmi")
    symbols_raw = config.get("symbols", "000300")
    period = int(config.get("period", 60))
    symbols = [s.strip() for s in symbols_raw.split(",") if s.strip()]

    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    # Fetch macro indicator series
    indicator_fn_map = {
        "pmi": lambda: ak.macro_china_pmi(),
        "cpi": lambda: ak.macro_china_cpi_monthly(),
        "ppi": lambda: ak.macro_china_ppi(),
        "m2": lambda: ak.macro_china_money_supply(),
        "gdp": lambda: ak.macro_china_gdp(),
    }
    fn = indicator_fn_map.get(indicator)
    if not fn:
        return {"error": f"Unsupported indicator for correlation: {indicator}. Supported: {list(indicator_fn_map.keys())}"}

    try:
        macro_df = fn()
    except Exception as e:
        return {"error": f"Failed to fetch {indicator}: {e}"}

    if macro_df is None or macro_df.empty:
        return {"error": f"No data for indicator {indicator}"}

    end_str = config.get("endDate", datetime.now().strftime("%Y-%m-%d"))
    start_str = config.get("startDate", (datetime.now() - timedelta(days=period * 30)).strftime("%Y-%m-%d"))

    results = []
    for sym in symbols[:5]:
        prices = _get_price_series(sym, start_str, end_str)
        if len(prices) < 10:
            results.append({"symbol": sym, "error": "insufficient price data"})
            continue

        closes = [p["close"] for p in prices]
        returns_monthly = []
        step = max(1, len(closes) // min(len(closes), 20))
        for i in range(step, len(closes), step):
            returns_monthly.append((closes[i] - closes[i - step]) / closes[i - step])

        # Trim to match lengths
        macro_vals = []
        for col in macro_df.columns:
            if any(kw in str(col) for kw in ["ֵ", "当月", "同比", "指数"]):
                vals = macro_df[col].dropna().tolist()
                macro_vals = [_parse_float(v) for v in vals[-len(returns_monthly):]]
                break
        if not macro_vals:
            macro_vals = [_parse_float(v) for v in macro_df.iloc[:, -1].dropna().tolist()[-len(returns_monthly):]]

        n = min(len(returns_monthly), len(macro_vals))
        if n < 5:
            results.append({"symbol": sym, "error": "insufficient overlap"})
            continue

        r = returns_monthly[-n:]
        m = macro_vals[-n:]

        mean_r = sum(r) / n
        mean_m = sum(m) / n
        cov = sum((r[i] - mean_r) * (m[i] - mean_m) for i in range(n)) / n
        std_r = (sum((x - mean_r) ** 2 for x in r) / n) ** 0.5
        std_m = (sum((x - mean_m) ** 2 for x in m) / n) ** 0.5
        corr = cov / (std_r * std_m) if std_r > 0 and std_m > 0 else 0

        results.append({
            "symbol": sym,
            "indicator": indicator,
            "correlation": round(corr, 4),
            "data_points": n,
            "interpretation": "正相关" if corr > 0.3 else ("负相关" if corr < -0.3 else "弱相关"),
        })

    return {"action": "macro_correlation", "indicator": indicator, "results": results}


# ── Sentiment Backtest ───────────────────────────────────────────

def _sentiment_backtest(config: dict) -> dict:
    """Backtest returns following hot-stock sentiment spikes."""
    symbols_raw = config.get("symbols", "")
    holding_days = int(config.get("period", 5))
    if not symbols_raw:
        return {"error": "symbols required for sentiment_backtest"}

    symbols = [s.strip() for s in symbols_raw.split(",") if s.strip()]

    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    end_str = config.get("endDate", datetime.now().strftime("%Y-%m-%d"))
    start_str = config.get("startDate", (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d"))

    results = []
    for sym in symbols[:5]:
        prices = _get_price_series(sym, start_str, end_str)
        if len(prices) < 30:
            results.append({"symbol": sym, "error": "insufficient price data"})
            continue

        closes = [p["close"] for p in prices]
        volumes = [_parse_float(p.get("volume", 0)) for p in prices]

        # Detect volume-spike events as proxy for sentiment surges
        # (volume > 2x 20-day average)
        avg_window = 20
        events = []
        for i in range(avg_window, len(volumes)):
            avg_vol = sum(volumes[i - avg_window:i]) / avg_window
            if avg_vol > 0 and volumes[i] > 2.0 * avg_vol:
                events.append(i)

        if not events:
            results.append({"symbol": sym, "events_found": 0, "note": "no volume spikes detected"})
            continue

        # Calculate forward returns after each event
        forward_returns = []
        for idx in events:
            end_idx = min(idx + holding_days, len(closes) - 1)
            if end_idx > idx:
                fwd = (closes[end_idx] - closes[idx]) / closes[idx]
                forward_returns.append(fwd)

        if not forward_returns:
            results.append({"symbol": sym, "events_found": len(events), "note": "no forward data"})
            continue

        avg_fwd = sum(forward_returns) / len(forward_returns)
        win_rate = sum(1 for r in forward_returns if r > 0) / len(forward_returns)
        max_gain = max(forward_returns)
        max_loss = min(forward_returns)

        results.append({
            "symbol": sym,
            "events_found": len(events),
            "holding_days": holding_days,
            "avg_forward_return": round(avg_fwd * 100, 2),
            "win_rate": round(win_rate * 100, 1),
            "max_gain_pct": round(max_gain * 100, 2),
            "max_loss_pct": round(max_loss * 100, 2),
            "interpretation": "正期望" if avg_fwd > 0.005 else ("负期望" if avg_fwd < -0.005 else "中性"),
        })

    return {"action": "sentiment_backtest", "holding_days": holding_days, "results": results}


# ── Main ─────────────────────────────────────────────────────────

ACTIONS = {
    "statistics": _statistics,
    "cross_market": _cross_market,
    "regression": _regression,
    "distribution": _distribution,
    "anomaly": _anomaly,
    "regime": _regime,
    "macro": _macro,
    "macro_correlation": _macro_correlation,
    "sentiment_backtest": _sentiment_backtest,
}


def main():
    config = json.loads(sys.stdin.read())
    action = config.get("action", "statistics")

    handler = ACTIONS.get(action)
    if not handler:
        print(json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False))
        return

    try:
        result = handler(config)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({
            "error": str(e),
            "traceback": traceback.format_exc(),
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
