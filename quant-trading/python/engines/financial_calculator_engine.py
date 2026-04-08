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
                result["eps"] = _parse_float(latest.get("基本每股收益(Ԫ)", 0))
                result["bvps"] = _parse_float(latest.get("每股净资产(Ԫ)", 0))
                result["roe"] = _parse_float(latest.get("净资产收益率(%)", 0))
                result["revenue"] = _parse_float(latest.get("营业总收入(Ԫ)", 0))
                result["netProfit"] = _parse_float(latest.get("净利润(Ԫ)", 0))
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


# ── Tax Calculation Models ──────────────────────────────────────

def _detect_market_from_symbol(symbol: str) -> str:
    """Detect market for tax purposes."""
    s = symbol.upper().strip()
    if s.endswith(".HK") or s.startswith("HK"):
        return "hk"
    if any(c.isalpha() for c in s) and not s.startswith(("0", "3", "6")):
        return "us"
    return "cn"


def _dividend_tax(config: dict) -> dict:
    """
    A股股息红利税 — 差异化税率:
      持股 ≤ 1个月: 20%
      持股 1-12个月: 10%
      持股 > 12个月: 免征
    机构投资者统一按 25% 企业所得税（如适用）。
    """
    holding_months = config.get("holding_months")
    dividend_amount = _parse_float(config.get("dividend_amount", 0))
    is_institutional = config.get("is_institutional", False)
    symbol = config.get("symbol", "")

    if dividend_amount <= 0:
        return {"error": "请提供 dividend_amount（股息红利金额）"}

    if is_institutional:
        tax_rate = 0.25
        tax_label = "企业所得税 25%"
    elif holding_months is not None:
        if holding_months <= 1:
            tax_rate = 0.20
            tax_label = "持股≤1个月，税率20%"
        elif holding_months <= 12:
            tax_rate = 0.10
            tax_label = "持股1-12个月，税率10%"
        else:
            tax_rate = 0.0
            tax_label = "持股>12个月，免征"
    else:
        # Default to worst case if holding period unknown
        tax_rate = 0.20
        tax_label = "持股期限未知，按最高税率20%计算"

    tax_amount = dividend_amount * tax_rate
    after_tax = dividend_amount - tax_amount

    return {
        "type": "dividend_tax",
        "symbol": symbol,
        "dividendAmount": dividend_amount,
        "holdingMonths": holding_months,
        "isInstitutional": is_institutional,
        "taxRate": tax_rate,
        "taxRateLabel": tax_label,
        "taxAmount": round(tax_amount, 2),
        "afterTaxAmount": round(after_tax, 2),
        "note": "A股个人投资者持股超过1年免征股息红利个人所得税（财税[2015]101号）",
    }


def _hk_connect_dividend(config: dict) -> dict:
    """
    港股通红利税:
      H股: 个人投资者 20%（企业 10%）
      非H股（红筹/民企）: 20% 或按注册地税率
    """
    dividend_amount = _parse_float(config.get("dividend_amount", 0))
    is_institutional = config.get("is_institutional", False)
    symbol = config.get("symbol", "")

    if dividend_amount <= 0:
        return {"error": "请提供 dividend_amount（股息红利金额）"}

    if is_institutional:
        tax_rate = 0.10
        tax_label = "港股通企业投资者H股红利税 10%"
    else:
        tax_rate = 0.20
        tax_label = "港股通个人投资者红利税 20%"

    tax_amount = dividend_amount * tax_rate
    after_tax = dividend_amount - tax_amount

    return {
        "type": "hk_connect_dividend",
        "symbol": symbol,
        "dividendAmount": dividend_amount,
        "isInstitutional": is_institutional,
        "taxRate": tax_rate,
        "taxRateLabel": tax_label,
        "taxAmount": round(tax_amount, 2),
        "afterTaxAmount": round(after_tax, 2),
        "note": "港股通红利税由中国结算代扣代缴。H股企业10%，个人20%；红筹股/民企统一20%",
    }


