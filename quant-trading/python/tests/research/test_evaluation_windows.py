from __future__ import annotations

import numpy as np
import json
import pandas as pd
import pytest

from aipha.research.backtest import run_portfolio_backtest
from aipha.research.contracts import TargetPosition
from aipha.research.evaluation import benchmark_diagnostics, feature_missing_rates, json_safe_metrics
from aipha.research.pipeline import ResearchConfig


def _tape():
    days = pd.bdate_range("2024-01-01", periods=30)
    prices = np.r_[np.full(26, 10.0), [11.0, 10.5, 12.0, 11.5]]
    market = pd.DataFrame({
        "trade_date": days, "symbol": "600000.SH", "open": prices,
        "high": prices, "low": prices, "close": prices, "volume": 10000, "amount": prices * 10000,
        "benchmark_close": prices * 10,
    })
    predictions = pd.DataFrame({"trade_date": days[24:28], "symbol": "600000.SH", "score": 1.0})
    targets = [TargetPosition(days[24].date(), "600000.SH", 1.0, "entry")]
    config = ResearchConfig(
        max_position_weight=1.0, max_daily_turnover=2.0, slippage_bps=0,
        commission_rate=0, commission_min=0, transfer_fee_rate=0, stamp_tax_rate=0,
    )
    return market, predictions, targets, config


def test_warmup_history_cannot_dilute_oos_annualization() -> None:
    market, predictions, targets, config = _tape()
    replay = run_portfolio_backtest(market, predictions, targets, config)
    trimmed = run_portfolio_backtest(market.iloc[24:], predictions, targets, config)
    assert replay["metrics"] == trimmed["metrics"]
    assert replay["daily_returns"] == trimmed["daily_returns"]
    assert len(replay["daily_returns"]) == 6
    assert replay["evaluation_window"]["return_observations"] == 5
    assert replay["evaluation_window"]["valuation_tail_sessions"] == 2
    assert replay["metrics"]["portfolio_annualized_return"] == pytest.approx(1.15 ** (252 / 5) - 1)
    expected = np.array([0.0, 0.1, 10.5 / 11 - 1, 12 / 10.5 - 1, 11.5 / 12 - 1])
    assert replay["metrics"]["portfolio_annualized_volatility"] == pytest.approx(expected.std(ddof=1) * np.sqrt(252))


def test_oos_cash_waiting_for_rebalance_is_a_real_return_interval() -> None:
    market, predictions, targets, config = _tape()
    targets = [TargetPosition(pd.Timestamp(market.trade_date.iloc[26]).date(), "600000.SH", 1.0, "entry")]
    replay = run_portfolio_backtest(market, predictions, targets, config)
    assert replay["daily_returns"][0]["trade_date"] == str(predictions.trade_date.min().date())
    assert replay["daily_returns"][1]["daily_return"] == 0.0
    assert replay["daily_returns"][2]["daily_return"] == 0.0
    assert replay["evaluation_window"]["return_observations"] == 5


def test_target_before_oos_or_prediction_outside_tape_is_rejected() -> None:
    market, predictions, targets, config = _tape()
    targets = [TargetPosition(market.trade_date.iloc[20].date(), "600000.SH", 1.0, "invalid")]
    with pytest.raises(ValueError, match="target signal dates"):
        run_portfolio_backtest(market, predictions, targets, config)
    predictions.loc[0, "trade_date"] = pd.Timestamp("2030-01-01")
    with pytest.raises(ValueError, match="market calendar"):
        run_portfolio_backtest(market, predictions, [], config)


def test_strategy_and_benchmark_use_identical_return_intervals() -> None:
    market, predictions, targets, config = _tape()
    replay = run_portfolio_backtest(market, predictions, targets, config)
    diagnostics = benchmark_diagnostics(market, predictions, replay["daily_returns"])
    assert diagnostics["available"]
    assert diagnostics["expected_return_observations"] == 5
    assert diagnostics["total_return"] == pytest.approx(0.15)
    assert diagnostics["relative"]["excess_return"] == pytest.approx(0.0)
    assert diagnostics["annualized_return"] == pytest.approx(replay["metrics"]["portfolio_annualized_return"])
    # Training-period prices can change freely without changing benchmark
    # returns inside the replay's fixed close-to-close observation window.
    market.loc[:23, "benchmark_close"] = np.arange(24) + 1.0
    assert benchmark_diagnostics(market, predictions, replay["daily_returns"]) == diagnostics


