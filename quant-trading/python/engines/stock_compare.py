"""
stock_compare — Head-to-head multi-dimensional stock comparison.

Compares 2-5 stocks across technicals, fundamentals, capital flow, and valuation.

Input:
  {
    "action": "compare",
    "symbols": "600519,000858,603288",
    "dimensions": ["technicals", "fundamentals", "capital_flow", "valuation"]
  }

Output:
  { "stocks": [...], "comparison_matrix": {...}, "summary": "..." }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from typing import Any

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def run_stock_compare(config: dict) -> dict:
    """Dispatch stock comparison."""
    action = config.get("action", "compare")
    symbols_raw = config.get("symbols", "")

    if isinstance(symbols_raw, list):
        symbols = symbols_raw
    else:
        symbols = [s.strip() for s in str(symbols_raw).split(",") if s.strip()]

    if len(symbols) < 2:
        return {"error": "至少需要2只股票进行对比，请提供逗号分隔的股票代码"}
    if len(symbols) > 5:
        return {"error": "最多支持5只股票同时对比"}

    dimensions = config.get("dimensions", ["technicals", "fundamentals", "capital_flow", "valuation"])
    if isinstance(dimensions, str):
        dimensions = [d.strip() for d in dimensions.split(",")]

    if action == "compare":
        return _compare_stocks(symbols, dimensions)
    else:
        return {"error": f"未知action: {action}"}


def _compare_stocks(symbols: list[str], dimensions: list[str]) -> dict:
    """Full multi-dimensional comparison."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    errors: list[str] = []
    stocks: list[dict] = []

    for symbol in symbols:
        stock_data: dict[str, Any] = {"symbol": symbol}

        # Basic info
        try:
            info_df = ak.stock_individual_info_em(symbol=symbol)
            if info_df is not None and not info_df.empty:
                info_dict = {}
                for _, row in info_df.iterrows():
                    key = str(row.iloc[0]) if len(row) > 0 else ""
                    val = row.iloc[1] if len(row) > 1 else ""
                    info_dict[key] = val
                stock_data["name"] = info_dict.get("股票简称", symbol)
                stock_data["industry"] = info_dict.get("行业", "")
                stock_data["total_market_cap"] = info_dict.get("总市值", "")
                stock_data["float_market_cap"] = info_dict.get("流通市值", "")
        except Exception as e:
            errors.append(f"{symbol} 基本信息获取失败: {str(e)[:80]}")
            stock_data["name"] = symbol

        # Fundamentals
        if "fundamentals" in dimensions:
            try:
                quote_df = ak.stock_zh_a_spot_em()
                if quote_df is not None and not quote_df.empty:
                    row = quote_df[quote_df["代码"] == symbol]
                    if not row.empty:
                        r = row.iloc[0]
                        stock_data["fundamentals"] = {
                            "pe_ttm": _safe_float(r.get("市盈率-动态")),
                            "pb": _safe_float(r.get("市净率")),
                            "total_mv": _safe_float(r.get("总市值")),
                            "float_mv": _safe_float(r.get("流通市值")),
                            "turnover_rate": _safe_float(r.get("换手率")),
                            "volume_ratio": _safe_float(r.get("量比")),
                            "price": _safe_float(r.get("最新价")),
                            "change_pct": _safe_float(r.get("涨跌幅")),
                            "amplitude": _safe_float(r.get("振幅")),
                            "high_52w": _safe_float(r.get("60日最高")),
                            "low_52w": _safe_float(r.get("60日最低")),
                        }
            except Exception as e:
                errors.append(f"{symbol} 基本面数据获取失败: {str(e)[:80]}")

        # Technicals — fetch recent klines and compute indicators
        if "technicals" in dimensions:
            try:
                kdf = ak.stock_zh_a_hist(symbol=symbol, period="daily", adjust="qfq")
                if kdf is not None and not kdf.empty:
                    kdf = kdf.tail(60)
                    closes = kdf["收盘"].tolist()
                    volumes = kdf["成交量"].tolist()

                    stock_data["technicals"] = {
                        "ma5": _ma(closes, 5),
                        "ma10": _ma(closes, 10),
                        "ma20": _ma(closes, 20),
                        "ma60": _ma(closes, 60) if len(closes) >= 60 else None,
                        "rsi_14": _rsi(closes, 14),
                        "price_vs_ma20": round((closes[-1] / _ma(closes, 20) - 1) * 100, 2) if _ma(closes, 20) else None,
                        "volume_avg_5": _ma(volumes, 5),
                        "volume_avg_20": _ma(volumes, 20),
                        "volume_ratio_5_20": round(_ma(volumes, 5) / _ma(volumes, 20), 2) if _ma(volumes, 20) else None,
                        "trend": _trend_direction(closes),
                        "latest_close": closes[-1] if closes else None,
                        "change_20d": round((closes[-1] / closes[-20] - 1) * 100, 2) if len(closes) >= 20 else None,
                    }
            except Exception as e:
                errors.append(f"{symbol} 技术面数据获取失败: {str(e)[:80]}")

        # Capital flow
        if "capital_flow" in dimensions:
            try:
                mkt = "sh" if symbol.startswith("6") else "sz"
                ff_df = ak.stock_individual_fund_flow(stock=symbol, market=mkt)
                if ff_df is not None and not ff_df.empty:
                    recent = ff_df.tail(5)
                    main_nets = []
                    for _, row in recent.iterrows():
                        net = _safe_float(row.get("主力净流入-净额"))
                        if net is not None:
                            main_nets.append(net)

                    stock_data["capital_flow"] = {
                        "main_net_today": main_nets[-1] if main_nets else None,
                        "main_net_5d_total": round(sum(main_nets), 2) if main_nets else None,
                        "main_net_trend": "流入" if sum(main_nets) > 0 else "流出" if main_nets else "未知",
                    }
            except Exception as e:
                errors.append(f"{symbol} 资金流向获取失败: {str(e)[:80]}")

        # Valuation
        if "valuation" in dimensions:
            try:
                # Use financial data for deeper valuation
                fin_df = ak.stock_financial_abstract_ths(symbol=symbol)
                if fin_df is not None and not fin_df.empty:
                    latest = fin_df.iloc[0]
                    stock_data["valuation"] = {
                        "roe": _safe_float(latest.get("净资产收益率")),
                        "gross_margin": _safe_float(latest.get("销售毛利率")),
                        "net_margin": _safe_float(latest.get("销售净利率")),
                        "revenue_growth": _safe_float(latest.get("营业总收入同比增长率")),
                        "profit_growth": _safe_float(latest.get("净利润同比增长率")),
                        "debt_ratio": _safe_float(latest.get("资产负债率")),
                    }
            except Exception as e:
                errors.append(f"{symbol} 估值数据获取失败: {str(e)[:80]}")

        stocks.append(stock_data)

    # Build comparison ranking
    rankings = _build_rankings(stocks, dimensions)

    result: dict[str, Any] = {
        "action": "compare",
        "stocks": stocks,
        "rankings": rankings,
        "count": len(stocks),
    }
    if errors:
        result["errors"] = errors

    return result


