"""
earnings_calendar — Earnings dates, analyst forecasts, and consensus data.

Fetches A-share earnings disclosure schedules, profit forecasts, analyst
ratings from akshare + tushare; US stock earnings from yfinance.

Input (JSON via stdin / QUANT_CONFIG):
  {
    "action": "upcoming" | "stock_forecast" | "surprise_history" | "analyst_ratings",
    "symbol": "600519",
    "market": "cn" | "us",
    "days": 30,
    "limit": 20
  }

Output: JSON with earnings/analyst data.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

# ── Proxy bypass ──────────────────────────────────────────────────────────────
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def run_earnings_calendar(config: dict) -> dict:
    """Dispatch earnings calendar actions."""
    action = config.get("action", "upcoming")
    market = config.get("market", "cn")

    if action == "upcoming":
        days = config.get("days", 30)
        limit = config.get("limit", 30)
        return _upcoming_earnings(market, days, limit)
    elif action == "stock_forecast":
        symbol = config.get("symbol", "")
        if not symbol:
            return {"error": "symbol is required for stock_forecast"}
        return _stock_forecast(symbol, market)
    elif action == "surprise_history":
        symbol = config.get("symbol", "")
        if not symbol:
            return {"error": "symbol is required for surprise_history"}
        return _surprise_history(symbol, market, config.get("limit", 10))
    elif action == "analyst_ratings":
        symbol = config.get("symbol", "")
        if not symbol:
            return {"error": "symbol is required for analyst_ratings"}
        return _analyst_ratings(symbol, market, config.get("limit", 20))
    else:
        return {"error": f"δ֪ action: {action}"}


# ── Upcoming earnings ─────────────────────────────────────────────────────────

def _upcoming_earnings(market: str, days: int, limit: int) -> dict:
    """Get upcoming earnings announcements within N days."""
    if market == "us":
        return _upcoming_earnings_us(days, limit)

    # China A-shares
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    earnings: list[dict] = []

    try:
        # A-share report disclosure schedule
        today = datetime.now()
        current_year = today.year
        # Try current quarter schedule
        for date_str in [f"{current_year}0331", f"{current_year}0630",
                         f"{current_year}0930", f"{current_year}1231"]:
            try:
                df = ak.stock_report_disclosure(date=date_str)
                if df is not None and not df.empty:
                    deadline = today + timedelta(days=days)
                    for _, row in df.iterrows():
                        disc_date_raw = str(row.get("首次披露日期",
                                                     row.get("实际披露日期",
                                                              row.get("披露日期", ""))))[:10]
                        if not disc_date_raw or disc_date_raw == "None":
                            continue
                        try:
                            disc_date = datetime.strptime(disc_date_raw, "%Y-%m-%d")
                        except ValueError:
                            continue
                        if today <= disc_date <= deadline:
                            earnings.append({
                                "symbol": str(row.get("股票代码", row.get("代码", ""))),
                                "name": str(row.get("股票简称", row.get("名称", ""))),
                                "disclosure_date": disc_date_raw,
                                "report_period": date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:],
                            })
                    if earnings:
                        break
            except Exception:
                continue
    except Exception as e:
        return {"error": f"获取财报日历失败: {e}"}

    # Sort by disclosure date
    earnings.sort(key=lambda x: x.get("disclosure_date", ""))

    return {
        "market": market,
        "days": days,
        "count": min(len(earnings), limit),
        "earnings": earnings[:limit],
    }


def _upcoming_earnings_us(days: int, limit: int) -> dict:
    """US stock upcoming earnings via yfinance."""
    # yfinance doesn't have a bulk earnings calendar endpoint,
    # so we check a curated list of popular tickers
    popular_us = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "JPM",
        "V", "JNJ", "WMT", "PG", "MA", "HD", "DIS", "PYPL", "NFLX",
        "INTC", "AMD", "CRM", "BA", "GS", "COST", "UBER", "ABNB",
    ]
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance not installed"}

    earnings: list[dict] = []
    today = datetime.now()
    deadline = today + timedelta(days=days)

    for ticker_sym in popular_us:
        try:
            ticker = yf.Ticker(ticker_sym)
            cal = ticker.calendar
            if cal is not None and isinstance(cal, dict):
                earn_date = cal.get("Earnings Date")
                if earn_date:
                    # May be a list of dates
                    dates = earn_date if isinstance(earn_date, list) else [earn_date]
                    for d in dates:
                        try:
                            if hasattr(d, "date"):
                                d_date = d
                            else:
                                d_date = datetime.strptime(str(d)[:10], "%Y-%m-%d")
                            if today.date() <= d_date.date() <= deadline.date():
                                earnings.append({
                                    "symbol": ticker_sym,
                                    "earnings_date": str(d_date.date()),
                                    "eps_estimate": _safe_float(cal.get("EPS Estimate")),
                                    "revenue_estimate": _safe_float(cal.get("Revenue Estimate")),
                                })
                        except Exception:
                            continue
        except Exception:
            continue

    earnings.sort(key=lambda x: x.get("earnings_date", ""))
    return {
        "market": "us",
        "days": days,
        "count": min(len(earnings), limit),
        "earnings": earnings[:limit],
    }


# ── Stock forecast (consensus estimates) ──────────────────────────────────────

def _stock_forecast(symbol: str, market: str) -> dict:
    """Get analyst consensus forecasts for a stock."""
    if market == "us":
        return _stock_forecast_us(symbol)

    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    forecasts: list[dict] = []

    # Method 1: Profit forecast from Eastmoney
    try:
        df = ak.stock_profit_forecast_em(symbol=symbol)
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                forecasts.append({
                    "institution": str(row.get("机构名称", row.get("研究机构", ""))),
                    "analyst": str(row.get("研究员", "")),
                    "report_date": str(row.get("报告日期", ""))[:10],
                    "year": str(row.get("预测年度", "")),
                    "eps_forecast": _safe_float(row.get("预测每股收益", row.get("预测EPS", None))),
                    "net_profit_forecast": _safe_float(
                        row.get("预测净利润", row.get("预测净利润(亿元)", None))
                    ),
                    "rating": str(row.get("最新评级", row.get("投资评级", ""))),
                })
    except Exception:
        pass

    # Method 2: Analyst consensus target prices
    consensus = {}
    try:
        df2 = ak.stock_analyst_rank_em(symbol=symbol)
        if df2 is not None and not df2.empty:
            row = df2.iloc[0]
            consensus = {
                "buy_count": int(row.get("买入", 0)) if row.get("买入") else 0,
                "overweight_count": int(row.get("增持", 0)) if row.get("增持") else 0,
                "hold_count": int(row.get("中性", 0)) if row.get("中性") else 0,
                "sell_count": int(row.get("减持", 0)) if row.get("减持") else 0,
            }
    except Exception:
        pass

    return {
        "symbol": symbol,
        "market": market,
        "forecast_count": len(forecasts),
        "forecasts": forecasts[:20],
        "consensus": consensus,
    }


def _stock_forecast_us(symbol: str) -> dict:
    """US stock analyst consensus via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance not installed"}

    try:
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        # Recommendations
        recs: list[dict] = []
        try:
            rec_df = ticker.recommendations
            if rec_df is not None and not rec_df.empty:
                for _, row in rec_df.tail(10).iterrows():
                    recs.append({
                        "date": str(row.name)[:10] if hasattr(row, "name") else str(row.get("Date", ""))[:10],
                        "firm": str(row.get("Firm", "")),
                        "grade": str(row.get("To Grade", row.get("toGrade", ""))),
                        "action": str(row.get("Action", row.get("action", ""))),
                    })
        except Exception:
            pass

        return {
            "symbol": symbol,
            "market": "us",
            "target_price_mean": _safe_float(info.get("targetMeanPrice")),
            "target_price_high": _safe_float(info.get("targetHighPrice")),
            "target_price_low": _safe_float(info.get("targetLowPrice")),
            "analyst_count": info.get("numberOfAnalystOpinions"),
            "recommendation": info.get("recommendationKey", ""),
            "eps_trailing": _safe_float(info.get("trailingEps")),
            "eps_forward": _safe_float(info.get("forwardEps")),
            "recent_recommendations": recs,
        }
    except Exception as e:
        return {"error": f"获取 {symbol} 预测数据失败: {e}"}


