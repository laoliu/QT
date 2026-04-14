#!/usr/bin/env python3
"""
Local Parquet data provider — reads tushare-format parquet files from disk.

Reads monthly parquet caches (daily_{YYYYMM}.parquet, adj_factor_{YYYYMM}.parquet)
from a local data directory (default: G:/qtdata/cache/equity_tushare) to provide
fast historical kline data without any network calls.

Directory layout expected:
  {data_dir}/cache/equity_tushare/
      daily_201201.parquet       # columns: ts_code, trade_date, open, high, low, close, pre_close, change, pct_chg, vol, amount
      daily_201202.parquet
      ...
      adj_factor_201201.parquet  # columns: ts_code, trade_date, adj_factor
      ...
      stock_basic.parquet        # columns: ts_code, symbol, name, area, industry, market, list_date, list_status, ...

Input:  JSON via QUANT_CONFIG env var or stdin.
Output: JSON to stdout.
"""

from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path


DEFAULT_DATA_DIR = "G:/qtdata"


def get_input():
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        return json.loads(config_str)
    return json.loads(sys.stdin.read())


# ── Symbol normalisation ──────────────────────────────────────────


def _to_ts_code(symbol: str, market: str = "cn_stock") -> str:
    """Convert plain symbol → tushare ts_code.

    600519 → 600519.SH;  000858 → 000858.SZ;  301234 → 301234.SZ
    Handles already-suffixed codes (600519.SH) and .SS suffix (Yahoo style).
    """
    code = symbol.strip().upper()
    code = code.replace(".SS", "").replace(".SH", "").replace(".SZ", "")

    if market == "index":
        if code.startswith("399"):
            return f"{code}.SZ"
        return f"{code}.SH"

    if code.startswith(("6", "5", "9")):
        return f"{code}.SH"
    return f"{code}.SZ"


# ── Safe parquet reading ──────────────────────────────────────────


