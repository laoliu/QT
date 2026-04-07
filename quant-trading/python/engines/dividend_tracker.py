"""
dividend_tracker — Dividend history, upcoming ex-dates, and portfolio yield tracking.

Fetches A-share dividend/bonus data from akshare.

Input (JSON via stdin / QUANT_CONFIG):
  {
    "action": "upcoming" | "history" | "portfolio" | "calendar",
    "symbols": "600519,000858",
    "symbol": "600519",
    "days": 30,
    "positions": "[{...}]"
  }

Output: JSON with dividend data.
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


def run_dividend_tracker(config: dict) -> dict:
    """Dispatch dividend tracker actions."""
    action = config.get("action", "upcoming")

    if action == "upcoming":
        symbols = config.get("symbols", "").split(",")
        symbols = [s.strip() for s in symbols if s.strip()]
        days = config.get("days", 30)
        return _upcoming_dividends(symbols, days)
    elif action == "history":
        symbol = config.get("symbol", "")
        if not symbol:
            return {"error": "symbol is required for history"}
        return _dividend_history(symbol)
    elif action == "portfolio":
        symbols = config.get("symbols", "").split(",")
        symbols = [s.strip() for s in symbols if s.strip()]
        positions_str = config.get("positions", "[]")
        try:
            positions = json.loads(positions_str) if isinstance(positions_str, str) else positions_str
        except json.JSONDecodeError:
            positions = []
        return _portfolio_dividends(symbols, positions)
    elif action == "calendar":
        days = config.get("days", 30)
        return _dividend_calendar(days)
    else:
        return {"error": f"Unknown action: {action}"}


# ── Upcoming dividends for specific symbols ──────────────────────────────────

def _upcoming_dividends(symbols: list[str], days: int) -> dict:
    """Get upcoming ex-dividend dates for given symbols."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    dividends: list[dict] = []
    today = datetime.now()
    deadline = today + timedelta(days=days)

    for symbol in symbols:
        try:
            # akshare: stock dividend history
            df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
            if df is None or df.empty:
                continue

            for _, row in df.iterrows():
                ex_date_str = str(row.get("除权除息日", ""))
                if not ex_date_str or ex_date_str in ("nan", "None", "--", ""):
                    continue

                try:
                    ex_date = datetime.strptime(ex_date_str[:10], "%Y-%m-%d")
                except (ValueError, TypeError):
                    continue

                # Only include if ex-date is in the future within the window
                if today <= ex_date <= deadline:
                    record = {
                        "symbol": symbol,
                        "exDate": ex_date.strftime("%Y-%m-%d"),
                    }

                    # Parse dividend amount (每股分红)
                    dividend = _safe_float(row.get("每股分红(税前)", row.get("派息(税前)(元)", 0)))
                    if dividend and dividend > 0:
                        record["dividend"] = dividend

                    # Parse bonus shares (每股送股)
                    bonus = _safe_float(row.get("每股送股", row.get("送股(股)", 0)))
                    if bonus and bonus > 0:
                        record["bonus"] = bonus

                    # Parse allotment (配股)
                    allotment = _safe_float(row.get("每股配股", row.get("配股(股)", 0)))
                    if allotment and allotment > 0:
                        record["allotment"] = allotment
                        allot_price = _safe_float(row.get("配股价(元)", 0))
                        if allot_price and allot_price > 0:
                            record["allotmentPrice"] = allot_price

                    # Record date and pay date if available
                    record_date = str(row.get("股权登记日", ""))
                    if record_date and record_date not in ("nan", "None", "--", ""):
                        record["recordDate"] = record_date[:10]

                    pay_date = str(row.get("红股上市日", row.get("派息日", "")))
                    if pay_date and pay_date not in ("nan", "None", "--", ""):
                        record["payDate"] = pay_date[:10]

                    dividends.append(record)

        except Exception:
            # Skip symbols that fail — don't break the whole batch
            continue

    # Sort by ex-date
    dividends.sort(key=lambda x: x.get("exDate", ""))

    return {"dividends": dividends}


# ── Dividend history for a single symbol ─────────────────────────────────────

