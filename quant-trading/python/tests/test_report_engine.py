"""Tests for report_engine — risk analysis and performance metrics (no network required)."""

from __future__ import annotations

import math
import os
import sys

# Ensure engines package is importable
_this_dir = os.path.dirname(os.path.abspath(__file__))
_python_root = os.path.dirname(_this_dir)
if _python_root not in sys.path:
    sys.path.insert(0, _python_root)

from engines.report_engine import (
    _calc_returns,
    _annualized_return,
    _max_drawdown,
    _mean,
    _std_dev,
    _sharpe_ratio,
    _sortino_ratio,
    _value_at_risk,
    _conditional_var,
    _correlation,
    _alpha_beta,
    _tracking_error,
    _information_ratio,
    _trade_statistics,
    _monthly_returns,
    analyze_risk,
)


# ── Helper ───────────────────────────────────────────────────────


def _make_equity_curve(values: list[float], start_date: str = "2024-01-02") -> list[dict]:
    """Build equity curve dicts from a value list."""
    from datetime import datetime, timedelta

    base = datetime.strptime(start_date, "%Y-%m-%d")
    return [
        {"date": (base + timedelta(days=i)).strftime("%Y-%m-%d"), "value": v}
        for i, v in enumerate(values)
    ]


# ── _calc_returns ────────────────────────────────────────────────


class TestCalcReturns:
    def test_basic_returns(self):
        values = [100, 110, 99]
        ret = _calc_returns(values)
        assert len(ret) == 2
        assert abs(ret[0] - 0.1) < 1e-9
        assert abs(ret[1] - (99 / 110 - 1)) < 1e-9

    def test_empty(self):
        assert _calc_returns([]) == []

    def test_single_value(self):
        assert _calc_returns([100]) == []

    def test_zero_value_in_series(self):
        ret = _calc_returns([0, 100])
        assert ret == [0.0]  # division by zero -> 0.0


# ── _annualized_return ───────────────────────────────────────────


class TestAnnualizedReturn:
    def test_one_year_doubling(self):
        assert abs(_annualized_return(1.0, 1.0) - 1.0) < 1e-9

    def test_two_years(self):
        total = 0.44
        ann = _annualized_return(total, 2.0)
        # (1 + 0.44)^(1/2) - 1
        expected = (1.44) ** 0.5 - 1
        assert abs(ann - expected) < 1e-9

    def test_zero_years(self):
        assert _annualized_return(0.5, 0) == 0.0

    def test_total_loss(self):
        assert _annualized_return(-1.0, 1.0) == -1.0


# ── _mean & _std_dev ─────────────────────────────────────────────


class TestMeanStdDev:
    def test_mean_basic(self):
        assert abs(_mean([1, 2, 3, 4]) - 2.5) < 1e-9

    def test_mean_empty(self):
        assert _mean([]) == 0.0

    def test_std_dev_empty(self):
        assert _std_dev([]) == 0.0

    def test_std_dev_single(self):
        assert _std_dev([5.0]) == 0.0

    def test_std_dev_uniform(self):
        assert _std_dev([3.0, 3.0, 3.0]) == 0.0

    def test_std_dev_basic(self):
        data = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        s = _std_dev(data)
        assert s > 0


# ── _max_drawdown ────────────────────────────────────────────────


class TestMaxDrawdown:
    def test_no_drawdown(self):
        result = _max_drawdown([100, 110, 120, 130])
        assert result["maxDrawdown"] == 0.0

    def test_simple_drawdown(self):
        result = _max_drawdown([100, 80, 90])
        assert abs(result["maxDrawdown"] - 0.2) < 1e-9

    def test_empty(self):
        result = _max_drawdown([])
        assert result["maxDrawdown"] == 0

    def test_single_value(self):
        result = _max_drawdown([100])
        assert result["maxDrawdown"] == 0.0

    def test_multiple_drawdowns(self):
        # First drawdown: 100 -> 90 (10%), second: 110 -> 80 (27.27%)
        values = [100, 90, 110, 80, 100]
        result = _max_drawdown(values)
        expected = (110 - 80) / 110
        assert abs(result["maxDrawdown"] - expected) < 1e-9


# ── _sharpe_ratio ────────────────────────────────────────────────


class TestSharpeRatio:
    def test_insufficient_data(self):
        assert _sharpe_ratio([]) == 0.0
        assert _sharpe_ratio([0.01]) == 0.0

    def test_zero_volatility(self):
        # Constant returns
        assert _sharpe_ratio([0.01, 0.01, 0.01]) == 0.0

    def test_positive_sharpe(self):
        # Varying positive returns with some noise
        import random
        random.seed(42)
        returns = [0.01 + random.gauss(0, 0.005) for _ in range(252)]
        sharpe = _sharpe_ratio(returns, 0.0)
        assert sharpe > 0