def _block_trade_tax(config: dict) -> dict:
    """
    大宗交易税费计算:
      印花税: 卖方 0.05%（2023年8月28日起）
      经手费: 买卖双方各 0.00487%
      证管费: 买卖双方各 0.002%
      过户费: 买卖双方各 0.001%
      佣金: 一般不超过成交金额的 0.3%（可协商）
    """
    trade_amount = _parse_float(config.get("trade_amount", 0))
    symbol = config.get("symbol", "")

    if trade_amount <= 0:
        return {"error": "请提供 trade_amount（交易金额）"}

    stamp_duty = trade_amount * 0.0005  # 卖方印花税 0.05%
    handling_fee = trade_amount * 0.0000487 * 2  # 买卖双方经手费
    regulation_fee = trade_amount * 0.00002 * 2  # 买卖双方证管费
    transfer_fee = trade_amount * 0.00001 * 2  # 买卖双方过户费
    commission_est = trade_amount * 0.001  # 估计佣金 0.1%（大宗可协商）

    total_fees = stamp_duty + handling_fee + regulation_fee + transfer_fee + commission_est

    return {
        "type": "block_trade",
        "symbol": symbol,
        "tradeAmount": trade_amount,
        "fees": {
            "stampDuty": {"rate": "0.05%（卖方）", "amount": round(stamp_duty, 2)},
            "handlingFee": {"rate": "0.00487%（双边）", "amount": round(handling_fee, 2)},
            "regulationFee": {"rate": "0.002%（双边）", "amount": round(regulation_fee, 2)},
            "transferFee": {"rate": "0.001%（双边）", "amount": round(transfer_fee, 2)},
            "commissionEst": {"rate": "≈0.1%（可协商）", "amount": round(commission_est, 2)},
        },
        "totalFees": round(total_fees, 2),
        "feeRatio": round(total_fees / trade_amount * 100, 4),
        "note": "大宗交易佣金率可与券商协商，此处按0.1%估算。印花税率为2023年8月28日调整后税率",
    }


