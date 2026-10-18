"""Portfolio budgets use the same historical cross-section in every consumer."""
from dataclasses import asdict, replace
import hashlib
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest
import yaml

from aipha.research import ResearchConfig, build_target_positions
from aipha.research.backtest import run_portfolio_backtest
from aipha.research.config import load_research_config
from aipha.research.execution_plan import build_execution_plan, read_execution_plan
from aipha.research.paper_book import advance_paper_book, replay_paper_book
from aipha.research.snapshots import attest_snapshot_metadata, point_in_time_adjust_prices, write_attested_parquet
from aipha.research.style import style_zscores, validate_target_styles


def _snapshot(end="2024-02-01"):
    frame = pd.DataFrame([
        dict(trade_date=day, symbol=f"60000{i}.SH", open=10., high=11., low=9., close=10.,
             volume=10000., amount=100000., adj_factor=1., up_limit=11., down_limit=9.,
             is_index_member=True, is_st=False, is_suspended=False, is_limit_up=False, is_limit_down=False,
             listing_days=400, listing_days_basis="exchange_open_sessions", feature_version="cn_equity_v1",
             log_market_cap=float(i + 1))
        for day in pd.to_datetime(["2024-01-31", "2024-02-01"]) if day <= pd.Timestamp(end)
        for i in range(4)
    ])
    return _attest(point_in_time_adjust_prices(frame))


def _attest(frame):
    frame = frame.copy()
    frame.attrs["snapshot_metadata"] = attest_snapshot_metadata(frame, {
        "provider": "tushare_pit", "data_snapshot_id": "style-fixture", "feature_version": "cn_equity_v1",
        "index_code": "000905.SH", "start_date": "2024-01-31",
        "end_date": str(pd.to_datetime(frame.trade_date).max().date()),
        "listing_calendar": {"provider": "tushare", "endpoint": "trade_cal", "exchange": "SSE",
                             "start_date": "2024-01-31", "end_date": "2024-02-01",
                             "open_sessions": ["2024-01-31", "2024-02-01"]},
    })
    return frame


def _scores():
    frame = _snapshot("2024-01-31")[["trade_date", "symbol"]].copy()
    frame["score"] = np.arange(4, dtype=float)
    frame["fwd_return"] = .01 + np.arange(4) * .001
    return frame


def _targets(reference=None, **kwargs):
    return build_target_positions(_scores(), top_k=2, max_position_weight=.5,
                                  max_style_exposure={"log_market_cap": .1},
                                  style_reference=_snapshot() if reference is None else reference,
                                  run_id="style-run", model_id="style-model", **kwargs)


def _policy():
    return ResearchConfig(top_k=2, max_position_weight=.5, max_industry_weight=None,
                          max_style_exposure={"log_market_cap": .1}, initial_capital=100000)


def test_absolute_style_budget_reweights_shortlist_and_keeps_zero_targets():
    targets = _targets()
    # Top two both have positive size exposure. Maximize invested capital by
    # retaining the less extreme name, while the zero target expresses cash.
    weights = {target.symbol: target.target_weight for target in targets}
    assert weights["600003.SH"] == 0
    assert weights["600002.SH"] == pytest.approx(.1 * np.sqrt(1.25) / .5)
    diagnostic, = validate_target_styles(targets, _snapshot(), {"log_market_cap": .1})
    assert diagnostic["weighted_zscores"]["log_market_cap"] == pytest.approx(.1)
    assert diagnostic["invested_weight"] < 1


def test_balanced_allocation_retains_capital_and_respects_industry_and_stock_caps():
    reference = _snapshot().assign(industry=["a", "b", "a", "b"] * 2)
    scores = _scores().assign(score=[4., 1., 2., 3.], industry=["a", "b", "a", "b"])
    targets = build_target_positions(scores, top_k=2, max_position_weight=.5, max_industry_weight=.5,
                                     max_style_exposure={"log_market_cap": 0.}, style_reference=reference)
    assert [target.target_weight for target in targets] == [.5, .5]
    assert validate_target_styles(targets, reference, {"log_market_cap": 0.})[0]["invested_weight"] == 1.


