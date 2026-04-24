"""
Local CSV-based sector index — maps sectors <-> stocks from a user-maintained CSV file.

The CSV file (`data/stock_sector.csv`) has columns:
    stock_code,stock_name,sector,free_float_mv

This module provides:
    - get_sector_list() → all sectors with stock counts
    - get_sector_members(sector_name) → list of stocks in a sector
    - get_stock_sector(stock_code) → sector name for a stock
    - get_index_stats() → summary statistics
    - reload() → re-read the CSV (after user updates the file)
"""

from __future__ import annotations

import csv
import os
import sys
import time
from typing import Any

_PYTHON_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_PYTHON_ROOT, "data")
_DEFAULT_CSV = os.path.join(_DATA_DIR, "stock_sector.csv")


def _log(msg: str) -> None:
    print(f"[sector_index] {msg}", file=sys.stderr)


# ── In-memory index ──────────────────────────────────────────────

# sector_name → list of {code, name, free_float_mv}
_sector_to_stocks: dict[str, list[dict[str, Any]]] = {}
# 6-digit code → sector_name
_stock_to_sector: dict[str, str] = {}
# 6-digit code → {code, name, sector, free_float_mv}
_stock_info: dict[str, dict[str, Any]] = {}
# metadata
_loaded_at: float = 0.0
_csv_path: str = ""
_total_stocks: int = 0


def _normalize_code(raw: str) -> str:
    """Strip exchange suffix (.SZ/.SH/.BJ) to get 6-digit code."""
    return raw.split(".")[0].strip() if "." in raw else raw.strip()


def _load_csv(csv_path: str | None = None) -> None:
    """Parse the CSV and build in-memory indexes."""
    global _sector_to_stocks, _stock_to_sector, _stock_info
    global _loaded_at, _csv_path, _total_stocks

    path = csv_path or _DEFAULT_CSV
    if not os.path.exists(path):
        _log(f"CSV not found: {path}")
        return

    sector_map: dict[str, list[dict[str, Any]]] = {}
    code_map: dict[str, str] = {}
    info_map: dict[str, dict[str, Any]] = {}
    count = 0

    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw_code = row.get("stock_code", "").strip()
            name = row.get("stock_name", "").strip()
            sector = row.get("sector", "").strip()
            fmv_str = row.get("free_float_mv", "0").strip()

            if not raw_code or not sector:
                continue

            code = _normalize_code(raw_code)
            try:
                free_float_mv = float(fmv_str) if fmv_str else 0.0
            except (ValueError, TypeError):
                free_float_mv = 0.0

            entry = {
                "code": code,
                "name": name,
                "sector": sector,
                "free_float_mv": free_float_mv,
            }

            sector_map.setdefault(sector, []).append(entry)
            code_map[code] = sector
            info_map[code] = entry
            count += 1

    _sector_to_stocks = sector_map
    _stock_to_sector = code_map
    _stock_info = info_map
    _loaded_at = time.time()
    _csv_path = path
    _total_stocks = count
    _log(f"Loaded {count} stocks across {len(sector_map)} sectors from {path}")


def _ensure_loaded() -> None:
    """Auto-load on first access."""
    if not _loaded_at:
        _load_csv()


# ── Public API ────────────────────────────────────────────────────


def reload(csv_path: str | None = None) -> dict[str, Any]:
    """Re-read the CSV and rebuild indexes. Returns stats."""
    _load_csv(csv_path)
    return get_index_stats()


def get_sector_list() -> list[dict[str, Any]]:
    """Return all sectors with stock counts, sorted by count desc."""
    _ensure_loaded()
    result = []
    for sector, stocks in _sector_to_stocks.items():
        total_mv = sum(s.get("free_float_mv", 0) for s in stocks)
        result.append({
            "sector": sector,
            "count": len(stocks),
            "total_free_float_mv": total_mv,
        })
    result.sort(key=lambda x: x["count"], reverse=True)
    return result


def get_sector_members(sector_name: str) -> list[dict[str, Any]]:
    """Return stocks in a given sector. Empty list if sector not found."""
    _ensure_loaded()
    # Exact match first
    members = _sector_to_stocks.get(sector_name, [])
    if members:
        return members
    # Fuzzy match: sector_name is a substring of a known sector, or vice versa
    for key, stocks in _sector_to_stocks.items():
        if sector_name in key or key in sector_name:
            return stocks
    return []


def get_sector_member_codes(sector_name: str) -> list[str]:
    """Return just the 6-digit codes for a sector."""
    return [s["code"] for s in get_sector_members(sector_name)]


def get_stock_sector(stock_code: str) -> str:
    """Return the sector name for a given stock code, or empty string."""
    _ensure_loaded()
    code = _normalize_code(stock_code)
    return _stock_to_sector.get(code, "")


def get_stock_info(stock_code: str) -> dict[str, Any] | None:
    """Return full stock info dict, or None."""
    _ensure_loaded()
    code = _normalize_code(stock_code)
    return _stock_info.get(code)


def get_index_stats() -> dict[str, Any]:
    """Return summary statistics about the loaded index."""
    _ensure_loaded()
    return {
        "total_stocks": _total_stocks,
        "total_sectors": len(_sector_to_stocks),
        "csv_path": _csv_path,
        "loaded_at": _loaded_at,
        "sectors": get_sector_list(),
    }


def is_loaded() -> bool:
    """Check if the CSV has been loaded."""
    return _loaded_at > 0


# ── CLI / Script Entry Point ─────────────────────────────────────
# Can be called via: python -m data_providers.sector_index
# or from the TS bridge via runPythonScript("data_providers/sector_index.py", ...)

if __name__ == "__main__":
    import json as _json

    # Read JSON from stdin (same pattern as scripts/*.py)
    try:
        raw = sys.stdin.read().strip()
        params = _json.loads(raw) if raw else {}
    except Exception:
        params = {}

    action = params.get("action", "stats")
    csv_path = params.get("csv_path")

    if csv_path:
        _load_csv(csv_path)
    else:
        _load_csv()

    if action == "stats":
        print(_json.dumps(get_index_stats(), ensure_ascii=False, indent=2))

    elif action == "sector_list":
        print(_json.dumps(get_sector_list(), ensure_ascii=False, indent=2))

    elif action == "sector_members":
        sector = params.get("sector", "")
        members = get_sector_members(sector)
        print(_json.dumps({
            "sector": sector,
            "count": len(members),
            "members": members,
        }, ensure_ascii=False, indent=2))

    elif action == "stock_sector":
        code = params.get("code", "")
        sector = get_stock_sector(code)
        info = get_stock_info(code)
        print(_json.dumps({
            "code": code,
            "sector": sector,
            "info": info,
        }, ensure_ascii=False, indent=2))

    elif action == "reload":
        stats = reload(csv_path)
        print(_json.dumps(stats, ensure_ascii=False, indent=2))

    elif action == "upload":
        # Accept CSV content as a string and overwrite the file
        content = params.get("content", "")
        if not content:
            print(_json.dumps({"error": "缺少 content 参数"}, ensure_ascii=False))
            sys.exit(1)
        target = csv_path or _DEFAULT_CSV
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "w", encoding="utf-8") as f:
            f.write(content)
        stats = reload(target)
        print(_json.dumps({
            "message": f"已更新 CSV ({stats['total_stocks']} stocks, {stats['total_sectors']} sectors)",
            **stats,
        }, ensure_ascii=False, indent=2))

    else:
        print(_json.dumps({"error": f"未知 action: {action}"}, ensure_ascii=False))
        sys.exit(1)
