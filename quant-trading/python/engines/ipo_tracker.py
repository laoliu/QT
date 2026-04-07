"""
ipo_tracker — IPO and new stock listing tracker for A-shares.

Tracks upcoming and recent IPOs:
- Upcoming IPO subscription dates (打新日历)
- Recent IPO performance tracking
- New stock (次新股) screening

Input:
  {
    "action": "upcoming" | "recent" | "performance" | "subscription",
    "days": 30,
    "limit": 20
  }

Output:
  { "ipos": [...], "calendar": [...] }
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta
from typing import Any

for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"


def run_ipo_tracker(config: dict) -> dict:
    """Dispatch IPO tracking."""
    action = config.get("action", "upcoming")
    limit = config.get("limit", 20)

    if action == "upcoming":
        return _upcoming_ipos(limit)
    elif action == "recent":
        days = config.get("days", 30)
        return _recent_ipos(days, limit)
    elif action == "performance":
        days = config.get("days", 60)
        return _ipo_performance(days, limit)
    elif action == "subscription":
        return _subscription_info(limit)
    else:
        return {"error": f"未知action: {action}"}


def _upcoming_ipos(limit: int) -> dict:
    """Get upcoming IPO subscription dates."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    errors: list[str] = []
    ipos = []

    try:
        # Try to get new stock subscription data
        df = ak.stock_xgsglb_em(symbol="全部股票")
        if df is not None and not df.empty:
            now = datetime.now()

            for _, row in df.iterrows():
                sub_date_str = str(row.get("申购日期", ""))
                if not sub_date_str or sub_date_str == "nan":
                    continue

                try:
                    sub_date = datetime.strptime(sub_date_str[:10], "%Y-%m-%d")
                except (ValueError, TypeError):
                    continue

                # Include upcoming (next 30 days) and recent (past 7 days)
                if (now - timedelta(days=7)) <= sub_date <= (now + timedelta(days=30)):
                    ipo = {
                        "code": str(row.get("股票代码", "")),
                        "name": str(row.get("股票简称", "")),
                        "subscription_date": sub_date_str[:10],
                        "issue_price": _safe_float(row.get("发行价格")),
                        "pe_ratio": _safe_float(row.get("发行市盈率")),
                        "issue_amount_wan": _safe_float(row.get("发行总数")),
                        "subscription_limit_wan": _safe_float(row.get("申购上限")),
                        "winning_rate": _safe_float(row.get("中签率")),
                        "listing_date": str(row.get("上市日期", ""))[:10],
                        "days_until": (sub_date - now).days,
                        "status": "即将申购" if sub_date > now else "已申购",
                    }
                    ipos.append(ipo)

            ipos.sort(key=lambda x: x.get("days_until", 999))
    except Exception as e:
        errors.append(f"获取新股申购数据失败: {str(e)[:100]}")

    result: dict[str, Any] = {
        "action": "upcoming",
        "ipos": ipos[:limit],
        "count": len(ipos),
    }
    if errors:
        result["errors"] = errors
    return result


