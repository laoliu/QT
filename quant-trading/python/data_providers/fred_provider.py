#!/usr/bin/env python3
"""
FRED (Federal Reserve Economic Data) provider bridge.

Fetches US macroeconomic time series from the FRED API via the fredapi
library.  Supports popular series like GDP, CPI, unemployment, Fed
Funds Rate, Treasury yields, and arbitrary user-specified series IDs.

Input: JSON via QUANT_CONFIG env var or stdin.
Output: JSON to stdout.

Requirements:
  pip install fredapi pandas
"""

import json
import os
import sys
from datetime import datetime, timedelta


def get_input():
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        return json.loads(config_str)
    return json.loads(sys.stdin.read())


# ── Well-known FRED series ───────────────────────────────────────────────────

POPULAR_SERIES = {
    # Interest rates
    "fed_rate": {"series_id": "FEDFUNDS", "name": "Federal Funds Effective Rate", "frequency": "monthly", "unit": "%"},
    "fed_rate_upper": {"series_id": "DFEDTARU", "name": "Fed Funds Target Rate Upper", "frequency": "daily", "unit": "%"},
    "fed_rate_lower": {"series_id": "DFEDTARL", "name": "Fed Funds Target Rate Lower", "frequency": "daily", "unit": "%"},
    "treasury_10y": {"series_id": "DGS10", "name": "10-Year Treasury Yield", "frequency": "daily", "unit": "%"},
    "treasury_2y": {"series_id": "DGS2", "name": "2-Year Treasury Yield", "frequency": "daily", "unit": "%"},
    "treasury_3m": {"series_id": "DGS3MO", "name": "3-Month Treasury Yield", "frequency": "daily", "unit": "%"},
    "yield_spread": {"series_id": "T10Y2Y", "name": "10Y-2Y Treasury Spread", "frequency": "daily", "unit": "%"},

    # Inflation
    "us_cpi": {"series_id": "CPIAUCSL", "name": "Consumer Price Index (All Urban)", "frequency": "monthly", "unit": "index"},
    "us_cpi_yoy": {"series_id": "CPIAUCNS", "name": "CPI Year-over-Year", "frequency": "monthly", "unit": "%"},
    "core_cpi": {"series_id": "CPILFESL", "name": "Core CPI (excl. Food & Energy)", "frequency": "monthly", "unit": "index"},
    "pce": {"series_id": "PCEPI", "name": "PCE Price Index", "frequency": "monthly", "unit": "index"},
    "core_pce": {"series_id": "PCEPILFE", "name": "Core PCE Price Index", "frequency": "monthly", "unit": "index"},
    "breakeven_10y": {"series_id": "T10YIE", "name": "10-Year Breakeven Inflation", "frequency": "daily", "unit": "%"},

    # Employment
    "us_unemployment": {"series_id": "UNRATE", "name": "Unemployment Rate", "frequency": "monthly", "unit": "%"},
    "nonfarm_payroll": {"series_id": "PAYEMS", "name": "Non-Farm Payrolls", "frequency": "monthly", "unit": "thousands"},
    "initial_claims": {"series_id": "ICSA", "name": "Initial Jobless Claims", "frequency": "weekly", "unit": "count"},

    # GDP & Output
    "us_gdp": {"series_id": "GDP", "name": "Gross Domestic Product", "frequency": "quarterly", "unit": "billions USD"},
    "real_gdp": {"series_id": "GDPC1", "name": "Real GDP", "frequency": "quarterly", "unit": "billions 2017 USD"},
    "gdp_growth": {"series_id": "A191RL1Q225SBEA", "name": "Real GDP Growth Rate", "frequency": "quarterly", "unit": "%"},
    "industrial_production": {"series_id": "INDPRO", "name": "Industrial Production Index", "frequency": "monthly", "unit": "index"},

    # Manufacturing & Business
    "us_pmi": {"series_id": "MANEMP", "name": "Manufacturing Employment", "frequency": "monthly", "unit": "thousands"},
    "retail_sales": {"series_id": "RSXFS", "name": "Retail Sales (ex Food Services)", "frequency": "monthly", "unit": "millions USD"},
    "consumer_sentiment": {"series_id": "UMCSENT", "name": "U. of Michigan Consumer Sentiment", "frequency": "monthly", "unit": "index"},

    # Money supply
    "m2": {"series_id": "M2SL", "name": "M2 Money Supply", "frequency": "monthly", "unit": "billions USD"},

    # Housing
    "housing_starts": {"series_id": "HOUST", "name": "Housing Starts", "frequency": "monthly", "unit": "thousands"},
    "case_shiller": {"series_id": "CSUSHPINSA", "name": "S&P/Case-Shiller Home Price Index", "frequency": "monthly", "unit": "index"},

    # Market indicators
    "vix": {"series_id": "VIXCLS", "name": "CBOE VIX", "frequency": "daily", "unit": "index"},
    "sp500": {"series_id": "SP500", "name": "S&P 500 Index", "frequency": "daily", "unit": "index"},
}


def _get_fred(api_key: str):
    """Initialize FRED API client."""
    from fredapi import Fred
    return Fred(api_key=api_key)


def list_indicators() -> dict:
    """Return catalog of well-known FRED series."""
    indicators = []
    for key, meta in POPULAR_SERIES.items():
        indicators.append({
            "id": key,
            "seriesId": meta["series_id"],
            "name": meta["name"],
            "frequency": meta["frequency"],
            "unit": meta["unit"],
        })
    return {
        "count": len(indicators),
        "indicators": indicators,
    }


