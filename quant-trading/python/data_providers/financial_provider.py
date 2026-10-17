"""
Financial statement data provider.

Fetches detailed financial data (revenue, profit, balance sheet, cash flow)
from akshare (A-shares) and yfinance (US/HK/global) for advanced screening
strategies like CAN SLIM, Piotroski F-Score, Magic Formula, and PEG/GARP.

Provides:
  - Quarterly & annual revenue/profit data
  - Balance sheet metrics (debt, assets, equity)
  - Cash flow metrics (operating CF, FCF)
  - Derived metrics (ROE, ROA, ROIC, debt ratio, CAGR, F-Score, EBIT/EV)

Caches results in SQLite for performance.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import traceback
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

# Bypass proxy for domestic Chinese financial API domains.
# East Money push2 API fails through typical HTTPS proxies.
_NO_PROXY_DOMAINS = ".eastmoney.com,.sina.com.cn"
_existing_no_proxy = os.environ.get("NO_PROXY", "")
if _existing_no_proxy:
    os.environ["NO_PROXY"] = _existing_no_proxy + "," + _NO_PROXY_DOMAINS
else:
    os.environ["NO_PROXY"] = _NO_PROXY_DOMAINS


def _retry_call(fn, *args, retries: int = 3, base_delay: float = 1.0, **kwargs):
    """Call fn with exponential-backoff retry on transient errors.

    Catches ConnectionError, RemoteDisconnected, JSONDecodeError, etc.
    """
    import time
    last_err = None
    for attempt in range(retries):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            last_err = e
            err_str = str(e)
            # Only retry on transient network/parsing errors
            transient = any(kw in err_str for kw in [
                "RemoteDisconnected", "Connection aborted",
                "ConnectionReset", "Expecting value",
                "NoneType", "timed out", "Too Many Requests",
                "HTTPSConnectionPool", "Read timed out",
            ])
            if not transient or attempt == retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            time.sleep(delay)
    raise last_err  # type: ignore
    os.environ["NO_PROXY"] = _NO_PROXY_DOMAINS


# ── Data Models ─────────────────────────────────────────────────


@dataclass
class QuarterlyData:
    """Single-quarter financial snapshot."""

    period: str = ""  # e.g. "2025Q3"
    revenue: float = 0.0
    net_profit: float = 0.0
    gross_profit: float = 0.0
    operating_profit: float = 0.0
    total_assets: float = 0.0
    total_liabilities: float = 0.0
    total_equity: float = 0.0
    current_assets: float = 0.0
    current_liabilities: float = 0.0
    cash_and_equivalents: float = 0.0
    operating_cash_flow: float = 0.0
    capital_expenditure: float = 0.0
    depreciation: float = 0.0
    shares_outstanding: float = 0.0
    interest_expense: float = 0.0
    tax_expense: float = 0.0
    ebit: float = 0.0  # can be computed
    ebitda: float = 0.0  # can be computed


@dataclass
class FinancialSnapshot:
    """Aggregated financial profile for a single stock."""

    code: str = ""
    name: str = ""
    market: str = ""

    # Latest quarter
    latest_quarter: str = ""

    # Growth metrics (YoY %)
    revenue_growth_yoy: Optional[float] = None
    revenue_growth_3y_cagr: Optional[float] = None
    net_profit_growth_yoy: Optional[float] = None
    eps_growth_yoy: Optional[float] = None

    # Profitability
    roe: Optional[float] = None  # Net profit / equity
    roa: Optional[float] = None  # Net profit / total assets
    roic: Optional[float] = None  # NOPAT / invested capital
    gross_margin: Optional[float] = None  # %
    operating_margin: Optional[float] = None  # %
    net_margin: Optional[float] = None  # %

    # Balance sheet
    debt_ratio: Optional[float] = None  # liabilities / assets
    current_ratio: Optional[float] = None  # current assets / current liabilities
    debt_to_equity: Optional[float] = None

    # Cash flow
    operating_cf: Optional[float] = None
    free_cash_flow: Optional[float] = None
    ocf_to_net_profit: Optional[float] = None  # quality of earnings

    # Market data (for screener convenience)
    pe: Optional[float] = None
    pe_ttm: Optional[float] = None
    pb: Optional[float] = None
    eps: Optional[float] = None
    price: Optional[float] = None
    total_market_val_yi: Optional[float] = None  # market cap in 亿
    shares_outstanding: Optional[float] = None  # total shares
    industry: Optional[str] = None  # 行业板块 (e.g. "白酒", "农化制品")

    # Efficiency
    asset_turnover: Optional[float] = None  # revenue / total assets
    ar_turnover_days: Optional[float] = None  # 应收账款周转天数 (higher = slower collections)

    # Margin trend (for cost-sensitivity proxy)
    gross_margin_prev: Optional[float] = None  # prior-year gross margin for trend comparison

    # Valuation helpers
    ebit: Optional[float] = None
    ebitda: Optional[float] = None
    enterprise_value: Optional[float] = None  # market cap + debt - cash
    ev_to_ebit: Optional[float] = None

    # Piotroski F-Score components
    f_score: Optional[int] = None
    f_score_details: dict = field(default_factory=dict)

    # Raw quarterly data (last 8 quarters for trend analysis)
    quarterly_data: list[dict] = field(default_factory=list)


# ── Cache Layer ─────────────────────────────────────────────────

_FINANCIAL_CACHE_TTL = 24 * 3600  # 24 hours

_FINANCIAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS financial_data (
    code TEXT NOT NULL,
    market TEXT NOT NULL DEFAULT 'cn_stock',
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (code, market)
);
"""