def _safe_read_parquet(path: Path):
    """Read a parquet file safely; return None + delete on corruption."""
    try:
        import pandas as pd
        return pd.read_parquet(path)
    except Exception as exc:
        print(
            f"[local_parquet] Corrupted cache, deleting: {path} ({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


# ── Month range helpers ───────────────────────────────────────────


def _months_between(start: str, end: str) -> list[str]:
    """Return list of YYYYMM strings covering [start, end].

    start/end can be YYYYMMDD or YYYY-MM-DD.
    """
    s = start.replace("-", "")[:6]
    e = end.replace("-", "")[:6]
    sy, sm = int(s[:4]), int(s[4:6])
    ey, em = int(e[:4]), int(e[4:6])

    months = []
    y, m = sy, sm
    while y < ey or (y == ey and m <= em):
        months.append(f"{y:04d}{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return months


# ── Core data loading ─────────────────────────────────────────────


def _load_daily_range(ts_code: str, start: str, end: str, cache_dir: Path):
    """Load daily bars for a single ts_code across monthly parquet files.

    Returns list of KlineBar dicts sorted by date ascending, or empty list.
    """
    import pandas as pd

    start_dt = start.replace("-", "")
    end_dt = end.replace("-", "")
    months = _months_between(start_dt, end_dt)

    dfs = []
    for month in months:
        path = cache_dir / f"daily_{month}.parquet"
        if not path.exists():
            continue
        df = _safe_read_parquet(path)
        if df is None or df.empty:
            continue
        # Filter for the requested stock
        df = df[df["ts_code"] == ts_code]
        if df.empty:
            continue
        dfs.append(df)

    if not dfs:
        return []

    merged = pd.concat(dfs, ignore_index=True)
    merged = merged[(merged["trade_date"] >= start_dt) & (merged["trade_date"] <= end_dt)]
    merged = merged.drop_duplicates(subset=["ts_code", "trade_date"])
    merged = merged.sort_values("trade_date")

    if merged.empty:
        return []

    # ── Apply QFQ (前复权) adjustment ─────────────────────────────
    merged = _apply_qfq(merged, ts_code, months, cache_dir)

    # ── Convert to KlineBar dicts ─────────────────────────────────
    bars = []
    for _, row in merged.iterrows():
        td = str(row["trade_date"])
        # Parse YYYYMMDD → unix ms
        dt = datetime(int(td[:4]), int(td[4:6]), int(td[6:8]))
        ts_ms = int(dt.timestamp() * 1000)
        bars.append({
            "timestamp": ts_ms,
            "date": f"{td[:4]}-{td[4:6]}-{td[6:8]}",
            "open": round(float(row["open"]), 4),
            "high": round(float(row["high"]), 4),
            "low": round(float(row["low"]), 4),
            "close": round(float(row["close"]), 4),
            "volume": int(float(row["vol"])),
            "amount": round(float(row["amount"]), 4) if "amount" in row and row["amount"] else None,
        })
    return bars


def _apply_qfq(df, ts_code: str, months: list[str], cache_dir: Path):
    """Apply 前复权 (forward adjustment) using adj_factor files.

    QFQ formula:  adjusted_price = price * (adj_factor / latest_adj_factor)
    """
    import pandas as pd

    # Load adj_factor data for the same months
    adj_dfs = []
    for month in months:
        path = cache_dir / f"adj_factor_{month}.parquet"
        if not path.exists():
            continue
        adf = _safe_read_parquet(path)
        if adf is None or adf.empty:
            continue
        adf = adf[adf["ts_code"] == ts_code]
        if not adf.empty:
            adj_dfs.append(adf)

    if not adj_dfs:
        # No adj_factor available — return unadjusted
        return df

    adj = pd.concat(adj_dfs, ignore_index=True)
    adj = adj.drop_duplicates(subset=["ts_code", "trade_date"])
    adj = adj.sort_values("trade_date")

    # Latest adj_factor for the stock (denominator for QFQ)
    latest_adj = float(adj["adj_factor"].iloc[-1])
    if latest_adj == 0:
        return df

    # Merge adj_factor into daily data
    df = df.merge(
        adj[["trade_date", "adj_factor"]],
        on="trade_date",
        how="left",
    )

    # Fill missing adj_factor with latest (for dates without adj data)
    df["adj_factor"] = df["adj_factor"].fillna(latest_adj)

    # Apply QFQ
    ratio = df["adj_factor"] / latest_adj
    for col in ("open", "high", "low", "close", "pre_close"):
        if col in df.columns:
            df[col] = df[col] * ratio

    df = df.drop(columns=["adj_factor"], errors="ignore")
    return df


def _resample_weekly(bars: list[dict]) -> list[dict]:
    """Resample daily bars to weekly OHLCV."""
    if not bars:
        return []
    import pandas as pd

    df = pd.DataFrame(bars)
    df["date_dt"] = pd.to_datetime(df["date"])
    df = df.set_index("date_dt")

    weekly = df.resample("W-FRI").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "amount": "sum",
        "timestamp": "last",
        "date": "last",
    }).dropna(subset=["close"])

    return weekly.to_dict("records")


def _resample_monthly(bars: list[dict]) -> list[dict]:
    """Resample daily bars to monthly OHLCV."""
    if not bars:
        return []
    import pandas as pd

    df = pd.DataFrame(bars)
    df["date_dt"] = pd.to_datetime(df["date"])
    df = df.set_index("date_dt")

    monthly = df.resample("ME").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "amount": "sum",
        "timestamp": "last",
        "date": "last",
    }).dropna(subset=["close"])

    return monthly.to_dict("records")


# ── Public API (called via Python bridge) ─────────────────────────


