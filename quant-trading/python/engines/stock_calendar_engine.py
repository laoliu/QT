"""
stock_calendar_engine — Comprehensive stock event calendar & catalyst tracker.

Aggregates key events beyond earnings: restricted share unlocks (限售股解禁),
shareholder meetings (股东大会), dividend ex-dates (分红除权), insider
buy/sell announcements (增减持公告), and more.

Input (JSON via stdin):
  {
    "action": "upcoming_events" | "stock_events" | "watchlist_events" | "portfolio_events",
    "symbols": ["600519", "000858"],   // optional: specific codes
    "days": 30,                        // look-ahead window (default 30)
    "event_types": ["unlock", "dividend", "shareholder_meeting", "insider", "earnings"],
    "limit": 50,
    "watchlist_path": "...",           // path to watchlist.json
    "portfolio_path": "..."            // path to portfolio-paper.json
  }

Output: JSON with aggregated events sorted by date.
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

# ── Event type constants ──────────────────────────────────────────────────────

EVENT_TYPES = {
    "unlock": "限售股解禁",
    "dividend": "分红除权",
    "shareholder_meeting": "股东大会",
    "insider_increase": "重要股东增持",
    "insider_decrease": "重要股东减持",
    "earnings": "财报披露",
    "allotment": "配股",
    "placement": "定增",
    "bond_convert": "可转债转股",
    "repurchase": "股份回购",
}


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:
            return None
        return round(f, 4)
    except (ValueError, TypeError):
        return None


def _safe_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _parse_date(raw: Any) -> str | None:
    """Try to parse a date string into YYYY-MM-DD format."""
    s = _safe_str(raw)[:10]
    if not s or s == "None" or s == "nan":
        return None
    for fmt in ("%Y-%m-%d", "%Y%m%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return None


# ── Event fetchers ────────────────────────────────────────────────────────────

def _fetch_unlock_events(days: int, symbols: list[str] | None = None) -> list[dict]:
    """Fetch restricted share unlock events (限售股解禁)."""
    events: list[dict] = []
    try:
        import akshare as ak
        today = datetime.now()
        start_date = today.strftime("%Y%m%d")
        end_date = (today + timedelta(days=days)).strftime("%Y%m%d")

        try:
            df = ak.stock_restricted_release_summary_em(start_date=start_date, end_date=end_date)
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = _safe_str(row.get("股票代码", row.get("代码", "")))
                    if symbols and code not in symbols:
                        continue
                    event_date = _parse_date(row.get("解禁日期", row.get("解除限售日期", "")))
                    if not event_date:
                        continue
                    unlock_shares = _safe_float(row.get("解禁数量", row.get("解禁股数", None)))
                    unlock_ratio = _safe_float(row.get("解禁比例", row.get("占总股本比例", None)))
                    unlock_value = _safe_float(row.get("解禁市值", row.get("解禁市值(万元)", None)))
                    events.append({
                        "type": "unlock",
                        "typeLabel": EVENT_TYPES["unlock"],
                        "date": event_date,
                        "symbol": code,
                        "name": _safe_str(row.get("股票简称", row.get("名称", ""))),
                        "summary": f"解禁{'%.2f' % (unlock_shares / 1e4) if unlock_shares else '?'}万股"
                                   + (f", 占比{unlock_ratio:.2f}%" if unlock_ratio else "")
                                   + (f", 市值{unlock_value:.0f}万" if unlock_value else ""),
                        "impact": "negative" if (unlock_ratio and unlock_ratio > 5) else "neutral",
                        "details": {
                            "unlockShares": unlock_shares,
                            "unlockRatio": unlock_ratio,
                            "unlockValue": unlock_value,
                        },
                    })
        except Exception as e:
            print(f"[stock_calendar] unlock fetch error: {e}", file=sys.stderr)
    except ImportError:
        pass
    return events


def _fetch_dividend_events(days: int, symbols: list[str] | None = None) -> list[dict]:
    """Fetch dividend/ex-date events (分红除权)."""
    events: list[dict] = []
    try:
        import akshare as ak
        today = datetime.now()
        deadline = today + timedelta(days=days)
        current_year = today.year

        try:
            df = ak.stock_dividend_cninfo(year=str(current_year))
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = _safe_str(row.get("股票代码", row.get("代码", "")))
                    if symbols and code not in symbols:
                        continue
                    ex_date = _parse_date(row.get("除权除息日", row.get("A股除权除息日", "")))
                    if not ex_date:
                        continue
                    try:
                        ed = datetime.strptime(ex_date, "%Y-%m-%d")
                        if not (today <= ed <= deadline):
                            continue
                    except ValueError:
                        continue

                    cash_div = _safe_float(row.get("派息比例", row.get("每股派息", None)))
                    bonus_ratio = _safe_float(row.get("送股比例", row.get("每股送股", None)))
                    transfer_ratio = _safe_float(row.get("转增比例", row.get("每股转增", None)))

                    parts = []
                    if cash_div and cash_div > 0:
                        parts.append(f"派{cash_div:.2f}元")
                    if bonus_ratio and bonus_ratio > 0:
                        parts.append(f"送{bonus_ratio:.0f}股")
                    if transfer_ratio and transfer_ratio > 0:
                        parts.append(f"转{transfer_ratio:.0f}股")

                    events.append({
                        "type": "dividend",
                        "typeLabel": EVENT_TYPES["dividend"],
                        "date": ex_date,
                        "symbol": code,
                        "name": _safe_str(row.get("股票简称", row.get("名称", ""))),
                        "summary": "除权除息: " + (", ".join(parts) if parts else "详见公告"),
                        "impact": "positive" if cash_div and cash_div > 0.5 else "neutral",
                        "details": {
                            "cashDividend": cash_div,
                            "bonusRatio": bonus_ratio,
                            "transferRatio": transfer_ratio,
                        },
                    })
        except Exception as e:
            print(f"[stock_calendar] dividend fetch error: {e}", file=sys.stderr)
    except ImportError:
        pass
    return events


def _fetch_shareholder_meeting_events(days: int, symbols: list[str] | None = None) -> list[dict]:
    """Fetch shareholder meeting events (股东大会)."""
    events: list[dict] = []
    try:
        import akshare as ak
        today = datetime.now()
        deadline = today + timedelta(days=days)

        try:
            date_str = today.strftime("%Y%m%d")
            df = ak.stock_gddh_em(date=date_str)
            if df is not None and not df.empty:
                for _, row in df.iterrows():
                    code = _safe_str(row.get("股票代码", row.get("代码", "")))
                    if symbols and code not in symbols:
                        continue
                    meeting_date = _parse_date(row.get("股东大会日期", row.get("召开日期", "")))
                    if not meeting_date:
                        continue
                    try:
                        md = datetime.strptime(meeting_date, "%Y-%m-%d")
                        if not (today <= md <= deadline):
                            continue
                    except ValueError:
                        continue

                    meeting_type = _safe_str(row.get("股东大会类型", row.get("会议类型", "股东大会")))
                    events.append({
                        "type": "shareholder_meeting",
                        "typeLabel": EVENT_TYPES["shareholder_meeting"],
                        "date": meeting_date,
                        "symbol": code,
                        "name": _safe_str(row.get("股票简称", row.get("名称", ""))),
                        "summary": meeting_type,
                        "impact": "neutral",
                        "details": {"meetingType": meeting_type},
                    })
        except Exception as e:
            print(f"[stock_calendar] shareholder_meeting fetch error: {e}", file=sys.stderr)
    except ImportError:
        pass
    return events


def _fetch_insider_events(days: int, symbols: list[str] | None = None) -> list[dict]:
    """Fetch insider trading (增减持) events."""
    events: list[dict] = []
    try:
        import akshare as ak
        today = datetime.now()
        deadline = today + timedelta(days=days)

        for direction, event_type in [("增持", "insider_increase"), ("减持", "insider_decrease")]:
            try:
                df = ak.stock_inner_trade_xq()
                if df is None or df.empty:
                    continue
                for _, row in df.iterrows():
                    code = _safe_str(row.get("股票代码", row.get("证券代码", "")))
                    if symbols and code not in symbols:
                        continue

                    trade_date = _parse_date(row.get("变动日期", row.get("公告日期", "")))
                    if not trade_date:
                        continue
                    try:
                        td = datetime.strptime(trade_date, "%Y-%m-%d")
                        if not (today - timedelta(days=7) <= td <= deadline):
                            continue
                    except ValueError:
                        continue

                    change_dir = _safe_str(row.get("变动方向", row.get("增减持", "")))
                    if direction == "增持" and "增" not in change_dir:
                        continue
                    if direction == "减持" and "减" not in change_dir:
                        continue

                    change_shares = _safe_float(row.get("变动股数", row.get("变动数量", None)))
                    holder = _safe_str(row.get("变动人", row.get("股东名称", "")))

                    events.append({
                        "type": event_type,
                        "typeLabel": EVENT_TYPES[event_type],
                        "date": trade_date,
                        "symbol": code,
                        "name": _safe_str(row.get("股票简称", row.get("名称", ""))),
                        "summary": f"{holder} {direction} {'%.2f' % (change_shares / 1e4) if change_shares else '?'}万股",
                        "impact": "positive" if event_type == "insider_increase" else "negative",
                        "details": {
                            "holder": holder,
                            "direction": direction,
                            "changeShares": change_shares,
                        },
                    })
            except Exception as e:
                print(f"[stock_calendar] insider {direction} fetch error: {e}", file=sys.stderr)
    except ImportError:
        pass
    return events


def _fetch_earnings_events(days: int, symbols: list[str] | None = None) -> list[dict]:
    """Fetch upcoming earnings disclosure events."""
    events: list[dict] = []
    try:
        import akshare as ak
        today = datetime.now()
        deadline = today + timedelta(days=days)
        current_year = today.year

        for date_str in [f"{current_year}0331", f"{current_year}0630",
                         f"{current_year}0930", f"{current_year}1231"]:
            try:
                df = ak.stock_report_disclosure(date=date_str)
                if df is None or df.empty:
                    continue
                for _, row in df.iterrows():
                    code = _safe_str(row.get("股票代码", row.get("代码", "")))
                    if symbols and code not in symbols:
                        continue
                    disc_date = _parse_date(row.get("首次披露日期",
                                                     row.get("实际披露日期",
                                                              row.get("披露日期", ""))))
                    if not disc_date:
                        continue
                    try:
                        dd = datetime.strptime(disc_date, "%Y-%m-%d")
                        if not (today <= dd <= deadline):
                            continue
                    except ValueError:
                        continue

                    report_period = date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:]
                    events.append({
                        "type": "earnings",
                        "typeLabel": EVENT_TYPES["earnings"],
                        "date": disc_date,
                        "symbol": code,
                        "name": _safe_str(row.get("股票简称", row.get("名称", ""))),
                        "summary": f"财报披露({report_period})",
                        "impact": "neutral",
                        "details": {"reportPeriod": report_period},
                    })
                if events:
                    break
            except Exception:
                continue
    except ImportError:
        pass
    return events


def _fetch_repurchase_events(days: int, symbols: list[str] | None = None) -> list[dict]:
    """Fetch share repurchase events (股份回购)."""
    events: list[dict] = []
    try:
        import akshare as ak
        today = datetime.now()

        try:
            df = ak.stock_repurchase_em()
            if df is not None and not df.empty:
                deadline = today + timedelta(days=days)
                for _, row in df.iterrows():
                    code = _safe_str(row.get("股票代码", row.get("代码", "")))
                    if symbols and code not in symbols:
                        continue
                    ann_date = _parse_date(row.get("公告日期", row.get("最新公告日", "")))
                    if not ann_date:
                        continue
                    try:
                        ad = datetime.strptime(ann_date, "%Y-%m-%d")
                        if not (today - timedelta(days=7) <= ad <= deadline):
                            continue
                    except ValueError:
                        continue

                    amount = _safe_float(row.get("回购金额", row.get("已回购金额(万元)", None)))
                    events.append({
                        "type": "repurchase",
                        "typeLabel": EVENT_TYPES["repurchase"],
                        "date": ann_date,
                        "symbol": code,
                        "name": _safe_str(row.get("股票简称", row.get("名称", ""))),
                        "summary": f"股份回购" + (f", 金额{amount:.0f}万" if amount else ""),
                        "impact": "positive",
                        "details": {"repurchaseAmount": amount},
                    })
        except Exception as e:
            print(f"[stock_calendar] repurchase fetch error: {e}", file=sys.stderr)
    except ImportError:
        pass
    return events


# ── Main actions ──────────────────────────────────────────────────────────────

_FETCHERS = {
    "unlock": _fetch_unlock_events,
    "dividend": _fetch_dividend_events,
    "shareholder_meeting": _fetch_shareholder_meeting_events,
    "insider": _fetch_insider_events,
    "earnings": _fetch_earnings_events,
    "repurchase": _fetch_repurchase_events,
}


def _aggregate_events(
    days: int,
    symbols: list[str] | None,
    event_types: list[str] | None,
    limit: int,
) -> list[dict]:
    """Aggregate events from all fetchers, sort by date."""
    all_events: list[dict] = []
    types_to_fetch = event_types or list(_FETCHERS.keys())

    for etype in types_to_fetch:
        if etype == "insider":
            for sub in ("insider_increase", "insider_decrease"):
                pass  # handled by _fetch_insider_events
            fetcher = _FETCHERS.get("insider")
        else:
            fetcher = _FETCHERS.get(etype)
        if fetcher:
            try:
                all_events.extend(fetcher(days, symbols))
            except Exception as e:
                print(f"[stock_calendar] {etype} error: {e}", file=sys.stderr)

    # Deduplicate by (type, date, symbol)
    seen = set()
    deduped = []
    for ev in all_events:
        key = (ev["type"], ev["date"], ev["symbol"])
        if key not in seen:
            seen.add(key)
            deduped.append(ev)

    deduped.sort(key=lambda x: x["date"])
    return deduped[:limit]


def _load_watchlist(path: str) -> list[str]:
    """Load watchlist codes from JSON file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(c).strip() for c in data if c]
        return []
    except Exception:
        return []


