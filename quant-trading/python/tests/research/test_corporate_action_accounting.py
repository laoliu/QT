from __future__ import annotations

import json
import hashlib
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from aipha.research.backtest import run_portfolio_backtest
from aipha.research.contracts import TargetPosition
from aipha.research.corporate_actions import (
    ACTION_COLUMN, CorporateAction, attach_corporate_actions,
    normalize_tushare_dividends, read_corporate_actions,
)
from aipha.research.pipeline import ResearchConfig
from aipha.research.snapshots import frame_content_sha256, point_in_time_adjust_prices


def _market() -> pd.DataFrame:
    return pd.DataFrame([
        {"trade_date": day, "symbol": symbol,
         "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0,
         "volume": 10000.0, "amount": 100000.0, "adj_factor": 1.0,
         "is_suspended": False, "is_limit_up": False, "is_limit_down": False,
         "up_limit": 100.0, "down_limit": 0.01}
        for day in pd.bdate_range("2024-01-31", periods=6)
        for symbol in ("600000.SH", "600001.SH")
    ])


def _action(**overrides) -> CorporateAction:
    return CorporateAction(**{
        "action_id": "dividend-1", "symbol": "600000.SH", "announce_date": "2024-01-30",
        "record_date": "2024-02-01", "ex_date": "2024-02-02",
        "cash_per_share": 1.0, "pay_date": "2024-02-06", **overrides,
    })


def _replay(market, *, rotate=None, capital=100000):
    targets = [TargetPosition(pd.Timestamp("2024-01-31").date(), "600000.SH", 1.0, "entry")]
    if rotate:
        targets.append(TargetPosition(pd.Timestamp(rotate).date(), "600001.SH", 1.0, "rotate"))
    config = ResearchConfig(
        initial_capital=capital, max_position_weight=1.0, max_daily_turnover=2.0,
        slippage_bps=0, commission_rate=0, commission_min=0,
        transfer_fee_rate=0, stamp_tax_rate=0,
    )
    return run_portfolio_backtest(market, pd.DataFrame(), targets, config)


def _with_action(action=None):
    action = action or _action()
    market = _market()
    after = market.symbol.eq(action.symbol) & market.trade_date.ge(pd.Timestamp(action.ex_date))
    price = (10.0 - action.cash_per_share) / (1 + action.bonus_per_share)
    market.loc[after, ["open", "high", "low", "close"]] = price
    market.loc[after, "adj_factor"] = 10.0 / price
    return attach_corporate_actions(point_in_time_adjust_prices(market), [action])


def test_raw_lots_and_costs_are_independent_of_constant_adjustment_anchor() -> None:
    raw = _market()
    expected = _replay(raw, rotate="2024-02-02")
    raw["adj_factor"] = 7.0
    actual = _replay(point_in_time_adjust_prices(raw), rotate="2024-02-02")
    assert actual["trades"] == expected["trades"]
    assert actual["daily_returns"] == expected["daily_returns"]
    assert actual["trades"][0]["qty"] == 10000
    assert actual["trades"][0]["price"] == 10.0


def test_cash_dividend_is_receivable_then_paid_without_artificial_return() -> None:
    source = _with_action()
    source.attrs["audit_payload"] = {"events": [{"metric_name": "roe", "action": "missing"}]}
    result = _replay(source)
    assert source.attrs == {"audit_payload": {"events": [{"metric_name": "roe", "action": "missing"}]}}
    daily = pd.DataFrame(result["daily_returns"]).set_index("trade_date")
    assert daily.loc["2024-02-02", "cash"] == 0
    assert daily.loc["2024-02-02", "dividend_receivable"] == 10000
    assert daily.loc["2024-02-06", "cash"] == 10000
    assert daily.loc["2024-02-06", "dividend_receivable"] == 0
    np.testing.assert_allclose(daily.nav, 100000)
    assert result["accounting"]["cash_dividends_paid"] == 10000
    assert result["accounting"]["dividend_tax_policy"] == "gross_before_holding_period_tax"


