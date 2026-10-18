from __future__ import annotations

import json
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
    result = _replay(_with_action())
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
