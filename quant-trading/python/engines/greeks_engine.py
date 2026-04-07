#!/usr/bin/env python3
"""
Greeks Calculation Engine (希腊值计算引擎).

Implements Black-Scholes-Merton (BSM) model for European options
and Barone-Adesi-Whaley (BAW) approximation for American options.

Capabilities:
  - Full Greeks: Delta, Gamma, Theta, Vega, Rho
  - Implied Volatility (IV) via Newton-Raphson
  - IV Surface construction
  - IV Smile for a specific expiry

Input: JSON via stdin
Output: JSON to stdout

Requirements:
  pip install numpy scipy
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime
from typing import Any, Optional

import numpy as np

try:
    from scipy.stats import norm
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    # Fallback: minimal norm CDF/PDF using math.erf
    class _NormFallback:
        @staticmethod
        def cdf(x):
            return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
        @staticmethod
        def pdf(x):
            return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
    norm = _NormFallback()  # type: ignore[assignment]


# ── BSM Core Functions ────────────────────────────────────────────────────────

def _d1(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Calculate d1 in BSM formula."""
    if T <= 0 or sigma <= 0:
        return 0.0
    return (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))


def _d2(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """Calculate d2 in BSM formula."""
    return _d1(S, K, T, r, sigma, q) - sigma * math.sqrt(T)


def bsm_price(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", q: float = 0.0,
) -> float:
    """Black-Scholes-Merton option price.

    Args:
        S:     Underlying price (标的价格)
        K:     Strike price (行权价)
        T:     Time to expiry in years (到期时间，年)
        r:     Risk-free rate (无风险利率)
        sigma: Volatility (波动率)
        option_type: "call" or "put"
        q:     Continuous dividend yield (股息率)
    """
    if T <= 0:
        # At expiry: intrinsic value
        if option_type == "call":
            return max(S - K, 0.0)
        return max(K - S, 0.0)

    d1 = _d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)

    if option_type == "call":
        return S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    else:
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)


# ── Greeks Calculation ────────────────────────────────────────────────────────

def calculate_delta(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", q: float = 0.0,
) -> float:
    """Delta: rate of change of option price with respect to underlying price."""
    if T <= 0 or sigma <= 0:
        if option_type == "call":
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0

    d1 = _d1(S, K, T, r, sigma, q)
    if option_type == "call":
        return math.exp(-q * T) * norm.cdf(d1)
    return math.exp(-q * T) * (norm.cdf(d1) - 1)


