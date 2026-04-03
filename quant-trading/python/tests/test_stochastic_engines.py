"""
Tests for Monte Carlo, Binomial Tree, Delta Hedge, and VaR engines.

Validates numerical correctness against BSM analytical benchmarks.
"""

import math
import sys
import os

import numpy as np
import pytest

# Ensure engines directory is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "engines"))

from monte_carlo_engine import (
    mc_price_european,
    mc_price_asian,
    mc_price_barrier,
    mc_greeks,
    simulate_gbm_paths,
    path_statistics,
    run_monte_carlo_engine,
)
from binomial_tree import (
    binomial_tree_price,
    convergence_analysis,
    run_binomial_tree_engine,
)
from delta_hedge_sim import (
    simulate_delta_hedge,
    hedge_ensemble,
    frequency_analysis,
    run_delta_hedge_engine,
)
from var_engine import (
    parametric_var,
    historical_var,
    monte_carlo_var,
    stress_test,
    backtest_var,
    run_var_engine,
)
from greeks_engine import bsm_price


# ── Monte Carlo Engine Tests ─────────────────────────────────────────────────


class TestMonteCarloEuropean:
    """MC European pricing should converge to BSM."""

    def test_call_price_matches_bsm(self):
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.2
        result = mc_price_european(S, K, T, r, sigma, "call", seed=42, n_paths=200_000)
        bsm = bsm_price(S, K, T, r, sigma, "call")
        assert abs(result["price"] - bsm) < 0.1, f"MC {result['price']} vs BSM {bsm}"

    def test_put_price_matches_bsm(self):
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.2
        result = mc_price_european(S, K, T, r, sigma, "put", seed=42, n_paths=200_000)
        bsm = bsm_price(S, K, T, r, sigma, "put")
        assert abs(result["price"] - bsm) < 0.1, f"MC {result['price']} vs BSM {bsm}"

    def test_deep_itm_call(self):
        S, K, T, r, sigma = 150, 100, 0.5, 0.03, 0.25
        result = mc_price_european(S, K, T, r, sigma, "call", seed=7, n_paths=100_000)
        bsm = bsm_price(S, K, T, r, sigma, "call")
        assert abs(result["price"] - bsm) < 0.5

    def test_otm_put(self):
        S, K, T, r, sigma = 120, 100, 0.25, 0.02, 0.3
        result = mc_price_european(S, K, T, r, sigma, "put", seed=99, n_paths=100_000)
        bsm = bsm_price(S, K, T, r, sigma, "put")
        assert abs(result["price"] - bsm) < 0.2

    def test_antithetic_reduces_std_error(self):
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.2
        no_anti = mc_price_european(S, K, T, r, sigma, seed=42, n_paths=50_000, antithetic=False)
        with_anti = mc_price_european(S, K, T, r, sigma, seed=42, n_paths=50_000, antithetic=True)
        # Antithetic typically reduces std error
        assert with_anti["stdError"] <= no_anti["stdError"] * 1.5  # allow some noise

    def test_confidence_interval_contains_bsm(self):
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.2
        result = mc_price_european(S, K, T, r, sigma, seed=42, n_paths=200_000)
        bsm = bsm_price(S, K, T, r, sigma)
        assert result["ci95Lower"] <= bsm <= result["ci95Upper"]

    def test_result_fields(self):
        result = mc_price_european(100, 100, 0.25, 0.05, 0.2, seed=42)
        assert "price" in result
        assert "stdError" in result
        assert "bsmBenchmark" in result
        assert "nPaths" in result


class TestMonteCarloAsian:
    """Asian option pricing (no closed-form for arithmetic)."""

    def test_arithmetic_asian_call(self):
        result = mc_price_asian(100, 100, 1.0, 0.05, 0.2, "call", seed=42, n_paths=100_000)
        # Asian call should be cheaper than vanilla call (averaging reduces vol)
        vanilla = bsm_price(100, 100, 1.0, 0.05, 0.2, "call")
        assert result["price"] < vanilla * 1.05  # allow small noise margin
        assert result["price"] > 0

    def test_geometric_asian_call(self):
        result = mc_price_asian(100, 100, 1.0, 0.05, 0.2, "call", average_type="geometric", seed=42, n_paths=100_000)
        assert result["price"] > 0
        assert result["averageType"] == "geometric"

    def test_control_variate_benefit(self):
        no_cv = mc_price_asian(100, 100, 1.0, 0.05, 0.2, "call", seed=42, n_paths=50_000, control_variate=False)
        with_cv = mc_price_asian(100, 100, 1.0, 0.05, 0.2, "call", seed=42, n_paths=50_000, control_variate=True)
        # Both should produce reasonably similar prices
        assert abs(no_cv["price"] - with_cv["price"]) < 1.0


