"""
stock_compare — Multi-stock comparison engine.

Compare any N arbitrary stocks across price performance, technical indicators,
and fundamentals side-by-side (not limited to same-industry peers).

Input:
  { "action": "compare" | "performance" | "fundamentals" | "technical",
    "symbols": ["600519", "000858", "000333"],
    "period": "1m" | "3m" | "6m" | "1y" }

Output: comparison table with unified metrics across all specified stocks.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

os.environ.setdefault("no_proxy", "eastmoney.com,push2his.eastmoney.com,datacenter-web.eastmoney.com")
os.environ.setdefault("NO_PROXY", "eastmoney.com,push2his.eastmoney.com,datacenter-web.eastmoney.com")


PERIOD_DAYS = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "2y": 730}


def run_stock_compare(config: dict) -> dict:
    """Dispatch stock comparison based on action."""
    action = config.get("action", "compare")
    symbols = config.get("symbols", [])
    if isinstance(symbols, str):
        symbols = [s.strip() for s in symbols.split(",") if s.strip()]
    if len(symbols) < 2:
        return {"error": "至少需要2个股票代码进行对比"}
    if len(symbols) > 10:
        return {"error": "最多支持10个股票同时对比"}

    period = config.get("period", "3m")
    errors: list[str] = []

    if action == "compare":
        return _full_compare(symbols, period, errors)
    elif action == "performance":
        return _performance_compare(symbols, period, errors)
    elif action == "fundamentals":
        return _fundamentals_compare(symbols, errors)
    elif action == "technical":
        return _technical_compare(symbols, errors)
    else:
        return {"error": f"未知操作: {action}"}


def _get_stock_info(symbol: str) -> dict[str, Any]:
    """Get basic stock info from akshare spot data."""
    try:
        import akshare as ak
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return {}
        row = df[df["代码"] == symbol]
        if row.empty:
            return {}
        row = row.iloc[0]
        return {
            "code": symbol,
            "name": str(row.get("名称", "")),
            "price": _safe_float(row.get("最新价")),
            "changePct": _safe_float(row.get("涨跌幅")),
            "volume": _safe_float(row.get("成交量")),
            "amount": _safe_float(row.get("成交额")),
            "pe": _safe_float(row.get("市盈率-动态")),
            "pb": _safe_float(row.get("市净率")),
            "totalMv": _safe_float(row.get("总市值")),
            "circulatingMv": _safe_float(row.get("流通市值")),
            "turnoverRate": _safe_float(row.get("换手率")),
            "high52w": _safe_float(row.get("52周最高")),
            "low52w": _safe_float(row.get("52周最低")),
            "amplitude": _safe_float(row.get("振幅")),
        }
    except Exception:
        return {"code": symbol, "error": "获取数据失败"}


def _get_klines(symbol: str, days: int) -> list[dict]:
    """Get historical klines for a stock."""
    try:
        import akshare as ak
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily",
                                 start_date=start_date, end_date=end_date, adjust="qfq")
        if df is None or df.empty:
            return []
        klines = []
        for _, row in df.iterrows():
            klines.append({
                "date": str(row["日期"]),
                "open": float(row["开盘"]),
                "high": float(row["最高"]),
                "low": float(row["最低"]),
                "close": float(row["收盘"]),
                "volume": float(row["成交量"]),
                "amount": float(row.get("成交额", 0)),
            })
        return klines[-days:]
    except Exception:
        return []


def _full_compare(symbols: list[str], period: str, errors: list[str]) -> dict:
    """Full comparison: performance + fundamentals + technicals."""
    days = PERIOD_DAYS.get(period, 90)
    stocks = []

    for symbol in symbols:
        info = _get_stock_info(symbol)
        klines = _get_klines(symbol, days)

        perf = _calc_performance(klines, days) if klines else {}
        tech = _calc_technicals(klines) if klines else {}

        stock_data = {**info, **perf, **tech}
        stocks.append(stock_data)

    # Build comparison summary
    if not stocks:
        return {"error": "无法获取任何股票数据"}

    # Find best/worst in key metrics
    ranking = _build_ranking(stocks)

    result: dict[str, Any] = {
        "action": "compare",
        "period": period,
        "count": len(stocks),
        "stocks": stocks,
        "ranking": ranking,
    }
    if errors:
        result["errors"] = errors
    return result


def _performance_compare(symbols: list[str], period: str, errors: list[str]) -> dict:
    """Compare price performance over a period."""
    days = PERIOD_DAYS.get(period, 90)
    stocks = []

    for symbol in symbols:
        info = _get_stock_info(symbol)
        klines = _get_klines(symbol, days)
        perf = _calc_performance(klines, days) if klines else {}
        stocks.append({
            "code": info.get("code", symbol),
            "name": info.get("name", ""),
            "price": info.get("price"),
            **perf,
        })

    result: dict[str, Any] = {
        "action": "performance",
        "period": period,
        "count": len(stocks),
        "stocks": stocks,
        "ranking": _rank_by(stocks, "returnPct"),
    }
    if errors:
        result["errors"] = errors
    return result


def _fundamentals_compare(symbols: list[str], errors: list[str]) -> dict:
    """Compare fundamental metrics."""
    stocks = []
    for symbol in symbols:
        info = _get_stock_info(symbol)
        # Try to get additional fundamentals
        extra = _get_fundamentals(symbol)
        stocks.append({
            "code": info.get("code", symbol),
            "name": info.get("name", ""),
            "price": info.get("price"),
            "pe": info.get("pe"),
            "pb": info.get("pb"),
            "totalMv": info.get("totalMv"),
            "circulatingMv": info.get("circulatingMv"),
            "turnoverRate": info.get("turnoverRate"),
            **extra,
        })

    result: dict[str, Any] = {
        "action": "fundamentals",
        "count": len(stocks),
        "stocks": stocks,
    }
    if errors:
        result["errors"] = errors
    return result


def _technical_compare(symbols: list[str], errors: list[str]) -> dict:
    """Compare technical indicators."""
    stocks = []
    for symbol in symbols:
        info = _get_stock_info(symbol)
        klines = _get_klines(symbol, 120)
        tech = _calc_technicals(klines) if klines else {}
        stocks.append({
            "code": info.get("code", symbol),
            "name": info.get("name", ""),
            "price": info.get("price"),
            **tech,
        })

    result: dict[str, Any] = {
        "action": "technical",
        "count": len(stocks),
        "stocks": stocks,
    }
    if errors:
        result["errors"] = errors
    return result


def _calc_performance(klines: list[dict], days: int) -> dict:
    """Calculate performance metrics from klines."""
    import numpy as np

    if not klines:
        return {}

    closes = [k["close"] for k in klines]
    if len(closes) < 2:
        return {}

    start_price = closes[0]
    end_price = closes[-1]
    return_pct = (end_price / start_price - 1) * 100 if start_price > 0 else 0

    # Max drawdown
    peak = closes[0]
    max_dd = 0
    for c in closes:
        if c > peak:
            peak = c
        dd = (peak - c) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Volatility (annualized)
    returns = np.diff(closes) / np.array(closes[:-1])
    volatility = float(np.std(returns) * (252 ** 0.5) * 100) if len(returns) > 1 else 0

    # Sharpe (simplified, risk-free = 2.5%)
    avg_return = float(np.mean(returns)) * 252
    sharpe = (avg_return - 0.025) / (float(np.std(returns)) * (252 ** 0.5)) if np.std(returns) > 0 else 0

    # High/Low during period
    high = max(k["high"] for k in klines)
    low = min(k["low"] for k in klines)

    return {
        "returnPct": round(return_pct, 2),
        "maxDrawdown": round(max_dd, 2),
        "volatility": round(volatility, 2),
        "sharpe": round(sharpe, 2),
        "periodHigh": round(high, 2),
        "periodLow": round(low, 2),
        "avgVolume": round(sum(k["volume"] for k in klines) / len(klines), 0),
    }


def _calc_technicals(klines: list[dict]) -> dict:
    """Calculate technical indicators from klines."""
    import numpy as np

    if not klines or len(klines) < 5:
        return {}

    closes = np.array([k["close"] for k in klines], dtype=float)

    result: dict[str, Any] = {}

    # MAs
    for period in [5, 10, 20, 60]:
        if len(closes) >= period:
            ma_val = float(np.mean(closes[-period:]))
            result[f"ma{period}"] = round(ma_val, 2)
            result[f"aboveMa{period}"] = bool(closes[-1] > ma_val)

    # RSI (14-period)
    if len(closes) >= 15:
        diffs = np.diff(closes)
        gains = np.where(diffs > 0, diffs, 0)
        losses = np.where(diffs < 0, -diffs, 0)
        avg_gain = float(np.mean(gains[-14:]))
        avg_loss = float(np.mean(losses[-14:]))
        rs = avg_gain / avg_loss if avg_loss > 0 else 100
        rsi = 100 - 100 / (1 + rs)
        result["rsi14"] = round(rsi, 2)

    # MACD
    if len(closes) >= 26:
        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        dif = ema12[-1] - ema26[-1]
        macd_line = ema12 - ema26
        dea = _ema(macd_line[-9:], 9)[-1] if len(macd_line) >= 9 else dif
        macd_histogram = 2 * (dif - dea)
        result["macdDIF"] = round(float(dif), 4)
        result["macdDEA"] = round(float(dea), 4)
        result["macdHistogram"] = round(float(macd_histogram), 4)
        result["macdSignal"] = "金叉" if dif > dea else "死叉"

    # Distance from 52-week high/low
    if len(closes) >= 20:
        period_high = float(np.max(closes))
        period_low = float(np.min(closes))
        current = float(closes[-1])
        result["distFromHigh"] = round((current / period_high - 1) * 100, 2)
        result["distFromLow"] = round((current / period_low - 1) * 100, 2)

    return result


def _get_fundamentals(symbol: str) -> dict:
    """Get additional fundamental data."""
    try:
        import akshare as ak
        # Try to get financial indicators
        df = ak.stock_financial_abstract_ths(symbol=symbol)
        if df is not None and not df.empty:
            row = df.iloc[0]
            return {
                "roe": _safe_float(row.get("净资产收益率")),
                "grossMargin": _safe_float(row.get("销售毛利率")),
                "revenueGrowth": _safe_float(row.get("营业收入同比增长率")),
                "netProfitGrowth": _safe_float(row.get("归母净利润同比增长率")),
            }
    except Exception:
        pass
    return {}


def _build_ranking(stocks: list[dict]) -> dict:
    """Build ranking for key metrics."""
    rankings: dict[str, list] = {}

    metrics = [
        ("returnPct", True, "收益率"),
        ("sharpe", True, "夏普比率"),
        ("maxDrawdown", False, "最大回撤"),
        ("volatility", False, "波动率"),
        ("pe", False, "市盈率"),
        ("pb", False, "市净率"),
    ]

    for metric, higher_is_better, label in metrics:
        valid = [(s.get("code", "?"), s.get(metric)) for s in stocks if s.get(metric) is not None]
        if not valid:
            continue
        valid.sort(key=lambda x: x[1] if x[1] is not None else 0, reverse=higher_is_better)
        rankings[label] = [{"code": code, "value": val} for code, val in valid]

    return rankings


def _rank_by(stocks: list[dict], key: str) -> list[dict]:
    """Rank stocks by a specific metric."""
    valid = [s for s in stocks if s.get(key) is not None]
    valid.sort(key=lambda x: x.get(key, 0), reverse=True)
    return [{"code": s.get("code"), "name": s.get("name"), key: s.get(key)} for s in valid]


def _ema(data, span: int):
    """Calculate EMA."""
    import numpy as np
    alpha = 2.0 / (span + 1)
    result = np.zeros_like(data, dtype=float)
    result[0] = float(data[0])
    for i in range(1, len(data)):
        result[i] = alpha * float(data[i]) + (1 - alpha) * result[i - 1]
    return result


def _safe_float(val) -> float | None:
    """Safely convert to float."""
    if val is None or val == "" or val == "-":
        return None
    try:
        return round(float(val), 4)
    except (ValueError, TypeError):
        return None


# ── NDJSON bridge entry point ──────────────────────────────────
if __name__ == "__main__":
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            config = json.loads(line)
            result = run_stock_compare(config)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False), flush=True)
