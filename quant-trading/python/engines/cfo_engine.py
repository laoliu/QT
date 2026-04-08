"""
CFO engine — financial statement analysis (CFO-level).

Three-statement analysis, DuPont decomposition, financial ratios,
trend analysis, red flag detection, and peer comparison.

Input (JSON via stdin):
  {
    "action": "three_statements" | "dupont" | "ratios" | "peer_compare" | "trend" | "red_flags" | "comprehensive",
    "symbol": "600519",
    "peerSymbols": "000858,603369",
    "years": 3,
    "period": "annual" | "quarterly"
  }

Output: JSON with financial analysis results.
"""

from __future__ import annotations

import json
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


def _get_financial_data(symbol: str, years: int = 3, period: str = "annual") -> dict[str, Any]:
    """Fetch comprehensive financial data."""
    market = _detect_market(symbol)
    if market == "cn":
        return _get_cn_financials(symbol, years, period)
    else:
        return _get_global_financials(symbol, years)


def _get_cn_financials(symbol: str, years: int, period: str) -> dict[str, Any]:
    """Fetch A-share financial statements from akshare."""
    try:
        import akshare as ak
        result: dict[str, Any] = {"market": "cn", "symbol": symbol}

        indicator = "按年度" if period == "annual" else "按单季度"

        # Basic info
        try:
            info_df = ak.stock_individual_info_em(symbol=symbol)
            if info_df is not None and not info_df.empty:
                info = dict(zip(info_df["item"], info_df["value"]))
                result["name"] = info.get("股票简称", "")
                result["industry"] = info.get("行业", "")
                result["pe"] = _parse_float(info.get("市盈率(动态)", 0))
                result["pb"] = _parse_float(info.get("市净率", 0))
                result["marketCap"] = _parse_float(info.get("总市值", 0))
        except Exception:
            pass

        # Income statement data
        try:
            fin = ak.stock_financial_abstract_ths(symbol=symbol, indicator=indicator)
            if fin is not None and not fin.empty:
                income_data = []
                for _, row in fin.head(years).iterrows():
                    income_data.append({
                        "period": str(row.get("报告期", "")),
                        "revenue": _parse_float(row.get("营业总收入(Ԫ)", 0)),
                        "netProfit": _parse_float(row.get("净利润(Ԫ)", 0)),
                        "eps": _parse_float(row.get("基本每股收益(Ԫ)", 0)),
                        "roe": _parse_float(row.get("净资产收益率(%)", 0)),
                        "grossMargin": _parse_float(row.get("销售毛利率(%)", 0)),
                        "netMargin": _parse_float(row.get("销售净利率(%)", 0)),
                    })
                result["incomeStatements"] = income_data
        except Exception:
            pass

        # Balance sheet data
        try:
            bs = ak.stock_financial_balance_ths(symbol=symbol, indicator=indicator)
            if bs is not None and not bs.empty:
                balance_data = []
                for _, row in bs.head(years).iterrows():
                    balance_data.append({
                        "period": str(row.get("报告期", "")),
                        "totalAssets": _parse_float(row.get("资产总计(Ԫ)", 0)),
                        "totalLiabilities": _parse_float(row.get("负债合计(Ԫ)", 0)),
                        "totalEquity": _parse_float(row.get("股东权益合计(Ԫ)", 0)),
                        "bvps": _parse_float(row.get("每股净资产(Ԫ)", 0)),
                    })
                result["balanceSheets"] = balance_data
        except Exception:
            pass

        # Cash flow data
        try:
            cf = ak.stock_financial_cash_ths(symbol=symbol, indicator=indicator)
            if cf is not None and not cf.empty:
                cash_data = []
                for _, row in cf.head(years).iterrows():
                    ocf = _parse_float(row.get("经营活动产生的现金流量净额", 0))
                    icf = _parse_float(row.get("投资活动产生的现金流量净额", 0))
                    fcf_val = _parse_float(row.get("筹资活动产生的现金流量净额", 0))
                    cash_data.append({
                        "period": str(row.get("报告期", "")),
                        "operatingCF": ocf,
                        "investingCF": icf,
                        "financingCF": fcf_val,
                    })
                result["cashFlows"] = cash_data
        except Exception:
            pass

        # Current price
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
        return {"error": "akshare not installed", "symbol": symbol}
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