def calculate_gamma(
    S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0,
) -> float:
    """Gamma: rate of change of delta. Same for calls and puts."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0

    d1 = _d1(S, K, T, r, sigma, q)
    return math.exp(-q * T) * norm.pdf(d1) / (S * sigma * math.sqrt(T))


def calculate_theta(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", q: float = 0.0,
) -> float:
    """Theta: time decay per day (negative for long positions).

    Returns daily theta (annualized / 365).
    """
    if T <= 0 or sigma <= 0:
        return 0.0

    d1 = _d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    sqrt_T = math.sqrt(T)

    # Common term
    term1 = -(S * sigma * math.exp(-q * T) * norm.pdf(d1)) / (2 * sqrt_T)

    if option_type == "call":
        term2 = q * S * math.exp(-q * T) * norm.cdf(d1)
        term3 = -r * K * math.exp(-r * T) * norm.cdf(d2)
    else:
        term2 = -q * S * math.exp(-q * T) * norm.cdf(-d1)
        term3 = r * K * math.exp(-r * T) * norm.cdf(-d2)

    # Return daily theta
    return (term1 + term2 + term3) / 365.0


def calculate_vega(
    S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0,
) -> float:
    """Vega: sensitivity to 1% change in volatility.

    Returns vega per 1% vol change (not per 1 vol point).
    """
    if T <= 0 or sigma <= 0:
        return 0.0

    d1 = _d1(S, K, T, r, sigma, q)
    return S * math.exp(-q * T) * norm.pdf(d1) * math.sqrt(T) / 100.0


def calculate_rho(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", q: float = 0.0,
) -> float:
    """Rho: sensitivity to 1% change in interest rate."""
    if T <= 0 or sigma <= 0:
        return 0.0

    d2 = _d2(S, K, T, r, sigma, q)

    if option_type == "call":
        return K * T * math.exp(-r * T) * norm.cdf(d2) / 100.0
    return -K * T * math.exp(-r * T) * norm.cdf(-d2) / 100.0


def calculate_all_greeks(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", q: float = 0.0,
) -> dict:
    """Calculate all Greeks at once (efficient: reuses d1/d2)."""
    result = {
        "delta": 0.0, "gamma": 0.0, "theta": 0.0,
        "vega": 0.0, "rho": 0.0, "impliedVol": sigma,
        "theoreticalPrice": 0.0,
    }

    if T <= 0 or sigma <= 0:
        # At or past expiry
        intrinsic = max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
        result["theoreticalPrice"] = intrinsic
        result["delta"] = (1.0 if S > K else 0.0) if option_type == "call" else (-1.0 if S < K else 0.0)
        return result

    d1 = _d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    sqrt_T = math.sqrt(T)
    exp_qT = math.exp(-q * T)
    exp_rT = math.exp(-r * T)
    nd1 = norm.cdf(d1)
    nd2 = norm.cdf(d2)
    pd1 = norm.pdf(d1)

    # Price
    if option_type == "call":
        price = S * exp_qT * nd1 - K * exp_rT * nd2
    else:
        price = K * exp_rT * norm.cdf(-d2) - S * exp_qT * norm.cdf(-d1)

    # Delta
    if option_type == "call":
        delta = exp_qT * nd1
    else:
        delta = exp_qT * (nd1 - 1)

    # Gamma (same for call/put)
    gamma = exp_qT * pd1 / (S * sigma * sqrt_T) if S > 0 else 0.0

    # Theta (daily)
    term1 = -(S * sigma * exp_qT * pd1) / (2 * sqrt_T)
    if option_type == "call":
        theta = (term1 + q * S * exp_qT * nd1 - r * K * exp_rT * nd2) / 365.0
    else:
        theta = (term1 - q * S * exp_qT * norm.cdf(-d1) + r * K * exp_rT * norm.cdf(-d2)) / 365.0

    # Vega (per 1% vol change)
    vega = S * exp_qT * pd1 * sqrt_T / 100.0

    # Rho (per 1% rate change)
    if option_type == "call":
        rho = K * T * exp_rT * nd2 / 100.0
    else:
        rho = -K * T * exp_rT * norm.cdf(-d2) / 100.0

    result["theoreticalPrice"] = round(price, 6)
    result["delta"] = round(delta, 6)
    result["gamma"] = round(gamma, 6)
    result["theta"] = round(theta, 6)
    result["vega"] = round(vega, 6)
    result["rho"] = round(rho, 6)
    result["impliedVol"] = round(sigma, 6)

    return result


# ── Implied Volatility ────────────────────────────────────────────────────────

def calculate_implied_vol(
    market_price: float, S: float, K: float, T: float, r: float,
    option_type: str = "call", q: float = 0.0,
    initial_guess: float = 0.3, max_iterations: int = 100,
    tolerance: float = 1e-6,
) -> Optional[float]:
    """Calculate implied volatility using Newton-Raphson method.

    Returns IV (annualized) or None if not converged.
    """
    if T <= 0 or market_price <= 0 or S <= 0 or K <= 0:
        return None

    # Check intrinsic value boundary
    intrinsic = max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
    if market_price < intrinsic - 0.01:
        return None  # Below intrinsic, no valid IV

    sigma = initial_guess

    for _ in range(max_iterations):
        price = bsm_price(S, K, T, r, sigma, option_type, q)
        diff = price - market_price

        if abs(diff) < tolerance:
            return sigma

        # Vega for Newton step (raw vega, not per 1%)
        d1 = _d1(S, K, T, r, sigma, q)
        vega_raw = S * math.exp(-q * T) * norm.pdf(d1) * math.sqrt(T)

        if vega_raw < 1e-10:
            # Vega too small, switch to bisection
            break

        sigma = sigma - diff / vega_raw

        # Clamp to reasonable range
        if sigma < 0.001:
            sigma = 0.001
        if sigma > 10.0:
            sigma = 10.0

    # Fallback: bisection method
    return _bisection_iv(market_price, S, K, T, r, option_type, q)


def _bisection_iv(
    market_price: float, S: float, K: float, T: float, r: float,
    option_type: str, q: float,
    lo: float = 0.001, hi: float = 5.0, max_iterations: int = 100,
    tolerance: float = 1e-6,
) -> Optional[float]:
    """Bisection fallback for IV calculation."""
    for _ in range(max_iterations):
        mid = (lo + hi) / 2.0
        price = bsm_price(S, K, T, r, mid, option_type, q)
        diff = price - market_price

        if abs(diff) < tolerance:
            return mid

        if diff > 0:
            hi = mid
        else:
            lo = mid

        if hi - lo < tolerance:
            return mid

    return None


# ── BAW Approximation (American Options) ──────────────────────────────────────

def baw_price(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", q: float = 0.0,
) -> float:
    """Barone-Adesi-Whaley approximation for American option pricing.

    Adds early exercise premium to European BSM price.
    For puts with no dividends or calls with dividends,
    the American price may exceed the European price.
    """
    european_price = bsm_price(S, K, T, r, sigma, option_type, q)

    if T <= 0:
        return european_price

    # For American calls on non-dividend stocks, American = European
    if option_type == "call" and q <= 0:
        return european_price

    # BAW critical price and early exercise premium
    try:
        M = 2 * r / (sigma ** 2)
        N_val = 2 * (r - q) / (sigma ** 2)
        K_baw = 1 - math.exp(-r * T)

        # Quadratic equation coefficients
        q2_coeff = (-(N_val - 1) + math.sqrt((N_val - 1) ** 2 + 4 * M / K_baw)) / 2

        if option_type == "call":
            if q2_coeff <= 0:
                return european_price

            # Find critical stock price S* iteratively
            S_star = _find_critical_price_call(K, T, r, sigma, q, q2_coeff)
            if S >= S_star:
                return S - K  # Exercise immediately
            A2 = (S_star / q2_coeff) * (1 - math.exp(-q * T) * norm.cdf(_d1(S_star, K, T, r, sigma, q)))
            return european_price + A2 * (S / S_star) ** q2_coeff

        else:  # put
            q1_coeff = (-(N_val - 1) - math.sqrt((N_val - 1) ** 2 + 4 * M / K_baw)) / 2
            if q1_coeff >= 0:
                return european_price

            S_star = _find_critical_price_put(K, T, r, sigma, q, q1_coeff)
            if S <= S_star:
                return K - S  # Exercise immediately
            A1 = -(S_star / q1_coeff) * (1 - math.exp(-q * T) * norm.cdf(-_d1(S_star, K, T, r, sigma, q)))
            return european_price + A1 * (S / S_star) ** q1_coeff

    except (ValueError, ZeroDivisionError, OverflowError):
        return european_price


def _find_critical_price_call(
    K: float, T: float, r: float, sigma: float, q: float, q2: float,
    tol: float = 1e-5, max_iter: int = 50,
) -> float:
    """Find critical stock price S* for American call BAW."""
    S_star = K  # initial guess
    for _ in range(max_iter):
        d1 = _d1(S_star, K, T, r, sigma, q)
        bsm_val = bsm_price(S_star, K, T, r, sigma, "call", q)
        lhs = S_star - K
        rhs = bsm_val + (1 - math.exp(-q * T) * norm.cdf(d1)) * S_star / q2
        diff = lhs - rhs
        if abs(diff) < tol:
            break
        # Newton update
        deriv = 1 - (1 / q2) * (1 - math.exp(-q * T) * norm.cdf(d1))
        if abs(deriv) < 1e-10:
            break
        S_star = S_star - diff / deriv
        if S_star < K * 0.5:
            S_star = K * 0.5
        if S_star > K * 3.0:
            S_star = K * 3.0
    return S_star


def _find_critical_price_put(
    K: float, T: float, r: float, sigma: float, q: float, q1: float,
    tol: float = 1e-5, max_iter: int = 50,
) -> float:
    """Find critical stock price S* for American put BAW."""
    S_star = K  # initial guess
    for _ in range(max_iter):
        d1 = _d1(S_star, K, T, r, sigma, q)
        bsm_val = bsm_price(S_star, K, T, r, sigma, "put", q)
        lhs = K - S_star
        rhs = bsm_val - (1 - math.exp(-q * T) * norm.cdf(-d1)) * S_star / q1
        diff = lhs - rhs
        if abs(diff) < tol:
            break
        deriv = -1 + (1 / q1) * (1 - math.exp(-q * T) * norm.cdf(-d1))
        if abs(deriv) < 1e-10:
            break
        S_star = S_star - diff / deriv
        if S_star < K * 0.01:
            S_star = K * 0.01
        if S_star > K * 1.5:
            S_star = K * 1.5
    return S_star


# ── Second-Order Greeks ──────────────────────────────────────────────────────

def calculate_charm(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", q: float = 0.0,
) -> float:
    """Charm (Delta decay): dDelta/dT — measures how delta changes as time passes.

    Useful for traders who delta-hedge and want to know how their hedge
    drifts overnight without any price change.
    Returns daily charm (annualized / 365).
    """
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0

    d1 = _d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    sqrt_T = math.sqrt(T)
    exp_qT = math.exp(-q * T)
    pd1 = norm.pdf(d1)

    # Charm = -exp(-qT) * [pdf(d1) * (2*(r-q)*T - d2*sigma*sqrt(T)) / (2*T*sigma*sqrt(T))]
    #         + q * exp(-qT) * N(d1)  [for call]
    common = exp_qT * pd1 * (2.0 * (r - q) * T - d2 * sigma * sqrt_T) / (2.0 * T * sigma * sqrt_T)

    if option_type == "call":
        charm_annual = -q * exp_qT * norm.cdf(d1) - common
    else:
        charm_annual = q * exp_qT * norm.cdf(-d1) - common

    return charm_annual / 365.0


def calculate_vanna(
    S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0,
) -> float:
    """Vanna: dDelta/dSigma = dVega/dSpot — cross-sensitivity of delta to vol.

    Same for calls and puts. Tells traders how their delta exposure shifts
    when implied volatility moves.
    Returns vanna per 1% vol change.
    """
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0

    d1 = _d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    exp_qT = math.exp(-q * T)
    pd1 = norm.pdf(d1)

    # Vanna = -exp(-qT) * pdf(d1) * d2 / sigma
    # Normalize per 1% vol change (divide by 100)
    return -exp_qT * pd1 * d2 / sigma / 100.0


def calculate_vomma(
    S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0,
) -> float:
    """Vomma (Volga): dVega/dSigma — second derivative of price w.r.t. volatility.

    Same for calls and puts. Critical for vol-of-vol risk and for
    understanding the convexity of a position's vol exposure.
    Returns vomma per 1% vol change squared.
    """
    if T <= 0 or sigma <= 0 or S <= 0:
        return 0.0

    d1 = _d1(S, K, T, r, sigma, q)
    d2 = d1 - sigma * math.sqrt(T)
    sqrt_T = math.sqrt(T)
    exp_qT = math.exp(-q * T)
    pd1 = norm.pdf(d1)

    # Vomma = S * exp(-qT) * pdf(d1) * sqrt(T) * d1 * d2 / sigma
    # Normalize per (1% vol)^2
    return S * exp_qT * pd1 * sqrt_T * d1 * d2 / sigma / 10000.0


# ── Greeks Sensitivity (What-If) ─────────────────────────────────────────────

def greeks_what_if(
    S: float, K: float, T: float, r: float, sigma: float,
    option_type: str = "call", q: float = 0.0,
    spot_bumps_pct: Optional[list] = None,
    vol_bumps: Optional[list] = None,
    time_bumps_days: Optional[list] = None,
) -> list:
    """Compute Greeks across a grid of spot/vol/time perturbations.

    Returns a list of scenarios, each containing the full Greeks snapshot
    plus the scenario parameters. This is essential for traders to understand
    how their option positions behave under various market conditions.
    """
    if spot_bumps_pct is None:
        spot_bumps_pct = [-10, -5, -2, -1, 0, 1, 2, 5, 10]
    if vol_bumps is None:
        vol_bumps = [0]
    if time_bumps_days is None:
        time_bumps_days = [0]

    scenarios = []
    for spot_pct in spot_bumps_pct:
        for vol_bump in vol_bumps:
            for time_bump in time_bumps_days:
                bumped_S = S * (1.0 + spot_pct / 100.0)
                bumped_sigma = max(sigma + vol_bump, 0.001)
                bumped_T = max(T - time_bump / 365.0, 0.0)

                if bumped_S <= 0:
                    continue

                greeks = calculate_all_greeks(bumped_S, K, bumped_T, r, bumped_sigma, option_type, q)
                # Add second-order Greeks
                greeks["charm"] = round(calculate_charm(bumped_S, K, bumped_T, r, bumped_sigma, option_type, q), 6)
                greeks["vanna"] = round(calculate_vanna(bumped_S, K, bumped_T, r, bumped_sigma, q), 6)
                greeks["vomma"] = round(calculate_vomma(bumped_S, K, bumped_T, r, bumped_sigma, q), 6)

                scenarios.append({
                    "spotBumpPct": spot_pct,
                    "volBump": vol_bump,
                    "timeBumpDays": time_bump,
                    "spotPrice": round(bumped_S, 4),
                    "volatility": round(bumped_sigma, 4),
                    "timeToExpiry": round(bumped_T, 6),
                    **greeks,
                })
    return scenarios


# ── P&L Attribution ──────────────────────────────────────────────────────────

def pnl_attribution(
    S_old: float, S_new: float,
    sigma_old: float, sigma_new: float,
    T_old: float, T_new: float,
    K: float, r: float,
    option_type: str = "call", q: float = 0.0,
    quantity: int = 1, multiplier: float = 10000.0,
) -> dict:
    """Decompose option P&L into Greeks components.

    Breaks down the actual price change into contributions from:
      - Delta effect:  delta * dS
      - Gamma effect:  0.5 * gamma * dS^2
      - Theta effect:  theta * dT (in days)
      - Vega effect:   vega * dSigma (per 1% vol)
      - Residual:      actual change minus sum of above

    This is the standard first+second order Taylor expansion used by
    professional options desks for P&L explain.
    """
    dS = S_new - S_old
    d_sigma = sigma_new - sigma_old
    dT_days = (T_old - T_new) * 365.0  # positive = time passed

    # Use old state Greeks for attribution
    greeks = calculate_all_greeks(S_old, K, T_old, r, sigma_old, option_type, q)
    price_old = greeks["theoreticalPrice"]

    # New theoretical price
    greeks_new = calculate_all_greeks(S_new, K, T_new, r, sigma_new, option_type, q)
    price_new = greeks_new["theoreticalPrice"]
    actual_change = price_new - price_old

    # Delta effect
    delta_effect = greeks["delta"] * dS

    # Gamma effect (second-order spot)
    gamma_effect = 0.5 * greeks["gamma"] * dS * dS

    # Theta effect (vega is per 1% so theta is per day)
    theta_effect = greeks["theta"] * dT_days

    # Vega effect (vega is per 1% vol change)
    vega_effect = greeks["vega"] * d_sigma * 100.0

    explained = delta_effect + gamma_effect + theta_effect + vega_effect
    residual = actual_change - explained

    return {
        "priceOld": round(price_old, 6),
        "priceNew": round(price_new, 6),
        "actualChange": round(actual_change, 6),
        "deltaEffect": round(delta_effect, 6),
        "gammaEffect": round(gamma_effect, 6),
        "thetaEffect": round(theta_effect, 6),
        "vegaEffect": round(vega_effect, 6),
        "explained": round(explained, 6),
        "residual": round(residual, 6),
        "pnlPerContract": round(actual_change * multiplier, 2),
        "totalPnl": round(actual_change * multiplier * quantity, 2),
        "decomposition": {
            "delta": round(delta_effect * multiplier * quantity, 2),
            "gamma": round(gamma_effect * multiplier * quantity, 2),
            "theta": round(theta_effect * multiplier * quantity, 2),
            "vega": round(vega_effect * multiplier * quantity, 2),
            "residual": round(residual * multiplier * quantity, 2),
        },
    }


# ── Margin Estimation ────────────────────────────────────────────────────────

def estimate_option_margin(
    S: float, K: float, T: float, sigma: float,
    option_type: str = "call",
    position_side: str = "short",
    quantity: int = 1,
    multiplier: float = 10000.0,
    settlement_price: Optional[float] = None,
) -> dict:
    """Estimate margin for option positions.

    Uses the simplified Chinese exchange margin formula for ETF options:
      Short call margin = (premium + max(X% * underlying - OTM, Y% * underlying)) * multiplier
      Short put margin  = min(premium + max(X% * underlying - OTM, Y% * strike), strike) * multiplier

    Where X% = 12%, Y% = 7% are typical exchange parameters.
    Long positions only require the premium paid (no additional margin).
    """
    if position_side == "long":
        premium = bsm_price(S, K, T, 0.02, sigma, option_type)
        return {
            "marginPerContract": round(premium * multiplier, 2),
            "totalMargin": round(premium * multiplier * quantity, 2),
            "marginType": "premium",
            "note": "买方只需付权利金，无额外保证金",
        }

    premium = settlement_price if settlement_price is not None else bsm_price(S, K, T, 0.02, sigma, option_type)

    # Exchange margin parameters (typical for SSE ETF options)
    margin_rate_1 = 0.12  # 12% of underlying
    margin_rate_2 = 0.07  # 7% of underlying (or strike for puts)

    if option_type == "call":
        otm_amount = max(K - S, 0.0)
        margin_per_unit = premium + max(margin_rate_1 * S - otm_amount, margin_rate_2 * S)
    else:
        otm_amount = max(S - K, 0.0)
        margin_per_unit = min(
            premium + max(margin_rate_1 * S - otm_amount, margin_rate_2 * K),
            K,
        )

    margin_per_contract = margin_per_unit * multiplier
    total_margin = margin_per_contract * quantity

    return {
        "marginPerContract": round(margin_per_contract, 2),
        "totalMargin": round(total_margin, 2),
        "marginType": "short_option",
        "premiumComponent": round(premium * multiplier, 2),
        "marginRate1": margin_rate_1,
        "marginRate2": margin_rate_2,
        "note": f"{'卖出看涨' if option_type == 'call' else '卖出看跌'}期权保证金估算（交易所参数可能略有差异）",
    }


# ── Utility: Time to Expiry ───────────────────────────────────────────────────

def days_to_expiry(expiry_str: str) -> int:
    """Calculate calendar days to expiry from today."""
    try:
        exp = datetime.strptime(expiry_str, "%Y-%m-%d")
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return max((exp - today).days, 0)
    except (ValueError, TypeError):
        return 0


def years_to_expiry(expiry_str: str) -> float:
    """Calculate time to expiry in years (calendar days / 365)."""
    return days_to_expiry(expiry_str) / 365.0


# ── Main Dispatcher ───────────────────────────────────────────────────────────

def run_greeks_engine(config: dict) -> dict:
    """Main entry point for Greeks calculations.

    Actions:
        greeks       — calculate all Greeks for given parameters
        implied_vol  — calculate implied volatility from market price
        price        — calculate theoretical price
        iv_surface   — build IV surface from option chain data
        iv_smile     — get volatility smile for specific expiry
        what_if      — Greeks sensitivity across spot/vol/time grid
        pnl_explain  — P&L attribution via Greeks decomposition
        margin       — estimate margin requirement for option position
    """
    action = config.get("action", "greeks")

    try:
        if action == "greeks":
            S = float(config.get("spotPrice", 0))
            K = float(config.get("strike", 0))
            r = float(config.get("riskFreeRate", 0.02))
            sigma = float(config.get("volatility", 0.25))
            opt_type = config.get("optionType", "call")
            q = float(config.get("dividendYield", 0.0))
            exercise = config.get("exerciseType", "european")

            # Time to expiry
            expiry = config.get("expiry", "")
            if expiry:
                T = years_to_expiry(expiry)
            else:
                T = float(config.get("timeToExpiry", 0.25))

            if S <= 0 or K <= 0:
                return {"ok": False, "error": "spotPrice and strike must be positive"}

            greeks = calculate_all_greeks(S, K, T, r, sigma, opt_type, q)

            # For American options, also calculate BAW price
            if exercise == "american":
                greeks["americanPrice"] = round(baw_price(S, K, T, r, sigma, opt_type, q), 6)

            greeks["dte"] = days_to_expiry(expiry) if expiry else int(T * 365)
            greeks["intrinsicValue"] = round(
                max(S - K, 0) if opt_type == "call" else max(K - S, 0), 6
            )
            greeks["timeValue"] = round(greeks["theoreticalPrice"] - greeks["intrinsicValue"], 6)
            greeks["moneyness"] = round(S / K, 4) if K > 0 else 0

            # Second-order Greeks
            greeks["charm"] = round(calculate_charm(S, K, T, r, sigma, opt_type, q), 6)
            greeks["vanna"] = round(calculate_vanna(S, K, T, r, sigma, q), 6)
            greeks["vomma"] = round(calculate_vomma(S, K, T, r, sigma, q), 6)

            greeks["calculatedAt"] = int(datetime.now().timestamp() * 1000)

            return {"ok": True, "data": greeks}

        elif action == "implied_vol":
            market_price = float(config.get("marketPrice", 0))
            S = float(config.get("spotPrice", 0))
            K = float(config.get("strike", 0))
            r = float(config.get("riskFreeRate", 0.02))
            opt_type = config.get("optionType", "call")
            q = float(config.get("dividendYield", 0.0))

            expiry = config.get("expiry", "")
            if expiry:
                T = years_to_expiry(expiry)
            else:
                T = float(config.get("timeToExpiry", 0.25))

            if market_price <= 0 or S <= 0 or K <= 0:
                return {"ok": False, "error": "marketPrice, spotPrice, strike must be positive"}

            iv = calculate_implied_vol(market_price, S, K, T, r, opt_type, q)
            if iv is None:
                return {"ok": False, "error": "IV calculation did not converge"}

            return {"ok": True, "data": {
                "impliedVol": round(iv, 6),
                "annualizedIV": round(iv * 100, 2),
                "marketPrice": market_price,
                "bsmPrice": round(bsm_price(S, K, T, r, iv, opt_type, q), 6),
                "calculatedAt": int(datetime.now().timestamp() * 1000),
            }}

        elif action == "price":
            S = float(config.get("spotPrice", 0))
            K = float(config.get("strike", 0))
            r = float(config.get("riskFreeRate", 0.02))
            sigma = float(config.get("volatility", 0.25))
            opt_type = config.get("optionType", "call")
            q = float(config.get("dividendYield", 0.0))
            exercise = config.get("exerciseType", "european")

            expiry = config.get("expiry", "")
            T = years_to_expiry(expiry) if expiry else float(config.get("timeToExpiry", 0.25))

            european = bsm_price(S, K, T, r, sigma, opt_type, q)
            result_data = {"europeanPrice": round(european, 6)}
            if exercise == "american":
                result_data["americanPrice"] = round(baw_price(S, K, T, r, sigma, opt_type, q), 6)

            return {"ok": True, "data": result_data}

        elif action == "iv_surface":
            # Expects chain data with calls and puts arrays
            chain_data = config.get("chainData", {})
            S = float(config.get("spotPrice", chain_data.get("underlyingPrice", 0)))
            r = float(config.get("riskFreeRate", 0.02))
            q = float(config.get("dividendYield", 0.0))

            surface = []
            for quote in chain_data.get("calls", []) + chain_data.get("puts", []):
                price = float(quote.get("price", 0))
                strike = float(quote.get("strike", 0))
                expiry_str = quote.get("expiry", "")
                opt_type = quote.get("optionType", "call")

                if price <= 0 or strike <= 0 or not expiry_str:
                    continue

                T = years_to_expiry(expiry_str)
                if T <= 0:
                    continue

                # Try to get IV — use market value if available, else compute
                iv_market = float(quote.get("impliedVol", 0))
                if iv_market <= 0:
                    iv_market = calculate_implied_vol(price, S, strike, T, r, opt_type, q)
                    if iv_market is None:
                        continue

                greeks = calculate_all_greeks(S, strike, T, r, iv_market, opt_type, q)
                moneyness = strike / S if S > 0 else 0

                surface.append({
                    "expiry": expiry_str,
                    "strike": strike,
                    "iv": round(iv_market, 6),
                    "delta": greeks["delta"],
                    "moneyness": round(moneyness, 4),
                    "optionType": opt_type,
                    "dte": days_to_expiry(expiry_str),
                })

            return {"ok": True, "data": {
                "underlying": chain_data.get("underlying", ""),
                "underlyingPrice": S,
                "surface": surface,
                "calculatedAt": int(datetime.now().timestamp() * 1000),
            }}

        elif action == "iv_smile":
            # IV smile for a specific expiry
            chain_data = config.get("chainData", {})
            target_expiry = config.get("expiry", "")
            S = float(config.get("spotPrice", chain_data.get("underlyingPrice", 0)))
            r = float(config.get("riskFreeRate", 0.02))
            q = float(config.get("dividendYield", 0.0))

            if not target_expiry:
                # Use nearest expiry
                expiries = chain_data.get("expiryDates", [])
                if expiries:
                    target_expiry = expiries[0]
                else:
                    return {"ok": False, "error": "No expiry date specified or available"}

            smile_data = []
            T = years_to_expiry(target_expiry)
            if T <= 0:
                return {"ok": False, "error": f"Expiry {target_expiry} is in the past"}

            for quote in chain_data.get("calls", []) + chain_data.get("puts", []):
                if quote.get("expiry") != target_expiry:
                    continue
                price = float(quote.get("price", 0))
                strike = float(quote.get("strike", 0))
                if price <= 0 or strike <= 0:
                    continue

                opt_type = quote.get("optionType", "call")
                iv = float(quote.get("impliedVol", 0))
                if iv <= 0:
                    iv = calculate_implied_vol(price, S, strike, T, r, opt_type, q)
                    if iv is None:
                        continue

                smile_data.append({
                    "strike": strike,
                    "iv": round(iv, 6),
                    "optionType": opt_type,
                    "moneyness": round(strike / S, 4) if S > 0 else 0,
                })

            smile_data.sort(key=lambda x: x["strike"])

            return {"ok": True, "data": {
                "expiry": target_expiry,
                "dte": days_to_expiry(target_expiry),
                "underlyingPrice": S,
                "smile": smile_data,
                "calculatedAt": int(datetime.now().timestamp() * 1000),
            }}

        elif action == "what_if":
            S = float(config.get("spotPrice", 0))
            K = float(config.get("strike", 0))
            r = float(config.get("riskFreeRate", 0.02))
            sigma = float(config.get("volatility", 0.25))
            opt_type = config.get("optionType", "call")
            q = float(config.get("dividendYield", 0.0))

            expiry = config.get("expiry", "")
            T = years_to_expiry(expiry) if expiry else float(config.get("timeToExpiry", 0.25))

            if S <= 0 or K <= 0:
                return {"ok": False, "error": "spotPrice and strike must be positive"}

            spot_bumps = config.get("spotBumpsPct", [-10, -5, -2, -1, 0, 1, 2, 5, 10])
            vol_bumps = config.get("volBumps", [-0.05, 0, 0.05])
            time_bumps = config.get("timeBumpsDays", [0, 7, 14])

            scenarios = greeks_what_if(S, K, T, r, sigma, opt_type, q,
                                       spot_bumps, vol_bumps, time_bumps)

            return {"ok": True, "data": {
                "baseSpot": S,
                "baseVol": sigma,
                "baseTimeToExpiry": T,
                "scenarios": scenarios,
                "scenarioCount": len(scenarios),
                "calculatedAt": int(datetime.now().timestamp() * 1000),
            }}

        elif action == "pnl_explain":
            S_old = float(config.get("spotPriceOld", 0))
            S_new = float(config.get("spotPriceNew", 0))
            sigma_old = float(config.get("volOld", 0.25))
            sigma_new = float(config.get("volNew", 0.25))
            K = float(config.get("strike", 0))
            r = float(config.get("riskFreeRate", 0.02))
            opt_type = config.get("optionType", "call")
            q = float(config.get("dividendYield", 0.0))
            quantity = int(config.get("quantity", 1))
            multiplier = float(config.get("multiplier", 10000.0))

            expiry_old = config.get("expiryOld", "")
            expiry_new = config.get("expiryNew", "")
            if expiry_old and expiry_new:
                T_old = years_to_expiry(expiry_old)
                T_new = years_to_expiry(expiry_new)
            else:
                T_old = float(config.get("timeToExpiryOld", 0.25))
                T_new = float(config.get("timeToExpiryNew", T_old))

            if S_old <= 0 or S_new <= 0 or K <= 0:
                return {"ok": False, "error": "spotPriceOld, spotPriceNew, strike must be positive"}

            result = pnl_attribution(S_old, S_new, sigma_old, sigma_new,
                                     T_old, T_new, K, r, opt_type, q,
                                     quantity, multiplier)
            result["calculatedAt"] = int(datetime.now().timestamp() * 1000)
            return {"ok": True, "data": result}

        elif action == "margin":
            S = float(config.get("spotPrice", 0))
            K = float(config.get("strike", 0))
            sigma = float(config.get("volatility", 0.25))
            opt_type = config.get("optionType", "call")
            position_side = config.get("positionSide", "short")
            quantity = int(config.get("quantity", 1))
            multiplier = float(config.get("multiplier", 10000.0))
            settlement = config.get("settlementPrice")
            if settlement is not None:
                settlement = float(settlement)

            expiry = config.get("expiry", "")
            T = years_to_expiry(expiry) if expiry else float(config.get("timeToExpiry", 0.25))

            if S <= 0 or K <= 0:
                return {"ok": False, "error": "spotPrice and strike must be positive"}

            result = estimate_option_margin(S, K, T, sigma, opt_type,
                                            position_side, quantity, multiplier, settlement)
            result["calculatedAt"] = int(datetime.now().timestamp() * 1000)
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
        result = run_greeks_engine(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