def test_both_signed_exposure_limits_are_enforced_and_all_cash_is_explicit():
    scores = _scores().assign(score=[4., 3., 2., 1.])
    negative = build_target_positions(scores, top_k=2, max_position_weight=.5,
                                     max_style_exposure={"log_market_cap": .1}, style_reference=_snapshot())
    assert validate_target_styles(negative, _snapshot(), {"log_market_cap": .1})[0]["weighted_zscores"]["log_market_cap"] == pytest.approx(-.1)
    cash = build_target_positions(_scores(), top_k=1, max_style_exposure={"log_market_cap": 0.},
                                 style_reference=_snapshot())
    assert len(cash) == 1 and cash[0].target_weight == 0.


def test_style_normalization_uses_full_signal_cross_section_and_is_causal():
    first = style_zscores(_snapshot(), {"log_market_cap": .1}, ["2024-01-31"])
    frame = _snapshot()
    frame.loc[frame.trade_date.eq(pd.Timestamp("2024-02-01")), "log_market_cap"] = 1e10
    other = style_zscores(frame.sample(frac=1, random_state=3), {"log_market_cap": .1}, ["2024-01-31"])
    pd.testing.assert_frame_equal(first.sort_values("symbol").reset_index(drop=True),
                                  other.sort_values("symbol").reset_index(drop=True))
    expected = (np.arange(1., 5.) - 2.5) / np.sqrt(1.25)
    assert first.log_market_cap.to_numpy() == pytest.approx(expected)
    constant = style_zscores(_snapshot().assign(log_market_cap=4.), {"log_market_cap": 0}, ["2024-01-31"])
    assert constant.log_market_cap.eq(0).all()


@pytest.mark.parametrize("bad", [None, {"close": 1}, {"unknown": .2}, {"log_market_cap": True},
                                     {"log_market_cap": "0.2"}, {"log_market_cap": -1},
                                     {"log_market_cap": float("nan")}, {"log_market_cap": float("inf")}])
def test_invalid_style_policy_rejected(bad):
    with pytest.raises(ValueError, match="max_style_exposure"):
        ResearchConfig(max_style_exposure=bad)


@pytest.mark.parametrize("change", ["missing_column", "missing_symbol", "nan", "duplicate", "missing_date"])
def test_incomplete_reference_cannot_silently_disable_limit(change):
    frame = _snapshot()
    if change == "missing_column":
        frame = frame.drop(columns="log_market_cap")
    elif change == "missing_symbol":
        frame = frame.loc[frame.symbol.ne("600003.SH")]
    elif change == "nan":
        frame.loc[0, "log_market_cap"] = np.nan
    elif change == "duplicate":
        frame = pd.concat([frame, frame.iloc[:1]])
    else:
        frame = frame.loc[frame.trade_date.eq(pd.Timestamp("2024-02-01"))]
    with pytest.raises(ValueError, match="style"):
        _targets(frame)
    with pytest.raises(ValueError, match="style_reference"):
        build_target_positions(_scores(), max_style_exposure={"log_market_cap": .1})


def test_yaml_and_hash_bind_style_policy_but_preserve_old_default_hash(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump({"portfolio": {"max_style_exposure": {"log_market_cap": .1}}}))
    assert load_research_config(path).max_style_exposure == (("log_market_cap", .1),)
    default = ResearchConfig()
    assert ResearchConfig(max_style_exposure=[]).max_style_exposure == ()
    legacy = asdict(default)
    legacy.pop("max_style_exposure")
    legacy.pop("financial_conflict_policy")
    expected = hashlib.sha256(json.dumps(legacy, sort_keys=True, ensure_ascii=False, default=list).encode()).hexdigest()[:16]
    assert default.model_config_hash() == expected
    assert replace(default, financial_conflict_policy="quarantine").model_config_hash() != expected
    assert _policy().model_config_hash() != replace(_policy(), max_style_exposure={}).model_config_hash()


