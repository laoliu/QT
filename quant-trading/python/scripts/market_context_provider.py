"""
market_context_provider — unified data provider for MarketContextService (TS).

Single-process JSON bridge: called by ``runPythonScript`` with an action param.

Supported actions:
    core_snapshot   — sentiment + lianban pool (high-frequency, <5s)
    slow_snapshot   — macro regime + catalyst + ST statuses (low-frequency)
    auction_snapshot — auction signals for current watchlist
    full_snapshot   — all of the above combined
    symbol_context  — per-symbol enrichment (sentiment + lianban + catalyst + ST)
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone
from typing import Any
from data_providers.yahoo_transport import YAHOO_PROXY  # Capture before domestic imports clear proxies.

# Proxy bypass for Chinese data APIs
for _k in (
    "http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
    "all_proxy", "ALL_PROXY",
):
    os.environ.pop(_k, None)
os.environ["no_proxy"] = "*"
os.environ["NO_PROXY"] = "*"

# ── Engine imports (lazy to minimise startup cost) ────────────────


def _get_sentiment() -> dict[str, Any]:
    """Fetch composite sentiment score via market_sentiment engine."""
    try:
        from engines.market_sentiment import sentiment_score
        data = sentiment_score()
        if not data or "error" in data:
            return {}

        score = data.get("score", 50)
        tone_map = {
            (80, 101): "强势",
            (60, 80): "偏多",
            (40, 60): "中性",
            (20, 40): "偏空",
            (0, 20): "弱势",
        }
        tone = "中性"
        for (lo, hi), label in tone_map.items():
            if lo <= score < hi:
                tone = label
                break

        return {
            "score": score,
            "tone": data.get("tone", tone),
            "zbgc_rate": data.get("zbgc_rate", 0),
            "promotion_rate": data.get("promotion_rate", {}).get("rate", 0)
                if isinstance(data.get("promotion_rate"), dict)
                else data.get("promotion_rate", 0),
            "zt_dt_ratio": data.get("zhangting_dieting_ratio", 1),
            "total_zt": data.get("total_zt", 0),
            "highest_board": data.get("highest_board", 0),
        }
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return {}


def _get_lianban_pool() -> dict[str, dict[str, Any]]:
    """Fetch the limit-up (涨停) pool and compute per-stock lianban entries."""
    try:
        from engines.lianban import fetch_zt_pool, board_quality_score
        pool = fetch_zt_pool()
        if not pool or not isinstance(pool, list):
            return {}

        result: dict[str, dict[str, Any]] = {}
        for row in pool:
            code = str(row.get("code", row.get("代码", "")))
            if not code:
                continue
            level = int(row.get("连板数", row.get("level", 1)))
            quality = 50.0
            try:
                quality = board_quality_score(row)
            except Exception:
                pass
            result[code] = {
                "level": level,
                "board_quality": quality,
                "in_pool": True,
            }
        return result
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return {}


def _get_macro_regime() -> dict[str, str]:
    """Classify current macro regime and volatility."""
    try:
        from engines.macro_economic import _analyze_macro_regime
        data = _analyze_macro_regime("cn")
        if not data or "error" in data:
            return {}
        regime = data.get("regime", "unknown")

        # Map to LiveEngine regime labels
        regime_map = {
            "recovery": "BULL",
            "overheating": "VOLATILE",
            "stagflation": "BEAR",
            "recession": "BEAR",
        }
        mapped = regime_map.get(regime, "SIDEWAYS")

        # Simple volatility heuristic based on regime
        vol_map = {
            "recovery": "NORMAL",
            "overheating": "HIGH",
            "stagflation": "HIGH",
            "recession": "EXTREME",
        }
        volatility = vol_map.get(regime, "NORMAL")

        return {"macro_regime": mapped, "volatility_regime": volatility}
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return {}


def _get_catalysts() -> dict[str, dict[str, Any]] | None:
    """Scan for event catalysts (US movers → A-share mapping, earnings)."""
    try:
        from engines.event_catalyst_engine import _scan
        data = _scan({"min_change_pct": 3.0, "include_news": False})
        if not data or "error" in data:
            return None

        result: dict[str, dict[str, Any]] = {}
        for pick in data.get("picks", []):
            catalysts = pick.get("catalysts", [])
            result[pick["code"]] = {
                "event_type": "us_mover",
                "days_until": 0,
                "impact": "high" if any(abs(c.get("usChangePct", 0)) > 5 for c in catalysts) else "medium",
                "description": "; ".join(pick.get("signals", [])),
            }
        return result
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return None


def _get_st_statuses() -> dict[str, dict[str, Any]] | None:
    """Scan all ST restructuring stocks and their lifecycle stages."""
    try:
        from engines.st_restructuring import scan_all_restructuring
        data = scan_all_restructuring(days=90)
        if not data:
            return {}

        result: dict[str, dict[str, Any]] = {}
        for code, record in data.items():
            info = record.to_dict()
            stage = info.get("current_stage", "UNKNOWN")
            # Risk classification
            high_risk_stages = {"MAIN-1", "MAIN-2", "MAIN-3", "MAIN-4"}
            extreme_stages = {"MAIN-5", "MAIN-6", "MAIN-7"}
            if stage in extreme_stages:
                risk = "extreme"
            elif stage in high_risk_stages:
                risk = "high"
            elif stage.startswith("PRE"):
                risk = "medium"
            else:
                risk = "low"

            result[code] = {
                "stage": stage,
                "next_milestone": "",
                "risk_level": risk,
            }
        return result
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return None


def _get_auction_signals(params: dict | None = None) -> dict[str, dict[str, Any]] | None:
    """Get current auction signals (only meaningful during auction windows)."""
    try:
        params = params or {}
        symbols = params.get("symbols", [])
        snapshots = params.get("snapshots")
        if not symbols and not snapshots:
            return {}
        if not snapshots:
            from aipha.data.calendar import CNStockCalendar
            now = datetime.now(timezone(timedelta(hours=8)))
            hm = now.hour * 100 + now.minute
            if not CNStockCalendar().is_trading_day(now.date()) or not (915 <= hm <= 930):
                return {}
        from engines.auction_sniper import scan_auction_sniper
        signals = scan_auction_sniper(symbols=symbols or None, snapshots_data=snapshots)
        return {signal["symbol"]: signal for signal in signals}
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return None


# ── Action handlers ───────────────────────────────────────────────


def handle_core_snapshot(_params: dict) -> dict[str, Any]:
    """High-frequency snapshot: sentiment + lianban."""
    return {
        "sentiment": _get_sentiment(),
        "lianban_pool": _get_lianban_pool(),
    }


def handle_macro_snapshot(_params: dict) -> dict[str, Any]:
    return _get_macro_regime()


def handle_catalyst_snapshot(_params: dict) -> dict[str, Any]:
    return {"catalysts": _get_catalysts()}


def handle_st_snapshot(_params: dict) -> dict[str, Any]:
    return {"st_statuses": _get_st_statuses()}


def handle_slow_snapshot(_params: dict) -> dict[str, Any]:
    """Low-frequency snapshot: macro + catalyst + ST."""
    regime = _get_macro_regime()
    return {
        **regime,
        "catalysts": _get_catalysts(),
        "st_statuses": _get_st_statuses(),
    }


def handle_auction_snapshot(params: dict) -> dict[str, Any]:
    """Auction-window signals."""
    return {
        "auction_signals": _get_auction_signals(params),
    }


def handle_full_snapshot(params: dict) -> dict[str, Any]:
    """Complete snapshot combining all modules."""
    core = handle_core_snapshot(params)
    slow = handle_slow_snapshot(params)
    auction = handle_auction_snapshot(params)
    return {**core, **slow, **auction}


def handle_symbol_context(params: dict) -> dict[str, Any]:
    """Per-symbol enrichment."""
    symbol = params.get("symbol", "")
    if not symbol:
        return {"error": "symbol required"}

    sentiment = _get_sentiment()
    lianban_pool = _get_lianban_pool()
    regime = _get_macro_regime()
    catalysts = _get_catalysts()
    st_statuses = _get_st_statuses()

    return {
        "sentiment": sentiment,
        "lianban": lianban_pool.get(symbol),
        "macro_regime": regime.get("macro_regime", "SIDEWAYS"),
        "volatility_regime": regime.get("volatility_regime", "NORMAL"),
        "catalyst": (catalysts or {}).get(symbol),
        "st_status": (st_statuses or {}).get(symbol),
    }


# ── Main dispatch ─────────────────────────────────────────────────

ACTIONS = {
    "core_snapshot": handle_core_snapshot,
    "slow_snapshot": handle_slow_snapshot,
    "macro_snapshot": handle_macro_snapshot,
    "catalyst_snapshot": handle_catalyst_snapshot,
    "st_snapshot": handle_st_snapshot,
    "auction_snapshot": handle_auction_snapshot,
    "full_snapshot": handle_full_snapshot,
    "symbol_context": handle_symbol_context,
}


def main():
    raw = sys.stdin.read()
    try:
        params = json.loads(raw)
    except json.JSONDecodeError:
        json.dump({"ok": False, "error": "Invalid JSON input"}, sys.stdout, ensure_ascii=False)
        sys.exit(1)

    action = params.get("action", "core_snapshot")
    handler = ACTIONS.get(action)
    if not handler:
        json.dump(
            {"ok": False, "error": f"Unknown action: {action}. Valid: {list(ACTIONS.keys())}"},
            sys.stdout,
            ensure_ascii=False,
        )
        sys.exit(1)

    try:
        result = handler(params)
        json.dump(result, sys.stdout, ensure_ascii=False)
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        json.dump({"ok": False, "error": str(exc)}, sys.stdout, ensure_ascii=False)
        sys.exit(1)


if __name__ == "__main__":
    main()
