"""可转债 Delta 分档因子

基于 Tsiveriotis-Fernandes (1998) 分解理论，利用转股价值/面值比近似 Delta，
将可转债分为三个区间：

- **BOND_LIKE** (Δ < 0.3)：债性区，受利率影响大，equity upside 有限
- **BALANCED** (0.3 ≤ Δ ≤ 0.7)：平衡区，Gamma 最大，凸性最佳
- **EQUITY_LIKE** (Δ > 0.7)：股性区，走势趋同正股，强赎风险高

理论基础（对齐 copilot-instructions.md §11.2 可转债部分）：
- Delta 随转股价值单调递增：深度价内 Δ→1，深度价外 Δ→0
- Gamma = dΔ/dS 在 ATM 附近最大 → 平衡区 CB 具有最优凸性
- 策略含义：做多 balanced-zone CB = 做多 Gamma（正股波动越大收益越高）

近似公式：
    delta_proxy ≈ cb_value / par_value  (par=100)
    更精确版本使用 max(0, min(1, (cb_value - bond_floor) / (par - bond_floor)))

使用方式：
    zone = classify_delta_zone(cb_value=95.0, par_value=100.0)
    delta = compute_delta_proxy(cb_value=95.0, par_value=100.0)
"""

from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Delta 分区枚举
# ---------------------------------------------------------------------------

class DeltaZone(Enum):
    """可转债 Delta 分区。

    BOND_LIKE: 债性区 (Δ < 0.3)
    BALANCED: 平衡区 (0.3 ≤ Δ ≤ 0.7)，Gamma 最大
    EQUITY_LIKE: 股性区 (Δ > 0.7)，强赎风险
    UNKNOWN: 数据不足无法分类
    """

    BOND_LIKE = "bond_like"
    BALANCED = "balanced"
    EQUITY_LIKE = "equity_like"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Delta 近似计算
# ---------------------------------------------------------------------------

# 默认分区阈值
DEFAULT_BOND_THRESHOLD: float = 0.3
DEFAULT_EQUITY_THRESHOLD: float = 0.7


def compute_delta_proxy(
    cb_value: float | None,
    par_value: float = 100.0,
    bond_floor: float | None = None,
) -> float | None:
    """计算 Delta 近似值。

    简易版：delta ≈ cb_value / par_value（线性近似）
    精确版（提供 bond_floor）：delta ≈ (cb_value - bond_floor) / (par_value - bond_floor)

    Args:
        cb_value: 转股价值（= 正股价 × 100 / 转股价）
        par_value: 面值，默认 100
        bond_floor: 纯债价值（可选），用于更精确的 Delta 估算

    Returns:
        Delta 近似值 ∈ [0, 1]，数据不足返回 None
    """
    if cb_value is None or par_value <= 0:
        return None

    if bond_floor is not None and bond_floor > 0 and bond_floor < par_value:
        # 精确版：(cb_value - bond_floor) / (par_value - bond_floor)
        raw = (cb_value - bond_floor) / (par_value - bond_floor)
    else:
        # 简易版：cb_value / par_value
        raw = cb_value / par_value

    # 截断到 [0, 1]
    return max(0.0, min(1.0, raw))


def classify_delta_zone(
    cb_value: float | None = None,
    par_value: float = 100.0,
    bond_floor: float | None = None,
    delta: float | None = None,
    bond_threshold: float = DEFAULT_BOND_THRESHOLD,
    equity_threshold: float = DEFAULT_EQUITY_THRESHOLD,
) -> DeltaZone:
    """将可转债分类到 Delta 分区。

    可以直接传入 delta 值，或传入 cb_value 由函数计算。

    Args:
        cb_value: 转股价值
        par_value: 面值，默认 100
        bond_floor: 纯债价值（可选）
        delta: 预计算的 Delta 值（优先使用）
        bond_threshold: 债性区上界（默认 0.3）
        equity_threshold: 股性区下界（默认 0.7）

    Returns:
        DeltaZone 枚举值
    """
    if delta is None:
        delta = compute_delta_proxy(cb_value, par_value, bond_floor)

    if delta is None:
        return DeltaZone.UNKNOWN

    if delta < bond_threshold:
        return DeltaZone.BOND_LIKE
    elif delta > equity_threshold:
        return DeltaZone.EQUITY_LIKE
    else:
        return DeltaZone.BALANCED


def compute_gamma_proxy(
    cb_value: float | None,
    par_value: float = 100.0,
) -> float | None:
    """估算 Gamma 代理值。

    Gamma 在 ATM (delta≈0.5) 附近最大，远离 ATM 递减。
    使用正态-like 形状近似：gamma_proxy = 4 * delta * (1 - delta)
    当 delta=0.5 时 gamma_proxy=1（最大），delta=0/1 时 gamma_proxy=0。

    Args:
        cb_value: 转股价值
        par_value: 面值

    Returns:
        Gamma 代理值 ∈ [0, 1]，数据不足返回 None
    """
    delta = compute_delta_proxy(cb_value, par_value)
    if delta is None:
        return None
    # 抛物线映射：在 delta=0.5 处最大为 1
    return 4.0 * delta * (1.0 - delta)


def compute_premium_rate(
    close: float,
    cb_value: float | None,
) -> float | None:
    """计算转股溢价率 (%)。

    premium_rate = (close / cb_value - 1) * 100

    Args:
        close: 可转债收盘价
        cb_value: 转股价值

    Returns:
        溢价率(%)，数据不足返回 None
    """
    if cb_value is None or cb_value <= 0 or close <= 0:
        return None
    return (close / cb_value - 1.0) * 100.0
