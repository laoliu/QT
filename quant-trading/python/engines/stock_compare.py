"""
stock_compare — Multi-stock comparison tool.

Actions:
  price_compare     — Normalized price performance comparison
  technical_compare — Technical indicators side-by-side
  fundamental_compare — Key fundamental metrics comparison
  comprehensive     — Full comparison (price + technical + fundamental)

Data sourced from akshare public APIs.
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any


def _safe_float(val: Any, default: float = 0.0) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _get_klines(symbol: str, days: int = 60):
    """Fetch historical klines for a stock."""
    import akshare as ak

    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

    df = None
    try:
        df = ak.stock_zh_a_hist(symbol=symbol, period="daily", start_date=start, end_date=end, adjust="qfq")
    except Exception:
        pass

    return df


def _get_quote(symbol: str) -> dict[str, Any]:
    """Get real-time quote for a stock."""
    import akshare as ak

    try:
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            return {}

        code_col = None
        for col in df.columns:
            if "代码" in col:
                code_col = col
                break

        if not code_col:
            return {}

        match = df[df[code_col].astype(str) == symbol]
        if match.empty:
            return {}

        row = match.iloc[0]
        result: dict[str, Any] = {}
        for col in df.columns:
            val = row[col]
            if hasattr(val, "isoformat"):
                result[col] = str(val)[:10]
            elif isinstance(val, float) and val != val:
                result[col] = None
            else:
                result[col] = val
        return result

    except Exception:
        return {}


def price_compare(symbols: list[str], days: int = 60) -> dict[str, Any]:
    """Normalized price performance comparison."""
    try:
        results: dict[str, Any] = {}

        for symbol in symbols:
            df = _get_klines(symbol, days)
            if df is None or df.empty:
                results[symbol] = {"error": f"无法获取 {symbol} K线数据"}
                continue

            df = df.tail(days)
            closes = df["收盘"].apply(lambda x: _safe_float(x)).tolist()

            if not closes or closes[0] == 0:
                results[symbol] = {"error": "数据异常"}
                continue

            base = closes[0]
            normalized = [round((c / base - 1) * 100, 2) for c in closes]

            results[symbol] = {
                "start_price": round(base, 2),
                "end_price": round(closes[-1], 2),
                "total_return_pct": round((closes[-1] / base - 1) * 100, 2),
                "max_price": round(max(closes), 2),
                "min_price": round(min(closes), 2),
                "max_drawdown_pct": _calc_max_drawdown(closes),
                "volatility_pct": _calc_volatility(closes),
                "data_points": len(closes),
            }

        return {
            "period_days": days,
            "stocks": results,
            "comparison_note": "total_return_pct 为区间涨跌幅（%），可直接对比",
        }

    except Exception as e:
        return {"error": f"价格对比失败: {str(e)}"}


def _calc_max_drawdown(closes: list[float]) -> float:
    """Calculate max drawdown percentage."""
    if not closes:
        return 0.0
    peak = closes[0]
    max_dd = 0.0
    for c in closes:
        if c > peak:
            peak = c
        dd = (peak - c) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _calc_volatility(closes: list[float]) -> float:
    """Calculate annualized volatility."""
    if len(closes) < 2:
        return 0.0
    returns = [(closes[i] / closes[i-1] - 1) for i in range(1, len(closes)) if closes[i-1] > 0]
    if not returns:
        return 0.0
    import math
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / len(returns)
    daily_vol = math.sqrt(var)
    return round(daily_vol * math.sqrt(252) * 100, 2)


def technical_compare(symbols: list[str]) -> dict[str, Any]:
    """Technical indicators side-by-side comparison."""
    try:
        results: dict[str, Any] = {}

        for symbol in symbols:
            df = _get_klines(symbol, 120)
            if df is None or df.empty:
                results[symbol] = {"error": f"无法获取 {symbol} K线数据"}
                continue

            closes = df["收盘"].apply(lambda x: _safe_float(x)).tolist()
            volumes = df["成交量"].apply(lambda x: _safe_float(x)).tolist() if "成交量" in df.columns else []

            if len(closes) < 20:
                results[symbol] = {"error": "数据不足"}
                continue

            current = closes[-1]

            # Calculate MAs
            ma5 = sum(closes[-5:]) / 5 if len(closes) >= 5 else None
            ma10 = sum(closes[-10:]) / 10 if len(closes) >= 10 else None
            ma20 = sum(closes[-20:]) / 20 if len(closes) >= 20 else None
            ma60 = sum(closes[-60:]) / 60 if len(closes) >= 60 else None

            # RSI (14)
            rsi = _calc_rsi(closes, 14)

            # MACD
            macd_val, signal_val, hist_val = _calc_macd(closes)

            # Volume analysis
            avg_vol_5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else None
            avg_vol_20 = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else None
            vol_ratio = round(avg_vol_5 / avg_vol_20, 2) if avg_vol_5 and avg_vol_20 and avg_vol_20 > 0 else None

            # Trend
            trend = "多头" if ma5 and ma20 and ma5 > ma20 else "空头" if ma5 and ma20 else "未知"

            results[symbol] = {
                "price": round(current, 2),
                "ma5": round(ma5, 2) if ma5 else None,
                "ma10": round(ma10, 2) if ma10 else None,
                "ma20": round(ma20, 2) if ma20 else None,
                "ma60": round(ma60, 2) if ma60 else None,
                "rsi_14": rsi,
                "macd": macd_val,
                "macd_signal": signal_val,
                "macd_hist": hist_val,
                "volume_ratio_5_20": vol_ratio,
                "trend": trend,
                "above_ma20": current > ma20 if ma20 else None,
                "above_ma60": current > ma60 if ma60 else None,
            }

        return {"stocks": results}

    except Exception as e:
        return {"error": f"技术指标对比失败: {str(e)}"}


def _calc_rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(-period, 0):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(-diff)
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def _calc_macd(closes: list[float]) -> tuple[float | None, float | None, float | None]:
    if len(closes) < 26:
        return None, None, None
    # EMA helper
    def ema(data, period):
        k = 2 / (period + 1)
        result = [data[0]]
        for i in range(1, len(data)):
            result.append(data[i] * k + result[-1] * (1 - k))
        return result

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    dif = [ema12[i] - ema26[i] for i in range(len(closes))]
    dea = ema(dif, 9)
    macd_hist = [(dif[i] - dea[i]) * 2 for i in range(len(dif))]
    return round(dif[-1], 3), round(dea[-1], 3), round(macd_hist[-1], 3)


def fundamental_compare(symbols: list[str]) -> dict[str, Any]:
    """Key fundamental metrics comparison."""
    try:
        results: dict[str, Any] = {}

        for symbol in symbols:
            quote = _get_quote(symbol)
            if not quote:
                results[symbol] = {"error": f"无法获取 {symbol} 数据"}
                continue

            info: dict[str, Any] = {"name": quote.get("名称", "")}
            for key in ["最新价", "涨跌幅", "成交额", "流通市值", "总市值",
                        "市盈率-动态", "市净率", "60日涨跌幅", "年初至今涨跌幅",
                        "换手率", "量比"]:
                if key in quote:
                    info[key] = quote[key]

            results[symbol] = info

        return {"stocks": results}

    except Exception as e:
        return {"error": f"基本面对比失败: {str(e)}"}


def comprehensive(symbols: list[str], days: int = 60) -> dict[str, Any]:
    """Full comparison: price performance + technical + fundamental."""
    try:
        price = price_compare(symbols, days)
        tech = technical_compare(symbols)
        fund = fundamental_compare(symbols)

        # Merge results
        combined: dict[str, Any] = {}
        for sym in symbols:
            combined[sym] = {
                "performance": price.get("stocks", {}).get(sym, {}),
                "technical": tech.get("stocks", {}).get(sym, {}),
                "fundamental": fund.get("stocks", {}).get(sym, {}),
            }

        return {
            "period_days": days,
            "stocks": combined,
        }

    except Exception as e:
        return {"error": f"综合对比失败: {str(e)}"}


def main():
    try:
        raw = sys.stdin.read()
        args = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        args = {}

    action = args.get("action", "comprehensive")
    symbols_raw = args.get("symbols", "")
    if isinstance(symbols_raw, list):
        symbols = symbols_raw
    else:
        symbols = [s.strip() for s in str(symbols_raw).split(",") if s.strip()]
    days = args.get("days", 60)

    if not symbols:
        print(json.dumps({"error": "请提供 symbols 参数（逗号分隔的股票代码列表）"}, ensure_ascii=False))
        return

    if action == "price_compare":
        result = price_compare(symbols, days)
    elif action == "technical_compare":
        result = technical_compare(symbols)
    elif action == "fundamental_compare":
        result = fundamental_compare(symbols)
    elif action == "comprehensive":
        result = comprehensive(symbols, days)
    else:
        result = {"error": f"未知action: {action}"}

    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
