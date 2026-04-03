"""
Financial calculator engine — multi-model stock valuation.

DCF, DDM, PE/PB/PS relative valuation, EV/EBITDA, PEG, WACC.

Input (JSON via stdin):
  { "action": "dcf"|"ddm"|"pe_valuation"|..., "symbol": "AAPL", ... }

Output: JSON with valuation results.
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback
from typing import Any

# ── Proxy bypass ──────────────────────────────────────────────────────────────
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def _detect_market(symbol: str) -> str:
    s = symbol.upper().strip()
    if s.endswith(".HK"):
        return "hk"
    if any(c.isalpha() for c in s) and not s.startswith(("0", "3", "6")):
        return "us"
    return "cn"


def _parse_float(val: Any) -> float:
    if val is None or val == "" or val == "--":
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


def _get_stock_data(symbol: str) -> dict[str, Any]:
    """Fetch stock data for valuation."""
    market = _detect_market(symbol)

    if market == "cn":
        return _get_cn_data(symbol)
    else:
        return _get_global_data(symbol)


def _get_cn_data(symbol: str) -> dict[str, Any]:
    """Fetch A-share data from akshare."""
    try:
        import akshare as ak
        result: dict[str, Any] = {"market": "cn", "symbol": symbol}

        try:
            df = ak.stock_individual_info_em(symbol=symbol)
            if df is not None and not df.empty:
                info = dict(zip(df["item"], df["value"]))
                result["name"] = info.get("股票简称", "")
                result["pe"] = _parse_float(info.get("市盈率(动态)", 0))
                result["pb"] = _parse_float(info.get("市净率", 0))
                result["marketCap"] = _parse_float(info.get("总市值", 0))
                result["totalShares"] = _parse_float(info.get("总股本", 0))
        except Exception:
            pass

        try:
            q = ak.stock_zh_a_spot_em()
            if q is not None:
                row = q[q["代码"] == symbol]
                if not row.empty:
                    result["price"] = float(row.iloc[0]["最新价"])
        except Exception:
            pass

        try:
            fin = ak.stock_financial_abstract_ths(symbol=symbol, indicator="按年度")
            if fin is not None and not fin.empty:
                latest = fin.iloc[0]
                result["eps"] = _parse_float(latest.get("基本每股收益(元)", 0))
                result["bvps"] = _parse_float(latest.get("每股净资产(元)", 0))
                result["roe"] = _parse_float(latest.get("净资产收益率(%)", 0))
                result["revenue"] = _parse_float(latest.get("营业总收入(元)", 0))
                result["netProfit"] = _parse_float(latest.get("净利润(元)", 0))
        except Exception:
            pass

        try:
            cf = ak.stock_financial_cash_ths(symbol=symbol, indicator="按年度")
            if cf is not None and not cf.empty:
                latest_cf = cf.iloc[0]
                ocf = _parse_float(latest_cf.get("经营活动产生的现金流量净额", 0))
                capex = abs(_parse_float(latest_cf.get("购建固定资产、无形资产和其他长期资产支付的现金", 0)))
                result["operatingCashFlow"] = ocf
                result["capex"] = capex
                result["freeCashFlow"] = ocf - capex
        except Exception:
            pass

        return result

    except ImportError:
        return {"error": "akshare not installed", "symbol": symbol}
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def _get_global_data(symbol: str) -> dict[str, Any]:
    """Fetch global stock data from yfinance."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        return {
            "market": _detect_market(symbol),
            "symbol": symbol,
            "name": info.get("shortName", ""),
            "price": info.get("currentPrice", info.get("regularMarketPrice", 0)),
            "marketCap": info.get("marketCap", 0),
            "pe": info.get("trailingPE", 0),
            "forwardPe": info.get("forwardPE", 0),
            "pb": info.get("priceToBook", 0),
            "ps": info.get("priceToSalesTrailing12Months", 0),
            "eps": info.get("trailingEps", 0),
            "forwardEps": info.get("forwardEps", 0),
            "bvps": info.get("bookValue", 0),
            "roe": info.get("returnOnEquity", 0),
            "revenue": info.get("totalRevenue", 0),
            "netProfit": info.get("netIncomeToCommon", 0),
            "freeCashFlow": info.get("freeCashflow", 0),
            "operatingCashFlow": info.get("operatingCashflow", 0),
            "totalDebt": info.get("totalDebt", 0),
            "totalEquity": info.get("totalStockholderEquity", 0),
            "ebitda": info.get("ebitda", 0),
            "enterpriseValue": info.get("enterpriseValue", 0),
            "dividendYield": info.get("dividendYield", 0),
            "dividendPerShare": info.get("dividendRate", 0),
            "beta": info.get("beta", 1.0),
            "earningsGrowth": info.get("earningsGrowth", 0),
            "revenueGrowth": info.get("revenueGrowth", 0),
            "totalShares": info.get("sharesOutstanding", 0),
        }
    except ImportError:
        return {"error": "yfinance not installed", "symbol": symbol}
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


