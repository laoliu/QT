"""Display projections must not mutate books or count previews as operation."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest
import yaml

from aipha.research.operations_view import OperationsView, tail, target_rows
from aipha.research import daily_operation as ops
from test_daily_operation import deployment, provider, now, DAYS


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def view_path(deployment, tmp_path):
    dep = deployment
    write(dep["model_path"] / "model_manifest.json", {"model_id": "fixture-model", "feature_version": "cn_equity_v1"})
    model_hash = hashlib.sha256((dep["model_path"] / "model_manifest.json").read_bytes()).hexdigest()
    env = tmp_path / "local.env"
    env.write_text("TUSHARE_TOKEN=private-local-token\n", encoding="utf-8")
    config = {k: str(v) for k, v in dep.items()}
    config.update(mode="paper", model_manifest_sha256=model_hash, required_operating_days=3, env_file=str(env))
    path = tmp_path / "deployment.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    run = dep["model_path"].parent
    write(run / "run_manifest.json", {"run_id": "fixture-model", "metrics": {"rank_ic": .1}})
    write(run / "report.json", {"walk_forward": {"folds": [{"fold": 0, "train_dates": ["DO-NOT-SEND"], "mean_ic": .1}]}})
    return path


def test_absent_book_does_not_create_files_or_count_previews(view_path, deployment):
    root = deployment["output_root"]
    write(root / "preflight/2024-01-30/preview.json", {"status": "preview"})
    before = sorted(str(p) for p in root.rglob("*"))
    result = OperationsView(view_path, now=now(DAYS[0])).overview()
    assert result["paper"]["consecutive_valid_days"] == 0 and not result["paper"]["passed"]
    assert result["paper"]["status"] == "awaiting_first_receipt"
    assert result["sessions"] == [] and result["issues"] == []
    assert sorted(str(p) for p in root.rglob("*")) == before
    assert not (root / "paper.sqlite3").exists()
    assert "train_dates" not in result["research"]["folds"][0]


def test_missed_first_day_is_not_reported_as_normal_waiting(view_path):
    result = OperationsView(view_path, now=now(DAYS[1])).overview()
    assert result["paper"]["status"] == "missing_first_session"
    assert any(a["level"] == "error" for a in result["alerts"])


def test_real_ledger_is_replayed_read_only_and_tampering_never_passes(view_path, deployment, monkeypatch):
    ops.operate_day(deployment, execute=True, now=now(DAYS[0]), provider_factory=provider)
    book = deployment["output_root"] / "paper.sqlite3"
    before = book.read_bytes()
    result = OperationsView(view_path, now=now(DAYS[0])).overview()
    assert result["paper"]["consecutive_valid_days"] == 1
    assert result["paper"]["receipts"][0]["trade_date"] == DAYS[0]
    assert not result["paper"]["passed"] and book.read_bytes() == before
    assert not result["issues"]
    with sqlite3.connect(book) as connection:
        encoded = connection.execute("SELECT payload FROM receipts LIMIT 1").fetchone()[0]
        value = json.loads(encoded)
        value["nav"] += 99
        connection.execute("UPDATE receipts SET payload=?", (json.dumps(value),))
    tampered = OperationsView(view_path, now=now(DAYS[0])).overview()
    assert tampered["paper"]["status"] == "invalid" and not tampered["paper"]["passed"]
    assert tampered["paper"]["receipts"] == []


def test_prediction_paging_is_date_scoped_and_sorted(view_path, deployment):
    path = deployment["model_path"].parent / "oos_predictions.parquet"
    pd.DataFrame([{"trade_date": d, "symbol": f"S{i}", "score": i / 10, "rank": 6-i,
                   "model_id": "model", "feature_version": "v1"} for d in DAYS[:2] for i in range(6)]).to_parquet(path)
    view = OperationsView(view_path)
    result = view.predictions("research", DAYS[0], 2, 2)
    assert result["total"] == 6 and [r["rank"] for r in result["rows"]] == [3, 4]
    assert {r["trade_date"] for r in result["rows"]} == {DAYS[0]}
    assert view.predictions("paper")["total"] == 0
    with pytest.raises(ValueError):
        view.predictions("paper", "../../.env")


def test_observer_reports_full_streak_without_rewriting_the_book(view_path, deployment, monkeypatch):
    for day in DAYS:
        monkeypatch.setattr("aipha.research.paper_book._commit_time", lambda d=day: d + "T18:05:00+08:00")
        ops.operate_day(deployment, execute=True, now=now(day), provider_factory=provider)
    book = deployment["output_root"] / "paper.sqlite3"
    before = book.read_bytes()
    result = OperationsView(view_path, now=now(DAYS[-1])).overview()
    assert result["paper"]["passed"] and result["paper"]["consecutive_valid_days"] == 3
    assert len(result["paper"]["receipts"]) == 3 and len(result["paper"]["nav_history"]) == 3
    assert result["sessions"][1]["targets_detail"][0]["targetWeight"] == .5
    assert result["paper"]["receipts"][0]["trades"]
    assert book.read_bytes() == before and result["issues"] == []


def test_redacts_secrets_logs_and_rejects_log_paths_outside_scheduler(view_path, deployment, tmp_path):
    root = deployment["output_root"]
    log = root / "scheduler/run.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("TUSHARE_TOKEN=private-local-token\nBearer unknown-secret\n", encoding="utf-16")
    write(root / "scheduler/latest_process_result.json", {"exit_code": 1, "log_path": str(log)})
    result = OperationsView(view_path, now=now(DAYS[0])).overview()
    encoded = json.dumps(result)
    assert "private-local-token" not in encoded and "unknown-secret" not in encoded
    assert any("[REDACTED]" in line for line in result["logs"])
    log.write_text("中文阶段已完成\n" * 3000 + "最后一条\n", encoding="utf-16")
    assert tail(log)[-1] == "最后一条"
    outside = tmp_path / "outside.log"
    outside.write_text("PRIVATE", encoding="utf-8")
    write(root / "scheduler/latest_process_result.json", {"log_path": str(outside)})
    assert OperationsView(view_path).overview()["logs"] == []


def test_model_mismatch_partial_errors_and_target_contracts(view_path, deployment):
    (deployment["model_path"] / "model_manifest.json").write_text("{}")
    (deployment["model_path"].parent / "report.json").write_text("{broken")
    result = OperationsView(view_path, now=now(DAYS[0])).overview()
    assert not result["deployment"]["manifest_matches"] and result["issues"]
    assert result["paper"]["consecutive_valid_days"] == 0
    assert target_rows([{"symbol": "A", "target_weight": .05}])[0]["targetWeight"] == .05


def test_target_reason_codes_have_human_readable_labels() -> None:
    rows = target_rows([
        {"symbol": "600970.SH", "reason": "ml_rank_1"},
        {"symbol": "301526.SZ", "reason": "ml_rank_02"},
        {"symbol": "688709.SH", "reason": "manual_review"},
        {"symbol": "000001.SZ", "reason": ""},
    ])
    assert rows[0]["reason"] == "ml_rank_1"
    assert rows[0]["reasonLabel"] == "模型评分排名第 1 名"
    assert rows[1]["reasonLabel"] == "模型评分排名第 2 名"
    assert rows[2]["reasonLabel"] == "manual_review"
    assert rows[3]["reasonLabel"] == "未提供原因"


def test_phase_timings_and_failures_are_observations_not_receipts(deployment):
    ops.operate_day(deployment, execute=True, now=now(DAYS[0]), provider_factory=provider)
    root = deployment["output_root"]
    progress = json.loads((root / "progress.json").read_text())
    assert progress["phase"] == "complete" and progress["attempt_id"]
    assert all(row.get("duration_seconds", 0) >= 0 for row in progress["stages"])
    assert {"verifying_snapshot", "selecting_features", "predicting", "building_targets"} <= {s["phase"] for s in progress["stages"]}
    with pytest.raises(ValueError, match="missing operating session"):
        ops.operate_day(deployment, execute=True, now=now(DAYS[-1]), provider_factory=provider)
    failed = json.loads((root / "progress.json").read_text())
    assert failed["phase"] == "failed" and failed["attempt_id"] != progress["attempt_id"]
    assert len((root / "phase_events.jsonl").read_text().splitlines()) > len(progress["stages"])


def test_target_names_use_local_qualified_codes_without_changing_artifacts(view_path, deployment):
    cache = deployment["cache_dir"]
    cache.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([
        {"ts_code": "000001.SZ", "name": "平安银行"},
        {"ts_code": "000001.SH", "name": "上证指数"},
        {"ts_code": "600970.SH", "name": "中材国际"},
        {"ts_code": "688709.SH", "name": None},
    ]).to_parquet(cache / "stock_basic.parquet")
    historical = deployment["model_path"].parent / "target_positions.json"
    plan = deployment["output_root"] / "sessions" / DAYS[0] / "plan.json"
    write(historical, [{"symbol": "000001.SZ", "rebalanceDate": DAYS[0], "targetWeight": .1},
                       {"symbol": "688709.SH", "rebalanceDate": DAYS[0], "targetWeight": .2}])
    write(plan, {"targets": [{"symbol": "600970.SH", "target_weight": .3}]})
    before = {p: p.read_bytes() for p in (historical, plan, cache / "stock_basic.parquet")}
    result = OperationsView(view_path, now=now(DAYS[0])).overview()
    assert result["research"]["targets"][0]["name"] == "平安银行"
    assert result["research"]["targets"][1]["name"] is None
    assert result["sessions"][0]["targets_detail"][0]["name"] == "中材国际"
    assert result["sessions"][0]["targets_detail"][0]["targetWeight"] == .3
    assert all(p.read_bytes() == data for p, data in before.items())
    assert not result["issues"]


@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_or_corrupt_name_cache_preserves_target_positions(view_path, deployment, corrupt):
    cache = deployment["cache_dir"]
    cache.mkdir(parents=True, exist_ok=True)
    if corrupt:
        (cache / "stock_basic.parquet").write_bytes(b"invalid parquet")
    write(deployment["model_path"].parent / "target_positions.json",
          [{"symbol": "600970.SH", "rebalanceDate": DAYS[0], "targetWeight": .1}])
    result = OperationsView(view_path, now=now(DAYS[0])).overview()
    assert result["research"]["targets"][0] == {
        "symbol": "600970.SH", "rebalanceDate": DAYS[0], "targetWeight": .1, "name": None}
    assert bool([i for i in result["issues"] if i["section"] == "stock_names"]) == corrupt
