#!/usr/bin/env python3
"""
Options Portfolio Stress Testing Engine (期权组合压力测试引擎).

Capabilities:
  - Portfolio Greeks aggregation with cash-equivalent (dollar) Greeks
  - Portfolio-level VaR (Value at Risk) for options positions
  - Predefined stress scenarios (market crash, vol spike, rate shock, etc.)
  - Custom scenario analysis
  - Pre-trade Greeks impact assessment
  - Auto-hedge suggestions (delta-neutral, vega-neutral)

Input: JSON via stdin
Output: JSON to stdout
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
    class _NormFallback:
        @staticmethod
        def cdf(x):
            return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))
        @staticmethod
        def pdf(x):
            return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)
    norm = _NormFallback()  # type: ignore[assignment]

# Import from greeks_engine (same directory)
try:
    from engines.greeks_engine import (
        bsm_price,
        calculate_all_greeks,
        calculate_charm,
        calculate_vanna,
        calculate_vomma,
        calculate_implied_vol,
        years_to_expiry,
        days_to_expiry,
    )
except ImportError:
    # Direct import fallback for standalone execution
    from greeks_engine import (
        bsm_price,
        calculate_all_greeks,
        calculate_charm,
        calculate_vanna,
        calculate_vomma,
        calculate_implied_vol,
        years_to_expiry,
        days_to_expiry,
    )


# ── Predefined Stress Scenarios ──────────────────────────────────────────────

STRESS_SCENARIOS = {
    "market_crash": {
        "name": "市场崩盘",
        "description": "标的暴跌10%，波动率飙升50%，时间流逝1天",
        "spotBumpPct": -10.0,
        "volBumpAbs": 0.15,     # IV absolute increase
        "timeBumpDays": 1,
        "rateBumpBps": 0,
    },
    "flash_crash": {
        "name": "闪崩",
        "description": "标的瞬间下跌5%，波动率翻倍",
        "spotBumpPct": -5.0,
        "volBumpAbs": 0.30,
        "timeBumpDays": 0,
        "rateBumpBps": 0,
    },
    "vol_explosion": {
        "name": "波动率爆发",
        "description": "标的不变，波动率上升80%",
        "spotBumpPct": 0.0,
        "volBumpAbs": 0.20,
        "timeBumpDays": 0,
        "rateBumpBps": 0,
    },
    "vol_crush": {
        "name": "波动率坍缩",
        "description": "标的小幅上涨，波动率下降40%（典型的财报后走势）",
        "spotBumpPct": 2.0,
        "volBumpAbs": -0.10,
        "timeBumpDays": 1,
        "rateBumpBps": 0,
    },
    "rate_hike": {
        "name": "加息冲击",
        "description": "利率上升50bp，标的下跌2%",
        "spotBumpPct": -2.0,
        "volBumpAbs": 0.03,
        "timeBumpDays": 0,
        "rateBumpBps": 50,
    },
    "time_decay_weekend": {
        "name": "周末时间衰减",
        "description": "标的不变，时间流逝3天",
        "spotBumpPct": 0.0,
        "volBumpAbs": 0.0,
        "timeBumpDays": 3,
        "rateBumpBps": 0,
    },
    "rally": {
        "name": "大幅上涨",
        "description": "标的上涨8%，波动率小幅下降",
        "spotBumpPct": 8.0,
        "volBumpAbs": -0.03,
        "timeBumpDays": 1,
        "rateBumpBps": 0,
    },
    "gamma_squeeze": {
        "name": "Gamma逼空",
        "description": "标的暴涨15%，波动率暴涨",
        "spotBumpPct": 15.0,
        "volBumpAbs": 0.25,
        "timeBumpDays": 0,
        "rateBumpBps": 0,
    },
}


# ── Position Data Structure ──────────────────────────────────────────────────

def _parse_position(pos: dict) -> dict:
    """Normalize a position dict from TS bridge."""
    return {
        "symbol": pos.get("symbol", ""),
        "quantity": int(pos.get("quantity", 0)),
        "optionType": pos.get("optionType", "call"),
        "strike": float(pos.get("strike", 0)),
        "expiry": pos.get("expiry", ""),
        "underlying": pos.get("underlying", ""),
        "spotPrice": float(pos.get("spotPrice", 0)),
        "volatility": float(pos.get("volatility", 0.25)),
        "multiplier": float(pos.get("multiplier", 10000)),
        "avgCost": float(pos.get("avgCost", 0)),
        "currentPrice": float(pos.get("currentPrice", 0)),
    }


# ── Portfolio Greeks Aggregation ─────────────────────────────────────────────

def portfolio_greeks(positions: list, r: float = 0.02, q: float = 0.0) -> dict:
    """Compute full portfolio Greeks with cash (dollar) Greeks.

    Cash Greeks represent the actual monetary exposure:
      - Cash Delta = delta * S * multiplier * quantity
      - Cash Gamma = 0.5 * gamma * S^2 * multiplier * quantity (1% move P&L)
      - Cash Theta = theta * multiplier * quantity (daily)
      - Cash Vega = vega * multiplier * quantity (per 1% IV move)
    """
    agg = {
        "delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0,
        "charm": 0.0, "vanna": 0.0, "vomma": 0.0,
        "cashDelta": 0.0, "cashGamma": 0.0, "cashTheta": 0.0, "cashVega": 0.0,
        "totalMarketValue": 0.0, "totalUnrealizedPnl": 0.0,
    }
    position_details = []

    for raw_pos in positions:
        pos = _parse_position(raw_pos)
        S = pos["spotPrice"]
        K = pos["strike"]
        T_years = years_to_expiry(pos["expiry"]) if pos["expiry"] else 0.25
        sigma = pos["volatility"]
        opt_type = pos["optionType"]
        qty = pos["quantity"]
        mult = pos["multiplier"]

        if S <= 0 or K <= 0 or qty == 0:
            continue

        greeks = calculate_all_greeks(S, K, T_years, r, sigma, opt_type, q)
        charm = calculate_charm(S, K, T_years, r, sigma, opt_type, q)
        vanna = calculate_vanna(S, K, T_years, r, sigma, q)
        vomma = calculate_vomma(S, K, T_years, r, sigma, q)

        # Position-level Greeks (weighted by quantity)
        pos_delta = greeks["delta"] * qty
        pos_gamma = greeks["gamma"] * qty
        pos_theta = greeks["theta"] * qty
        pos_vega = greeks["vega"] * qty
        pos_rho = greeks["rho"] * qty

        # Cash (dollar) Greeks
        cash_delta = greeks["delta"] * S * mult * qty
        cash_gamma = 0.5 * greeks["gamma"] * S * S * mult * qty / 100.0  # per 1% spot move
        cash_theta = greeks["theta"] * mult * qty
        cash_vega = greeks["vega"] * mult * qty

        # Market value
        theo_price = greeks["theoreticalPrice"]
        mkt_val = theo_price * mult * qty
        cost_val = pos["avgCost"] * mult * qty if pos["avgCost"] > 0 else 0
        unrealized = mkt_val - cost_val

        agg["delta"] += pos_delta
        agg["gamma"] += pos_gamma
        agg["theta"] += pos_theta
        agg["vega"] += pos_vega
        agg["rho"] += pos_rho
        agg["charm"] += charm * qty
        agg["vanna"] += vanna * qty
        agg["vomma"] += vomma * qty
        agg["cashDelta"] += cash_delta
        agg["cashGamma"] += cash_gamma
        agg["cashTheta"] += cash_theta
        agg["cashVega"] += cash_vega
        agg["totalMarketValue"] += mkt_val
        agg["totalUnrealizedPnl"] += unrealized

        dte = days_to_expiry(pos["expiry"]) if pos["expiry"] else int(T_years * 365)
        position_details.append({
            "symbol": pos["symbol"],
            "underlying": pos["underlying"],
            "optionType": opt_type,
            "strike": K,
            "expiry": pos["expiry"],
            "dte": dte,
            "quantity": qty,
            "theoreticalPrice": round(theo_price, 6),
            "delta": round(greeks["delta"], 6),
            "gamma": round(greeks["gamma"], 6),
            "theta": round(greeks["theta"], 6),
            "vega": round(greeks["vega"], 6),
            "rho": round(greeks["rho"], 6),
            "charm": round(charm, 6),
            "vanna": round(vanna, 6),
            "vomma": round(vomma, 6),
            "iv": round(sigma, 4),
            "cashDelta": round(cash_delta, 2),
            "cashGamma": round(cash_gamma, 2),
            "cashTheta": round(cash_theta, 2),
            "cashVega": round(cash_vega, 2),
            "marketValue": round(mkt_val, 2),
            "unrealizedPnl": round(unrealized, 2),
            "moneyness": round(S / K, 4) if K > 0 else 0,
        })

    # Round aggregates
    for key in agg:
        agg[key] = round(agg[key], 4 if "cash" not in key and key != "totalMarketValue" and key != "totalUnrealizedPnl" else 2)

    return {
        "portfolioGreeks": agg,
        "positions": position_details,
        "positionCount": len(position_details),
        "calculatedAt": int(datetime.now().timestamp() * 1000),
    }


# ── Stress Testing ───────────────────────────────────────────────────────────

def stress_test(
    positions: list,
    scenarios: Optional[list] = None,
    r: float = 0.02,
    q: float = 0.0,
) -> dict:
    """Run stress scenarios on the options portfolio.

    Each scenario bumps spot, vol, time, and rate, then re-prices
    all positions to compute P&L impact.
    """
    if scenarios is None:
        # Use all predefined scenarios
        scenarios = list(STRESS_SCENARIOS.values())

    # Calculate base portfolio value
    base_value = 0.0
    parsed_positions = []
    for raw_pos in positions:
        pos = _parse_position(raw_pos)
        S = pos["spotPrice"]
        K = pos["strike"]
        T = years_to_expiry(pos["expiry"]) if pos["expiry"] else 0.25
        sigma = pos["volatility"]
        opt_type = pos["optionType"]
        qty = pos["quantity"]
        mult = pos["multiplier"]

        if S <= 0 or K <= 0 or qty == 0:
            continue

        base_price = bsm_price(S, K, T, r, sigma, opt_type, q)
        base_value += base_price * mult * qty
        parsed_positions.append({
            **pos, "T": T, "basePrice": base_price,
        })

    results = []
    for scenario in scenarios:
        spot_bump_pct = float(scenario.get("spotBumpPct", 0))
        vol_bump_abs = float(scenario.get("volBumpAbs", 0))
        time_bump_days = float(scenario.get("timeBumpDays", 0))
        rate_bump_bps = float(scenario.get("rateBumpBps", 0))

        stressed_value = 0.0
        position_impacts = []

        for pos in parsed_positions:
            S_new = pos["spotPrice"] * (1 + spot_bump_pct / 100.0)
            sigma_new = max(pos["volatility"] + vol_bump_abs, 0.01)
            T_new = max(pos["T"] - time_bump_days / 365.0, 0.0)
            r_new = r + rate_bump_bps / 10000.0

            if S_new <= 0:
                S_new = 0.01

            stressed_price = bsm_price(S_new, pos["strike"], T_new, r_new, sigma_new, pos["optionType"], q)
            stressed_pos_value = stressed_price * pos["multiplier"] * pos["quantity"]
            base_pos_value = pos["basePrice"] * pos["multiplier"] * pos["quantity"]
            pnl_impact = stressed_pos_value - base_pos_value

            stressed_value += stressed_pos_value
            position_impacts.append({
                "symbol": pos["symbol"],
                "pnlImpact": round(pnl_impact, 2),
                "baseValue": round(base_pos_value, 2),
                "stressedValue": round(stressed_pos_value, 2),
                "pnlPct": round(pnl_impact / abs(base_pos_value) * 100, 2) if base_pos_value != 0 else 0,
            })

        total_pnl = stressed_value - base_value
        results.append({
            "scenario": scenario.get("name", "自定义"),
            "description": scenario.get("description", ""),
            "parameters": {
                "spotBumpPct": spot_bump_pct,
                "volBumpAbs": vol_bump_abs,
                "timeBumpDays": time_bump_days,
                "rateBumpBps": rate_bump_bps,
            },
            "totalPnl": round(total_pnl, 2),
            "totalPnlPct": round(total_pnl / abs(base_value) * 100, 2) if base_value != 0 else 0,
            "baseValue": round(base_value, 2),
            "stressedValue": round(stressed_value, 2),
            "positionImpacts": position_impacts,
            "worstPosition": min(position_impacts, key=lambda x: x["pnlImpact"])["symbol"] if position_impacts else None,
        })

    # Sort by worst P&L
    results.sort(key=lambda x: x["totalPnl"])

    return {
        "basePortfolioValue": round(base_value, 2),
        "worstScenario": results[0]["scenario"] if results else None,
        "worstPnl": results[0]["totalPnl"] if results else 0,
        "scenarios": results,
        "calculatedAt": int(datetime.now().timestamp() * 1000),
    }


# ── Portfolio VaR ────────────────────────────────────────────────────────────

def portfolio_var(
    positions: list,
    r: float = 0.02,
    q: float = 0.0,
    confidence: float = 0.95,
    horizon_days: int = 1,
    num_simulations: int = 5000,
) -> dict:
    """Compute Portfolio VaR using delta-gamma-theta approximation with Monte Carlo.

    Uses the delta-gamma-theta normal model:
      dP ≈ Σ(delta_i * dS_i + 0.5 * gamma_i * dS_i^2 + theta_i * dt + vega_i * dσ_i)

    Monte Carlo simulates correlated spot and vol moves.
    """
    parsed_positions = []
    underlyings = {}  # group by underlying

    for raw_pos in positions:
        pos = _parse_position(raw_pos)
        S = pos["spotPrice"]
        K = pos["strike"]
        T = years_to_expiry(pos["expiry"]) if pos["expiry"] else 0.25
        sigma = pos["volatility"]
        opt_type = pos["optionType"]
        qty = pos["quantity"]
        mult = pos["multiplier"]

        if S <= 0 or K <= 0 or qty == 0:
            continue

        greeks = calculate_all_greeks(S, K, T, r, sigma, opt_type, q)
        base_price = greeks["theoreticalPrice"]

        entry = {
            **pos, "T": T, "basePrice": base_price,
            "delta": greeks["delta"], "gamma": greeks["gamma"],
            "theta": greeks["theta"], "vega": greeks["vega"],
        }
        parsed_positions.append(entry)

        und = pos["underlying"] or pos["symbol"]
        if und not in underlyings:
            underlyings[und] = {"spotPrice": S, "vol": sigma}

    if not parsed_positions:
        return {"ok": False, "error": "No valid positions for VaR calculation"}

    dt = horizon_days / 365.0
    sqrt_dt = math.sqrt(dt)

    # Monte Carlo simulation
    np.random.seed(42)
    pnl_samples = np.zeros(num_simulations)

    for sim in range(num_simulations):
        sim_pnl = 0.0
        # Generate correlated shocks per underlying
        und_shocks = {}
        for und, info in underlyings.items():
            # Spot shock: GBM random return
            z_spot = np.random.standard_normal()
            spot_return = info["vol"] * sqrt_dt * z_spot
            # Vol shock: mean-reverting with some randomness
            z_vol = np.random.standard_normal()
            vol_shock = 0.3 * info["vol"] * sqrt_dt * z_vol  # vol-of-vol ~30%
            und_shocks[und] = (spot_return, vol_shock)

        for pos in parsed_positions:
            und = pos["underlying"] or pos["symbol"]
            spot_ret, vol_shock = und_shocks.get(und, (0, 0))

            S = pos["spotPrice"]
            dS = S * spot_ret
            d_sigma = vol_shock
            mult = pos["multiplier"]
            qty = pos["quantity"]

            # Delta-gamma-theta-vega approximation
            delta_pnl = pos["delta"] * dS
            gamma_pnl = 0.5 * pos["gamma"] * dS * dS
            theta_pnl = pos["theta"] * horizon_days
            vega_pnl = pos["vega"] * d_sigma * 100.0  # vega is per 1%

            pos_pnl = (delta_pnl + gamma_pnl + theta_pnl + vega_pnl) * mult * qty
            sim_pnl += pos_pnl

        pnl_samples[sim] = sim_pnl

    # Sort for percentile calculation
    pnl_sorted = np.sort(pnl_samples)

    var_idx = int((1 - confidence) * num_simulations)
    var_value = float(-pnl_sorted[var_idx])  # VaR is positive loss

    # CVaR (Expected Shortfall)
    cvar_value = float(-np.mean(pnl_sorted[:var_idx + 1])) if var_idx > 0 else var_value

    # Percentile distribution
    percentiles = {}
    for pct in [1, 5, 10, 25, 50, 75, 90, 95, 99]:
        idx = int(pct / 100.0 * num_simulations)
        idx = min(idx, num_simulations - 1)
        percentiles[f"p{pct}"] = round(float(pnl_sorted[idx]), 2)

    return {
        "var": round(var_value, 2),
        "cvar": round(cvar_value, 2),
        "confidence": confidence,
        "horizonDays": horizon_days,
        "numSimulations": num_simulations,
        "meanPnl": round(float(np.mean(pnl_samples)), 2),
        "stdPnl": round(float(np.std(pnl_samples)), 2),
        "maxLoss": round(float(-np.min(pnl_samples)), 2),
        "maxGain": round(float(np.max(pnl_samples)), 2),
        "percentiles": percentiles,
        "positionCount": len(parsed_positions),
        "calculatedAt": int(datetime.now().timestamp() * 1000),
    }


# ── Pre-Trade Greeks Impact ──────────────────────────────────────────────────

def pretrade_check(
    existing_positions: list,
    new_order: dict,
    r: float = 0.02,
    q: float = 0.0,
    limits: Optional[dict] = None,
) -> dict:
    """Assess the Greeks impact of a proposed trade on the portfolio.

    Returns before/after Greeks comparison and checks against limits.
    """
    if limits is None:
        limits = {}

    # Current portfolio Greeks
    before = portfolio_greeks(existing_positions, r, q)
    before_agg = before["portfolioGreeks"]

    # Simulate adding the new position
    new_pos = _parse_position(new_order)
    S = new_pos["spotPrice"]
    K = new_pos["strike"]
    T = years_to_expiry(new_pos["expiry"]) if new_pos["expiry"] else 0.25
    sigma = new_pos["volatility"]
    opt_type = new_pos["optionType"]
    qty = new_pos["quantity"]
    mult = new_pos["multiplier"]

    if S <= 0 or K <= 0:
        return {"ok": False, "error": "Invalid order parameters (spotPrice/strike must be positive)"}

    new_greeks = calculate_all_greeks(S, K, T, r, sigma, opt_type, q)

    # Impact of the new trade
    order_delta = new_greeks["delta"] * qty
    order_gamma = new_greeks["gamma"] * qty
    order_theta = new_greeks["theta"] * qty
    order_vega = new_greeks["vega"] * qty
    order_cash_delta = new_greeks["delta"] * S * mult * qty
    order_cash_vega = new_greeks["vega"] * mult * qty

    # After adding trade
    after_delta = before_agg["delta"] + order_delta
    after_gamma = before_agg["gamma"] + order_gamma
    after_theta = before_agg["theta"] + order_theta
    after_vega = before_agg["vega"] + order_vega
    after_cash_delta = before_agg["cashDelta"] + order_cash_delta
    after_cash_vega = before_agg["cashVega"] + order_cash_vega

    # Margin estimate for the new position
    margin_per_contract = 0.0
    if qty < 0:  # short position needs margin
        from_otm = max(K - S, 0) if opt_type == "call" else max(S - K, 0)
        premium = new_greeks["theoreticalPrice"]
        margin_per_contract = (premium + max(0.12 * S - from_otm, 0.07 * S)) * mult

    total_margin = margin_per_contract * abs(qty)

    # Check limits
    warnings = []
    breaches = []

    max_delta = limits.get("maxDelta")
    if max_delta and abs(after_delta) > max_delta:
        breaches.append(f"Delta暴露将达 {after_delta:.2f}，超过限制 ±{max_delta}")

    max_gamma = limits.get("maxGamma")
    if max_gamma and abs(after_gamma) > max_gamma:
        breaches.append(f"Gamma暴露将达 {after_gamma:.4f}，超过限制 ±{max_gamma}")

    max_vega = limits.get("maxVega")
    if max_vega and abs(after_vega) > max_vega:
        breaches.append(f"Vega暴露将达 {after_vega:.2f}，超过限制 ±{max_vega}")

    max_theta = limits.get("maxTheta")
    if max_theta and max_theta > 0 and abs(after_theta) > max_theta:
        breaches.append(f"Theta暴露将达 {after_theta:.2f}/天，超过限制 ±{max_theta}")

    max_cash_delta = limits.get("maxCashDelta")
    if max_cash_delta and abs(after_cash_delta) > max_cash_delta:
        breaches.append(f"现金Delta将达 ¥{after_cash_delta:.0f}，超过限制 ±¥{max_cash_delta}")

    # Concentration warning
    if before["positionCount"] > 0:
        order_vega_ratio = abs(order_vega) / (abs(before_agg["vega"]) + abs(order_vega)) * 100 if (abs(before_agg["vega"]) + abs(order_vega)) > 0 else 0
        if order_vega_ratio > 50:
            warnings.append(f"该笔交易贡献了组合 {order_vega_ratio:.0f}% 的Vega敞口，集中度过高")

    return {
        "orderGreeks": {
            "delta": round(order_delta, 4),
            "gamma": round(order_gamma, 6),
            "theta": round(order_theta, 4),
            "vega": round(order_vega, 4),
            "cashDelta": round(order_cash_delta, 2),
            "cashVega": round(order_cash_vega, 2),
            "theoreticalPrice": round(new_greeks["theoreticalPrice"], 6),
        },
        "before": {
            "delta": round(before_agg["delta"], 4),
            "gamma": round(before_agg["gamma"], 6),
            "theta": round(before_agg["theta"], 4),
            "vega": round(before_agg["vega"], 4),
            "cashDelta": round(before_agg["cashDelta"], 2),
        },
        "after": {
            "delta": round(after_delta, 4),
            "gamma": round(after_gamma, 6),
            "theta": round(after_theta, 4),
            "vega": round(after_vega, 4),
            "cashDelta": round(after_cash_delta, 2),
        },
        "marginEstimate": round(total_margin, 2) if total_margin > 0 else None,
        "breaches": breaches if breaches else None,
        "warnings": warnings if warnings else None,
        "approved": len(breaches) == 0,
        "calculatedAt": int(datetime.now().timestamp() * 1000),
    }


# ── Auto-Hedge Suggestions ──────────────────────────────────────────────────

def hedge_suggestions(
    positions: list,
    r: float = 0.02,
    q: float = 0.0,
    hedge_targets: Optional[dict] = None,
) -> dict:
    """Suggest hedging trades to neutralize portfolio Greeks.

    Targets:
      - Delta neutral: suggest underlying shares or ATM options
      - Vega neutral: suggest straddle/strangle adjustments
      - Gamma neutral: suggest near-ATM options
    """
    if hedge_targets is None:
        hedge_targets = {"delta": 0, "vega": 0}

    pf = portfolio_greeks(positions, r, q)
    agg = pf["portfolioGreeks"]

    suggestions = []

    # --- Delta hedge ---
    target_delta = hedge_targets.get("delta", 0)
    delta_excess = agg["delta"] - target_delta

    if abs(delta_excess) > 0.5:  # Only suggest if meaningful
        # Option 1: Hedge with underlying shares
        # For ETF options with multiplier 10000, each share of underlying = 1 delta
        # delta_excess > 0 means too long → sell underlying
        underlying_hedge_qty = -round(delta_excess, 0)
        if underlying_hedge_qty != 0:
            suggestions.append({
                "type": "delta_hedge_underlying",
                "description": f"{'买入' if underlying_hedge_qty > 0 else '卖出'}标的 {abs(int(underlying_hedge_qty))} 份以对冲Delta",
                "instrument": "underlying",
                "quantity": int(underlying_hedge_qty),
                "greeksImpact": {
                    "deltaChange": round(-delta_excess, 4),
                    "resultingDelta": round(target_delta, 4),
                },
                "priority": "high",
            })

        # Option 2: Hedge with ATM options (provides gamma as a bonus)
        # Buy puts if delta is too positive, buy calls if too negative
        if delta_excess > 0:
            # Need negative delta → buy puts
            suggestions.append({
                "type": "delta_hedge_option",
                "description": f"买入ATM看跌期权约 {abs(int(delta_excess / 0.5))} 张（每张delta≈-0.5）",
                "instrument": "atm_put",
                "quantity": max(1, abs(int(delta_excess / 0.5))),
                "greeksImpact": {
                    "deltaChange": round(-delta_excess, 4),
                    "gammaBonus": "正Gamma增加",
                },
                "priority": "medium",
                "note": "期权对冲同时获得正Gamma保护，但有时间成本",
            })
        else:
            suggestions.append({
                "type": "delta_hedge_option",
                "description": f"买入ATM看涨期权约 {abs(int(delta_excess / 0.5))} 张（每张delta≈0.5）",
                "instrument": "atm_call",
                "quantity": max(1, abs(int(delta_excess / 0.5))),
                "greeksImpact": {
                    "deltaChange": round(-delta_excess, 4),
                    "gammaBonus": "正Gamma增加",
                },
                "priority": "medium",
                "note": "期权对冲同时获得正Gamma保护，但有时间成本",
            })

    # --- Vega hedge ---
    target_vega = hedge_targets.get("vega", 0)
    vega_excess = agg["vega"] - target_vega

    if abs(vega_excess) > 1.0:
        if vega_excess > 0:
            # Too long vega → sell straddle or sell options
            suggestions.append({
                "type": "vega_hedge",
                "description": f"卖出ATM跨式组合约 {max(1, abs(int(vega_excess / 2)))} 组以减少Vega敞口",
                "instrument": "short_straddle",
                "quantity": max(1, abs(int(vega_excess / 2))),
                "greeksImpact": {
                    "vegaChange": round(-vega_excess, 4),
                    "resultingVega": round(target_vega, 4),
                },
                "priority": "medium",
                "note": "卖出跨式有裸卖风险，需充足保证金",
            })
        else:
            suggestions.append({
                "type": "vega_hedge",
                "description": f"买入ATM跨式组合约 {max(1, abs(int(vega_excess / 2)))} 组以增加Vega敞口",
                "instrument": "long_straddle",
                "quantity": max(1, abs(int(vega_excess / 2))),
                "greeksImpact": {
                    "vegaChange": round(-vega_excess, 4),
                    "resultingVega": round(target_vega, 4),
                },
                "priority": "medium",
            })

    # --- Gamma hedge ---
    target_gamma = hedge_targets.get("gamma")
    if target_gamma is not None:
        gamma_excess = agg["gamma"] - target_gamma
        if abs(gamma_excess) > 0.01:
            if gamma_excess < 0:
                # Short gamma → buy near-ATM options
                suggestions.append({
                    "type": "gamma_hedge",
                    "description": "买入近月ATM期权以增加正Gamma",
                    "instrument": "near_atm_options",
                    "greeksImpact": {
                        "gammaChange": round(-gamma_excess, 6),
                    },
                    "priority": "high" if abs(gamma_excess) > 0.1 else "medium",
                    "note": "负Gamma头寸在标的大幅波动时亏损加速，建议尽快对冲",
                })

    if not suggestions:
        suggestions.append({
            "type": "no_action",
            "description": "当前组合Greeks在可接受范围内，暂无需调整",
            "priority": "low",
        })

    return {
        "currentGreeks": {
            "delta": round(agg["delta"], 4),
            "gamma": round(agg["gamma"], 6),
            "theta": round(agg["theta"], 4),
            "vega": round(agg["vega"], 4),
            "cashDelta": round(agg["cashDelta"], 2),
            "cashVega": round(agg["cashVega"], 2),
        },
        "hedgeTargets": hedge_targets,
        "suggestions": suggestions,
        "suggestionCount": len(suggestions),
        "calculatedAt": int(datetime.now().timestamp() * 1000),
    }


# ── Main Dispatcher ──────────────────────────────────────────────────────────

def run_stress_test_engine(config: dict) -> dict:
    """Main entry point for options stress testing.

    Actions:
        portfolio_greeks  — full portfolio Greeks with cash Greeks
        stress_test       — run stress scenarios
        var               — portfolio VaR calculation
        pretrade_check    — pre-trade Greeks impact assessment
        hedge_suggestions — auto-hedge suggestions
    """
    action = config.get("action", "stress_test")
    r = float(config.get("riskFreeRate", 0.02))
    q = float(config.get("dividendYield", 0.0))
    positions = config.get("positions", [])

    try:
        if action == "portfolio_greeks":
            result = portfolio_greeks(positions, r, q)
            return {"ok": True, "data": result}

        elif action == "stress_test":
            scenario_names = config.get("scenarios")
            if scenario_names:
                # Filter to requested scenarios
                selected = []
                for name in scenario_names:
                    if name in STRESS_SCENARIOS:
                        selected.append(STRESS_SCENARIOS[name])
                    else:
                        # Custom scenario passed inline
                        selected.append(name if isinstance(name, dict) else {
                            "name": name, "description": "自定义场景",
                            "spotBumpPct": 0, "volBumpAbs": 0,
                            "timeBumpDays": 0, "rateBumpBps": 0,
                        })
                scenarios = selected if selected else None
            else:
                scenarios = None

            result = stress_test(positions, scenarios, r, q)
            return {"ok": True, "data": result}

        elif action == "var":
            confidence = float(config.get("confidence", 0.95))
            horizon = int(config.get("horizonDays", 1))
            sims = int(config.get("numSimulations", 5000))
            result = portfolio_var(positions, r, q, confidence, horizon, sims)
            if "ok" in result:
                return result  # Error case
            return {"ok": True, "data": result}

        elif action == "pretrade_check":
            new_order = config.get("newOrder", {})
            limits = config.get("limits", {})
            result = pretrade_check(positions, new_order, r, q, limits)
            if "ok" in result:
                return result
            return {"ok": True, "data": result}

        elif action == "hedge_suggestions":
            targets = config.get("hedgeTargets", {"delta": 0, "vega": 0})
            result = hedge_suggestions(positions, r, q, targets)
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
        result = run_stress_test_engine(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
