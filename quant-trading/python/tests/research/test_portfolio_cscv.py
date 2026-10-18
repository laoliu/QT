from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from aipha.research.backtest import run_portfolio_backtest
from aipha.research.overfitting import portfolio_selection_cscv
from aipha.research.pipeline import ResearchConfig, _evaluate_predictions, build_target_positions
from aipha.research.style import validate_target_styles


def _inputs(n_days=45, n_symbols=10):
    rows, scores = [], []
    for i, day in enumerate(pd.bdate_range("2024-01-02", periods=n_days)):
        for j in range(n_symbols):
            price = 10.0 + j * 0.1 + 0.2 * np.sin(i * 0.3 + j)
            symbol = f"60000{j}.SH"
            rows.append({
                "trade_date": day, "symbol": symbol,
                "open": price, "high": price, "low": price, "close": price,
                "volume": 10000, "amount": price * 10000,
                "is_suspended": False, "is_limit_up": False, "is_limit_down": False,
            })
            scores.append({"trade_date": day, "symbol": symbol, "score": float(j)})
    config = ResearchConfig(
        top_k=2, max_position_weight=0.5, max_industry_weight=None,
        max_daily_turnover=1.0, slippage_bps=10, commission_rate=0.001, commission_min=5,
    )
    return pd.DataFrame(rows), pd.DataFrame(scores), config


@pytest.fixture(scope="module")
def selection():
    market, predictions, config = _inputs()
    matrix, analysis = portfolio_selection_cscv(market, predictions, config)
    return market, predictions, config, matrix, analysis


def test_cscv_uses_actual_daily_net_account_returns(selection) -> None:
    market, predictions, config, matrix, analysis = selection
    assert analysis["available"]
    assert analysis["exhaustive"]
    assert analysis["return_basis"] == "net_portfolio_replay"
    assert not analysis["overlapping_return_intervals"]
    assert matrix.index.is_unique and matrix.index.is_monotonic_increasing
    assert analysis["result"]["num_observations"] == len(matrix) == 44
    assert sum(analysis["result"]["subsample_sizes"]) == 44
    assert analysis["config"]["annualization_periods"] == 252
    assert analysis["config"]["risk_free_rate"] == 0
    targets = build_target_positions(predictions, top_k=config.top_k, max_position_weight=config.max_position_weight)
    replay = run_portfolio_backtest(market, predictions, targets, config)
    account = pd.DataFrame(replay["daily_returns"]).set_index("trade_date")
    np.testing.assert_allclose(matrix["top_k_2"], account.daily_return.iloc[1:])
    assert replay["metrics"]["portfolio_total_costs"] > 0
    free = replace(config, commission_min=0, commission_rate=0, transfer_fee_rate=0, stamp_tax_rate=0, slippage_bps=0)
    free_replay = run_portfolio_backtest(market, predictions, targets, free)
    assert not np.allclose(matrix["top_k_2"], [row["daily_return"] for row in free_replay["daily_returns"]][1:])


def test_labels_cannot_change_the_candidate_grid_or_realized_matrix(selection) -> None:
    market, predictions, config, matrix, analysis = selection
    altered = predictions.assign(fwd_return=np.arange(len(predictions)) * 1000.0)
    other_matrix, other = portfolio_selection_cscv(market, altered, config)
    pd.testing.assert_frame_equal(matrix, other_matrix)
    assert other["pbo"] == analysis["pbo"]
    assert other["candidates"] == analysis["candidates"]


def test_cscv_candidates_share_the_style_budgets_and_reference_universe():
    market, predictions, config = _inputs()
    market = market.assign(log_market_cap=np.tile(np.arange(10, dtype=float), 45),
                           is_index_member=True, is_st=False, listing_days=400,
                           listing_days_basis="exchange_open_sessions")
    config = replace(config, max_style_exposure={"log_market_cap": .1})
    matrix, analysis = portfolio_selection_cscv(market, predictions, config)
    assert not matrix.empty
    for candidate in analysis["candidates"]:
        policy = replace(config, top_k=candidate["top_k"])
        targets = build_target_positions(predictions, top_k=policy.top_k, max_position_weight=policy.max_position_weight,
                                         max_style_exposure=policy.max_style_exposure, style_reference=market)
        diagnostics = validate_target_styles(targets, market, policy.max_style_exposure)
        assert all(abs(row["weighted_zscores"]["log_market_cap"]) <= .1 + 1e-8 for row in diagnostics)
        replay = run_portfolio_backtest(market, predictions, targets, policy)
        assert matrix[candidate["id"]].to_numpy() == pytest.approx([row["daily_return"] for row in replay["daily_returns"]][1:])


def test_thin_universe_does_not_silently_drop_large_candidates() -> None:
    market, predictions, config = _inputs(n_symbols=3)
    matrix, analysis = portfolio_selection_cscv(market, predictions, config)
    assert matrix.empty
    assert not analysis["available"] and analysis["pbo"] is None
    assert analysis["reason"] == "insufficient_candidate_universe"
    assert [c["top_k"] for c in analysis["candidates"]] == [2, 4, 5, 8]


def test_too_short_replay_or_indistinguishable_candidates_do_not_report_pbo() -> None:
    market, predictions, config = _inputs(n_days=7)
    _, short = portfolio_selection_cscv(market, predictions, config)
    assert short["reason"] == "insufficient_oos" and short["pbo"] is None
    market, predictions, config = _inputs()
    for column in ("open", "high", "low", "close"):
        market[column] = 10.0
    free = replace(config, commission_min=0, commission_rate=0, transfer_fee_rate=0, stamp_tax_rate=0, slippage_bps=0)
    _, flat = portfolio_selection_cscv(market, predictions, free)
    assert flat["reason"] == "identical_candidate_returns" and flat["pbo"] is None


def test_predictions_only_evaluation_cannot_claim_formal_pbo() -> None:
    _, predictions, config = _inputs(n_symbols=3)
    predictions["fwd_return"] = predictions.score / 100
    metrics = _evaluate_predictions(predictions, [], config)
    assert metrics["pbo"] is None
    assert metrics["pbo_method"] == "unavailable_requires_portfolio_replay"