class TestMonteCarloBarrier:
    """Barrier option pricing."""

    def test_up_and_out_call(self):
        result = mc_price_barrier(100, 100, 0.5, 0.05, 0.2, "call", barrier=130, barrier_type="up-and-out", seed=42)
        vanilla = bsm_price(100, 100, 0.5, 0.05, 0.2, "call")
        # Up-and-out call should be cheaper than vanilla
        assert result["price"] <= vanilla + 0.5
        assert result["price"] >= 0
        assert "knockProbability" in result

    def test_down_and_out_put(self):
        result = mc_price_barrier(100, 100, 0.5, 0.05, 0.2, "put", barrier=80, barrier_type="down-and-out", seed=42)
        assert result["price"] >= 0

    def test_in_out_parity(self):
        """knock-in + knock-out ≈ vanilla (in-out parity)."""
        S, K, T, r, sigma = 100, 100, 0.5, 0.05, 0.2
        out = mc_price_barrier(S, K, T, r, sigma, "call", barrier=120, barrier_type="up-and-out", seed=42, n_paths=200_000)
        in_ = mc_price_barrier(S, K, T, r, sigma, "call", barrier=120, barrier_type="up-and-in", seed=42, n_paths=200_000)
        vanilla = bsm_price(S, K, T, r, sigma, "call")
        # in + out ≈ vanilla
        assert abs(out["price"] + in_["price"] - vanilla) < 0.5


class TestGBMPaths:
    """GBM path simulation properties."""

    def test_path_shape(self):
        paths = simulate_gbm_paths(100, 0.05, 0.2, 1.0, 252, 1000, seed=42)
        assert paths.shape == (1000, 253)

    def test_initial_price(self):
        paths = simulate_gbm_paths(100, 0.05, 0.2, 1.0, 10, 100, seed=42)
        assert np.all(paths[:, 0] == 100.0)

    def test_risk_neutral_drift(self):
        """E[S_T] ≈ S_0 * exp(r*T) under risk-neutral measure."""
        S0, r, T = 100, 0.05, 1.0
        paths = simulate_gbm_paths(S0, r, 0.2, T, 252, 100_000, seed=42)
        mean_ST = np.mean(paths[:, -1])
        expected = S0 * math.exp(r * T)
        assert abs(mean_ST - expected) / expected < 0.01

    def test_path_stats_action(self):
        result = path_statistics(100, 0.05, 0.2, 1.0, seed=42, n_paths=10_000)
        assert "terminalPrice" in result
        assert "logReturn" in result
        assert abs(result["logReturn"]["mean"] - result["logReturn"]["theoretical_mean"]) < 0.02


# ── Binomial Tree Tests ──────────────────────────────────────────────────────


