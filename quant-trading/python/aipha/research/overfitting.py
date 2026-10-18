"""CSCV on fixed candidate portfolios' realized, non-overlapping returns."""
from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

import numpy as np
import pandas as pd

from .backtest import run_portfolio_backtest


def portfolio_selection_cscv(
    market: pd.DataFrame,
    predictions: pd.DataFrame,
    config: Any,
    *,
    baseline_returns: list[dict[str, Any]] | None = None,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Replay the predeclared Top-K grid, then cross-validate daily net returns.

    This measures portfolio-size selection conditional on a frozen OOS score
    panel. It makes no claim about an unrecorded model/factor search. Forward
    labels never enter either candidate construction or the return matrix.
    """
    from .pipeline import _top_k_candidate_grid, build_target_positions
    from aipha.reporting.pbo import PBOAnalyzer, PBOConfig

    grid = _top_k_candidate_grid(config.top_k)
    candidate_ids = [f"top_k_{k}" for k in grid]
    empty = pd.DataFrame(columns=candidate_ids, index=pd.DatetimeIndex([], name="trade_date"), dtype=float)
    analysis: dict[str, Any] = {
        "available": False, "pbo": None,
        "method": "cscv_net_top_k_portfolios",
        "scope": "portfolio_size_selection_on_frozen_oos_scores",
        "return_frequency": "daily", "return_basis": "net_portfolio_replay",
        "overlapping_return_intervals": False,
        "candidate_grid_fixed_before_returns": True,
        "candidates": [{"id": name, "top_k": k} for name, k in zip(candidate_ids, grid)],
        "model_config_hash": config.model_config_hash(),
        "n_observations": 0,
    }
    if predictions.empty:
        analysis["reason"] = "insufficient_oos"
        return empty, analysis
    scores = predictions.copy()
    scores["trade_date"] = pd.to_datetime(scores["trade_date"]).dt.date
    if scores.duplicated(["trade_date", "symbol"]).any():
        raise ValueError("CSCV predictions contain duplicate trade_date/symbol rows")
    if not np.isfinite(pd.to_numeric(scores["score"], errors="coerce")).all():
        raise ValueError("CSCV predictions must contain finite scores")
    if "industry" not in scores and "industry" in market:
        classifications = market[["trade_date", "symbol", "industry"]].copy()
        classifications["trade_date"] = pd.to_datetime(classifications["trade_date"]).dt.date
        scores = scores.merge(classifications, on=["trade_date", "symbol"], how="left", validate="one_to_one")
    eligible = scores[np.isfinite(pd.to_numeric(scores["score"], errors="coerce"))]
    dates = pd.to_datetime(eligible["trade_date"])
    signal_dates = eligible.assign(_month=dates.dt.to_period("M")).groupby("_month")["trade_date"].max()
    counts = eligible.loc[eligible.trade_date.isin(signal_dates)].groupby("trade_date").symbol.nunique()
    analysis["minimum_rebalance_cross_section"] = int(counts.min()) if len(counts) else 0
    if not len(counts) or counts.min() < max(grid):
        analysis["reason"] = "insufficient_candidate_universe"
        return empty, analysis

    candidate_returns: dict[str, pd.Series] = {}
    for candidate, name in zip(grid, candidate_ids):
        if candidate == config.top_k and baseline_returns is not None:
            daily = baseline_returns
        else:
            candidate_config = replace(config, top_k=candidate)
            targets = build_target_positions(
                scores, top_k=candidate,
                max_position_weight=config.max_position_weight,
                max_industry_weight=config.max_industry_weight,
            )
            daily = run_portfolio_backtest(market, scores, targets, candidate_config)["daily_returns"]
        frame = pd.DataFrame(daily)
        frame["trade_date"] = pd.to_datetime(frame["trade_date"])
        # Exclude the initial NAV anchor. Every retained row is one actual
        # close-to-close account return, including fees and unfilled trades.
        candidate_returns[name] = frame.set_index("trade_date")["daily_return"].iloc[1:]
    matrix = pd.DataFrame(candidate_returns).sort_index()
    matrix.index.name = "trade_date"
    if not np.isfinite(matrix.to_numpy(dtype=float)).all():
        raise ValueError("CSCV candidates must have identical finite return observation dates")
    analysis["n_observations"] = len(matrix)
    if len(matrix) < 8:
        analysis["reason"] = "insufficient_oos"
        return matrix, analysis
    if np.array_equal(matrix.to_numpy(), np.repeat(matrix.iloc[:, :1].to_numpy(), len(grid), axis=1)):
        analysis["reason"] = "identical_candidate_returns"
        return matrix, analysis
    partitions = min(16, len(matrix) // 2)
    partitions -= partitions % 2
    cscv_config = PBOConfig(num_subsamples=partitions, annualization_periods=252, risk_free_rate=0.0)
    result = PBOAnalyzer(cscv_config).compute(matrix.to_numpy(dtype=float))
    analysis.update({
        "available": True, "pbo": result.pbo, "is_overfit": result.is_overfit,
        "config": asdict(cscv_config), "result": asdict(result),
        "num_combinations_tested": result.num_combinations_tested,
        "num_combinations_total": result.num_combinations_total,
        "exhaustive": result.num_combinations_tested == result.num_combinations_total,
    })
    return matrix, analysis
