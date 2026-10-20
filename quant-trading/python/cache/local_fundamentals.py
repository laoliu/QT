"""Current fundamental snapshots from the shared TuShare cache, without network I/O.

Source dates remain attached to each snapshot. These are current cache views,
not authenticated historical point-in-time research snapshots.
"""
from __future__ import annotations

import math
import re
import sys
from datetime import datetime
from pathlib import Path


def cache_dir() -> Path:
    from aipha.config import get_cache_dir
    return get_cache_dir() / "equity_tushare"


def coverage() -> dict:
    root = cache_dir()
    datasets = {name: {"files": 0, "unique_stocks": 0}
                for name in ("fina_indicator", "income", "balancesheet", "cashflow")}
    stocks = {name: set() for name in datasets}
    valuation_files = 0
    latest_partition = None
    try:
        for path in root.glob("*.parquet"):
            match = re.fullmatch(r"(fina_indicator|income|balancesheet|cashflow)_(?:pit_v\d+_)?(\d{6})_(SH|SZ|BJ)(?:_(?:\d{8}|all)_(?:\d{8}|all))?", path.stem)
            if match:
                name, code, exchange = match.groups()
                datasets[name]["files"] += 1
                stocks[name].add(code + "." + exchange)
            match = re.fullmatch(r"daily_basic_(\d{6}|\d{8})", path.stem)
            if match:
                valuation_files += 1
                latest_partition = max(latest_partition or "", match[1])
        for name, codes in stocks.items():
            datasets[name]["unique_stocks"] = len(codes)
        return {"available": root.is_dir(), "cache_dir": str(root), "datasets": datasets,
                "financial_unique_stocks": len(set().union(*stocks.values())),
                "valuation_files": valuation_files, "latest_valuation_partition": latest_partition}
    except OSError as exc:
        return {"available": False, "cache_dir": str(root), "error": str(exc)}


def _read(path: Path):
    import pandas as pd
    try:
        return pd.read_parquet(path)
    except Exception as exc:
        # Never delete a source cache because a read failed.
        print(f"[fundamentals] Cannot read {path.name}: {exc}", file=sys.stderr)
        return pd.DataFrame()


def _date(value) -> str:
    text = str(value).replace("-", "").replace("/", "")[:8]
    try:
        datetime.strptime(text, "%Y%m%d")
        return text
    except ValueError:
        return ""


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def load_current(codes: list[str], as_of: str) -> dict[str, dict]:
    """Load locally available metrics, excluding future trading/announcement dates."""
    root = cache_dir()
    if not root.is_dir() or not codes:
        return {}
    target = _date(as_of)
    if not target:
        return {}
    # Callers use six-digit A-share equity codes, never infer index identities.
    wanted = {c for c in codes if re.fullmatch(r"\d{6}", c)}
    if not wanted:
        return {}
    result: dict[str, dict] = {}

    # Read newest valuation partitions first; stop once all requested stocks exist.
    files = sorted((p for p in root.glob("daily_basic_*.parquet")
                    if re.fullmatch(r"daily_basic_\d{6}(?:\d{2})?", p.stem)
                    and p.stem.removeprefix("daily_basic_")[:6] <= target[:6]), reverse=True)
    previous_month = None
    for path in files:
        month = path.stem.removeprefix("daily_basic_")[:6]
        if previous_month != month and wanted.issubset(result):
            break
        previous_month = month
        frame = _read(path)
        if not {"ts_code", "trade_date"}.issubset(frame.columns):
            continue
        frame = frame[frame["ts_code"].str[:6].isin(wanted)].copy()
        frame["_date"] = frame["trade_date"].map(_date)
        frame = frame[(frame["_date"] != "") & (frame["_date"] <= target)]
        for _, row in frame.sort_values("_date").drop_duplicates("ts_code", keep="last").iterrows():
            code = str(row["ts_code"])[:6]
            if result.get(code, {}).get("valuation_date", "") > row["_date"]:
                continue
            record = {}
            for column, key, scale in [("pe", "pe", 1), ("pe_ttm", "pe_ttm", 1),
                    ("pb", "pb", 1), ("total_mv", "total_market_val", 10000),
                    ("circ_mv", "circulating_market_val", 10000),
                    ("turnover_rate", "turnover_rate", 1), ("dv_ttm", "dividend_yield", 1)]:
                value = _number(row.get(column))
                if value is not None:
                    record[key] = value * scale
            if not record:
                continue
            for key in ("total_market_val", "circulating_market_val"):
                if key in record:
                    record[key + "_yi"] = record[key] / 1e8
            record["valuation_date"] = row["_date"]
            result[code] = record

    basic = _read(root / "stock_basic.parquet") if (root / "stock_basic.parquet").exists() else None
    for code in wanted:
        # Financial indicators are stored per exchange-qualified equity symbol.
        exchange = "SH" if code.startswith("6") else "BJ" if code.startswith(("4", "8", "9")) else "SZ"
        paths = [root / f"fina_indicator_{code}_{exchange}.parquet"]
        paths.extend(sorted(root.glob(f"fina_indicator_pit_v*_{code}_{exchange}_*.parquet")))
        paths = [p for p in paths if p.is_file()]
        record = result.get(code, {})
        if paths:
            import pandas as pd
            frame = pd.concat([_read(p) for p in paths], ignore_index=True)
            if {"ts_code", "end_date", "ann_date"}.issubset(frame.columns):
                frame = frame[frame["ts_code"] == f"{code}.{exchange}"].copy()
                frame["_ann"] = frame["ann_date"].map(_date)
                if "f_ann_date" in frame:
                    effective = frame["f_ann_date"].map(_date)
                    frame["_ann"] = effective.where(effective != "", frame["_ann"])
                frame["_end"] = frame["end_date"].map(_date)
                frame = frame[(frame["_ann"] != "") & (frame["_ann"] <= target)
                              & (frame["_end"] != "") & (frame["_end"] <= target)]
                if not frame.empty:
                    row = frame.sort_values(["_end", "_ann"], kind="stable").iloc[-1]
                    for column, key in [("eps", "eps"), ("bps", "bvps"), ("roe", "roe"), ("roa", "roa")]:
                        value = _number(row.get(column))
                        if value is not None:
                            record[key] = value
                    if any(key in record for key in ("eps", "bvps", "roe", "roa")):
                        record.update(report_date=row["_end"], announcement_date=row["_ann"])
        if record:
            if basic is not None and "ts_code" in basic:
                rows = basic[basic["ts_code"] == f"{code}.{exchange}"]
                if not rows.empty:
                    for key in ("name", "industry"):
                        value = rows.iloc[0].get(key)
                        if isinstance(value, str) and value:
                            record[key] = value
            record.update(source="local_tushare", snapshot_as_of=as_of, pit_verified=False)
            result[code] = record
    return result