def _get_financial_db() -> sqlite3.Connection:
    """Get SQLite connection for financial data cache."""
    cache_dir = Path.home() / ".quant-trading" / "extensions" / "quant-trading" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    db_path = cache_dir / "financial_data.db"
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_FINANCIAL_SCHEMA)
    return conn


def _get_cached_financial(code: str, market: str) -> Optional[FinancialSnapshot]:
    """Get cached financial data if fresh enough."""
    try:
        conn = _get_financial_db()
        row = conn.execute(
            "SELECT data_json, updated_at FROM financial_data WHERE code = ? AND market = ?",
            (code, market),
        ).fetchone()
        conn.close()
        if not row:
            return None
        data_json, updated_at = row
        # Check freshness
        updated = datetime.fromisoformat(updated_at)
        if (datetime.utcnow() - updated).total_seconds() > _FINANCIAL_CACHE_TTL:
            return None
        data = json.loads(data_json)
        snap = FinancialSnapshot(**{k: v for k, v in data.items()
                                    if k in FinancialSnapshot.__dataclass_fields__})
        return snap
    except Exception:
        return None


def _save_cached_financial(snap: FinancialSnapshot) -> None:
    """Save financial snapshot to cache."""
    try:
        conn = _get_financial_db()
        now = datetime.utcnow().isoformat()
        data = asdict(snap)
        conn.execute(
            "INSERT OR REPLACE INTO financial_data (code, market, data_json, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (snap.code, snap.market, json.dumps(data, ensure_ascii=False, default=str), now),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[financial_cache] Save error: {e}", file=sys.stderr)


# ── A-Share Financial Data (akshare) ────────────────────────────


def _code_to_xq_symbol(code: str) -> str:
    """Convert numeric A-share code to Xueqiu symbol like 'SH600519'."""
    if code.startswith("6"):
        return f"SH{code}"
    if code.startswith(("0", "3")):
        return f"SZ{code}"
    if code.startswith(("4", "8", "9")):
        return f"BJ{code}"
    return f"SH{code}"


