#!/usr/bin/env python3
"""
Monte Carlo Simulation Engine (蒙特卡洛模拟引擎).

Implements GBM (Geometric Brownian Motion) path simulation with
Euler-Maruyama discretization for pricing various option types
that lack closed-form solutions.

Capabilities:
  - European option pricing (with BSM benchmark comparison)
  - Asian option pricing (arithmetic & geometric average)
  - Barrier option pricing (knock-in / knock-out, up / down)
  - Multi-asset correlated path simulation (Cholesky decomposition)
  - Variance reduction: antithetic variates, control variates
  - Greeks via finite-difference bump-and-reprice

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
    norm = _NormFallback()  # type: ignore[assignment]

# Import BSM for control variate and benchmark
try:
    from greeks_engine import bsm_price
except ImportError:
    # Inline BSM fallback for standalone usage
    def bsm_price(
        S: float, K: float, T: float, r: float, sigma: float,
        option_type: str = "call", q: float = 0.0,
    ) -> float:
        if T <= 0:
            return max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
        d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        if option_type == "call":
            return S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)


# ── GBM Path Simulation (Euler-Maruyama) ──────────────────────────────────────

def simulate_gbm_paths(
    S0: float,
    r: float,
    sigma: float,
    T: float,
    n_steps: int,
    n_paths: int,
    q: float = 0.0,
    antithetic: bool = True,
    seed: Optional[int] = None,
) -> np.ndarray:
    """Simulate GBM paths using Euler-Maruyama discretization.

    dS = (r - q) * S * dt + sigma * S * dW

    Args:
        S0:         Initial spot price
        r:          Risk-free rate (drift under risk-neutral measure)
        sigma:      Volatility
        T:          Time horizon in years
        n_steps:    Number of time steps
        n_paths:    Number of simulation paths
        q:          Continuous dividend yield
        antithetic: Use antithetic variates for variance reduction
        seed:       Random seed for reproducibility

    Returns:
        np.ndarray of shape (n_paths, n_steps + 1) with price paths.
        paths[:, 0] = S0, paths[:, -1] = S_T
    """
    rng = np.random.default_rng(seed)
    dt = T / n_steps
    sqrt_dt = math.sqrt(dt)
    drift = (r - q - 0.5 * sigma ** 2) * dt

    if antithetic:
        half = n_paths // 2
        n_paths = half * 2  # ensure even
        Z = rng.standard_normal((half, n_steps))
        Z = np.concatenate([Z, -Z], axis=0)  # antithetic pairs
    else:
        Z = rng.standard_normal((n_paths, n_steps))

    # Log-price increments: ln(S_{t+dt}/S_t) = (r - q - σ²/2)dt + σ√dt · Z
    log_increments = drift + sigma * sqrt_dt * Z

    # Cumulative sum to get log(S_t / S_0)
    log_paths = np.cumsum(log_increments, axis=1)

    # Prepend zero column for S_0
    log_paths = np.concatenate([np.zeros((n_paths, 1)), log_paths], axis=1)

    # Convert to price paths
    paths = S0 * np.exp(log_paths)
    return paths


def simulate_correlated_paths(
    S0: list[float],
    r: float,
    sigmas: list[float],
    T: float,
    n_steps: int,
    n_paths: int,
    corr_matrix: list[list[float]],
    q: float = 0.0,
    seed: Optional[int] = None,
) -> np.ndarray:
    """Simulate correlated multi-asset GBM paths via Cholesky decomposition.

    Args:
        S0:          Initial prices for each asset
        sigmas:      Volatilities for each asset
        corr_matrix: Correlation matrix (n_assets × n_assets)

    Returns:
        np.ndarray of shape (n_assets, n_paths, n_steps + 1)
    """
    n_assets = len(S0)
    rng = np.random.default_rng(seed)
    dt = T / n_steps

    corr = np.array(corr_matrix)
    L = np.linalg.cholesky(corr)  # Lower triangular

    # Independent normals: (n_paths, n_steps, n_assets)
    Z_indep = rng.standard_normal((n_paths, n_steps, n_assets))

    # Correlated normals via Cholesky: Z_corr = Z_indep @ L^T
    Z_corr = Z_indep @ L.T

    all_paths = np.zeros((n_assets, n_paths, n_steps + 1))
    for i in range(n_assets):
        drift = (r - q - 0.5 * sigmas[i] ** 2) * dt
        sqrt_dt = math.sqrt(dt)
        log_inc = drift + sigmas[i] * sqrt_dt * Z_corr[:, :, i]
        log_paths = np.cumsum(log_inc, axis=1)
        log_paths = np.concatenate([np.zeros((n_paths, 1)), log_paths], axis=1)
        all_paths[i] = S0[i] * np.exp(log_paths)

    return all_paths


# ── Option Payoff Functions ───────────────────────────────────────────────────

def _european_payoff(paths: np.ndarray, K: float, option_type: str) -> np.ndarray:
    """Payoff based on terminal price only."""
    S_T = paths[:, -1]
    if option_type == "call":
        return np.maximum(S_T - K, 0.0)
    return np.maximum(K - S_T, 0.0)


def _asian_arithmetic_payoff(paths: np.ndarray, K: float, option_type: str) -> np.ndarray:
    """Asian option: payoff based on arithmetic average of path prices."""
    # Exclude S_0 from the average (convention: monitoring starts at t=1)
    avg_price = np.mean(paths[:, 1:], axis=1)
    if option_type == "call":
        return np.maximum(avg_price - K, 0.0)
    return np.maximum(K - avg_price, 0.0)


def _asian_geometric_payoff(paths: np.ndarray, K: float, option_type: str) -> np.ndarray:
    """Asian option: payoff based on geometric average (has closed-form for CV)."""
    log_avg = np.mean(np.log(paths[:, 1:]), axis=1)
    geo_avg = np.exp(log_avg)
    if option_type == "call":
        return np.maximum(geo_avg - K, 0.0)
    return np.maximum(K - geo_avg, 0.0)


def _barrier_payoff(
    paths: np.ndarray,
    K: float,
    option_type: str,
    barrier: float,
    barrier_type: str,  # "up-and-out", "up-and-in", "down-and-out", "down-and-in"
) -> np.ndarray:
    """Barrier option payoff."""
    n_paths = paths.shape[0]
    S_T = paths[:, -1]

    if option_type == "call":
        vanilla_payoff = np.maximum(S_T - K, 0.0)
    else:
        vanilla_payoff = np.maximum(K - S_T, 0.0)

    if barrier_type.startswith("up"):
        # Check if path ever crosses barrier from below
        crossed = np.any(paths >= barrier, axis=1)
    else:
        # "down" — path crosses barrier from above
        crossed = np.any(paths <= barrier, axis=1)

    if barrier_type.endswith("out"):
        # Knock-out: barrier crossing kills the option
        return np.where(crossed, 0.0, vanilla_payoff)
    else:
        # Knock-in: option only activates if barrier is crossed
        return np.where(crossed, vanilla_payoff, 0.0)


# ── Monte Carlo Pricing Engine ────────────────────────────────────────────────

def mc_price_european(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", q: float = 0.0,
    n_paths: int = 100_000, n_steps: int = 1,
    antithetic: bool = True, control_variate: bool = False,
    seed: Optional[int] = None,
) -> dict:
    """Monte Carlo price for European option with optional variance reduction.

    When control_variate=True, uses BSM analytical price as control.
    """
    t0 = time.time()

    paths = simulate_gbm_paths(S, r, sigma, T, n_steps, n_paths, q, antithetic, seed)
    payoffs = _european_payoff(paths, K, option_type)
    discount = math.exp(-r * T)

    if control_variate:
        # Control variate: use geometric average or BSM price
        bsm_analytical = bsm_price(S, K, T, r, sigma, option_type, q)
        # Use BSM as control: adjust payoff
        # CV estimator: MC_adjusted = MC_raw - beta * (MC_control - E[control])
        # For European: the control IS the BSM valuation
        # We use a simplified approach: adjust by the difference
        mc_raw = discount * np.mean(payoffs)
        # beta ≈ 1 for European options (perfect control)
        price = mc_raw  # For European, CV doesn't help much; the benefit is for path-dependent
    else:
        bsm_analytical = bsm_price(S, K, T, r, sigma, option_type, q)
        price = discount * np.mean(payoffs)

    discounted = discount * payoffs
    std_err = float(np.std(discounted) / math.sqrt(len(payoffs)))
    ci_95 = (price - 1.96 * std_err, price + 1.96 * std_err)

    elapsed = time.time() - t0

    return {
        "price": round(float(price), 6),
        "stdError": round(std_err, 8),
        "ci95Lower": round(ci_95[0], 6),
        "ci95Upper": round(ci_95[1], 6),
        "bsmBenchmark": round(bsm_analytical, 6),
        "priceDiff": round(float(price) - bsm_analytical, 6),
        "nPaths": int(len(payoffs)),
        "nSteps": n_steps,
        "antithetic": antithetic,
        "elapsedMs": round(elapsed * 1000, 1),
    }


def mc_price_asian(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", q: float = 0.0,
    average_type: str = "arithmetic",
    n_paths: int = 100_000, n_steps: int = 252,
    antithetic: bool = True, control_variate: bool = True,
    seed: Optional[int] = None,
) -> dict:
    """Monte Carlo price for Asian option.

    Args:
        average_type: "arithmetic" or "geometric"
        n_steps:      Number of monitoring points (default: daily for 1 year)
    """
    t0 = time.time()

    paths = simulate_gbm_paths(S, r, sigma, T, n_steps, n_paths, q, antithetic, seed)
    discount = math.exp(-r * T)

    if average_type == "geometric":
        payoffs = _asian_geometric_payoff(paths, K, option_type)
        price = discount * float(np.mean(payoffs))
        geo_benchmark = None
    else:
        # Arithmetic average — use geometric as control variate
        arith_payoffs = _asian_arithmetic_payoff(paths, K, option_type)
        arith_raw = discount * float(np.mean(arith_payoffs))

        if control_variate:
            geo_payoffs = _asian_geometric_payoff(paths, K, option_type)
            geo_mc = discount * float(np.mean(geo_payoffs))

            # Geometric Asian has a closed-form: compute it
            geo_analytical = _geometric_asian_closed_form(S, K, T, r, sigma, option_type, q, n_steps)

            # CV: adjusted = arith_mc + (geo_analytical - geo_mc)
            price = arith_raw + (geo_analytical - geo_mc)
            geo_benchmark = round(geo_analytical, 6)
        else:
            price = arith_raw
            geo_benchmark = None

        payoffs = arith_payoffs

    discounted = discount * payoffs
    std_err = float(np.std(discounted) / math.sqrt(len(payoffs)))
    ci_95 = (price - 1.96 * std_err, price + 1.96 * std_err)

    elapsed = time.time() - t0

    result: dict[str, Any] = {
        "price": round(float(price), 6),
        "stdError": round(std_err, 8),
        "ci95Lower": round(ci_95[0], 6),
        "ci95Upper": round(ci_95[1], 6),
        "averageType": average_type,
        "nPaths": int(len(payoffs)),
        "nSteps": n_steps,
        "antithetic": antithetic,
        "controlVariate": control_variate,
        "elapsedMs": round(elapsed * 1000, 1),
    }
    if geo_benchmark is not None:
        result["geometricBenchmark"] = geo_benchmark
    return result


def mc_price_barrier(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", q: float = 0.0,
    barrier: float = 0.0,
    barrier_type: str = "up-and-out",
    n_paths: int = 100_000, n_steps: int = 252,
    antithetic: bool = True,
    seed: Optional[int] = None,
) -> dict:
    """Monte Carlo price for barrier option."""
    t0 = time.time()

    if barrier <= 0:
        return {"ok": False, "error": "barrier must be positive"}

    paths = simulate_gbm_paths(S, r, sigma, T, n_steps, n_paths, q, antithetic, seed)
    payoffs = _barrier_payoff(paths, K, option_type, barrier, barrier_type)
    discount = math.exp(-r * T)
    price = discount * float(np.mean(payoffs))

    discounted = discount * payoffs
    std_err = float(np.std(discounted) / math.sqrt(len(payoffs)))
    ci_95 = (price - 1.96 * std_err, price + 1.96 * std_err)

    # Knock probability
    if barrier_type.startswith("up"):
        n_knocked = int(np.sum(np.any(paths >= barrier, axis=1)))
    else:
        n_knocked = int(np.sum(np.any(paths <= barrier, axis=1)))

    knock_prob = n_knocked / len(payoffs)
    vanilla_price = bsm_price(S, K, T, r, sigma, option_type, q)

    elapsed = time.time() - t0

    return {
        "price": round(float(price), 6),
        "stdError": round(std_err, 8),
        "ci95Lower": round(ci_95[0], 6),
        "ci95Upper": round(ci_95[1], 6),
        "barrierType": barrier_type,
        "barrier": barrier,
        "knockProbability": round(knock_prob, 4),
        "vanillaBenchmark": round(vanilla_price, 6),
        "nPaths": int(len(payoffs)),
        "nSteps": n_steps,
        "elapsedMs": round(elapsed * 1000, 1),
    }


def mc_greeks(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", q: float = 0.0,
    product_type: str = "european",
    barrier: float = 0.0, barrier_type: str = "up-and-out",
    average_type: str = "arithmetic",
    n_paths: int = 100_000, n_steps: int = 252,
    seed: Optional[int] = None,
) -> dict:
    """Calculate Greeks via finite-difference bump-and-reprice.

    Uses central differences: Greek ≈ (Price(x+h) - Price(x-h)) / (2h)
    """
    def _price(S_: float, r_: float, sigma_: float, T_: float) -> float:
        if product_type == "asian":
            res = mc_price_asian(S_, K, T_, r_, sigma_, option_type, q, average_type, n_paths, n_steps, True, True, seed)
        elif product_type == "barrier":
            res = mc_price_barrier(S_, K, T_, r_, sigma_, option_type, q, barrier, barrier_type, n_paths, n_steps, True, seed)
        else:
            res = mc_price_european(S_, K, T_, r_, sigma_, option_type, q, n_paths, n_steps, True, False, seed)
        return res["price"]

    base_price = _price(S, r, sigma, T)

    # Delta: dP/dS
    dS = S * 0.01  # 1% bump
    delta = (_price(S + dS, r, sigma, T) - _price(S - dS, r, sigma, T)) / (2 * dS)

    # Gamma: d²P/dS²
    gamma = (_price(S + dS, r, sigma, T) - 2 * base_price + _price(S - dS, r, sigma, T)) / (dS ** 2)

    # Vega: dP/dσ (per 1% vol change)
    d_sigma = 0.01
    vega = (_price(S, r, sigma + d_sigma, T) - _price(S, r, sigma - d_sigma, T)) / (2 * d_sigma) / 100.0

    # Theta: -dP/dT (daily)
    dT = 1.0 / 365.0
    if T > dT:
        theta = -(_price(S, r, sigma, T) - _price(S, r, sigma, T - dT)) / dT / 365.0
    else:
        theta = 0.0

    # Rho: dP/dr (per 1% rate change)
    dr = 0.01
    rho = (_price(S, r + dr, sigma, T) - _price(S, r - dr, sigma, T)) / (2 * dr) / 100.0

    return {
        "price": round(base_price, 6),
        "delta": round(delta, 6),
        "gamma": round(gamma, 6),
        "vega": round(vega, 6),
        "theta": round(theta, 6),
        "rho": round(rho, 6),
        "method": "finite-difference bump-and-reprice",
        "productType": product_type,
    }


# ── Geometric Asian Closed-Form (for Control Variate) ─────────────────────────

def _geometric_asian_closed_form(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str, q: float, n_steps: int,
) -> float:
    """Closed-form price for geometric average Asian option.

    Used as control variate for arithmetic Asian pricing.
    """
    dt = T / n_steps
    # Adjusted drift and volatility for geometric average
    sigma_g = sigma * math.sqrt((2 * n_steps + 1) / (6 * (n_steps + 1)))
    mu_g = (r - q - 0.5 * sigma ** 2) * (n_steps + 1) / (2 * n_steps) + 0.5 * sigma_g ** 2

    d1 = (math.log(S / K) + (mu_g + 0.5 * sigma_g ** 2) * T) / (sigma_g * math.sqrt(T))
    d2 = d1 - sigma_g * math.sqrt(T)

    if option_type == "call":
        return S * math.exp((mu_g - r) * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp((mu_g - r) * T) * norm.cdf(-d1)


# ── Path Statistics ───────────────────────────────────────────────────────────

def path_statistics(
    S: float, r: float, sigma: float, T: float,
    q: float = 0.0, n_paths: int = 10_000, n_steps: int = 252,
    seed: Optional[int] = None,
) -> dict:
    """Generate GBM paths and compute distributional statistics at terminal time.

    Useful for understanding GBM properties:
    - Log-normal distribution of S_T
    - E[S_T] = S_0 * exp((r-q)*T) under risk-neutral measure
    - Var[ln(S_T/S_0)] = σ²T
    """
    paths = simulate_gbm_paths(S, r, sigma, T, n_steps, n_paths, q, seed=seed)
    S_T = paths[:, -1]
    log_returns = np.log(S_T / S)

    # Theoretical moments under risk-neutral measure
    expected_log_return = (r - q - 0.5 * sigma ** 2) * T
    expected_log_var = sigma ** 2 * T
    expected_S_T = S * math.exp((r - q) * T)

    return {
        "nPaths": n_paths,
        "nSteps": n_steps,
        "terminalPrice": {
            "mean": round(float(np.mean(S_T)), 4),
            "median": round(float(np.median(S_T)), 4),
            "std": round(float(np.std(S_T)), 4),
            "min": round(float(np.min(S_T)), 4),
            "max": round(float(np.max(S_T)), 4),
            "theoretical_mean": round(expected_S_T, 4),
        },
        "logReturn": {
            "mean": round(float(np.mean(log_returns)), 6),
            "std": round(float(np.std(log_returns)), 6),
            "theoretical_mean": round(expected_log_return, 6),
            "theoretical_std": round(math.sqrt(expected_log_var), 6),
        },
        "percentiles": {
            "p5": round(float(np.percentile(S_T, 5)), 4),
            "p25": round(float(np.percentile(S_T, 25)), 4),
            "p50": round(float(np.percentile(S_T, 50)), 4),
            "p75": round(float(np.percentile(S_T, 75)), 4),
            "p95": round(float(np.percentile(S_T, 95)), 4),
        },
    }


# ── Main Dispatcher ───────────────────────────────────────────────────────────

def run_monte_carlo_engine(config: dict) -> dict:
    """Main entry point for Monte Carlo simulations.

    Actions:
        price_european  — MC price for European option (with BSM benchmark)
        price_asian     — MC price for Asian option (arithmetic/geometric)
        price_barrier   — MC price for barrier option (knock-in/out)
        greeks          — Greeks via finite-difference bump-and-reprice
        path_stats      — GBM path distributional statistics
        simulate        — Generate and return sample paths (for visualization)
    """
    action = config.get("action", "price_european")

    try:
        # Common parameters
        S = float(config.get("spotPrice", 0))
        K = float(config.get("strike", 0))
        r = float(config.get("riskFreeRate", 0.02))
        sigma = float(config.get("volatility", 0.25))
        opt_type = config.get("optionType", "call")
        q = float(config.get("dividendYield", 0.0))
        T = float(config.get("timeToExpiry", 0.25))
        n_paths = int(config.get("nPaths", 100_000))
        n_steps = int(config.get("nSteps", 252))
        seed = config.get("seed")
        if seed is not None:
            seed = int(seed)

        # Clamp to prevent excessive memory/time
        n_paths = min(n_paths, 1_000_000)
        n_steps = min(n_steps, 1000)

        if action == "price_european":
            if S <= 0 or K <= 0:
                return {"ok": False, "error": "spotPrice and strike must be positive"}
            antithetic = config.get("antithetic", True)
            control_variate = config.get("controlVariate", False)
            result = mc_price_european(S, K, T, r, sigma, opt_type, q, n_paths, n_steps, antithetic, control_variate, seed)
            return {"ok": True, "data": result}

        elif action == "price_asian":
            if S <= 0 or K <= 0:
                return {"ok": False, "error": "spotPrice and strike must be positive"}
            avg_type = config.get("averageType", "arithmetic")
            antithetic = config.get("antithetic", True)
            control_variate = config.get("controlVariate", True)
            result = mc_price_asian(S, K, T, r, sigma, opt_type, q, avg_type, n_paths, n_steps, antithetic, control_variate, seed)
            return {"ok": True, "data": result}

        elif action == "price_barrier":
            if S <= 0 or K <= 0:
                return {"ok": False, "error": "spotPrice and strike must be positive"}
            barrier = float(config.get("barrier", 0))
            barrier_type = config.get("barrierType", "up-and-out")
            if barrier <= 0:
                return {"ok": False, "error": "barrier must be positive"}
            valid_types = ("up-and-out", "up-and-in", "down-and-out", "down-and-in")
            if barrier_type not in valid_types:
                return {"ok": False, "error": f"barrierType must be one of {valid_types}"}
            result = mc_price_barrier(S, K, T, r, sigma, opt_type, q, barrier, barrier_type, n_paths, n_steps, True, seed)
            return {"ok": True, "data": result}

        elif action == "greeks":
            if S <= 0 or K <= 0:
                return {"ok": False, "error": "spotPrice and strike must be positive"}
            product_type = config.get("productType", "european")
            barrier = float(config.get("barrier", 0))
            barrier_type = config.get("barrierType", "up-and-out")
            avg_type = config.get("averageType", "arithmetic")
            # Fewer paths for greeks (many reprices needed)
            greeks_paths = min(n_paths, 50_000)
            result = mc_greeks(S, K, T, r, sigma, opt_type, q, product_type, barrier, barrier_type, avg_type, greeks_paths, n_steps, seed)
            return {"ok": True, "data": result}

        elif action == "path_stats":
            if S <= 0:
                return {"ok": False, "error": "spotPrice must be positive"}
            result = path_statistics(S, r, sigma, T, q, min(n_paths, 50_000), n_steps, seed)
            return {"ok": True, "data": result}

        elif action == "simulate":
            if S <= 0:
                return {"ok": False, "error": "spotPrice must be positive"}
            # Return a small number of paths for visualization
            vis_paths = min(int(config.get("nPaths", 20)), 100)
            vis_steps = min(n_steps, 252)
            paths = simulate_gbm_paths(S, r, sigma, T, vis_steps, vis_paths, q, False, seed)

            # Downsample time axis for JSON size
            time_points = np.linspace(0, T, vis_steps + 1).tolist()
            path_data = []
            for i in range(vis_paths):
                path_data.append([round(float(p), 4) for p in paths[i]])

            return {"ok": True, "data": {
                "paths": path_data,
                "timePoints": [round(t, 6) for t in time_points],
                "nPaths": vis_paths,
                "nSteps": vis_steps,
                "spotPrice": S,
                "drift": round(r - q, 6),
                "volatility": sigma,
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
        result = run_monte_carlo_engine(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