def _get_global_financials(symbol: str, years: int) -> dict[str, Any]:
    """Fetch global financial data from yfinance."""
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
            "pb": info.get("priceToBook", 0),
        }

        # Income statement
        try:
            inc = ticker.income_stmt
            if inc is not None and not inc.empty:
                income_data = []
                for col in inc.columns[:years]:
                    income_data.append({
                        "period": str(col.date()) if hasattr(col, "date") else str(col),
                        "revenue": float(inc.loc["Total Revenue", col]) if "Total Revenue" in inc.index else 0,
                        "netProfit": float(inc.loc["Net Income", col]) if "Net Income" in inc.index else 0,
                        "grossProfit": float(inc.loc["Gross Profit", col]) if "Gross Profit" in inc.index else 0,
                        "operatingIncome": float(inc.loc["Operating Income", col]) if "Operating Income" in inc.index else 0,
                        "ebitda": float(inc.loc["EBITDA", col]) if "EBITDA" in inc.index else 0,
                    })
                result["incomeStatements"] = income_data
        except Exception:
            pass

        # Balance sheet
        try:
            bs = ticker.balance_sheet
            if bs is not None and not bs.empty:
                balance_data = []
                for col in bs.columns[:years]:
                    balance_data.append({
                        "period": str(col.date()) if hasattr(col, "date") else str(col),
                        "totalAssets": float(bs.loc["Total Assets", col]) if "Total Assets" in bs.index else 0,
                        "totalLiabilities": float(bs.loc["Total Liabilities Net Minority Interest", col]) if "Total Liabilities Net Minority Interest" in bs.index else 0,
                        "totalEquity": float(bs.loc["Stockholders Equity", col]) if "Stockholders Equity" in bs.index else 0,
                        "totalDebt": float(bs.loc["Total Debt", col]) if "Total Debt" in bs.index else 0,
                        "cash": float(bs.loc["Cash And Cash Equivalents", col]) if "Cash And Cash Equivalents" in bs.index else 0,
                    })
                result["balanceSheets"] = balance_data
        except Exception:
            pass

        # Cash flow
        try:
            cf = ticker.cashflow
            if cf is not None and not cf.empty:
                cash_data = []
                for col in cf.columns[:years]:
                    cash_data.append({
                        "period": str(col.date()) if hasattr(col, "date") else str(col),
                        "operatingCF": float(cf.loc["Operating Cash Flow", col]) if "Operating Cash Flow" in cf.index else 0,
                        "investingCF": float(cf.loc["Investing Cash Flow", col]) if "Investing Cash Flow" in cf.index else 0,
                        "financingCF": float(cf.loc["Financing Cash Flow", col]) if "Financing Cash Flow" in cf.index else 0,
                        "freeCF": float(cf.loc["Free Cash Flow", col]) if "Free Cash Flow" in cf.index else 0,
                        "capex": float(cf.loc["Capital Expenditure", col]) if "Capital Expenditure" in cf.index else 0,
                    })
                result["cashFlows"] = cash_data
        except Exception:
            pass

        return result

    except ImportError:
        return {"error": "yfinance not installed", "symbol": symbol}
    except Exception as e:
        return {"error": str(e), "symbol": symbol}


# ── Analysis Actions ─────────────────────────────────────────────

def _three_statements(data: dict, _config: dict) -> dict:
    """Summarize three financial statements."""
    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "price": data.get("price", 0),
        "marketCap": data.get("marketCap", 0),
        "incomeStatement": data.get("incomeStatements", []),
        "balanceSheet": data.get("balanceSheets", []),
        "cashFlow": data.get("cashFlows", []),
    }