# ── _sortino_ratio ───────────────────────────────────────────────


class TestSortinoRatio:
    def test_insufficient_data(self):
        assert _sortino_ratio([]) == 0.0

    def test_no_downside(self):
        # All positive returns: downside deviation is 0 -> return 0
        returns = [0.02, 0.03, 0.01]
        sortino = _sortino_ratio(returns, 0.0)
        # downside of excess returns (all positive) should be zero
        # so sortino should be 0
        assert sortino == 0.0

    def test_with_downside(self):
        returns = [0.02, -0.03, 0.01, -0.02, 0.005]
        sortino = _sortino_ratio(returns, 0.0)
        # Should be some finite number
        assert isinstance(sortino, float)


# ── VaR and CVaR ─────────────────────────────────────────────────


class TestVarCvar:
    def test_var_empty(self):
        assert _value_at_risk([]) == 0.0

    def test_cvar_empty(self):
        assert _conditional_var([]) == 0.0

    def test_var_basic(self):
        returns = sorted([-0.05, -0.03, -0.01, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07])
        var = _value_at_risk(returns, 0.1)
        # 10% of 10 items = index 1 -> second worst = -0.03
        assert var > 0

    def test_cvar_worse_than_var(self):
        returns = [-0.10, -0.05, -0.03, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.07]
        var = _value_at_risk(returns, 0.1)
        cvar = _conditional_var(returns, 0.1)
        # CVaR (expected shortfall) should be >= VaR
        assert cvar >= var


# ── _correlation ─────────────────────────────────────────────────


class TestCorrelation:
    def test_perfect_positive(self):
        x = [1.0, 2.0, 3.0, 4.0]
        corr = _correlation(x, x)
        assert abs(corr - 1.0) < 1e-6

    def test_perfect_negative(self):
        x = [1.0, 2.0, 3.0, 4.0]
        y = [-1.0, -2.0, -3.0, -4.0]
        corr = _correlation(x, y)
        assert abs(corr - (-1.0)) < 1e-6

    def test_insufficient_data(self):
        assert _correlation([1.0], [2.0]) == 0.0

    def test_constant_series(self):
        x = [3.0, 3.0, 3.0]
        y = [1.0, 2.0, 3.0]
        assert _correlation(x, y) == 0.0


# ── _alpha_beta ──────────────────────────────────────────────────


class TestAlphaBeta:
    def test_insufficient_data(self):
        alpha, beta = _alpha_beta([0.01], [0.02])
        assert alpha == 0.0
        assert beta == 1.0

    def test_identical_returns(self):
        returns = [0.01, -0.005, 0.02, -0.01, 0.015]
        alpha, beta = _alpha_beta(returns, returns, 0.0)
        assert abs(beta - 1.0) < 1e-6
        assert abs(alpha) < 1e-4  # alpha should be near zero


# ── _tracking_error ──────────────────────────────────────────────


class TestTrackingError:
    def test_identical_returns(self):
        returns = [0.01, -0.005, 0.02]
        te = _tracking_error(returns, returns)
        assert te == 0.0

    def test_different_returns(self):
        r = [0.01, 0.02, 0.03]
        b = [0.005, 0.01, 0.015]
        te = _tracking_error(r, b)
        assert te > 0

    def test_insufficient_data(self):
        assert _tracking_error([0.01], [0.02]) == 0.0


# ── _information_ratio ───────────────────────────────────────────


class TestInformationRatio:
    def test_identical_returns(self):
        returns = [0.01, 0.02, 0.03]
        assert _information_ratio(returns, returns) == 0.0

    def test_outperformance(self):
        r = [0.02, 0.03, 0.04, 0.05]
        b = [0.01, 0.01, 0.01, 0.01]
        ir = _information_ratio(r, b)
        assert ir > 0


# ── _trade_statistics ────────────────────────────────────────────


class TestTradeStatistics:
    def test_empty_trades(self):
        result = _trade_statistics([])
        assert result["totalTrades"] == 0
        assert result["winRate"] == 0.0
        assert result["profitFactor"] == 0.0

    def test_all_wins(self):
        trades = [{"profit": 100}, {"profit": 200}]
        result = _trade_statistics(trades)
        assert result["winRate"] == 1.0
        assert result["totalTrades"] == 2
        assert result["avgWin"] == 150.0
        assert result["avgLoss"] == 0.0
        assert result["profitFactor"] == 0  # no losses means 0 avgLoss -> 0

    def test_all_losses(self):
        trades = [{"profit": -100}, {"profit": -200}]
        result = _trade_statistics(trades)
        assert result["winRate"] == 0.0
        assert result["avgWin"] == 0.0
        assert result["avgLoss"] == 150.0

    def test_mixed(self):
        trades = [
            {"profit": 300},
            {"profit": -100},
            {"profit": 200},
            {"profit": -50},
        ]
        result = _trade_statistics(trades)
        assert result["totalTrades"] == 4
        assert result["winRate"] == 0.5
        assert result["avgWin"] == 250.0
        assert result["avgLoss"] == 75.0
        expected_pf = 250.0 / 75.0
        assert abs(result["profitFactor"] - expected_pf) < 0.01


