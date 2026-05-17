#!/usr/bin/env python3
"""
Volatility Models Engine (波动率模型引擎).

Implements advanced volatility modeling for calibrating and interpolating
implied volatility surfaces — essential for exotic option pricing and
market-making.

Capabilities:
  - SABR model calibration & evaluation (Hagan et al. 2002)
  - Dupire local volatility extraction from IV surface
  - SVI (Stochastic Volatility Inspired) smile parameterization
  - IV surface interpolation & smoothing
  - Volatility term structure analysis

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
    from scipy.optimize import minimize, least_squares
    from scipy.stats import norm
    from scipy.interpolate import RectBivariateSpline
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False
    from scipy.stats import norm  # type: ignore[no-redef]

try:
    from greeks_engine import bsm_price, calculate_implied_vol
except ImportError:
    def bsm_price(S, K, T, r, sigma, option_type="call", q=0.0):
        if T <= 0:
            return max(S - K, 0.0) if option_type == "call" else max(K - S, 0.0)
        d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        if option_type == "call":
            return S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * math.exp(-q * T) * norm.cdf(-d1)

    def calculate_implied_vol(market_price, S, K, T, r, option_type="call", q=0.0):
        for sigma in np.linspace(0.01, 5.0, 500):
            if abs(bsm_price(S, K, T, r, sigma, option_type, q) - market_price) < 1e-4:
                return float(sigma)
        return None


# ══════════════════════════════════════════════════════════════════════════════
# SABR Model (Hagan et al. 2002)
#
# The SABR stochastic volatility model:
#   dF = σ · F^β · dW₁
#   dσ = ν · σ · dW₂
#   E[dW₁ dW₂] = ρ dt
#
# Parameters: α (initial vol), β (CEV exponent), ρ (correlation), ν (vol-of-vol)
# ══════════════════════════════════════════════════════════════════════════════


def sabr_implied_vol(
    F: float,
    K: float,
    T: float,
    alpha: float,
    beta: float,
    rho: float,
    nu: float,
) -> float:
    """Hagan's SABR implied volatility approximation formula.

    Args:
        F:     Forward price
        K:     Strike price
        T:     Time to expiry (years)
        alpha: SABR α — initial volatility level
        beta:  SABR β — CEV exponent (0 = normal, 1 = lognormal)
        rho:   SABR ρ — correlation between F and σ processes (-1 < ρ < 1)
        nu:    SABR ν — vol-of-vol

    Returns:
        Implied (Black) volatility for the given strike.
    """
    if F <= 0 or K <= 0 or T <= 0 or alpha <= 0:
        return 0.0

    # Handle ATM case (F ≈ K)
    if abs(F - K) < 1e-12:
        FK_mid = F
        logFK = 0.0
    else:
        FK_mid = math.sqrt(F * K)
        logFK = math.log(F / K)

    FK_beta = (F * K) ** ((1 - beta) / 2)

    # z and x(z) — Hagan's z-mapping
    if abs(nu) < 1e-12:
        # ν → 0 limit
        z_over_xz = 1.0
    else:
        z = (nu / alpha) * FK_beta * logFK
        # x(z) = log((√(1 - 2ρz + z²) + z - ρ) / (1 - ρ))
        disc = 1 - 2 * rho * z + z * z
        if disc < 0:
            disc = 0.0
        sqrt_disc = math.sqrt(disc)
        xz = math.log((sqrt_disc + z - rho) / (1 - rho))
        if abs(xz) < 1e-12:
            z_over_xz = 1.0
        else:
            z_over_xz = z / xz

    # Denominator terms
    one_minus_beta = 1 - beta
    denom1 = FK_beta * (
        1
        + one_minus_beta ** 2 / 24 * logFK ** 2
        + one_minus_beta ** 4 / 1920 * logFK ** 4
    )

    # Correction terms
    term1 = one_minus_beta ** 2 / 24 * alpha ** 2 / (FK_beta ** 2)
    term2 = rho * beta * nu * alpha / (4 * FK_beta)
    term3 = (2 - 3 * rho ** 2) / 24 * nu ** 2
    correction = 1 + (term1 + term2 + term3) * T

    sigma_B = (alpha / denom1) * z_over_xz * correction
    return max(sigma_B, 1e-8)


def calibrate_sabr(
    F: float,
    T: float,
    strikes: list[float],
    market_vols: list[float],
    beta: float = 0.5,
    initial_guess: Optional[list[float]] = None,
) -> dict:
    """Calibrate SABR parameters (α, ρ, ν) to market implied volatilities.

    β is typically fixed (common choices: 0, 0.5, or 1).

    Args:
        F:           Forward price
        T:           Time to expiry
        strikes:     List of strike prices
        market_vols: List of market implied volatilities
        beta:        Fixed β parameter (default 0.5)
        initial_guess: [alpha, rho, nu] initial values

    Returns:
        dict with calibrated parameters and fit quality
    """
    if not HAS_SCIPY:
        return {"ok": False, "error": "scipy required for SABR calibration"}

    if len(strikes) < 3:
        return {"ok": False, "error": "Need at least 3 strike/vol pairs for calibration"}

    t0 = time.time()

    strikes_arr = np.array(strikes)
    vols_arr = np.array(market_vols)

    if initial_guess is None:
        # Heuristic: alpha ≈ ATM vol, rho ≈ 0, nu ≈ vol-of-vol
        atm_idx = np.argmin(np.abs(strikes_arr - F))
        alpha0 = float(vols_arr[atm_idx])
        initial_guess = [alpha0, -0.2, 0.3]

    def objective(params):
        alpha, rho, nu = params
        # Enforce bounds
        if alpha <= 0 or nu <= 0 or abs(rho) >= 1:
            return np.full(len(strikes), 1e6)
        model_vols = []
        for K in strikes:
            try:
                sv = sabr_implied_vol(F, K, T, alpha, beta, rho, nu)
                model_vols.append(sv)
            except Exception:
                model_vols.append(0.0)
        return np.array(model_vols) - vols_arr

    result = least_squares(
        objective,
        initial_guess,
        bounds=([1e-6, -0.999, 1e-6], [10.0, 0.999, 10.0]),
        method="trf",
        max_nfev=500,
    )

    alpha_cal, rho_cal, nu_cal = result.x

    # Compute fitted vols
    fitted_vols = [sabr_implied_vol(F, K, T, alpha_cal, beta, rho_cal, nu_cal) for K in strikes]
    errors = [abs(f - m) for f, m in zip(fitted_vols, market_vols)]
    rmse = math.sqrt(sum(e ** 2 for e in errors) / len(errors))

    elapsed = time.time() - t0

    return {
        "ok": True,
        "data": {
            "alpha": round(alpha_cal, 8),
            "beta": beta,
            "rho": round(rho_cal, 8),
            "nu": round(nu_cal, 8),
            "rmse": round(rmse, 8),
            "maxError": round(max(errors), 8),
            "nPoints": len(strikes),
            "fittedSmile": [
                {
                    "strike": round(K, 4),
                    "marketVol": round(mv, 6),
                    "modelVol": round(fv, 6),
                    "error": round(abs(fv - mv), 8),
                }
                for K, mv, fv in zip(strikes, market_vols, fitted_vols)
            ],
            "elapsedMs": round(elapsed * 1000, 1),
        },
    }


def sabr_smile(
    F: float,
    T: float,
    alpha: float,
    beta: float,
    rho: float,
    nu: float,
    strike_range: Optional[tuple[float, float]] = None,
    n_points: int = 50,
) -> dict:
    """Generate a full volatility smile from calibrated SABR parameters.

    Args:
        strike_range: (low, high) strike range. Default: F * [0.7, 1.3]
        n_points:     Number of points in the smile curve
    """
    if strike_range is None:
        lo = F * 0.7
        hi = F * 1.3
    else:
        lo, hi = strike_range

    strikes = np.linspace(lo, hi, n_points)
    smile = []
    for K in strikes:
        vol = sabr_implied_vol(F, float(K), T, alpha, beta, rho, nu)
        smile.append({
            "strike": round(float(K), 4),
            "moneyness": round(float(K) / F, 4),
            "impliedVol": round(vol, 6),
        })

    return {
        "forwardPrice": F,
        "timeToExpiry": T,
        "params": {"alpha": alpha, "beta": beta, "rho": rho, "nu": nu},
        "smile": smile,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SVI Model (Gatheral 2004 — Stochastic Volatility Inspired)
#
# Raw SVI parameterization of total implied variance:
#   w(k) = a + b · (ρ · (k - m) + √((k - m)² + σ²))
#
# where k = ln(K/F) is log-moneyness, w = σ²_imp · T is total variance.
# Parameters: a, b, ρ (SVI rho), m (center), σ (SVI sigma)
# ══════════════════════════════════════════════════════════════════════════════


def svi_total_variance(
    k: float,
    a: float,
    b: float,
    rho: float,
    m: float,
    sigma: float,
) -> float:
    """SVI raw parameterization: total implied variance w(k).

    w(k) = a + b · (ρ · (k - m) + √((k - m)² + σ²))
    """
    km = k - m
    return a + b * (rho * km + math.sqrt(km * km + sigma * sigma))


def svi_implied_vol(
    k: float,
    T: float,
    a: float,
    b: float,
    rho: float,
    m: float,
    sigma: float,
) -> float:
    """Convert SVI total variance to implied volatility."""
    w = svi_total_variance(k, a, b, rho, m, sigma)
    if w <= 0 or T <= 0:
        return 0.0
    return math.sqrt(w / T)


def calibrate_svi(
    F: float,
    T: float,
    strikes: list[float],
    market_vols: list[float],
    initial_guess: Optional[list[float]] = None,
) -> dict:
    """Calibrate SVI parameters to market smile data.

    Args:
        F:           Forward price
        T:           Time to expiry
        strikes:     Market strikes
        market_vols: Market implied vols

    Returns:
        dict with calibrated SVI parameters
    """
    if not HAS_SCIPY:
        return {"ok": False, "error": "scipy required for SVI calibration"}

    if len(strikes) < 5:
        return {"ok": False, "error": "Need at least 5 strike/vol pairs for SVI calibration"}

    t0 = time.time()

    # Convert to log-moneyness and total variance
    k_arr = np.log(np.array(strikes) / F)
    w_market = np.array(market_vols) ** 2 * T  # total variance

    if initial_guess is None:
        atm_var = float(np.interp(0, k_arr, w_market))
        initial_guess = [atm_var, 0.1, -0.2, 0.0, 0.1]

    def objective(params):
        a, b, rho_s, m_s, sigma_s = params
        if b < 0 or abs(rho_s) >= 1 or sigma_s <= 0:
            return 1e10
        w_model = np.array([svi_total_variance(k, a, b, rho_s, m_s, sigma_s) for k in k_arr])
        # Penalize negative total variance (calendar arbitrage)
        penalty = np.sum(np.maximum(-w_model, 0) ** 2) * 1e6
        return float(np.sum((w_model - w_market) ** 2)) + penalty

    result = minimize(
        objective,
        initial_guess,
        method="Nelder-Mead",
        options={"maxiter": 2000, "xatol": 1e-10, "fatol": 1e-10},
    )

    a, b, rho_s, m_s, sigma_s = result.x

    # Compute fitted
    fitted_vols = [svi_implied_vol(k, T, a, b, rho_s, m_s, sigma_s) for k in k_arr]
    errors = [abs(f - m) for f, m in zip(fitted_vols, market_vols)]
    rmse = math.sqrt(sum(e ** 2 for e in errors) / len(errors))

    elapsed = time.time() - t0

    return {
        "ok": True,
        "data": {
            "a": round(a, 8),
            "b": round(b, 8),
            "rho": round(rho_s, 8),
            "m": round(m_s, 8),
            "sigma": round(sigma_s, 8),
            "rmse": round(rmse, 8),
            "maxError": round(max(errors), 8),
            "nPoints": len(strikes),
            "fittedSmile": [
                {
                    "strike": round(K, 4),
                    "logMoneyness": round(float(k), 6),
                    "marketVol": round(mv, 6),
                    "modelVol": round(fv, 6),
                    "error": round(abs(fv - mv), 8),
                }
                for K, k, mv, fv in zip(strikes, k_arr, market_vols, fitted_vols)
            ],
            "elapsedMs": round(elapsed * 1000, 1),
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# Dupire Local Volatility
#
# The Dupire equation extracts local volatility from the IV surface:
#   σ_loc²(K, T) = (∂C/∂T + (r-q)K ∂C/∂K + qC) / (½ K² ∂²C/∂K²)
#
# In terms of implied volatility:
#   σ_loc²(K, T) = (∂w/∂T) / (1 - k/w ∂w/∂k + ¼(-¼ - 1/w + k²/w²)(∂w/∂k)² + ½ ∂²w/∂k²)
#
# where w = σ²_imp · T, k = ln(K/F)
# ══════════════════════════════════════════════════════════════════════════════


def dupire_local_vol(
    S: float,
    r: float,
    q: float,
    strikes: list[float],
    expiries: list[float],
    iv_matrix: list[list[float]],
) -> dict:
    """Extract Dupire local volatility surface from implied volatility data.

    Uses finite differences on the call price surface derived from BSM + IV.

    Args:
        S:          Spot price
        r:          Risk-free rate
        q:          Dividend yield
        strikes:    List of strikes (columns)
        expiries:   List of expiry times in years (rows)
        iv_matrix:  2D array [expiry_idx][strike_idx] of implied vols

    Returns:
        dict with local volatility surface
    """
    K_arr = np.array(strikes)
    T_arr = np.array(expiries)
    iv_mat = np.array(iv_matrix)

    n_T, n_K = iv_mat.shape

    if n_T < 2 or n_K < 3:
        return {"ok": False, "error": "Need at least 2 expiries and 3 strikes for Dupire extraction"}

    # Compute call price surface: C(K, T) = BSM(S, K, T, r, σ_imp(K,T))
    C = np.zeros((n_T, n_K))
    for i in range(n_T):
        for j in range(n_K):
            C[i, j] = bsm_price(S, K_arr[j], T_arr[i], r, iv_mat[i, j], "call", q)

    local_vol_surface = []

    # Compute local vol using Dupire formula with finite differences
    # σ_loc²(K,T) = (∂C/∂T + (r-q)K ∂C/∂K + qC) / (½K² ∂²C/∂K²)
    for i in range(n_T):
        row = []
        for j in range(n_K):
            # ∂C/∂T: forward/backward/central difference
            if i == 0:
                dC_dT = (C[1, j] - C[0, j]) / (T_arr[1] - T_arr[0])
            elif i == n_T - 1:
                dC_dT = (C[i, j] - C[i - 1, j]) / (T_arr[i] - T_arr[i - 1])
            else:
                dC_dT = (C[i + 1, j] - C[i - 1, j]) / (T_arr[i + 1] - T_arr[i - 1])

            # ∂C/∂K: central difference
            if j == 0:
                dC_dK = (C[i, 1] - C[i, 0]) / (K_arr[1] - K_arr[0])
                d2C_dK2 = 0.0
            elif j == n_K - 1:
                dC_dK = (C[i, j] - C[i, j - 1]) / (K_arr[j] - K_arr[j - 1])
                d2C_dK2 = 0.0
            else:
                dK_fwd = K_arr[j + 1] - K_arr[j]
                dK_bwd = K_arr[j] - K_arr[j - 1]
                dC_dK = (C[i, j + 1] - C[i, j - 1]) / (dK_fwd + dK_bwd)
                d2C_dK2 = 2 * (
                    (C[i, j + 1] - C[i, j]) / dK_fwd
                    - (C[i, j] - C[i, j - 1]) / dK_bwd
                ) / (dK_fwd + dK_bwd)

            K = K_arr[j]
            numerator = dC_dT + (r - q) * K * dC_dK + q * C[i, j]
            denominator = 0.5 * K ** 2 * d2C_dK2

            if denominator > 1e-10 and numerator > 0:
                local_var = numerator / denominator
                local_sigma = math.sqrt(local_var)
            else:
                # Fallback to implied vol where Dupire formula is unstable
                local_sigma = float(iv_mat[i, j])

            row.append({
                "strike": round(float(K), 4),
                "expiry": round(float(T_arr[i]), 6),
                "localVol": round(local_sigma, 6),
                "impliedVol": round(float(iv_mat[i, j]), 6),
                "moneyness": round(float(K) / S, 4),
            })

        local_vol_surface.extend(row)

    return {
        "ok": True,
        "data": {
            "spotPrice": S,
            "nStrikes": n_K,
            "nExpiries": n_T,
            "surface": local_vol_surface,
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
# IV Surface Interpolation & Term Structure
# ══════════════════════════════════════════════════════════════════════════════


def interpolate_iv_surface(
    strikes: list[float],
    expiries: list[float],
    iv_matrix: list[list[float]],
    target_strikes: Optional[list[float]] = None,
    target_expiries: Optional[list[float]] = None,
) -> dict:
    """Interpolate/smooth an IV surface using bicubic spline.

    Takes sparse market IV data and produces a smooth, dense surface.

    Args:
        strikes:     Known strikes (columns)
        expiries:    Known expiries in years (rows)
        iv_matrix:   Known IVs [expiry_idx][strike_idx]
        target_strikes:  Target strikes for interpolation
        target_expiries: Target expiries for interpolation
    """
    if not HAS_SCIPY:
        return {"ok": False, "error": "scipy required for IV surface interpolation"}

    K_arr = np.array(strikes)
    T_arr = np.array(expiries)
    iv_mat = np.array(iv_matrix)

    # Build total variance surface w = σ² · T (avoids calendar spread arbitrage)
    w_mat = iv_mat ** 2 * T_arr[:, np.newaxis]

    try:
        spline = RectBivariateSpline(T_arr, K_arr, w_mat, kx=min(3, len(T_arr) - 1), ky=min(3, len(K_arr) - 1))
    except Exception as e:
        return {"ok": False, "error": f"Spline fitting failed: {e}"}

    if target_strikes is None:
        target_strikes = np.linspace(K_arr[0], K_arr[-1], 30).tolist()
    if target_expiries is None:
        target_expiries = np.linspace(T_arr[0], T_arr[-1], 10).tolist()

    surface = []
    for T in target_expiries:
        for K in target_strikes:
            w = float(spline(T, K)[0, 0])
            if w > 0 and T > 0:
                iv = math.sqrt(w / T)
            else:
                iv = 0.0
            surface.append({
                "strike": round(K, 4),
                "expiry": round(T, 6),
                "impliedVol": round(iv, 6),
                "totalVariance": round(w, 8),
            })

    return {
        "ok": True,
        "data": {
            "nTargetStrikes": len(target_strikes),
            "nTargetExpiries": len(target_expiries),
            "surface": surface,
        },
    }


def vol_term_structure(
    F: float,
    expiries: list[float],
    atm_vols: list[float],
) -> dict:
    """Analyze the ATM volatility term structure.

    Reports:
      - Forward volatility between tenors
      - Variance ratio (deviation from √T scaling)
      - Term structure slope
    """
    T_arr = np.array(expiries)
    v_arr = np.array(atm_vols)
    total_var = v_arr ** 2 * T_arr

    results = []
    for i in range(len(T_arr)):
        entry: dict[str, Any] = {
            "expiry": round(float(T_arr[i]), 6),
            "expiryDays": int(T_arr[i] * 365),
            "atmVol": round(float(v_arr[i]), 6),
            "totalVariance": round(float(total_var[i]), 8),
        }

        if i > 0:
            # Forward variance: σ²_fwd = (w₂ - w₁) / (T₂ - T₁)
            dw = total_var[i] - total_var[i - 1]
            dT = T_arr[i] - T_arr[i - 1]
            if dT > 0 and dw > 0:
                fwd_var = dw / dT
                fwd_vol = math.sqrt(fwd_var)
                entry["forwardVol"] = round(fwd_vol, 6)
            else:
                entry["forwardVol"] = None

            # Variance ratio: w(T) / (σ_atm_short² · T)
            # Should be ≈ 1 if term structure is flat
            if total_var[0] > 0:
                var_ratio = total_var[i] / (v_arr[0] ** 2 * T_arr[i])
                entry["varianceRatio"] = round(float(var_ratio), 4)

        results.append(entry)

    # Classify term structure shape
    if len(v_arr) >= 2:
        if v_arr[-1] > v_arr[0] * 1.05:
            shape = "contango"  # upward sloping — normal
        elif v_arr[-1] < v_arr[0] * 0.95:
            shape = "backwardation"  # inverted — stress/event
        else:
            shape = "flat"
    else:
        shape = "insufficient_data"

    return {
        "forwardPrice": F,
        "shape": shape,
        "termStructure": results,
    }


# ── Main Dispatcher ───────────────────────────────────────────────────────────

def run_vol_models_engine(config: dict) -> dict:
    """Main entry point for volatility model calculations.

    Actions:
        sabr_calibrate   — Calibrate SABR parameters to market smile
        sabr_smile       — Generate smile from SABR parameters
        sabr_price       — Get SABR implied vol for a single strike
        svi_calibrate    — Calibrate SVI parameters to market smile
        dupire           — Extract Dupire local volatility surface
        interpolate      — Interpolate/smooth IV surface (bicubic spline)
        term_structure   — ATM volatility term structure analysis
    """
    action = config.get("action", "sabr_calibrate")

    try:
        if action == "sabr_calibrate":
            F = float(config.get("forwardPrice", 0))
            T = float(config.get("timeToExpiry", 0))
            strikes = config.get("strikes", [])
            market_vols = config.get("marketVols", [])
            beta = float(config.get("beta", 0.5))

            if F <= 0 or T <= 0:
                return {"ok": False, "error": "forwardPrice and timeToExpiry must be positive"}
            if len(strikes) < 3 or len(strikes) != len(market_vols):
                return {"ok": False, "error": "Need at least 3 matching strike/vol pairs"}

            return calibrate_sabr(F, T, strikes, market_vols, beta)

        elif action == "sabr_smile":
            F = float(config.get("forwardPrice", 0))
            T = float(config.get("timeToExpiry", 0))
            alpha = float(config.get("alpha", 0))
            beta = float(config.get("beta", 0.5))
            rho = float(config.get("rho", 0))
            nu = float(config.get("nu", 0))

            if F <= 0 or T <= 0 or alpha <= 0:
                return {"ok": False, "error": "forwardPrice, timeToExpiry, alpha must be positive"}

            sr = config.get("strikeRange")
            strike_range = tuple(sr) if sr and len(sr) == 2 else None
            n_points = int(config.get("nPoints", 50))

            result = sabr_smile(F, T, alpha, beta, rho, nu, strike_range, n_points)
            return {"ok": True, "data": result}

        elif action == "sabr_price":
            F = float(config.get("forwardPrice", 0))
            K = float(config.get("strike", 0))
            T = float(config.get("timeToExpiry", 0))
            alpha = float(config.get("alpha", 0))
            beta = float(config.get("beta", 0.5))
            rho = float(config.get("rho", 0))
            nu = float(config.get("nu", 0))

            if F <= 0 or K <= 0 or T <= 0 or alpha <= 0:
                return {"ok": False, "error": "forwardPrice, strike, timeToExpiry, alpha must be positive"}

            vol = sabr_implied_vol(F, K, T, alpha, beta, rho, nu)
            r = float(config.get("riskFreeRate", 0.02))
            q = float(config.get("dividendYield", 0.0))
            opt_type = config.get("optionType", "call")
            price = bsm_price(F * math.exp(-r * T), K, T, r, vol, opt_type, q)

            return {"ok": True, "data": {
                "sabrImpliedVol": round(vol, 6),
                "bsmPrice": round(price, 6),
                "strike": K,
                "moneyness": round(K / F, 4),
            }}

        elif action == "svi_calibrate":
            F = float(config.get("forwardPrice", 0))
            T = float(config.get("timeToExpiry", 0))
            strikes = config.get("strikes", [])
            market_vols = config.get("marketVols", [])

            if F <= 0 or T <= 0:
                return {"ok": False, "error": "forwardPrice and timeToExpiry must be positive"}
            if len(strikes) < 5 or len(strikes) != len(market_vols):
                return {"ok": False, "error": "Need at least 5 matching strike/vol pairs"}

            return calibrate_svi(F, T, strikes, market_vols)

        elif action == "dupire":
            S = float(config.get("spotPrice", 0))
            r = float(config.get("riskFreeRate", 0.02))
            q = float(config.get("dividendYield", 0.0))
            strikes = config.get("strikes", [])
            expiries = config.get("expiries", [])
            iv_matrix = config.get("ivMatrix", [])

            if S <= 0:
                return {"ok": False, "error": "spotPrice must be positive"}
            if not strikes or not expiries or not iv_matrix:
                return {"ok": False, "error": "strikes, expiries, and ivMatrix are required"}

            return dupire_local_vol(S, r, q, strikes, expiries, iv_matrix)

        elif action == "interpolate":
            strikes = config.get("strikes", [])
            expiries = config.get("expiries", [])
            iv_matrix = config.get("ivMatrix", [])

            if not strikes or not expiries or not iv_matrix:
                return {"ok": False, "error": "strikes, expiries, and ivMatrix are required"}

            target_K = config.get("targetStrikes")
            target_T = config.get("targetExpiries")

            return interpolate_iv_surface(strikes, expiries, iv_matrix, target_K, target_T)

        elif action == "term_structure":
            F = float(config.get("forwardPrice", 0))
            expiries = config.get("expiries", [])
            atm_vols = config.get("atmVols", [])

            if F <= 0:
                return {"ok": False, "error": "forwardPrice must be positive"}
            if len(expiries) < 2 or len(expiries) != len(atm_vols):
                return {"ok": False, "error": "Need at least 2 matching expiry/vol pairs"}

            result = vol_term_structure(F, expiries, atm_vols)
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
        result = run_vol_models_engine(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