def get_klines(symbol, interval="1d", start="", end="", market="cn_stock", data_dir=""):
    """Load historical klines from local parquet cache.

    Returns list of KlineBar dicts or empty list if not available.
    """
    if not data_dir:
        data_dir = os.environ.get("QTDATA_DIR", DEFAULT_DATA_DIR)

    cache_dir = Path(data_dir) / "cache" / "equity_tushare"
    if not cache_dir.exists():
        return []

    ts_code = _to_ts_code(symbol, market)

    # Default date range: last 3 years
    if not end:
        end = datetime.now().strftime("%Y%m%d")
    if not start:
        start = str(int(end[:4]) - 3) + end[4:]

    bars = _load_daily_range(ts_code, start, end, cache_dir)
    if not bars:
        return []

    # Resample if needed
    if interval == "1w":
        bars = _resample_weekly(bars)
    elif interval == "1M":
        bars = _resample_monthly(bars)
    elif interval != "1d":
        # Intraday intervals not supported from daily parquet
        return []

    return bars


def get_quote(symbol, market="cn_stock", data_dir=""):
    """Build a quote snapshot from the latest available daily bar."""
    if not data_dir:
        data_dir = os.environ.get("QTDATA_DIR", DEFAULT_DATA_DIR)

    cache_dir = Path(data_dir) / "cache" / "equity_tushare"
    if not cache_dir.exists():
        return None

    ts_code = _to_ts_code(symbol, market)

    # Read the latest monthly file to get the most recent bar
    import pandas as pd
    today = datetime.now().strftime("%Y%m%d")
    month = today[:6]

    # Try current month, then previous month
    for offset in range(3):
        y, m = int(month[:4]), int(month[4:6])
        m -= offset
        if m <= 0:
            m += 12
            y -= 1
        check_month = f"{y:04d}{m:02d}"
        path = cache_dir / f"daily_{check_month}.parquet"
        if not path.exists():
            continue
        df = _safe_read_parquet(path)
        if df is None or df.empty:
            continue
        df = df[df["ts_code"] == ts_code]
        if df.empty:
            continue
        df = df.sort_values("trade_date")
        row = df.iloc[-1]

        # Try to get stock name from stock_basic
        name = symbol
        basic_path = cache_dir / "stock_basic.parquet"
        if basic_path.exists():
            basic = _safe_read_parquet(basic_path)
            if basic is not None:
                match = basic[basic["ts_code"] == ts_code]
                if not match.empty:
                    name = str(match.iloc[0].get("name", symbol))

        td = str(row["trade_date"])
        dt = datetime(int(td[:4]), int(td[4:6]), int(td[6:8]))
        price = float(row["close"])
        prev_close = float(row["pre_close"])
        change = price - prev_close
        change_pct = (change / prev_close * 100) if prev_close else 0

        return {
            "symbol": {"code": symbol, "name": name, "market": market},
            "price": round(price, 4),
            "change": round(change, 4),
            "changePct": round(change_pct, 4),
            "open": round(float(row["open"]), 4),
            "high": round(float(row["high"]), 4),
            "low": round(float(row["low"]), 4),
            "prevClose": round(prev_close, 4),
            "volume": int(float(row["vol"])),
            "amount": round(float(row["amount"]), 4),
            "timestamp": int(dt.timestamp() * 1000),
        }
    return None


def search_symbols(keyword, data_dir=""):
    """Search for stock symbols matching keyword in local stock_basic data."""
    if not data_dir:
        data_dir = os.environ.get("QTDATA_DIR", DEFAULT_DATA_DIR)

    basic_path = Path(data_dir) / "cache" / "equity_tushare" / "stock_basic.parquet"
    if not basic_path.exists():
        return []

    df = _safe_read_parquet(basic_path)
    if df is None or df.empty:
        return []

    kw = keyword.strip().upper()
    mask = (
        df["ts_code"].str.contains(kw, case=False, na=False) |
        df["symbol"].str.contains(kw, case=False, na=False) |
        df["name"].str.contains(kw, case=False, na=False)
    )
    matches = df[mask].head(50)

    results = []
    for _, row in matches.iterrows():
        code = str(row["ts_code"]).replace(".SH", "").replace(".SZ", "")
        exchange = "SSE" if str(row["ts_code"]).endswith(".SH") else "SZSE"
        results.append({
            "code": code,
            "name": str(row.get("name", "")),
            "market": "cn_stock",
            "exchange": exchange,
        })
    return results


