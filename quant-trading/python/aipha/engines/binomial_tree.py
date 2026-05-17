#!/usr/bin/env python3
"""
Binomial Tree Option Pricing Engine (二叉树期权定价引擎).

Implements Cox-Ross-Rubinstein (CRR) binomial tree for pricing
European and American options with exact early-exercise handling.

Capabilities:
  - European option pricing (converges to BSM as steps → ∞)
  - American option pricing (exact early-exercise boundary)
  - Greeks via tree node differences
  - Early exercise boundary visualization
  - Convergence analysis (price vs number of steps)

Input:  JSON via stdin or QUANT_CONFIG env
Output: JSON to stdout

Requirements:
  pip install numpy
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
    from greeks_engine import bsm_price
except ImportError:
    from scipy.stats import norm as _norm
    def bsm_price(
        S: float, K: float, T: float, r: float, sigma: float,
        option_type: str = "call", q: float = 0.0,
    ) -> float:
        if T <= 0:
            return max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
        d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        if option_type == "call":
            return S * math.exp(-q * T) * _norm.cdf(d1) - K * math.exp(-r * T) * _norm.cdf(d2)
        return K * math.exp(-r * T) * _norm.cdf(-d2) - S * math.exp(-q * T) * _norm.cdf(-d1)


# ── CRR Binomial Tree ────────────────────────────────────────────────────────

def binomial_tree_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
    exercise: str = "european",
    q: float = 0.0,
    n_steps: int = 200,
) -> dict:
    """Price an option using the CRR binomial tree.

    CRR Parameters:
        u = exp(σ√Δt)           — up factor
        d = 1/u = exp(-σ√Δt)    — down factor
        p = (exp((r-q)Δt) - d) / (u - d)  — risk-neutral probability

    Args:
        S:         Spot price
        K:         Strike price
        T:         Time to expiry (years)
        r:         Risk-free rate
        sigma:     Volatility
        option_type: "call" or "put"
        exercise:  "european" or "american"
        q:         Dividend yield
        n_steps:   Number of tree steps (higher = more accurate)

    Returns:
        dict with price, Greeks, early exercise info
    """
    t0 = time.time()

    if T <= 0:
        intrinsic = max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
        return {
            "price": round(intrinsic, 6),
            "delta": 1.0 if (option_type == "call" and S > K) else (-1.0 if (option_type == "put" and S < K) else 0.0),
            "gamma": 0.0,
            "theta": 0.0,
            "exercise": exercise,
            "nSteps": 0,
        }

    dt = T / n_steps
    u = math.exp(sigma * math.sqrt(dt))    # up factor
    d = 1.0 / u                             # down factor
    p = (math.exp((r - q) * dt) - d) / (u - d)  # risk-neutral prob
    disc = math.exp(-r * dt)                # discount factor per step

    # Build stock price at terminal nodes
    # S_T[j] = S * u^j * d^(n_steps - j) for j = 0, 1, ..., n_steps
    stock = np.zeros(n_steps + 1)
    for j in range(n_steps + 1):
        stock[j] = S * (u ** (2 * j - n_steps))

    # Option values at terminal nodes
    if option_type == "call":
        option = np.maximum(stock - K, 0.0)
    else:
        option = np.maximum(K - stock, 0.0)

    # Track early exercise boundary (for American)
    early_exercise_nodes = []

    # Backward induction
    # We need values at step 1 and 2 for Greeks computation
    option_at_1 = None  # values at step 1 (2 nodes around center)
    option_at_2 = None  # values at step 2 (3 nodes around center)

    for i in range(n_steps - 1, -1, -1):
        stock_i = np.zeros(i + 1)
        for j in range(i + 1):
            stock_i[j] = S * (u ** (2 * j - i))

        # Continuation value
        option_new = disc * (p * option[1:i + 2] + (1 - p) * option[0:i + 1])

        if exercise == "american":
            if option_type == "call":
                intrinsic = np.maximum(stock_i - K, 0.0)
            else:
                intrinsic = np.maximum(K - stock_i, 0.0)

            # Check for early exercise
            exercise_mask = intrinsic > option_new
            if np.any(exercise_mask):
                # Record the boundary stock prices where early exercise occurs
                ex_prices = stock_i[exercise_mask]
                if option_type == "put":
                    early_exercise_nodes.append({
                        "step": i,
                        "time": round(i * dt, 6),
                        "boundaryPrice": round(float(np.max(ex_prices)), 4),
                    })
                else:
                    early_exercise_nodes.append({
                        "step": i,
                        "time": round(i * dt, 6),
                        "boundaryPrice": round(float(np.min(ex_prices)), 4),
                    })

            option_new = np.maximum(option_new, intrinsic)

        option = option_new

        # Save for Greeks
        if i == 2:
            option_at_2 = option.copy()
            stock_at_2 = stock_i.copy()
        if i == 1:
            option_at_1 = option.copy()
            stock_at_1 = stock_i.copy()

    price = float(option[0])

    # Greeks from tree
    delta = 0.0
    gamma = 0.0
    theta = 0.0

    if option_at_1 is not None and len(option_at_1) >= 2:
        # Delta = (f_u - f_d) / (S_u - S_d) at step 1
        S_u = S * u
        S_d = S * d
        f_u = float(option_at_1[1])  # up state
        f_d = float(option_at_1[0])  # down state
        delta = (f_u - f_d) / (S_u - S_d)

    if option_at_2 is not None and len(option_at_2) >= 3:
        # Gamma from step 2 nodes
        S_uu = S * u * u
        S_dd = S * d * d
        f_uu = float(option_at_2[2])
        f_ud = float(option_at_2[1])
        f_dd = float(option_at_2[0])

        delta_up = (f_uu - f_ud) / (S_uu - S)
        delta_down = (f_ud - f_dd) / (S - S_dd)
        gamma = (delta_up - delta_down) / (0.5 * (S_uu - S_dd))

        # Theta: (f_ud - f_00) / (2Δt)  (daily)
        theta = (f_ud - price) / (2 * dt) / 365.0

    elapsed = time.time() - t0

    # BSM benchmark
    bsm_ref = bsm_price(S, K, T, r, sigma, option_type, q)

    result: dict[str, Any] = {
        "price": round(price, 6),
        "delta": round(delta, 6),
        "gamma": round(gamma, 6),
        "theta": round(theta, 6),
        "exercise": exercise,
        "nSteps": n_steps,
        "bsmBenchmark": round(bsm_ref, 6),
        "priceDiff": round(price - bsm_ref, 6),
        "treeParams": {
            "u": round(u, 8),
            "d": round(d, 8),
            "p": round(p, 8),
            "dt": round(dt, 8),
        },
        "elapsedMs": round(elapsed * 1000, 1),
    }

    if exercise == "american":
        early_premium = price - bsm_ref
        result["earlyExercisePremium"] = round(early_premium, 6)
        # Keep only a sample of boundary points
        if len(early_exercise_nodes) > 50:
            step_size = len(early_exercise_nodes) // 50
            early_exercise_nodes = early_exercise_nodes[::step_size]
        result["earlyExerciseBoundary"] = early_exercise_nodes

    return result


def convergence_analysis(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", exercise: str = "european",
    q: float = 0.0,
    step_range: Optional[list[int]] = None,
) -> dict:
    """Analyze how tree price converges as n_steps increases.

    Shows oscillatory convergence characteristic of binomial trees.
    """
    if step_range is None:
        step_range = [10, 20, 50, 100, 200, 500, 1000]

    bsm_ref = bsm_price(S, K, T, r, sigma, option_type, q)

    results = []
    for n in step_range:
        n = min(n, 2000)  # cap
        tree = binomial_tree_price(S, K, T, r, sigma, option_type, exercise, q, n)
        results.append({
            "nSteps": n,
            "price": tree["price"],
            "error": round(tree["price"] - bsm_ref, 8),
            "elapsedMs": tree["elapsedMs"],
        })

    return {
        "bsmBenchmark": round(bsm_ref, 6),
        "convergence": results,
        "exercise": exercise,
    }


# ── Main Dispatcher ───────────────────────────────────────────────────────────

def run_binomial_tree_engine(config: dict) -> dict:
    """Main entry point for binomial tree pricing.

    Actions:
        price         — Price option using CRR binomial tree
        convergence   — Convergence analysis (price vs steps)
        compare       — Compare European vs American pricing
    """
    action = config.get("action", "price")

    try:
        S = float(config.get("spotPrice", 0))
        K = float(config.get("strike", 0))
        r = float(config.get("riskFreeRate", 0.02))
        sigma = float(config.get("volatility", 0.25))
        opt_type = config.get("optionType", "call")
        q = float(config.get("dividendYield", 0.0))
        T = float(config.get("timeToExpiry", 0.25))
        n_steps = int(config.get("nSteps", 200))
        exercise = config.get("exerciseType", "european")

        n_steps = min(n_steps, 2000)

        if action == "price":
            if S <= 0 or K <= 0:
                return {"ok": False, "error": "spotPrice and strike must be positive"}
            result = binomial_tree_price(S, K, T, r, sigma, opt_type, exercise, q, n_steps)
            return {"ok": True, "data": result}

        elif action == "convergence":
            if S <= 0 or K <= 0:
                return {"ok": False, "error": "spotPrice and strike must be positive"}
            steps = config.get("stepRange")
            if steps and isinstance(steps, list):
                steps = [int(s) for s in steps]
            result = convergence_analysis(S, K, T, r, sigma, opt_type, exercise, q, steps)
            return {"ok": True, "data": result}

        elif action == "compare":
            if S <= 0 or K <= 0:
                return {"ok": False, "error": "spotPrice and strike must be positive"}
            european = binomial_tree_price(S, K, T, r, sigma, opt_type, "european", q, n_steps)
            american = binomial_tree_price(S, K, T, r, sigma, opt_type, "american", q, n_steps)
            bsm_ref = bsm_price(S, K, T, r, sigma, opt_type, q)

            return {"ok": True, "data": {
                "european": european,
                "american": american,
                "bsmBenchmark": round(bsm_ref, 6),
                "earlyExercisePremium": round(american["price"] - european["price"], 6),
                "summary": (
                    f"European: {european['price']:.6f}, "
                    f"American: {american['price']:.6f}, "
                    f"Early exercise premium: {american['price'] - european['price']:.6f}"
                ),
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
        result = run_binomial_tree_engine(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
