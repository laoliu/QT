"""可转债 Delta 区间分类 & Gamma 凸性代理指标"""
from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)

DEFAULT_BOND_THRESHOLD: float = 0.3
DEFAULT_EQUITY_THRESHOLD: float = 0.7


class DeltaZone(Enum):
    BOND_LIKE = "bond_like"
    BALANCED = "balanced"
    EQUITY_LIKE = "equity_like"
    UNKNOWN = "unknown"


def compute_delta_proxy(
    cb_value: float | None,
    par_value: float = 100.0,
    bond_floor: float | None = None,
) -> float | None:
    """简易 delta 代理: (cb_value - bond_floor) / par_value, clamp [0, 1]"""
    if cb_value is None:
        return None
    floor = bond_floor if bond_floor is not None else par_value * 0.85
    if par_value <= 0:
        return None
    d = (cb_value - floor) / par_value
    return max(0.0, min(1.0, d))


def classify_delta_zone(
    cb_value: float | None = None,
    par_value: float = 100.0,
    bond_floor: float | None = None,
    delta: float | None = None,
    bond_threshold: float = DEFAULT_BOND_THRESHOLD,
    equity_threshold: float = DEFAULT_EQUITY_THRESHOLD,
) -> DeltaZone:
    d = delta if delta is not None else compute_delta_proxy(cb_value, par_value, bond_floor)
    if d is None:
        return DeltaZone.UNKNOWN
    if d < bond_threshold:
        return DeltaZone.BOND_LIKE
    if d > equity_threshold:
        return DeltaZone.EQUITY_LIKE
    return DeltaZone.BALANCED


def compute_gamma_proxy(
    cb_value: float | None, par_value: float = 100.0,
) -> float | None:
    """gamma 代理: delta 在 balanced 区间时最大, bond_like / equity_like 时较低"""
    d = compute_delta_proxy(cb_value, par_value)
    if d is None:
        return None
    # 简化 gamma ≈ 4 * d * (1 - d)  (peak at d=0.5)
    return 4.0 * d * (1.0 - d)


def compute_premium_rate(close: float, cb_value: float | None) -> float | None:
    if cb_value is None or cb_value <= 0:
        return None
    return (close - cb_value) / cb_value