def test_sale_after_record_date_retains_dividend_but_cannot_spend_it_early() -> None:
    result = _replay(_with_action(), rotate="2024-02-02")
    buys = [t for t in result["trades"] if t["side"] == "buy"]
    assert buys[-1]["symbol"] == "600001.SH"
    assert buys[-1]["qty"] == 9000
    assert result["daily_returns"][-1]["cash"] == 10000
    assert result["daily_returns"][-1]["nav"] == pytest.approx(100000)


def test_buy_on_ex_date_does_not_receive_prior_owners_dividend() -> None:
    market = _with_action()
    targets = [TargetPosition(pd.Timestamp("2024-02-01").date(), "600000.SH", 1.0, "entry")]
    result = run_portfolio_backtest(market, pd.DataFrame(), targets, ResearchConfig(max_position_weight=1, max_daily_turnover=2))
    assert result["accounting"]["cash_dividends_paid"] == 0
    assert result["corporate_actions"] == []


def test_bonus_shares_are_valued_at_ex_date_and_unavailable_until_listing() -> None:
    action = _action(cash_per_share=0, pay_date=None, bonus_per_share=0.1, share_list_date="2024-02-07")
    result = _replay(_with_action(action), rotate="2024-02-02")
    sells = [t for t in result["trades"] if t["side"] == "sell"]
    assert sells[0]["qty"] == 10000  # Pending 1000 shares cannot be sold on Feb 5.
    assert result["final_positions"]["600000.SH"] == 1000
    assert result["accounting"]["bonus_shares_delivered"] == 1000
    np.testing.assert_allclose([d["nav"] for d in result["daily_returns"]], 100000)


def test_bonus_odd_lot_can_be_fully_liquidated_after_listing() -> None:
    action = _action(cash_per_share=0, pay_date=None, bonus_per_share=0.13, share_list_date="2024-02-07")
    result = _replay(_with_action(action), rotate="2024-02-06", capital=1000)
    sells = [t for t in result["trades"] if t["side"] == "sell"]
    assert sells[0]["qty"] == 113
    assert "600000.SH" not in result["final_positions"]


def test_pending_bonus_shares_continue_marking_after_original_position_is_sold() -> None:
    action = _action(cash_per_share=0, pay_date=None, bonus_per_share=0.1, share_list_date="2024-02-07")
    market = _with_action(action)
    after_sale = market.symbol.eq("600000.SH") & market.trade_date.ge("2024-02-05")
    for column in ("open", "close"):
        market.loc[after_sale, f"{column}_raw"] = 8.0
        market.loc[after_sale, column] = 8.8
    result = _replay(market, rotate="2024-02-02")
    day = next(d for d in result["daily_returns"] if d["trade_date"] == "2024-02-05")
    assert day["share_receivable_value"] == pytest.approx(8000)
    assert result["final_positions"]["600000.SH"] == 1000


def test_sale_on_record_date_has_no_entitlement() -> None:
    action = _action(record_date="2024-02-02", ex_date="2024-02-05")
    result = _replay(_with_action(action), rotate="2024-02-01")
    assert result["accounting"]["cash_dividends_paid"] == 0


def test_dividend_past_run_end_remains_receivable() -> None:
    result = _replay(_with_action(_action(pay_date="2024-03-01")))
    assert result["daily_returns"][-1]["cash"] == 0
    assert result["daily_returns"][-1]["dividend_receivable"] == 10000
    assert result["daily_returns"][-1]["nav"] == pytest.approx(100000)


