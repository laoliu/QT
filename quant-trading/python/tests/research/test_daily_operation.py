"""Daily operation counts actual sessions and never fills gaps silently."""
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from aipha.research import daily_operation as ops
from aipha.research.pipeline import ResearchConfig
from aipha.research.snapshots import TushareSnapshot, attest_snapshot_metadata

DAYS = ["2024-01-30", "2024-01-31", "2024-02-02"]


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    cfg = ResearchConfig(top_k=2, max_position_weight=.5, max_industry_weight=None)
    monkeypatch.setattr(ops, "load_research_config", lambda _: cfg)
    monkeypatch.setattr("aipha.research.paper_book._commit_time", lambda: "2024-01-30T18:05:00+08:00")
    selector = SimpleNamespace(research_manifest={"model_id": "pinned-model", "model_config_hash": cfg.model_config_hash()},
                               _wf_results=[{"train_end": "2023-12-29"}],
                               predict=lambda frame: pd.Series(np.arange(len(frame)), index=frame.index))
    monkeypatch.setattr(ops, "load_verified_selector", lambda *a, **k: selector)
    def snapshot(deployment, policy, day, provider):
        frame = pd.DataFrame([dict(trade_date=d, symbol=f"60000{i}.SH", open=10., high=11., low=9., close=10.,
            volume=100000., amount=1000000., adj_factor=1., feature_version="cn_equity_v1", price_adjustment="point_in_time_factor",
            is_suspended=False, is_limit_up=False, is_limit_down=False, is_st=False, is_index_member=True,
            listing_days=400, listing_days_basis="exchange_open_sessions")
            for d in DAYS if d <= str(day) for i in range(6)])
        metadata = attest_snapshot_metadata(frame, {"provider": "tushare_pit", "feature_version": "cn_equity_v1",
            "data_snapshot_id": "daily-fixture:" + str(day), "start_date": DAYS[0], "end_date": str(day), "index_code": "000905.SH",
            "listing_calendar": {"provider": "tushare", "endpoint": "trade_cal", "exchange": "SSE", "start_date": DAYS[0],
                                 "end_date": DAYS[-1], "open_sessions": DAYS}})
        frame.attrs["snapshot_metadata"] = metadata
        return TushareSnapshot(frame, metadata)
    monkeypatch.setattr(ops, "prepare_snapshot", snapshot)
    return {"output_root": tmp_path / "operations", "start_date": date(2024, 1, 30), "research_config": tmp_path / "policy.yaml",
            "cache_dir": tmp_path / "cache", "model_path": tmp_path / "model", "model_manifest_sha256": "a" * 64}


def provider(**kwargs):
    return SimpleNamespace(calendar=pd.DataFrame({"cal_date": pd.to_datetime(DAYS), "is_open": 1}))


def now(day):
    return datetime.fromisoformat(day + "T18:05:00+08:00")


def test_real_sessions_month_end_next_open_and_idempotency(deployment, monkeypatch):
    first = ops.operate_day(deployment, execute=True, now=now(DAYS[0]), provider_factory=provider)
    assert first["targets"] == 0 and first["receipt"]["new_trades"] == []
    book = Path(first["book_path"])
    before = book.read_bytes()
    repeat = ops.operate_day(deployment, execute=True, now=now(DAYS[0]), provider_factory=provider)
    assert repeat == first and book.read_bytes() == before
    for day in DAYS[1:]:
        monkeypatch.setattr("aipha.research.paper_book._commit_time", lambda d=day: d + "T18:05:00+08:00")
        result = ops.operate_day(deployment, execute=True, now=now(day), provider_factory=provider)
        if day == "2024-01-31":
            assert result["targets"] == 2 and result["receipt"]["new_trades"] == []
        else:
            assert result["targets"] == 0
            assert result["receipt"]["new_trades"]
            assert all(t["signal_date"] == "2024-01-31" and t["trade_date"] == day for t in result["receipt"]["new_trades"])
    from aipha.research.paper_monitor import inspect_paper_book
    evidence = inspect_paper_book(book, now=now(DAYS[-1]), required_days=3)
    assert evidence["passed"] and evidence["consecutive_valid_days"] == 3


def test_preflight_and_preview_never_create_operating_book(deployment):
    result = ops.operate_day(deployment, now=now(DAYS[0]), preflight_date=date(2024, 1, 30), provider_factory=provider)
    assert result["status"] == "preview" and not result["operational"]
    assert not Path(result["book_path"]).exists()
    assert not (deployment["output_root"] / "paper.sqlite3").exists()
    with pytest.raises(ValueError, match="preflight"):
        ops.operate_day(deployment, execute=True, preflight_date=date(2024, 1, 30), provider_factory=provider)


def test_missed_start_or_intermediate_session_is_rejected(deployment):
    with pytest.raises(ValueError, match="first operating close"):
        ops.operate_day(deployment, execute=True, now=now(DAYS[1]), provider_factory=provider)
    ops.operate_day(deployment, execute=True, now=now(DAYS[0]), provider_factory=provider)
    with pytest.raises(ValueError, match="missing operating session"):
        ops.operate_day(deployment, execute=True, now=now(DAYS[2]), provider_factory=provider)