# ── Surprise history ──────────────────────────────────────────────────────────

def _surprise_history(symbol: str, market: str, limit: int) -> dict:
    """Historical earnings surprise (beat/miss) track record."""
    if market == "us":
        return _surprise_history_us(symbol, limit)

    # A-shares: akshare business performance express (业绩快报)
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    surprises: list[dict] = []
    try:
        # Performance express shows actual vs prior period
        df = ak.stock_yjkb_em(date="")  # latest quarter
        if df is not None and not df.empty:
            stock_rows = df[df["股票代码"].astype(str) == str(symbol)]
            for _, row in stock_rows.tail(limit).iterrows():
                surprises.append({
                    "report_period": str(row.get("报告期", ""))[:10],
                    "eps": _safe_float(row.get("每股收益", None)),
                    "net_profit": _safe_float(row.get("净利润", None)),
                    "net_profit_yoy": _safe_float(row.get("净利润同比", row.get("净利润同比增长", None))),
                    "revenue": _safe_float(row.get("营业收入", None)),
                    "revenue_yoy": _safe_float(row.get("营业收入同比增长", None)),
                })
    except Exception:
        pass

    return {
        "symbol": symbol,
        "market": market,
        "count": len(surprises),
        "history": surprises,
    }


def _surprise_history_us(symbol: str, limit: int) -> dict:
    """US stock earnings surprise via yfinance."""
    try:
        import yfinance as yf
    except ImportError:
        return {"error": "yfinance not installed"}

    surprises: list[dict] = []
    try:
        ticker = yf.Ticker(symbol)
        earnings = ticker.earnings_history
        if earnings is not None and not earnings.empty:
            for _, row in earnings.tail(limit).iterrows():
                surprises.append({
                    "date": str(row.get("Earnings Date", row.name))[:10] if hasattr(row, "name") else "",
                    "eps_estimate": _safe_float(row.get("EPS Estimate")),
                    "eps_actual": _safe_float(row.get("Reported EPS")),
                    "surprise_pct": _safe_float(row.get("Surprise(%)")),
                })
    except Exception:
        pass

    return {
        "symbol": symbol,
        "market": "us",
        "count": len(surprises),
        "history": surprises,
    }