def _annual_tax_summary(config: dict) -> dict:
    """
    年度税务摘要 — 从交易记录中汇总计算:
      1. 各市场资本利得汇总
      2. 股息红利税汇总
      3. 交易费用汇总
      4. 纳税建议
    """
    tax_year = config.get("tax_year", 2025)
    is_institutional = config.get("is_institutional", False)
    records = config.get("trade_records", [])
    symbol_filter = config.get("symbol", "")

    # Filter by symbol if provided
    if symbol_filter:
        records = [r for r in records if r.get("symbol", "") == symbol_filter]

    # Classify trades by market
    cn_trades: list[dict] = []
    hk_trades: list[dict] = []
    us_trades: list[dict] = []

    for r in records:
        sym = r.get("symbol", "")
        market = r.get("market", _detect_market_from_symbol(sym))
        if market == "hk":
            hk_trades.append(r)
        elif market == "us":
            us_trades.append(r)
        else:
            cn_trades.append(r)

    def _sum_pnl(trades: list[dict]) -> tuple[float, float, int]:
        total_gain = 0.0
        total_loss = 0.0
        count = 0
        for t in trades:
            pnl = _parse_float(t.get("pnl", 0))
            if pnl > 0:
                total_gain += pnl
            else:
                total_loss += pnl
            count += 1
        return total_gain, total_loss, count

    cn_gain, cn_loss, cn_count = _sum_pnl(cn_trades)
    hk_gain, hk_loss, hk_count = _sum_pnl(hk_trades)
    us_gain, us_loss, us_count = _sum_pnl(us_trades)

    # Dividend tax estimation
    dividend_records = [r for r in records if _parse_float(r.get("dividendAmount", 0)) > 0]
    total_dividends = sum(_parse_float(r.get("dividendAmount", 0)) for r in dividend_records)

    # A股个人：暂免资本利得税；机构：按企业所得税
    cn_capital_gains_tax = 0.0
    if is_institutional:
        cn_net = cn_gain + cn_loss
        cn_capital_gains_tax = max(0, cn_net) * 0.25

    # 港股通：资本利得暂免；红利税20%（个人）/10%（机构）
    hk_dividend_tax_rate = 0.10 if is_institutional else 0.20
    hk_dividend_tax = 0.0
    for r in records:
        sym = r.get("symbol", "")
        mkt = r.get("market", _detect_market_from_symbol(sym))
        if mkt == "hk":
            div = _parse_float(r.get("dividendAmount", 0))
            hk_dividend_tax += div * hk_dividend_tax_rate

    # A股红利税估算（按最高税率20%，持股期限未知）
    cn_dividend_tax = 0.0
    for r in records:
        sym = r.get("symbol", "")
        mkt = r.get("market", _detect_market_from_symbol(sym))
        if mkt == "cn":
            div = _parse_float(r.get("dividendAmount", 0))
            holding = r.get("holdingMonths")
            if holding is not None:
                if holding <= 1:
                    cn_dividend_tax += div * 0.20
                elif holding <= 12:
                    cn_dividend_tax += div * 0.10
                # >12 months: exempt
            else:
                cn_dividend_tax += div * 0.20  # worst case

    total_tax_estimate = cn_capital_gains_tax + hk_dividend_tax + cn_dividend_tax

    # Build suggestions
    suggestions: list[str] = []
    if not is_institutional:
        suggestions.append("A股个人投资者资本利得暂免个人所得税")
        if cn_dividend_tax > 0:
            suggestions.append("持股超过1年可免征股息红利税，建议优化持股期限")
    else:
        if cn_capital_gains_tax > 0:
            suggestions.append("机构投资者需按企业所得税缴纳资本利得税")
        suggestions.append("可利用亏损抵扣减少应纳税所得额")

    if hk_count > 0:
        suggestions.append("港股通资本利得暂免个人所得税，但红利税需缴纳")

    return {
        "type": "annual_summary",
        "taxYear": tax_year,
        "isInstitutional": is_institutional,
        "symbolFilter": symbol_filter or "全部",
        "tradeCount": {
            "cn": cn_count,
            "hk": hk_count,
            "us": us_count,
            "total": cn_count + hk_count + us_count,
        },
        "capitalGains": {
            "cn": {
                "totalGain": round(cn_gain, 2),
                "totalLoss": round(cn_loss, 2),
                "netPnl": round(cn_gain + cn_loss, 2),
                "taxLiability": round(cn_capital_gains_tax, 2),
                "taxNote": "机构按25%企业所得税" if is_institutional else "个人暂免",
            },
            "hk": {
                "totalGain": round(hk_gain, 2),
                "totalLoss": round(hk_loss, 2),
                "netPnl": round(hk_gain + hk_loss, 2),
                "taxLiability": 0,
                "taxNote": "港股通资本利得暂免",
            },
            "us": {
                "totalGain": round(us_gain, 2),
                "totalLoss": round(us_loss, 2),
                "netPnl": round(us_gain + us_loss, 2),
                "taxNote": "美股资本利得需按照中美税收协定申报",
            },
        },
        "dividendTax": {
            "totalDividends": round(total_dividends, 2),
            "cnDividendTax": round(cn_dividend_tax, 2),
            "hkConnectDividendTax": round(hk_dividend_tax, 2),
            "cnNote": "A股红利税取决于持股期限（≤1月20%, 1-12月10%, >1年免征）",
            "hkNote": f"港股通红利税 {int(hk_dividend_tax_rate*100)}%",
        },
        "totalEstimatedTax": round(total_tax_estimate, 2),
        "suggestions": suggestions,
        "disclaimer": "本计算仅供参考，实际税务处理请咨询专业税务顾问。税率以最新法规为准。",
    }


def _tax_calculation(_data: dict, config: dict) -> dict:
    """Route to specific tax calculation based on tax_type."""
    tax_type = config.get("tax_type", "annual_summary")

    if tax_type == "dividend_tax":
        return _dividend_tax(config)
    elif tax_type == "hk_connect_dividend":
        return _hk_connect_dividend(config)
    elif tax_type == "block_trade":
        return _block_trade_tax(config)
    elif tax_type == "annual_summary":
        return _annual_tax_summary(config)
    else:
        return {"error": f"未知的税务计算类型: {tax_type}"}


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
    "tax_calculation": _tax_calculation,
}


def main():
    config = json.loads(sys.stdin.read())
    action = config.get("action", "compare")
    symbol = config.get("symbol", "")

    handler = ACTIONS.get(action)
    if not handler:
        print(json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False))
        return

    # tax_calculation doesn't require stock data fetching
    if action == "tax_calculation":
        try:
            result = handler({}, config)
            print(json.dumps(result, ensure_ascii=False, default=str))
        except Exception as e:
            print(json.dumps({
                "error": str(e),
                "traceback": traceback.format_exc(),
            }, ensure_ascii=False))
        return

    if not symbol:
        print(json.dumps({"error": "symbol is required"}, ensure_ascii=False))
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