def test_paper_and_direct_backtest_reject_unconstrained_plan_before_writes(tmp_path):
    unsafe = build_target_positions(_scores(), top_k=2, max_position_weight=.5, run_id="run", model_id="model")
    book = tmp_path / "paper.sqlite3"
    with pytest.raises(ValueError, match="max_style_exposure"):
        advance_paper_book(book, _snapshot("2024-01-31"), _policy(), plan=build_execution_plan(unsafe), commit=True)
    assert not book.exists()
    with pytest.raises(ValueError, match="max_style_exposure"):
        run_portfolio_backtest(_snapshot(), _scores(), unsafe, _policy())


def test_paper_replay_freezes_style_reference_and_remains_readonly(tmp_path):
    targets = _targets()
    book = tmp_path / "paper.sqlite3"
    plan = build_execution_plan(targets)
    advance_paper_book(book, _snapshot("2024-01-31"), _policy(), plan=plan, commit=True)
    before = book.read_bytes()
    changed = _snapshot()
    changed.loc[0, "log_market_cap"] += .1
    with pytest.raises(ValueError, match="changed or removed"):
        advance_paper_book(book, _attest(changed), _policy(), commit=True)
    assert book.read_bytes() == before
    executed = advance_paper_book(book, _snapshot(), _policy(), commit=True)
    assert executed["new_trades"]
    before = book.read_bytes()
    assert replay_paper_book(book)["trades"] == executed["new_trades"]
    assert book.read_bytes() == before


def test_book_without_optional_style_field_can_still_append(tmp_path):
    policy = replace(_policy(), max_style_exposure={})
    book = tmp_path / "legacy-default.sqlite3"
    advance_paper_book(book, _snapshot("2024-01-31"), policy, commit=True)
    with sqlite3.connect(book) as connection:
        config = json.loads(connection.execute("SELECT value FROM settings WHERE key='config'").fetchone()[0])
        config.pop("max_style_exposure")
        connection.execute("UPDATE settings SET value=? WHERE key='config'", (json.dumps(config),))
    advance_paper_book(book, _snapshot(), policy, commit=True)
    assert len(replay_paper_book(book)["daily_returns"]) == 2


@pytest.mark.parametrize("action", ["export", "evaluate"])
def test_bridge_requires_matching_complete_snapshot_and_uses_style_config(tmp_path, action):
    from aipha.engines.aipha_ml_engine import run

    reference = _snapshot()
    panel = tmp_path / "snapshot.parquet"
    write_attested_parquet(reference, panel, reference.attrs["snapshot_metadata"])
    scores = tmp_path / "scores.parquet"
    write_attested_parquet(_scores(), scores, {
        "provider": "aipha_research", "data_snapshot_id": "style-fixture", "feature_version": "cn_equity_v1",
        "start_date": "2024-01-31", "end_date": "2024-01-31",
    })
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump({"portfolio": {"top_k": 2, "max_position_weight": .5,
                                                       "max_style_exposure": {"log_market_cap": .1}}}))
    args = dict(action=action, configPath=str(config), dataPath=str(scores),
                runId="run", modelId="model", outputPath=str(tmp_path / "targets.json"))
    missing = run(args)
    assert not missing["ok"] and "featurePath" in missing["error"]
    args["featurePath"] = str(panel)
    result = run(args)
    assert result["ok"], result
    if action == "export":
        plan = read_execution_plan(result["data"]["executionPlanPath"])
        assert sum(target.target_weight for target in plan.targets) < 1
        validate_target_styles(plan.targets, reference, _policy().max_style_exposure)
    metadata = dict(reference.attrs["snapshot_metadata"], data_snapshot_id="other")
    write_attested_parquet(reference, panel, metadata)
    mismatch = run(args)
    assert not mismatch["ok"] and "data_snapshot_id" in mismatch["error"]