def test_successive_dividends_during_suspension_use_effective_raw_marks() -> None:
    first = _action()
    second = _action(action_id="dividend-2", record_date="2024-02-05", ex_date="2024-02-06", pay_date="2024-02-07")
    market = _market()
    stock = market.symbol.eq("600000.SH")
    suspended = stock & market.trade_date.isin(pd.to_datetime(["2024-02-02", "2024-02-05"]))
    market.loc[suspended, "is_suspended"] = True
    # The vendor carries an old close through suspension; it is not an
    # executable/current mark and must not replace the theoretical ex-mark.
    market.loc[stock & market.trade_date.ge("2024-02-02"), "adj_factor"] = 10 / 9
    after_second = stock & market.trade_date.ge("2024-02-06")
    market.loc[after_second, ["open", "high", "low", "close"]] = 8.0
    market.loc[after_second, "adj_factor"] = 10 / 8
    market = attach_corporate_actions(point_in_time_adjust_prices(market), [first, second])
    result = _replay(market)
    np.testing.assert_allclose([d["nav"] for d in result["daily_returns"]], 100000)
    assert result["accounting"]["cash_dividends_paid"] == 20000


@pytest.mark.parametrize("overrides", [
    {"record_date": None}, {"announce_date": "2024-02-03"},
    {"pay_date": "2024-02-01"}, {"cash_per_share": float("nan")},
])
def test_incomplete_or_noncausal_action_terms_are_rejected(overrides) -> None:
    with pytest.raises(ValueError):
        _action(**overrides)


def test_ex_date_without_symbol_bar_still_books_entitlement() -> None:
    market = _with_action()
    market = market[~(market.symbol.eq("600000.SH") & market.trade_date.eq("2024-02-02"))]
    result = _replay(market)
    np.testing.assert_allclose([d["nav"] for d in result["daily_returns"]], 100000)
    assert result["accounting"]["cash_dividends_paid"] == 10000


def test_unexplained_or_inconsistent_factor_change_is_rejected_while_held() -> None:
    market = _with_action()
    with pytest.raises(ValueError, match="unexplained adjustment"):
        _replay(market.drop(columns=ACTION_COLUMN))
    market.loc[market.symbol.eq("600000.SH") & market.trade_date.ge("2024-02-02"), "adj_factor"] = 2.0
    with pytest.raises(ValueError, match="does not reconcile"):
        _replay(market)


def test_fractional_bonus_allocation_is_never_silently_rounded() -> None:
    action = _action(cash_per_share=0, pay_date=None, bonus_per_share=0.001, share_list_date="2024-02-07")
    with pytest.raises(ValueError, match="fractional bonus"):
        _replay(_with_action(action), capital=1000)


def test_action_round_trip_is_covered_by_frozen_panel_hash(tmp_path) -> None:
    panel = _with_action()
    path = tmp_path / "snapshot.parquet"
    panel.to_parquet(path, index=False)
    restored = pd.read_parquet(path)
    assert read_corporate_actions(restored) == [_action()]
    assert frame_content_sha256(restored) == frame_content_sha256(panel)
    changed = attach_corporate_actions(restored, [replace(_action(), cash_per_share=0.5)])
    assert frame_content_sha256(changed) != frame_content_sha256(panel)
    duplicate = restored.copy()
    duplicate.loc[duplicate.symbol.eq("600000.SH"), ACTION_COLUMN] = json.dumps([_action().to_wire()])
    with pytest.raises(ValueError, match="duplicate"):
        read_corporate_actions(duplicate)


def test_tushare_dividend_mapping_preserves_dates_and_ignores_proposals() -> None:
    row = {"ts_code": "600000.SH", "end_date": "20231231", "ann_date": "20240101",
           "imp_ann_date": "20240130", "record_date": "20240201", "ex_date": "20240202",
           "pay_date": "20240206", "div_listdate": "20240207", "cash_div_tax": 1.0, "stk_div": 0.1}
    history = pd.DataFrame([{**row, "div_proc": "实施"}, {**row, "div_proc": "预案"}])
    action, = normalize_tushare_dividends(history)
    assert action.cash_per_share == 1.0 and action.bonus_per_share == 0.1
    assert action.announce_date.isoformat() == "2024-01-30"
    assert action.pay_date.isoformat() == "2024-02-06"
    assert action.share_list_date.isoformat() == "2024-02-07"
    with pytest.raises(ValueError, match="settlement"):
        normalize_tushare_dividends(history.drop(columns="pay_date"))