def _dupont(data: dict, _config: dict) -> dict:
    """DuPont decomposition: ROE = Net Margin × Asset Turnover × Equity Multiplier."""
    income = data.get("incomeStatements", [])
    balance = data.get("balanceSheets", [])

    decompositions = []

    for i, inc in enumerate(income):
        bs = balance[i] if i < len(balance) else {}

        revenue = inc.get("revenue", 0)
        net_profit = inc.get("netProfit", 0)
        total_assets = bs.get("totalAssets", 0)
        total_equity = bs.get("totalEquity", 0)

        net_margin = net_profit / revenue if revenue else 0
        asset_turnover = revenue / total_assets if total_assets else 0
        equity_multiplier = total_assets / total_equity if total_equity else 0
        roe = net_margin * asset_turnover * equity_multiplier

        decompositions.append({
            "period": inc.get("period", ""),
            "roe": round(roe * 100, 2),
            "netMargin": round(net_margin * 100, 2),
            "assetTurnover": round(asset_turnover, 3),
            "equityMultiplier": round(equity_multiplier, 2),
            "revenue": revenue,
            "netProfit": net_profit,
            "totalAssets": total_assets,
            "totalEquity": total_equity,
        })

    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "dupontDecomposition": decompositions,
        "formula": "ROE = 净利率 × 总资产周转率 × 权益乘数",
    }


def _ratios(data: dict, _config: dict) -> dict:
    """Financial ratio analysis."""
    income = data.get("incomeStatements", [{}])
    balance = data.get("balanceSheets", [{}])
    cashflow = data.get("cashFlows", [{}])

    inc = income[0] if income else {}
    bs = balance[0] if balance else {}
    cf = cashflow[0] if cashflow else {}

    revenue = inc.get("revenue", 0)
    net_profit = inc.get("netProfit", 0)
    total_assets = bs.get("totalAssets", 0)
    total_liabilities = bs.get("totalLiabilities", 0)
    total_equity = bs.get("totalEquity", 0)
    total_debt = bs.get("totalDebt", total_liabilities)
    cash = bs.get("cash", 0)
    ocf = cf.get("operatingCF", 0)

    result: dict[str, Any] = {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "period": inc.get("period", ""),
    }

    # Profitability
    result["profitability"] = {
        "grossMargin": round(inc.get("grossMargin", 0), 2) if inc.get("grossMargin") else (
            round((inc.get("grossProfit", 0) / revenue) * 100, 2) if revenue else 0
        ),
        "netMargin": round(inc.get("netMargin", 0), 2) if inc.get("netMargin") else (
            round((net_profit / revenue) * 100, 2) if revenue else 0
        ),
        "roe": round(inc.get("roe", 0), 2) if inc.get("roe") else (
            round((net_profit / total_equity) * 100, 2) if total_equity else 0
        ),
        "roa": round((net_profit / total_assets) * 100, 2) if total_assets else 0,
    }

    # Solvency
    result["solvency"] = {
        "debtToEquity": round(total_debt / total_equity, 2) if total_equity else 0,
        "debtToAssets": round(total_liabilities / total_assets, 2) if total_assets else 0,
        "interestCoverage": "N/A",
    }

    # Liquidity
    result["liquidity"] = {
        "cashToDebt": round(cash / total_debt, 2) if total_debt else "无债务",
        "cashToAssets": round(cash / total_assets * 100, 2) if total_assets else 0,
    }

    # Efficiency
    result["efficiency"] = {
        "assetTurnover": round(revenue / total_assets, 2) if total_assets else 0,
        "cashFlowToNetIncome": round(ocf / net_profit, 2) if net_profit else 0,
    }

    return result