# ── _monthly_returns ─────────────────────────────────────────────


class TestMonthlyReturns:
    def test_empty(self):
        assert _monthly_returns([]) == []

    def test_single_point(self):
        assert _monthly_returns([{"date": "2024-01-01", "value": 100}]) == []

    def test_same_month(self):
        curve = [
            {"date": "2024-01-01", "value": 100},
            {"date": "2024-01-15", "value": 110},
        ]
        result = _monthly_returns(curve)
        assert len(result) == 1
        assert result[0]["month"] == "2024-01"
        assert abs(result[0]["return"] - 0.1) < 1e-4

    def test_two_months(self):
        curve = [
            {"date": "2024-01-01", "value": 100},
            {"date": "2024-02-01", "value": 110},
            {"date": "2024-02-28", "value": 105},
        ]
        result = _monthly_returns(curve)
        assert len(result) == 2
        assert result[0]["month"] == "2024-01"
        assert result[1]["month"] == "2024-02"


# ── analyze_risk (integration) ───────────────────────────────────


class TestAnalyzeRisk:
    def test_insufficient_data(self):
        result = analyze_risk({"equityCurve": [{"date": "2024-01-01", "value": 100}]})
        assert "error" in result

    def test_empty_curve(self):
        result = analyze_risk({"equityCurve": []})
        assert "error" in result

    def test_basic_analysis(self):
        curve = _make_equity_curve([100000, 101000, 102000, 101500, 103000, 104000])
        result = analyze_risk({"equityCurve": curve, "initialCash": 100000})
        assert "totalReturn" in result
        assert result["totalReturn"] > 0
        assert "annualizedReturn" in result
        assert "annualizedVolatility" in result
        assert "maxDrawdown" in result
        assert "sharpeRatio" in result
        assert "sortinoRatio" in result
        assert "calmarRatio" in result
        assert "var95" in result
        assert "cvar95" in result
        assert "tradingDays" in result
        assert result["tradingDays"] == 5

    def test_downtrend(self):
        curve = _make_equity_curve([100000, 98000, 95000, 93000, 90000])
        result = analyze_risk({"equityCurve": curve, "initialCash": 100000})
        assert result["totalReturn"] < 0
        assert result["maxDrawdown"] > 0

    def test_with_trades(self):
        curve = _make_equity_curve([100000, 101000, 102000])
        trades = [
            {"profit": 500, "returnPct": 0.02},
            {"profit": -200, "returnPct": -0.01},
            {"profit": 700, "returnPct": 0.03},
        ]
        result = analyze_risk({"equityCurve": curve, "trades": trades})
        assert result["totalTrades"] == 3
        assert result["winRate"] > 0

    def test_with_benchmark(self):
        curve = _make_equity_curve([100000, 105000, 110000, 108000, 112000])
        bench = _make_equity_curve([100000, 101000, 102000, 101000, 103000])
        result = analyze_risk({
            "equityCurve": curve,
            "benchmarkCurve": bench,
            "riskFreeRate": 0.03,
        })
        assert "benchmark" in result
        bm = result["benchmark"]
        assert "totalReturn" in bm
        assert "alpha" in bm
        assert "beta" in bm
        assert "trackingError" in bm
        assert "informationRatio" in bm
        assert "correlation" in bm

    def test_without_benchmark(self):
        curve = _make_equity_curve([100000, 105000, 110000])
        result = analyze_risk({"equityCurve": curve})
        assert "benchmark" not in result

    def test_monthly_returns_in_output(self):
        # Curve spanning 2 months
        values = [100000 + i * 100 for i in range(60)]
        curve = _make_equity_curve(values, start_date="2024-01-01")
        result = analyze_risk({"equityCurve": curve})
        assert "monthlyReturns" in result
        assert len(result["monthlyReturns"]) >= 1

    def test_flat_equity_curve(self):
        curve = _make_equity_curve([100000, 100000, 100000, 100000])
        result = analyze_risk({"equityCurve": curve})
        assert result["totalReturn"] == 0.0
        assert result["maxDrawdown"] == 0.0
        assert result["annualizedVolatility"] == 0.0

    def test_custom_risk_free_rate(self):
        curve = _make_equity_curve([100000, 101000, 102000, 103000])
        r1 = analyze_risk({"equityCurve": curve, "riskFreeRate": 0.0})
        r2 = analyze_risk({"equityCurve": curve, "riskFreeRate": 0.10})
        # Higher risk-free rate should result in lower Sharpe
        assert r1["sharpeRatio"] >= r2["sharpeRatio"]
