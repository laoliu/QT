"""
Polymarket engine — prediction market odds lookup.

Fetches data from Polymarket's public CLOB API (no auth required).

API docs: https://docs.polymarket.com/

Input (JSON via stdin):
  {
    "action": "search" | "trending" | "category" | "detail",
    "keyword": "...",
    "category": "economics" | "politics" | "crypto" | "sports" | "tech" | "all",
    "eventId": "...",
    "limit": 10,
    "sortBy": "volume" | "liquidity" | "newest" | "ending_soon"
  }

Output: JSON with prediction market data.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
import urllib.request
import urllib.parse
from typing import Any
from datetime import datetime

# ── Proxy bypass ──────────────────────────────────────────────────────────────
for _k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
           "all_proxy", "ALL_PROXY"):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

# Polymarket public API
_GAMMA_BASE = "https://gamma-api.polymarket.com"
_CLOB_BASE = "https://clob.polymarket.com"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
}

_CATEGORY_MAP = {
    "economics": "Economics",
    "politics": "Politics",
    "crypto": "Crypto",
    "sports": "Sports",
    "tech": "Science & Tech",
    "all": None,
}


def _fetch_json(url: str, timeout: int = 15) -> Any:
    """Fetch JSON from a URL."""
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _format_market(m: dict) -> dict:
    """Format a market entry to a clean output."""
    outcomes = m.get("outcomes", [])
    prices = m.get("outcomePrices", [])

    # Parse prices (comma-separated string or list)
    if isinstance(prices, str):
        prices = [p.strip() for p in prices.split(",")]

    odds = []
    for i, outcome in enumerate(outcomes if isinstance(outcomes, list) else []):
        price = float(prices[i]) if i < len(prices) else 0
        if isinstance(outcome, str):
            odds.append({
                "outcome": outcome,
                "probability": round(price * 100, 1),
            })

    return {
        "id": m.get("id", ""),
        "question": m.get("question", ""),
        "description": (m.get("description", "") or "")[:200],
        "odds": odds,
        "volume": m.get("volume", 0),
        "volumeFormatted": _format_volume(m.get("volume", 0)),
        "liquidity": m.get("liquidity", 0),
        "endDate": m.get("endDate", ""),
        "active": m.get("active", True),
        "category": m.get("groupItemTitle", m.get("category", "")),
    }


def _format_volume(vol: Any) -> str:
    """Format volume to human-readable."""
    try:
        v = float(vol)
    except (ValueError, TypeError):
        return "0"
    if v >= 1_000_000:
        return f"${v/1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v/1_000:.1f}K"
    return f"${v:.0f}"


def _search(config: dict) -> dict:
    """Search prediction markets."""
    keyword = config.get("keyword", "")
    limit = config.get("limit", 10)

    if not keyword:
        return {"error": "keyword is required for search"}

    try:
        encoded = urllib.parse.quote(keyword)
        url = f"{_GAMMA_BASE}/markets?_limit={limit}&closed=false&search={encoded}"
        data = _fetch_json(url)

        markets = [_format_market(m) for m in (data if isinstance(data, list) else [])]

        return {
            "keyword": keyword,
            "count": len(markets),
            "markets": markets,
        }
    except Exception as e:
        return {"error": f"搜索失败: {e}"}


def _trending(config: dict) -> dict:
    """Get trending prediction markets."""
    limit = config.get("limit", 10)
    sort_by = config.get("sortBy", "volume")

    try:
        sort_map = {
            "volume": "volume",
            "liquidity": "liquidity",
            "newest": "startDate",
            "ending_soon": "endDate",
        }
        sort_field = sort_map.get(sort_by, "volume")
        order = "asc" if sort_by == "ending_soon" else "desc"

        url = f"{_GAMMA_BASE}/markets?_limit={limit}&closed=false&active=true&_order={order}&_sort={sort_field}"
        data = _fetch_json(url)

        markets = [_format_market(m) for m in (data if isinstance(data, list) else [])]

        return {
            "sortBy": sort_by,
            "count": len(markets),
            "markets": markets,
        }
    except Exception as e:
        return {"error": f"获取热门事件失败: {e}"}


def _category(config: dict) -> dict:
    """Browse by category."""
    cat = config.get("category", "all")
    limit = config.get("limit", 10)

    try:
        tag = _CATEGORY_MAP.get(cat)
        url = f"{_GAMMA_BASE}/markets?_limit={limit}&closed=false&active=true&_order=desc&_sort=volume"
        if tag:
            url += f"&tag={urllib.parse.quote(tag)}"

        data = _fetch_json(url)
        markets = [_format_market(m) for m in (data if isinstance(data, list) else [])]

        return {
            "category": cat,
            "count": len(markets),
            "markets": markets,
        }
    except Exception as e:
        return {"error": f"获取分类数据失败: {e}"}


def _detail(config: dict) -> dict:
    """Get detailed info about a specific market."""
    event_id = config.get("eventId", "")

    if not event_id:
        return {"error": "eventId is required for detail"}

    try:
        url = f"{_GAMMA_BASE}/markets/{urllib.parse.quote(str(event_id))}"
        data = _fetch_json(url)

        market = _format_market(data)

        # Add extra detail fields
        market["fullDescription"] = (data.get("description", "") or "")[:1000]
        market["resolutionSource"] = data.get("resolutionSource", "")
        market["startDate"] = data.get("startDate", "")
        market["createdAt"] = data.get("createdAt", "")

        return market
    except Exception as e:
        return {"error": f"获取事件详情失败: {e}"}


# ── Main ─────────────────────────────────────────────────────────

ACTIONS = {
    "search": _search,
    "trending": _trending,
    "category": _category,
    "detail": _detail,
}


def main():
    config = json.loads(sys.stdin.read())
    action = config.get("action", "trending")

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