def _dividend_history(symbol: str) -> dict:
    """Get full dividend history for a symbol."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    try:
        df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
        if df is None or df.empty:
            return {"history": [], "summary": {"totalYears": 0, "avgDividend": 0, "dividendYield": 0, "consecutiveYears": 0}}

        history: list[dict] = []
        yearly_dividends: dict[str, float] = {}

        for _, row in df.iterrows():
            ex_date_str = str(row.get("除权除息日", ""))
            if not ex_date_str or ex_date_str in ("nan", "None", "--", ""):
                continue

            record: dict[str, Any] = {"exDate": ex_date_str[:10]}

            try:
                year = ex_date_str[:4]
                record["year"] = year
            except (ValueError, IndexError):
                pass

            dividend = _safe_float(row.get("每股分红(税前)", row.get("派息(税前)(元)", 0)))
            if dividend and dividend > 0:
                record["dividend"] = dividend
                yr = record.get("year", "")
                if yr:
                    yearly_dividends[yr] = yearly_dividends.get(yr, 0) + dividend

            bonus = _safe_float(row.get("每股送股", row.get("送股(股)", 0)))
            if bonus and bonus > 0:
                record["bonus"] = bonus

            allotment = _safe_float(row.get("每股配股", row.get("配股(股)", 0)))
            if allotment and allotment > 0:
                record["allotment"] = allotment
                allot_price = _safe_float(row.get("配股价(元)", 0))
                if allot_price and allot_price > 0:
                    record["allotmentPrice"] = allot_price

            history.append(record)

        # Sort by date descending (newest first)
        history.sort(key=lambda x: x.get("exDate", ""), reverse=True)

        # Compute summary
        total_years = len(yearly_dividends)
        avg_dividend = sum(yearly_dividends.values()) / total_years if total_years > 0 else 0

        # Count consecutive years with dividends
        current_year = datetime.now().year
        consecutive = 0
        for yr in range(current_year, current_year - 30, -1):
            if str(yr) in yearly_dividends:
                consecutive += 1
            else:
                break

        # Try to get current price for dividend yield
        dividend_yield = 0
        try:
            quote_df = ak.stock_zh_a_spot_em()
            if quote_df is not None and not quote_df.empty:
                match = quote_df[quote_df["代码"] == symbol]
                if not match.empty:
                    price = float(match.iloc[0].get("最新价", 0))
                    if price > 0 and avg_dividend > 0:
                        dividend_yield = round(avg_dividend / price * 100, 2)
        except Exception:
            pass

        return {
            "history": history[:30],  # Limit to last 30 records
            "summary": {
                "totalYears": total_years,
                "avgDividend": round(avg_dividend, 4),
                "dividendYield": dividend_yield,
                "consecutiveYears": consecutive,
            },
        }

    except Exception as e:
        return {"error": f"查询分红历史失败: {str(e)}", "history": []}


# ── Portfolio dividend summary ───────────────────────────────────────────────

def _portfolio_dividends(symbols: list[str], positions: list[dict]) -> dict:
    """Compute dividend summary for portfolio positions."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    position_map = {p["symbol"]: p for p in positions}
    portfolio_dividends: list[dict] = []
    total_annual_dividend = 0
    total_market_value = 0

    for symbol in symbols:
        pos = position_map.get(symbol, {})
        quantity = pos.get("quantity", 0)
        avg_cost = pos.get("avgCost", 0)
        current_price = pos.get("currentPrice", 0)
        market_value = pos.get("marketValue", 0) or (current_price * quantity)

        try:
            df = ak.stock_history_dividend_detail(symbol=symbol, indicator="分红")
            if df is None or df.empty:
                portfolio_dividends.append({
                    "symbol": symbol,
                    "annualDividend": 0,
                    "dividendYield": 0,
                    "yieldOnCost": 0,
                })
                continue

            # Calculate average annual dividend from last 3 years
            yearly_dividends: dict[str, float] = {}
            last_ex_date = None
            next_ex_date = None
            today = datetime.now()

            for _, row in df.iterrows():
                ex_date_str = str(row.get("除权除息日", ""))
                if not ex_date_str or ex_date_str in ("nan", "None", "--", ""):
                    continue

                dividend = _safe_float(row.get("每股分红(税前)", row.get("派息(税前)(元)", 0)))
                if not dividend or dividend <= 0:
                    continue

                year = ex_date_str[:4]
                yearly_dividends[year] = yearly_dividends.get(year, 0) + dividend

                try:
                    ex_date = datetime.strptime(ex_date_str[:10], "%Y-%m-%d")
                    if ex_date <= today:
                        if last_ex_date is None or ex_date_str > last_ex_date:
                            last_ex_date = ex_date_str[:10]
                    else:
                        if next_ex_date is None or ex_date_str < next_ex_date:
                            next_ex_date = ex_date_str[:10]
                except (ValueError, TypeError):
                    pass

            # Use last 3 years average
            recent_years = sorted(yearly_dividends.keys(), reverse=True)[:3]
            if recent_years:
                avg_annual = sum(yearly_dividends[y] for y in recent_years) / len(recent_years)
            else:
                avg_annual = 0

            annual_income = avg_annual * quantity
            total_annual_dividend += annual_income
            total_market_value += market_value

            div_yield = round(avg_annual / current_price * 100, 2) if current_price > 0 else 0
            yield_on_cost = round(avg_annual / avg_cost * 100, 2) if avg_cost > 0 else 0

            portfolio_dividends.append({
                "symbol": symbol,
                "name": pos.get("name"),
                "annualDividend": round(avg_annual, 4),
                "annualIncome": round(annual_income, 2),
                "dividendYield": div_yield,
                "yieldOnCost": yield_on_cost,
                "lastExDate": last_ex_date,
                "nextExDate": next_ex_date,
            })

        except Exception:
            portfolio_dividends.append({
                "symbol": symbol,
                "annualDividend": 0,
                "dividendYield": 0,
                "yieldOnCost": 0,
                "error": "data_fetch_failed",
            })
            continue

    weighted_yield = round(total_annual_dividend / total_market_value * 100, 2) if total_market_value > 0 else 0

    return {
        "portfolio_dividends": portfolio_dividends,
        "summary": {
            "totalAnnualDividend": round(total_annual_dividend, 2),
            "weightedYield": weighted_yield,
            "totalMarketValue": round(total_market_value, 2),
        },
    }


