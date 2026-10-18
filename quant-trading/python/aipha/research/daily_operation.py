"""One idempotent, post-close operating session for a dedicated Paper book."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, time, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import uuid

import numpy as np
import pandas as pd
import yaml

from .artifacts import load_verified_selector
from .config import load_research_config
from .execution_plan import build_execution_plan, read_execution_plan
from .paper_book import advance_paper_book, read_paper_book_evidence
from .pipeline import build_target_positions, monthly_rebalance_dates
from .snapshots import (
    SnapshotSpec, TushareSnapshot, attest_snapshot_metadata, build_tushare_snapshot,
    correct_snapshot_action_units, frame_content_sha256, verify_attested_parquet,
)
from aipha.universe.historical import filter_historical_universe
from aipha.universe.indices import universe_index_code

SHANGHAI = timezone(timedelta(hours=8))


def _json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f".{uuid.uuid4().hex}.tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def load_deployment(path):
    path = Path(path).resolve()
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    required = {"schema_version", "mode", "start_date", "history_start", "financial_start", "research_config",
                "model_path", "model_manifest_sha256", "cache_dir", "output_root", "env_file",
                "corrections_path", "evidence_dir", "required_operating_days"}
    if not isinstance(raw, dict) or set(raw) != required or raw["schema_version"] != 1 or raw["mode"] != "paper":
        raise ValueError("daily deployment requires exactly the versioned Paper-only fields")
    for field in ("start_date", "history_start", "financial_start"):
        raw[field] = date.fromisoformat(raw[field])
    if not raw["financial_start"] <= raw["history_start"] < raw["start_date"]:
        raise ValueError("financial/history/start dates must be ordered")
    if type(raw["required_operating_days"]) is not int or raw["required_operating_days"] < 2:
        raise ValueError("operating acceptance requires at least two real sessions")
    for key in ("research_config", "model_path", "cache_dir", "output_root", "env_file", "corrections_path", "evidence_dir"):
        value = Path(raw[key]).expanduser()
        raw[key] = (value if value.is_absolute() else path.parent / value).resolve()
    manifest = raw["model_path"] / "model_manifest.json"
    if hashlib.sha256(manifest.read_bytes()).hexdigest() != raw["model_manifest_sha256"]:
        raise ValueError("deployed model manifest changed; promote a new deployment explicitly")
    return raw


@contextmanager
def operation_lock(root):
    """OS-held lock releases on process death; a stale filename is harmless."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    with (root / "operation.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def prepare_snapshot(deployment, policy, day, provider):
    from aipha.data.tushare_pit_provider import TuSharePITProvider
    spec = SnapshotSpec(start_date=deployment["history_start"].strftime("%Y%m%d"), end_date=day.strftime("%Y%m%d"),
                        index_code=universe_index_code(policy.universe), benchmark_code=universe_index_code(policy.universe),
                        feature_version=policy.feature_version, financial_conflict_policy=policy.financial_conflict_policy)
    pit = TuSharePITProvider(provider, conflict_policy=policy.financial_conflict_policy,
                            history_start=deployment["financial_start"].strftime("%Y%m%d"), history_end=spec.end_date)
    snapshot = build_tushare_snapshot(provider, spec, pit_provider=pit)
    # Future exchange open/closed flags establish the actual last session of
    # this month. They supply no future prices or financial observations.
    calendar = provider.calendar
    start = snapshot.metadata["listing_calendar"]["start_date"]
    calendar = calendar.loc[calendar.cal_date.ge(pd.Timestamp(start))]
    snapshot.metadata["listing_calendar"] = {**snapshot.metadata["listing_calendar"],
        "end_date": str(calendar.cal_date.max().date()), "content_sha256": frame_content_sha256(calendar),
        "open_sessions": calendar.loc[calendar.is_open.eq(1), "cal_date"].dt.strftime("%Y-%m-%d").tolist()}
    snapshot.metadata.update(attest_snapshot_metadata(snapshot.panel, snapshot.metadata))
    from .corporate_actions import read_corporate_actions
    identities = {event.action_id for event in read_corporate_actions(snapshot.panel)}
    records = json.loads(deployment["corrections_path"].read_text(encoding="utf-8"))
    records = [r for r in records if r["before"]["action_id"] in identities]
    if records:
        evidence = {r["evidence_sha256"]: (deployment["evidence_dir"] / (r["evidence_sha256"] + ".txt")).read_bytes()
                    for r in records}
        snapshot = correct_snapshot_action_units(snapshot, records, evidence, factor_rounding_unit=.001)
    snapshot.panel.attrs["snapshot_metadata"] = snapshot.metadata
    return snapshot


