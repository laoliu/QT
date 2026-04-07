"""
multi_compare — Horizontally compare multiple stocks across dimensions.

Called from TypeScript stock_compare tool via python-bridge.
Input:
  {
    "codes": ["600519", "000858", "002304"],
    "market": "cn_stock"
  }

Output:
  {
    "stocks": [...],
    "fundamentals": [...],
    "technicals": [...],
    "capitalFlow": [...],
    "performance": [...],
    "summary": { ... }
  }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional


def run_multi_compare(config: dict) -> dict:
    """Horizontally compare 2-10 stocks across fundamental, technical, capital flow, and performance."""
    codes: list[str] = config.get("codes", [])
    market: str = config.get("market", "cn_stock")

    if not codes or len(codes) < 2:
        return {"error": "请提供至少2只股票代码 (codes)"}
    if len(codes) > 10:
        return {"error": "最多支持10只股票横向对比"}

    errors: list[str] = []

    # Fetch all data in parallel using threads
    fundamentals: dict[str, dict] = {}
    technicals: dict[str, dict] = {}
    capital_flows: dict[str, dict] = {}
    performances: dict[str, dict] = {}

    with ThreadPoolExecutor(max_workers=min(len(codes), 6)) as pool:
        # Submit fundamental fetch tasks
        fund_futures = {
            pool.submit(_fetch_fundamentals, code, market, errors): code
            for code in codes
        }
        # Submit technical fetch tasks
        tech_futures = {
            pool.submit(_fetch_technicals, code, market, errors): code
            for code in codes
        }
        # Submit capital flow tasks
        flow_futures = {
            pool.submit(_fetch_capital_flow, code, market, errors): code
            for code in codes
        }
        # Submit performance tasks
        perf_futures = {
            pool.submit(_fetch_performance, code, market, errors): code
            for code in codes
        }

        for future in as_completed(fund_futures):
            code = fund_futures[future]
            try:
                result = future.result()
                if result:
                    fundamentals[code] = result
            except Exception as e:
                errors.append(f"{code} 基本面获取异常: {e}")

        for future in as_completed(tech_futures):
            code = tech_futures[future]
            try:
                result = future.result()
                if result:
                    technicals[code] = result
            except Exception as e:
                errors.append(f"{code} 技术面获取异常: {e}")

        for future in as_completed(flow_futures):
            code = flow_futures[future]
            try:
                result = future.result()
                if result:
                    capital_flows[code] = result
            except Exception as e:
                errors.append(f"{code} 资金面获取异常: {e}")

        for future in as_completed(perf_futures):
            code = perf_futures[future]
            try:
                result = future.result()
                if result:
                    performances[code] = result
            except Exception as e:
                errors.append(f"{code} 涨跌幅获取异常: {e}")

    # Build structured comparison table
    stocks: list[dict] = []
    for code in codes:
        fund = fundamentals.get(code, {})
        tech = technicals.get(code, {})
        flow = capital_flows.get(code, {})
        perf = performances.get(code, {})
        stocks.append({
            "code": code,
            "name": fund.get("name", ""),
            "industry": fund.get("industry", ""),
        })

    # Build comparison sections
    fund_table = []
    for code in codes:
        f = fundamentals.get(code, {})
        fund_table.append({
            "code": code,
            "name": f.get("name", ""),
            "pe": f.get("pe"),
            "pb": f.get("pb"),
            "roe": f.get("roe"),
            "marketCap": f.get("marketCap"),
            "revenueGrowth": f.get("revenueGrowth"),
        })

    tech_table = []
    for code in codes:
        t = technicals.get(code, {})
        tech_table.append({
            "code": code,
            "trend": t.get("trend", "未知"),
            "rsi": t.get("rsi"),
            "macdSignal": t.get("macdSignal", "未知"),
            "ma5": t.get("ma5"),
            "ma20": t.get("ma20"),
            "bollingerPosition": t.get("bollingerPosition"),
        })

    flow_table = []
    for code in codes:
        fl = capital_flows.get(code, {})
        flow_table.append({
            "code": code,
            "mainNetInflow": fl.get("mainNetInflow"),
            "mainNetInflowPct": fl.get("mainNetInflowPct"),
            "northHoldingChange": fl.get("northHoldingChange"),
            "northHoldingChangePct": fl.get("northHoldingChangePct"),
        })

    perf_table = []
    for code in codes:
        p = performances.get(code, {})
        perf_table.append({
            "code": code,
            "change1d": p.get("change1d"),
            "change5d": p.get("change5d"),
            "change20d": p.get("change20d"),
            "change60d": p.get("change60d"),
            "ytd": p.get("ytd"),
        })

    # Compute summary rankings
    summary = _compute_rankings(codes, fundamentals, technicals, capital_flows, performances)

    return {
        "stockCount": len(codes),
        "stocks": stocks,
        "fundamentals": fund_table,
        "technicals": tech_table,
        "capitalFlow": flow_table,
        "performance": perf_table,
        "summary": summary,
        "errors": errors if errors else None,
    }


def _fetch_fundamentals(code: str, market: str, errors: list[str]) -> Optional[dict]:
    """Fetch fundamental data for a single stock."""
    try:
        from data_providers.financial_provider import FinancialProvider

        provider = FinancialProvider()
        snapshot = provider.fetch(code, market)
        if snapshot is None:
            errors.append(f"{code}: 基本面数据为空")
            return None

        return {
            "name": snapshot.get("name", ""),
            "industry": snapshot.get("industry", ""),
            "pe": _safe_float(snapshot.get("pe")),
            "pb": _safe_float(snapshot.get("pb")),
            "roe": _safe_float(snapshot.get("roe")),
            "marketCap": _safe_float(snapshot.get("total_market_val")),
            "revenueGrowth": _safe_float(snapshot.get("revenue_growth_yoy")),
        }
    except Exception as e:
        errors.append(f"{code} 基本面: {e}")
        # Fallback: try yfinance
        try:
            from data_providers.yfinance_provider import get_fundamentals

            data = get_fundamentals(code, market)
            if data and "error" not in data:
                return {
                    "name": data.get("name", ""),
                    "industry": data.get("industry", data.get("sector", "")),
                    "pe": _safe_float(data.get("pe")),
                    "pb": _safe_float(data.get("pb")),
                    "roe": _safe_float(
                        (data.get("profitability") or {}).get("roe")
                    ),
                    "marketCap": _safe_float(data.get("marketCap")),
                    "revenueGrowth": None,
                }
        except Exception:
            pass
        return None


def _fetch_technicals(code: str, market: str, errors: list[str]) -> Optional[dict]:
    """Fetch technical indicators for a single stock."""
    try:
        import akshare as ak
        import numpy as np

        # Fetch recent kline data
        if market == "cn_stock":
            symbol = code if len(code) == 6 else code
            df = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
        elif market == "us_stock":
            df = ak.stock_us_daily(symbol=code, adjust="qfq")
        else:
            errors.append(f"{code}: 不支持的市场 {market}")
            return None

        if df is None or df.empty or len(df) < 20:
            errors.append(f"{code}: K线数据不足")
            return None

        # Use last 120 bars
        df = df.tail(120).copy()
        closes = df["收盘"].values if "收盘" in df.columns else df["close"].values
        closes = closes.astype(float)

        # MA
        ma5 = float(np.mean(closes[-5:])) if len(closes) >= 5 else None
        ma20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else None
        current_price = float(closes[-1])

        # Trend direction
        if ma5 is not None and ma20 is not None:
            if ma5 > ma20 and current_price > ma5:
                trend = "上升"
            elif ma5 < ma20 and current_price < ma5:
                trend = "下降"
            else:
                trend = "震荡"
        else:
            trend = "未知"

        # RSI (14-day)
        rsi = _calc_rsi(closes, 14)

        # MACD
        macd_signal = _calc_macd_signal(closes)

        # Bollinger position
        bollinger_pos = _calc_bollinger_position(closes, 20)

        return {
            "trend": trend,
            "rsi": round(rsi, 2) if rsi is not None else None,
            "macdSignal": macd_signal,
            "ma5": round(ma5, 2) if ma5 is not None else None,
            "ma20": round(ma20, 2) if ma20 is not None else None,
            "bollingerPosition": bollinger_pos,
        }
    except ImportError:
        errors.append(f"{code}: akshare 未安装")
        return None
    except Exception as e:
        errors.append(f"{code} 技术面: {e}")
        return None


def _fetch_capital_flow(code: str, market: str, errors: list[str]) -> Optional[dict]:
    """Fetch capital flow data for a single stock."""
    result: dict[str, Any] = {}

    # Main net inflow
    try:
        import akshare as ak

        if market == "cn_stock":
            df = ak.stock_individual_fund_flow(stock=code, market="sh" if code.startswith("6") else "sz")
            if df is not None and not df.empty:
                latest = df.iloc[-1]
                main_net = _safe_float(latest.get("主力净流入-净额"))
                main_pct = _safe_float(latest.get("主力净流入-净占比"))
                result["mainNetInflow"] = round(main_net / 1e4, 2) if main_net is not None else None  # 万元
                result["mainNetInflowPct"] = round(main_pct, 2) if main_pct is not None else None
    except Exception as e:
        errors.append(f"{code} 资金流: {e}")

    # North-bound holding change
    try:
        import akshare as ak

        if market == "cn_stock":
            df = ak.stock_hsgt_individual_em(symbol=code)
            if df is not None and not df.empty:
                df = df.tail(5)
                if len(df) >= 2:
                    latest_hold = _safe_float(df.iloc[-1].get("持股数量"))
                    prev_hold = _safe_float(df.iloc[-2].get("持股数量"))
                    if latest_hold is not None and prev_hold is not None and prev_hold > 0:
                        change = latest_hold - prev_hold
                        change_pct = change / prev_hold * 100
                        result["northHoldingChange"] = round(change / 1e4, 2)  # 万股
                        result["northHoldingChangePct"] = round(change_pct, 2)
    except Exception as e:
        errors.append(f"{code} 北向: {e}")

    return result if result else None


def _fetch_performance(code: str, market: str, errors: list[str]) -> Optional[dict]:
    """Fetch recent price change percentages."""
    try:
        import akshare as ak

        if market == "cn_stock":
            df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
        elif market == "us_stock":
            df = ak.stock_us_daily(symbol=code, adjust="qfq")
        else:
            return None

        if df is None or df.empty:
            return None

        col = "收盘" if "收盘" in df.columns else "close"
        closes = df[col].values.astype(float)

        current = float(closes[-1])
        result: dict[str, Any] = {}

        if len(closes) >= 2:
            result["change1d"] = round((current / float(closes[-2]) - 1) * 100, 2)
        if len(closes) >= 6:
            result["change5d"] = round((current / float(closes[-6]) - 1) * 100, 2)
        if len(closes) >= 21:
            result["change20d"] = round((current / float(closes[-21]) - 1) * 100, 2)
        if len(closes) >= 61:
            result["change60d"] = round((current / float(closes[-61]) - 1) * 100, 2)

        # YTD
        date_col = "日期" if "日期" in df.columns else "date"
        if date_col in df.columns:
            import datetime

            now = datetime.date.today()
            year_start = datetime.date(now.year, 1, 1)
            df_copy = df.copy()
            df_copy[date_col] = df_copy[date_col].astype(str)
            ytd_rows = df_copy[df_copy[date_col] >= str(year_start)]
            if not ytd_rows.empty:
                first_close = float(ytd_rows.iloc[0][col])
                if first_close > 0:
                    result["ytd"] = round((current / first_close - 1) * 100, 2)

        return result if result else None
    except Exception as e:
        errors.append(f"{code} 涨跌幅: {e}")
        return None


def _calc_rsi(closes, period: int = 14) -> Optional[float]:
    """Calculate RSI."""
    import numpy as np

    if len(closes) < period + 1:
        return None
    deltas = np.diff(closes[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return float(100 - 100 / (1 + rs))


def _calc_macd_signal(closes) -> str:
    """Calculate MACD status: 金叉/死叉/多头/空头."""
    import numpy as np

    if len(closes) < 35:
        return "数据不足"

    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    dif = ema12 - ema26
    dea = _ema(dif, 9)

    if len(dif) < 2:
        return "未知"

    curr_dif = dif[-1]
    prev_dif = dif[-2]
    curr_dea = dea[-1]
    prev_dea = dea[-2]

    # Cross detection
    if prev_dif <= prev_dea and curr_dif > curr_dea:
        return "金叉"
    elif prev_dif >= prev_dea and curr_dif < curr_dea:
        return "死叉"
    elif curr_dif > curr_dea and curr_dif > 0:
        return "多头"
    elif curr_dif < curr_dea and curr_dif < 0:
        return "空头"
    else:
        return "中性"


def _ema(data, period: int):
    """Compute exponential moving average."""
    import numpy as np

    arr = np.array(data, dtype=float)
    result = np.empty_like(arr)
    result[0] = arr[0]
    k = 2.0 / (period + 1)
    for i in range(1, len(arr)):
        result[i] = arr[i] * k + result[i - 1] * (1 - k)
    return result


def _calc_bollinger_position(closes, period: int = 20) -> Optional[str]:
    """Determine where current price sits in Bollinger Bands."""
    import numpy as np

    if len(closes) < period:
        return None
    recent = closes[-period:]
    mid = np.mean(recent)
    std = np.std(recent)
    if std == 0:
        return "中轨"
    upper = mid + 2 * std
    lower = mid - 2 * std
    current = float(closes[-1])

    if current >= upper:
        return "上轨上方"
    elif current > mid + std:
        return "上轨附近"
    elif current > mid - std:
        return "中轨附近"
    elif current > lower:
        return "下轨附近"
    else:
        return "下轨下方"


def _compute_rankings(
    codes: list[str],
    fundamentals: dict[str, dict],
    technicals: dict[str, dict],
    capital_flows: dict[str, dict],
    performances: dict[str, dict],
) -> dict:
    """Compute summary rankings across all dimensions."""
    rankings: dict[str, Any] = {}

    # Best PE (lowest positive PE is better)
    pe_list = [
        (c, fundamentals.get(c, {}).get("pe"))
        for c in codes
        if fundamentals.get(c, {}).get("pe") is not None
        and fundamentals.get(c, {}).get("pe") > 0
    ]
    if pe_list:
        pe_list.sort(key=lambda x: x[1])
        rankings["lowestPE"] = {"code": pe_list[0][0], "value": pe_list[0][1]}

    # Best ROE (highest)
    roe_list = [
        (c, fundamentals.get(c, {}).get("roe"))
        for c in codes
        if fundamentals.get(c, {}).get("roe") is not None
    ]
    if roe_list:
        roe_list.sort(key=lambda x: x[1], reverse=True)
        rankings["highestROE"] = {"code": roe_list[0][0], "value": roe_list[0][1]}

    # Best revenue growth
    growth_list = [
        (c, fundamentals.get(c, {}).get("revenueGrowth"))
        for c in codes
        if fundamentals.get(c, {}).get("revenueGrowth") is not None
    ]
    if growth_list:
        growth_list.sort(key=lambda x: x[1], reverse=True)
        rankings["highestRevenueGrowth"] = {"code": growth_list[0][0], "value": growth_list[0][1]}

    # Strongest trend (RSI highest)
    rsi_list = [
        (c, technicals.get(c, {}).get("rsi"))
        for c in codes
        if technicals.get(c, {}).get("rsi") is not None
    ]
    if rsi_list:
        rsi_list.sort(key=lambda x: x[1], reverse=True)
        rankings["strongestMomentum"] = {"code": rsi_list[0][0], "rsi": rsi_list[0][1]}

    # Biggest main net inflow
    inflow_list = [
        (c, capital_flows.get(c, {}).get("mainNetInflow"))
        for c in codes
        if capital_flows.get(c, {}).get("mainNetInflow") is not None
    ]
    if inflow_list:
        inflow_list.sort(key=lambda x: x[1], reverse=True)
        rankings["largestMainInflow"] = {"code": inflow_list[0][0], "value": inflow_list[0][1]}

    # Best short-term performance (5d change)
    perf5_list = [
        (c, performances.get(c, {}).get("change5d"))
        for c in codes
        if performances.get(c, {}).get("change5d") is not None
    ]
    if perf5_list:
        perf5_list.sort(key=lambda x: x[1], reverse=True)
        rankings["best5dPerformance"] = {"code": perf5_list[0][0], "value": perf5_list[0][1]}

    # Best YTD
    ytd_list = [
        (c, performances.get(c, {}).get("ytd"))
        for c in codes
        if performances.get(c, {}).get("ytd") is not None
    ]
    if ytd_list:
        ytd_list.sort(key=lambda x: x[1], reverse=True)
        rankings["bestYTD"] = {"code": ytd_list[0][0], "value": ytd_list[0][1]}

    return rankings


def _safe_float(val: Any) -> Optional[float]:
    """Safely convert to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if f == f else None  # NaN check
    except (ValueError, TypeError):
        return None


def main():
    config_str = os.environ.get("QUANT_CONFIG", "")
    if not config_str:
        config_str = sys.stdin.read()
    try:
        config = json.loads(config_str) if config_str else {}
        result = run_multi_compare(config)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        print(json.dumps({
            "error": f"多股对比失败: {traceback.format_exc()[:500]}"
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