def _load_portfolio_codes(path: str) -> list[str]:
    """Load portfolio position codes from JSON file."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        positions = data.get("positions", [])
        return [str(p.get("symbol", "")).strip() for p in positions if p.get("symbol")]
    except Exception:
        return []


def _upcoming_events(config: dict) -> dict:
    """Get all upcoming events across all stocks."""
    days = config.get("days", 30)
    limit = config.get("limit", 50)
    event_types = config.get("event_types")
    symbols = config.get("symbols")

    events = _aggregate_events(days, symbols, event_types, limit)

    # Group by date for timeline view
    timeline: dict[str, list[dict]] = {}
    for ev in events:
        d = ev["date"]
        if d not in timeline:
            timeline[d] = []
        timeline[d].append(ev)

    # Summary by type
    type_counts: dict[str, int] = {}
    for ev in events:
        t = ev["type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    return {
        "action": "upcoming_events",
        "days": days,
        "totalEvents": len(events),
        "typeCounts": type_counts,
        "events": events,
        "timeline": {k: v for k, v in sorted(timeline.items())},
        "dateRange": {
            "start": datetime.now().strftime("%Y-%m-%d"),
            "end": (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d"),
        },
    }


def _stock_events(config: dict) -> dict:
    """Get events for specific stocks."""
    symbols = config.get("symbols", [])
    if not symbols:
        return {"error": "symbols is required for stock_events"}
    days = config.get("days", 60)
    limit = config.get("limit", 50)
    event_types = config.get("event_types")

    events = _aggregate_events(days, symbols, event_types, limit)

    # Group by symbol
    by_symbol: dict[str, list[dict]] = {}
    for ev in events:
        s = ev["symbol"]
        if s not in by_symbol:
            by_symbol[s] = []
        by_symbol[s].append(ev)

    return {
        "action": "stock_events",
        "symbols": symbols,
        "days": days,
        "totalEvents": len(events),
        "events": events,
        "bySymbol": by_symbol,
    }


def _watchlist_events(config: dict) -> dict:
    """Get events for watchlist stocks."""
    watchlist_path = config.get("watchlist_path", "")
    if not watchlist_path:
        return {"error": "watchlist_path is required"}

    codes = _load_watchlist(watchlist_path)
    if not codes:
        return {
            "action": "watchlist_events",
            "message": "自选股列表为空",
            "events": [],
            "totalEvents": 0,
        }

    days = config.get("days", 30)
    limit = config.get("limit", 100)
    event_types = config.get("event_types")

    events = _aggregate_events(days, codes, event_types, limit)

    # Highlight events that need attention
    alerts: list[dict] = []
    for ev in events:
        if ev["impact"] == "negative" and ev["type"] == "unlock":
            alerts.append({
                "level": "warning",
                "symbol": ev["symbol"],
                "name": ev["name"],
                "message": f'{ev["name"]}({ev["symbol"]}) 将于 {ev["date"]} 解禁, {ev["summary"]}',
                "event": ev,
            })
        elif ev["impact"] == "negative" and ev["type"] == "insider_decrease":
            alerts.append({
                "level": "warning",
                "symbol": ev["symbol"],
                "name": ev["name"],
                "message": f'{ev["name"]}({ev["symbol"]}) {ev["summary"]}',
                "event": ev,
            })
        elif ev["type"] == "earnings":
            alerts.append({
                "level": "info",
                "symbol": ev["symbol"],
                "name": ev["name"],
                "message": f'{ev["name"]}({ev["symbol"]}) 将于 {ev["date"]} 披露财报',
                "event": ev,
            })

    return {
        "action": "watchlist_events",
        "watchlistCount": len(codes),
        "days": days,
        "totalEvents": len(events),
        "alertCount": len(alerts),
        "alerts": alerts,
        "events": events,
    }


def _portfolio_events(config: dict) -> dict:
    """Get events for portfolio holding stocks."""
    portfolio_path = config.get("portfolio_path", "")
    if not portfolio_path:
        return {"error": "portfolio_path is required"}

    codes = _load_portfolio_codes(portfolio_path)
    if not codes:
        return {
            "action": "portfolio_events",
            "message": "持仓为空",
            "events": [],
            "totalEvents": 0,
        }

    days = config.get("days", 30)
    limit = config.get("limit", 100)
    event_types = config.get("event_types")

    events = _aggregate_events(days, codes, event_types, limit)

    # Risk alerts for holding stocks
    alerts: list[dict] = []
    for ev in events:
        if ev["impact"] == "negative":
            alerts.append({
                "level": "danger",
                "symbol": ev["symbol"],
                "name": ev["name"],
                "message": f'⚠️ 持仓股 {ev["name"]}({ev["symbol"]}) {ev["summary"]} ({ev["date"]})',
                "event": ev,
            })
        elif ev["type"] == "earnings":
            alerts.append({
                "level": "info",
                "symbol": ev["symbol"],
                "name": ev["name"],
                "message": f'📋 持仓股 {ev["name"]}({ev["symbol"]}) 将于 {ev["date"]} 披露财报',
                "event": ev,
            })
        elif ev["impact"] == "positive":
            alerts.append({
                "level": "positive",
                "symbol": ev["symbol"],
                "name": ev["name"],
                "message": f'✅ 持仓股 {ev["name"]}({ev["symbol"]}) {ev["summary"]} ({ev["date"]})',
                "event": ev,
            })

    return {
        "action": "portfolio_events",
        "holdingCount": len(codes),
        "days": days,
        "totalEvents": len(events),
        "alertCount": len(alerts),
        "alerts": alerts,
        "events": events,
    }


# ── Main dispatch ─────────────────────────────────────────────────────────────

ACTIONS = {
    "upcoming_events": _upcoming_events,
    "stock_events": _stock_events,
    "watchlist_events": _watchlist_events,
    "portfolio_events": _portfolio_events,
}


def main():
    config = json.loads(sys.stdin.read())
    action = config.get("action", "upcoming_events")

    handler = ACTIONS.get(action)
    if not handler:
        print(json.dumps({"error": f"Unknown action: {action}"}, ensure_ascii=False))
        return

    try:
        result = handler(config)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({
            "error": str(e),
            "traceback": traceback.format_exc(),
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