def test_kill_switch_and_before_close_never_fetch_data(deployment):
    def no_provider(**kwargs):
        pytest.fail("must not fetch or load a model")
    response = ops.operate_day(deployment, now=datetime.fromisoformat("2024-01-30T15:01:00+08:00"), provider_factory=no_provider)
    assert response["status"] == "before_daily_window"
    import json
    assert json.loads((deployment["output_root"] / "progress.json").read_text())["phase"] == "before_daily_window"
    (deployment["output_root"] / "STOP").touch()
    with pytest.raises(ValueError, match="kill switch"):
        ops.operate_day(deployment, execute=True, now=now(DAYS[0]), provider_factory=no_provider)


def test_exclusive_lock_recovers_after_owner_closes(tmp_path):
    with ops.operation_lock(tmp_path):
        with pytest.raises(OSError):
            with ops.operation_lock(tmp_path):
                pytest.fail("second writer entered")
    with ops.operation_lock(tmp_path):
        pass


def test_changed_predictions_are_rejected_without_mutating_book(deployment):
    result = ops.operate_day(deployment, execute=True, now=now(DAYS[0]), provider_factory=provider)
    book = Path(result["book_path"])
    before = book.read_bytes()
    path = Path(result["snapshot_path"]).parent / "predictions.parquet"
    predictions = pd.read_parquet(path)
    assert sorted(predictions["rank"]) == list(range(1, len(predictions) + 1))
    predictions.loc[0, "score"] += 1
    predictions.to_parquet(path, index=False)
    with pytest.raises(ValueError, match="predictions changed"):
        ops.operate_day(deployment, execute=True, now=now(DAYS[0]), provider_factory=provider)
    assert book.read_bytes() == before


def test_kill_switch_during_preparation_blocks_commit(deployment, monkeypatch):
    original = ops.prepare_snapshot
    def stop(*args):
        snapshot = original(*args)
        (deployment["output_root"] / "STOP").touch()
        return snapshot
    monkeypatch.setattr(ops, "prepare_snapshot", stop)
    with pytest.raises(ValueError, match="activated during"):
        ops.operate_day(deployment, execute=True, now=now(DAYS[0]), provider_factory=provider)
    assert not (deployment["output_root"] / "paper.sqlite3").exists()


def test_deployment_rejects_live_and_changed_manifest(tmp_path):
    import hashlib
    import yaml
    model = tmp_path / "model"
    model.mkdir()
    (model / "model_manifest.json").write_text("{}")
    raw = dict(schema_version=1, mode="paper", start_date="2024-01-30", history_start="2020-01-01",
               financial_start="2019-01-01", required_operating_days=5,
               model_manifest_sha256=hashlib.sha256(b"{}").hexdigest())
    raw.update({key: key for key in ("model_path", "research_config", "cache_dir", "output_root", "env_file",
                                    "corrections_path", "evidence_dir")})
    raw["model_path"] = "model"
    path = tmp_path / "deployment.yaml"
    path.write_text(yaml.safe_dump(raw))
    assert ops.load_deployment(path)["mode"] == "paper"
    raw["mode"] = "live"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="Paper-only"):
        ops.load_deployment(path)
    raw["mode"] = "paper"
    path.write_text(yaml.safe_dump(raw))
    (model / "model_manifest.json").write_text('{"changed": true}')
    with pytest.raises(ValueError, match="manifest changed"):
        ops.load_deployment(path)


@pytest.mark.parametrize("finish", ["2024-01-31T00:01:00+08:00", "2024-01-30T17:59:00+08:00",
                                    "2024-01-30T18:04:59+08:00"])
def test_expired_close_or_backwards_clock_never_commits(deployment, finish):
    ticks = iter([now(DAYS[0]), datetime.fromisoformat(finish)])
    with pytest.raises(ValueError, match="expired or clock moved backwards"):
        ops.operate_day(deployment, execute=True, clock=lambda: next(ticks), provider_factory=provider)
    assert not (deployment["output_root"] / "paper.sqlite3").exists()


def test_delayed_same_day_commit_and_terminal_skip_state(deployment):
    import json
    ticks = iter([now(DAYS[0]), datetime.fromisoformat(DAYS[0] + "T20:00:00+08:00")])
    result = ops.operate_day(deployment, execute=True, clock=lambda: next(ticks), provider_factory=provider)
    assert result["status"] == "committed"
    assert json.loads((deployment["output_root"] / "progress.json").read_text())["phase"] == "complete"
    response = ops.operate_day(deployment, now=now("2024-01-29"), provider_factory=lambda **k: pytest.fail("no fetch"))
    assert response["status"] == "not_started" and response["operational"] is False
    assert json.loads((deployment["output_root"] / "progress.json").read_text())["phase"] == "not_started"
    assert json.loads((deployment["output_root"] / "latest.json").read_text())["status"] == "not_started"
