"""Read real paper trading state from disk.

Reads state.json / paper_state.json and nav_history.jsonl produced by
the Paper Engine (StateManager) and converts them to the snapshot format
expected by the trading API router.

Container path mapping:
  Host: /opt/aipha-q/data/paper/state/{combo_id}/
  Docker: /app/data/paper_state/{combo_id}/
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default base dir — overridden by env AIPHA_PAPER_STATE_DIR
_DEFAULT_STATE_BASE = os.environ.get(
    "AIPHA_PAPER_STATE_DIR", "data/paper_state"
)


def _state_base() -> Path:
    """Return the paper state root directory."""
    return Path(_DEFAULT_STATE_BASE)


def _find_state_file(combo_dir: Path) -> Optional[Path]:
    """Find state.json or paper_state.json in a combo directory."""
    for name in ("state.json", "paper_state.json"):
        p = combo_dir / name
        if p.exists():
            return p
    return None


def discover_state_dirs() -> List[str]:
    """List all combo_ids that have state directories on disk."""
    base = _state_base()
    if not base.is_dir():
        return []
    return sorted(
        d.name for d in base.iterdir()
        if d.is_dir() and (_find_state_file(d) is not None
                           or (d / "nav_history.jsonl").exists())
    )


def has_state_data(portfolio_id: str) -> bool:
    """Check whether a portfolio has any state data on disk.

    Returns True if state.json / paper_state.json or nav_history.jsonl exists.
    """
    combo_dir = _state_base() / portfolio_id
    if not combo_dir.is_dir():
        return False
    return (_find_state_file(combo_dir) is not None
            or (combo_dir / "nav_history.jsonl").exists())


def read_account_state(portfolio_id: str) -> Optional[Dict[str, Any]]:
    """Read account state from state.json or paper_state.json.

    Supports two formats:
    - Format A (StateManager): {"account": {"cash": ..., "positions": [{"symbol","qty","avg_cost"}]}}
    - Format B (flat/R106+): {"cash": ..., "positions": [{"code","shares","avg_cost"}], "nav_history": [...]}

    Returns normalized dict with keys: cash, positions[], initial_capital, nav_history[], trades[].
    Returns None if no state file found.
    """
    combo_dir = _state_base() / portfolio_id
    sf = _find_state_file(combo_dir) if combo_dir.is_dir() else None
    if sf is None:
        return None
    try:
        with open(sf, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("Failed to read state for %s: %s", portfolio_id, e)
        return None

    # Detect format
    if "account" in data:
        # Format A (StateManager)
        acct = data["account"]
        positions = []
        for p in acct.get("positions", []):
            positions.append({
                "symbol": p.get("symbol", ""),
                "qty": int(p.get("qty", 0)),
                "avg_cost": float(p.get("avg_cost", 0)),
            })
        return {
            "cash": float(acct.get("cash", 0)),
            "initial_capital": float(data.get("meta", {}).get("initial_capital", 0)),
            "positions": positions,
            "nav_history": [],  # separate file
            "trades": [],
            "meta": data.get("meta", {}),
        }
    else:
        # Format B (flat / R106/R107/R115)
        positions = []
        for p in data.get("positions", []):
            positions.append({
                "symbol": p.get("code", p.get("symbol", "")),
                "qty": int(p.get("shares", p.get("qty", 0))),
                "avg_cost": float(p.get("avg_cost", 0)),
                "component": p.get("component", ""),
            })
        return {
            "cash": float(data.get("cash", 0)),
            "initial_capital": float(data.get("initial_capital", 0)),
            "positions": positions,
            "nav_history": data.get("nav_history", []),
            "trades": data.get("trades", []),
            "meta": {
                "profile_name": data.get("profile_name", ""),
                "last_update": data.get("last_update", ""),
                "days_traded": data.get("days_traded", 0),
                "peak_nav": data.get("peak_nav"),
            },
        }


def read_nav_history(portfolio_id: str) -> List[Dict[str, Any]]:
    """Read nav_history.jsonl → list of {date, nav, total_return_pct}.

    Each line is a JSON object with at least: date, nav, total_return_pct.
    """
    nav_path = _state_base() / portfolio_id / "nav_history.jsonl"
    if not nav_path.exists():
        return []
    result: List[Dict[str, Any]] = []
    try:
        with open(nav_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    result.append(json.loads(line))
    except Exception as e:
        logger.warning("Failed to read nav_history for %s: %s", portfolio_id, e)
    return result


def build_real_snapshot(
    row: Dict[str, Any],
) -> Dict[str, Any]:
    """Build snapshot from real state files, matching _build_snapshot format.

    Returns dict with keys: positions[], orders[], pnl[], summary{}.
    If no state files found, returns empty/zeroed structure.
    """
    portfolio_id = row.get("portfolio_id", "")
    initial_capital = float(row.get("initial_capital", 1_000_000) or 1_000_000)

    # -- Read account state (normalized) --
    state_data = read_account_state(portfolio_id)
    positions: List[Dict[str, Any]] = []
    total_market_value = 0.0
    cash = initial_capital

    if state_data:
        cash = state_data.get("cash", 0)
        if state_data.get("initial_capital"):
            initial_capital = state_data["initial_capital"]
        raw_positions = state_data.get("positions", [])

        for p in raw_positions:
            symbol = p.get("symbol", "")
            qty = int(p.get("qty", 0))
            avg_cost = float(p.get("avg_cost", 0))
            market_price = avg_cost  # no live quotes
            market_value = round(qty * market_price, 2)
            total_market_value += market_value
            positions.append({
                "symbol": symbol,
                "name": p.get("component", symbol),
                "qty": qty,
                "avg_cost": round(avg_cost, 4),
                "market_price": round(market_price, 4),
                "market_value": market_value,
                "pnl": 0.0,
                "pnl_pct": 0.0,
                "weight": 0.0,
                "industry": p.get("component", ""),
            })

        nav_est = cash + total_market_value
        for pos in positions:
            pos["weight"] = round(
                pos["market_value"] / nav_est, 4
            ) if nav_est > 0 else 0.0

    # -- NAV history: prefer embedded, fallback to separate file --
    embedded_nav = (state_data or {}).get("nav_history", [])
    pnl_points: List[Dict[str, Any]] = []

    if embedded_nav:
        # Format B embedded nav_history
        for rec in embedded_nav:
            nav_val = float(rec.get("nav", initial_capital))
            cum_pnl = round(nav_val - initial_capital, 2)
            pnl_points.append({
                "date": rec.get("date", ""),
                "daily_pnl": 0.0,
                "cumulative_pnl": cum_pnl,
                "nav": round(nav_val / initial_capital, 4) if initial_capital else 1.0,
            })
        # Compute daily PnL from sequential differences
        for i in range(len(pnl_points) - 1, 0, -1):
            pnl_points[i]["daily_pnl"] = round(
                pnl_points[i]["cumulative_pnl"] - pnl_points[i - 1]["cumulative_pnl"], 2
            )
    else:
        # Separate nav_history.jsonl
        nav_records = read_nav_history(portfolio_id)
        prev_nav_val = initial_capital
        for rec in nav_records:
            nav_val = float(rec.get("nav", initial_capital))
            cum_pnl = round(nav_val - initial_capital, 2)
            daily_pnl = round(nav_val - prev_nav_val, 2)
            pnl_points.append({
                "date": rec.get("date", ""),
                "daily_pnl": daily_pnl,
                "cumulative_pnl": cum_pnl,
                "nav": round(nav_val / initial_capital, 4) if initial_capital else 1.0,
            })
            prev_nav_val = nav_val

    # -- Orders from trades[] (Format B) --
    orders: List[Dict[str, Any]] = []
    for t in (state_data or {}).get("trades", []):
        orders.append({
            "order_id": t.get("order_id", ""),
            "symbol": t.get("code", t.get("symbol", "")),
            "name": t.get("code", ""),
            "side": t.get("side", t.get("direction", "")),
            "qty": int(t.get("shares", t.get("qty", 0))),
            "price": float(t.get("price", 0)),
            "filled_qty": int(t.get("shares", t.get("qty", 0))),
            "filled_price": float(t.get("price", 0)),
            "status": "filled",
            "created_at": t.get("date", t.get("datetime", "")),
        })

    # -- Summary --
    if pnl_points:
        last = pnl_points[-1]
        current_nav = last["nav"]
        total_pnl = last["cumulative_pnl"]
    else:
        nav_est = cash + total_market_value
        current_nav = round(nav_est / initial_capital, 4) if initial_capital else 1.0
        total_pnl = round(nav_est - initial_capital, 2)

    return {
        "positions": positions,
        "orders": orders,
        "pnl": pnl_points,
        "summary": {
            "current_nav": current_nav,
            "pnl": total_pnl,
            "pnl_pct": round(total_pnl / initial_capital, 4) if initial_capital else 0.0,
            "total_trades": len(orders),
            "positions_count": len(positions),
        },
    }
