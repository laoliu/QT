"""
macro_economic — Macro/economic indicator data engine.

Fetches GDP, CPI, PPI, PMI, interest rates, money supply (M2), and
other macro indicators from akshare.

Input (JSON via stdin / QUANT_CONFIG):
  {
    "action": "indicators" | "history" | "calendar" | "regime",
    "indicator": "gdp" | "cpi" | "ppi" | "pmi" | "shibor" | "lpr" | "m2" | "social_finance",
    "market": "cn" | "us",
    "limit": 20
  }

Output: JSON with macro data.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from typing import Any

# ── Proxy bypass ──────────────────────────────────────────────────────────────
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


# ── Available indicators catalog ──────────────────────────────────────────────

INDICATOR_CATALOG = {
    "cn": [
        {"id": "gdp", "name": "GDP (国内生产总值)", "frequency": "quarterly"},
        {"id": "cpi", "name": "CPI (居民消费价格指数)", "frequency": "monthly"},
        {"id": "ppi", "name": "PPI (工业生产者出厂价格指数)", "frequency": "monthly"},
        {"id": "pmi", "name": "PMI (制造业采购经理指数)", "frequency": "monthly"},
        {"id": "shibor", "name": "SHIBOR (上海银行间同业拆放利率)", "frequency": "daily"},
        {"id": "lpr", "name": "LPR (贷款市场报价利率)", "frequency": "monthly"},
        {"id": "m2", "name": "M2 (广义货币供应量)", "frequency": "monthly"},
        {"id": "social_finance", "name": "社会融资规模", "frequency": "monthly"},
    ],
    "us": [
        {"id": "fed_rate", "name": "Federal Funds Rate (联邦基金利率)", "frequency": "as_needed"},
        {"id": "us_cpi", "name": "US CPI (美国CPI)", "frequency": "monthly"},
        {"id": "us_pmi", "name": "US ISM PMI (美国ISM PMI)", "frequency": "monthly"},
        {"id": "us_unemployment", "name": "US Unemployment Rate (美国失业率)", "frequency": "monthly"},
    ],
}


def run_macro_economic(config: dict) -> dict:
    """Dispatch macro economic data actions."""
    action = config.get("action", "indicators")
    market = config.get("market", "cn")

    if action == "indicators":
        return _list_indicators(market)
    elif action == "history":
        indicator = config.get("indicator", "")
        limit = config.get("limit", 20)
        if not indicator:
            return {"error": "indicator is required for history action"}
        return _get_indicator_history(indicator, market, limit)
    elif action == "calendar":
        return _get_economic_calendar(market, config.get("limit", 20))
    elif action == "regime":
        return _analyze_macro_regime(market)
    else:
        return {"error": f"未知 action: {action}"}


def _list_indicators(market: str) -> dict:
    """Return available macro indicators for the market."""
    indicators = INDICATOR_CATALOG.get(market, INDICATOR_CATALOG["cn"])
    return {
        "market": market,
        "indicators": indicators,
        "count": len(indicators),
    }


def _get_indicator_history(indicator: str, market: str, limit: int) -> dict:
    """Fetch time-series history for a given indicator."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    fetchers = {
        "gdp": _fetch_gdp,
        "cpi": _fetch_cpi,
        "ppi": _fetch_ppi,
        "pmi": _fetch_pmi,
        "shibor": _fetch_shibor,
        "lpr": _fetch_lpr,
        "m2": _fetch_m2,
        "social_finance": _fetch_social_finance,
        "fed_rate": _fetch_fed_rate,
        "us_cpi": _fetch_us_cpi,
        "us_pmi": _fetch_us_pmi,
        "us_unemployment": _fetch_us_unemployment,
    }

    fetcher = fetchers.get(indicator)
    if not fetcher:
        return {"error": f"未知指标: {indicator}", "available": list(fetchers.keys())}

    try:
        data = fetcher(limit)
        return {
            "indicator": indicator,
            "market": market,
            "count": len(data),
            "data": data,
        }
    except Exception as e:
        return {"error": f"获取 {indicator} 数据失败: {e}"}


