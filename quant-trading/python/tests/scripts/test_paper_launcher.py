"""Batch dispatch and offline canonical-book tests; no network or broker."""
from __future__ import annotations

import json
import subprocess

import pandas as pd
import pytest
import yaml

from scripts import paper_launcher as launcher


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    monkeypatch.setattr(launcher, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(launcher, "PAPER_CONFIG_DIR", tmp_path / "profiles")
    monkeypatch.setattr(launcher, "KILL_SWITCH_PATH", tmp_path / "kill.flag")
    launcher.PAPER_CONFIG_DIR.mkdir()
    policy = tmp_path / "research.yaml"
    policy.write_text("execution:\n  mode: paper\n", encoding="utf-8")
    (tmp_path / "2024-01-31.parquet").write_bytes(b"not read during command preparation")
    (tmp_path / "plans").mkdir()
    raw = {"research_paper": {
        "schema_version": 1, "research_config": "research.yaml",
        "snapshot_path": "{date}.parquet", "book_path": "books/ml.sqlite3", "plan_dir": "plans",
    }}
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"paper_trading_enabled": True, "paper_auto_start": True,
                                    "selected_portfolios": ["ml"]}), encoding="utf-8")
    profile = launcher.PAPER_CONFIG_DIR / "ml.yml"
    profile.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return tmp_path, raw, profile, settings


def _rewrite(profile, raw):
    profile.write_text(yaml.safe_dump(raw), encoding="utf-8")


def _settings(path, **updates):
    raw = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**raw, **updates}), encoding="utf-8")


def test_dispatch_selects_only_dated_plan_and_requires_explicit_commit(deployment):
    root, raw, profile, _ = deployment
    (root / "plans/2024-01-30.json").write_text("old plan", encoding="utf-8")
    spec = launcher.prepare_launch(profile, trade_date="2024-01-31")
    assert spec["engine"] == "research_paper"
    assert "--execute" not in spec["command"] and "--plan-path" not in spec["command"]
    assert spec["book_path"] == str(root / "books/ml.sqlite3")
    assert not (root / "books").exists()
    plan = root / "plans/2024-01-31.json"
    plan.write_text("dated plan", encoding="utf-8")
    cmd = launcher.prepare_launch(profile, dry_run=False, trade_date="2024-01-31")["command"]
    assert "--execute" in cmd and cmd[cmd.index("--plan-path") + 1] == str(plan)
    assert cmd[cmd.index("--panel") + 1] == str(root / "2024-01-31.parquet")


def test_default_date_is_today_in_shanghai(deployment, monkeypatch):
    from datetime import datetime

    root, _, profile, _ = deployment
    zones = []
    class Clock:
        @staticmethod
        def now(zone):
            zones.append(str(zone))
            return datetime(2024, 1, 31, 16, 0, tzinfo=zone)
    monkeypatch.setattr(launcher, "datetime", Clock)
    cmd = launcher.prepare_launch(profile)["command"]
    assert zones == ["Asia/Shanghai"]
    assert cmd[cmd.index("--date") + 1] == "2024-01-31"


@pytest.mark.parametrize("change,error", [
    ({"schema_version": True}, "schema_version"),
    ({"schema_version": 2}, "schema_version"),
    ({"mode": "live"}, "fields"),
    ({"snapshot_path": "missing.parquet"}, "must exist"),
    ({"book_path": "books/{date}.sqlite3"}, "only snapshot/plan"),
    ({"book_path": "state.json"}, ".sqlite3"),
    ({"plan_dir": "absent"}, "directory not found"),
    ({"plan_path": "absent.json"}, "not both"),
])
def test_invalid_profile_never_starts_process(deployment, monkeypatch, change, error):
    _, raw, profile, settings = deployment
    raw["research_paper"].update(change)
    _rewrite(profile, raw)
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: pytest.fail("launched invalid profile"))
    with pytest.raises((OSError, ValueError), match=error):
        launcher.prepare_launch(profile, trade_date="2024-01-31")
    assert launcher.main(["--settings", str(settings), "--date", "2024-01-31", "--execute"]) == 1