def _fetch_cn_financials(code: str) -> FinancialSnapshot:
    """Fetch A-share financial data via akshare.

    Uses multiple akshare APIs:
    - stock_financial_analysis_indicator: ROE, margins, ratios, growth (Sina)
    - stock_financial_report_sina: balance sheet, cash flow
    - stock_individual_info_em: name, price, market cap (for PE/PB)

    Fetches two years of indicator data so F-Score can compute proper YoY deltas.
    """
    import akshare as ak
    import pandas as pd

    snap = FinancialSnapshot(code=code, market="cn_stock")

    # ── Stock basic info (name, price, market cap) ──
    try:
        df_info = _retry_call(ak.stock_individual_info_em, symbol=code)
        if df_info is not None and not df_info.empty:
            info_map: dict[str, Any] = {}
            for _, row in df_info.iterrows():
                info_map[str(row.iloc[0])] = row.iloc[1]
            snap.name = str(info_map.get("股票简称", ""))
            snap.price = _safe_float(info_map.get("最新"))
            mkt_cap = _safe_float(info_map.get("总市值"))
            if mkt_cap:
                snap.total_market_val_yi = round(mkt_cap / 1e8, 2)
            shares = _safe_float(info_map.get("总股本"))
            if shares:
                snap.shares_outstanding = shares
            industry = info_map.get("行业")
            if industry and str(industry).strip():
                snap.industry = str(industry).strip()
    except Exception as e:
        print(f"[financial] stock_info error for {code}: {e}", file=sys.stderr)

    # ── Financial indicator data (Sina, comprehensive) ──
    # We store prev-year data for YoY F-Score comparison
    _prev_year_data: dict[str, Any] = {}  # same-period data from prior year
    try:
        df_ind = _retry_call(ak.stock_financial_analysis_indicator, symbol=code, start_year="2021")
        if df_ind is not None and not df_ind.empty:
            df_ind = df_ind.sort_values("日期", ascending=False)

            # Latest quarter
            latest = df_ind.iloc[0]
            snap.latest_quarter = str(latest.get("日期", ""))[:10]

            # ROE, margins
            snap.roe = _safe_float(latest.get("净资产收益率(%)"))
            snap.gross_margin = _safe_float(latest.get("销售毛利率(%)"))
            if snap.gross_margin is None:
                snap.gross_margin = _safe_float(latest.get("主营业务利润率(%)"))
            snap.net_margin = _safe_float(latest.get("销售净利率(%)"))
            snap.operating_margin = _safe_float(latest.get("营业利润率(%)"))
            snap.roa = _safe_float(latest.get("总资产净利润率(%)"))
            snap.current_ratio = _safe_float(latest.get("流动比率"))
            snap.debt_to_equity = _safe_float(latest.get("产权比率(%)"))
            snap.debt_ratio = _safe_float(latest.get("资产负债率(%)"))

            # EPS
            snap.eps = _safe_float(latest.get("摊薄每股收益(Ԫ)"))

            # PE/PB computed from available data
            if snap.price and snap.eps and snap.eps > 0:
                snap.pe = round(snap.price / snap.eps, 2)
                snap.pe_ttm = snap.pe
            bvps = _safe_float(latest.get("每股净资产_调整后(Ԫ)"))
            if snap.price and bvps and bvps > 0:
                snap.pb = round(snap.price / bvps, 2)

            # Growth: use ready-made growth rate fields from akshare
            snap.revenue_growth_yoy = _safe_float(latest.get("主营业务收入增长率(%)"))
            snap.net_profit_growth_yoy = _safe_float(latest.get("净利润增长率(%)"))
            snap.eps_growth_yoy = snap.net_profit_growth_yoy

            # OCF/profit ratio
            ocf_ratio = _safe_float(latest.get("经营现金净流量与净利润的比率(%)"))
            if ocf_ratio is not None:
                snap.ocf_to_net_profit = round(ocf_ratio, 2)

            # Asset turnover
            snap.asset_turnover = _safe_float(latest.get("总资产周转率(次)"))

            # Accounts receivable turnover (collection efficiency)
            ar_days = _safe_float(latest.get("应收账款周转天数(天)"))
            if ar_days is not None:
                snap.ar_turnover_days = round(ar_days, 1)

            # 3-year revenue CAGR from historical data
            if len(df_ind) >= 13:
                try:
                    rev_latest = _safe_float(latest.get("主营业务利润(Ԫ)"))
                    rev_3y = _safe_float(df_ind.iloc[12].get("主营业务利润(Ԫ)"))
                    if rev_latest and rev_3y and rev_3y > 0:
                        snap.revenue_growth_3y_cagr = round(
                            ((rev_latest / rev_3y) ** (1 / 3) - 1) * 100, 2
                        )
                except Exception:
                    pass

            # Find same-period row from prior year for YoY F-Score comparison
            latest_date = latest.get("日期")
            if latest_date is not None:
                import datetime
                if isinstance(latest_date, str):
                    latest_date = datetime.date.fromisoformat(latest_date[:10])
                target_date = latest_date.replace(year=latest_date.year - 1)
                # Find closest match (same quarter, prior year)
                for i in range(1, len(df_ind)):
                    row_date = df_ind.iloc[i].get("日期")
                    if row_date is not None:
                        if isinstance(row_date, str):
                            row_date = datetime.date.fromisoformat(str(row_date)[:10])
                        # Match same month (same quarter end)
                        if (hasattr(row_date, 'month') and hasattr(target_date, 'month')
                                and row_date.month == target_date.month
                                and row_date.year == target_date.year):
                            prev = df_ind.iloc[i]
                            prev_gm = _safe_float(prev.get("销售毛利率(%)")) or _safe_float(prev.get("主营业务利润率(%)"))
                            snap.gross_margin_prev = prev_gm
                            _prev_year_data = {
                                "roa": _safe_float(prev.get("总资产净利润率(%)")),
                                "debt_ratio": _safe_float(prev.get("资产负债率(%)")),
                                "current_ratio": _safe_float(prev.get("流动比率")),
                                "gross_margin": prev_gm,
                                "asset_turnover": _safe_float(prev.get("总资产周转率(次)")),
                            }
                            break
    except Exception as e:
        print(f"[financial] indicator error for {code}: {e}", file=sys.stderr)

    # ── Balance sheet (Sina) ──
    _share_capital_curr: Optional[float] = None
    _share_capital_prev: Optional[float] = None
    try:
        df_bs = _retry_call(ak.stock_financial_report_sina, stock=code, symbol="资产负债表")
        if df_bs is not None and not df_bs.empty:
            latest_bs = df_bs.iloc[0] if isinstance(df_bs.iloc[0], pd.Series) else {}
            total_assets = _safe_float_from_series(latest_bs, "资产总计")
            total_liab = _safe_float_from_series(latest_bs, "负债合计")
            current_assets = _safe_float_from_series(latest_bs, "流动资产合计")
            current_liab = _safe_float_from_series(latest_bs, "流动负债合计")
            _share_capital_curr = _safe_float_from_series(latest_bs, "实收资本(或股本)")

            if total_assets and total_liab:
                snap.debt_ratio = round(total_liab / total_assets * 100, 2)
            if current_assets and current_liab and current_liab > 0:
                snap.current_ratio = snap.current_ratio or round(current_assets / current_liab, 2)

            # Check prior year share capital for dilution signal
            if len(df_bs) >= 2:
                prev_bs = df_bs.iloc[1]
                _share_capital_prev = _safe_float_from_series(prev_bs, "实收资本(或股本)")
    except Exception as e:
        print(f"[financial] balance sheet error for {code}: {e}", file=sys.stderr)

    # ── Cash flow (Sina) ──
    try:
        df_cf = _retry_call(ak.stock_financial_report_sina, stock=code, symbol="现金流量表")
        if df_cf is not None and not df_cf.empty:
            latest_cf = df_cf.iloc[0]
            ocf = _safe_float_from_series(latest_cf, "经营活动产生的现金流量净额")
            cex = _safe_float_from_series(latest_cf, "购建固定资产、无形资产和其他长期资产支付的现金")
            snap.operating_cf = ocf
            if ocf is not None and cex is not None:
                snap.free_cash_flow = ocf - abs(cex)
    except Exception as e:
        print(f"[financial] cash flow error for {code}: {e}", file=sys.stderr)

    # ── Xueqiu fallback for missing PE/PB/market-cap ──
    # Sina-computed PE requires EPS>0; Xueqiu provides pre-computed values
    # (including negative PE) directly from market data.
    # Also fills total_market_val_yi when Eastmoney stock_individual_info_em
    # was rate-limited.
    if snap.pe is None or snap.pb is None or snap.total_market_val_yi is None:
        try:
            _xq_sym = _code_to_xq_symbol(code)
            df_xq = _retry_call(ak.stock_individual_spot_xq, symbol=_xq_sym)
            if df_xq is not None and not df_xq.empty:
                _xq_map = {
                    "市盈率(动)": "pe",
                    "市盈率(TTM)": "pe_ttm",
                    "市净率": "pb",
                    "每股收益": "eps",
                    "每股净资产": "bvps",
                }
                for _, row in df_xq.iterrows():
                    item = str(row.get("item", ""))
                    value = row.get("value")
                    if item in _xq_map and value is not None:
                        fval = _safe_float(value)
                        if fval is not None:
                            key = _xq_map[item]
                            # Only fill missing fields, don't overwrite
                            if getattr(snap, key, None) is None:
                                setattr(snap, key, round(fval, 2))
                    # Extract total market cap from Xueqiu (资产净值/总市值)
                    if item == "资产净值/总市值" and snap.total_market_val_yi is None:
                        mval = _safe_float(value)
                        if mval and mval > 0:
                            snap.total_market_val_yi = round(mval / 1e8, 2)
                print(f"[financial] Xueqiu fallback for {code}: pe={snap.pe}, pb={snap.pb}, mktcap={snap.total_market_val_yi}亿", file=sys.stderr)
        except Exception as e:
            print(f"[financial] Xueqiu fallback error for {code}: {e}", file=sys.stderr)

    # Compute full 9-signal Piotroski F-Score with YoY comparison
    _compute_fscore_cn(snap, _prev_year_data, _share_capital_curr, _share_capital_prev)

    return snap