# ── China macro fetchers ──────────────────────────────────────────────────────

def _fetch_gdp(limit: int) -> list[dict]:
    """GDP quarterly data."""
    import akshare as ak
    try:
        df = ak.macro_china_gdp()
        if df is None or df.empty:
            return []
        records = []
        for _, row in df.head(limit).iterrows():
            records.append({
                "date": str(row.get("季度", row.get("日期", "")))[:10],
                "gdp": _safe_float(row.get("国内生产总值-绝对值", row.get("今值", None))),
                "gdp_yoy": _safe_float(row.get("国内生产总值-同比增长", row.get("同比", None))),
            })
        return records
    except Exception:
        # Fallback: try alternate API
        try:
            df = ak.macro_china_gdp_yearly()
            if df is None or df.empty:
                return []
            records = []
            for _, row in df.tail(limit).iterrows():
                records.append({
                    "date": str(row.iloc[0])[:10] if len(row) > 0 else "",
                    "value": _safe_float(row.iloc[1]) if len(row) > 1 else None,
                })
            return records
        except Exception:
            return []


def _fetch_cpi(limit: int) -> list[dict]:
    """CPI monthly data."""
    import akshare as ak
    try:
        df = ak.macro_china_cpi()
        if df is None or df.empty:
            return []
        records = []
        for _, row in df.head(limit).iterrows():
            records.append({
                "date": str(row.get("月份", row.get("日期", "")))[:10],
                "cpi": _safe_float(row.get("全国-当月", row.get("今值", None))),
                "cpi_yoy": _safe_float(row.get("全国-同比增长", row.get("同比", None))),
                "cpi_mom": _safe_float(row.get("全国-环比增长", row.get("环比", None))),
            })
        return records
    except Exception:
        return []


def _fetch_ppi(limit: int) -> list[dict]:
    """PPI monthly data."""
    import akshare as ak
    try:
        df = ak.macro_china_ppi()
        if df is None or df.empty:
            return []
        records = []
        for _, row in df.head(limit).iterrows():
            records.append({
                "date": str(row.get("月份", row.get("日期", "")))[:10],
                "ppi_yoy": _safe_float(row.get("当月", row.get("今值", None))),
            })
        return records
    except Exception:
        return []


def _fetch_pmi(limit: int) -> list[dict]:
    """PMI (manufacturing) monthly data."""
    import akshare as ak
    try:
        df = ak.macro_china_pmi()
        if df is None or df.empty:
            return []
        records = []
        for _, row in df.head(limit).iterrows():
            date_raw = str(row.get("月份", row.get("日期", ""))).strip()
            # Normalize "2026年02月份" → "2026-02"
            date_clean = date_raw.replace("年", "-").replace("月份", "").replace("月", "")
            records.append({
                "date": date_clean,
                "pmi": _safe_float(row.get("制造业-指数", row.get("今值", None))),
                "pmi_yoy": _safe_float(row.get("制造业-同比增长", None)),
                "non_mfg_pmi": _safe_float(row.get("非制造业-指数", None)),
                "non_mfg_pmi_yoy": _safe_float(row.get("非制造业-同比增长", None)),
            })
        return records
    except Exception:
        return []


def _fetch_shibor(limit: int) -> list[dict]:
    """SHIBOR (interbank rate) daily data."""
    import akshare as ak
    try:
        df = ak.rate_interbank(market="上海银行间同业拆放利率(Shibor)", symbol="Shibor人民币", indicator="1月")
        if df is None or df.empty:
            return []
        records = []
        for _, row in df.tail(limit).iterrows():
            records.append({
                "date": str(row.get("报告日", row.get("日期", "")))[:10],
                "rate": _safe_float(row.get("利率", row.get("Shibor", None))),
            })
        return records
    except Exception:
        return []