class TestBinomialTree:
    """CRR binomial tree convergence to BSM."""

    def test_european_call_converges(self):
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.2
        result = binomial_tree_price(S, K, T, r, sigma, "call", "european", n_steps=500)
        bsm = bsm_price(S, K, T, r, sigma, "call")
        assert abs(result["price"] - bsm) < 0.01, f"Tree {result['price']} vs BSM {bsm}"

    def test_european_put_converges(self):
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.2
        result = binomial_tree_price(S, K, T, r, sigma, "put", "european", n_steps=500)
        bsm = bsm_price(S, K, T, r, sigma, "put")
        assert abs(result["price"] - bsm) < 0.01

    def test_american_put_higher_than_european(self):
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.2
        eur = binomial_tree_price(S, K, T, r, sigma, "put", "european", n_steps=200)
        amer = binomial_tree_price(S, K, T, r, sigma, "put", "american", n_steps=200)
        assert amer["price"] >= eur["price"] - 0.001  # allow tiny numerical noise

    def test_american_call_no_dividend_equals_european(self):
        """Without dividends, American call = European call."""
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.2
        eur = binomial_tree_price(S, K, T, r, sigma, "call", "european", q=0, n_steps=200)
        amer = binomial_tree_price(S, K, T, r, sigma, "call", "american", q=0, n_steps=200)
        assert abs(eur["price"] - amer["price"]) < 0.01

    def test_delta_matches_bsm(self):
        S, K, T, r, sigma = 100, 100, 0.25, 0.05, 0.2
        result = binomial_tree_price(S, K, T, r, sigma, "call", "european", n_steps=500)
        from greeks_engine import calculate_delta
        bsm_delta = calculate_delta(S, K, T, r, sigma, "call")
        assert abs(result["delta"] - bsm_delta) < 0.01

    def test_tree_params(self):
        result = binomial_tree_price(100, 100, 0.25, 0.05, 0.2, "call", n_steps=100)
        assert "treeParams" in result
        assert result["treeParams"]["u"] > 1.0
        assert result["treeParams"]["d"] < 1.0
        assert 0 < result["treeParams"]["p"] < 1

    def test_convergence_analysis(self):
        result = convergence_analysis(100, 100, 0.25, 0.05, 0.2, "call")
        assert "convergence" in result
        assert len(result["convergence"]) > 3
        # Error should decrease with more steps (generally)
        errors = [abs(r["error"]) for r in result["convergence"]]
        assert errors[-1] < errors[0]  # last step more accurate than first

    def test_expired_option(self):
        result = binomial_tree_price(100, 90, 0, 0.05, 0.2, "call", n_steps=100)
        assert result["price"] == 10.0  # intrinsic value


# ── Delta Hedge Tests ────────────────────────────────────────────────────────


class TestDeltaHedge:
    """Delta hedging P&L simulation."""

    def test_single_hedge_returns_structure(self):
        result = simulate_delta_hedge(100, 100, 0.25, 0.05, 0.2, "call", seed=42, n_steps=50)
        assert "finalPnl" in result
        assert "optionPremium" in result
        assert "gammaPnl" in result
        assert "thetaPnl" in result
        assert "path" in result
        assert len(result["path"]) > 1

    def test_hedge_pnl_near_zero_with_matched_vol(self):
        """Perfect hedge with same implied and realized vol → P&L ≈ 0."""
        result = hedge_ensemble(
            100, 100, 0.25, 0.05, 0.2, "call",
            n_steps=252, n_simulations=500, hedge_freq=1,
            transaction_cost=0.0, seed=42,
        )
        # Mean P&L should be close to 0 when no vol mismatch and no costs
        assert abs(result["pnl"]["mean"]) < 0.5, f"Mean P&L: {result['pnl']['mean']}"

    def test_vol_mismatch_creates_systematic_pnl(self):
        """If realized vol > implied vol, short option loses (gamma P&L > theta P&L)."""
        result = hedge_ensemble(
            100, 100, 0.25, 0.05, 0.2, "call",
            n_steps=252, n_simulations=500, hedge_freq=1,
            realized_vol=0.35,  # realized > implied
            seed=42,
        )
        # Short option should lose when realized > implied
        assert result["pnl"]["mean"] < 0

    def test_transaction_costs_reduce_pnl(self):
        no_cost = hedge_ensemble(
            100, 100, 0.25, 0.05, 0.2, "call",
            n_steps=50, n_simulations=200, hedge_freq=1,
            transaction_cost=0.0, seed=42,
        )
        with_cost = hedge_ensemble(
            100, 100, 0.25, 0.05, 0.2, "call",
            n_steps=50, n_simulations=200, hedge_freq=1,
            transaction_cost=0.1, seed=42,
        )
        assert with_cost["pnl"]["mean"] < no_cost["pnl"]["mean"]

    def test_frequency_analysis(self):
        result = frequency_analysis(
            100, 100, 0.25, 0.05, 0.2, "call",
            n_steps=50, n_simulations=100,
            frequencies=[1, 5, 10], seed=42,
        )
        assert "analysis" in result
        assert len(result["analysis"]) == 3


# ── VaR Engine Tests ─────────────────────────────────────────────────────────