def check_availability(symbol, data_dir="", market="cn_stock"):
    """Check if local data is available for a symbol.

    Returns dict with available date range, or None if not available.
    """
    if not data_dir:
        data_dir = os.environ.get("QTDATA_DIR", DEFAULT_DATA_DIR)

    cache_dir = Path(data_dir) / "cache" / "equity_tushare"
    if not cache_dir.exists():
        return None

    ts_code = _to_ts_code(symbol, market)

    # Scan monthly files for this stock
    import glob
    files = sorted(cache_dir.glob("daily_*.parquet"))
    if not files:
        return None

    earliest = None
    latest = None
    for f in files:
        month = f.stem.replace("daily_", "")
        df = _safe_read_parquet(f)
        if df is None or df.empty:
            continue
        stock_data = df[df["ts_code"] == ts_code]
        if stock_data.empty:
            continue
        dates = stock_data["trade_date"].sort_values()
        if earliest is None or str(dates.iloc[0]) < earliest:
            earliest = str(dates.iloc[0])
        if latest is None or str(dates.iloc[-1]) > latest:
            latest = str(dates.iloc[-1])

    if earliest is None:
        return None

    return {
        "symbol": symbol,
        "ts_code": ts_code,
        "earliest": earliest,
        "latest": latest,
    }


def check_data_dir(data_dir=""):
    """Health check — verify data directory and report stats."""
    if not data_dir:
        data_dir = os.environ.get("QTDATA_DIR", DEFAULT_DATA_DIR)

    cache_dir = Path(data_dir) / "cache" / "equity_tushare"

    result = {
        "data_dir": str(data_dir),
        "cache_dir": str(cache_dir),
        "exists": cache_dir.exists(),
        "daily_files": 0,
        "adj_factor_files": 0,
        "has_stock_basic": False,
        "date_range": None,
    }

    if not cache_dir.exists():
        return result

    daily_files = sorted(cache_dir.glob("daily_*.parquet"))
    # Exclude daily_basic_* files
    daily_files = [f for f in daily_files if not f.stem.startswith("daily_basic")]
    adj_files = list(cache_dir.glob("adj_factor_*.parquet"))

    result["daily_files"] = len(daily_files)
    result["adj_factor_files"] = len(adj_files)
    result["has_stock_basic"] = (cache_dir / "stock_basic.parquet").exists()

    if daily_files:
        first_month = daily_files[0].stem.replace("daily_", "")
        last_month = daily_files[-1].stem.replace("daily_", "")
        result["date_range"] = {"from": first_month, "to": last_month}

    return result


# ── CLI entry point (called by Python bridge) ─────────────────────

def main():
    cfg = get_input()
    action = cfg.get("action", "klines")
    data_dir = cfg.get("data_dir", "")

    try:
        if action == "klines":
            data = get_klines(
                symbol=cfg.get("symbol", ""),
                interval=cfg.get("interval", "1d"),
                start=cfg.get("start", ""),
                end=cfg.get("end", ""),
                market=cfg.get("market", "cn_stock"),
                data_dir=data_dir,
            )
            print(json.dumps({"ok": True, "data": data}))

        elif action == "quote":
            data = get_quote(
                symbol=cfg.get("symbol", ""),
                market=cfg.get("market", "cn_stock"),
                data_dir=data_dir,
            )
            print(json.dumps({"ok": True, "data": data}))

        elif action == "search":
            data = search_symbols(
                keyword=cfg.get("keyword", ""),
                data_dir=data_dir,
            )
            print(json.dumps({"ok": True, "data": data}))

        elif action == "check":
            data = check_availability(
                symbol=cfg.get("symbol", ""),
                data_dir=data_dir,
                market=cfg.get("market", "cn_stock"),
            )
            print(json.dumps({"ok": True, "data": data}))

        elif action == "health":
            data = check_data_dir(data_dir=data_dir)
            print(json.dumps({"ok": True, "data": data}))

        else:
            print(json.dumps({"ok": False, "error": f"Unknown action: {action}"}))

    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