def test_tushare_null_distribution_requires_an_absent_settlement_leg() -> None:
    row = {"ts_code": "600000.SH", "end_date": "20231231", "ann_date": "20240130",
           "div_proc": "实施", "record_date": "20240201", "ex_date": "20240202",
           "pay_date": None, "div_listdate": "20240207", "cash_div_tax": np.nan, "stk_div": 0.3}
    action, = normalize_tushare_dividends(pd.DataFrame([row]))
    assert action.cash_per_share == 0 and action.bonus_per_share == 0.3
    with pytest.raises(ValueError, match="unknown.*amount"):
        normalize_tushare_dividends(pd.DataFrame([{**row, "pay_date": "20240206"}]))
    with pytest.raises(ValueError, match="unknown.*amount"):
        normalize_tushare_dividends(pd.DataFrame([{**row, "stk_div": np.nan}]))
    cash, = normalize_tushare_dividends(pd.DataFrame([
        {**row, "cash_div_tax": 1., "stk_div": np.nan, "pay_date": "20240206", "div_listdate": None},
    ]))
    assert cash.cash_per_share == 1 and cash.bonus_per_share == 0


def test_tushare_same_day_distinct_periods_sum_without_double_counting_duplicates() -> None:
    row = {"ts_code": "600000.SH", "end_date": "20231231", "ann_date": "20240501",
           "div_proc": "实施", "record_date": "20240601", "ex_date": "20240602",
           "pay_date": "20240606", "div_listdate": None, "cash_div_tax": .3, "stk_div": 0.}
    quarterly = {**row, "end_date": "20240331", "ann_date": "20240502", "cash_div_tax": .6}
    action, = normalize_tushare_dividends(pd.DataFrame([row, row, quarterly]))
    assert action.cash_per_share == pytest.approx(.9)
    assert action.announce_date.isoformat() == "2024-05-02"
    with pytest.raises(ValueError, match="conflicting.*terms"):
        normalize_tushare_dividends(pd.DataFrame([row, {**row, "cash_div_tax": .4}]))
    with pytest.raises(ValueError, match="incompatible.*settlement"):
        normalize_tushare_dividends(pd.DataFrame([row, {**quarterly, "pay_date": "20240607"}]))


def test_tushare_cash_only_snapshot_ignores_out_of_scope_incomplete_archives() -> None:
    old = {"ts_code": "600000.SH", "end_date": "19931231", "ann_date": "19940401",
           "div_proc": "实施", "record_date": None, "ex_date": None,
           "pay_date": None, "div_listdate": None, "cash_div_tax": 1., "stk_div": 0.}
    future = {**old, "ann_date": "20250101"}
    assert normalize_tushare_dividends(pd.DataFrame([old, future]), start_date="20230101", end_date="20241231") == []
    with pytest.raises(ValueError, match="requires a valid"):
        normalize_tushare_dividends(pd.DataFrame([future]), start_date="20230101", end_date="20251231")


def _reviewed_cdr_correction():
    before = _action(cash_per_share=10.)
    after = replace(before, cash_per_share=1., reference_cash_per_share=.98)
    text = b"Issuer notice: CNY 10 per underlying share; CNY 1 per CDR; diluted reference CNY 0.98."
    digest = hashlib.sha256(text).hexdigest()
    record = {"before": before.to_wire(), "after": after.to_wire(),
              "notice_date": "2024-01-30", "issuer": "fixture issuer",
              "source_unit": "underlying_share", "trading_unit": "CDR",
              "source_url": "https://example.com/issuer-notice", "evidence_sha256": digest,
              "evidence_excerpt": text.decode()}
    return before, after, record, {digest: text}