def _peer_compare(data: dict, config: dict) -> dict:
    """Compare with peer companies."""
    peer_str = config.get("peerSymbols", "")
    years = config.get("years", 1)

    if not peer_str:
        return {"error": "peerSymbols is required for peer_compare"}

    peers = [s.strip() for s in str(peer_str).split(",") if s.strip()]

    comparisons = []

    # Add subject company
    inc = data.get("incomeStatements", [{}])[0] if data.get("incomeStatements") else {}
    comparisons.append({
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "pe": data.get("pe", 0),
        "pb": data.get("pb", 0),
        "roe": inc.get("roe", 0),
        "grossMargin": inc.get("grossMargin", 0),
        "netMargin": inc.get("netMargin", 0),
        "revenue": inc.get("revenue", 0),
        "netProfit": inc.get("netProfit", 0),
        "marketCap": data.get("marketCap", 0),
        "isSubject": True,
    })

    # Fetch peer data
    for peer in peers[:5]:  # limit to 5 peers
        try:
            pd = _get_financial_data(peer, years, "annual")
            p_inc = pd.get("incomeStatements", [{}])[0] if pd.get("incomeStatements") else {}
            comparisons.append({
                "symbol": pd.get("symbol", peer),
                "name": pd.get("name", ""),
                "pe": pd.get("pe", 0),
                "pb": pd.get("pb", 0),
                "roe": p_inc.get("roe", 0),
                "grossMargin": p_inc.get("grossMargin", 0),
                "netMargin": p_inc.get("netMargin", 0),
                "revenue": p_inc.get("revenue", 0),
                "netProfit": p_inc.get("netProfit", 0),
                "marketCap": pd.get("marketCap", 0),
                "isSubject": False,
            })
        except Exception:
            pass

    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "peerCount": len(comparisons) - 1,
        "comparison": comparisons,
    }


def _trend(data: dict, _config: dict) -> dict:
    """Multi-year trend analysis."""
    income = data.get("incomeStatements", [])
    balance = data.get("balanceSheets", [])
    cashflow = data.get("cashFlows", [])

    trends = {
        "revenue": [],
        "netProfit": [],
        "roe": [],
        "grossMargin": [],
    }

    for inc in reversed(income):  # oldest first
        trends["revenue"].append({"period": inc.get("period"), "value": inc.get("revenue", 0)})
        trends["netProfit"].append({"period": inc.get("period"), "value": inc.get("netProfit", 0)})
        trends["roe"].append({"period": inc.get("period"), "value": inc.get("roe", 0)})
        trends["grossMargin"].append({"period": inc.get("period"), "value": inc.get("grossMargin", 0)})

    # Calculate growth rates
    rev_values = [t["value"] for t in trends["revenue"] if t["value"]]
    profit_values = [t["value"] for t in trends["netProfit"] if t["value"]]

    rev_cagr = _cagr(rev_values) if len(rev_values) >= 2 else None
    profit_cagr = _cagr(profit_values) if len(profit_values) >= 2 else None

    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "trends": trends,
        "cagr": {
            "revenue": round(rev_cagr * 100, 2) if rev_cagr is not None else None,
            "netProfit": round(profit_cagr * 100, 2) if profit_cagr is not None else None,
        },
        "periods": len(income),
    }


def _cagr(values: list[float]) -> float | None:
    """Compound Annual Growth Rate."""
    if len(values) < 2 or values[0] <= 0:
        return None
    n = len(values) - 1
    return (values[-1] / values[0]) ** (1 / n) - 1


