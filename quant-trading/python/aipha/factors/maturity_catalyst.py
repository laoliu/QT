"""到期催化因子（Maturity Catalyst Factor）

量化可转债临近到期时的"催化效应"——随着到期日临近，
发行人面临的偿债压力迫使其采取行动（下修转股价、促转股、回购），
这种确定性事件窗口为投资者提供alpha来源。

**经济学原理**：
- 到期前1-3年：发行人偿债压力增大 → 主动下修转股价概率显著提升
- 到期前6-18月：回售条款通常在最后2年生效 → 持有人获得回售权筹码
- 到期前<6月：大部分催化已被市场充分定价 → 边际信息价值递减

**催化曲线设计**（基于实证研究）：
- [0, 180) 天：score ∈ [0.1, 0.3]（临近到期，大部分已定价）
- [180, 365) 天：score ∈ [0.4, 0.7]（6-12月，催化效应加速）
- [365, 1095) 天：score ∈ [0.7, 1.0]（1-3年，催化高峰区）
- [1095, 1825) 天：score ∈ [0.3, 0.6]（3-5年，中等催化）
- ≥1825 天：score ∈ [0.0, 0.2]（>5年，催化效应很弱）

包含：
- compute_maturity_catalyst: 核心催化分数计算
- compute_maturity_urgency: 时间紧迫度（单调递减）
- MaturityZone: 到期时间区划分
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Optional


class MaturityZone(Enum):
    """可转债到期时间区划分。"""

    IMMINENT = "imminent"        # <180天，即将到期
    ACCELERATING = "accelerating"  # 180-365天，催化加速
    PEAK = "peak"                # 1-3年，催化高峰
    MODERATE = "moderate"        # 3-5年，中等催化
    DISTANT = "distant"          # >5年，远期


@dataclass(frozen=True, slots=True)
class MaturityCatalystThresholds:
    """到期催化阈值配置。

    所有天数阈值均为正整数（日历天）。
    """

    imminent_days: int = 180     # <180天归为 IMMINENT
    accel_days: int = 365        # 180-365天归为 ACCELERATING
    peak_days: int = 1095        # 365-1095天（1-3年）归为 PEAK
    moderate_days: int = 1825    # 1095-1825天（3-5年）归为 MODERATE
    # >moderate_days → DISTANT


def classify_maturity_zone(
    days_to_maturity: int,
    thresholds: MaturityCatalystThresholds | None = None,
) -> MaturityZone:
    """根据距到期天数划分时间区。

    Args:
        days_to_maturity: 距到期日的日历天数（≥0）
        thresholds: 阈值配置，None 使用默认值

    Returns:
        MaturityZone 枚举值
    """
    if thresholds is None:
        thresholds = MaturityCatalystThresholds()

    if days_to_maturity < thresholds.imminent_days:
        return MaturityZone.IMMINENT
    elif days_to_maturity < thresholds.accel_days:
        return MaturityZone.ACCELERATING
    elif days_to_maturity < thresholds.peak_days:
        return MaturityZone.PEAK
    elif days_to_maturity < thresholds.moderate_days:
        return MaturityZone.MODERATE
    else:
        return MaturityZone.DISTANT


def compute_maturity_catalyst(
    days_to_maturity: int,
    thresholds: MaturityCatalystThresholds | None = None,
) -> float:
    """计算到期催化分数。

    催化分数量化了发行人面临的"行动压力"——到期日越近，
    发行人越可能采取对持有人有利的行动（下修/促转/回购）。

    分数设计为非单调曲线（先升后降）：
    - 远期(>5年): 无催化压力 → 低分
    - 中期(3-5年): 初现压力 → 中等分数
    - 高峰(1-3年): 催化压力最大 → 最高分
    - 加速(<1年): 催化持续但部分已定价 → 中高分
    - 临近(<6月): 大部分已被市场消化 → 低分

    Args:
        days_to_maturity: 距到期日的日历天数（≥0）
        thresholds: 阈值配置

    Returns:
        催化分数 [0.0, 1.0]。None-safe: 负值输入返回 0.0
    """
    if days_to_maturity < 0:
        return 0.0

    if thresholds is None:
        thresholds = MaturityCatalystThresholds()

    zone = classify_maturity_zone(days_to_maturity, thresholds)

    if zone == MaturityZone.IMMINENT:
        # [0, 180): 线性从 0.1 升到 0.3（越临近越低价值，因为已定价）
        t = days_to_maturity / max(thresholds.imminent_days, 1)
        return 0.1 + 0.2 * t

    elif zone == MaturityZone.ACCELERATING:
        # [180, 365): 从 0.4 升到 0.7（催化加速但尚未到高峰）
        span = thresholds.accel_days - thresholds.imminent_days
        t = (days_to_maturity - thresholds.imminent_days) / max(span, 1)
        return 0.4 + 0.3 * t

    elif zone == MaturityZone.PEAK:
        # [365, 1095): 使用钟形曲线，峰值在约 1.5-2 年（~600天）
        center = (thresholds.accel_days + thresholds.peak_days) / 2.0
        half_width = (thresholds.peak_days - thresholds.accel_days) / 2.0
        # 高斯形状，中心为 0.95，边缘为 0.70
        x = (days_to_maturity - center) / max(half_width, 1)
        return 0.70 + 0.25 * math.exp(-2.0 * x * x)

    elif zone == MaturityZone.MODERATE:
        # [1095, 1825): 线性从 0.55 降到 0.25
        span = thresholds.moderate_days - thresholds.peak_days
        t = (days_to_maturity - thresholds.peak_days) / max(span, 1)
        return 0.55 - 0.30 * t

    else:  # DISTANT
        # ≥1825天：指数衰减到接近 0
        excess = days_to_maturity - thresholds.moderate_days
        return 0.20 * math.exp(-excess / 1000.0)


def compute_maturity_urgency(
    days_to_maturity: int,
    max_days: int = 2190,
) -> float:
    """计算时间紧迫度（单调递减函数）。

    与 compute_maturity_catalyst 不同，此函数是简单的单调递减，
    适用于需要"越近越紧迫"的场景（如回售博弈权重调整）。

    Args:
        days_to_maturity: 距到期日的日历天数（≥0）
        max_days: 最大有效天数（超过此值紧迫度≈0），默认 6 年

    Returns:
        紧迫度 [0.0, 1.0]。0=最远/无紧迫，1=最近/最紧迫。
    """
    if days_to_maturity <= 0:
        return 1.0
    if days_to_maturity >= max_days:
        return 0.0
    # 平方根衰减（比线性衰减更快接近0，但在中期仍有一定值）
    return 1.0 - math.sqrt(days_to_maturity / max_days)


def days_to_maturity_from_str(
    maturity_date_str: str,
    current_date: date | None = None,
) -> int | None:
    """从日期字符串计算距到期天数。

    Args:
        maturity_date_str: YYYYMMDD 或 YYYY-MM-DD 格式
        current_date: 当前日期，None 则使用 today

    Returns:
        天数（≥0），解析失败返回 None
    """
    if not maturity_date_str:
        return None

    mat_date: date | None = None
    for fmt in ("%Y%m%d", "%Y-%m-%d"):
        try:
            mat_date = datetime.strptime(str(maturity_date_str).strip()[:10], fmt).date()
            break
        except ValueError:
            continue

    if mat_date is None:
        return None

    if current_date is None:
        current_date = date.today()

    delta = (mat_date - current_date).days
    return max(0, delta)
