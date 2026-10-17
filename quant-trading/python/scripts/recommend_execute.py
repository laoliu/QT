"""
recommend_execute.py — Apply a combo portfolio to paper trading.

Reads allocations from QUANT_CONFIG env var, resolves latest stock signals
from fitness data, and saves the combo routing config for the auto-trade
scheduler to pick up.

Input (QUANT_CONFIG JSON):
  {
    "allocations": [{"strategy_name": "...", "weight": 0.2}, ...],
    "dry_run": true/false,
    "execute_pct": 100,
    "initial_capital": 10000000,
    "source": "combo-backtest"
  }

Output (JSON to stdout):
  {"ok": true, "applied_strategies": [...], "total_symbols": N, ...}
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ── Paths ──────────────────────────────────────────────────────────

def _get_db_path() -> str:
    data_dir = os.environ.get("QUANT_DATA_DIR") or os.path.join(
        os.path.expanduser("~"), "data"
    )
    return os.path.join(data_dir, "quant-trading.db")


def _get_trading_dir() -> str:
    return os.environ.get("QUANT_TRADING_DIR") or os.path.join(
        os.path.expanduser("~"), "data"
    )


# ── Load latest fitness signals ───────────────────────────────────

def _get_latest_batch_id(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT batch_id FROM global_strategy_ranking ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    return row[0] if row else None


def _load_top_stocks(
    conn: sqlite3.Connection, batch_id: str, strategy_id: str, top_n: int = 10
) -> list[dict]:
    """Load top-N stocks for a strategy from stock_strategy_fitness."""
    rows = conn.execute(
        """
        SELECT symbol, composite_score
        FROM stock_strategy_fitness
        WHERE batch_id = ? AND strategy_id = ?
        ORDER BY composite_score DESC
        LIMIT ?
        """,
        (batch_id, strategy_id, top_n),
    ).fetchall()
    return [{"symbol": r[0], "score": round(r[1], 4)} for r in rows]


# ── Main logic ────────────────────────────────────────────────────

def run(config: dict[str, Any]) -> dict[str, Any]:
    allocations = config.get("allocations", [])
    if not allocations:
        return {"ok": False, "error": "需要 allocations 数组"}

    dry_run = config.get("dry_run", True)
    initial_capital = config.get("initial_capital", 10_000_000)
    source = config.get("source", "combo-backtest")

    db_path = _get_db_path()
    if not os.path.exists(db_path):
        return {"ok": False, "error": f"数据库不存在: {db_path}"}

    conn = sqlite3.connect(db_path)
    try:
        batch_id = _get_latest_batch_id(conn)
        if not batch_id:
            return {"ok": False, "error": "未找到 fitness 批次数据"}

        applied: list[dict] = []
        all_symbols: set[str] = set()

        for alloc in allocations:
            name = alloc.get("strategy_name", "")
            weight = float(alloc.get("weight", 0))
            if not name or weight <= 0:
                continue

            stocks = _load_top_stocks(conn, batch_id, name)
            capital = round(initial_capital * weight, 2)
            all_symbols.update(s["symbol"] for s in stocks)

            applied.append({
                "strategy_name": name,
                "weight": round(weight, 4),
                "capital": capital,
                "stocks": stocks,
                "stock_count": len(stocks),
            })
    finally:
        conn.close()

    if not applied:
        return {"ok": False, "error": "无有效的策略分配"}

    # Build combo config
    combo_config = {
        "source": source,
        "created_at": datetime.now().isoformat(),
        "batch_id": batch_id,
        "initial_capital": initial_capital,
        "dry_run": dry_run,
        "allocations": applied,
        "total_unique_symbols": len(all_symbols),
    }

    # Save combo routing config
    if not dry_run:
        trading_dir = _get_trading_dir()
        os.makedirs(trading_dir, exist_ok=True)
        config_path = os.path.join(trading_dir, "active-combo-config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(combo_config, f, ensure_ascii=False, indent=2)

    return {
        "ok": True,
        "dry_run": dry_run,
        "batch_id": batch_id,
        "applied_strategies": [
            {
                "name": a["strategy_name"],
                "weight": f'{a["weight"]*100:.1f}%',
                "capital": a["capital"],
                "stock_count": a["stock_count"],
                "top_stocks": [s["symbol"] for s in a["stocks"][:5]],
            }
            for a in applied
        ],
        "total_unique_symbols": len(all_symbols),
        "config_saved": not dry_run,
        "message": (
            f"已将 {len(applied)} 个策略组合应用到模拟盘，共覆盖 {len(all_symbols)} 只股票"
            if not dry_run
            else f"预览模式：{len(applied)} 个策略，{len(all_symbols)} 只股票"
        ),
    }


# ── Entry point (Python bridge protocol) ─────────────────────────

if __name__ == "__main__":
    raw = os.environ.get("QUANT_CONFIG", "{}")
    try:
        cfg = json.loads(raw)
    except json.JSONDecodeError:
        cfg = {}

    result = run(cfg)
    print(json.dumps(result, ensure_ascii=False))