# ── Valuation Models ──────────────────────────────────────────────

def _dcf(data: dict, config: dict) -> dict:
    """DCF valuation."""
    fcf = config.get("freeCashFlow") or data.get("freeCashFlow", 0)
    growth = config.get("growthRate", 0.08)
    discount = config.get("discountRate", 0.10)
    terminal = config.get("terminalGrowthRate", 0.03)
    years = config.get("years", 10)

    if not fcf or fcf <= 0:
        return {"error": "无法获取自由现金流", "symbol": data.get("symbol")}

    pv_sum = 0.0
    projected = fcf
    projections = []
    for y in range(1, years + 1):
        projected *= (1 + growth)
        pv = projected / ((1 + discount) ** y)
        pv_sum += pv
        projections.append({"year": y, "fcf": round(projected, 2), "pv": round(pv, 2)})

    terminal_fcf = projected * (1 + terminal)
    tv = terminal_fcf / (discount - terminal)
    tv_pv = tv / ((1 + discount) ** years)
    total = pv_sum + tv_pv

    shares = data.get("totalShares", 0)
    price = data.get("price", 0)
    if not shares and data.get("marketCap") and price:
        shares = data["marketCap"] / price

    per_share = total / shares if shares else 0
    upside = (per_share / price - 1) * 100 if price else 0

    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "method": "DCF",
        "totalValue": round(total, 2),
        "perShare": round(per_share, 2),
        "currentPrice": price,
        "upside": round(upside, 1),
        "terminalValue": round(tv, 2),
        "terminalValuePV": round(tv_pv, 2),
        "assumptions": {
            "currentFCF": fcf,
            "growthRate": growth,
            "discountRate": discount,
            "terminalGrowth": terminal,
            "years": years,
        },
        "projections": projections[:5],  # first 5 years
    }


def _ddm(data: dict, config: dict) -> dict:
    """Dividend Discount Model (Gordon Growth)."""
    div = config.get("dividend") or data.get("dividendPerShare", 0)
    growth = config.get("dividendGrowthRate", 0.05)
    discount = config.get("discountRate", 0.10)

    if not div or div <= 0:
        return {"error": "无股利数据，DDM不适用", "symbol": data.get("symbol")}

    if discount <= growth:
        return {"error": "折现率必须大于增长率", "symbol": data.get("symbol")}

    # Gordon Growth Model: P = D1 / (r - g)
    d1 = div * (1 + growth)
    value = d1 / (discount - growth)
    price = data.get("price", 0)
    upside = (value / price - 1) * 100 if price else 0

    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "method": "DDM (Gordon Growth)",
        "intrinsicValue": round(value, 2),
        "currentPrice": price,
        "upside": round(upside, 1),
        "currentDividend": div,
        "expectedD1": round(d1, 2),
        "dividendGrowth": growth,
        "discountRate": discount,
    }


