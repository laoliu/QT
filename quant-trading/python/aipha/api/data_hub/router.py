"""Data quality & coverage API router."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List

from fastapi import APIRouter, Depends, Query

from aipha.api.auth import get_current_user
from aipha.api.deps import get_result_store
from aipha.api.schemas import ApiResponse, UserInfo
from aipha.factors.technical import TechnicalFactorCalculator, bollinger_bands, macd
from aipha.utils.paths import project_path

router = APIRouter(prefix="/data", tags=["data"])

try:
    from aipha.config import get_cache_dir as _get_cache_dir
    _CACHE_DIR = _get_cache_dir()
except Exception:
    # 环境变量优先；兜底值锚到项目根，不吃当前工作目录
    _env_cache = os.environ.get("AIPHA_CACHE_DIR")
    _CACHE_DIR = Path(_env_cache) if _env_cache else project_path("data", "cache")
_ANN_DIR = project_path("data", "annotations")
_MONTHLY_CACHE_RE = re.compile(r"^(?P<symbol>[A-Z0-9.]+)_(?P<month>\d{6})_daily_")
_CB_DAILY_RE = re.compile(r"^cb_daily_(?P<date>\d{8})$")


def _iter_cache_files() -> List[Path]:
    if not _CACHE_DIR.exists():
        return []
    return sorted(_CACHE_DIR.rglob("*.parquet"))


def _safe_modified(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")


def _format_trade_date(value: Any) -> str:
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _load_bars_frame(symbol: str):
    import pandas as pd

    pattern = f"{symbol}*.parquet"
    files = sorted(_CACHE_DIR.rglob(pattern))
    if not files:
        return pd.DataFrame()

    frames = []
    for path in files:
        try:
            frames.append(pd.read_parquet(path))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)
    if "trade_date" not in df.columns:
        return pd.DataFrame()

    df["trade_date"] = df["trade_date"].astype(str)
    rename_map = {"vol": "volume"}
    df = df.rename(columns=rename_map)
    df = df.sort_values("trade_date").drop_duplicates(subset=["trade_date"], keep="last")
    keep_cols = [col for col in ["trade_date", "open", "high", "low", "close", "volume"] if col in df.columns]
    return df[keep_cols].reset_index(drop=True)


def _resample_bars(df, freq: str):
    import pandas as pd

    normalized = (freq or "D").upper()
    if normalized in {"DAILY", "DAY"}:
        normalized = "D"
    elif normalized in {"WEEKLY", "WEEK"}:
        normalized = "W"
    elif normalized in {"MONTHLY", "MONTH"}:
        normalized = "M"

    if df.empty or normalized == "D":
        return df

    rule = "W-FRI" if normalized == "W" else "ME"
    sampled = df.copy()
    sampled["trade_date"] = pd.to_datetime(sampled["trade_date"])
    sampled = sampled.set_index("trade_date")
    agg = {}
    if "open" in sampled.columns:
        agg["open"] = "first"
    if "high" in sampled.columns:
        agg["high"] = "max"
    if "low" in sampled.columns:
        agg["low"] = "min"
    if "close" in sampled.columns:
        agg["close"] = "last"
    if "volume" in sampled.columns:
        agg["volume"] = "sum"
    sampled = sampled.resample(rule).agg(agg).dropna(subset=["close"], how="all").reset_index()
    sampled["trade_date"] = sampled["trade_date"].dt.strftime("%Y%m%d")
    return sampled


def _annotation_count() -> int:
    if not _ANN_DIR.exists():
        return 0
    return len(list(_ANN_DIR.glob("*.json")))


def _monthly_coverage(files: Iterable[Path]) -> Dict[str, set[str]]:
    monthly: Dict[str, set[str]] = {}
    for path in files:
        match = _MONTHLY_CACHE_RE.match(path.stem)
        if not match:
            continue
        month = match.group("month")
        monthly.setdefault(month, set()).add(match.group("symbol"))
    return monthly


def _load_quality_snapshot(paths: Iterable[Path]) -> Dict[str, Dict[str, int]]:
    import pandas as pd

    fields = ["open", "high", "low", "close", "volume"]
    stats = {field: {"missing": 0, "anomaly": 0, "total": 0} for field in fields}
    for path in paths:
        try:
            df = pd.read_parquet(path)
        except Exception:
            continue
        row_count = len(df.index)
        if row_count == 0:
            continue
        high_series = pd.to_numeric(df.get("high"), errors="coerce") if "high" in df.columns else None
        low_series = pd.to_numeric(df.get("low"), errors="coerce") if "low" in df.columns else None
        for field in fields:
            column = "vol" if field == "volume" and "volume" not in df.columns else field
            if column not in df.columns:
                stats[field]["missing"] += row_count
                stats[field]["total"] += row_count
                continue
            series = pd.to_numeric(df[column], errors="coerce")
            stats[field]["missing"] += int(series.isna().sum())
            stats[field]["total"] += row_count
            if field == "volume":
                stats[field]["anomaly"] += int((series.fillna(0) < 0).sum())
            else:
                stats[field]["anomaly"] += int((series.fillna(0) <= 0).sum())
        if high_series is not None and low_series is not None:
            cross = int((high_series.fillna(0) < low_series.fillna(0)).sum())
            stats["high"]["anomaly"] += cross
            stats["low"]["anomaly"] += cross
    return stats


@router.get("/coverage", response_model=ApiResponse[Dict[str, Any]])
async def data_coverage(
    _user: UserInfo = Depends(get_current_user),
):
    """Get data cache coverage summary."""
    parquet_files = _iter_cache_files()
    total_bytes = sum(path.stat().st_size for path in parquet_files)
    latest_files = sorted(parquet_files, key=lambda path: path.stat().st_mtime, reverse=True)[:200]
    info: Dict[str, Any] = {
        "cache_dir": str(_CACHE_DIR),
        "total_files": len(parquet_files),
        "total_size_mb": round(total_bytes / 1024 / 1024, 1),
        "cache_files": [
            {
                "name": str(path.relative_to(_CACHE_DIR)).replace("\\", "/"),
                "size_mb": round(path.stat().st_size / 1024 / 1024, 3),
                "modified": _safe_modified(path),
            }
            for path in latest_files
        ],
    }
    return ApiResponse(data=info)


@router.get("/quality", response_model=ApiResponse[Dict[str, Any]])
async def data_quality(
    _user: UserInfo = Depends(get_current_user),
):
    """Get data quality summary (annotation based)."""
    parquet_files = _iter_cache_files()
    sample_files = sorted(parquet_files, key=lambda path: path.stat().st_mtime, reverse=True)[:12]
    snapshot = _load_quality_snapshot(sample_files)
    total_cells = sum(item["total"] for item in snapshot.values()) or 1
    missing_cells = sum(item["missing"] for item in snapshot.values())
    anomaly_cells = sum(item["anomaly"] for item in snapshot.values())

    monthly = _monthly_coverage(parquet_files)
    sources = []
    if monthly:
        months = sorted(monthly.keys())
        sources.append({
            "name": "equity_monthly_cache",
            "coverage": f"{months[0]} ~ {months[-1]}",
            "status": "ok",
            "completeness": min(100, round(sum(len(symbols) for symbols in monthly.values()) / max(len(monthly), 1))),
        })
    cb_days = sorted(path.stem for path in parquet_files if _CB_DAILY_RE.match(path.stem))
    if cb_days:
        sources.append({
            "name": "cb_daily_cache",
            "coverage": f"{cb_days[0][9:]} ~ {cb_days[-1][9:]}",
            "status": "ok",
            "completeness": 100,
        })
    return ApiResponse(
        data={
            "total_annotations": _annotation_count(),
            "missing_rate": round(missing_cells * 100 / total_cells, 4),
            "anomaly_rate": round(anomaly_cells * 100 / total_cells, 4),
            "sources": sources,
        }
    )


@router.get("/coverage-timeline", response_model=ApiResponse[Dict[str, Any]])
async def data_coverage_timeline(
    _user: UserInfo = Depends(get_current_user),
):
    """Return monthly coverage trend based on real cache files."""
    monthly = _monthly_coverage(_iter_cache_files())
    months = sorted(monthly.keys())[-36:]
    return ApiResponse(data={
        "dates": [f"{month[:4]}-{month[4:]}" for month in months],
        "counts": [len(monthly[month]) for month in months],
    })


@router.get("/anomaly-heatmap", response_model=ApiResponse[Dict[str, Any]])
async def data_anomaly_heatmap(
    _user: UserInfo = Depends(get_current_user),
):
    """Return recent cache anomaly heatmap derived from sampled parquet files."""
    monthly_files: Dict[str, List[Path]] = {}
    for path in _iter_cache_files():
        match = _MONTHLY_CACHE_RE.match(path.stem)
        if match:
            monthly_files.setdefault(match.group("month"), []).append(path)
    months = sorted(monthly_files.keys())[-18:]
    fields = ["open", "high", "low", "close", "volume"]
    data: List[List[int]] = []
    for x_idx, month in enumerate(months):
        snapshot = _load_quality_snapshot(monthly_files[month][:3])
        for y_idx, field in enumerate(fields):
            data.append([x_idx, y_idx, snapshot[field]["anomaly"] + snapshot[field]["missing"]])
    return ApiResponse(data={
        "dates": [f"{month[:4]}-{month[4:]}" for month in months],
        "fields": fields,
        "data": data,
    })


@router.get("/update-log", response_model=ApiResponse[List[Dict[str, Any]]])
async def data_update_log(
    _user: UserInfo = Depends(get_current_user),
):
    """Return persisted data-update tasks as the data update log."""
    store = get_result_store()
    rows = store.get_system_setting("async_tasks", [])
    if not isinstance(rows, list):
        rows = []
    entries = [
        {
            "timestamp": row.get("created_at") or row.get("started_at"),
            "action": row.get("description") or "数据更新",
            "status": row.get("status", "unknown"),
            "detail": row.get("error_message") or row.get("result_id"),
        }
        for row in rows
        if row.get("type") == "data_update"
    ]
    entries.sort(key=lambda item: item.get("timestamp") or "", reverse=True)
    return ApiResponse(data=entries[:20])


@router.get("/bars/{symbol}", response_model=ApiResponse[Dict[str, Any]])
async def get_bars(
    symbol: str,
    start: str = Query(None),
    end: str = Query(None),
    freq: str = Query("daily"),
    _user: UserInfo = Depends(get_current_user),
):
    """Get OHLCV bars for a symbol (reads from cache)."""
    df = _load_bars_frame(symbol)
    if start:
        df = df[df["trade_date"] >= start.replace("-", "")]
    if end:
        df = df[df["trade_date"] <= end.replace("-", "")]
    df = _resample_bars(df, freq)
    bars = [
        {
            "date": _format_trade_date(row["trade_date"]),
            "open": float(row.get("open", 0) or 0),
            "high": float(row.get("high", 0) or 0),
            "low": float(row.get("low", 0) or 0),
            "close": float(row.get("close", 0) or 0),
            "volume": float(row.get("volume", 0) or 0),
        }
        for row in df.tail(500).to_dict("records")
    ]
    return ApiResponse(data={"symbol": symbol, "bars": bars})


@router.get("/bars/{symbol}/indicators", response_model=ApiResponse[Dict[str, Any]])
async def get_bar_indicators(
    symbol: str,
    indicators: str = Query(""),
    start: str | None = Query(None),
    end: str | None = Query(None),
    _user: UserInfo = Depends(get_current_user),
):
    """Calculate selected technical indicators from real cached bars."""
    df = _load_bars_frame(symbol)
    if start:
        df = df[df["trade_date"] >= start.replace("-", "")]
    if end:
        df = df[df["trade_date"] <= end.replace("-", "")]
    requested = [item.strip() for item in indicators.split(",") if item.strip()]
    if df.empty or not requested:
        return ApiResponse(data={"dates": [], "indicators": {}})

    calculator = TechnicalFactorCalculator()
    alias_map = {"MA5": "sma_5", "MA20": "sma_20", "RSI": "rsi_14"}
    computed = calculator.calculate(df, [alias_map[item] for item in requested if item in alias_map])
    result: Dict[str, List[float | None]] = {}
    for item in requested:
        if item in alias_map and alias_map[item] in computed.columns:
            result[item] = computed[alias_map[item]].round(4).where(computed[alias_map[item]].notna(), None).tolist()
        elif item == "BOLL":
            _, upper, lower = bollinger_bands(df["close"])
            result["BOLL_UPPER"] = upper.round(4).where(upper.notna(), None).tolist()
            result["BOLL_LOWER"] = lower.round(4).where(lower.notna(), None).tolist()
        elif item == "MACD":
            macd_line, signal_line, histogram = macd(df["close"])
            result["MACD"] = macd_line.round(4).where(macd_line.notna(), None).tolist()
            result["MACD_SIGNAL"] = signal_line.round(4).where(signal_line.notna(), None).tolist()
            result["MACD_HIST"] = histogram.round(4).where(histogram.notna(), None).tolist()
    return ApiResponse(data={
        "dates": [_format_trade_date(value) for value in df["trade_date"].tolist()],
        "indicators": result,
    })


@router.post("/update", response_model=ApiResponse[Dict[str, Any]])
async def trigger_data_update(
    body: Dict[str, Any] = {},
    _user: UserInfo = Depends(get_current_user),
):
    """Trigger data update."""
    import uuid

    task_id = f"data_{uuid.uuid4().hex[:8]}"
    store = get_result_store()
    rows = store.get_system_setting("async_tasks", [])
    if not isinstance(rows, list):
        rows = []
    rows.insert(0, {
        "task_id": task_id,
        "type": "data_update",
        "status": "pending",
        "progress": 0,
        "description": f"数据更新任务 {body.get('source', 'all')}",
        "created_by": getattr(_user, 'username', 'system'),
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    })
    store.set_system_setting("async_tasks", rows, updated_by=getattr(_user, 'username', 'system'))
    return ApiResponse(data={"task_id": task_id, "status": "queued", "source": body.get("source", "all")})