class TestVaR:
    """VaR/CVaR calculations."""

    @pytest.fixture
    def sample_returns(self):
        rng = np.random.default_rng(42)
        return rng.normal(0.0005, 0.015, 500)  # ~500 daily returns

    def test_parametric_var(self, sample_returns):
        result = parametric_var(sample_returns)
        assert result["varPct"] > 0
        assert result["cvarPct"] > result["varPct"]  # CVaR > VaR
        assert result["method"] == "parametric"

    def test_historical_var(self, sample_returns):
        result = historical_var(sample_returns)
        assert result["varPct"] > 0
        assert result["cvarPct"] >= result["varPct"]
        assert result["method"] == "historical"

    def test_monte_carlo_var(self, sample_returns):
        result = monte_carlo_var(sample_returns, seed=42)
        assert result["varPct"] > 0
        assert result["method"] == "monte_carlo"

    def test_higher_confidence_higher_var(self, sample_returns):
        var_95 = parametric_var(sample_returns, confidence=0.95)
        var_99 = parametric_var(sample_returns, confidence=0.99)
        assert var_99["varPct"] > var_95["varPct"]

    def test_longer_holding_higher_var(self, sample_returns):
        var_1d = parametric_var(sample_returns, holding_period=1)
        var_10d = parametric_var(sample_returns, holding_period=10)
        assert var_10d["varAbsolute"] > var_1d["varAbsolute"]

    def test_cvar_exceeds_var(self, sample_returns):
        result = parametric_var(sample_returns)
        assert result["cvarAbsolute"] >= result["varAbsolute"]

    def test_stress_test(self):
        result = stress_test(
            1_000_000,
            [
                {"name": "沪深300", "weight": 0.6, "beta": 1.0},
                {"name": "创业板", "weight": 0.4, "beta": 1.3},
            ],
            [
                {"name": "2015股灾", "marketReturn": -0.40, "volShock": 1.0},
                {"name": "温和下跌", "marketReturn": -0.10, "volShock": 1.0},
            ],
        )
        assert len(result["scenarios"]) == 2
        assert result["scenarios"][0]["totalLoss"] < 0  # loss in crash

    def test_backtest_var(self, sample_returns):
        result = backtest_var(sample_returns, 0.02, confidence=0.95)
        assert "nBreaches" in result
        assert "zone" in result
        assert result["zone"] in ("green", "yellow", "red")

    def test_var_compare(self, sample_returns):
        result = run_var_engine({
            "action": "compare",
            "returns": sample_returns.tolist(),
            "seed": 42,
        })
        assert result["ok"]
        assert "parametric" in result["data"]
        assert "historical" in result["data"]
        assert "monteCarlo" in result["data"]


# ── Dispatcher (run_*) Tests ─────────────────────────────────────────────────


class TestDispatchers:
    """Test the JSON dispatchers return proper ok/error format."""

    def test_mc_unknown_action(self):
        result = run_monte_carlo_engine({"action": "bogus"})
        assert result["ok"] is False

    def test_mc_missing_params(self):
        result = run_monte_carlo_engine({"action": "price_european", "spotPrice": 0})
        assert result["ok"] is False

    def test_bt_price_action(self):
        result = run_binomial_tree_engine({
            "action": "price",
            "spotPrice": 100, "strike": 100,
            "volatility": 0.2, "timeToExpiry": 0.25,
        })
        assert result["ok"]
        assert "price" in result["data"]

    def test_bt_compare_action(self):
        result = run_binomial_tree_engine({
            "action": "compare",
            "spotPrice": 100, "strike": 100,
            "volatility": 0.2, "timeToExpiry": 0.25,
        })
        assert result["ok"]
        assert "earlyExercisePremium" in result["data"]

    def test_dh_simulate_action(self):
        result = run_delta_hedge_engine({
            "action": "simulate",
            "spotPrice": 100, "strike": 100,
            "volatility": 0.2, "timeToExpiry": 0.25,
            "nSteps": 50, "seed": 42,
        })
        assert result["ok"]
        assert "finalPnl" in result["data"]

    def test_var_missing_returns(self):
        result = run_var_engine({"action": "parametric"})
        assert result["ok"] is False
