"""Calendar, information-time and cash invariants for the portfolio replay."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aipha.research.backtest import run_portfolio_backtest
from aipha.research.contracts import TargetPosition, price_limit_flags
from aipha.research.pipeline import ResearchConfig, build_forward_labels


def _market() -> pd.DataFrame:
    return pd.DataFrame([
        {"trade_date": day, "symbol": symbol,
         "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0,
         "volume": 10000.0, "amount": 100000.0, "adj_factor": 1.0,
         "is_suspended": False, "is_limit_up": False, "is_limit_down": False,
         "up_limit": 11.0, "down_limit": 9.0}
        for day in pd.bdate_range("2024-01-31", periods=6)
        for symbol in ("600000.SH", "600001.SH")
    ])


def _targets(*, exit_day=None) -> list[TargetPosition]:
    targets = [TargetPosition(pd.Timestamp("2024-01-31").date(), "600000.SH", 1.0, "entry")]
    if exit_day is not None:
        targets.append(TargetPosition(pd.Timestamp(exit_day).date(), "600001.SH", 1.0, "rotate"))
    return targets


def _config(**overrides) -> ResearchConfig:
    return ResearchConfig(**{
        "initial_capital": 100000, "max_position_weight": 1.0,
        "max_daily_turnover": 2.0, "commission_min": 0.0,
        "commission_rate": 0.0, "transfer_fee_rate": 0.0,
        "stamp_tax_rate": 0.0, "slippage_bps": 0.0,
        **overrides,
    })


@pytest.mark.parametrize("missing_day", ["2024-02-01", "2024-02-02"])
def test_missing_symbol_bar_invalidates_fixed_calendar_label(missing_day) -> None:
    market = _market()
    market = market[~((market.symbol == "600000.SH") & (market.trade_date == missing_day))]
    # A non-contiguous external index must not change lookup alignment.
    labels = build_forward_labels(market, horizon_days=1, skip_days=1)
    first = labels[labels.trade_date == pd.Timestamp("2024-01-31").date()]
    assert first.symbol.tolist() == ["600001.SH"]
    assert first.iloc[0].fwd_return == pytest.approx(0.0)


@pytest.mark.parametrize("day,column,value", [
    ("2024-02-01", "is_suspended", True),
    ("2024-02-02", "is_suspended", True),
    ("2024-02-01", "open", 11.0),
    ("2024-02-02", "close", 9.0),
    ("2024-02-01", "open", 0.0),
    ("2024-02-02", "close", np.inf),
])
def test_untradeable_entry_or_exit_does_not_create_label(day, column, value) -> None:
    market = _market()
    market.loc[(market.symbol == "600000.SH") & (market.trade_date == day), column] = value
    labels = build_forward_labels(market, horizon_days=1)
    first = labels[labels.trade_date == pd.Timestamp("2024-01-31").date()]
    assert first.symbol.tolist() == ["600001.SH"]


def test_label_directionality_and_duplicate_keys() -> None:
    market = _market()
    market.loc[market.trade_date == "2024-02-01", "open"] = 9.0  # Buying at lower band is allowed.
    market.loc[market.trade_date == "2024-02-02", "close"] = 11.0  # Selling at upper band is allowed.
    first = build_forward_labels(market, horizon_days=1).iloc[0]
    assert first.fwd_return == pytest.approx(11 / 9 - 1)
    with pytest.raises(ValueError, match="duplicate"):
        build_forward_labels(pd.concat([market, market.iloc[:1]]))


def test_walk_forward_purge_covers_delayed_entry_and_holding_horizon() -> None:
    from aipha.models.ml_stock_selector import MLSelectorConfig, MLStockSelector

    config = MLSelectorConfig(
        train_years=1, predict_months=1, forward_days=20,
        label_skip_days=40, purge_days=20,
    )
    dates = list(pd.bdate_range("2022-01-03", periods=400))
    splits = MLStockSelector(config)._generate_wf_splits(dates)
    assert splits
    for train, test in splits:
        last_label_exit = dates.index(train[-1]) + config.label_skip_days + config.forward_days
        assert last_label_exit < dates.index(test[0])
        assert not set(train) & set(test)


@pytest.mark.parametrize("open_price,close_price,expected_trades", [
    (10.0, 11.0, 1),  # Closes locked, but the earlier open was executable.
    (11.0, 10.0, 0),  # Unlocks later: no fill can be assumed at the locked open.
    (10.99, 11.0, 1),  # One tick inside the band is not locked.
])
@pytest.mark.parametrize("adjusted", [False, True])
def test_open_buy_decision_uses_raw_open_not_future_close(open_price, close_price, expected_trades, adjusted) -> None:
    market = _market()
    execution = (market.symbol == "600000.SH") & (market.trade_date == "2024-02-01")
    market.loc[execution, ["open", "close"]] = [open_price, close_price]
    market.loc[execution, "is_limit_up"] = close_price == 11.0
    if adjusted:
        for column in ("open", "high", "low", "close"):
            market[f"{column}_raw"] = market[column]
            market[column] *= 2.0
        market["adj_factor"] = 2.0
        market["price_adjustment"] = "point_in_time_factor"
    result = run_portfolio_backtest(market, pd.DataFrame(), _targets(), _config())
    assert len(result["trades"]) == expected_trades
    if not expected_trades:
        assert result["metrics"]["unexecutable_reasons"]["limit_up_buy_block"] == 1


@pytest.mark.parametrize("open_price,close_price,expected_sells", [(10.0, 9.0, 1), (9.0, 10.0, 0)])
def test_open_sell_does_not_use_later_close(open_price, close_price, expected_sells) -> None:
    market = _market()
    execution = (market.symbol == "600000.SH") & (market.trade_date == "2024-02-05")
    market.loc[execution, ["open", "close"]] = [open_price, close_price]
    market.loc[execution, "is_limit_down"] = close_price == 9.0
    result = run_portfolio_backtest(market, pd.DataFrame(), _targets(exit_day="2024-02-02"), _config())
    sells = [trade for trade in result["trades"] if trade["side"] == "sell"]
    assert len(sells) == expected_sells


@pytest.mark.parametrize("cash,expected_qty", [(1000.01, 0), (1005.01, 100), (2000.02, 100)])
def test_buy_cash_constraint_includes_minimum_commission(cash, expected_qty) -> None:
    result = run_portfolio_backtest(
        _market(), pd.DataFrame(), _targets(),
        _config(initial_capital=cash, commission_min=5.0, commission_rate=0.0001, transfer_fee_rate=0.00001),
    )
    assert sum(trade["qty"] for trade in result["trades"]) == expected_qty
    assert all(row["cash"] >= 0 for row in result["daily_returns"])
    if expected_qty:
        assert result["daily_returns"][1]["cash"] == pytest.approx(cash - expected_qty * 10 * 1.00001 - 5)


def test_limit_flags_recover_raw_price_and_fail_closed_on_invalid_inputs() -> None:
    frame = pd.DataFrame({
        "open": [22.0], "adj_factor": [2.0], "price_adjustment": ["point_in_time_factor"],
        "up_limit": [11.0], "down_limit": [9.0],
    })
    up, down = price_limit_flags(frame)
    assert up.iloc[0] and not down.iloc[0]
    with pytest.raises(ValueError, match="both"):
        price_limit_flags(frame.drop(columns="down_limit"))
    frame["adj_factor"] = 0.0
    with pytest.raises(ValueError, match="positive finite"):
        price_limit_flags(frame)
    with pytest.raises(ValueError, match="boolean"):
        price_limit_flags(pd.DataFrame({"is_limit_up": ["false"]}))