def test_reviewed_correction_only_changes_exact_event_and_requires_causal_frozen_evidence():
    from aipha.research.corporate_actions import apply_action_unit_corrections

    before, after, record, evidence = _reviewed_cdr_correction()
    other = _action(action_id="other", symbol="600001.SH")
    actual, audit = apply_action_unit_corrections([before, other], [record], evidence, as_of="2024-02-07")
    assert {a.action_id: a for a in actual} == {before.action_id: after, other.action_id: other}
    assert audit[0]["before"]["cash_per_share"] == 10.
    assert audit[0]["after"]["cash_per_share"] == 1.
    for change, expected in [
        ({"notice_date": "2024-02-03"}, "notice"),
        ({"evidence_sha256": "0" * 64}, "evidence"),
        ({"evidence_excerpt": "invented"}, "excerpt"),
        ({"after": replace(after, pay_date="2024-02-07").to_wire()}, "settlement"),
    ]:
        with pytest.raises(ValueError, match=expected):
            apply_action_unit_corrections([before], [{**record, **change}], evidence, as_of="2024-02-07")
    with pytest.raises(ValueError, match="match exactly"):
        apply_action_unit_corrections([after], [record], evidence, as_of="2024-02-07")
    with pytest.raises(ValueError, match="match exactly"):
        apply_action_unit_corrections([before], [record, record], evidence, as_of="2024-02-07")
    with pytest.raises(ValueError, match="notice"):
        apply_action_unit_corrections([before], [record], evidence, as_of="2024-01-29")


def test_differential_dividend_credits_actual_cash_and_uses_diluted_exchange_reference():
    action = _action(cash_per_share=1., reference_cash_per_share=.98)
    market = _with_action(action)
    after = market.symbol.eq(action.symbol) & market.trade_date.ge(pd.Timestamp(action.ex_date))
    market.loc[after, ["open_raw", "high_raw", "low_raw", "close_raw"]] = 9.02
    market.loc[after, "adj_factor"] = 10. / 9.02
    market["pre_close"] = 10.
    market.loc[after, "pre_close"] = 9.02
    result = _replay(market)
    assert result["accounting"]["cash_dividends_paid"] == 10000
    assert result["daily_returns"][-1]["nav"] == pytest.approx(100200)
    market.loc[after, "pre_close"] = 8.
    with pytest.raises(ValueError, match="exchange pre_close"):
        _replay(market)


def test_differential_bonus_preserves_actual_share_entitlement():
    action = _action(cash_per_share=1., reference_cash_per_share=.98,
                     bonus_per_share=.1, reference_bonus_per_share=.09, share_list_date="2024-02-07")
    market = _market()
    after = market.symbol.eq(action.symbol) & market.trade_date.ge(pd.Timestamp(action.ex_date))
    reference_price = (10. - .98) / 1.09
    market.loc[after, ["open", "high", "low", "close"]] = reference_price
    market.loc[after, "adj_factor"] = 10. / reference_price
    result = _replay(attach_corporate_actions(market, [action]))
    assert result["accounting"]["bonus_shares_delivered"] == 1000
    assert result["accounting"]["cash_dividends_paid"] == 10000
    assert result["daily_returns"][-1]["nav"] == pytest.approx(11000 * reference_price + 10000)


def test_declared_factor_quantization_has_bounded_error_without_masking_wrong_terms():
    action = _action(cash_per_share=1.23852, reference_cash_per_share=1.22317)
    market = _market()
    stock = market.symbol.eq(action.symbol)
    market.loc[stock, ["open", "close", "high", "low"]] = 42.60
    market.loc[stock, "adj_factor"] = 1.033
    after = stock & market.trade_date.ge(pd.Timestamp(action.ex_date))
    market.loc[after, ["open", "close", "high", "low"]] = 41.38
    market.loc[after, "adj_factor"] = 1.064
    market = attach_corporate_actions(market, [action])
    with pytest.raises(ValueError, match="does not reconcile"):
        _replay(market)
    market["adj_factor_rounding_unit"] = .001
    assert _replay(market)["accounting"]["cash_dividends_paid"] > 0
    wrong = attach_corporate_actions(market, [replace(action, reference_cash_per_share=.5)])
    with pytest.raises(ValueError, match="does not reconcile"):
        _replay(wrong)
    market["adj_factor_rounding_unit"] = .1
    with pytest.raises(ValueError, match="rounding_unit"):
        _replay(market)