@pytest.mark.parametrize("column", ["benchmark_close", "benchmark_return"])
def test_missing_benchmark_dates_do_not_become_compressed_returns(column) -> None:
    market, predictions, targets, config = _tape()
    replay = run_portfolio_backtest(market, predictions, targets, config)
    if column == "benchmark_return":
        market[column] = market.benchmark_close.pct_change(fill_method=None)
        market = market.drop(columns="benchmark_close")
    market.loc[26, column] = np.nan
    diagnostic = benchmark_diagnostics(market, predictions, replay["daily_returns"])
    assert not diagnostic["available"]
    assert diagnostic["reason"] == "incomplete_benchmark_coverage"
    assert diagnostic["missing_return_observations"] == (2 if column == "benchmark_close" else 1)
    assert not diagnostic["relative"]["available"]


def test_prediction_only_benchmark_excludes_training_history() -> None:
    market, predictions, _, _ = _tape()
    diagnostic = benchmark_diagnostics(market, predictions)
    assert diagnostic["available"]
    assert diagnostic["scope"] == "prediction_oos"
    assert diagnostic["expected_return_observations"] == 3
    assert diagnostic["total_return"] == pytest.approx(0.05)


def test_conflicting_benchmark_values_are_not_resolved_by_row_order() -> None:
    market, predictions, targets, config = _tape()
    replay = run_portfolio_backtest(market, predictions, targets, config)
    conflict = market.iloc[25:26].copy().assign(symbol="600001.SH", benchmark_close=999.0)
    with pytest.raises(ValueError, match="disagree"):
        benchmark_diagnostics(pd.concat([market, conflict]), predictions, replay["daily_returns"])


def test_feature_missing_rate_uses_only_model_inputs_and_counts_infinity() -> None:
    frame = pd.DataFrame({
        "momentum": [1.0, np.nan], "value": [np.inf, 2.0],
        "open_raw": [np.nan, np.nan], "up_limit": [np.nan, np.nan],
        "listing_days": [1000, 1000],
    })
    actual = feature_missing_rates(frame, ["momentum", "value"])
    assert actual["mean"] == 0.5
    assert actual["by_feature"] == {"momentum": 0.5, "value": 0.5}
    assert actual["rows"] == 2
    with pytest.raises(ValueError, match="absent"):
        feature_missing_rates(frame, ["unknown_factor"])


def test_initial_benchmark_loss_is_included_in_max_drawdown() -> None:
    market, predictions, targets, config = _tape()
    market.loc[25:, "benchmark_close"] = 90.0
    replay = run_portfolio_backtest(market, predictions, targets, config)
    diagnostic = benchmark_diagnostics(market, predictions, replay["daily_returns"])
    assert diagnostic["max_drawdown"] == pytest.approx(-0.1)


def test_nullable_feature_values_count_as_missing_observations() -> None:
    frame = pd.DataFrame({
        "momentum": pd.Series([1.0, pd.NA], dtype="Float64"),
        "value": pd.Series([pd.NA, 2], dtype="Int64"),
    })
    assert feature_missing_rates(frame, ["momentum", "value"])["by_feature"] == {"momentum": 0.5, "value": 0.5}


def test_undefined_walk_forward_ic_remains_null_in_strict_json() -> None:
    from aipha.models.ml_stock_selector import MLSelectorConfig, MLStockSelector

    selector = MLStockSelector(MLSelectorConfig())
    selector._wf_results = [{"fold": 0, "mean_ic": float("nan"), "is_mean_ic": float("nan")}]
    summary = json_safe_metrics(selector.get_wf_summary())
    assert summary["valid_ic_folds"] == 0
    assert summary["mean_ic"] is None and summary["ic_ir"] is None
    assert summary["folds"][0]["mean_ic"] is None
    assert json.loads(json.dumps(summary, allow_nan=False)) == summary