def test_explicit_missing_plan_fails_and_live_policy_is_rejected(deployment):
    root, raw, profile, _ = deployment
    raw["research_paper"].pop("plan_dir")
    raw["research_paper"]["plan_path"] = "plans/{trade_date}.json"
    _rewrite(profile, raw)
    with pytest.raises(FileNotFoundError, match="explicit Paper plan"):
        launcher.prepare_launch(profile, trade_date="2024-01-31")
    (root / "plans/20240131.json").write_text("not read by preparation", encoding="utf-8")
    (root / "research.yaml").write_text("execution:\n  mode: live\n", encoding="utf-8")
    with pytest.raises(ValueError, match="execution.mode must be paper"):
        launcher.prepare_launch(profile, trade_date="2024-01-31")


@pytest.mark.parametrize("gate", ["kill", "disabled", "autostart", "string_boolean"])
def test_safety_gates_prevent_any_subprocess(deployment, monkeypatch, gate):
    _, _, _, settings = deployment
    if gate == "kill":
        launcher.KILL_SWITCH_PATH.touch()
    elif gate == "disabled":
        _settings(settings, paper_trading_enabled=False)
    elif gate == "autostart":
        _settings(settings, paper_auto_start=False)
    else:
        _settings(settings, paper_trading_enabled="false")
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: pytest.fail("bypassed gate"))
    assert launcher.main(["--settings", str(settings), "--execute", "--date", "2024-01-31"]) == 0
    if gate != "autostart":
        assert launcher.main(["--settings", str(settings), "--execute", "--force"]) == 0


def test_force_overrides_only_autostart_and_default_previews(deployment, monkeypatch):
    _, _, _, settings = deployment
    calls = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda cmd, **kwargs: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    _settings(settings, paper_auto_start=False)
    assert launcher.main(["--settings", str(settings), "--force", "--date", "2024-01-31"]) == 0
    assert len(calls) == 1 and "--execute" not in calls[0]
    assert launcher.main(["--settings", str(settings), "--force", "--date", "2024-01-31", "--execute"]) == 0
    assert "--execute" in calls[1]


def test_list_never_starts_process_and_missing_profile_is_not_success(deployment, monkeypatch, capsys):
    _, _, _, settings = deployment
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: pytest.fail("list spawned process"))
    assert launcher.main(["--settings", str(settings), "--list", "--date", "2024-01-31"]) == 0
    assert "research_paper" in capsys.readouterr().out
    _settings(settings, selected_portfolios=["missing"])
    assert launcher.main(["--settings", str(settings), "--list"]) == 1
    assert launcher.main(["--settings", str(settings), "--execute"]) == 1


def test_shared_book_or_one_invalid_profile_stops_entire_batch(deployment, monkeypatch):
    _, raw, profile, settings = deployment
    second = profile.with_name("other.yml")
    _rewrite(second, raw)
    _settings(settings, selected_portfolios=["ml", "other"])
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: pytest.fail("partial invalid batch launched"))
    assert launcher.main(["--settings", str(settings), "--execute", "--date", "2024-01-31"]) == 1
    second.write_text("broken: [", encoding="utf-8")
    assert launcher.main(["--settings", str(settings), "--execute", "--date", "2024-01-31"]) == 1


@pytest.mark.parametrize("change", ["kill", "settings", "selection"])
def test_operator_can_stop_remaining_jobs_between_subprocesses(deployment, monkeypatch, change):
    _, raw, profile, settings = deployment
    other = {"research_paper": {**raw["research_paper"], "book_path": "books/other.sqlite3"}}
    _rewrite(profile.with_name("other.yml"), other)
    _settings(settings, selected_portfolios=["ml", "other"])
    calls = []
    def run(cmd, **kwargs):
        calls.append(cmd)
        if change == "kill":
            launcher.KILL_SWITCH_PATH.touch()
        elif change == "settings":
            _settings(settings, paper_trading_enabled=False)
        else:
            _settings(settings, selected_portfolios=["ml"])
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.main(["--settings", str(settings), "--execute", "--date", "2024-01-31"]) == 1
    assert len(calls) == 1


def test_legacy_dispatch_is_explicit_preview_and_cannot_fake_date_replay(deployment):
    _, _, profile, _ = deployment
    _rewrite(profile, {"ml": {"legacy_ml_compat": True, "model_dir": "old"}})
    spec = launcher.prepare_launch(profile)
    assert spec["engine"] == "legacy_eod" and "--dry-run" in spec["command"]
    assert "--dry-run" not in launcher.prepare_launch(profile, dry_run=False)["command"]
    with pytest.raises(ValueError, match="legacy EOD cannot replay"):
        launcher.prepare_launch(profile, trade_date="2024-01-31")


