#!/usr/bin/env python3
"""
VaR/CVaR Risk Engine (风险价值计算引擎).

Implements Value at Risk (VaR) and Conditional VaR (Expected Shortfall)
using three approaches:
  1. Parametric (Variance-Covariance) — normal distribution assumption
  2. Historical Simulation — empirical distribution from past returns
  3. Monte Carlo Simulation — GBM-simulated return distribution

Capabilities:
  - Single-asset VaR/CVaR
  - Portfolio VaR with correlation (parametric & MC)
  - Stress testing with custom scenarios
  - VaR backtesting (Kupiec test)

Input:  JSON via stdin or QUANT_CONFIG env
Output: JSON to stdout

Requirements:
  pip install numpy scipy
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from typing import Any, Optional

import numpy as np

try:
    from scipy.stats import norm
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    class _NormFallback:
        @staticmethod
        def cdf(x: float) -> float:
            return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
        @staticmethod
        def pdf(x: float) -> float:
            return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
        @staticmethod
        def ppf(p: float) -> float:
            # Rational approximation (Abramowitz and Stegun 26.2.23)
            if p <= 0:
                return -10.0
            if p >= 1:
                return 10.0
            if p < 0.5:
                return -_NormFallback.ppf(1 - p)
            t = math.sqrt(-2 * math.log(1 - p))
            c0, c1, c2 = 2.515517, 0.802853, 0.010328
            d1, d2, d3 = 1.432788, 0.189269, 0.001308
            return t - (c0 + c1 * t + c2 * t * t) / (1 + d1 * t + d2 * t * t + d3 * t * t * t)
    norm = _NormFallback()  # type: ignore[assignment]


# ── Parametric VaR (Variance-Covariance) ──────────────────────────────────────

def parametric_var(
    returns: np.ndarray,
    confidence: float = 0.95,
    holding_period: int = 1,
    portfolio_value: float = 1_000_000.0,
) -> dict:
    """Parametric VaR assuming normal distribution.

    VaR = μ - z_α · σ · √h
    where z_α is the inverse normal CDF at confidence level.
    Scaled by √(holding_period) for multi-day VaR.
    """
    mu = float(np.mean(returns))
    sigma = float(np.std(returns, ddof=1))

    z = norm.ppf(1 - confidence)  # negative for left tail
    sqrt_h = math.sqrt(holding_period)

    var_pct = -(mu * holding_period + z * sigma * sqrt_h)
    var_abs = var_pct * portfolio_value

    # CVaR (Expected Shortfall) for normal distribution
    # ES = μ + σ · φ(z_α) / (1 - α)  (left tail)
    pdf_z = norm.pdf(norm.ppf(1 - confidence))
    cvar_pct = -(mu * holding_period - sigma * sqrt_h * pdf_z / (1 - confidence))
    cvar_abs = cvar_pct * portfolio_value

    return {
        "method": "parametric",
        "confidence": confidence,
        "holdingPeriod": holding_period,
        "varPct": round(var_pct * 100, 4),
        "varAbsolute": round(var_abs, 2),
        "cvarPct": round(cvar_pct * 100, 4),
        "cvarAbsolute": round(cvar_abs, 2),
        "portfolioValue": portfolio_value,
        "dailyMean": round(mu * 100, 6),
        "dailyStd": round(sigma * 100, 6),
        "annualizedVol": round(sigma * math.sqrt(252) * 100, 2),
    }


def portfolio_parametric_var(
    returns_matrix: np.ndarray,
    weights: np.ndarray,
    confidence: float = 0.95,
    holding_period: int = 1,
    portfolio_value: float = 1_000_000.0,
) -> dict:
    """Portfolio VaR using variance-covariance method.

    σ_p² = w^T · Σ · w
    VaR_p = z_α · σ_p · √h · V
    """
    n_assets = returns_matrix.shape[1]
    cov_matrix = np.cov(returns_matrix, rowvar=False)
    port_var = float(weights @ cov_matrix @ weights)
    port_std = math.sqrt(port_var)
    port_mean = float(np.mean(returns_matrix @ weights))

    z = norm.ppf(1 - confidence)
    sqrt_h = math.sqrt(holding_period)

    var_pct = -(port_mean * holding_period + z * port_std * sqrt_h)
    var_abs = var_pct * portfolio_value

    pdf_z = norm.pdf(norm.ppf(1 - confidence))
    cvar_pct = -(port_mean * holding_period - port_std * sqrt_h * pdf_z / (1 - confidence))
    cvar_abs = cvar_pct * portfolio_value

    # Component VaR: marginal contribution of each asset
    marginal_var = cov_matrix @ weights / port_std
    component_var = weights * marginal_var * norm.ppf(confidence) * sqrt_h * portfolio_value

    # Correlation matrix for reporting
    std_devs = np.sqrt(np.diag(cov_matrix))
    corr_matrix = cov_matrix / np.outer(std_devs, std_devs)

    return {
        "method": "parametric_portfolio",
        "confidence": confidence,
        "holdingPeriod": holding_period,
        "varPct": round(var_pct * 100, 4),
        "varAbsolute": round(var_abs, 2),
        "cvarPct": round(cvar_pct * 100, 4),
        "cvarAbsolute": round(cvar_abs, 2),
        "portfolioValue": portfolio_value,
        "portfolioVol": round(port_std * math.sqrt(252) * 100, 2),
        "componentVaR": [round(float(c), 2) for c in component_var],
        "correlationMatrix": [[round(float(c), 4) for c in row] for row in corr_matrix],
        "weights": [round(float(w), 4) for w in weights],
    }


# ── Historical Simulation VaR ────────────────────────────────────────────────

def historical_var(
    returns: np.ndarray,
    confidence: float = 0.95,
    holding_period: int = 1,
    portfolio_value: float = 1_000_000.0,
) -> dict:
    """Historical simulation VaR — uses empirical distribution directly.

    No distributional assumptions. Percentile of historical returns.
    For multi-day, uses overlapping windows.
    """
    if holding_period > 1:
        # Multi-day returns from overlapping windows
        multi_returns = np.array([
            np.sum(returns[i:i + holding_period])
            for i in range(len(returns) - holding_period + 1)
        ])
    else:
        multi_returns = returns

    sorted_returns = np.sort(multi_returns)
    n = len(sorted_returns)

    # VaR: the (1-confidence) quantile of losses
    var_idx = int(math.floor(n * (1 - confidence)))
    var_pct = -float(sorted_returns[var_idx])
    var_abs = var_pct * portfolio_value

    # CVaR: average of losses beyond VaR
    tail = sorted_returns[:var_idx + 1]
    if len(tail) > 0:
        cvar_pct = -float(np.mean(tail))
    else:
        cvar_pct = var_pct
    cvar_abs = cvar_pct * portfolio_value

    # Worst-case scenarios
    worst_n = min(5, len(sorted_returns))
    worst_returns = sorted_returns[:worst_n]

    return {
        "method": "historical",
        "confidence": confidence,
        "holdingPeriod": holding_period,
        "varPct": round(var_pct * 100, 4),
        "varAbsolute": round(var_abs, 2),
        "cvarPct": round(cvar_pct * 100, 4),
        "cvarAbsolute": round(cvar_abs, 2),
        "portfolioValue": portfolio_value,
        "nObservations": int(n),
        "tailObservations": int(len(tail)),
        "worstReturns": [round(float(r) * 100, 4) for r in worst_returns],
        "skewness": round(float(_skewness(multi_returns)), 4),
        "kurtosis": round(float(_kurtosis(multi_returns)), 4),
    }


# ── Monte Carlo VaR ──────────────────────────────────────────────────────────

def monte_carlo_var(
    returns: np.ndarray,
    confidence: float = 0.95,
    holding_period: int = 1,
    portfolio_value: float = 1_000_000.0,
    n_simulations: int = 100_000,
    seed: Optional[int] = None,
) -> dict:
    """Monte Carlo VaR — simulate future returns from fitted parameters.

    Fits normal distribution to historical returns, then simulates
    forward returns via GBM-like process.
    """
    t0 = time.time()
    rng = np.random.default_rng(seed)

    mu = float(np.mean(returns))
    sigma = float(np.std(returns, ddof=1))

    n_simulations = min(n_simulations, 1_000_000)

    # Simulate multi-day returns
    if holding_period > 1:
        daily_returns = rng.normal(mu, sigma, (n_simulations, holding_period))
        # Compound returns over holding period
        cumulative = np.sum(daily_returns, axis=1)
    else:
        cumulative = rng.normal(mu, sigma, n_simulations)

    sorted_sim = np.sort(cumulative)

    var_idx = int(math.floor(n_simulations * (1 - confidence)))
    var_pct = -float(sorted_sim[var_idx])
    var_abs = var_pct * portfolio_value

    tail = sorted_sim[:var_idx + 1]
    cvar_pct = -float(np.mean(tail)) if len(tail) > 0 else var_pct
    cvar_abs = cvar_pct * portfolio_value

    elapsed = time.time() - t0

    return {
        "method": "monte_carlo",
        "confidence": confidence,
        "holdingPeriod": holding_period,
        "varPct": round(var_pct * 100, 4),
        "varAbsolute": round(var_abs, 2),
        "cvarPct": round(cvar_pct * 100, 4),
        "cvarAbsolute": round(cvar_abs, 2),
        "portfolioValue": portfolio_value,
        "nSimulations": n_simulations,
        "fittedMean": round(mu * 100, 6),
        "fittedStd": round(sigma * 100, 6),
        "elapsedMs": round(elapsed * 1000, 1),
    }


# ── Stress Testing ───────────────────────────────────────────────────────────

def stress_test(
    portfolio_value: float,
    positions: list[dict],
    scenarios: list[dict],
) -> dict:
    """Run stress test scenarios on a portfolio.

    Args:
        positions: [{"name": "沪深300", "weight": 0.6, "beta": 1.0}, ...]
        scenarios: [{"name": "2015股灾", "marketReturn": -0.40, "volShock": 2.0}, ...]

    Each scenario contains:
        marketReturn: expected market return in the scenario
        volShock:     volatility multiplier (1.0 = no change)
    """
    results = []
    for scenario in scenarios:
        name = scenario.get("name", "unnamed")
        mkt_return = float(scenario.get("marketReturn", 0))
        vol_shock = float(scenario.get("volShock", 1.0))

        losses = []
        for pos in positions:
            weight = float(pos.get("weight", 0))
            beta = float(pos.get("beta", 1.0))
            pos_return = beta * mkt_return * vol_shock
            loss = weight * pos_return * portfolio_value
            losses.append({
                "name": pos.get("name", "unknown"),
                "weight": weight,
                "posReturn": round(pos_return * 100, 2),
                "loss": round(loss, 2),
            })

        total_loss = sum(l["loss"] for l in losses)
        results.append({
            "scenario": name,
            "marketReturn": round(mkt_return * 100, 2),
            "volShock": vol_shock,
            "totalLoss": round(total_loss, 2),
            "totalReturnPct": round(total_loss / portfolio_value * 100, 2),
            "positions": losses,
        })

    return {
        "portfolioValue": portfolio_value,
        "scenarios": results,
    }


# ── VaR Backtesting (Kupiec POF test) ────────────────────────────────────────

def backtest_var(
    returns: np.ndarray,
    var_pct: float,
    confidence: float = 0.95,
) -> dict:
    """Kupiec Proportion of Failures (POF) test for VaR model validation.

    Tests whether the observed number of VaR breaches matches the
    expected number (1 - confidence) × N.
    """
    n = len(returns)
    breaches = np.sum(returns < -var_pct)
    expected_breaches = n * (1 - confidence)
    breach_rate = breaches / n

    # Kupiec LR statistic
    # LR = -2 * [ln((1-p)^(n-x) * p^x) - ln((1-x/n)^(n-x) * (x/n)^x)]
    p = 1 - confidence
    x = int(breaches)

    if x == 0 or x == n:
        lr_stat = 0.0
        p_value = 1.0
    else:
        obs_rate = x / n
        lr_stat = -2 * (
            (n - x) * math.log(1 - p) + x * math.log(p)
            - (n - x) * math.log(1 - obs_rate) - x * math.log(obs_rate)
        )
        # LR ~ chi2(1), critical value at 95% is 3.841
        p_value = 1 - _chi2_cdf_1(lr_stat)

    zone = "green" if p_value > 0.05 else ("yellow" if p_value > 0.01 else "red")

    return {
        "nObservations": int(n),
        "nBreaches": int(breaches),
        "expectedBreaches": round(expected_breaches, 1),
        "breachRate": round(breach_rate * 100, 2),
        "expectedRate": round((1 - confidence) * 100, 2),
        "kupiecLR": round(lr_stat, 4),
        "pValue": round(p_value, 4),
        "zone": zone,
        "interpretation": (
            f"{'通过' if zone == 'green' else '未通过'}: "
            f"观察到 {breaches} 次突破 (预期 {expected_breaches:.0f}), "
            f"突破率 {breach_rate * 100:.1f}% (预期 {(1 - confidence) * 100:.0f}%)"
        ),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _skewness(x: np.ndarray) -> float:
    n = len(x)
    if n < 3:
        return 0.0
    mu = np.mean(x)
    s = np.std(x, ddof=1)
    if s == 0:
        return 0.0
    return float(np.mean(((x - mu) / s) ** 3) * n * (n - 1) / ((n - 1) * (n - 2))) if n > 2 else 0.0

def _kurtosis(x: np.ndarray) -> float:
    n = len(x)
    if n < 4:
        return 0.0
    mu = np.mean(x)
    s = np.std(x, ddof=1)
    if s == 0:
        return 0.0
    return float(np.mean(((x - mu) / s) ** 4)) - 3.0  # excess kurtosis

def _chi2_cdf_1(x: float) -> float:
    """CDF of chi-squared distribution with 1 degree of freedom."""
    if x <= 0:
        return 0.0
    return 2 * norm.cdf(math.sqrt(x)) - 1


# ── Main Dispatcher ───────────────────────────────────────────────────────────

def run_var_engine(config: dict) -> dict:
    """Main entry point for VaR/CVaR calculations.

    Actions:
        parametric    — Parametric (normal) VaR/CVaR
        historical    — Historical simulation VaR/CVaR
        monte_carlo   — Monte Carlo VaR/CVaR
        portfolio     — Portfolio parametric VaR with correlations
        stress_test   — Stress testing with custom scenarios
        backtest      — Kupiec POF test for VaR backtesting
        compare       — Compare all three methods side-by-side
    """
    action = config.get("action", "parametric")

    try:
        confidence = float(config.get("confidence", 0.95))
        holding_period = int(config.get("holdingPeriod", 1))
        portfolio_value = float(config.get("portfolioValue", 1_000_000))

        # Returns data: either provided directly or computed from prices
        returns_raw = config.get("returns", [])
        prices_raw = config.get("prices", [])

        if returns_raw:
            returns = np.array(returns_raw, dtype=float)
        elif prices_raw:
            prices = np.array(prices_raw, dtype=float)
            returns = np.diff(np.log(prices))  # log returns
        else:
            if action not in ("stress_test",):
                return {"ok": False, "error": "Must provide 'returns' or 'prices' array"}
            returns = np.array([])

        if action == "parametric":
            if len(returns) < 10:
                return {"ok": False, "error": "Need at least 10 return observations"}
            result = parametric_var(returns, confidence, holding_period, portfolio_value)
            return {"ok": True, "data": result}

        elif action == "historical":
            if len(returns) < 30:
                return {"ok": False, "error": "Need at least 30 return observations for historical VaR"}
            result = historical_var(returns, confidence, holding_period, portfolio_value)
            return {"ok": True, "data": result}

        elif action == "monte_carlo":
            if len(returns) < 10:
                return {"ok": False, "error": "Need at least 10 return observations"}
            n_sims = int(config.get("nSimulations", 100_000))
            seed = config.get("seed")
            if seed is not None:
                seed = int(seed)
            result = monte_carlo_var(returns, confidence, holding_period, portfolio_value, n_sims, seed)
            return {"ok": True, "data": result}

        elif action == "portfolio":
            # Multiple asset returns
            assets_returns = config.get("assetsReturns", [])
            weights = config.get("weights", [])
            if not assets_returns or not weights:
                return {"ok": False, "error": "Must provide 'assetsReturns' (2D array) and 'weights'"}

            returns_mat = np.array(assets_returns, dtype=float).T  # columns = assets
            w = np.array(weights, dtype=float)

            if returns_mat.shape[1] != len(w):
                return {"ok": False, "error": f"assetsReturns has {returns_mat.shape[1]} assets but weights has {len(w)}"}

            result = portfolio_parametric_var(returns_mat, w, confidence, holding_period, portfolio_value)
            return {"ok": True, "data": result}

        elif action == "stress_test":
            positions = config.get("positions", [])
            scenarios = config.get("scenarios", [])
            if not positions or not scenarios:
                return {"ok": False, "error": "Must provide 'positions' and 'scenarios'"}
            result = stress_test(portfolio_value, positions, scenarios)
            return {"ok": True, "data": result}

        elif action == "backtest":
            if len(returns) < 30:
                return {"ok": False, "error": "Need at least 30 return observations"}
            # First compute VaR, then backtest
            var_result = parametric_var(returns, confidence, 1, portfolio_value)
            var_pct = var_result["varPct"] / 100.0
            bt = backtest_var(returns, var_pct, confidence)
            bt["varMethod"] = "parametric"
            bt["varPct"] = var_result["varPct"]
            return {"ok": True, "data": bt}

        elif action == "compare":
            if len(returns) < 30:
                return {"ok": False, "error": "Need at least 30 return observations"}
            seed = config.get("seed")
            if seed is not None:
                seed = int(seed)

            param = parametric_var(returns, confidence, holding_period, portfolio_value)
            hist = historical_var(returns, confidence, holding_period, portfolio_value)
            mc = monte_carlo_var(returns, confidence, holding_period, portfolio_value, 100_000, seed)

            return {"ok": True, "data": {
                "parametric": param,
                "historical": hist,
                "monteCarlo": mc,
                "summary": {
                    "confidence": confidence,
                    "holdingPeriod": holding_period,
                    "portfolioValue": portfolio_value,
                    "varRange": {
                        "min": min(param["varAbsolute"], hist["varAbsolute"], mc["varAbsolute"]),
                        "max": max(param["varAbsolute"], hist["varAbsolute"], mc["varAbsolute"]),
                    },
                    "cvarRange": {
                        "min": min(param["cvarAbsolute"], hist["cvarAbsolute"], mc["cvarAbsolute"]),
                        "max": max(param["cvarAbsolute"], hist["cvarAbsolute"], mc["cvarAbsolute"]),
                    },
                },
            }}

        else:
            return {"ok": False, "error": f"Unknown action: {action}"}

    except Exception as e:
        import traceback
        traceback.print_exc(file=sys.stderr)
        return {"ok": False, "error": str(e)}


# ── stdin → stdout JSON bridge ────────────────────────────────────────────────

if __name__ == "__main__":
    def get_input() -> dict:
        config_str = os.environ.get("QUANT_CONFIG")
        if config_str:
            return json.loads(config_str)
        return json.loads(sys.stdin.read())

    try:
        cfg = get_input()
        result = run_var_engine(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