def _recent_ipos(days: int, limit: int) -> dict:
    """Get recently listed new stocks."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    errors: list[str] = []
    stocks = []

    try:
        df = ak.stock_xgsglb_em(symbol="全部股票")
        if df is not None and not df.empty:
            now = datetime.now()

            for _, row in df.iterrows():
                list_date_str = str(row.get("上市日期", ""))
                if not list_date_str or list_date_str == "nan" or list_date_str == "-":
                    continue

                try:
                    list_date = datetime.strptime(list_date_str[:10], "%Y-%m-%d")
                except (ValueError, TypeError):
                    continue

                if (now - timedelta(days=days)) <= list_date <= now:
                    stock = {
                        "code": str(row.get("股票代码", "")),
                        "name": str(row.get("股票简称", "")),
                        "listing_date": list_date_str[:10],
                        "issue_price": _safe_float(row.get("发行价格")),
                        "days_listed": (now - list_date).days,
                    }
                    stocks.append(stock)

            stocks.sort(key=lambda x: x.get("days_listed", 999))
    except Exception as e:
        errors.append(f"获取次新股数据失败: {str(e)[:100]}")

    # Enrich with current price data
    if stocks:
        try:
            spot_df = ak.stock_zh_a_spot_em()
            if spot_df is not None and not spot_df.empty:
                price_map = {}
                for _, row in spot_df.iterrows():
                    code = str(row.get("代码", ""))
                    price_map[code] = {
                        "price": _safe_float(row.get("最新价")),
                        "change_pct": _safe_float(row.get("涨跌幅")),
                        "turnover_rate": _safe_float(row.get("换手率")),
                        "total_mv": _safe_float(row.get("总市值")),
                    }

                for stock in stocks:
                    info = price_map.get(stock["code"], {})
                    stock.update(info)
                    issue_price = stock.get("issue_price")
                    current_price = stock.get("price")
                    if issue_price and current_price:
                        stock["ipo_return"] = round(
                            (current_price - issue_price) / issue_price * 100, 2
                        )
        except Exception as e:
            errors.append(f"获取次新股行情失败: {str(e)[:80]}")

    result: dict[str, Any] = {
        "action": "recent",
        "stocks": stocks[:limit],
        "count": len(stocks),
        "query_range_days": days,
    }
    if errors:
        result["errors"] = errors
    return result


def _ipo_performance(days: int, limit: int) -> dict:
    """Track IPO performance — how new stocks performed since listing."""
    result = _recent_ipos(days, limit)
    if "error" in result:
        return result

    stocks = result.get("stocks", [])

    # Summary statistics
    returns = [s.get("ipo_return") for s in stocks if s.get("ipo_return") is not None]

    summary = {
        "total_ipos": len(stocks),
        "with_return_data": len(returns),
        "avg_return": round(sum(returns) / len(returns), 2) if returns else None,
        "max_return": round(max(returns), 2) if returns else None,
        "min_return": round(min(returns), 2) if returns else None,
        "positive_count": len([r for r in returns if r > 0]),
        "negative_count": len([r for r in returns if r < 0]),
    }

    result["action"] = "performance"
    result["summary"] = summary
    return result


def _subscription_info(limit: int) -> dict:
    """Get detailed IPO subscription information."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装"}

    try:
        df = ak.stock_xgsglb_em(symbol="全部股票")
        if df is None or df.empty:
            return {"action": "subscription", "ipos": [], "count": 0}

        now = datetime.now()
        subs = []

        for _, row in df.head(limit * 2).iterrows():
            sub_date_str = str(row.get("申购日期", ""))
            try:
                sub_date = datetime.strptime(sub_date_str[:10], "%Y-%m-%d")
                if sub_date < now - timedelta(days=30):
                    continue
            except (ValueError, TypeError):
                continue

            sub = {
                "code": str(row.get("股票代码", "")),
                "name": str(row.get("股票简称", "")),
                "subscription_date": sub_date_str[:10],
                "subscription_code": str(row.get("申购代码", "")),
                "issue_price": _safe_float(row.get("发行价格")),
                "pe_ratio": _safe_float(row.get("发行市盈率")),
                "issue_shares_wan": _safe_float(row.get("发行总数")),
                "online_issue_wan": _safe_float(row.get("网上发行")),
                "subscription_limit_wan": _safe_float(row.get("申购上限")),
                "winning_rate": _safe_float(row.get("中签率")),
                "winning_number_date": str(row.get("中签号公布日", ""))[:10],
                "listing_date": str(row.get("上市日期", ""))[:10],
            }
            subs.append(sub)

        subs = subs[:limit]

        return {
            "action": "subscription",
            "ipos": subs,
            "count": len(subs),
        }

    except Exception as e:
        return {"error": f"获取申购信息失败: {str(e)[:100]}"}


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        return round(f, 4) if abs(f) < 1e15 else None
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

        result = run_ipo_tracker(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