def test_timeouts_and_child_failures_are_reported(deployment, monkeypatch):
    _, _, profile, _ = deployment
    def timeout(*a, **k):
        raise subprocess.TimeoutExpired(a[0], 1)
    monkeypatch.setattr(launcher.subprocess, "run", timeout)
    result = launcher.launch_one(profile, timeout=1, trade_date="2024-01-31")
    assert not result["ok"] and result["returncode"] == -1
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a[0], 2))
    result = launcher.launch_one(profile, trade_date="2024-01-31")
    assert not result["ok"] and result["returncode"] == 2


def test_real_subprocess_preview_commit_next_open_and_stale_snapshot_rejection(deployment, monkeypatch):
    from aipha.research.contracts import TargetPosition
    from aipha.research.execution_plan import build_execution_plan
    from aipha.research.paper_book import replay_paper_book
    from aipha.research.snapshots import point_in_time_adjust_prices, write_attested_parquet

    root, _, _, settings = deployment
    monkeypatch.setenv("AIPHA_PIT_SNAPSHOT_KEY", "offline-launcher-test-signing-key-only-12345")
    (root / "research.yaml").write_text(yaml.safe_dump({
        "portfolio": {"initial_capital": 100000, "top_k": 1, "max_position_weight": 1,
                      "max_industry_weight": 1, "max_daily_turnover": 2},
        "cost": {"slippage_bps": 0, "commission_rate": 0, "commission_min": 0,
                 "stamp_tax_rate": 0, "transfer_fee_rate": 0},
    }), encoding="utf-8")
    frame = pd.DataFrame([
        dict(trade_date=day, symbol="600000.SH", open=10., high=12., low=9., close=11.,
             volume=10000., amount=100000., adj_factor=1., is_suspended=False,
             is_limit_up=False, is_limit_down=False, up_limit=100., down_limit=.01,
             is_index_member=True, is_st=False, listing_days=400,
             listing_days_basis="exchange_open_sessions", industry="bank", feature_version="cn_equity_v1")
        for day in pd.to_datetime(["2024-01-31", "2024-02-01"])
    ])
    calendar = {"provider": "tushare", "endpoint": "trade_cal", "exchange": "SSE",
                "start_date": "2024-01-31", "end_date": "2024-02-01",
                "open_sessions": ["2024-01-31", "2024-02-01"]}
    for day in ["2024-01-31", "2024-02-01"]:
        part = point_in_time_adjust_prices(frame[frame.trade_date.le(pd.Timestamp(day))].reset_index(drop=True))
        write_attested_parquet(part, root / f"{day}.parquet", {
            "provider": "tushare_pit", "feature_version": "cn_equity_v1", "index_code": "000905.SH",
            "data_snapshot_id": f"offline-launch:{day}", "start_date": "2024-01-31", "end_date": day,
            "listing_calendar": calendar,
        })
    plan = build_execution_plan([
        TargetPosition(pd.Timestamp("2024-01-31").date(), "600000.SH", .5, "test",
                       run_id="offline-run", model_id="offline-model", signal_id="offline-signal"),
    ])
    plan_path = root / "plans/2024-01-31.json"
    plan_path.write_text(json.dumps(plan.to_dict(), default=str), encoding="utf-8")
    base = ["--settings", str(settings)]
    assert launcher.main([*base, "--date", "2024-01-31"]) == 0
    book = root / "books/ml.sqlite3"
    assert not book.parent.exists()
    assert launcher.main([*base, "--date", "2024-01-31", "--execute"]) == 0
    before = book.read_bytes()
    assert launcher.main([*base, "--date", "2024-02-01"]) == 0
    assert book.read_bytes() == before
    assert launcher.main([*base, "--date", "2024-02-01", "--execute"]) == 0
    replay = replay_paper_book(book)
    trade, = replay["trades"]
    assert trade["trade_date"] == "2024-02-01" and trade["price"] == 10.
    assert (trade["run_id"], trade["model_id"], trade["signal_id"]) == (
        "offline-run", "offline-model", "offline-signal",
    )
    assert replay["daily_returns"][-1]["nav"] == 105000
    # A dated filename cannot disguise yesterday's snapshot as today's close.
    from shutil import copyfile
    for suffix in [".parquet", ".metadata.json"]:
        copyfile(root / ("2024-02-01" + suffix), root / ("2024-02-02" + suffix))
    before = book.read_bytes()
    assert launcher.main([*base, "--date", "2024-02-02", "--execute"]) == 1
    assert book.read_bytes() == before