def _safe_float(val: Any) -> Optional[float]:
    """Convert to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN check
            return None
        return f
    except (ValueError, TypeError):
        return None


def _safe_float_from_series(series: Any, key: str) -> Optional[float]:
    """Extract float from a pandas Series by key name."""
    try:
        val = series.get(key) if hasattr(series, "get") else None
        if val is None:
            # Try matching column names that contain the key
            for k in series.index if hasattr(series, "index") else []:
                if key in str(k):
                    val = series[k]
                    break
        return _safe_float(val)
    except Exception:
        return None


def _compute_fscore_cn(
    snap: FinancialSnapshot,
    prev_year: dict[str, Any],
    share_capital_curr: Optional[float] = None,
    share_capital_prev: Optional[float] = None,
) -> None:
    """Compute full 9-signal Piotroski F-Score for A-share stocks.

    Uses same-period YoY comparison from indicator data:
      Profitability (4 signals):
        1. ROA > 0
        2. CFO > 0
        3. ΔROA > 0 (ROA improving YoY)
        4. CFO > NI (accruals quality, via ocf_to_net_profit ratio)
      Leverage/Liquidity (3 signals):
        5. Δ杠杆下降 (debt ratio decreasing YoY)
        6. Δ流动比率提升 (current ratio increasing YoY)
        7. 未增发 (no new share issuance)
      Efficiency (2 signals):
        8. Δ毛利率提升 (gross margin increasing YoY)
        9. Δ资产周转率提升 (asset turnover increasing YoY)

    Args:
        snap: Current financial snapshot
        prev_year: Dict with same-period prior year values for YoY comparison
        share_capital_curr: Current period share capital (实收资本)
        share_capital_prev: Prior period share capital
    """
    score = 0
    details: dict[str, bool] = {}

    # ── Profitability (4 signals) ──

    # 1. ROA > 0
    if snap.roa is not None:
        val = snap.roa > 0
        details["positive_roa"] = val
        score += int(val)

    # 2. Operating CF > 0
    if snap.operating_cf is not None:
        val = snap.operating_cf > 0
        details["positive_ocf"] = val
        score += int(val)

    # 3. ΔROA > 0 (ROA improving YoY)
    prev_roa = prev_year.get("roa")
    if snap.roa is not None and prev_roa is not None:
        val = snap.roa > prev_roa
        details["roa_increasing"] = val
        score += int(val)
    elif snap.net_profit_growth_yoy is not None:
        # Fallback: net profit growing → ROA likely improving
        val = snap.net_profit_growth_yoy > 0
        details["roa_increasing"] = val
        score += int(val)

    # 4. CFO > NI (accruals quality)
    if snap.ocf_to_net_profit is not None:
        # ocf_to_net_profit is the ratio; values from akshare can be >1 (decimal)
        # or >100 (percentage). We detect by magnitude.
        threshold = 1.0 if abs(snap.ocf_to_net_profit) < 10 else 100.0
        val = snap.ocf_to_net_profit > threshold
        details["ocf_quality"] = val
        score += int(val)

    # ── Leverage / Liquidity (3 signals) ──

    # 5. Debt ratio decreasing YoY
    prev_debt = prev_year.get("debt_ratio")
    if snap.debt_ratio is not None and prev_debt is not None:
        val = snap.debt_ratio < prev_debt
        details["leverage_decreasing"] = val
        score += int(val)
    elif snap.debt_ratio is not None:
        # No prior data → use absolute threshold as fallback
        val = snap.debt_ratio < 60
        details["leverage_decreasing"] = val
        score += int(val)

    # 6. Current ratio increasing YoY
    prev_cr = prev_year.get("current_ratio")
    if snap.current_ratio is not None and prev_cr is not None:
        val = snap.current_ratio > prev_cr
        details["current_ratio_improving"] = val
        score += int(val)
    elif snap.current_ratio is not None:
        val = snap.current_ratio > 1.0
        details["current_ratio_improving"] = val
        score += int(val)

    # 7. No new share issuance (股本未增加)
    if share_capital_curr is not None and share_capital_prev is not None:
        # Allow tiny rounding difference (< 0.1%)
        val = share_capital_curr <= share_capital_prev * 1.001
        details["no_dilution"] = val
        score += int(val)
    else:
        # No data → assume no dilution (benefit of doubt)
        details["no_dilution"] = True
        score += 1

    # ── Efficiency (2 signals) ──

    # 8. Gross margin increasing YoY
    prev_gm = prev_year.get("gross_margin")
    if snap.gross_margin is not None and prev_gm is not None:
        val = snap.gross_margin > prev_gm
        details["gross_margin_improving"] = val
        score += int(val)
    elif snap.gross_margin is not None:
        val = snap.gross_margin > 0
        details["gross_margin_improving"] = val
        score += int(val)

    # 9. Asset turnover increasing YoY
    prev_at = prev_year.get("asset_turnover")
    if snap.asset_turnover is not None and prev_at is not None:
        val = snap.asset_turnover > prev_at
        details["asset_turnover_improving"] = val
        score += int(val)
    elif snap.revenue_growth_yoy is not None:
        # Revenue growing faster than assets → turnover likely improving
        val = snap.revenue_growth_yoy > 0
        details["asset_turnover_improving"] = val
        score += int(val)

    snap.f_score = score
    snap.f_score_details = details


# ── US/HK/Global Financial Data (yfinance) ──────────────────────


def _fetch_us_financials(symbol: str, market: str = "us_stock") -> FinancialSnapshot:
    """Fetch financial data via yfinance for US/HK/global stocks."""
    import yfinance as yf

    from .provider_factory import to_yfinance_symbol

    snap = FinancialSnapshot(code=symbol, market=market)
    yf_sym = to_yfinance_symbol(symbol, market)

    try:
        ticker = yf.Ticker(yf_sym)

        # Market data (PE, PB, price, market cap)
        try:
            info = ticker.fast_info
            snap.price = _safe_float(info.last_price) if hasattr(info, 'last_price') else None
            mkt_cap = _safe_float(info.market_cap) if hasattr(info, 'market_cap') else None
            if mkt_cap:
                snap.total_market_val_yi = round(mkt_cap / 1e8, 2)
            # Get PE/PB and TTM ratios from full info (Yahoo provides annualized)
            full_info = ticker.info
            snap.pe = _safe_float(full_info.get('trailingPE'))
            snap.pe_ttm = snap.pe
            snap.pb = _safe_float(full_info.get('priceToBook'))
            snap.eps = _safe_float(full_info.get('trailingEps'))
            snap.name = full_info.get('shortName') or full_info.get('longName') or symbol

            # Use Yahoo's TTM ratios (annualized, much more accurate than single-quarter)
            roe_ttm = _safe_float(full_info.get('returnOnEquity'))
            if roe_ttm is not None:
                snap.roe = round(roe_ttm * 100, 2)  # Convert from decimal
            roa_ttm = _safe_float(full_info.get('returnOnAssets'))
            if roa_ttm is not None:
                snap.roa = round(roa_ttm * 100, 2)

            # Yahoo TTM margins
            gm_ttm = _safe_float(full_info.get('grossMargins'))
            if gm_ttm is not None:
                snap.gross_margin = round(gm_ttm * 100, 2)
            om_ttm = _safe_float(full_info.get('operatingMargins'))
            if om_ttm is not None:
                snap.operating_margin = round(om_ttm * 100, 2)
            nm_ttm = _safe_float(full_info.get('profitMargins'))
            if nm_ttm is not None:
                snap.net_margin = round(nm_ttm * 100, 2)

            # Yahoo growth rates (quarterly YoY)
            eg = _safe_float(full_info.get('earningsGrowth'))
            if eg is not None:
                snap.eps_growth_yoy = round(eg * 100, 2)
            rg = _safe_float(full_info.get('revenueGrowth'))
            if rg is not None:
                snap.revenue_growth_yoy = round(rg * 100, 2)
        except Exception as e:
            print(f"[financial] fast_info error for {symbol}: {e}", file=sys.stderr)

        # Income statement (quarterly) — compute TTM by summing last 4 quarters
        inc_q = ticker.quarterly_financials
        ttm_revenue = None
        ttm_net_income = None
        ttm_ebit = None
        if inc_q is not None and not inc_q.empty:
            # Columns are dates (most recent first), rows are line items
            latest_col = inc_q.columns[0]
            snap.latest_quarter = str(latest_col.date()) if hasattr(latest_col, "date") else str(latest_col)[:10]

            # Sum last 4 quarters for TTM
            n_cols = min(4, len(inc_q.columns))
            ttm_cols = inc_q.columns[:n_cols]

            def _sum_ttm(label: str) -> Optional[float]:
                if label not in inc_q.index:
                    return None
                total = 0.0
                count = 0
                for c in ttm_cols:
                    v = _safe_float(inc_q.loc[label, c])
                    if v is not None:
                        total += v
                        count += 1
                return total if count >= 3 else None  # Need at least 3 quarters

            ttm_revenue = _sum_ttm("Total Revenue")
            ttm_net_income = _sum_ttm("Net Income")
            ttm_ebit = _sum_ttm("EBIT") or _sum_ttm("Operating Income")
            ttm_ebitda = _sum_ttm("EBITDA")
            ttm_gross_profit = _sum_ttm("Gross Profit")

            snap.ebit = ttm_ebit
            snap.ebitda = ttm_ebitda

            # TTM margins (fallback — only if Yahoo info didn't provide them)
            if ttm_revenue and ttm_revenue > 0:
                if snap.gross_margin is None and ttm_gross_profit:
                    snap.gross_margin = round(ttm_gross_profit / ttm_revenue * 100, 2)
                if snap.operating_margin is None and ttm_ebit:
                    snap.operating_margin = round(ttm_ebit / ttm_revenue * 100, 2)
                if snap.net_margin is None and ttm_net_income:
                    snap.net_margin = round(ttm_net_income / ttm_revenue * 100, 2)

            # YoY growth (latest Q vs same Q last year) — fallback
            if snap.revenue_growth_yoy is None:
                prev_col = inc_q.columns[4] if len(inc_q.columns) > 4 else None
                if prev_col is not None:
                    revenue = _safe_float(inc_q.loc["Total Revenue", latest_col]) if "Total Revenue" in inc_q.index else None
                    rev_prev = _safe_float(inc_q.loc["Total Revenue", prev_col]) if "Total Revenue" in inc_q.index else None
                    if revenue and rev_prev and rev_prev > 0:
                        snap.revenue_growth_yoy = round((revenue - rev_prev) / abs(rev_prev) * 100, 2)

            if snap.net_profit_growth_yoy is None:
                prev_col = inc_q.columns[4] if len(inc_q.columns) > 4 else None
                if prev_col is not None:
                    net_income = _safe_float(inc_q.loc["Net Income", latest_col]) if "Net Income" in inc_q.index else None
                    ni_prev = _safe_float(inc_q.loc["Net Income", prev_col]) if "Net Income" in inc_q.index else None
                    if net_income and ni_prev and ni_prev > 0:
                        snap.net_profit_growth_yoy = round((net_income - ni_prev) / abs(ni_prev) * 100, 2)

            if snap.eps_growth_yoy is None:
                snap.eps_growth_yoy = snap.net_profit_growth_yoy

            # 3-year revenue CAGR from annual data
            try:
                inc_a = ticker.financials
                if inc_a is not None and len(inc_a.columns) >= 4 and "Total Revenue" in inc_a.index:
                    rev_latest = _safe_float(inc_a.loc["Total Revenue", inc_a.columns[0]])
                    rev_3y = _safe_float(inc_a.loc["Total Revenue", inc_a.columns[3]])
                    if rev_latest and rev_3y and rev_3y > 0:
                        snap.revenue_growth_3y_cagr = round(((rev_latest / rev_3y) ** (1 / 3) - 1) * 100, 2)
            except Exception:
                pass

        # Balance sheet
        bs = ticker.quarterly_balance_sheet
        if bs is not None and not bs.empty:
            col = bs.columns[0]
            total_assets = _yf_get(bs, "Total Assets", col)
            total_liab = _yf_get(bs, "Total Liabilities Net Minority Interest", col) or _yf_get(bs, "Total Liab", col)
            total_equity = _yf_get(bs, "Stockholders Equity", col) or _yf_get(bs, "Total Stockholder Equity", col)
            current_assets = _yf_get(bs, "Current Assets", col)
            current_liab = _yf_get(bs, "Current Liabilities", col)
            cash = _yf_get(bs, "Cash And Cash Equivalents", col) or _yf_get(bs, "Cash", col)

            if total_assets and total_liab:
                snap.debt_ratio = round(total_liab / total_assets * 100, 2)
            # ROE/ROA: use TTM net income (from Yahoo info preferred, balance sheet as fallback)
            if snap.roe is None and total_equity and ttm_net_income and total_equity > 0:
                snap.roe = round(ttm_net_income / total_equity * 100, 2)
            if snap.roa is None and total_assets and ttm_net_income:
                snap.roa = round(ttm_net_income / total_assets * 100, 2)
            if current_assets and current_liab and current_liab > 0:
                snap.current_ratio = round(current_assets / current_liab, 2)
            if total_equity and total_liab:
                snap.debt_to_equity = round(total_liab / total_equity * 100, 2) if total_equity > 0 else None

            # Enterprise value = market cap + debt - cash
            try:
                info = ticker.fast_info
                mkt_cap = float(info.market_cap) if hasattr(info, "market_cap") else None
                if mkt_cap and total_liab and cash:
                    snap.enterprise_value = mkt_cap + total_liab - cash
                    # Use TTM EBIT for EV/EBIT
                    ebit_for_ev = ttm_ebit or snap.ebit
                    if ebit_for_ev and ebit_for_ev > 0:
                        snap.ev_to_ebit = round(snap.enterprise_value / ebit_for_ev, 2)
            except Exception:
                pass

            # ROIC = NOPAT / (equity + debt - cash) using TTM EBIT
            ebit_for_roic = ttm_ebit or snap.ebit
            if ebit_for_roic and total_equity and total_liab:
                tax_rate = 0.21  # US corporate tax rate approximation
                nopat = ebit_for_roic * (1 - tax_rate)
                invested = total_equity + total_liab - (cash or 0)
                if invested > 0:
                    snap.roic = round(nopat / invested * 100, 2)

        # Cash flow
        cf = ticker.quarterly_cashflow
        if cf is not None and not cf.empty:
            col = cf.columns[0]
            ocf = _yf_get(cf, "Operating Cash Flow", col) or _yf_get(cf, "Total Cash From Operating Activities", col)
            capex = _yf_get(cf, "Capital Expenditure", col) or _yf_get(cf, "Capital Expenditures", col)
            snap.operating_cf = ocf
            if ocf is not None and capex is not None:
                snap.free_cash_flow = ocf - abs(capex)

            # OCF to net income ratio (use TTM net income)
            ni_for_ocf = ttm_net_income
            if ocf and ni_for_ocf and ni_for_ocf > 0:
                snap.ocf_to_net_profit = round(ocf / ni_for_ocf, 2)

        # F-Score
        _compute_fscore_us(snap)

    except Exception as e:
        print(f"[financial] yfinance error for {symbol}: {e}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)

    return snap


def _yf_get(df: Any, label: str, col: Any) -> Optional[float]:
    """Safely get a value from a yfinance DataFrame."""
    try:
        if label in df.index:
            return _safe_float(df.loc[label, col])
        # Try fuzzy match
        for idx in df.index:
            if label.lower() in str(idx).lower():
                return _safe_float(df.loc[idx, col])
    except Exception:
        pass
    return None


def _compute_fscore_us(snap: FinancialSnapshot) -> None:
    """Compute Piotroski F-Score for US stocks."""
    score = 0
    details: dict[str, bool] = {}

    # 1. ROA > 0
    if snap.roa is not None:
        val = snap.roa > 0
        details["positive_roa"] = val
        score += int(val)

    # 2. Operating CF > 0
    if snap.operating_cf is not None:
        val = snap.operating_cf > 0
        details["positive_ocf"] = val
        score += int(val)

    # 4. OCF > Net profit (accruals quality)
    if snap.ocf_to_net_profit is not None:
        val = snap.ocf_to_net_profit > 1.0
        details["ocf_quality"] = val
        score += int(val)

    # 5. Low leverage
    if snap.debt_ratio is not None:
        val = snap.debt_ratio < 50
        details["low_leverage"] = val
        score += int(val)

    # 6. Current ratio > 1
    if snap.current_ratio is not None:
        val = snap.current_ratio > 1.0
        details["current_ratio_ok"] = val
        score += int(val)

    # 8. Gross margin positive and healthy
    if snap.gross_margin is not None:
        val = snap.gross_margin > 20
        details["healthy_gross_margin"] = val
        score += int(val)

    # Revenue growth
    if snap.revenue_growth_yoy is not None:
        val = snap.revenue_growth_yoy > 0
        details["revenue_growing"] = val
        score += int(val)

    # ROE positive
    if snap.roe is not None:
        val = snap.roe > 10
        details["strong_roe"] = val
        score += int(val)

    snap.f_score = score
    snap.f_score_details = details


# ── Public API ──────────────────────────────────────────────────


def fetch_financials(code: str, market: str = "") -> FinancialSnapshot:
    """Fetch financial data for a single stock.

    Auto-detects market if not specified. Uses cache when available.

    Args:
        code: Stock code (e.g. "600519", "AAPL")
        market: Market type (cn_stock, us_stock, hk_stock). Auto-detected if empty.

    Returns:
        FinancialSnapshot with available financial metrics.
    """
    from .provider_factory import detect_market

    if not market:
        market = detect_market(code)

    # Check cache first
    cached = _get_cached_financial(code, market)
    if cached is not None and _is_snapshot_valid(cached):
        return cached

    print(f"[financial] fetching: {code} ({market})", file=sys.stderr)

    if market == "cn_stock":
        snap = _fetch_cn_financials(code)
    else:
        snap = _fetch_us_financials(code, market)

    # Only cache if we got meaningful data (avoid poisoning cache with empty results)
    if _is_snapshot_valid(snap):
        _save_cached_financial(snap)
    else:
        print(f"[financial] not caching {code}: insufficient data", file=sys.stderr)
    return snap


def _is_snapshot_valid(snap: FinancialSnapshot) -> bool:
    """Check if a snapshot has enough data to be considered valid/useful.

    A snapshot without any key metrics (ROE, PE, debt_ratio, etc.) is likely
    from a failed API call and should not be cached/trusted.
    """
    has_indicator = snap.roe is not None or snap.roa is not None
    has_valuation = snap.pe is not None or snap.pb is not None
    has_balance = snap.debt_ratio is not None
    return has_indicator or has_valuation or has_balance


def fetch_financials_batch(
    codes: list[str],
    market: str = "",
    max_workers: int = 4,
    chunk_size: int = 20,
    chunk_delay: float = 2.0,
) -> dict[str, FinancialSnapshot]:
    """Fetch financial data for multiple stocks in parallel with rate limiting.

    Processes stocks in chunks to avoid API rate-limiting on Sina/East Money.
    Each chunk uses ThreadPoolExecutor for concurrency within the chunk,
    with a delay between chunks to let the APIs breathe.

    Args:
        codes: List of stock codes
        market: Market type (auto-detected per code if empty)
        max_workers: Max parallel threads per chunk
        chunk_size: Number of stocks per chunk (default 20)
        chunk_delay: Seconds to wait between chunks (default 2.0)

    Returns:
        Dict of {code: FinancialSnapshot}
    """
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .provider_factory import detect_market

    results: dict[str, FinancialSnapshot] = {}
    to_fetch: list[tuple[str, str]] = []

    for code in codes:
        m = market or detect_market(code)
        cached = _get_cached_financial(code, m)
        if cached is not None:
            results[code] = cached
        else:
            to_fetch.append((code, m))

    if results:
        print(f"[financial] cache hits: {len(results)}, to fetch: {len(to_fetch)}", file=sys.stderr)

    if not to_fetch:
        return results

    def _fetch_one(item: tuple[str, str]) -> tuple[str, FinancialSnapshot]:
        code, m = item
        snap = fetch_financials(code, m)
        return code, snap

    # Split to_fetch into chunks to avoid overwhelming APIs
    total_chunks = (len(to_fetch) + chunk_size - 1) // chunk_size
    for chunk_idx in range(total_chunks):
        chunk = to_fetch[chunk_idx * chunk_size : (chunk_idx + 1) * chunk_size]
        chunk_num = chunk_idx + 1
        print(
            f"[financial] chunk {chunk_num}/{total_chunks}: "
            f"fetching {len(chunk)} stocks...",
            file=sys.stderr,
        )

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_one, item): item[0] for item in chunk}
            for future in as_completed(futures):
                try:
                    code, snap = future.result()
                    results[code] = snap
                except Exception as e:
                    code = futures[future]
                    print(f"[financial] error fetching {code}: {e}", file=sys.stderr)
                    results[code] = FinancialSnapshot(code=code)

        # Delay between chunks to avoid rate limiting (skip after last chunk)
        if chunk_idx < total_chunks - 1:
            time.sleep(chunk_delay)

    return results


def financial_to_dict(snap: FinancialSnapshot) -> dict[str, Any]:
    """Convert FinancialSnapshot to a flat dict for screening pipelines."""
    result: dict[str, Any] = {}
    for k, v in asdict(snap).items():
        if k == "quarterly_data":
            continue  # Skip raw quarterly data in flat dict
        if v is not None:
            result[k] = v
    return result


# ── CLI Entry ───────────────────────────────────────────────────

if __name__ == "__main__":
    """CLI entry: reads JSON from stdin, outputs JSON to stdout.

    Input: {"action": "fetch", "codes": ["600519", "AAPL"], "market": ""}
    Output: {"results": {code: {financial snapshot dict}}, "count": N}
    """
    config = json.loads(sys.stdin.read())
    action = config.get("action", "fetch")

    if action == "fetch":
        codes = config.get("codes", [])
        market = config.get("market", "")
        if not codes:
            print(json.dumps({"error": "No codes provided"}, ensure_ascii=False))
            sys.exit(1)

        results = fetch_financials_batch(codes, market)
        output = {
            "results": {code: financial_to_dict(snap) for code, snap in results.items()},
            "count": len(results),
        }
        print(json.dumps(output, ensure_ascii=False, default=str))
    else:
        print(json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False))
