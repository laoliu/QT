#!/usr/bin/env python3
"""
Option Strategy Analysis Engine (期权策略分析引擎).

Provides:
  - Strategy template construction (Covered Call, Iron Condor, etc.)
  - Payoff diagram calculation
  - Strategy recommendation based on market outlook
  - Scenario analysis (price × IV × time grid)
  - Strategy comparison

Input: JSON via stdin
Output: JSON to stdout

Requirements:
  pip install numpy
  Optional: scipy (for probability calculations)
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Optional

import numpy as np

# Import Greeks engine for calculation
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from greeks_engine import (
    bsm_price,
    calculate_all_greeks,
    calculate_implied_vol,
    days_to_expiry,
    years_to_expiry,
)


# ══════════════════════════════════════════════════════════════════════════════
# Strategy Templates (策略模板)
# ══════════════════════════════════════════════════════════════════════════════

STRATEGY_TEMPLATES = {
    # ── Single-Leg (单腿) ──
    "long_call": {
        "name": "买入看涨 (Long Call)",
        "description": "看涨标的，有限风险（权利金），理论无限收益",
        "outlook": "bullish",
        "vol_outlook": "rising",
        "risk": "limited",
        "legs_template": [
            {"side": "buy_open", "optionType": "call", "strike_offset": 0},
        ],
    },
    "long_put": {
        "name": "买入看跌 (Long Put)",
        "description": "看跌标的，有限风险（权利金），收益至标的归零",
        "outlook": "bearish",
        "vol_outlook": "rising",
        "risk": "limited",
        "legs_template": [
            {"side": "buy_open", "optionType": "put", "strike_offset": 0},
        ],
    },
    "short_call": {
        "name": "卖出看涨 (Short Call / Naked Call)",
        "description": "看跌/中性，收取权利金，风险理论无限",
        "outlook": "bearish",
        "vol_outlook": "falling",
        "risk": "unlimited",
        "legs_template": [
            {"side": "sell_open", "optionType": "call", "strike_offset": 0},
        ],
    },
    "short_put": {
        "name": "卖出看跌 (Short Put)",
        "description": "看涨/中性，收取权利金，风险为标的跌至零",
        "outlook": "bullish",
        "vol_outlook": "falling",
        "risk": "substantial",
        "legs_template": [
            {"side": "sell_open", "optionType": "put", "strike_offset": 0},
        ],
    },

    # ── Two-Leg Spreads (双腿价差) ──
    "covered_call": {
        "name": "备兑开仓 (Covered Call)",
        "description": "持有标的+卖出虚值Call，增强收益，有限下行保护",
        "outlook": "neutral_bullish",
        "vol_outlook": "any",
        "risk": "limited",
        "requires_underlying": True,
        "legs_template": [
            {"side": "sell_open", "optionType": "call", "strike_offset": 1},
        ],
    },
    "protective_put": {
        "name": "保护性看跌 (Protective Put)",
        "description": "持有标的+买入Put，保险策略",
        "outlook": "bullish",
        "vol_outlook": "any",
        "risk": "limited",
        "requires_underlying": True,
        "legs_template": [
            {"side": "buy_open", "optionType": "put", "strike_offset": -1},
        ],
    },
    "bull_call_spread": {
        "name": "牛市看涨价差 (Bull Call Spread)",
        "description": "买低行权Call+卖高行权Call，适度看涨",
        "outlook": "bullish",
        "vol_outlook": "any",
        "risk": "limited",
        "legs_template": [
            {"side": "buy_open", "optionType": "call", "strike_offset": 0},
            {"side": "sell_open", "optionType": "call", "strike_offset": 2},
        ],
    },
    "bear_put_spread": {
        "name": "熊市看跌价差 (Bear Put Spread)",
        "description": "买高行权Put+卖低行权Put，适度看跌",
        "outlook": "bearish",
        "vol_outlook": "any",
        "risk": "limited",
        "legs_template": [
            {"side": "buy_open", "optionType": "put", "strike_offset": 0},
            {"side": "sell_open", "optionType": "put", "strike_offset": -2},
        ],
    },
    "long_straddle": {
        "name": "买入跨式 (Long Straddle)",
        "description": "同行权价买Call+买Put，预期大幅波动",
        "outlook": "volatile",
        "vol_outlook": "rising",
        "risk": "limited",
        "legs_template": [
            {"side": "buy_open", "optionType": "call", "strike_offset": 0},
            {"side": "buy_open", "optionType": "put", "strike_offset": 0},
        ],
    },
    "short_straddle": {
        "name": "卖出跨式 (Short Straddle)",
        "description": "同行权价卖Call+卖Put，预期横盘",
        "outlook": "neutral",
        "vol_outlook": "falling",
        "risk": "unlimited",
        "legs_template": [
            {"side": "sell_open", "optionType": "call", "strike_offset": 0},
            {"side": "sell_open", "optionType": "put", "strike_offset": 0},
        ],
    },
    "long_strangle": {
        "name": "买入宽跨式 (Long Strangle)",
        "description": "买虚值Call+买虚值Put，成本更低的波动策略",
        "outlook": "volatile",
        "vol_outlook": "rising",
        "risk": "limited",
        "legs_template": [
            {"side": "buy_open", "optionType": "call", "strike_offset": 1},
            {"side": "buy_open", "optionType": "put", "strike_offset": -1},
        ],
    },
    "short_strangle": {
        "name": "卖出宽跨式 (Short Strangle)",
        "description": "卖虚值Call+卖虚值Put，收取权利金",
        "outlook": "neutral",
        "vol_outlook": "falling",
        "risk": "unlimited",
        "legs_template": [
            {"side": "sell_open", "optionType": "call", "strike_offset": 1},
            {"side": "sell_open", "optionType": "put", "strike_offset": -1},
        ],
    },

    # ── Three-Leg (三腿) ──
    "collar": {
        "name": "领口策略 (Collar)",
        "description": "持有标的+买低Put+卖高Call，零成本保护",
        "outlook": "neutral_bullish",
        "vol_outlook": "any",
        "risk": "limited",
        "requires_underlying": True,
        "legs_template": [
            {"side": "buy_open", "optionType": "put", "strike_offset": -1},
            {"side": "sell_open", "optionType": "call", "strike_offset": 1},
        ],
    },

    # ── Four-Leg (四腿) ──
    "iron_condor": {
        "name": "铁鹰式 (Iron Condor)",
        "description": "卖虚值Call+卖虚值Put，双翼保护，区间收益",
        "outlook": "neutral",
        "vol_outlook": "falling",
        "risk": "limited",
        "legs_template": [
            {"side": "buy_open", "optionType": "put", "strike_offset": -3},
            {"side": "sell_open", "optionType": "put", "strike_offset": -1},
            {"side": "sell_open", "optionType": "call", "strike_offset": 1},
            {"side": "buy_open", "optionType": "call", "strike_offset": 3},
        ],
    },
    "iron_butterfly": {
        "name": "铁蝶式 (Iron Butterfly)",
        "description": "ATM同行权价卖Call+卖Put，双翼保护",
        "outlook": "neutral",
        "vol_outlook": "falling",
        "risk": "limited",
        "legs_template": [
            {"side": "buy_open", "optionType": "put", "strike_offset": -2},
            {"side": "sell_open", "optionType": "put", "strike_offset": 0},
            {"side": "sell_open", "optionType": "call", "strike_offset": 0},
            {"side": "buy_open", "optionType": "call", "strike_offset": 2},
        ],
    },
    "box_spread": {
        "name": "盒式价差 (Box Spread)",
        "description": "牛市Call Spread + 熊市Put Spread，套利策略",
        "outlook": "neutral",
        "vol_outlook": "any",
        "risk": "limited",
        "legs_template": [
            {"side": "buy_open", "optionType": "call", "strike_offset": -1},
            {"side": "sell_open", "optionType": "call", "strike_offset": 1},
            {"side": "buy_open", "optionType": "put", "strike_offset": 1},
            {"side": "sell_open", "optionType": "put", "strike_offset": -1},
        ],
    },

    # ── Calendar/Diagonal Spreads (时间价差) ──
    "calendar_spread": {
        "name": "日历价差 (Calendar Spread)",
        "description": "卖近月+买远月同行权价，利用时间衰减差异",
        "outlook": "neutral",
        "vol_outlook": "rising",
        "risk": "limited",
        "multi_expiry": True,
        "legs_template": [
            {"side": "sell_open", "optionType": "call", "strike_offset": 0, "expiry_idx": 0},
            {"side": "buy_open", "optionType": "call", "strike_offset": 0, "expiry_idx": 1},
        ],
    },
    "diagonal_spread": {
        "name": "对角价差 (Diagonal Spread)",
        "description": "不同到期+不同行权价，灵活方向性策略",
        "outlook": "neutral_bullish",
        "vol_outlook": "any",
        "risk": "limited",
        "multi_expiry": True,
        "legs_template": [
            {"side": "sell_open", "optionType": "call", "strike_offset": 1, "expiry_idx": 0},
            {"side": "buy_open", "optionType": "call", "strike_offset": 0, "expiry_idx": 1},
        ],
    },

    # ── Ratio Spreads (比率价差) ──
    "ratio_call_spread": {
        "name": "看涨比率价差 (Ratio Call Spread)",
        "description": "买1低Call+卖2高Call，适度看涨",
        "outlook": "neutral_bullish",
        "vol_outlook": "falling",
        "risk": "unlimited_upside",
        "legs_template": [
            {"side": "buy_open", "optionType": "call", "strike_offset": 0, "qty_ratio": 1},
            {"side": "sell_open", "optionType": "call", "strike_offset": 2, "qty_ratio": 2},
        ],
    },
    "ratio_put_spread": {
        "name": "看跌比率价差 (Ratio Put Spread)",
        "description": "买1高Put+卖2低Put，适度看跌",
        "outlook": "neutral_bearish",
        "vol_outlook": "falling",
        "risk": "unlimited_downside",
        "legs_template": [
            {"side": "buy_open", "optionType": "put", "strike_offset": 0, "qty_ratio": 1},
            {"side": "sell_open", "optionType": "put", "strike_offset": -2, "qty_ratio": 2},
        ],
    },
    "call_backspread": {
        "name": "看涨反比率价差 (Call Backspread)",
        "description": "卖1低Call+买2高Call，大涨获利",
        "outlook": "bullish",
        "vol_outlook": "rising",
        "risk": "limited",
        "legs_template": [
            {"side": "sell_open", "optionType": "call", "strike_offset": 0, "qty_ratio": 1},
            {"side": "buy_open", "optionType": "call", "strike_offset": 2, "qty_ratio": 2},
        ],
    },
    "put_backspread": {
        "name": "看跌反比率价差 (Put Backspread)",
        "description": "卖1高Put+买2低Put，大跌获利",
        "outlook": "bearish",
        "vol_outlook": "rising",
        "risk": "limited",
        "legs_template": [
            {"side": "sell_open", "optionType": "put", "strike_offset": 0, "qty_ratio": 1},
            {"side": "buy_open", "optionType": "put", "strike_offset": -2, "qty_ratio": 2},
        ],
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# Payoff Calculation
# ══════════════════════════════════════════════════════════════════════════════

def calculate_payoff(
    legs: list[dict],
    underlying_price: float,
    price_range: Optional[tuple[float, float]] = None,
    num_points: int = 100,
) -> dict:
    """Calculate option strategy payoff at expiry.

    Args:
        legs: List of leg dicts, each with:
            side, optionType, strike, price (premium), quantity
        underlying_price: Current underlying price
        price_range: (min, max) price range for payoff curve
        num_points: Number of points in payoff curve
    """
    if not legs:
        return {"error": "No legs provided"}

    # Determine price range
    strikes = [leg["strike"] for leg in legs if "strike" in leg]
    if not strikes:
        return {"error": "No strikes in legs"}

    min_strike = min(strikes)
    max_strike = max(strikes)
    spread = max_strike - min_strike if max_strike > min_strike else underlying_price * 0.1

    if price_range:
        p_min, p_max = price_range
    else:
        p_min = max(min_strike - spread * 2, underlying_price * 0.7)
        p_max = max_strike + spread * 2

    prices = np.linspace(p_min, p_max, num_points)

    # Calculate net premium
    net_premium = 0.0
    for leg in legs:
        premium = leg.get("price", 0)
        qty = leg.get("quantity", 1)
        multiplier = leg.get("contractMultiplier", 1)
        side = leg.get("side", "buy_open")
        if "buy" in side:
            net_premium -= premium * qty * multiplier
        else:
            net_premium += premium * qty * multiplier

    # Calculate payoff at each price point
    payoff_curve = []
    for S in prices:
        pnl = net_premium  # Start with premium paid/received
        for leg in legs:
            strike = leg["strike"]
            qty = leg.get("quantity", 1)
            multiplier = leg.get("contractMultiplier", 1)
            opt_type = leg.get("optionType", "call")
            side = leg.get("side", "buy_open")

            # Intrinsic value at expiry
            if opt_type == "call":
                intrinsic = max(S - strike, 0)
            else:
                intrinsic = max(strike - S, 0)

            if "buy" in side:
                pnl += intrinsic * qty * multiplier
            else:
                pnl -= intrinsic * qty * multiplier

        payoff_curve.append({"price": round(float(S), 4), "pnl": round(float(pnl), 2)})

    # Find breakevens (where PnL crosses zero)
    breakevens = []
    for i in range(1, len(payoff_curve)):
        prev_pnl = payoff_curve[i - 1]["pnl"]
        curr_pnl = payoff_curve[i]["pnl"]
        if prev_pnl * curr_pnl < 0:  # sign change
            # Linear interpolation
            prev_price = payoff_curve[i - 1]["price"]
            curr_price = payoff_curve[i]["price"]
            ratio = abs(prev_pnl) / (abs(prev_pnl) + abs(curr_pnl))
            be = prev_price + ratio * (curr_price - prev_price)
            breakevens.append(round(be, 4))

    # Max profit and loss
    pnls = [p["pnl"] for p in payoff_curve]
    max_profit = max(pnls)
    max_loss = min(pnls)

    # Check if unlimited (at boundaries)
    if payoff_curve[-1]["pnl"] > payoff_curve[-2]["pnl"]:
        max_profit = float("inf")
    if payoff_curve[0]["pnl"] < payoff_curve[1]["pnl"]:
        max_loss_check = payoff_curve[0]["pnl"]
        if max_loss_check < max_loss:
            max_loss = float("-inf") if max_loss_check < -underlying_price * 10 else max_loss

    return {
        "netPremium": round(net_premium, 2),
        "maxProfit": max_profit if max_profit != float("inf") else "unlimited",
        "maxLoss": max_loss if max_loss != float("-inf") else "unlimited",
        "breakevens": breakevens,
        "payoffCurve": payoff_curve,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Strategy Recommendation
# ══════════════════════════════════════════════════════════════════════════════

def recommend_strategies(
    outlook: str,
    vol_outlook: str = "any",
    risk_tolerance: str = "medium",
    has_underlying: bool = False,
) -> list[dict]:
    """Recommend option strategies based on market outlook.

    Args:
        outlook: bullish, bearish, neutral, volatile
        vol_outlook: rising, falling, any
        risk_tolerance: low, medium, high
        has_underlying: whether the user holds the underlying
    """
    outlook_map = {
        "bullish": ["bullish", "neutral_bullish"],
        "bearish": ["bearish", "neutral_bearish"],
        "neutral": ["neutral", "neutral_bullish", "neutral_bearish"],
        "volatile": ["volatile"],
    }
    target_outlooks = outlook_map.get(outlook, [outlook])

    candidates = []
    for key, template in STRATEGY_TEMPLATES.items():
        # Match outlook
        if template.get("outlook") not in target_outlooks:
            continue

        # Match volatility outlook
        tmpl_vol = template.get("vol_outlook", "any")
        if vol_outlook != "any" and tmpl_vol != "any" and tmpl_vol != vol_outlook:
            continue

        # Filter by risk tolerance
        tmpl_risk = template.get("risk", "limited")
        if risk_tolerance == "low" and tmpl_risk in ("unlimited", "unlimited_upside", "unlimited_downside", "substantial"):
            continue
        if risk_tolerance == "medium" and tmpl_risk in ("unlimited", "unlimited_upside", "unlimited_downside"):
            continue

        # Filter strategies requiring underlying
        if template.get("requires_underlying") and not has_underlying:
            continue

        # Score the strategy
        score = 0
        if tmpl_vol == vol_outlook:
            score += 2
        if tmpl_risk == "limited":
            score += 1

        candidates.append({
            "id": key,
            "name": template["name"],
            "description": template["description"],
            "outlook": template["outlook"],
            "volOutlook": tmpl_vol,
            "risk": tmpl_risk,
            "numLegs": len(template.get("legs_template", [])),
            "requiresUnderlying": template.get("requires_underlying", False),
            "multiExpiry": template.get("multi_expiry", False),
            "score": score,
        })

    # Sort by score descending
    candidates.sort(key=lambda x: x["score"], reverse=True)
    return candidates


# ══════════════════════════════════════════════════════════════════════════════
# Construct Strategy Legs from Template
# ══════════════════════════════════════════════════════════════════════════════

def construct_legs(
    template_id: str,
    underlying_price: float,
    strikes: list[float],
    expiry: str,
    expiry2: Optional[str] = None,
    quantity: int = 1,
    premiums: Optional[dict[str, float]] = None,
    contract_multiplier: int = 10000,
) -> list[dict]:
    """Construct concrete legs from a strategy template.

    Args:
        template_id: Strategy template identifier
        underlying_price: Current underlying price
        strikes: Available sorted strike prices
        expiry: Expiry date (YYYY-MM-DD) for the strategy
        expiry2: Second expiry for calendar/diagonal spreads
        quantity: Base lot quantity
        premiums: Dict of "strike_optionType" -> premium for pricing
        contract_multiplier: Option contract multiplier
    """
    template = STRATEGY_TEMPLATES.get(template_id)
    if not template:
        return []

    # Find ATM strike (closest to underlying price)
    atm_idx = _find_nearest_strike_idx(strikes, underlying_price)
    if atm_idx is None:
        return []

    legs = []
    for leg_spec in template.get("legs_template", []):
        offset = leg_spec.get("strike_offset", 0)
        target_idx = atm_idx + offset

        # Clamp to valid range
        target_idx = max(0, min(len(strikes) - 1, target_idx))
        strike = strikes[target_idx]

        # Expiry selection for multi-expiry strategies
        leg_expiry = expiry
        if leg_spec.get("expiry_idx", 0) == 1 and expiry2:
            leg_expiry = expiry2

        # Quantity ratio
        qty_ratio = leg_spec.get("qty_ratio", 1)

        # Look up premium if available
        premium_key = f"{strike}_{leg_spec['optionType']}"
        premium = premiums.get(premium_key, 0.0) if premiums else 0.0

        legs.append({
            "side": leg_spec["side"],
            "optionType": leg_spec["optionType"],
            "strike": strike,
            "expiry": leg_expiry,
            "quantity": quantity * qty_ratio,
            "price": premium,
            "contractMultiplier": contract_multiplier,
        })

    return legs


def _find_nearest_strike_idx(strikes: list[float], target: float) -> Optional[int]:
    """Find the index of the strike closest to target price."""
    if not strikes:
        return None
    min_diff = float("inf")
    best_idx = 0
    for i, s in enumerate(strikes):
        diff = abs(s - target)
        if diff < min_diff:
            min_diff = diff
            best_idx = i
    return best_idx


# ══════════════════════════════════════════════════════════════════════════════
# Greeks Aggregation
# ══════════════════════════════════════════════════════════════════════════════

def aggregate_greeks(
    legs: list[dict],
    underlying_price: float,
    r: float = 0.02,
    q: float = 0.0,
) -> dict:
    """Calculate aggregate Greeks for a multi-leg position."""
    total = {"delta": 0.0, "gamma": 0.0, "theta": 0.0, "vega": 0.0, "rho": 0.0}

    for leg in legs:
        strike = leg.get("strike", 0)
        expiry = leg.get("expiry", "")
        opt_type = leg.get("optionType", "call")
        qty = leg.get("quantity", 1)
        multiplier = leg.get("contractMultiplier", 1)
        side = leg.get("side", "buy_open")
        sigma = leg.get("impliedVol", 0.25)

        T = years_to_expiry(expiry) if expiry else 0.25

        if strike > 0 and underlying_price > 0 and T > 0:
            greeks = calculate_all_greeks(underlying_price, strike, T, r, sigma, opt_type, q)
            sign = 1 if "buy" in side else -1
            total["delta"] += greeks["delta"] * qty * multiplier * sign
            total["gamma"] += greeks["gamma"] * qty * multiplier * sign
            total["theta"] += greeks["theta"] * qty * multiplier * sign
            total["vega"] += greeks["vega"] * qty * multiplier * sign
            total["rho"] += greeks["rho"] * qty * multiplier * sign

    return {k: round(v, 4) for k, v in total.items()}


# ══════════════════════════════════════════════════════════════════════════════
# Scenario Analysis
# ══════════════════════════════════════════════════════════════════════════════

def scenario_analysis(
    legs: list[dict],
    underlying_price: float,
    r: float = 0.02,
    q: float = 0.0,
    price_range_pct: float = 0.2,
    price_steps: int = 11,
    days_steps: list[int] | None = None,
    iv_shifts: list[float] | None = None,
) -> dict:
    """Multi-dimensional scenario analysis.

    Varies: underlying price × time decay × IV change.
    Returns a grid of P&L values.
    """
    if days_steps is None:
        days_steps = [0, 7, 14, 30]
    if iv_shifts is None:
        iv_shifts = [-0.05, 0, 0.05]

    # Price range around current underlying
    p_min = underlying_price * (1 - price_range_pct)
    p_max = underlying_price * (1 + price_range_pct)
    price_points = np.linspace(p_min, p_max, price_steps)

    # Current position value
    current_value = 0
    for leg in legs:
        premium = leg.get("price", 0)
        qty = leg.get("quantity", 1)
        multiplier = leg.get("contractMultiplier", 1)
        side = leg.get("side", "buy_open")
        if "buy" in side:
            current_value -= premium * qty * multiplier
        else:
            current_value += premium * qty * multiplier

    scenarios = []
    for days_forward in days_steps:
        for iv_shift in iv_shifts:
            row = {
                "daysForward": days_forward,
                "ivShift": iv_shift,
                "prices": [],
            }
            for S_new in price_points:
                total_value = 0
                for leg in legs:
                    strike = leg.get("strike", 0)
                    expiry = leg.get("expiry", "")
                    opt_type = leg.get("optionType", "call")
                    qty = leg.get("quantity", 1)
                    multiplier = leg.get("contractMultiplier", 1)
                    side = leg.get("side", "buy_open")
                    sigma = leg.get("impliedVol", 0.25) + iv_shift

                    if sigma <= 0.01:
                        sigma = 0.01

                    # Adjust T for days forward
                    dte = days_to_expiry(expiry) if expiry else 90
                    T_new = max((dte - days_forward) / 365.0, 0.001)

                    if T_new <= 0.001:
                        # At expiry — intrinsic value
                        if opt_type == "call":
                            leg_value = max(float(S_new) - strike, 0)
                        else:
                            leg_value = max(strike - float(S_new), 0)
                    else:
                        leg_value = bsm_price(float(S_new), strike, T_new, r, sigma, opt_type, q)

                    sign = 1 if "buy" in side else -1
                    total_value += leg_value * qty * multiplier * sign

                pnl = total_value + current_value  # Net P&L
                row["prices"].append({
                    "underlyingPrice": round(float(S_new), 2),
                    "positionValue": round(total_value, 2),
                    "pnl": round(pnl, 2),
                })

            scenarios.append(row)

    return {
        "currentUnderlyingPrice": underlying_price,
        "currentValue": round(current_value, 2),
        "scenarios": scenarios,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Strategy Comparison
# ══════════════════════════════════════════════════════════════════════════════

def compare_strategies(
    strategies: list[dict],
    underlying_price: float,
    r: float = 0.02,
    q: float = 0.0,
) -> list[dict]:
    """Compare multiple strategies head to head.

    Each strategy dict has: name, legs[]
    Returns comparison with payoff, greeks, risk metrics.
    """
    results = []
    for strategy in strategies:
        name = strategy.get("name", "unnamed")
        legs = strategy.get("legs", [])

        payoff = calculate_payoff(legs, underlying_price)
        greeks = aggregate_greeks(legs, underlying_price, r, q)

        results.append({
            "name": name,
            "netPremium": payoff.get("netPremium", 0),
            "maxProfit": payoff.get("maxProfit", 0),
            "maxLoss": payoff.get("maxLoss", 0),
            "breakevens": payoff.get("breakevens", []),
            "greeks": greeks,
            "numLegs": len(legs),
        })

    return results


# ══════════════════════════════════════════════════════════════════════════════
# Optimal Strike Selection
# ══════════════════════════════════════════════════════════════════════════════

def find_optimal_strike(
    strikes: list[float],
    underlying_price: float,
    option_type: str,
    criteria: str = "delta",
    target_delta: float = 0.3,
    expiry: str = "",
    r: float = 0.02,
    sigma: float = 0.25,
    q: float = 0.0,
) -> dict:
    """Find optimal strike based on criteria.

    Criteria:
        delta       — closest to target delta
        atm         — closest to ATM
        liquidity   — placeholder (needs volume data)
        risk_reward — best risk/reward ratio
    """
    T = years_to_expiry(expiry) if expiry else 0.25

    if criteria == "atm":
        idx = _find_nearest_strike_idx(strikes, underlying_price)
        return {"strike": strikes[idx] if idx is not None else 0, "criteria": "atm"}

    elif criteria == "delta":
        best_strike = strikes[0] if strikes else 0
        best_diff = float("inf")
        best_delta = 0

        for strike in strikes:
            greeks = calculate_all_greeks(underlying_price, strike, T, r, sigma, option_type, q)
            delta = abs(greeks["delta"])
            diff = abs(delta - abs(target_delta))
            if diff < best_diff:
                best_diff = diff
                best_strike = strike
                best_delta = greeks["delta"]

        return {
            "strike": best_strike,
            "delta": round(best_delta, 4),
            "targetDelta": target_delta,
            "criteria": "delta",
        }

    return {"strike": 0, "criteria": criteria, "error": f"Unsupported criteria: {criteria}"}


# ══════════════════════════════════════════════════════════════════════════════
# Main Dispatcher
# ══════════════════════════════════════════════════════════════════════════════

def run_option_strategy(config: dict) -> dict:
    """Main entry point for option strategy analysis.

    Actions:
        recommend     — recommend strategies by outlook
        payoff        — calculate payoff diagram
        construct     — build legs from template
        greeks        — aggregate Greeks for multi-leg position
        scenario      — scenario analysis grid
        compare       — compare multiple strategies
        optimal_strike — find optimal strike
        list_templates — list all available strategy templates
    """
    action = config.get("action", "recommend")

    try:
        if action == "recommend":
            outlook = config.get("outlook", "neutral")
            vol_outlook = config.get("volOutlook", "any")
            risk_tolerance = config.get("riskTolerance", "medium")
            has_underlying = config.get("hasUnderlying", False)

            strategies = recommend_strategies(outlook, vol_outlook, risk_tolerance, has_underlying)
            return {"ok": True, "data": strategies}

        elif action == "payoff":
            legs = config.get("legs", [])
            underlying_price = float(config.get("underlyingPrice", 0))
            if not legs or underlying_price <= 0:
                return {"ok": False, "error": "legs and underlyingPrice are required"}

            result = calculate_payoff(legs, underlying_price)
            return {"ok": True, "data": result}

        elif action == "construct":
            template_id = config.get("templateId", "")
            underlying_price = float(config.get("underlyingPrice", 0))
            strikes = config.get("strikes", [])
            expiry = config.get("expiry", "")
            expiry2 = config.get("expiry2")
            quantity = int(config.get("quantity", 1))
            premiums = config.get("premiums", {})
            multiplier = int(config.get("contractMultiplier", 10000))

            if not template_id or not strikes:
                return {"ok": False, "error": "templateId and strikes are required"}

            legs = construct_legs(
                template_id, underlying_price, sorted(strikes),
                expiry, expiry2, quantity, premiums, multiplier,
            )
            if not legs:
                return {"ok": False, "error": f"Failed to construct legs for {template_id}"}

            # Calculate payoff for the constructed legs
            payoff = calculate_payoff(legs, underlying_price)

            return {"ok": True, "data": {
                "templateId": template_id,
                "templateName": STRATEGY_TEMPLATES.get(template_id, {}).get("name", ""),
                "legs": legs,
                "payoff": payoff,
            }}

        elif action == "greeks":
            legs = config.get("legs", [])
            underlying_price = float(config.get("underlyingPrice", 0))
            r = float(config.get("riskFreeRate", 0.02))
            q = float(config.get("dividendYield", 0.0))

            if not legs or underlying_price <= 0:
                return {"ok": False, "error": "legs and underlyingPrice are required"}

            result = aggregate_greeks(legs, underlying_price, r, q)
            return {"ok": True, "data": result}

        elif action == "scenario":
            legs = config.get("legs", [])
            underlying_price = float(config.get("underlyingPrice", 0))
            r = float(config.get("riskFreeRate", 0.02))
            q = float(config.get("dividendYield", 0.0))
            price_range_pct = float(config.get("priceRangePct", 0.2))

            # Auto-construct legs from templateId when legs not provided
            if not legs:
                template_id = config.get("templateId", "")
                if template_id and underlying_price > 0:
                    # Generate strike grid around underlying price (±10%, 21 steps)
                    step = underlying_price * 0.01
                    auto_strikes = [round(underlying_price + (i - 10) * step, 4) for i in range(21)]
                    legs = construct_legs(template_id, underlying_price, auto_strikes,
                                          config.get("expiry", ""), quantity=1)

            if not legs or underlying_price <= 0:
                return {"ok": False, "error": "legs (or templateId) and underlyingPrice are required"}

            result = scenario_analysis(legs, underlying_price, r, q, price_range_pct)
            return {"ok": True, "data": result}

        elif action == "compare":
            raw_strategies = config.get("strategies", [])
            underlying_price = float(config.get("underlyingPrice", 0))
            r = float(config.get("riskFreeRate", 0.02))
            q = float(config.get("dividendYield", 0.0))

            if not raw_strategies or underlying_price <= 0:
                return {"ok": False, "error": "strategies and underlyingPrice are required"}

            # Resolve string IDs to {name, legs} dicts
            strategies = []
            for s in raw_strategies:
                if isinstance(s, str):
                    tmpl = STRATEGY_TEMPLATES.get(s)
                    if not tmpl:
                        return {"ok": False, "error": f"Unknown strategy template: {s}"}
                    step = underlying_price * 0.01
                    auto_strikes = [round(underlying_price + (i - 10) * step, 4) for i in range(21)]
                    legs = construct_legs(s, underlying_price, auto_strikes,
                                          config.get("expiry", ""), quantity=1)
                    strategies.append({"name": tmpl["name"], "legs": legs})
                else:
                    strategies.append(s)

            result = compare_strategies(strategies, underlying_price, r, q)
            return {"ok": True, "data": result}

        elif action == "optimal_strike":
            strikes = config.get("strikes", [])
            underlying_price = float(config.get("underlyingPrice", 0))
            option_type = config.get("optionType", "call")
            criteria = config.get("criteria", "delta")
            target_delta = float(config.get("targetDelta", 0.3))
            expiry = config.get("expiry", "")
            r = float(config.get("riskFreeRate", 0.02))
            sigma = float(config.get("volatility", 0.25))

            # Auto-generate strike grid if not provided
            if not strikes and underlying_price > 0:
                step = underlying_price * 0.01
                strikes = [round(underlying_price + (i - 10) * step, 4) for i in range(21)]

            if not strikes or underlying_price <= 0:
                return {"ok": False, "error": "strikes and underlyingPrice are required"}

            result = find_optimal_strike(
                sorted(strikes), underlying_price, option_type,
                criteria, target_delta, expiry, r, sigma,
            )
            return {"ok": True, "data": result}

        elif action == "list_templates":
            templates = []
            for key, tmpl in STRATEGY_TEMPLATES.items():
                templates.append({
                    "id": key,
                    "name": tmpl["name"],
                    "description": tmpl["description"],
                    "outlook": tmpl["outlook"],
                    "volOutlook": tmpl.get("vol_outlook", "any"),
                    "risk": tmpl.get("risk", "limited"),
                    "numLegs": len(tmpl.get("legs_template", [])),
                    "requiresUnderlying": tmpl.get("requires_underlying", False),
                    "multiExpiry": tmpl.get("multi_expiry", False),
                })
            return {"ok": True, "data": templates}

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
        result = run_option_strategy(cfg)
        print(json.dumps(result, ensure_ascii=False, default=str))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        sys.exit(1)