def test_factor_precision_change_without_economic_event_does_not_invent_a_distribution():
    market = _market()
    market["adj_factor"] = 1.0332
    market["adj_factor_rounding_unit"] = 0.
    changed = market.trade_date.ge("2024-02-02")
    market.loc[changed, "adj_factor"] = 1.033
    market.loc[changed, "adj_factor_rounding_unit"] = .001
    result = _replay(market)
    assert result["accounting"]["cash_dividends_paid"] == 0
    assert result["daily_returns"][-1]["nav"] == 100000
    market.loc[changed, "adj_factor"] = 1.03
    with pytest.raises(ValueError, match="unexplained adjustment"):
        _replay(market)


def test_corrected_snapshot_preserves_parent_and_authenticates_event_audit(monkeypatch, tmp_path):
    from aipha.research.snapshots import (
        TushareSnapshot, attest_snapshot_metadata, correct_snapshot_action_units,
        verify_snapshot_signature, verify_attested_parquet,
    )
    monkeypatch.setenv("AIPHA_PIT_SNAPSHOT_KEY", "fixture-key-" * 4)
    before, after, record, evidence = _reviewed_cdr_correction()
    panel = attach_corporate_actions(_market(), [before])
    panel.loc[panel.index[0], "adj_factor"] = 1.0332
    metadata = attest_snapshot_metadata(panel, {"provider": "tushare_pit", "data_snapshot_id": "parent",
        "feature_version": "cn_equity_v1", "start_date": "2024-01-31", "end_date": "2024-02-07"})
    parent_hash = frame_content_sha256(panel)
    source = TushareSnapshot(panel, metadata)
    corrected = correct_snapshot_action_units(source, [record], evidence, factor_rounding_unit=.001)
    assert read_corporate_actions(corrected.panel) == [after]
    assert frame_content_sha256(source.panel) == parent_hash
    assert source.metadata["data_snapshot_id"] == "parent"
    assert corrected.metadata["snapshot_lineage"]["parent_snapshot_content_sha256"] == parent_hash
    assert corrected.metadata["data_snapshot_id"] != "parent"
    assert corrected.panel.adj_factor_rounding_unit.iloc[0] == .0001
    assert corrected.panel.adj_factor_rounding_unit.iloc[-1] == .001
    path = tmp_path / "corrected.parquet"
    corrected.write(path)
    verify_attested_parquet(path, require_source_signature=True, require_artifact_hash=True)
    tampered = {**corrected.metadata, "corporate_action_unit_corrections": []}
    with pytest.raises(ValueError, match="signature"):
        verify_snapshot_signature(tampered)


@pytest.mark.parametrize("rows", [0, 1, 2051, 4100])
def test_streamed_snapshot_hash_matches_legacy_split_json_bytes(rows):
    frame = pd.DataFrame({
        "trade_date": pd.date_range("2023-01-01", periods=rows),
        "symbol": [f"stock-{i % 3}" for i in range(rows)],
        "unicode": ['公告,"index":[]\n' for _ in range(rows)],
        "value": [np.nan if i % 5 == 0 else -1.23456789012345e-8 * i for i in range(rows)],
        "flag": [i % 2 == 0 for i in range(rows)],
    }).iloc[::-1]
    legacy = frame.copy()
    legacy.trade_date = legacy.trade_date.dt.strftime("%Y-%m-%dT%H:%M:%S.%f")
    legacy = legacy.reindex(sorted(legacy.columns), axis=1).sort_values(["trade_date", "symbol"], kind="mergesort").reset_index(drop=True)
    payload = legacy.to_json(orient="split", date_format="iso", double_precision=15,
                             force_ascii=False, default_handler=str).encode("utf-8")
    assert frame_content_sha256(frame) == hashlib.sha256(payload).hexdigest()