def _fetch_lpr(limit: int) -> list[dict]:
    """LPR monthly data."""
    import akshare as ak
    try:
        df = ak.macro_china_lpr()
        if df is None or df.empty:
            return []
        records = []
        for _, row in df.tail(limit).iterrows():
            records.append({
                "date": str(row.get("TRADE_DATE", row.get("日期", "")))[:10],
                "lpr_1y": _safe_float(row.get("LPR1Y", row.get("1年期LPR", None))),
                "lpr_5y": _safe_float(row.get("LPR5Y", row.get("5年期以上LPR", None))),
            })
        return records
    except Exception:
        return []


def _fetch_m2(limit: int) -> list[dict]:
    """M2 money supply monthly data."""
    import akshare as ak
    try:
        df = ak.macro_china_money_supply()
        if df is None or df.empty:
            return []
        records = []
        for _, row in df.head(limit).iterrows():
            records.append({
                "date": str(row.get("月份", row.get("日期", "")))[:10],
                "m2": _safe_float(row.get("货币和准货币(M2)-数量(亿元)", row.get("M2-数量(亿元)", None))),
                "m2_yoy": _safe_float(row.get("货币和准货币(M2)-同比增长", row.get("M2-同比增长", None))),
                "m1": _safe_float(row.get("货币(M1)-数量(亿元)", row.get("M1-数量(亿元)", None))),
                "m1_yoy": _safe_float(row.get("货币(M1)-同比增长", row.get("M1-同比增长", None))),
            })
        return records
    except Exception:
        return []


def _fetch_social_finance(limit: int) -> list[dict]:
    """Social financing aggregate monthly data."""
    import akshare as ak
    try:
        df = ak.macro_china_shrzgm()
        if df is None or df.empty:
            return []
        records = []
        for _, row in df.tail(limit).iterrows():
            records.append({
                "date": str(row.get("月份", row.get("日期", "")))[:10],
                "total": _safe_float(row.get("社会融资规模增量", row.get("其中-Loss", None))),
            })
        return records
    except Exception:
        return []


# ── US macro fetchers ─────────────────────────────────────────────────────────

def _fetch_fed_rate(limit: int) -> list[dict]:
    """US Federal Funds Rate."""
    import akshare as ak
    try:
        df = ak.macro_bank_usa_interest_rate()
        if df is None or df.empty:
            return []
        records = []
        for _, row in df.tail(limit).iterrows():
            records.append({
                "date": str(row.get("日期", row.get("商报日期", "")))[:10],
                "rate": _safe_float(row.get("今值", row.get("利率", None))),
                "previous": _safe_float(row.get("前值", None)),
            })
        return records
    except Exception:
        return []


def _fetch_us_cpi(limit: int) -> list[dict]:
    """US CPI year-over-year."""
    import akshare as ak
    try:
        df = ak.macro_usa_cpi()
        if df is None or df.empty:
            return []
        records = []
        for _, row in df.tail(limit).iterrows():
            records.append({
                "date": str(row.get("日期", ""))[:10],
                "cpi_yoy": _safe_float(row.get("今值", None)),
                "previous": _safe_float(row.get("前值", None)),
            })
        return records
    except Exception:
        return []


def _fetch_us_pmi(limit: int) -> list[dict]:
    """US ISM Manufacturing PMI."""
    import akshare as ak
    try:
        df = ak.macro_usa_ism_pmi()
        if df is None or df.empty:
            return []
        records = []
        for _, row in df.tail(limit).iterrows():
            records.append({
                "date": str(row.get("日期", ""))[:10],
                "pmi": _safe_float(row.get("今值", None)),
                "previous": _safe_float(row.get("前值", None)),
            })
        return records
    except Exception:
        return []


