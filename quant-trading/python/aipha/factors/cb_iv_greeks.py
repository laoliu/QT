"""CB Implied Volatility & Greeks module (Phase 3 / C1).

BSM-based pricing decomposition for Chinese convertible bonds:
    V_cb ≈ V_bond_floor + R * BSM_call(S, K, T, r, σ)
where R = 100/K (conversion ratio).

IV is solved by inverting: V_option = V_market - V_bond_floor.

References:
    - Tsiveriotis & Fernandes (1998) CB decomposition
    - docs/knowledge/china_market/cb_iv_option_pricing_research.md §4
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.optimize import brentq
from scipy.stats import norm

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_SIGMA_MIN = 0.01
_SIGMA_MAX = 2.0
_IV_TOL = 1e-6
_IV_MAX_ITER = 100
_IV_ANOMALY_THRESHOLD = 1.5  # σ > 1.5 flagged as anomaly

# Default credit spreads by rating (bp)
CREDIT_SPREAD_MAP: dict[str, float] = {
    "AAA": 0.0050,
    "AA+": 0.0080,
    "AA": 0.0150,
    "AA-": 0.0200,
    "A+": 0.0300,
    "A": 0.0400,
}
DEFAULT_CREDIT_SPREAD = 0.0150  # AA equivalent
DEFAULT_RISK_FREE_RATE = 0.02
DEFAULT_DIVIDEND_YIELD = 0.005

# Typical CB coupon schedule (progressive, 6-year)
DEFAULT_COUPON_SCHEDULE = [0.003, 0.005, 0.010, 0.015, 0.018, 0.020]
DEFAULT_COMPENSATION = 0.006  # 到期补偿利率


# ---------------------------------------------------------------------------
# Bond floor calculation
# ---------------------------------------------------------------------------
def bond_floor(
    T: float,
    r: float = DEFAULT_RISK_FREE_RATE,
    spread: float = DEFAULT_CREDIT_SPREAD,
    coupons: list[float] | None = None,
    compensation: float = DEFAULT_COMPENSATION,
    face: float = 100.0,
) -> float:
    """Calculate bond floor (pure debt value) of a CB.

    Args:
        T: Years to maturity.
        r: Risk-free rate (annualized).
        spread: Credit spread (annualized).
        coupons: Annual coupon rates. If None, uses default schedule.
        compensation: End-of-term compensation rate.
        face: Face value (default 100).

    Returns:
        Bond floor value in same units as face.
    """
    if T <= 0:
        return face
    discount_rate = r + spread
    if coupons is None:
        coupons = DEFAULT_COUPON_SCHEDULE
    n_years = max(1, int(math.ceil(T)))
    pv = 0.0
    for i in range(n_years):
        yr = i + 1
        coupon_rate = coupons[min(i, len(coupons) - 1)]
        cf = face * coupon_rate
        if yr == n_years:
            cf += face * (1 + compensation)
        t_i = min(yr, T)
        pv += cf / (1 + discount_rate) ** t_i
    return pv


# ---------------------------------------------------------------------------
# BSM European call pricing
# ---------------------------------------------------------------------------
def bsm_call_price(
    S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0,
) -> float:
    """Black-Scholes-Merton European call option price.

    Args:
        S: Underlying stock price.
        K: Strike (conversion) price.
        T: Time to maturity in years.
        r: Risk-free rate.
        sigma: Volatility (annualized).
        q: Continuous dividend yield.

    Returns:
        Call option price. Returns 0 if T <= 0 or invalid inputs.
    """
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    return S * math.exp(-q * T) * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)


def cb_theoretical_price(
    S: float, K: float, T: float, r: float, sigma: float,
    q: float = 0.0, spread: float = DEFAULT_CREDIT_SPREAD,
    coupons: list[float] | None = None,
    compensation: float = DEFAULT_COMPENSATION,
) -> float:
    """Theoretical CB price = bond_floor + R * BSM_call."""
    bf = bond_floor(T, r, spread, coupons, compensation)
    R = 100.0 / K  # conversion ratio per 100 face
    call = bsm_call_price(S, K, T, r, sigma, q)
    return bf + R * call


# ---------------------------------------------------------------------------
# Implied Volatility solver
# ---------------------------------------------------------------------------
def implied_vol(
    market_price: float, S: float, K: float, T: float, r: float,
    q: float = 0.0, spread: float = DEFAULT_CREDIT_SPREAD,
    coupons: list[float] | None = None,
    compensation: float = DEFAULT_COMPENSATION,
) -> float:
    """Solve implied volatility from CB market price using Brent method.

    Returns:
        Implied volatility, or NaN if no solution exists.
    """
    if T <= 0 or S <= 0 or K <= 0 or market_price <= 0:
        return float("nan")
    bf = bond_floor(T, r, spread, coupons, compensation)
    option_value = market_price - bf
    if option_value <= 0:
        return float("nan")  # CB trading below bond floor → no option value
    R = 100.0 / K

    def objective(sigma: float) -> float:
        return R * bsm_call_price(S, K, T, r, sigma, q) - option_value

    # Check bounds
    try:
        lo = objective(_SIGMA_MIN)
        hi = objective(_SIGMA_MAX)
    except (ValueError, OverflowError):
        return float("nan")
    if lo * hi > 0:
        return float("nan")  # No root in [σ_min, σ_max]
    try:
        iv = brentq(objective, _SIGMA_MIN, _SIGMA_MAX,
                     xtol=_IV_TOL, maxiter=_IV_MAX_ITER)
        return iv
    except (ValueError, RuntimeError):
        return float("nan")


# ---------------------------------------------------------------------------
# Greeks
# ---------------------------------------------------------------------------
def bsm_delta(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """BSM Delta = ∂C/∂S."""
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return math.exp(-q * T) * norm.cdf(d1)

def bsm_gamma(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """BSM Gamma = ∂²C/∂S²."""
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return math.exp(-q * T) * norm.pdf(d1) / (S * sigma * math.sqrt(T))

def bsm_vega(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """BSM Vega = ∂C/∂σ (per 1% vol change)."""
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return S * math.exp(-q * T) * norm.pdf(d1) * math.sqrt(T) * 0.01

def bsm_theta(S: float, K: float, T: float, r: float, sigma: float, q: float = 0.0) -> float:
    """BSM Theta = -∂C/∂T (daily decay, per calendar day)."""
    if T <= 0 or S <= 0 or K <= 0 or sigma <= 0:
        return 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    term1 = -S * math.exp(-q * T) * norm.pdf(d1) * sigma / (2 * math.sqrt(T))
    term2 = r * K * math.exp(-r * T) * norm.cdf(d2)
    term3 = -q * S * math.exp(-q * T) * norm.cdf(d1)
    return (term1 - term2 + term3) / 365.0


# ---------------------------------------------------------------------------
# Historical volatility helper
# ---------------------------------------------------------------------------
def historical_vol(
    returns: list[float] | np.ndarray, annualize: int = TRADING_DAYS_PER_YEAR,
) -> float:
    """Annualized historical volatility from daily log returns."""
    if len(returns) < 10:
        return float("nan")
    arr = np.asarray(returns, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) < 10:
        return float("nan")
    return float(np.std(arr, ddof=1) * math.sqrt(annualize))


# ---------------------------------------------------------------------------
# IV Factor dataclass
# ---------------------------------------------------------------------------
@dataclass
class CBIVFactors:
    """Container for CB IV/Greeks factor values."""

    symbol: str
    iv: float  # implied volatility
    hist_vol: float  # historical volatility
    iv_prem: float  # IV premium = (IV - hist_vol) / hist_vol
    ivpr: float  # IV percentile rank (0-1) over lookback window
    pricing_bias: float  # (market - theoretical) / theoretical
    delta: float
    gamma: float
    vega: float
    theta: float

    def as_dict(self) -> dict[str, float]:
        """Return factor values as dict."""
        return {
            "iv": self.iv,
            "hist_vol": self.hist_vol,
            "iv_prem": self.iv_prem,
            "ivpr": self.ivpr,
            "pricing_bias": self.pricing_bias,
            "delta": self.delta,
            "gamma": self.gamma,
            "vega": self.vega,
            "theta": self.theta,
        }


# ---------------------------------------------------------------------------
# Compute IV factors for a single CB
# ---------------------------------------------------------------------------
def compute_cb_iv_factors(
    symbol: str,
    cb_price: float,
    stock_price: float,
    conversion_price: float,
    T: float,
    stock_returns: list[float] | np.ndarray | None = None,
    iv_history: list[float] | None = None,
    r: float = DEFAULT_RISK_FREE_RATE,
    q: float = DEFAULT_DIVIDEND_YIELD,
    spread: float = DEFAULT_CREDIT_SPREAD,
    rating: str | None = None,
) -> CBIVFactors | None:
    """Compute IV, Greeks, and derived factors for a single CB.

    Args:
        symbol: CB ts_code.
        cb_price: CB market price.
        stock_price: Underlying stock price (S).
        conversion_price: Conversion price (K).
        T: Years to maturity.
        stock_returns: Daily log returns for hist_vol (≥60 days preferred).
        iv_history: Past IV values for IVPR calculation.
        r: Risk-free rate.
        q: Dividend yield.
        spread: Credit spread. If None, derived from rating.
        rating: CB credit rating (e.g. "AA+") for spread lookup.

    Returns:
        CBIVFactors or None if IV cannot be computed.
    """
    if rating and rating in CREDIT_SPREAD_MAP:
        spread = CREDIT_SPREAD_MAP[rating]
    if cb_price <= 0 or stock_price <= 0 or conversion_price <= 0 or T <= 0:
        return None

    # 1. Implied volatility
    iv = implied_vol(cb_price, stock_price, conversion_price, T, r, q, spread)
    if not math.isfinite(iv):
        return None

    # 2. Historical volatility
    hist_vol = float("nan")
    if stock_returns is not None:
        hist_vol = historical_vol(stock_returns)

    # 3. IV premium
    iv_prem = float("nan")
    if math.isfinite(hist_vol) and hist_vol > 0.01:
        iv_prem = (iv - hist_vol) / hist_vol

    # 4. IVPR (percentile rank)
    ivpr = float("nan")
    if iv_history and len(iv_history) >= 20:
        valid = [v for v in iv_history if math.isfinite(v)]
        if valid:
            ivpr = sum(1 for v in valid if v <= iv) / len(valid)

    # 5. Pricing bias
    theo = cb_theoretical_price(
        stock_price, conversion_price, T, r, iv, q, spread,
    )
    pricing_bias = (cb_price - theo) / theo if theo > 0 else float("nan")

    # 6. Greeks (with conversion ratio adjustment)
    R = 100.0 / conversion_price
    delta = R * bsm_delta(stock_price, conversion_price, T, r, iv, q)
    gamma = R * bsm_gamma(stock_price, conversion_price, T, r, iv, q)
    vega = R * bsm_vega(stock_price, conversion_price, T, r, iv, q)
    theta = R * bsm_theta(stock_price, conversion_price, T, r, iv, q)

    return CBIVFactors(
        symbol=symbol, iv=iv, hist_vol=hist_vol,
        iv_prem=iv_prem, ivpr=ivpr, pricing_bias=pricing_bias,
        delta=delta, gamma=gamma, vega=vega, theta=theta,
    )