def operate_day(deployment, *, execute=False, now=None, preflight_date=None, provider_factory=None, clock=None):
    """Historical preflight never commits; only today's close is operational."""
    if now is not None and clock is not None:
        raise ValueError("provide a fixed fixture time or a clock, not both")
    if clock is None:
        clock = (lambda: now) if now is not None else (lambda: datetime.now(timezone.utc))
    now = clock()
    if now.tzinfo is None:
        raise ValueError("operation clock must have a timezone")
    local = now.astimezone(SHANGHAI)
    if preflight_date is not None and execute:
        raise ValueError("historical preflight cannot commit an operating receipt")
    day = preflight_date or local.date()
    root = deployment["output_root"]
    if preflight_date is not None:
        root = root / "preflight"
    with operation_lock(root):
        def progress(phase):
            _json(root / "progress.json", {"phase": phase, "date": str(day), "pid": os.getpid(),
                                          "observed_at": datetime.now(timezone.utc).isoformat()})

        def skipped(status, **fields):
            result = {"status": status, "date": str(day), "operational": False,
                      "broker_contacted": False, **fields}
            _json(root / "latest.json", result)
            progress(status)
            return result

        progress("checking_calendar")
        from aipha.utils.paths import KILL_SWITCH_PATH
        if KILL_SWITCH_PATH.exists() or (deployment["output_root"] / "STOP").exists():
            raise ValueError("Paper kill switch is active")
        if preflight_date is None and day < deployment["start_date"]:
            return skipped("not_started", start_date=str(deployment["start_date"]))
        if preflight_date is None and local.time() < time(18):
            return skipped("before_daily_window")
        month_end = (pd.Timestamp(day) + pd.offsets.MonthEnd(0)).strftime("%Y%m%d")
        if provider_factory is None:
            from .daily_inputs import OperatingEquityProvider
            provider_factory = OperatingEquityProvider
        provider = provider_factory(cache_dir=str(deployment["cache_dir"]), archive_dir=str(root / "raw_archive"), calendar_end=month_end)
        sessions = provider.calendar.loc[provider.calendar.is_open.eq(1), "cal_date"].dt.date.tolist()
        if day not in sessions:
            return skipped("market_closed")
        book = root / "paper.sqlite3"
        if book.exists():
            previous = read_paper_book_evidence(book)["receipts"][-1]["trade_date"]
            if previous != str(day) and previous != str(max(d for d in sessions if d < day)):
                raise ValueError("missing operating session: reconcile explicitly; no silent historical catch-up")
        elif execute and day != deployment["start_date"]:
            raise ValueError("first operating close was missed; choose a new explicit start date")
        policy = load_research_config(deployment["research_config"])
        directory = root / "sessions" / str(day)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "snapshot.parquet"
        if path.exists():
            metadata = verify_attested_parquet(path, require_artifact_hash=True, require_source_signature=True)
            panel = pd.read_parquet(path)
            panel.attrs["snapshot_metadata"] = metadata
        else:
            progress("preparing_tushare_snapshot")
            snapshot = prepare_snapshot(deployment, policy, day, provider)
            snapshot.write(path)
            panel, metadata = snapshot.panel, snapshot.metadata
        if (pd.Timestamp(metadata["end_date"]).date() != day
                or pd.Timestamp(panel.trade_date.max()).date() != day):
            raise ValueError("operating snapshot does not reach the requested close")
        progress("loading_pinned_model")
        selector = load_verified_selector(deployment["model_path"], expected_feature_version=policy.feature_version,
            expected_model_type=policy.model_type, expected_universe=policy.universe,
            expected_min_listing_days=policy.min_listing_days, expected_feature_groups=policy.feature_groups,
            expected_feature_column_groups=policy.feature_column_groups)
        if selector.research_manifest["model_config_hash"] != policy.model_config_hash():
            raise ValueError("research config differs from the pinned model's configuration")
        if any(pd.Timestamp(fold["train_end"]).date() >= day for fold in selector._wf_results):
            raise ValueError("operating model training reaches the signal date")
        compute = panel.copy(deep=False)
        compute.attrs = {}
        current = compute.loc[pd.to_datetime(compute.trade_date).dt.date.eq(day)].copy(deep=False)
        current = filter_historical_universe(current, min_listing_days=policy.min_listing_days)
        scores = selector.predict(current).to_numpy()
        if len(scores) < policy.top_k or not np.isfinite(scores).all() or np.unique(scores).size < 2:
            raise ValueError("missing or degenerate operating cross-section")
        model_id = selector.research_manifest["model_id"]
        run_id = "paper-" + str(day) + "-" + deployment["model_manifest_sha256"][:12]
        current["score"] = scores
        predictions = current[["trade_date", "symbol", "score"]].assign(model_id=model_id, feature_version=policy.feature_version)
        predictions["rank"] = predictions.score.rank(method="first", ascending=False).astype(int)
        prediction_path = directory / "predictions.parquet"
        if prediction_path.exists():
            if frame_content_sha256(pd.read_parquet(prediction_path)) != frame_content_sha256(predictions):
                raise ValueError("operating predictions changed after preparation")
        else:
            predictions.to_parquet(prediction_path, index=False)
        targets = build_target_positions(current, top_k=policy.top_k, max_position_weight=policy.max_position_weight,
            max_industry_weight=policy.max_industry_weight, max_style_exposure=policy.max_style_exposure,
            style_reference=current, run_id=run_id, model_id=model_id, rebalance_dates=monthly_rebalance_dates(metadata))
        plan = build_execution_plan(targets, mode="paper", run_id=run_id, model_id=model_id) if targets else None
        plan_path = directory / "plan.json"
        if plan_path.exists():
            if plan is None or read_execution_plan(plan_path) != plan:
                raise ValueError("operating plan changed after preparation")
        elif plan is not None:
            plan.write(plan_path)
        if KILL_SWITCH_PATH.exists() or (deployment["output_root"] / "STOP").exists():
            raise ValueError("Paper kill switch was activated during preparation")
        if execute:
            commit_time = clock()
            if commit_time.tzinfo is None:
                raise ValueError("operation clock must have a timezone")
            commit_local = commit_time.astimezone(SHANGHAI)
            if commit_local.date() != day or commit_local.time() < time(18) or commit_time < now:
                raise ValueError("operating close expired or clock moved backwards; no historical commit")
        progress("committing_book" if execute else "previewing_book")
        receipt = advance_paper_book(book, panel, policy, plan=plan, commit=execute)
        result = {"status": "committed" if execute else "preview", "date": str(day), "operational": bool(execute),
            "run_id": run_id, "model_id": model_id, "predictions": len(predictions), "targets": len(targets),
            "book_path": str(book), "snapshot_path": str(path), "broker_contacted": False, "receipt": receipt}
        _json(directory / ("operation.json" if execute else "preview.json"), result)
        _json(root / "latest.json", {k: v for k, v in result.items() if k != "receipt"})
        progress("complete")
        return result