def _fetch_us_unemployment(limit: int) -> list[dict]:
    """US Unemployment Rate."""
    import akshare as ak
    try:
        df = ak.macro_usa_unemployment_rate()
        if df is None or df.empty:
            return []
        records = []
        for _, row in df.tail(limit).iterrows():
            records.append({
                "date": str(row.get("日期", ""))[:10],
                "rate": _safe_float(row.get("今值", None)),
                "previous": _safe_float(row.get("前值", None)),
            })
        return records
    except Exception:
        return []


# ── Economic calendar ─────────────────────────────────────────────────────────

def _get_economic_calendar(market: str, limit: int) -> dict:
    """Upcoming economic events/releases."""
    import akshare as ak
    events: list[dict] = []

    try:
        if market in ("cn", "global"):
            try:
                df = ak.news_economic_baidu()
                if df is not None and not df.empty:
                    for _, row in df.head(limit).iterrows():
                        events.append({
                            "date": str(row.get("日期", ""))[:10],
                            "time": str(row.get("时间", "")),
                            "country": str(row.get("国家", "中国")),
                            "event": str(row.get("事件", row.get("指标名称", ""))),
                            "importance": str(row.get("重要性", "")),
                            "previous": str(row.get("前值", "")),
                            "forecast": str(row.get("预测值", "")),
                            "actual": str(row.get("公布值", "")),
                        })
            except Exception:
                pass
    except Exception:
        pass

    return {
        "market": market,
        "count": len(events),
        "events": events[:limit],
    }


# ── Macro regime analysis ─────────────────────────────────────────────────────

def _analyze_macro_regime(market: str) -> dict:
    """
    Simple macro regime classification based on latest data points:
    - Growth + Inflation → Overheating
    - Growth + Deflation → Recovery
    - Slowdown + Inflation → Stagflation
    - Slowdown + Deflation → Recession
    """
    signals: dict[str, Any] = {}
    regime = "unknown"
    description = ""

    try:
        # Gather latest data points
        pmi_data = _fetch_pmi(3)
        cpi_data = _fetch_cpi(3)
        m2_data = _fetch_m2(3)

        latest_pmi = pmi_data[-1].get("pmi") if pmi_data else None
        latest_cpi_yoy = cpi_data[-1].get("cpi_yoy") if cpi_data else None
        latest_m2_yoy = m2_data[-1].get("m2_yoy") if m2_data else None

        signals["pmi"] = latest_pmi
        signals["cpi_yoy"] = latest_cpi_yoy
        signals["m2_yoy"] = latest_m2_yoy

        # Classify: PMI > 50 = expansion; CPI > 3 = high inflation
        if latest_pmi is not None and latest_cpi_yoy is not None:
            expanding = latest_pmi > 50
            inflating = latest_cpi_yoy > 3.0

            if expanding and inflating:
                regime = "overheating"
                description = "经济扩张 + 通胀上行 → 过热期。关注紧缩政策风险。"
            elif expanding and not inflating:
                regime = "recovery"
                description = "经济扩张 + 通胀温和 → 复苏期。对权益资产最友好的阶段。"
            elif not expanding and inflating:
                regime = "stagflation"
                description = "经济放缓 + 通胀高企 → 类滞胀。防御为主，关注必需消费和贵金属。"
            else:
                regime = "recession"
                description = "经济放缓 + 通胀低迷 → 衰退/宽松期。关注利率债和高股息。"
        else:
            description = "数据不足，无法判断宏观周期。"

    except Exception as e:
        return {"error": f"宏观周期分析失败: {e}"}

    return {
        "market": market,
        "regime": regime,
        "description": description,
        "signals": signals,
        "methodology": "美林时钟简化版: PMI>50=扩张, CPI>3%=通胀",
    }


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN
            return None
        return round(f, 4)
    except (ValueError, TypeError):
        return None


# ── CLI entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    try:
        if "--json" in sys.argv:
            raw = sys.stdin.read()
            cfg = json.loads(raw) if raw.strip() else {}
        else:
            cfg = json.loads(os.environ.get("QUANT_CONFIG", "{}"))

        result = run_macro_economic(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
