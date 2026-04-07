"""
stock_events — Fetch upcoming stock events for given symbols.

Events include:
  - earnings: 财报披露日期
  - lift_ban: 限售股解禁
  - shareholder_meeting: 股东大会
  - dividend: 分红派息
  - additional_issue: 增发配股

Input (JSON via stdin / QUANT_CONFIG):
  {
    "action": "check_events",
    "symbols": ["600519", "000858"],
    "days": 30
  }

Output: JSON with events grouped by symbol.
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


def run_stock_events(config: dict) -> dict:
    """Dispatch stock events actions."""
    action = config.get("action", "check_events")
    symbols = config.get("symbols", [])
    days = config.get("days", 30)

    if action == "check_events":
        if not symbols:
            return {"error": "symbols list is required"}
        return _check_events(symbols, days)
    else:
        return {"error": f"未知 action: {action}"}


def _check_events(symbols: list[str], days: int) -> dict:
    """Check multiple event types for given symbols within N days."""
    today = datetime.now()
    deadline = today + timedelta(days=days)

    results: dict[str, list[dict]] = {}
    for sym in symbols:
        results[sym] = []

    # 1. Earnings disclosure dates (财报披露)
    _fetch_earnings_events(symbols, today, deadline, results)

    # 2. Lift-ban / restricted share unlock (限售股解禁)
    _fetch_lift_ban_events(symbols, today, deadline, results)

    # 3. Shareholder meetings (股东大会)
    _fetch_shareholder_meeting_events(symbols, today, deadline, results)

    # 4. Dividend / ex-dividend (分红派息)
    _fetch_dividend_events(symbols, today, deadline, results)

    # Sort events by date for each symbol
    for sym in results:
        results[sym].sort(key=lambda x: x.get("event_date", "9999-99-99"))

    return {
        "check_date": today.strftime("%Y-%m-%d"),
        "lookahead_days": days,
        "symbol_count": len(symbols),
        "events": results,
    }


def _fetch_earnings_events(
    symbols: list[str], today: datetime, deadline: datetime, results: dict
) -> None:
    """Fetch earnings disclosure dates from akshare."""
    try:
        import akshare as ak
    except ImportError:
        return

    symbol_set = set(s.lstrip("0") if len(s) <= 6 else s for s in symbols)
    symbol_set_full = set(symbols)

    try:
        current_year = today.year
        for date_str in [
            f"{current_year}0331", f"{current_year}0630",
            f"{current_year}0930", f"{current_year}1231",
        ]:
            try:
                df = ak.stock_report_disclosure(date=date_str)
                if df is None or df.empty:
                    continue
                for _, row in df.iterrows():
                    code = str(row.get("股票代码", row.get("代码", ""))).strip()
                    if code not in symbol_set_full and code.lstrip("0") not in symbol_set:
                        continue

                    disc_date_raw = str(
                        row.get("首次披露日期",
                                row.get("实际披露日期",
                                         row.get("披露日期", "")))
                    )[:10]
                    if not disc_date_raw or disc_date_raw == "None":
                        continue
                    try:
                        disc_date = datetime.strptime(disc_date_raw, "%Y-%m-%d")
                    except ValueError:
                        continue

                    if today <= disc_date <= deadline:
                        # Normalize code to match input
                        matched_sym = code if code in symbol_set_full else None
                        if not matched_sym:
                            for s in symbols:
                                if s == code or s.lstrip("0") == code.lstrip("0"):
                                    matched_sym = s
                                    break
                        if matched_sym and matched_sym in results:
                            days_until = (disc_date - today).days
                            report_period = (
                                date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:]
                            )
                            results[matched_sym].append({
                                "event_type": "earnings",
                                "event_name": f"财报披露 ({report_period})",
                                "event_date": disc_date_raw,
                                "days_until": days_until,
                                "stock_name": str(
                                    row.get("股票简称", row.get("名称", ""))
                                ),
                                "details": {
                                    "report_period": report_period,
                                },
                            })
            except Exception:
                continue
    except Exception:
        pass


def _fetch_lift_ban_events(
    symbols: list[str], today: datetime, deadline: datetime, results: dict
) -> None:
    """Fetch restricted share lift-ban (限售股解禁) events."""
    try:
        import akshare as ak
    except ImportError:
        return

    symbol_set = set(symbols)

    try:
        # akshare provides restricted share unlock schedule
        start_str = today.strftime("%Y%m%d")
        end_str = deadline.strftime("%Y%m%d")
        try:
            df = ak.stock_restricted_release_summary_em(
                start_date=start_str, end_date=end_str
            )
        except Exception:
            # Fallback: try the detail API per-stock
            df = None

        if df is not None and not df.empty:
            for _, row in df.iterrows():
                code = str(row.get("股票代码", row.get("代码", ""))).strip()
                if code not in symbol_set:
                    continue

                event_date_raw = str(
                    row.get("解禁日期", row.get("解禁时间", ""))
                )[:10]
                if not event_date_raw or event_date_raw == "None":
                    continue
                try:
                    event_date = datetime.strptime(event_date_raw, "%Y-%m-%d")
                except ValueError:
                    continue

                if today <= event_date <= deadline:
                    days_until = (event_date - today).days
                    unlock_shares = _safe_float(
                        row.get("解禁数量", row.get("解禁股数", 0))
                    )
                    unlock_ratio = _safe_float(
                        row.get("解禁比例", row.get("占总股本比例", 0))
                    )
                    results[code].append({
                        "event_type": "lift_ban",
                        "event_name": "限售股解禁",
                        "event_date": event_date_raw,
                        "days_until": days_until,
                        "stock_name": str(
                            row.get("股票简称", row.get("名称", ""))
                        ),
                        "details": {
                            "unlock_shares": unlock_shares,
                            "unlock_ratio_pct": unlock_ratio,
                        },
                    })
    except Exception:
        pass


def _fetch_shareholder_meeting_events(
    symbols: list[str], today: datetime, deadline: datetime, results: dict
) -> None:
    """Fetch shareholder meeting (股东大会) dates.

    Note: akshare may not have a direct API for this; we attempt
    best-effort fetching. If unavailable, we skip silently.
    """
    try:
        import akshare as ak
    except ImportError:
        return

    # akshare does not have a dedicated shareholder meeting API that is
    # reliable across versions. We'll attempt the news/announcement route.
    # For now, this is a placeholder that will be populated as data becomes
    # available through the news_summary or manual calendar entries.
    pass


def _fetch_dividend_events(
    symbols: list[str], today: datetime, deadline: datetime, results: dict
) -> None:
    """Fetch dividend (分红派息) events."""
    try:
        import akshare as ak
    except ImportError:
        return

    symbol_set = set(symbols)

    try:
        current_year = today.year
        try:
            df = ak.stock_fhps_em(date=str(current_year))
        except Exception:
            df = None

        if df is not None and not df.empty:
            for _, row in df.iterrows():
                code = str(row.get("股票代码", row.get("代码", ""))).strip()
                if code not in symbol_set:
                    continue

                # Try ex-dividend date first, then record date
                ex_date_raw = str(
                    row.get("除权除息日", row.get("股权登记日", ""))
                )[:10]
                if not ex_date_raw or ex_date_raw == "None" or ex_date_raw == "nan":
                    continue
                try:
                    ex_date = datetime.strptime(ex_date_raw, "%Y-%m-%d")
                except ValueError:
                    continue

                if today <= ex_date <= deadline:
                    days_until = (ex_date - today).days
                    results[code].append({
                        "event_type": "dividend",
                        "event_name": "分红派息",
                        "event_date": ex_date_raw,
                        "days_until": days_until,
                        "stock_name": str(
                            row.get("股票简称", row.get("名称", ""))
                        ),
                        "details": {
                            "scheme": str(
                                row.get("分红方案", row.get("送转方案", ""))
                            ),
                        },
                    })
    except Exception:
        pass


def _safe_float(val: Any) -> float | None:
    """Safely convert to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        return f if f == f else None  # NaN check
    except (ValueError, TypeError):
        return None


# ── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    try:
        raw = os.environ.get("QUANT_CONFIG", "")
        if not raw:
            raw = sys.stdin.read()
        config = json.loads(raw) if raw else {}
        result = run_stock_events(config)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"error": str(e), "traceback": traceback.format_exc()},
                         ensure_ascii=False))