def _build_rankings(stocks: list[dict], dimensions: list[str]) -> dict:
    """Rank stocks across key metrics."""
    rankings: dict[str, list] = {}

    metrics = [
        ("pe_ttm", "fundamentals", True),    # lower is better
        ("pb", "fundamentals", True),         # lower is better
        ("roe", "valuation", False),          # higher is better
        ("profit_growth", "valuation", False), # higher is better
        ("rsi_14", "technicals", None),       # neutral — informational
        ("change_20d", "technicals", False),  # higher is better (momentum)
        ("main_net_5d_total", "capital_flow", False),  # higher is better
    ]

    for metric, dim, lower_is_better in metrics:
        vals = []
        for s in stocks:
            d = s.get(dim, {})
            v = d.get(metric) if isinstance(d, dict) else None
            vals.append({"symbol": s["symbol"], "name": s.get("name", ""), "value": v})

        ranked = sorted(
            [v for v in vals if v["value"] is not None],
            key=lambda x: x["value"],
            reverse=not lower_is_better if lower_is_better is not None else False,
        )
        for i, item in enumerate(ranked):
            item["rank"] = i + 1

        rankings[metric] = ranked

    return rankings


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return round(f, 4) if abs(f) < 1e15 else None
    except (ValueError, TypeError):
        return None


def _ma(data: list, period: int) -> float | None:
    if len(data) < period:
        return None
    return round(sum(data[-period:]) / period, 4)


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 2)


def _trend_direction(closes: list[float]) -> str:
    if len(closes) < 20:
        return "数据不足"
    ma5 = sum(closes[-5:]) / 5
    ma20 = sum(closes[-20:]) / 20
    if ma5 > ma20 * 1.02:
        return "上升趋势"
    elif ma5 < ma20 * 0.98:
        return "下降趋势"
    return "震荡"


# ── CLI entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    try:
        if "--json" in sys.argv:
            raw = sys.stdin.read()
            cfg = json.loads(raw) if raw.strip() else {}
        else:
            cfg = json.loads(os.environ.get("QUANT_CONFIG", "{}"))

        result = run_stock_compare(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