def _pe_valuation(data: dict, config: dict) -> dict:
    """PE-based relative valuation."""
    pe = data.get("pe", 0)
    eps = data.get("eps", 0)
    price = data.get("price", 0)

    result: dict[str, Any] = {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "method": "PE Valuation",
        "currentPE": pe,
        "eps": eps,
        "currentPrice": price,
    }

    # Standard PE bands
    pe_bands = {
        "低估 (10x)": 10,
        "合理 (15x)": 15,
        "略高 (20x)": 20,
        "偏贵 (25x)": 25,
        "昂贵 (30x)": 30,
    }

    if eps and eps > 0:
        result["valuationBands"] = {
            label: {"pe": mult, "impliedPrice": round(eps * mult, 2)}
            for label, mult in pe_bands.items()
        }
        result["pePercentile"] = _pe_grade(pe)

    # Peer comparison
    peers_str = config.get("peerSymbols")
    if peers_str:
        peers = [s.strip() for s in str(peers_str).split(",") if s.strip()]
        peer_data = []
        for p in peers[:5]:
            try:
                pd = _get_stock_data(p)
                peer_data.append({
                    "symbol": p,
                    "name": pd.get("name", ""),
                    "pe": pd.get("pe", 0),
                    "price": pd.get("price", 0),
                })
            except Exception:
                pass
        if peer_data:
            avg_peer_pe = sum(p["pe"] for p in peer_data if p["pe"] and p["pe"] > 0) / max(1, sum(1 for p in peer_data if p["pe"] and p["pe"] > 0))
            result["peerComparison"] = {
                "peers": peer_data,
                "avgPeerPE": round(avg_peer_pe, 1),
                "impliedPrice": round(eps * avg_peer_pe, 2) if eps else 0,
            }

    return result


def _pe_grade(pe: float) -> str:
    if pe <= 0:
        return "亏损"
    if pe < 10:
        return "深度低估"
    if pe < 15:
        return "低估"
    if pe < 20:
        return "合理"
    if pe < 30:
        return "偏高"
    return "高估"


def _pb_valuation(data: dict, _config: dict) -> dict:
    """PB-based valuation."""
    pb = data.get("pb", 0)
    bvps = data.get("bvps", 0)
    price = data.get("price", 0)
    roe = data.get("roe", 0)

    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "method": "PB Valuation",
        "currentPB": pb,
        "bvps": bvps,
        "currentPrice": price,
        "roe": roe,
        "pbBands": {
            "破净 (0.8x)": round(bvps * 0.8, 2) if bvps else 0,
            "低估 (1.0x)": round(bvps * 1.0, 2) if bvps else 0,
            "合理 (1.5x)": round(bvps * 1.5, 2) if bvps else 0,
            "偏高 (2.0x)": round(bvps * 2.0, 2) if bvps else 0,
            "昂贵 (3.0x)": round(bvps * 3.0, 2) if bvps else 0,
        },
        "justifiedPB": round(roe * 0.1, 2) if roe else None,
        "interpretation": (
            "深度低估（破净）" if pb and pb < 1 else
            "低估" if pb and pb < 1.5 else
            "合理" if pb and pb < 2.5 else
            "偏高" if pb else "无数据"
        ),
    }


def _ps_valuation(data: dict, _config: dict) -> dict:
    """PS-based valuation."""
    ps = data.get("ps", 0)
    revenue = data.get("revenue", 0)
    price = data.get("price", 0)
    market_cap = data.get("marketCap", 0)
    rev_growth = data.get("revenueGrowth", 0)

    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "method": "PS Valuation",
        "currentPS": ps,
        "revenue": revenue,
        "marketCap": market_cap,
        "currentPrice": price,
        "revenueGrowth": rev_growth,
        "interpretation": (
            "低估" if ps and ps < 2 else
            "合理" if ps and ps < 5 else
            "偏高" if ps and ps < 10 else
            "高估" if ps else "无数据"
        ),
    }


def _ev_ebitda(data: dict, _config: dict) -> dict:
    """EV/EBITDA valuation."""
    ev = data.get("enterpriseValue", 0)
    ebitda = data.get("ebitda", 0)

    ratio = ev / ebitda if ebitda and ebitda > 0 else 0

    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "method": "EV/EBITDA",
        "enterpriseValue": ev,
        "ebitda": ebitda,
        "evToEbitda": round(ratio, 2),
        "interpretation": (
            "低估" if ratio and ratio < 8 else
            "合理" if ratio and ratio < 12 else
            "偏高" if ratio and ratio < 18 else
            "高估" if ratio else "无数据"
        ),
    }


