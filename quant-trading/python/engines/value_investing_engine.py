"""
Value investing engine — Graham/Buffett-style fundamental analysis.

Input (JSON via stdin):
  {
    "action": "intrinsic_value" | "moat" | "quality_score" | "margin_of_safety" | "comprehensive",
    "symbol": "600519",
    "discountRate": 0.10,
    "growthRate": 0.05,
    "terminalGrowthRate": 0.03,
    "years": 10
  }

Output: JSON with analysis results.
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
    """Detect market from symbol pattern."""
    s = symbol.upper().strip()
    if s.endswith(".HK"):
        return "hk"
    if any(c.isalpha() for c in s) and not s.startswith(("0", "3", "6")):
        return "us"
    return "cn"


def _get_fundamentals(symbol: str) -> dict[str, Any]:
    """Fetch fundamental data using akshare (A-shares) or yfinance."""
    market = _detect_market(symbol)

    if market == "cn":
        return _get_cn_fundamentals(symbol)
    else:
        return _get_global_fundamentals(symbol)


def _get_cn_fundamentals(symbol: str) -> dict[str, Any]:
    """Fetch A-share fundamentals from akshare."""
    try:
        import akshare as ak
        import pandas as pd

        # Get financial summary
        result: dict[str, Any] = {"market": "cn", "symbol": symbol}

        # Get individual stock info for basic data
        try:
            df = ak.stock_individual_info_em(symbol=symbol)
            if df is not None and not df.empty:
                info_dict = dict(zip(df["item"], df["value"]))
                result["name"] = info_dict.get("股票简称", "")
                result["industry"] = info_dict.get("行业", "")
                result["totalShares"] = _parse_float(info_dict.get("总股本", 0))
                result["marketCap"] = _parse_float(info_dict.get("总市值", 0))
                result["pe"] = _parse_float(info_dict.get("市盈率(动态)", 0))
                result["pb"] = _parse_float(info_dict.get("市净率", 0))
        except Exception:
            pass

        # Get financial reports
        try:
            fin = ak.stock_financial_abstract_ths(symbol=symbol, indicator="按年度")
            if fin is not None and not fin.empty:
                latest = fin.iloc[0]
                result["eps"] = _parse_float(latest.get("基本每股收益(Ԫ)", 0))
                result["bvps"] = _parse_float(latest.get("每股净资产(Ԫ)", 0))
                result["roe"] = _parse_float(latest.get("净资产收益率(%)", 0))
                result["revenue"] = _parse_float(latest.get("营业总收入(Ԫ)", 0))
                result["netProfit"] = _parse_float(latest.get("净利润(Ԫ)", 0))
                result["operatingCashFlow"] = _parse_float(latest.get("经营活动产生的现金流量净额(Ԫ)", 0))

                # Multi-year data for growth/stability
                if len(fin) >= 3:
                    roe_list = [_parse_float(r.get("净资产收益率(%)", 0)) for _, r in fin.head(5).iterrows()]
                    eps_list = [_parse_float(r.get("基本每股收益(Ԫ)", 0)) for _, r in fin.head(5).iterrows()]
                    rev_list = [_parse_float(r.get("营业总收入(Ԫ)", 0)) for _, r in fin.head(5).iterrows()]
                    result["roeHistory"] = roe_list
                    result["epsHistory"] = eps_list
                    result["revenueHistory"] = rev_list
        except Exception:
            pass

        # Get cash flow data for FCF estimation
        try:
            cf = ak.stock_financial_cash_ths(symbol=symbol, indicator="按年度")
            if cf is not None and not cf.empty:
                latest_cf = cf.iloc[0]
                result["operatingCashFlow"] = _parse_float(latest_cf.get("经营活动产生的现金流量净额", 0))
                result["capex"] = abs(_parse_float(latest_cf.get("购建固定资产、无形资产和其他长期资产支付的现金", 0)))
                result["freeCashFlow"] = result.get("operatingCashFlow", 0) - result.get("capex", 0)
        except Exception:
            pass

        # Get current price
        try:
            q = ak.stock_zh_a_spot_em()
            if q is not None:
                row = q[q["代码"] == symbol]
                if not row.empty:
                    result["price"] = float(row.iloc[0]["最新价"])
        except Exception:
            pass

        return result

    except ImportError:
        return {"error": "akshare not installed", "market": "cn", "symbol": symbol}
    except Exception as e:
        return {"error": str(e), "market": "cn", "symbol": symbol}


def _get_global_fundamentals(symbol: str) -> dict[str, Any]:
    """Fetch non-CN fundamentals from yfinance."""
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        result: dict[str, Any] = {
            "market": _detect_market(symbol),
            "symbol": symbol,
            "name": info.get("shortName", ""),
            "industry": info.get("industry", ""),
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
            "roa": info.get("returnOnAssets", 0),
            "revenue": info.get("totalRevenue", 0),
            "netProfit": info.get("netIncomeToCommon", 0),
            "freeCashFlow": info.get("freeCashflow", 0),
            "operatingCashFlow": info.get("operatingCashflow", 0),
            "totalDebt": info.get("totalDebt", 0),
            "totalAssets": info.get("totalAssets", 0),
            "totalEquity": info.get("totalStockholderEquity", 0),
            "dividendYield": info.get("dividendYield", 0),
            "dividendPerShare": info.get("dividendRate", 0),
            "grossMargin": info.get("grossMargins", 0),
            "operatingMargin": info.get("operatingMargins", 0),
            "profitMargin": info.get("profitMargins", 0),
            "revenueGrowth": info.get("revenueGrowth", 0),
            "earningsGrowth": info.get("earningsGrowth", 0),
            "beta": info.get("beta", 1.0),
        }
        return result

    except ImportError:
        return {"error": "yfinance not installed", "market": "us", "symbol": symbol}
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def _parse_float(val: Any) -> float:
    if val is None or val == "" or val == "--":
        return 0.0
    try:
        return float(val)
    except (ValueError, TypeError):
        return 0.0


# ── Analysis Functions ─────────────────────────────────────────────

def _intrinsic_value(data: dict, config: dict) -> dict:
    """Calculate intrinsic value using multiple methods."""
    discount_rate = config.get("discountRate", 0.10)
    growth_rate = config.get("growthRate", 0.05)
    terminal_growth = config.get("terminalGrowthRate", 0.03)
    years = config.get("years", 10)

    results: dict[str, Any] = {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "currentPrice": data.get("price", 0),
    }

    # 1. DCF (Discounted Cash Flow)
    fcf = data.get("freeCashFlow", 0)
    if fcf and fcf > 0:
        dcf_value = 0.0
        projected_fcf = fcf
        for y in range(1, years + 1):
            projected_fcf *= (1 + growth_rate)
            dcf_value += projected_fcf / ((1 + discount_rate) ** y)

        # Terminal value
        terminal_fcf = projected_fcf * (1 + terminal_growth)
        terminal_value = terminal_fcf / (discount_rate - terminal_growth)
        dcf_value += terminal_value / ((1 + discount_rate) ** years)

        shares = data.get("totalShares", 0)
        market_cap = data.get("marketCap", 0)
        if shares and shares > 0:
            dcf_per_share = dcf_value / shares
        elif market_cap and data.get("price", 0) > 0:
            shares_est = market_cap / data["price"]
            dcf_per_share = dcf_value / shares_est if shares_est > 0 else 0
        else:
            dcf_per_share = 0

        results["dcf"] = {
            "totalValue": round(dcf_value, 2),
            "perShare": round(dcf_per_share, 2),
            "currentFCF": round(fcf, 2),
            "assumptions": {
                "growthRate": growth_rate,
                "discountRate": discount_rate,
                "terminalGrowth": terminal_growth,
                "years": years,
            },
        }
    else:
        results["dcf"] = {"error": "无法获取自由现金流数据"}

    # 2. Graham Number = sqrt(22.5 × EPS × BVPS)
    eps = data.get("eps", 0)
    bvps = data.get("bvps", 0)
    if eps and eps > 0 and bvps and bvps > 0:
        graham = math.sqrt(22.5 * eps * bvps)
        results["grahamNumber"] = {
            "value": round(graham, 2),
            "eps": eps,
            "bvps": bvps,
        }
    else:
        results["grahamNumber"] = {"error": "EPS或每股净资产数据不足"}

    # 3. EPV (Earnings Power Value) = adjusted_earnings / cost_of_capital
    net_profit = data.get("netProfit", 0)
    if net_profit and net_profit > 0:
        epv_total = net_profit / discount_rate
        market_cap = data.get("marketCap", 0)
        epv_ratio = epv_total / market_cap if market_cap > 0 else 0
        results["epv"] = {
            "totalValue": round(epv_total, 2),
            "epvToMarketCap": round(epv_ratio, 2),
            "interpretation": "低估" if epv_ratio > 1 else "合理/高估",
        }
    else:
        results["epv"] = {"error": "净利润数据不足"}

    return results


def _moat_assessment(data: dict, _config: dict) -> dict:
    """Assess competitive moat (护城河)."""
    results: dict[str, Any] = {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "moatScore": 0,
        "moatFactors": [],
    }

    score = 0
    factors = []

    # ROE stability (if history available)
    roe_history = data.get("roeHistory", [])
    roe = data.get("roe", 0)
    if roe_history and len(roe_history) >= 3:
        avg_roe = sum(roe_history) / len(roe_history)
        roe_std = (sum((r - avg_roe) ** 2 for r in roe_history) / len(roe_history)) ** 0.5
        if avg_roe > 15:
            score += 2
            factors.append({"factor": "高ROE", "detail": f"平均ROE {avg_roe:.1f}%", "score": 2})
        elif avg_roe > 10:
            score += 1
            factors.append({"factor": "良好ROE", "detail": f"平均ROE {avg_roe:.1f}%", "score": 1})
        if roe_std < 3:
            score += 1
            factors.append({"factor": "ROE稳定", "detail": f"标准差 {roe_std:.1f}", "score": 1})
    elif roe and roe > 15:
        score += 2
        factors.append({"factor": "高ROE", "detail": f"当前ROE {roe:.1f}%", "score": 2})

    # Gross margin
    gross_margin = data.get("grossMargin", 0)
    if gross_margin and gross_margin > 0.4:
        score += 2
        factors.append({"factor": "高毛利率", "detail": f"{gross_margin*100:.1f}%", "score": 2})
    elif gross_margin and gross_margin > 0.25:
        score += 1
        factors.append({"factor": "良好毛利率", "detail": f"{gross_margin*100:.1f}%", "score": 1})

    # Revenue growth
    rev_growth = data.get("revenueGrowth", 0)
    rev_history = data.get("revenueHistory", [])
    if rev_history and len(rev_history) >= 3:
        # Check consistent growth
        growing = all(rev_history[i] >= rev_history[i+1] * 0.95 for i in range(len(rev_history) - 1) if rev_history[i+1] > 0)
        if growing:
            score += 1
            factors.append({"factor": "持续增长", "detail": "近几年营收稳步增长", "score": 1})
    elif rev_growth and rev_growth > 0.1:
        score += 1
        factors.append({"factor": "营收增长", "detail": f"{rev_growth*100:.1f}%", "score": 1})

    # Debt safety
    total_debt = data.get("totalDebt", 0)
    total_equity = data.get("totalEquity", 0)
    if total_equity and total_equity > 0 and total_debt is not None:
        de_ratio = total_debt / total_equity
        if de_ratio < 0.5:
            score += 1
            factors.append({"factor": "低负债", "detail": f"D/E={de_ratio:.2f}", "score": 1})

    # Operating margin
    op_margin = data.get("operatingMargin", 0)
    if op_margin and op_margin > 0.2:
        score += 1
        factors.append({"factor": "高营业利润率", "detail": f"{op_margin*100:.1f}%", "score": 1})

    results["moatScore"] = score
    results["moatFactors"] = factors
    results["moatGrade"] = (
        "宽护城河" if score >= 6 else
        "窄护城河" if score >= 3 else
        "无明显护城河"
    )

    return results


def _quality_score(data: dict, _config: dict) -> dict:
    """Piotroski F-Score and basic quality metrics."""
    results: dict[str, Any] = {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
    }

    f_score = 0
    f_details = []

    # 1. Positive net income
    net_profit = data.get("netProfit", 0)
    if net_profit and net_profit > 0:
        f_score += 1
        f_details.append({"criterion": "正净利润", "pass": True})
    else:
        f_details.append({"criterion": "正净利润", "pass": False})

    # 2. Positive operating cash flow
    ocf = data.get("operatingCashFlow", 0)
    if ocf and ocf > 0:
        f_score += 1
        f_details.append({"criterion": "正经营现金流", "pass": True})
    else:
        f_details.append({"criterion": "正经营现金流", "pass": False})

    # 3. Cash flow > net income (earnings quality)
    if ocf and net_profit and ocf > net_profit:
        f_score += 1
        f_details.append({"criterion": "现金流>净利润", "pass": True})
    else:
        f_details.append({"criterion": "现金流>净利润", "pass": False})

    # 4. Positive ROE
    roe = data.get("roe", 0)
    if roe and roe > 0:
        f_score += 1
        f_details.append({"criterion": "正ROE", "pass": True})
    else:
        f_details.append({"criterion": "正ROE", "pass": False})

    # 5. Low leverage
    total_debt = data.get("totalDebt", 0)
    total_assets = data.get("totalAssets", 0)
    if total_assets and total_assets > 0:
        leverage = (total_debt or 0) / total_assets
        if leverage < 0.5:
            f_score += 1
            f_details.append({"criterion": "低杠杆(<50%)", "pass": True, "value": f"{leverage:.2%}"})
        else:
            f_details.append({"criterion": "低杠杆(<50%)", "pass": False, "value": f"{leverage:.2%}"})

    results["piotroskiF"] = {
        "score": f_score,
        "maxScore": 5,
        "details": f_details,
        "grade": "优秀" if f_score >= 4 else "良好" if f_score >= 3 else "一般",
    }

    return results


def _margin_of_safety(data: dict, config: dict) -> dict:
    """Calculate margin of safety vs intrinsic value."""
    iv = _intrinsic_value(data, config)
    price = data.get("price", 0)

    results: dict[str, Any] = {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "currentPrice": price,
        "margins": [],
    }

    # DCF margin
    dcf = iv.get("dcf", {})
    dcf_ps = dcf.get("perShare", 0)
    if dcf_ps and price:
        mos = (dcf_ps - price) / dcf_ps
        results["margins"].append({
            "method": "DCF",
            "intrinsicValue": dcf_ps,
            "marginOfSafety": round(mos * 100, 1),
            "verdict": "有安全边际" if mos > 0.25 else "安全边际不足" if mos > 0 else "高于内在价值",
        })

    # Graham Number margin
    gn = iv.get("grahamNumber", {})
    gn_val = gn.get("value", 0)
    if gn_val and price:
        mos = (gn_val - price) / gn_val
        results["margins"].append({
            "method": "格雷厄姆数",
            "intrinsicValue": gn_val,
            "marginOfSafety": round(mos * 100, 1),
            "verdict": "有安全边际" if mos > 0.25 else "安全边际不足" if mos > 0 else "高于内在价值",
        })

    return results


def _comprehensive(data: dict, config: dict) -> dict:
    """Full value investing report."""
    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "currentPrice": data.get("price", 0),
        "intrinsicValue": _intrinsic_value(data, config),
        "moat": _moat_assessment(data, config),
        "quality": _quality_score(data, config),
        "marginOfSafety": _margin_of_safety(data, config),
        "fundamentals": {
            "pe": data.get("pe"),
            "pb": data.get("pb"),
            "roe": data.get("roe"),
            "eps": data.get("eps"),
            "grossMargin": data.get("grossMargin"),
            "operatingMargin": data.get("operatingMargin"),
            "debtToEquity": (
                data.get("totalDebt", 0) / data.get("totalEquity", 1)
                if data.get("totalEquity", 0) > 0 else None
            ),
        },
    }


# ── Main ─────────────────────────────────────────────────────────

ACTIONS = {
    "intrinsic_value": _intrinsic_value,
    "moat": _moat_assessment,
    "quality_score": _quality_score,
    "margin_of_safety": _margin_of_safety,
    "comprehensive": _comprehensive,
}


def main():
    config = json.loads(sys.stdin.read())
    action = config.get("action", "comprehensive")
    symbol = config.get("symbol", "")

    if not symbol:
        print(json.dumps({"error": "symbol is required"}, ensure_ascii=False))
        return

    handler = ACTIONS.get(action)
    if not handler:
        print(json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False))
        return

    try:
        data = _get_fundamentals(symbol)
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
