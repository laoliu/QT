#!/usr/bin/env python3
"""
Delta Hedge Simulation Engine (Delta对冲模拟引擎).

Simulates discrete delta-hedging of an option position to demonstrate
the relationship between hedging frequency, transaction costs, and
hedging P&L — core concepts from the BSM derivation.

Capabilities:
  - Discrete delta-hedging P&L simulation
  - Hedging frequency analysis (daily, hourly, custom)
  - Impact of transaction costs on hedging performance
  - Gamma P&L decomposition (Theta vs Gamma P&L)
  - Multi-path ensemble for hedging error distribution

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


# ── BSM helpers (inline to avoid import issues) ──────────────────────────────

def _d1(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    return (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))

def _bsm_price(S: float, K: float, T: float, r: float, sigma: float,
               option_type: str = "call", q: float = 0.0) -> float:
    if T <= 0:
        return max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
    d1 = _d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == "call":
        return S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    return K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)

def _bsm_delta(S: float, K: float, T: float, r: float, sigma: float,
               option_type: str = "call", q: float = 0.0) -> float:
    if T <= 0 or sigma <= 0:
        if option_type == "call":
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    if option_type == "call":
        return math.exp(-q * T) * norm.cdf(d1)
    return math.exp(-q * T) * (norm.cdf(d1) - 1.0)

def _bsm_gamma(S: float, K: float, T: float, r: float, sigma: float,
               q: float = 0.0) -> float:
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    return math.exp(-q * T) * norm.pdf(d1) / (S * sigma * math.sqrt(T))

def _bsm_theta(S: float, K: float, T: float, r: float, sigma: float,
               option_type: str = "call", q: float = 0.0) -> float:
    if T <= 0 or sigma <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    sqrt_T = math.sqrt(T)
    term1 = -(S * sigma * math.exp(-q * T) * norm.pdf(d1)) / (2 * sqrt_T)
    if option_type == "call":
        return (term1 + q * S * math.exp(-q * T) * norm.cdf(d1) - r * K * math.exp(-r * T) * norm.cdf(d2))
    return (term1 - q * S * math.exp(-q * T) * norm.cdf(-d1) + r * K * math.exp(-r * T) * norm.cdf(-d2))


# ── Delta Hedging Simulator ──────────────────────────────────────────────────

def simulate_delta_hedge(
    S0: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
    q: float = 0.0,
    n_steps: int = 252,
    position: str = "short",       # "short" = wrote the option, "long" = bought it
    hedge_freq: int = 1,            # rehedge every N steps
    transaction_cost: float = 0.0,  # cost per share traded (absolute)
    realized_vol: Optional[float] = None,  # actual vol if different from implied
    seed: Optional[int] = None,
) -> dict:
    """Simulate delta hedging of a single option over one path.

    The classic BSM argument: selling an option and continuously delta-hedging
    should produce a risk-free return. In practice, discrete hedging introduces
    hedging error proportional to gamma × (ΔS)² / 2.

    Args:
        position:     "short" = sold option (collect premium, hedge)
                      "long" = bought option (pay premium, hedge)
        hedge_freq:   Rehedge every N time steps (1 = every step)
        transaction_cost: Cost per share per trade
        realized_vol: If set, simulate with this vol but hedge using sigma (vol mismatch)

    Returns:
        dict with hedge P&L, path data, decomposition
    """
    rng = np.random.default_rng(seed)
    dt = T / n_steps
    sqrt_dt = math.sqrt(dt)
    sim_vol = realized_vol if realized_vol is not None else sigma

    # Sign: short option = +1 (collect premium), long = -1
    sign = 1.0 if position == "short" else -1.0

    # Initial option price (premium collected/paid)
    option_price_0 = _bsm_price(S0, K, T, r, sigma, option_type, q)

    # Cash account: start with +/- premium
    cash = sign * option_price_0

    # Simulate underlying path
    S = S0
    delta_prev = sign * _bsm_delta(S0, K, T, r, sigma, option_type, q)
    shares_held = -delta_prev  # Hedge: short delta to offset

    # Buy initial hedge
    cash -= shares_held * S
    total_cost = abs(shares_held) * transaction_cost

    # Record path
    path_data = [{
        "step": 0,
        "time": 0.0,
        "stockPrice": round(S, 4),
        "delta": round(-sign * delta_prev, 6),
        "sharesHeld": round(shares_held, 6),
        "cash": round(cash, 4),
        "optionValue": round(option_price_0, 6),
    }]

    # Gamma/Theta P&L decomposition
    gamma_pnl_total = 0.0
    theta_pnl_total = 0.0

    for step in range(1, n_steps + 1):
        # GBM step with simulation volatility
        Z = rng.standard_normal()
        S_new = S * math.exp((r - q - 0.5 * sim_vol ** 2) * dt + sim_vol * sqrt_dt * Z)

        time_remaining = T - step * dt
        time_remaining = max(time_remaining, 0.0)

        # Gamma P&L: 0.5 * Gamma * (ΔS)² — profit from gamma when realized move > implied
        gamma_at_S = _bsm_gamma(S, K, time_remaining + dt, r, sigma, q)
        dS = S_new - S
        gamma_pnl = sign * 0.5 * gamma_at_S * dS * dS
        gamma_pnl_total += gamma_pnl

        # Theta P&L: Theta * dt — cost of carry for the option
        theta_at_S = _bsm_theta(S, K, time_remaining + dt, r, sigma, option_type, q)
        theta_pnl = sign * theta_at_S * dt
        theta_pnl_total += theta_pnl

        # Interest on cash
        cash *= math.exp(r * dt)

        # Stock position mark-to-market change is in shares_held * (S_new - S)
        # but tracked via shares_held

        S = S_new

        # Rehedge at specified frequency
        if step % hedge_freq == 0 and time_remaining > 0:
            new_delta = sign * _bsm_delta(S, K, time_remaining, r, sigma, option_type, q)
            new_shares = -new_delta
            trade_qty = new_shares - shares_held

            # Execute trade
            cash -= trade_qty * S
            total_cost += abs(trade_qty) * transaction_cost
            shares_held = new_shares
        elif time_remaining <= 0:
            new_delta = sign * _bsm_delta(S, K, 0.001, r, sigma, option_type, q)

        # Record (sample every N steps to keep output small)
        if step % max(1, n_steps // 50) == 0 or step == n_steps:
            opt_val = _bsm_price(S, K, time_remaining, r, sigma, option_type, q) if time_remaining > 0 else (
                max(S - K, 0) if option_type == "call" else max(K - S, 0)
            )
            path_data.append({
                "step": step,
                "time": round(step * dt, 6),
                "stockPrice": round(S, 4),
                "delta": round(-sign * (new_delta if 'new_delta' in dir() else delta_prev), 6),
                "sharesHeld": round(shares_held, 6),
                "cash": round(cash, 4),
                "optionValue": round(opt_val, 6),
            })

    # Final settlement
    if option_type == "call":
        payoff = max(S - K, 0.0)
    else:
        payoff = max(K - S, 0.0)

    # Close stock position
    cash += shares_held * S
    total_cost += abs(shares_held) * transaction_cost

    # Pay option payoff (short) or receive (long)
    cash -= sign * payoff

    # Final P&L (should be ≈ 0 for perfect hedge)
    final_pnl = cash - total_cost

    return {
        "finalPnl": round(final_pnl, 6),
        "transactionCosts": round(total_cost, 6),
        "pnlBeforeCosts": round(final_pnl + total_cost, 6),
        "optionPremium": round(option_price_0, 6),
        "finalStockPrice": round(S, 4),
        "optionPayoff": round(payoff, 6),
        "nRehedges": n_steps // hedge_freq,
        "position": position,
        "gammaPnl": round(gamma_pnl_total, 6),
        "thetaPnl": round(theta_pnl_total, 6),
        "gammaThetaRatio": round(gamma_pnl_total / theta_pnl_total, 4) if abs(theta_pnl_total) > 1e-10 else 0,
        "impliedVol": sigma,
        "realizedVol": sim_vol,
        "volMismatch": round(sim_vol - sigma, 6) if realized_vol is not None else 0,
        "path": path_data,
    }


def hedge_ensemble(
    S0: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", q: float = 0.0,
    n_steps: int = 252,
    n_simulations: int = 1000,
    hedge_freq: int = 1,
    transaction_cost: float = 0.0,
    realized_vol: Optional[float] = None,
    seed: Optional[int] = None,
) -> dict:
    """Run many delta-hedge simulations and analyze P&L distribution.

    Shows how hedging error shrinks with more frequent hedging
    and how vol mismatch creates systematic P&L.
    """
    t0 = time.time()
    rng = np.random.default_rng(seed)

    pnls = []
    gamma_pnls = []
    theta_pnls = []
    costs = []

    n_simulations = min(n_simulations, 10_000)

    for i in range(n_simulations):
        result = simulate_delta_hedge(
            S0, K, T, r, sigma, option_type, q,
            n_steps, "short", hedge_freq, transaction_cost,
            realized_vol, seed=int(rng.integers(0, 2**31)),
        )
        pnls.append(result["finalPnl"])
        gamma_pnls.append(result["gammaPnl"])
        theta_pnls.append(result["thetaPnl"])
        costs.append(result["transactionCosts"])

    pnls = np.array(pnls)
    gamma_pnls = np.array(gamma_pnls)
    theta_pnls = np.array(theta_pnls)
    costs = np.array(costs)

    elapsed = time.time() - t0

    return {
        "pnl": {
            "mean": round(float(np.mean(pnls)), 6),
            "std": round(float(np.std(pnls)), 6),
            "median": round(float(np.median(pnls)), 6),
            "min": round(float(np.min(pnls)), 6),
            "max": round(float(np.max(pnls)), 6),
            "p5": round(float(np.percentile(pnls, 5)), 6),
            "p95": round(float(np.percentile(pnls, 95)), 6),
        },
        "gammaPnl": {
            "mean": round(float(np.mean(gamma_pnls)), 6),
            "std": round(float(np.std(gamma_pnls)), 6),
        },
        "thetaPnl": {
            "mean": round(float(np.mean(theta_pnls)), 6),
            "std": round(float(np.std(theta_pnls)), 6),
        },
        "transactionCosts": {
            "mean": round(float(np.mean(costs)), 6),
            "total": round(float(np.sum(costs)), 4),
        },
        "nSimulations": n_simulations,
        "hedgeFreq": hedge_freq,
        "impliedVol": sigma,
        "realizedVol": realized_vol if realized_vol is not None else sigma,
        "elapsedMs": round(elapsed * 1000, 1),
    }


def frequency_analysis(
    S0: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", q: float = 0.0,
    n_steps: int = 252,
    n_simulations: int = 500,
    frequencies: Optional[list[int]] = None,
    transaction_cost: float = 0.0,
    seed: Optional[int] = None,
) -> dict:
    """Analyze hedging performance across different rebalancing frequencies.

    Shows the tradeoff: more frequent hedging reduces tracking error
    but increases transaction costs.
    """
    if frequencies is None:
        frequencies = [1, 2, 5, 10, 21, 63]  # daily, 2-day, weekly, biweekly, monthly, quarterly

    results = []
    for freq in frequencies:
        ens = hedge_ensemble(
            S0, K, T, r, sigma, option_type, q,
            n_steps, min(n_simulations, 500), freq,
            transaction_cost, None, seed,
        )
        results.append({
            "hedgeFreq": freq,
            "hedgeLabel": _freq_label(freq),
            "pnlMean": ens["pnl"]["mean"],
            "pnlStd": ens["pnl"]["std"],
            "avgCost": ens["transactionCosts"]["mean"],
        })

    return {
        "analysis": results,
        "optionPremium": round(_bsm_price(S0, K, T, r, sigma, option_type, q), 6),
        "nSimulations": n_simulations,
    }


def _freq_label(freq: int) -> str:
    labels = {1: "每步", 2: "ÿ2步", 5: "每周", 10: "ÿ2周", 21: "每月", 63: "每季"}
    return labels.get(freq, f"ÿ{freq}步")


# ── Main Dispatcher ───────────────────────────────────────────────────────────

def run_delta_hedge_engine(config: dict) -> dict:
    """Main entry point for delta hedging simulations.

    Actions:
        simulate        — Single delta hedge simulation (with path)
        ensemble        — Multi-path P&L distribution
        frequency       — Hedging frequency tradeoff analysis
    """
    action = config.get("action", "simulate")

    try:
        S = float(config.get("spotPrice", 0))
        K = float(config.get("strike", 0))
        r = float(config.get("riskFreeRate", 0.02))
        sigma = float(config.get("volatility", 0.25))
        opt_type = config.get("optionType", "call")
        q = float(config.get("dividendYield", 0.0))
        T = float(config.get("timeToExpiry", 0.25))
        n_steps = int(config.get("nSteps", 252))
        seed = config.get("seed")
        if seed is not None:
            seed = int(seed)

        n_steps = min(n_steps, 1000)

        if S <= 0 or K <= 0:
            return {"ok": False, "error": "spotPrice and strike must be positive"}

        if action == "simulate":
            position = config.get("position", "short")
            hedge_freq = int(config.get("hedgeFreq", 1))
            tx_cost = float(config.get("transactionCost", 0.0))
            realized_vol = config.get("realizedVol")
            if realized_vol is not None:
                realized_vol = float(realized_vol)

            result = simulate_delta_hedge(
                S, K, T, r, sigma, opt_type, q,
                n_steps, position, hedge_freq, tx_cost,
                realized_vol, seed,
            )
            return {"ok": True, "data": result}

        elif action == "ensemble":
            n_sims = min(int(config.get("nSimulations", 1000)), 5000)
            hedge_freq = int(config.get("hedgeFreq", 1))
            tx_cost = float(config.get("transactionCost", 0.0))
            realized_vol = config.get("realizedVol")
            if realized_vol is not None:
                realized_vol = float(realized_vol)

            result = hedge_ensemble(
                S, K, T, r, sigma, opt_type, q,
                n_steps, n_sims, hedge_freq, tx_cost,
                realized_vol, seed,
            )
            return {"ok": True, "data": result}

        elif action == "frequency":
            n_sims = min(int(config.get("nSimulations", 500)), 2000)
            tx_cost = float(config.get("transactionCost", 0.0))
            freqs = config.get("frequencies")
            if freqs and isinstance(freqs, list):
                freqs = [int(f) for f in freqs]

            result = frequency_analysis(
                S, K, T, r, sigma, opt_type, q,
                n_steps, n_sims, freqs, tx_cost, seed,
            )
            return {"ok": True, "data": result}

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
        result = run_delta_hedge_engine(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