def _red_flags(data: dict, _config: dict) -> dict:
    """Detect financial red flags / accounting anomalies."""
    income = data.get("incomeStatements", [])
    balance = data.get("balanceSheets", [])
    cashflow = data.get("cashFlows", [])

    flags: list[dict] = []

    if not income:
        return {"symbol": data.get("symbol"), "flags": [], "note": "无财报数据"}

    latest_inc = income[0]
    latest_bs = balance[0] if balance else {}
    latest_cf = cashflow[0] if cashflow else {}

    net_profit = latest_inc.get("netProfit", 0)
    ocf = latest_cf.get("operatingCF", 0)

    # 1. Cash flow vs net income divergence
    if net_profit > 0 and ocf < 0:
        flags.append({
            "flag": "现金流与利润背离",
            "severity": "high",
            "detail": f"净利润 {net_profit:,.0f} 为正但经营现金流 {ocf:,.0f} 为负",
        })
    elif net_profit > 0 and ocf > 0 and ocf < net_profit * 0.5:
        flags.append({
            "flag": "现金流含金量低",
            "severity": "medium",
            "detail": f"经营现金流仅为净利润的 {ocf/net_profit*100:.0f}%",
        })

    # 2. High leverage
    total_assets = latest_bs.get("totalAssets", 0)
    total_liabilities = latest_bs.get("totalLiabilities", 0)
    if total_assets > 0:
        leverage = total_liabilities / total_assets
        if leverage > 0.8:
            flags.append({
                "flag": "高杠杆风险",
                "severity": "high",
                "detail": f"负债率 {leverage*100:.1f}%",
            })
        elif leverage > 0.6:
            flags.append({
                "flag": "负债偏高",
                "severity": "medium",
                "detail": f"负债率 {leverage*100:.1f}%",
            })

    # 3. Declining revenue/profit trend
    if len(income) >= 2:
        prev_rev = income[1].get("revenue", 0)
        curr_rev = latest_inc.get("revenue", 0)
        if prev_rev > 0 and curr_rev < prev_rev * 0.9:
            flags.append({
                "flag": "营收下滑",
                "severity": "medium",
                "detail": f"同比下降 {(1 - curr_rev/prev_rev)*100:.1f}%",
            })

        prev_profit = income[1].get("netProfit", 0)
        if prev_profit > 0 and net_profit < prev_profit * 0.7:
            flags.append({
                "flag": "利润大幅下降",
                "severity": "high",
                "detail": f"同比下降 {(1 - net_profit/prev_profit)*100:.1f}%",
            })

    # 4. ROE declining
    roe_values = [inc.get("roe", 0) for inc in income if inc.get("roe")]
    if len(roe_values) >= 3 and all(roe_values[i] < roe_values[i+1] for i in range(len(roe_values) - 1)):
        flags.append({
            "flag": "ROE持续下降",
            "severity": "medium",
            "detail": f"近{len(roe_values)}期ROE: {', '.join(f'{v:.1f}%' for v in roe_values)}",
        })

    risk_level = (
        "高风险" if any(f["severity"] == "high" for f in flags) else
        "中等风险" if flags else
        "低风险"
    )

    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "riskLevel": risk_level,
        "flagCount": len(flags),
        "flags": flags,
    }


def _comprehensive(data: dict, config: dict) -> dict:
    """Comprehensive CFO-level analysis."""
    return {
        "symbol": data.get("symbol"),
        "name": data.get("name", ""),
        "price": data.get("price", 0),
        "marketCap": data.get("marketCap", 0),
        "threeStatements": _three_statements(data, config),
        "dupont": _dupont(data, config),
        "ratios": _ratios(data, config),
        "trend": _trend(data, config),
        "redFlags": _red_flags(data, config),
    }


# ── Main ─────────────────────────────────────────────────────────

ACTIONS = {
    "three_statements": _three_statements,
    "dupont": _dupont,
    "ratios": _ratios,
    "peer_compare": _peer_compare,
    "trend": _trend,
    "red_flags": _red_flags,
    "comprehensive": _comprehensive,
}


def main():
    config = json.loads(sys.stdin.read())
    action = config.get("action", "comprehensive")
    symbol = config.get("symbol", "")
    years = config.get("years", 3)
    period = config.get("period", "annual")

    if not symbol:
        print(json.dumps({"error": "symbol is required"}, ensure_ascii=False))
        return

    handler = ACTIONS.get(action)
    if not handler:
        print(json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False))
        return

    try:
        data = _get_financial_data(symbol, years, period)
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