def get_series(series_id: str, api_key: str, limit: int = 60,
               start_date: str = "", end_date: str = "") -> dict:
    """Fetch a FRED time series by series ID (e.g. 'FEDFUNDS', 'UNRATE').

    Also accepts well-known aliases (e.g. 'fed_rate', 'us_cpi').
    """
    if not api_key:
        return {"error": "FRED API key not configured. Get one free at https://fred.stlouisfed.org/docs/api/"}

    # Resolve alias
    meta = POPULAR_SERIES.get(series_id.lower())
    if meta:
        resolved_id = meta["series_id"]
        name = meta["name"]
        unit = meta["unit"]
        frequency = meta["frequency"]
    else:
        resolved_id = series_id.upper()
        name = resolved_id
        unit = ""
        frequency = ""

    fred = _get_fred(api_key)

    try:
        # Parse dates
        obs_start = start_date if start_date else None
        obs_end = end_date if end_date else None

        series = fred.get_series(resolved_id, observation_start=obs_start, observation_end=obs_end)

        if series is None or series.empty:
            return {"error": f"No data for series {resolved_id}"}

        # If no start/end specified, take last N observations
        if not start_date and not end_date:
            series = series.tail(limit)

        # Get series info for better metadata
        if not meta:
            try:
                info = fred.get_series_info(resolved_id)
                if info is not None:
                    name = info.get("title", resolved_id)
                    unit = info.get("units", "")
                    frequency = info.get("frequency", "")
            except Exception:
                pass

        data = []
        for date, value in series.items():
            if value is not None and value == value:  # skip NaN
                data.append({
                    "date": date.strftime("%Y-%m-%d"),
                    "value": round(float(value), 4),
                })

        return {
            "seriesId": resolved_id,
            "name": name,
            "frequency": frequency,
            "unit": unit,
            "count": len(data),
            "data": data,
        }

    except Exception as e:
        print(f"[fred] Error fetching series {resolved_id}: {e}", file=sys.stderr)
        return {"error": str(e)}


def get_multi_series(series_ids: list[str], api_key: str, limit: int = 60) -> dict:
    """Fetch multiple FRED series at once."""
    if not api_key:
        return {"error": "FRED API key not configured"}

    results = {}
    for sid in series_ids:
        results[sid] = get_series(sid, api_key, limit)

    return {
        "count": len(results),
        "series": results,
    }


def search_series(keyword: str, api_key: str, limit: int = 20) -> dict:
    """Search FRED series by keyword."""
    if not api_key:
        return {"error": "FRED API key not configured"}

    fred = _get_fred(api_key)

    try:
        df = fred.search(keyword)

        if df is None or df.empty:
            return {"count": 0, "results": []}

        # Sort by popularity (highest first)
        if "popularity" in df.columns:
            df = df.sort_values("popularity", ascending=False)

        results = []
        for series_id, row in df.head(limit).iterrows():
            results.append({
                "seriesId": str(series_id),
                "name": str(row.get("title", "")),
                "frequency": str(row.get("frequency", "")),
                "unit": str(row.get("units", "")),
                "seasonalAdj": str(row.get("seasonal_adjustment", "")),
                "popularity": int(row.get("popularity", 0)) if "popularity" in row else 0,
                "lastUpdated": str(row.get("last_updated", ""))[:10],
            })

        return {
            "count": len(results),
            "results": results,
        }

    except Exception as e:
        print(f"[fred] Error searching: {e}", file=sys.stderr)
        return {"error": str(e)}


def get_macro_dashboard(api_key: str) -> dict:
    """Fetch a dashboard of key US macro indicators (latest values)."""
    if not api_key:
        return {"error": "FRED API key not configured"}

    fred = _get_fred(api_key)

    dashboard_series = [
        "fed_rate", "treasury_10y", "treasury_2y", "yield_spread",
        "us_cpi", "us_unemployment", "gdp_growth",
        "nonfarm_payroll", "consumer_sentiment", "vix",
    ]

    indicators = []
    for key in dashboard_series:
        meta = POPULAR_SERIES[key]
        try:
            series = fred.get_series(meta["series_id"])
            if series is not None and not series.empty:
                # Last non-NaN value
                latest = series.dropna().iloc[-1]
                latest_date = series.dropna().index[-1]

                # Compute change from previous
                prev = None
                if len(series.dropna()) > 1:
                    prev = float(series.dropna().iloc[-2])

                indicators.append({
                    "id": key,
                    "name": meta["name"],
                    "value": round(float(latest), 4),
                    "unit": meta["unit"],
                    "date": latest_date.strftime("%Y-%m-%d"),
                    "previous": round(prev, 4) if prev is not None else None,
                    "change": round(float(latest) - prev, 4) if prev is not None else None,
                })
        except Exception as e:
            print(f"[fred] Dashboard skip {key}: {e}", file=sys.stderr)
            continue

    return {
        "count": len(indicators),
        "indicators": indicators,
    }


if __name__ == "__main__":
    config = get_input()
    action = config.get("action")
    api_key = config.get("apiKey", "") or os.environ.get("FRED_API_KEY", "")

    if action == "indicators":
        result = list_indicators()
    elif action == "series":
        result = get_series(
            config.get("seriesId", config.get("indicator", "")),
            api_key,
            config.get("limit", 60),
            config.get("start", ""),
            config.get("end", ""),
        )
    elif action == "multi_series":
        result = get_multi_series(
            config.get("seriesIds", []),
            api_key,
            config.get("limit", 60),
        )
    elif action == "search":
        result = search_series(
            config.get("keyword", ""),
            api_key,
            config.get("limit", 20),
        )
    elif action == "dashboard":
        result = get_macro_dashboard(api_key)
    else:
        result = {"error": f"Unknown action: {action}"}

    print(json.dumps(result, ensure_ascii=False))
