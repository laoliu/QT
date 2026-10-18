#!/usr/bin/env python3
"""
Parquet data updater — pulls new tushare data into local parquet cache.

Incrementally syncs daily klines, adj_factor, and stock_basic data from
TuShare Pro API into monthly parquet files at G:/qtdata/cache/equity_tushare/.

Actions:
  check  — dry-run: report stale/missing months
  sync   — download missing data and merge into parquet cache
  health — report data directory stats

Input:  JSON via QUANT_CONFIG env var or stdin.
Output: JSON to stdout.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

DEFAULT_DATA_DIR = "G:/qtdata"
THROTTLE_WAIT = 5.0       # seconds to back off on rate-limit errors
CALL_INTERVAL = 0.35       # seconds between normal API calls
MAX_RETRIES = 20


def get_input():
    config_str = os.environ.get("QUANT_CONFIG")
    if config_str:
        return json.loads(config_str)
    return json.loads(sys.stdin.read())


# ── TuShare API helpers ───────────────────────────────────────────


def _get_token(cfg: dict) -> str:
    """Resolve tushare token from config → env → quant-trading config."""
    token = cfg.get("tushare_token", "")
    if not token:
        token = os.environ.get("TUSHARE_TOKEN", "")
    if not token:
        # Try reading from quant-trading plugin config
        oc_path = Path(__file__).resolve().parent.parent / "config.json"
        if oc_path.exists():
            try:
                oc = json.loads(oc_path.read_text(encoding="utf-8"))
                token = (
                    oc.get("dataProviders", {})
                    .get("tushare", {})
                    .get("token", "")
                )
            except Exception:
                pass
    return token


def _get_api(token: str):
    """Initialise and return tushare pro_api."""
    import tushare as ts
    ts.set_token(token)
    return ts.pro_api()


def _is_rate_limited(exc: Exception) -> bool:
    """Detect tushare rate-limit / throttle errors."""
    msg = str(exc).lower()
    for kw in ("频率", "超限", "每分钟", "rate", "limit", "too many", "ip"):
        if kw in msg:
            return True
    return False


def _retry_call(api_func, max_retries=MAX_RETRIES, **kwargs):
    """Call tushare API with retry + rate-limit back-off."""
    last_exc = None
    for attempt in range(max_retries):
        try:
            return api_func(**kwargs)
        except Exception as exc:
            last_exc = exc
            if _is_rate_limited(exc):
                wait = THROTTLE_WAIT * (1 + attempt * 0.3)
                print(
                    f"[updater] Rate limited (attempt {attempt + 1}), "
                    f"waiting {wait:.1f}s …",
                    file=sys.stderr,
                )
                time.sleep(wait)
                continue
            if attempt < max_retries - 1:
                print(
                    f"[updater] Attempt {attempt + 1}/{max_retries} failed: {exc}",
                    file=sys.stderr,
                )
                time.sleep(1.0)
                continue
            raise
    raise RuntimeError(
        f"Max retries ({max_retries}) exceeded for tushare call. "
        f"Last error: {last_exc}"
    )


# ── Parquet I/O ───────────────────────────────────────────────────


def _safe_read_parquet(path: Path):
    """Read parquet or return None on corruption."""
    try:
        import pandas as pd
        return pd.read_parquet(path)
    except Exception as exc:
        print(
            f"[updater] Corrupted cache: {path} ({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None


def _safe_write_parquet(df, path: Path):
    """Write parquet atomically via .tmp rename."""
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False, engine="pyarrow")
    tmp.replace(path)


# ── Date helpers ──────────────────────────────────────────────────


def _iter_months(start_ym: str, end_ym: str):
    """Iterate YYYYMM strings from start to end inclusive."""
    sy, sm = int(start_ym[:4]), int(start_ym[4:6])
    ey, em = int(end_ym[:4]), int(end_ym[4:6])
    y, m = sy, sm
    while y < ey or (y == ey and m <= em):
        yield f"{y:04d}{m:02d}"
        m += 1
        if m > 12:
            m = 1
            y += 1


def _trading_days_in_month(year: int, month: int) -> list[str]:
    """Approximate trading days (Mon-Fri) for a month.

    Returns list of YYYYMMDD strings.
    """
    import calendar
    days = []
    _, last_day = calendar.monthrange(year, month)
    for d in range(1, last_day + 1):
        dt = datetime(year, month, d)
        if dt.weekday() < 5:  # Mon-Fri
            days.append(dt.strftime("%Y%m%d"))
    return days


def _latest_cached_month(cache_dir: Path) -> str | None:
    """Find the latest YYYYMM with a daily_*.parquet file."""
    files = _daily_month_files(cache_dir)
    if not files:
        return None
    return files[-1].stem.replace("daily_", "")


def _daily_month_files(cache_dir: Path) -> list[Path]:
    # Per-day snapshots and daily_basic files are separate cache formats.
    return sorted(p for p in cache_dir.glob("daily_*.parquet")
                  if p.is_file() and re.fullmatch(r"daily_\d{4}(?:0[1-9]|1[0-2])", p.stem))


def _yesterday_yyyymmdd() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")


# ── Sync logic: daily klines ─────────────────────────────────────


def _sync_daily_month(api, month: str, cache_dir: Path, force=False) -> dict:
    """Sync daily bars for one month.

    Returns {"month", "status", "rows_before", "rows_after", "fetched_days"}.
    """
    import pandas as pd

    path = cache_dir / f"daily_{month}.parquet"
    y, m = int(month[:4]), int(month[4:6])
    now_ym = datetime.now().strftime("%Y%m")

    existing = None
    rows_before = 0
    if path.exists() and not force:
        existing = _safe_read_parquet(path)
        if existing is not None:
            rows_before = len(existing)

    # Historical month with data → skip (already complete)
    if existing is not None and rows_before > 0 and month < now_ym and not force:
        return {
            "month": month,
            "status": "skipped",
            "rows_before": rows_before,
            "rows_after": rows_before,
            "fetched_days": 0,
        }

    # Determine which trading days to fetch
    approx_days = _trading_days_in_month(y, m)
    # Filter out future dates
    today = datetime.now().strftime("%Y%m%d")
    approx_days = [d for d in approx_days if d <= today]

    if not approx_days:
        return {
            "month": month,
            "status": "future",
            "rows_before": rows_before,
            "rows_after": rows_before,
            "fetched_days": 0,
        }

    # For current month with existing data, only fetch missing days
    existing_dates = set()
    if existing is not None and not existing.empty:
        existing_dates = set(existing["trade_date"].astype(str).unique())

    days_to_fetch = [d for d in approx_days if d not in existing_dates]
    if not days_to_fetch and not force:
        return {
            "month": month,
            "status": "up-to-date",
            "rows_before": rows_before,
            "rows_after": rows_before,
            "fetched_days": 0,
        }

    # Fetch day by day
    new_dfs = []
    for td in days_to_fetch:
        try:
            df = _retry_call(
                api.daily,
                trade_date=td,
                fields="ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount",
            )
            if df is not None and not df.empty:
                new_dfs.append(df)
        except Exception as exc:
            print(f"[updater] daily({td}) failed: {exc}", file=sys.stderr)
        time.sleep(CALL_INTERVAL)

    if not new_dfs and existing is not None:
        return {
            "month": month,
            "status": "no-new-data",
            "rows_before": rows_before,
            "rows_after": rows_before,
            "fetched_days": len(days_to_fetch),
        }

    # Merge with existing
    parts = []
    if existing is not None and not existing.empty:
        parts.append(existing)
    parts.extend(new_dfs)

    if not parts:
        return {
            "month": month,
            "status": "empty",
            "rows_before": rows_before,
            "rows_after": 0,
            "fetched_days": len(days_to_fetch),
        }

    merged = pd.concat(parts, ignore_index=True)
    merged = merged.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    merged = merged.sort_values(["trade_date", "ts_code"])

    _safe_write_parquet(merged, path)

    return {
        "month": month,
        "status": "synced",
        "rows_before": rows_before,
        "rows_after": len(merged),
        "fetched_days": len(days_to_fetch),
    }


# ── Sync logic: adj_factor ────────────────────────────────────────


def _sync_adj_factor_month(api, month: str, cache_dir: Path, force=False) -> dict:
    """Sync adj_factor for one month. Same incremental logic as daily."""
    import pandas as pd

    path = cache_dir / f"adj_factor_{month}.parquet"
    y, m = int(month[:4]), int(month[4:6])
    now_ym = datetime.now().strftime("%Y%m")

    existing = None
    rows_before = 0
    if path.exists() and not force:
        existing = _safe_read_parquet(path)
        if existing is not None:
            rows_before = len(existing)

    # Historical month with data → skip
    if existing is not None and rows_before > 0 and month < now_ym and not force:
        return {"month": month, "status": "skipped", "fetched_days": 0}

    approx_days = _trading_days_in_month(y, m)
    today = datetime.now().strftime("%Y%m%d")
    approx_days = [d for d in approx_days if d <= today]

    if not approx_days:
        return {"month": month, "status": "future", "fetched_days": 0}

    existing_dates = set()
    if existing is not None and not existing.empty:
        existing_dates = set(existing["trade_date"].astype(str).unique())

    days_to_fetch = [d for d in approx_days if d not in existing_dates]
    if not days_to_fetch and not force:
        return {"month": month, "status": "up-to-date", "fetched_days": 0}

    new_dfs = []
    for td in days_to_fetch:
        try:
            df = _retry_call(api.adj_factor, trade_date=td)
            if df is not None and not df.empty:
                new_dfs.append(df)
        except Exception as exc:
            print(f"[updater] adj_factor({td}) failed: {exc}", file=sys.stderr)
        time.sleep(CALL_INTERVAL)

    if not new_dfs and existing is not None:
        return {"month": month, "status": "no-new-data", "fetched_days": len(days_to_fetch)}

    parts = []
    if existing is not None and not existing.empty:
        parts.append(existing)
    parts.extend(new_dfs)

    if not parts:
        return {"month": month, "status": "empty", "fetched_days": len(days_to_fetch)}

    merged = pd.concat(parts, ignore_index=True)
    merged = merged.drop_duplicates(subset=["ts_code", "trade_date"], keep="last")
    merged = merged.sort_values(["trade_date", "ts_code"])

    _safe_write_parquet(merged, path)

    return {"month": month, "status": "synced", "fetched_days": len(days_to_fetch)}


# ── Sync logic: stock_basic ───────────────────────────────────────


def _akshare_stock_basic_fallback(cache_dir: Path) -> dict | None:
    """Last-resort fallback: build stock_basic from AkShare (code + name only).

    Returns result dict on success, None on failure.
    """
    import pandas as pd

    try:
        import akshare as ak

        df = ak.stock_info_a_code_name()
        if df is None or df.empty:
            return None

        # Normalise to TuShare-compatible schema
        records = []
        for _, row in df.iterrows():
            code = str(row["code"]).zfill(6)
            # Determine exchange suffix
            if code.startswith(("6", "9")):
                ts_code = f"{code}.SH"
                market = "主板"
            elif code.startswith(("0", "2")):
                ts_code = f"{code}.SZ"
                market = "主板" if code.startswith("00") else "主板"
            elif code.startswith("3"):
                ts_code = f"{code}.SZ"
                market = "创业板"
            elif code.startswith(("4", "8")):
                ts_code = f"{code}.BJ"
                market = "北交所"
            else:
                ts_code = f"{code}.SZ"
                market = ""
            records.append({
                "ts_code": ts_code,
                "symbol": code,
                "name": row["name"],
                "area": "",
                "industry": "",
                "market": market,
                "list_date": "",
                "list_status": "L",
                "delist_date": "",
                "is_hs": "",
            })

        result = pd.DataFrame(records)
        path = cache_dir / "stock_basic.parquet"
        _safe_write_parquet(result, path)
        print(
            f"[updater] stock_basic: AkShare fallback saved {len(result)} stocks",
            file=sys.stderr,
        )
        return {
            "status": "synced_akshare",
            "stocks": len(result),
            "message": "TuShare unavailable, used AkShare fallback (code+name only)",
        }
    except Exception as exc:
        print(f"[updater] AkShare stock_basic fallback failed: {exc}", file=sys.stderr)
        return None


def _sync_stock_basic(api, cache_dir: Path, force=False, max_retries=3) -> dict:
    """Refresh stock_basic.parquet if older than 30 days.

    stock_basic API has strict rate limits (L: 1/hour, D: 1/min),
    so we use short retries with longer waits and fall back to
    existing cache on failure.
    """
    import pandas as pd

    path = cache_dir / "stock_basic.parquet"
    has_existing = path.exists()

    if has_existing and not force:
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        age_days = (datetime.now() - mtime).days
        if age_days < 30:
            return {"status": "fresh", "age_days": age_days}

    # Fetch active + delisted stocks
    # Use fewer retries + longer wait since stock_basic is heavily rate-limited
    dfs = []
    errors = []
    for status in ("L", "D"):
        try:
            df = _retry_call(
                api.stock_basic,
                max_retries=max_retries,
                list_status=status,
                fields="ts_code,symbol,name,area,industry,market,list_date,list_status,delist_date,is_hs",
            )
            if df is not None and not df.empty:
                dfs.append(df)
            else:
                errors.append(f"stock_basic({status}): empty result")
        except Exception as exc:
            errors.append(f"stock_basic({status}): {exc}")
            print(f"[updater] stock_basic({status}) failed: {exc}", file=sys.stderr)
        time.sleep(CALL_INTERVAL)

    if not dfs:
        # Fall back to existing cache if available
        if has_existing:
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            age_days = (datetime.now() - mtime).days
            print(
                f"[updater] stock_basic API failed, keeping existing cache "
                f"({age_days} days old)",
                file=sys.stderr,
            )
            return {
                "status": "fallback",
                "age_days": age_days,
                "errors": errors,
                "message": f"API rate-limited, using existing cache ({age_days}d old)",
            }
        # No existing cache — try AkShare as last resort (code + name only)
        ak_result = _akshare_stock_basic_fallback(cache_dir)
        if ak_result:
            return ak_result
        return {"status": "failed", "error": "; ".join(errors) or "No data returned"}

    merged = pd.concat(dfs, ignore_index=True)
    merged = merged.drop_duplicates(subset=["ts_code"], keep="last")

    _safe_write_parquet(merged, path)

    return {"status": "synced", "stocks": len(merged)}


# ── High-level actions ────────────────────────────────────────────


def do_check(cfg: dict) -> dict:
    """Dry-run: report which months are stale or missing."""
    data_dir = cfg.get("data_dir", "") or os.environ.get("QTDATA_DIR", DEFAULT_DATA_DIR)
    cache_dir = Path(data_dir) / "cache" / "equity_tushare"

    paths = {"data_dir": str(Path(data_dir).resolve()), "cache_dir": str(cache_dir.resolve())}
    if not cache_dir.is_dir():
        return {"ok": False, **paths, "error": f"Cache dir not found: {cache_dir}"}

    daily_files = _daily_month_files(cache_dir)
    latest = daily_files[-1].stem.removeprefix("daily_") if daily_files else None
    now_ym = datetime.now().strftime("%Y%m")

    if not latest:
        return {
            "ok": True,
            **paths,
            "cached_months": 0,
            "latest_month": None,
            "stale_months": [],
            "message": "No cached data found. Run sync to download.",
        }

    # Check for gaps and stale current month
    stale = []
    first = daily_files[0].stem.removeprefix("daily_")
    for ym in _iter_months(first, now_ym):
        daily_path = cache_dir / f"daily_{ym}.parquet"
        adj_path = cache_dir / f"adj_factor_{ym}.parquet"

        if not daily_path.exists():
            stale.append({"month": ym, "reason": "missing_daily"})
            continue
        if not adj_path.exists():
            stale.append({"month": ym, "reason": "missing_adj_factor"})

        # This legacy-cache freshness check uses weekdays, not an exchange
        # calendar. It is not certification of canonical research readiness.
        if ym == now_ym:
            expected = datetime.now() - timedelta(days=1)
            while expected.weekday() >= 5:
                expected -= timedelta(days=1)
            try:
                import pandas as pd
                # The sync reader removes corrupt caches. A check must be
                # read-only even for malformed files, so do not reuse it.
                df = pd.read_parquet(daily_path, columns=["trade_date"])
                dates = pd.to_datetime(df["trade_date"].astype(str).str.slice(0, 10).str.replace("-", "", regex=False),
                                       format="%Y%m%d", errors="raise")
                if dates.empty or dates.isna().any():
                    raise ValueError("daily cache has empty or invalid trade dates")
                max_date = dates.max().strftime("%Y%m%d")
                if max_date < expected.strftime("%Y%m%d"):
                    stale.append({"month": ym, "reason": "stale", "latest_date": max_date,
                                  "expected": expected.strftime("%Y%m%d")})
            except Exception as exc:
                stale.append({"month": ym, "reason": "unreadable_daily",
                              "error": f"{type(exc).__name__}: {exc}"})

    return {
        "ok": True,
        **paths,
        "cached_months": len(daily_files),
        "latest_month": latest,
        "stale_months": stale,
        "freshness_basis": "previous_weekday_not_exchange_calendar",
        "message": f"{len(stale)} month(s) need updating" if stale else "All up to date",
    }


def do_sync(cfg: dict) -> dict:
    """Incremental sync: download missing/stale data."""
    data_dir = cfg.get("data_dir", "") or os.environ.get("QTDATA_DIR", DEFAULT_DATA_DIR)
    cache_dir = Path(data_dir) / "cache" / "equity_tushare"
    force = cfg.get("force", False)
    start_from = cfg.get("start_from", "")  # optional YYYYMM override

    token = _get_token(cfg)
    if not token:
        return {"ok": False, "error": "No tushare token found (config / TUSHARE_TOKEN env)"}

    # Ensure cache directory exists
    cache_dir.mkdir(parents=True, exist_ok=True)

    api = _get_api(token)
    t0 = time.time()

    results = {"stock_basic": None, "daily": [], "adj_factor": []}

    # 1. Sync stock_basic (skip if requested — useful when rate-limited)
    if cfg.get("skip_stock_basic", False):
        results["stock_basic"] = {"status": "skipped"}
    else:
        try:
            results["stock_basic"] = _sync_stock_basic(api, cache_dir, force=force)
        except Exception as exc:
            results["stock_basic"] = {"status": "error", "error": str(exc)}

    # 2. Determine sync range
    if start_from:
        start_ym = start_from
    else:
        latest = _latest_cached_month(cache_dir)
        if latest:
            # A newer daily file does not imply older adjustment partitions
            # exist. Repair the gaps reported by Check, not just the tail.
            pending = do_check(cfg).get("stale_months", [])
            start_ym = min([latest, *(item["month"] for item in pending)])
        else:
            start_ym = "201201"  # full historical sync

    end_ym = datetime.now().strftime("%Y%m")

    # 3. Sync daily klines month by month
    for ym in _iter_months(start_ym, end_ym):
        try:
            r = _sync_daily_month(api, ym, cache_dir, force=force)
            results["daily"].append(r)
            if r["status"] == "synced":
                print(
                    f"[updater] daily {ym}: +{r['rows_after'] - r['rows_before']} rows "
                    f"({r['fetched_days']} days fetched)",
                    file=sys.stderr,
                )
        except Exception as exc:
            results["daily"].append({"month": ym, "status": "error", "error": str(exc)})
            print(f"[updater] daily {ym} error: {exc}", file=sys.stderr)

    # 4. Sync adj_factor month by month
    for ym in _iter_months(start_ym, end_ym):
        try:
            r = _sync_adj_factor_month(api, ym, cache_dir, force=force)
            results["adj_factor"].append(r)
            if r["status"] == "synced":
                print(f"[updater] adj_factor {ym}: synced", file=sys.stderr)
        except Exception as exc:
            results["adj_factor"].append({"month": ym, "status": "error", "error": str(exc)})

    elapsed = round(time.time() - t0, 1)
    daily_synced = sum(1 for r in results["daily"] if r.get("status") == "synced")
    adj_synced = sum(1 for r in results["adj_factor"] if r.get("status") == "synced")
    failures = [r for r in [results["stock_basic"], *results["daily"], *results["adj_factor"]]
                if r and r.get("status") in {"error", "failed"}]

    return {
        "ok": not failures,
        **({"error": f"{len(failures)} cache update(s) failed; inspect details and retry"} if failures else {}),
        "elapsed_seconds": elapsed,
        "daily_months_synced": daily_synced,
        "adj_factor_months_synced": adj_synced,
        "stock_basic": results["stock_basic"],
        "details": results,
    }


def do_health(cfg: dict) -> dict:
    """Report data directory health stats."""
    if __package__:
        from .local_parquet_provider import check_data_dir
    else:
        from local_parquet_provider import check_data_dir

    data_dir = cfg.get("data_dir", "") or os.environ.get("QTDATA_DIR", DEFAULT_DATA_DIR)
    return check_data_dir(data_dir)


# ── CLI entry point ───────────────────────────────────────────────


def main():
    cfg = get_input()
    action = cfg.get("action", "check")

    try:
        if action == "check":
            data = do_check(cfg)
            print(json.dumps(data))
        elif action == "sync":
            data = do_sync(cfg)
            print(json.dumps(data))
        elif action == "health":
            data = do_health(cfg)
            print(json.dumps({"ok": True, "data": data}))
        else:
            print(json.dumps({"ok": False, "error": f"Unknown action: {action}"}))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}))


if __name__ == "__main__":
    main()
