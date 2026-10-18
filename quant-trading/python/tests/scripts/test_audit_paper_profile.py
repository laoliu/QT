"""Canonical deployment auditing previews accounting without publishing state."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sqlite3

import pandas as pd
import pytest
import yaml

from scripts import audit_ml_migration as audit
from scripts import paper_launcher as launcher
from aipha.research.config import load_research_config
from aipha.research.contracts import TargetPosition
from aipha.research.execution_plan import build_execution_plan
from aipha.research.paper_book import advance_paper_book
from aipha.research.snapshots import point_in_time_adjust_prices, write_attested_parquet


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "ROOT", tmp_path)
    monkeypatch.setattr(launcher, "PROJECT_ROOT", tmp_path)
    policy = tmp_path / "research.yaml"
    policy.write_text(yaml.safe_dump({"portfolio": {"initial_capital": 100000, "top_k": 2,
                                                   "max_position_weight": .5, "max_industry_weight": 1}}))
    (tmp_path / "plans").mkdir()
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    calendar = {"provider": "tushare", "endpoint": "trade_cal", "exchange": "SSE",
                "start_date": "2024-01-31", "end_date": "2024-02-01",
                "open_sessions": ["2024-01-31", "2024-02-01"]}
    full = pd.DataFrame([
        dict(trade_date=day, symbol=symbol, open=10., high=11., low=9., close=10.,
             volume=10000., amount=100000., adj_factor=1., up_limit=11., down_limit=9.,
             is_index_member=True, is_st=False, is_suspended=False, is_limit_up=False, is_limit_down=False,
             listing_days=400, listing_days_basis="exchange_open_sessions", feature_version="cn_equity_v1",
             industry="bank", log_market_cap=style)
        for day in pd.to_datetime(calendar["open_sessions"])
        for symbol, style in [("600000.SH", 1.), ("600001.SH", 3.)]
    ])
    panels = {}
    for day in calendar["open_sessions"]:
        panel = point_in_time_adjust_prices(full.loc[full.trade_date.le(pd.Timestamp(day))].reset_index(drop=True))
        _, _, metadata = write_attested_parquet(panel, tmp_path / f"{day}.parquet", {
            "provider": "tushare_pit", "data_snapshot_id": f"audit-paper:{day}", "feature_version": "cn_equity_v1",
            "index_code": "000905.SH", "start_date": "2024-01-31", "end_date": day, "listing_calendar": calendar,
        })
        panel.attrs["snapshot_metadata"] = metadata
        panels[day] = panel
    plan = build_execution_plan([TargetPosition(pd.Timestamp("2024-01-31").date(), "600001.SH", .5, "fixture",
                                               run_id="run-audit", model_id="model-audit", signal_id="signal-audit")])
    plan.write(tmp_path / "plans/2024-01-31.json")
    raw = {"profile_name": "research_ml", "research_paper": {
        "schema_version": 1, "research_config": "research.yaml", "snapshot_path": "{date}.parquet",
        "book_path": "books/ml.sqlite3", "plan_dir": "plans",
    }}
    path = profiles / "research_ml.yaml"
    _write(path, raw)
    return tmp_path, raw, path, panels, plan


def _write(path, raw):
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _bytes(root):
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_paper_profile_passes_readonly_preview_without_loading_models_or_launching(deployment, monkeypatch, capsys):
    import joblib

    root, _, path, _, plan = deployment
    monkeypatch.setattr(joblib, "load", lambda *a, **k: pytest.fail("no model deserialization in audit"))
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: pytest.fail("audit must not launch a process"))
    before = _bytes(root)
    result = audit.audit_config(path, project_root=root, trade_date="2024-01-31")
    assert result["status"] == "canonical_paper"
    scope, = result["scopes"]
    assert scope["verification_level"] == "paper_snapshot_plan_book_preview"
    assert scope["preview_succeeded"] and scope["plan_id"] == plan.plan_id
    assert not scope["model_provenance_verified"] and not scope["inference_exercised"]
    assert not scope["book_exists"] and not scope["committed"]
    assert scope["preview_nav"] == 100000
    assert not (root / "books").exists()
    assert audit.main(["--config-dir", str(path.parent), "--trade-date", "2024-01-31", "--strict"]) == 0
    assert '"canonical_paper": 1' in capsys.readouterr().out
    assert _bytes(root) == before


@pytest.mark.parametrize("value", [None, False, {}, {"schema_version": True}, {"schema_version": 2}])
def test_malformed_new_profile_is_applicable_and_cannot_pass_strict(deployment, value, capsys):
    root, raw, path, _, _ = deployment
    raw["research_paper"] = value
    _write(path, raw)
    result = audit.audit_config(path, project_root=root, trade_date="2024-01-31")
    assert result["status"] == "unmigrated" and result["scopes"][0]["error"]
    assert audit.main(["--config-dir", str(path.parent), "--trade-date", "2024-01-31", "--strict"]) == 2
    capsys.readouterr()


def test_nested_or_mixed_profiles_cannot_borrow_other_components_validation(deployment):
    root, raw, path, _, _ = deployment
    for value in [{"component": raw}, {**raw, "legacy_ml_compat": True}, {**raw, "strategy": "ml_factor"}]:
        _write(path, value)
        result = audit.audit_config(path, project_root=root, trade_date="2024-01-31")
        assert result["status"] == "unmigrated"
        assert "error" in result["scopes"][0]


def test_fixed_path_defaults_to_verified_end_but_template_requires_date(deployment):
    root, raw, path, _, _ = deployment
    result = audit.audit_config(path, project_root=root)
    assert result["status"] == "unmigrated" and "--trade-date" in result["scopes"][0]["error"]
    raw["research_paper"]["snapshot_path"] = "2024-01-31.parquet"
    _write(path, raw)
    result = audit.audit_config(path, project_root=root)
    assert result["status"] == "canonical_paper"
    assert result["scopes"][0]["trade_date"] == "2024-01-31"
    stale = audit.audit_config(path, project_root=root, trade_date="2024-02-01")
    assert stale["status"] == "unmigrated" and "must end" in stale["scopes"][0]["error"]


@pytest.mark.parametrize("mutation", ["signature", "bytes", "live_plan", "risk", "style", "wrong_universe",
                                     "missing_plan", "missing_dir", "bad_book", "live_config"])
def test_invalid_runtime_materials_fail_without_creating_or_changing_book(deployment, mutation):
    root, raw, path, panels, plan = deployment
    if mutation == "signature":
        sidecar = root / "2024-01-31.metadata.json"
        metadata = json.loads(sidecar.read_text())
        metadata["source_signature"] = "0" * 64
        sidecar.write_text(json.dumps(metadata))
    elif mutation == "bytes":
        with (root / "2024-01-31.parquet").open("ab") as stream:
            stream.write(b"tampered")
    elif mutation in {"live_plan", "risk"}:
        changed = build_execution_plan([replace(plan.targets[0], target_weight=.6 if mutation == "risk" else .5)],
                                       mode="live" if mutation == "live_plan" else "paper", allow_live=True)
        changed.write(root / "plans/2024-01-31.json", allow_live=True)
    elif mutation == "style":
        _write(root / "research.yaml", {"portfolio": {"max_position_weight": .5, "max_style_exposure": {"log_market_cap": .1}}})
    elif mutation == "wrong_universe":
        panel = panels["2024-01-31"]
        metadata = dict(panel.attrs["snapshot_metadata"], index_code="000852.SH")
        write_attested_parquet(panel, root / "2024-01-31.parquet", metadata)
    elif mutation == "missing_plan":
        raw["research_paper"].pop("plan_dir")
        raw["research_paper"]["plan_path"] = "missing.json"
    elif mutation == "missing_dir":
        raw["research_paper"]["plan_dir"] = "missing"
    elif mutation == "bad_book":
        (root / "books").mkdir()
        with sqlite3.connect(root / "books/ml.sqlite3") as connection:
            connection.execute("CREATE TABLE chat_history(id INTEGER)")
    else:
        _write(root / "research.yaml", {"execution": {"mode": "live"}})
    _write(path, raw)
    before = _bytes(root)
    result = audit.audit_config(path, project_root=root, trade_date="2024-01-31")
    assert result["status"] == "unmigrated"
    assert result["scopes"][0]["error"]
    assert _bytes(root) == before


def test_existing_book_is_verified_and_next_session_preview_does_not_commit(deployment):
    root, _, path, panels, plan = deployment
    book = root / "books/ml.sqlite3"
    policy = load_research_config(root / "research.yaml")
    advance_paper_book(book, panels["2024-01-31"], policy, plan=plan, commit=True)
    before = _bytes(root)
    result = audit.audit_config(path, project_root=root, trade_date="2024-02-01")
    assert result["status"] == "canonical_paper"
    scope, = result["scopes"]
    assert scope["book_evidence_verified"] and scope["preview_succeeded"]
    assert not scope["plan_present"] and not scope["committed"]
    assert _bytes(root) == before


def test_corrupt_older_receipt_cannot_pass_existing_book_audit(deployment):
    root, _, path, panels, plan = deployment
    book = root / "books/ml.sqlite3"
    policy = load_research_config(root / "research.yaml")
    advance_paper_book(book, panels["2024-01-31"], policy, plan=plan, commit=True)
    advance_paper_book(book, panels["2024-02-01"], policy, commit=True)
    with sqlite3.connect(book) as connection:
        row = json.loads(connection.execute("SELECT payload FROM receipts WHERE day='2024-01-31'").fetchone()[0])
        row["cash"] += 1
        connection.execute("UPDATE receipts SET payload=? WHERE day='2024-01-31'", (json.dumps(row),))
    before = book.read_bytes()
    result = audit.audit_config(path, project_root=root, trade_date="2024-02-01")
    assert result["status"] == "unmigrated"
    assert "receipt NAV/cash/date" in result["scopes"][0]["error"]
    assert book.read_bytes() == before


def test_no_dated_plan_is_valid_valuation_not_model_migration_evidence(deployment):
    root, _, path, _, _ = deployment
    result = audit.audit_config(path, project_root=root, trade_date="2024-02-01")
    assert result["status"] == "canonical_paper"
    scope, = result["scopes"]
    assert not scope["plan_present"] and not scope["book_exists"]
    assert not scope["model_provenance_verified"] and not scope["inference_exercised"]
    assert not (root / "books").exists()


def test_shared_books_fail_batch_audit_and_strict_gate(deployment, capsys):
    root, raw, path, _, _ = deployment
    second = deepcopy(raw)
    second["research_paper"]["book_path"] = "books/../books/ml.sqlite3"
    _write(path.parent / "second.yaml", second)
    report = audit.audit_tree(path.parent, project_root=root, trade_date="2024-01-31")
    assert report["counts"] == {"unmigrated": 2}
    assert all("shared Paper book" in row["scopes"][0]["error"] for row in report["configs"])
    assert audit.main(["--config-dir", str(path.parent), "--trade-date", "2024-01-31", "--strict"]) == 2
    capsys.readouterr()
    assert not (root / "books").exists()


def test_profile_path_selection_matches_launcher(deployment):
    root, _, path, _, _ = deployment
    command = launcher.prepare_launch(path, trade_date="2024-01-31")["command"]
    scope = audit.audit_config(path, project_root=root, trade_date="2024-01-31")["scopes"][0]
    for flag, field in [("--research-config", "research_config"), ("--panel", "canonical_snapshot"),
                        ("--book-path", "book_path"), ("--plan-path", "plan_path")]:
        actual = scope[field][0][1] if field == "canonical_snapshot" else scope[field]
        assert command[command.index(flag) + 1] == actual