# ── Market-wide dividend calendar ────────────────────────────────────────────

def _dividend_calendar(days: int) -> dict:
    """Get market-wide dividend calendar for the next N days."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare not installed"}

    try:
        today = datetime.now()
        deadline = today + timedelta(days=days)
        calendar: list[dict] = []

        # Try getting the dividend calendar from akshare
        try:
            df = ak.stock_fhps_em(date=today.strftime("%Y%m%d"))
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    ex_date_str = str(row.get("除权除息日", ""))
                    if not ex_date_str or ex_date_str in ("nan", "None", "--", ""):
                        continue
                    try:
                        ex_date = datetime.strptime(ex_date_str[:10], "%Y-%m-%d")
                    except (ValueError, TypeError):
                        continue

                    if today <= ex_date <= deadline:
                        record: dict[str, Any] = {
                            "symbol": str(row.get("代码", "")),
                            "name": str(row.get("名称", "")),
                            "exDate": ex_date.strftime("%Y-%m-%d"),
                        }
                        dividend = _safe_float(row.get("现金分红-每股派息(元)", row.get("每股分红", 0)))
                        if dividend and dividend > 0:
                            record["dividend"] = dividend
                        bonus = _safe_float(row.get("送转股-每股送转股", row.get("每股送股", 0)))
                        if bonus and bonus > 0:
                            record["bonus"] = bonus
                        calendar.append(record)
        except Exception:
            # Fallback: try alternative function
            pass

        # Sort by ex-date
        calendar.sort(key=lambda x: x.get("exDate", ""))

        return {"calendar": calendar[:100]}  # Limit results

    except Exception as e:
        return {"error": f"获取分红日历失败: {str(e)}", "calendar": []}


# ── Utilities ────────────────────────────────────────────────────────────────

def _safe_float(val: Any) -> float | None:
    """Safely convert a value to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN check
            return None
        return f
    except (ValueError, TypeError):
        return None


# ── Main entry point ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    raw = os.environ.get("QUANT_CONFIG", "")
    if not raw:
        raw = sys.stdin.read()
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        cfg = {}

    try:
        result = run_dividend_tracker(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"error": str(e), "traceback": traceback.format_exc()},
                         ensure_ascii=False))
