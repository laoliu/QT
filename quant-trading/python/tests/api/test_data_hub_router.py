from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
from fastapi.testclient import TestClient

from aipha.api import deps
from aipha.api.data_hub import router as data_router
from aipha.api.deps import create_access_token, get_result_store


def _bootstrap(tmp_path: Path, app) -> tuple[TestClient, dict[str, str], Path]:
    deps._DB_PATH = tmp_path / "backtest_index.db"
    get_result_store.cache_clear()
    cache_dir = tmp_path / "cache"
    ann_dir = tmp_path / "annotations"
    cache_dir.mkdir()
    ann_dir.mkdir()
    data_router._CACHE_DIR = cache_dir
    data_router._ANN_DIR = ann_dir
    store = get_result_store()
    store.upsert_user({"username": "researcher1", "name": "researcher1", "password_hash": "x", "role": "researcher"})
    headers = {"Authorization": f"Bearer {create_access_token('researcher1', 'researcher')}"}
    return TestClient(app), headers, cache_dir


def _write_equity(cache_dir: Path, symbol: str, month: str, closes: list[float]) -> None:
    dates = [f"{month}{day:02d}" for day in range(1, len(closes) + 1)]
    df = pd.DataFrame({
        "trade_date": dates,
        "open": closes,
        "high": [value + 1 for value in closes],
        "low": [value - 1 for value in closes],
        "close": closes,
        "vol": [1000 + idx * 10 for idx, _ in enumerate(closes)],
    })
    df.to_parquet(cache_dir / f"{symbol}_{month}_daily_qfq.parquet")


def test_data_hub_routes_use_real_cache_files(tmp_path: Path, app) -> None:
    client, headers, cache_dir = _bootstrap(tmp_path, app)
    _write_equity(cache_dir, "000001.SZ", "202401", [10, 11, 12])
    _write_equity(cache_dir, "000002.SZ", "202401", [20, 21, 22])
    _write_equity(cache_dir, "000001.SZ", "202402", [13, 14, 15])
    pd.DataFrame({"trade_date": ["20240201", "20240202"], "close": [100, 101]}).to_parquet(cache_dir / "cb_daily_20240201.parquet")
    (tmp_path / "annotations" / "quality.json").write_text(json.dumps({"name": "check"}), encoding="utf-8")

    coverage = client.get("/api/v1/data/coverage", headers=headers)
    assert coverage.status_code == 200
    assert coverage.json()["data"]["total_files"] == 4

    quality = client.get("/api/v1/data/quality", headers=headers)
    assert quality.status_code == 200
    assert quality.json()["data"]["total_annotations"] == 1
    assert len(quality.json()["data"]["sources"]) >= 1

    timeline = client.get("/api/v1/data/coverage-timeline", headers=headers)
    assert timeline.status_code == 200
    assert timeline.json()["data"]["dates"] == ["2024-01", "2024-02"]
    assert timeline.json()["data"]["counts"] == [2, 1]

    heatmap = client.get("/api/v1/data/anomaly-heatmap", headers=headers)
    assert heatmap.status_code == 200
    assert heatmap.json()["data"]["fields"] == ["open", "high", "low", "close", "volume"]


def test_bars_and_indicators_are_loaded_from_cached_parquet(tmp_path: Path, app) -> None:
    client, headers, cache_dir = _bootstrap(tmp_path, app)
    _write_equity(cache_dir, "600000.SH", "202401", [10, 11, 12, 13, 14])
    _write_equity(cache_dir, "600000.SH", "202402", [15, 16, 17, 18, 19])

    bars = client.get("/api/v1/data/bars/600000.SH?start=2024-01-01&end=2024-02-05", headers=headers)
    assert bars.status_code == 200
    payload = bars.json()["data"]
    assert payload["symbol"] == "600000.SH"
    assert payload["bars"][0]["date"] == "2024-01-01"
    assert payload["bars"][-1]["close"] == 19.0

    indicators = client.get(
        "/api/v1/data/bars/600000.SH/indicators?indicators=MA5,RSI,MACD,BOLL",
        headers=headers,
    )
    assert indicators.status_code == 200
    data = indicators.json()["data"]
    assert len(data["dates"]) == 10
    assert "MA5" in data["indicators"]
    assert "RSI" in data["indicators"]
    assert "MACD" in data["indicators"]
    assert "BOLL_UPPER" in data["indicators"]


def test_data_update_creates_task_and_update_log(tmp_path: Path, app) -> None:
    client, headers, _cache_dir = _bootstrap(tmp_path, app)

    queued = client.post("/api/v1/data/update", headers=headers, json={"source": "cb"})
    assert queued.status_code == 200
    assert queued.json()["data"]["status"] == "queued"

    log_resp = client.get("/api/v1/data/update-log", headers=headers)
    assert log_resp.status_code == 200
    entries = log_resp.json()["data"]
    assert len(entries) == 1
    assert entries[0]["status"] == "pending"
    assert "cb" in entries[0]["action"]