def _peg(data: dict, _config: dict) -> dict:
    """PEG ratio analysis."""
    pe = data.get("pe", 0)
    growth = data.get("earningsGrowth", 0) or data.get("revenueGrowth", 0)

    growth_pct = growth * 100 if growth and abs(growth) < 5 else growth

    peg = pe / growth_pct if growth_pct and growth_pct > 0 and pe and pe > 0 else 0

    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "method": "PEG",
        "pe": pe,
        "earningsGrowth": growth,
        "earningsGrowthPct": round(growth_pct, 1) if growth_pct else 0,
        "peg": round(peg, 2),
        "interpretation": (
            "深度低估" if peg and 0 < peg < 0.5 else
            "低估" if peg and 0 < peg < 1 else
            "合理" if peg and peg < 1.5 else
            "偏高" if peg and peg < 2 else
            "高估" if peg and peg > 0 else "不适用"
        ),
    }


def _wacc(data: dict, config: dict) -> dict:
    """Weighted Average Cost of Capital."""
    beta = config.get("beta") or data.get("beta", 1.0)
    rf = config.get("riskFreeRate", 0.03)
    rm = config.get("marketReturn", 0.09)
    tax = config.get("taxRate", 0.25)
    cost_debt = config.get("costOfDebt", 0.05)

    # Cost of equity (CAPM)
    cost_equity = rf + beta * (rm - rf)

    # Capital structure
    debt = data.get("totalDebt", 0)
    equity = data.get("marketCap", 0) or data.get("totalEquity", 0)
    total = (debt or 0) + (equity or 1)

    debt_ratio = config.get("debtRatio")
    if debt_ratio is None and total > 0:
        debt_ratio = (debt or 0) / total

    equity_weight = 1 - (debt_ratio or 0)
    debt_weight = debt_ratio or 0

    wacc_val = equity_weight * cost_equity + debt_weight * cost_debt * (1 - tax)

    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "method": "WACC",
        "wacc": round(wacc_val * 100, 2),
        "costOfEquity": round(cost_equity * 100, 2),
        "costOfDebt": round(cost_debt * 100, 2),
        "equityWeight": round(equity_weight * 100, 1),
        "debtWeight": round(debt_weight * 100, 1),
        "beta": beta,
        "riskFreeRate": rf,
        "marketReturn": rm,
        "taxRate": tax,
    }


def _compare(data: dict, config: dict) -> dict:
    """Multi-model comparison."""
    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "currentPrice": data.get("price", 0),
        "models": {
            "dcf": _dcf(data, config),
            "pe": _pe_valuation(data, config),
            "pb": _pb_valuation(data, config),
            "peg": _peg(data, config),
        },
        "summary": "多模型估值对比，以DCF为主参考",
    }


# ── Main ─────────────────────────────────────────────────────────

ACTIONS = {
    "dcf": _dcf,
    "ddm": _ddm,
    "pe_valuation": _pe_valuation,
    "pb_valuation": _pb_valuation,
    "ps_valuation": _ps_valuation,
    "ev_ebitda": _ev_ebitda,
    "peg": _peg,
    "wacc": _wacc,
    "compare": _compare,
}


def main():
    config = json.loads(sys.stdin.read())
    action = config.get("action", "compare")
    symbol = config.get("symbol", "")

    if not symbol:
        print(json.dumps({"error": "symbol is required"}, ensure_ascii=False))
        return

    handler = ACTIONS.get(action)
    if not handler:
        print(json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False))
        return

    try:
        data = _get_stock_data(symbol)
        if "error" in data and not any(k for k in data if k not in ("error", "market", "symbol")):
            print(json.dumps(data, ensure_ascii=False))
            return

        result = handler(data, config)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({
            "error": str(e),
            "traceback": traceback.format_exc(),
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
