"""
sector_analysis — Industry sector ranking and detail analysis.

Uses Sina Finance APIs (Eastmoney is unreachable from some networks).

Called from TypeScript sector_analysis tool via python-bridge.
Input:
  { "action": "ranking" | "detail" | "history", "sector": "...", "limit": 20 }

Output varies by action:
  ranking → { sectors: [...] }
  detail  → { name, change_pct, stocks: [...] }
  history → { sector, history: [...] }
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
import urllib.request
from typing import Any

# ── Proxy bypass ──────────────────────────────────────────────────────────────
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"
urllib.request.getproxies = lambda: {}  # type: ignore[assignment]
try:
    import requests, requests.utils  # noqa: E401
    requests.utils.getproxies = lambda: {}  # type: ignore[assignment]
    requests.utils.get_environ_proxies = lambda *a, **kw: {}  # type: ignore[assignment]
except ImportError:
    pass

_SINA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn/",
}
_SINA_TIMEOUT = 15
_XQ_SESSION: requests.Session | None = None


def _xq_session() -> requests.Session:
    """Lazy-init a Xueqiu session with auth cookie."""
    global _XQ_SESSION
    if _XQ_SESSION is not None:
        return _XQ_SESSION
    from akshare.stock.cons import xq_a_token
    s = requests.Session()
    s.headers.update({
        "cookie": f"xq_a_token={xq_a_token};",
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15",
    })
    s.get("https://xueqiu.com", timeout=5)
    _XQ_SESSION = s
    return s


def _xq_search_boards(keyword: str) -> list[dict]:
    """Search Xueqiu for BK-coded boards matching keyword."""
    s = _xq_session()
    r = s.get("https://xueqiu.com/query/v1/search/web/stock.json",
              params={"q": keyword, "size": "10"}, timeout=10)
    results = []
    for item in r.json().get("list", []):
        code = item.get("code", "")
        if code.startswith("BK"):
            results.append({"code": code, "name": item.get("name", "")})
    return results


def _xq_board_quote(bk_code: str) -> dict | None:
    """Get board-level quote from Xueqiu."""
    s = _xq_session()
    r = s.get("https://stock.xueqiu.com/v5/stock/batch/quote.json",
              params={"symbol": bk_code}, timeout=10)
    items = r.json().get("data", {}).get("items", [])
    if items and items[0].get("quote"):
        return items[0]["quote"]
    return None


# ── Sina Finance board data ───────────────────────────────────────────────────

def _fetch_sina_boards(category: str = "class") -> dict[str, dict]:
    """Fetch all boards from Sina Finance.

    category: "class" (concept boards) or "industry" (industry boards).
    Returns {board_name: {code, name, count, avg_price, avg_change, avg_pct,
                          leader_code, leader_name, leader_price, leader_change}}.
    """
    url = f"https://vip.stock.finance.sina.com.cn/q/view/newFLJK.php?param={category}"
    r = requests.get(url, headers=_SINA_HEADERS, timeout=_SINA_TIMEOUT)
    r.raise_for_status()
    m = re.search(r"=\s*({.*})\s*;?\s*$", r.text, re.DOTALL)
    if not m:
        return {}
    raw = json.loads(m.group(1))
    boards: dict[str, dict] = {}
    for _key, val in raw.items():
        parts = val.split(",")
        if len(parts) < 13:
            continue
        # Format: code,name,count,avg_price,avg_change,avg_pct,vol,amount,
        #          leader_code,leader_price,leader_high,leader_change,leader_name
        name = parts[1]
        boards[name] = {
            "code": parts[0],
            "name": name,
            "count": int(parts[2]) if parts[2].isdigit() else 0,
            "avg_price": _safe_float(parts[3]),
            "avg_change": _safe_float(parts[4]),
            "avg_pct": _safe_float(parts[5]),
            "volume": _safe_float(parts[6]),
            "amount": _safe_float(parts[7]),
            "leader_code": parts[8],
            "leader_name": parts[12] if len(parts) > 12 else "",
            "leader_price": _safe_float(parts[9]),
            "leader_change": _safe_float(parts[11]),
            "type": "concept" if category == "class" else "industry",
        }
    return boards


def _fetch_board_stocks(board_code: str, limit: int = 20) -> list[dict]:
    """Fetch constituent stocks for a board from Sina Finance."""
    url = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/"
           "json_v2.php/Market_Center.getHQNodeData")
    params = {
        "page": "1",
        "num": str(limit),
        "sort": "changepercent",
        "asc": "0",
        "node": board_code,
    }
    r = requests.get(url, params=params, headers=_SINA_HEADERS, timeout=_SINA_TIMEOUT)
    r.raise_for_status()
    raw_stocks = json.loads(r.text)
    stocks = []
    for st in raw_stocks:
        chg = _safe_float(st.get("changepercent"))
        stocks.append({
            "code": st.get("symbol", ""),
            "name": st.get("name", ""),
            "price": _safe_float(st.get("trade")),
            "change_pct": chg,
            "changePct": chg,
            "volume": _safe_float(st.get("volume")),
            "amount": _safe_float(st.get("amount")),
            "turnoverRate": _safe_float(st.get("turnoverratio")),
        })
    return stocks


def _fuzzy_match_boards(keyword: str) -> list[dict]:
    """Find boards matching keyword with progressive fuzzy matching.

    Strategy:
    1. Exact: keyword == board name
    2. Contains: keyword in board name, or board name in keyword
    3. Substring: any 2+ char substring of keyword in board name
    """
    all_boards: dict[str, dict] = {}
    for cat in ("class", "industry"):
        try:
            boards = _fetch_sina_boards(cat)
            all_boards.update(boards)
        except Exception:
            pass
    if not all_boards:
        return []

    # 1. Exact match
    if keyword in all_boards:
        return [all_boards[keyword]]

    # 2. Contains (keyword in name, or name in keyword)
    results = [info for name, info in all_boards.items() if keyword in name]
    if results:
        return results
    results = [info for name, info in all_boards.items() if name in keyword]
    if results:
        return results

    # 3. Try 2+ char substrings of the keyword (longer first)
    for length in range(len(keyword) - 1, 1, -1):
        for start in range(len(keyword) - length + 1):
            sub = keyword[start : start + length]
            hits = [info for name, info in all_boards.items() if sub in name]
            if hits:
                return hits

    return []


# ── Public API ────────────────────────────────────────────────────────────────

def run_sector_analysis(config: dict) -> dict:
    """Dispatch sector analysis based on action."""
    action = config.get("action", "ranking")
    limit = config.get("limit", 20)
    errors: list[str] = []

    if action == "ranking":
        return _sector_ranking(limit, errors)
    elif action == "detail":
        sector = config.get("sector", "")
        if not sector:
            return {"error": "请提供板块名称 (sector)"}
        return _sector_detail(sector, limit, errors)
    elif action == "history":
        sector = config.get("sector", "")
        if not sector:
            return {"error": "请提供板块名称 (sector)"}
        days = config.get("days", 20)
        return _sector_history(sector, days, errors)
    elif action in ("rotation", "momentum", "hot"):
        from .sector_rotation import run_sector_rotation
        return run_sector_rotation(config)
    else:
        return {"error": f"δ֪action: {action}"}


def _sector_ranking(limit: int, errors: list[str]) -> dict:
    """Get sector rankings (today's performance) from Sina Finance."""
    sectors: list[dict] = []

    # Collect both industry and concept boards, sort by avg_pct descending
    for cat in ("industry", "class"):
        try:
            boards = _fetch_sina_boards(cat)
            for info in boards.values():
                sectors.append({
                    "name": info["name"],
                    "code": info["code"],
                    "change_pct": info["avg_pct"],
                    "changePct": info["avg_pct"],
                    "amount": info["amount"],
                    "volume": info["volume"],
                    "leader": info["leader_name"],
                    "leader_change": info["leader_change"] or 0,
                    "stockCount": info["count"],
                    "type": info["type"],
                })
        except Exception as e:
            errors.append(f"{'行业' if cat == 'industry' else '概念'}板块获取失败: {e}")

    if not sectors:
        return {"error": "无法获取板块数据", "errors": errors}

    # Sort by absolute change percentage descending (biggest movers first)
    sectors.sort(key=lambda s: abs(s.get("change_pct", 0) or 0), reverse=True)
    sectors = sectors[:limit]

    return {
        "action": "ranking",
        "count": len(sectors),
        "sectors": sectors,
        "errors": errors if errors else None,
    }


def _sector_detail(sector_name: str, limit: int, errors: list[str]) -> dict:
    """Get constituent stocks of a specific sector.

    Uses Sina Finance (has constituent stocks) with Xueqiu fallback
    (board-level overview only) for boards Sina doesn't cover.
    """
    matches = _fuzzy_match_boards(sector_name)

    # If no exact/contains match, check Xueqiu first before falling back to
    # substring matches which may be less relevant
    has_good_match = any(
        m["name"] == sector_name or sector_name in m["name"]
        for m in matches
    )
    if not has_good_match:
        xq_result = _xq_sector_detail(sector_name, errors)
        if xq_result.get("name"):
            return xq_result

    if not matches:
        return {"error": f"未找到板块「{sector_name}」的数据", "errors": errors}

    # Pick best match: prefer exact name, then shortest name containing keyword
    exact = [m for m in matches if m["name"] == sector_name]
    if exact:
        board = exact[0]
    else:
        matches.sort(key=lambda m: len(m["name"]))
        board = matches[0]
        print(f"[板块] 模糊匹配: '{sector_name}' → '{board['name']}' ({board['type']})",
              file=sys.stderr)

    # Fetch constituent stocks
    try:
        stocks = _fetch_board_stocks(board["code"], limit)
    except Exception as e:
        errors.append(f"板块成分股获取失败: {e}")
        stocks = []

    if not stocks:
        # Sina board found but constituent stock fetch failed → try Xueqiu
        return _xq_sector_detail(sector_name, errors)

    # Aggregate statistics
    changes = [s["changePct"] for s in stocks if s.get("changePct") is not None]
    avg_change = sum(changes) / len(changes) if changes else 0
    up_count = sum(1 for c in changes if c > 0)
    down_count = sum(1 for c in changes if c < 0)

    return {
        "action": "detail",
        "name": board["name"],
        "sector": board["name"],
        "change_pct": round(avg_change, 2),
        "avgChange": round(avg_change, 2),
        "stockCount": len(stocks),
        "upCount": up_count,
        "downCount": down_count,
        "stocks": stocks,
        "errors": errors if errors else None,
    }


def _xq_sector_detail(sector_name: str, errors: list[str]) -> dict:
    """Fallback: get board overview from Xueqiu (no constituent stocks)."""
    try:
        bk_boards = _xq_search_boards(sector_name)
    except Exception as e:
        errors.append(f"雪球板块搜索失败: {e}")
        bk_boards = []

    if not bk_boards:
        return {"error": f"未找到板块「{sector_name}」的数据", "errors": errors}

    board = bk_boards[0]
    if board["name"] != sector_name:
        print(f"[板块] 雪球匹配: '{sector_name}' → '{board['name']}' ({board['code']})",
              file=sys.stderr)

    try:
        quote = _xq_board_quote(board["code"])
    except Exception as e:
        errors.append(f"雪球板块行情失败: {e}")
        quote = None

    if not quote:
        return {"error": f"未找到板块「{sector_name}」的数据", "errors": errors}

    pct = _safe_float(quote.get("percent")) or 0
    return {
        "action": "detail",
        "name": quote.get("name", board["name"]),
        "sector": quote.get("name", board["name"]),
        "change_pct": round(pct, 2),
        "avgChange": round(pct, 2),
        "stockCount": 0,
        "stocks": [],
        "source": "xueqiu",
        "errors": errors if errors else None,
    }


def _sector_history(sector_name: str, days: int, errors: list[str]) -> dict:
    """Get sector historical K-line data via akshare."""
    try:
        import akshare as ak
    except ImportError:
        return {"error": "akshare 未安装，无法获取板块历史数据"}

    from datetime import datetime, timedelta

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days * 2)).strftime("%Y%m%d")

    history: list[dict] = []

    # Try industry board first
    try:
        df = ak.stock_board_industry_hist_em(
            symbol=sector_name,
            period="日k",
            start_date=start_date,
            end_date=end_date,
            adjust="",
        )
        if df is not None and not df.empty:
            df = df.sort_values("日期").tail(days)
            for _, row in df.iterrows():
                history.append({
                    "date": str(row.get("日期", "")),
                    "open": _safe_float(row.get("开盘")),
                    "high": _safe_float(row.get("最高")),
                    "low": _safe_float(row.get("最低")),
                    "close": _safe_float(row.get("收盘")),
                    "volume": _safe_float(row.get("成交量")),
                    "amount": _safe_float(row.get("成交额")),
                    "changePct": _safe_float(row.get("涨跌幅")),
                })
    except Exception as e:
        errors.append(f"行业板块历史获取失败: {e}")

    # If industry board not found, try concept board
    if not history:
        try:
            df = ak.stock_board_concept_hist_em(
                symbol=sector_name,
                period="日k",
                start_date=start_date,
                end_date=end_date,
                adjust="",
            )
            if df is not None and not df.empty:
                df = df.sort_values("日期").tail(days)
                for _, row in df.iterrows():
                    history.append({
                        "date": str(row.get("日期", "")),
                        "open": _safe_float(row.get("开盘")),
                        "high": _safe_float(row.get("最高")),
                        "low": _safe_float(row.get("最低")),
                        "close": _safe_float(row.get("收盘")),
                        "volume": _safe_float(row.get("成交量")),
                        "amount": _safe_float(row.get("成交额")),
                        "changePct": _safe_float(row.get("涨跌幅")),
                    })
        except Exception as e:
            errors.append(f"概念板块历史获取失败: {e}")

    if not history:
        return {
            "error": f"未找到板块「{sector_name}」的历史数据，请确认板块名称是否准确",
            "errors": errors,
        }

    # Calculate summary stats
    changes = [h["changePct"] for h in history if h.get("changePct") is not None]
    total_change = 0
    if history and history[0].get("open") and history[-1].get("close"):
        total_change = round((history[-1]["close"] / history[0]["open"] - 1) * 100, 2)

    return {
        "action": "history",
        "sector": sector_name,
        "days": len(history),
        "totalChangePct": total_change,
        "avgDailyChangePct": round(sum(changes) / len(changes), 2) if changes else 0,
        "maxGain": round(max(changes), 2) if changes else 0,
        "maxLoss": round(min(changes), 2) if changes else 0,
        "history": history,
        "errors": errors if errors else None,
    }


def _safe_float(val: Any) -> float | None:
    """Convert value to float, returning None on failure."""
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN check
            return None
        return round(f, 4)
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> int:
    """Convert value to int, returning 0 on failure."""
    try:
        return int(val)
    except (ValueError, TypeError):
        return 0


# ── CLI entry point ───────────────────────────────────────────────

if __name__ == "__main__":
    try:
        if "--json" in sys.argv:
            raw = sys.stdin.read()
            cfg = json.loads(raw) if raw.strip() else {}
        else:
            cfg = json.loads(os.environ.get("QUANT_CONFIG", "{}"))

        result = run_sector_analysis(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception:
        traceback.print_exc(file=sys.stderr)
        print(json.dumps({"error": traceback.format_exc()}, ensure_ascii=False))
        sys.exit(1)
