"""tests/models/test_wf_stats.py — wf_stats 单元测试"""
from __future__ import annotations

import math
import numpy as np
import pandas as pd
import pytest

from aipha.models.wf_stats import (
    compute_dsr,
    compute_hlz_adjusted_t,
    compute_wf_statistical_summary,
)


# ---------------------------------------------------------------------------
# compute_dsr
# ---------------------------------------------------------------------------

def _ic_series(n: int = 100, mean: float = 0.05, std: float = 0.1,
               seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(mean, std, n))


def test_dsr_returns_dict():
    result = compute_dsr(_ic_series())
    assert set(result.keys()) >= {"dsr", "raw_sr", "skewness", "kurtosis", "n_obs"}


def test_dsr_too_few_obs():
    result = compute_dsr(pd.Series([0.01, 0.02, float("nan")]))
    assert math.isnan(result["dsr"])


def test_dsr_zero_std():
    result = compute_dsr(pd.Series([0.05] * 50))
    assert result["raw_sr"] == pytest.approx(0.0)


def test_dsr_positive_for_high_ic():
    """高 IC IR 序列 DSR 应为正"""
    ic = _ic_series(n=200, mean=0.10, std=0.03)
    result = compute_dsr(ic, n_trials=1)
    assert result["dsr"] > 0


def test_dsr_penalized_for_many_trials():
    """多重检验时 DSR 应低于单次"""
    ic = _ic_series(n=100, mean=0.05, std=0.08)
    dsr_1 = compute_dsr(ic, n_trials=1)["dsr"]
    dsr_50 = compute_dsr(ic, n_trials=50)["dsr"]
    assert dsr_50 < dsr_1


# ---------------------------------------------------------------------------
# compute_hlz_adjusted_t
# ---------------------------------------------------------------------------

def test_hlz_nan_for_few_obs():
    result = compute_hlz_adjusted_t(pd.Series([0.01]), n_trials=1)
    assert math.isnan(result)


def test_hlz_decreases_with_n_trials():
    ic = _ic_series(n=100, mean=0.05, std=0.05)
    t1 = compute_hlz_adjusted_t(ic, n_trials=1)
    t10 = compute_hlz_adjusted_t(ic, n_trials=10)
    assert t10 < t1


def test_hlz_type_is_float():
    ic = _ic_series()
    result = compute_hlz_adjusted_t(ic, n_trials=5)
    assert isinstance(result, float)


# ---------------------------------------------------------------------------
# compute_wf_statistical_summary
# ---------------------------------------------------------------------------

def _make_oos_df(n_dates: int = 50, n_stocks: int = 20,
                 seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-02", periods=n_dates, freq="B").strftime("%Y-%m-%d")
    records = []
    for d in dates:
        for sym in [f"S{i:03d}" for i in range(n_stocks)]:
            records.append({
                "datetime": d,
                "symbol": sym,
                "pred_score": rng.normal(0, 1),
                "fwd_return": rng.normal(0, 0.01),
            })
    return pd.DataFrame(records)


def test_summary_returns_expected_keys():
    oos_df = _make_oos_df()
    result = compute_wf_statistical_summary(oos_df, {"n_folds": 1, "folds": []},
                                            n_trials=1)
    assert set(result.keys()) >= {"dsr", "hlz_adjusted_t", "pbo", "n_obs", "n_trials"}


def test_summary_missing_cols():
    empty_df = pd.DataFrame({"datetime": [], "symbol": []})
    result = compute_wf_statistical_summary(empty_df, {}, n_trials=1)
    assert math.isnan(result["dsr"])


def test_summary_n_trials_propagated():
    oos_df = _make_oos_df()
    result = compute_wf_statistical_summary(oos_df, {"n_folds": 3, "folds": []},
                                            n_trials=5)
    assert result["n_trials"] == 5