# ── Analyst ratings ───────────────────────────────────────────────────────────

def _analyst_ratings(symbol: str, market: str, limit: int) -> dict:
    """Analyst ratings and price targets."""
    if market == "us":
        return _stock_forecast_us(symbol)  # reuse yfinance recommendations

    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    ratings: list[dict] = []
    try:
        df = ak.stock_profit_forecast_em(symbol=symbol)
        if df is not None and not df.empty:
            for _, row in df.head(limit).iterrows():
                ratings.append({
                    "institution": str(row.get("机构名称", row.get("研究机构", ""))),
                    "analyst": str(row.get("研究员", "")),
                    "date": str(row.get("报告日期", ""))[:10],
                    "rating": str(row.get("最新评级", row.get("投资评级", ""))),
                    "target_price": _safe_float(row.get("目标价", None)),
                    "eps_forecast": _safe_float(row.get("预测每股收益", None)),
                })
    except Exception:
        pass

    # Aggregate rating distribution
    distribution = {"buy": 0, "overweight": 0, "hold": 0, "underweight": 0, "sell": 0}
    for r in ratings:
        grade = r.get("rating", "")
        if "买入" in grade or "强推" in grade:
            distribution["buy"] += 1
        elif "增持" in grade or "推荐" in grade:
            distribution["overweight"] += 1
        elif "中性" in grade or "持有" in grade:
            distribution["hold"] += 1
        elif "减持" in grade:
            distribution["underweight"] += 1
        elif "卖出" in grade:
            distribution["sell"] += 1

    return {
        "symbol": symbol,
        "market": market,
        "count": len(ratings),
        "ratings": ratings,
        "distribution": distribution,
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

        result = run_earnings_calendar(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
