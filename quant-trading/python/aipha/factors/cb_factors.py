"""可转债专属因子 — 8 因子体系 (5截面 + 3时序)

基于 AlphQ 的可转债因子框架：

截面因子（无需历史）:
1. iv_bias — 隐含波动率偏差（时间价值效率）
2. gamma_convexity — Gamma 凸性价值
3. credit_spread — 信用利差
4. liquidity_activity — 流动性活跃度
5. maturity_convexity — 到期凸性

时序因子（需历史数据）:
6. delta_accel — Delta 加速度
7. credit_spread_mom — 信用利差动量
8. liquidity_shock — 流动性突变
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_RISK_FREE_RATE: float = 0.02
DEFAULT_PAR_VALUE: float = 100.0
DEFAULT_MATURITY_SWEET_SPOT: float = 3.0
DEFAULT_MATURITY_HALF_RANGE: float = 3.0


@dataclass
class CBFactorValues:
    """单只 CB 的因子计算结果"""
    symbol: str
    iv_bias: float = 0.5
    gamma_convexity: float = 0.0
    credit_spread: float = 0.0
    liquidity_activity: float = 0.0
    maturity_convexity: float = 0.0
    delta_accel: float = 0.0
    credit_spread_mom: float = 0.0
    liquidity_shock: float = 1.0

    def as_dict(self) -> dict[str, float]:
        return {
            "iv_bias": self.iv_bias,
            "gamma_convexity": self.gamma_convexity,
            "credit_spread": self.credit_spread,
            "liquidity_activity": self.liquidity_activity,
            "maturity_convexity": self.maturity_convexity,
            "delta_accel": self.delta_accel,
            "credit_spread_mom": self.credit_spread_mom,
            "liquidity_shock": self.liquidity_shock,
        }


def _years_to_maturity(maturity_date: str, now: datetime) -> float:
    try:
        mat = datetime.strptime(str(maturity_date)[:8], "%Y%m%d")
        return max(0.0, (mat - now).days / 365.0)
    except (ValueError, TypeError):
        return 0.0


# ── 因子1: 隐含波动率偏差 ────────────────────────────────────────

def compute_iv_bias(
    close: float,
    cb_value: float | None,
    maturity_date: str | None,
    now: datetime,
    par_value: float = DEFAULT_PAR_VALUE,
) -> float:
    """iv_bias = time_value / (close * sqrt(years_to_mat))
    值越低 → 期权越便宜 → 买入信号"""
    if close <= 0 or maturity_date is None:
        return 0.5
    years = _years_to_maturity(maturity_date, now)
    if years <= 0.1:
        return 0.5
    cv = cb_value if cb_value is not None and cb_value > 0 else 0.0
    intrinsic = max(par_value, cv)
    time_value = max(0.0, close - intrinsic)
    return time_value / (close * math.sqrt(years))


# ── 因子2: Gamma 凸性价值 ──────────────────────────────────────

def compute_gamma_convexity(
    cb_value: float | None,
    maturity_date: str | None,
    now: datetime,
    par_value: float = DEFAULT_PAR_VALUE,
) -> float:
    """gamma_convexity = gamma_proxy * sqrt(years_to_mat)
    gamma_proxy ~ 4 * delta * (1 - delta)"""
    if cb_value is None or cb_value <= 0:
        return 0.0
    years = _years_to_maturity(maturity_date, now) if maturity_date else 0.0
    delta = max(0.0, min(1.0, cb_value / par_value))
    gamma_proxy = 4.0 * delta * (1.0 - delta)
    return gamma_proxy * math.sqrt(max(years, 0.01))


# ── 因子3: 信用利差 ────────────────────────────────────────────

def compute_credit_spread(
    close: float,
    maturity_date: str | None,
    now: datetime,
    par_value: float = DEFAULT_PAR_VALUE,
    risk_free: float = DEFAULT_RISK_FREE_RATE,
) -> float:
    """credit_spread = approx_ytm - risk_free_rate"""
    if close <= 0 or maturity_date is None:
        return 0.0
    years = _years_to_maturity(maturity_date, now)
    if years < 0.1:
        return 0.0
    ytm = (par_value / close) ** (1.0 / years) - 1.0
    return ytm - risk_free


# ── 因子4: 流动性活跃度 ────────────────────────────────────────

def compute_liquidity_activity(
    amount: float,
    issue_size: float,
) -> float:
    """liquidity = amount / issue_size"""
    if issue_size <= 0:
        return 0.0
    return amount / issue_size


# ── 因子5: 到期凸性 ────────────────────────────────────────────

def compute_maturity_convexity(
    maturity_date: str | None,
    now: datetime,
    sweet_spot: float = DEFAULT_MATURITY_SWEET_SPOT,
    half_range: float = DEFAULT_MATURITY_HALF_RANGE,
) -> float:
    """maturity_convexity = max(0, 1 - |years - sweet_spot| / half_range)
    2~4 年最优"""
    if maturity_date is None:
        return 0.0
    years = _years_to_maturity(maturity_date, now)
    return max(0.0, 1.0 - abs(years - sweet_spot) / half_range)


# ── 因子6: Delta 加速度 (时序) ─────────────────────────────────

def compute_delta_accel(
    current_delta: float,
    prev_delta: float,
) -> float:
    """delta_accel = delta_t - delta_{t-1}"""
    return current_delta - prev_delta


# ── 因子7: 信用利差动量 (时序) ─────────────────────────────────

def compute_credit_spread_mom(
    current_spread: float,
    lookback_spread: float,
) -> float:
    """spread_mom = spread_t - spread_{t-lookback}
    负值 = 利差收窄 = 信用改善"""
    return current_spread - lookback_spread


# ── 因子8: 流动性突变 (时序) ───────────────────────────────────

def compute_liquidity_shock(
    current_amount: float,
    ma_amount: float,
) -> float:
    """liquidity_shock = amount_t / MA(amount)
    大于1 = 放量"""
    if ma_amount <= 0:
        return 1.0
    return current_amount / ma_amount


# ── Delta 分区 ──────────────────────────────────────────────────

class DeltaZone:
    BOND_LIKE = "bond_like"       # δ < 0.3
    BALANCED = "balanced"         # 0.3 ≤ δ ≤ 0.7
    EQUITY_LIKE = "equity_like"   # δ > 0.7
    UNKNOWN = "unknown"


def classify_delta_zone(
    cb_value: float | None = None,
    par_value: float = 100.0,
    delta: float | None = None,
    bond_threshold: float = 0.3,
    equity_threshold: float = 0.7,
) -> str:
    """将可转债分类到 Delta 分区"""
    if delta is None:
        if cb_value is None or par_value <= 0:
            return DeltaZone.UNKNOWN
        delta = max(0.0, min(1.0, cb_value / par_value))
    if delta < bond_threshold:
        return DeltaZone.BOND_LIKE
    elif delta > equity_threshold:
        return DeltaZone.EQUITY_LIKE
    return DeltaZone.BALANCED


# ── 评级映射 ────────────────────────────────────────────────────

RATING_SCORES = {
    "AAA": 10, "AA+": 9, "AA": 8, "AA-": 7, "A+": 6, "A": 5,
    "A-": 4, "BBB+": 3, "BBB": 2, "BBB-": 1,
}


def rating_score(rating: str) -> float:
    """信用评级 → 数值分 (越高越好)"""
    return float(RATING_SCORES.get(rating.strip().upper(), 0))


def delta_exposure(close: float, cb_value: float | None) -> float:
    """Delta 暴露 = cb_value / close"""
    if close <= 0 or cb_value is None or cb_value <= 0:
        return 0.0
    return cb_value / close


# ── 批量计算 ────────────────────────────────────────────────────

def compute_all_factors(
    symbol: str,
    close: float,
    cb_value: float | None,
    amount: float,
    issue_size: float,
    maturity_date: str | None,
    now: datetime,
    prev_delta: float = 0.0,
    lookback_spread: float = 0.0,
    ma_amount: float = 0.0,
) -> CBFactorValues:
    """批量计算8因子"""
    par = DEFAULT_PAR_VALUE
    iv = compute_iv_bias(close, cb_value, maturity_date, now, par)
    gamma = compute_gamma_convexity(cb_value, maturity_date, now, par)
    spread = compute_credit_spread(close, maturity_date, now, par)
    liq = compute_liquidity_activity(amount, issue_size)
    mat_conv = compute_maturity_convexity(maturity_date, now)

    current_delta = max(0.0, min(1.0, cb_value / par)) if cb_value and cb_value > 0 else 0.0
    d_accel = compute_delta_accel(current_delta, prev_delta)
    sp_mom = compute_credit_spread_mom(spread, lookback_spread)
    liq_shock = compute_liquidity_shock(amount, ma_amount)

    return CBFactorValues(
        symbol=symbol,
        iv_bias=iv,
        gamma_convexity=gamma,
        credit_spread=spread,
        liquidity_activity=liq,
        maturity_convexity=mat_conv,
        delta_accel=d_accel,
        credit_spread_mom=sp_mom,
        liquidity_shock=liq_shock,
    )
