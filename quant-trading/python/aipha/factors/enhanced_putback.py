"""回售博弈增强因子（Enhanced Putback Play）

将可转债回售条款与到期催化效应结合，形成"条款博弈"复合因子。
本模块桥接 putback_clause.py（需要 stock_price/conversion_price）和
实际 CB 策略运行时的可用数据（conv_price + cb_value）。

**核心洞察**：
1. 回售接近度：正股价/转股价越低 → 越接近回售触发线 → 发行人下修概率越高
2. 到期催化：剩余期限越短 → 发行人行动的紧迫性越高
3. 两者的交叉效应：低价+临近到期 = 极高的下修/转股催化概率

**数据桥接**：
- 现有 PutbackProximityFactor 需要 stock_price 和 conversion_price
- CB 策略运行时有 conv_price（转股价）和 cb_value（转股价值）
- cb_value = 正股价 / 转股价 × 100 → 正股价 = cb_value × conv_price / 100
- 利用此等式将 CB 数据转换为因子所需格式

包含：
- compute_putback_from_cb: 从 CB 数据计算回售接近度
- compute_clause_play_score: 回售+到期复合分数
- compute_downward_revision_probability: 下修概率代理指标
"""

from __future__ import annotations

import math
from typing import Optional


def compute_putback_from_cb(
    cb_value: float | None,
    conv_price: float | None = None,
    trigger_ratio: float = 0.70,
) -> float | None:
    """从可转债数据计算回售接近度。

    回售条款典型触发条件：正股价连续 N 日 ≤ 转股价 × trigger_ratio
    回售接近度 = 1 - (stock_price / conv_price) / trigger_ratio
              = 1 - parity / trigger_ratio

    其中 parity = cb_value / 100（转股平价比率）

    Args:
        cb_value: 转股价值（标准化为面值 100 的百分比）
        conv_price: 当前转股价（元），None 时使用 cb_value 代理
        trigger_ratio: 回售触发比例（典型 0.70，即正股≤70%转股价）

    Returns:
        回售接近度值。>0 表示已低于触发线，<0 表示距触发线尚远。
        数据不足返回 None。
    """
    if cb_value is None or cb_value <= 0:
        return None
    if trigger_ratio <= 0:
        return None

    # parity = stock_price / conv_price = cb_value / 100
    parity = cb_value / 100.0
    return 1.0 - parity / trigger_ratio


def compute_downward_revision_probability(
    cb_value: float | None,
    days_to_maturity: int | None,
    cb_price: float | None = None,
    trigger_ratio: float = 0.70,
    *,
    has_put_clause: bool = True,
    remain_size: float = 0.0,
    large_size_threshold: float = 50000.0,
) -> float:
    """下修概率代理指标。

    综合评估发行人主动下修转股价的概率，基于：
    1. 回售接近度（正股价距触发线的距离）—— 权重 35%
    2. 到期紧迫性（剩余期限越短越紧迫）—— 权重 25%
    3. 转债价格低于面值的程度（下修弹性空间）—— 权重 20%
    4. 发行人动机因素（余额大+有回售条款）—— 权重 20%

    .. warning:: PIT 合规性
        ``remain_size`` 参数来自 TuShare cb_basic 最新快照，**非历史时点数据**。
        在回测中使用 remain_size 存在前视偏差。建议改用 ``issue_size``（IPO 静态字段）
        或从历史数据源获取逐日剩余规模。

    Args:
        cb_value: 转股价值
        days_to_maturity: 距到期天数
        cb_price: 转债当前价格
        trigger_ratio: 回售触发比例
        has_put_clause: 是否有回售条款
        remain_size: 剩余规模（万元）— **非PIT安全**，详见上方 warning
        large_size_threshold: 大规模阈值（万元）

    Returns:
        下修概率代理 [0.0, 1.0]。数据不足返回 0.0。
    """
    # 1. 回售接近度分数
    proximity = compute_putback_from_cb(cb_value, trigger_ratio=trigger_ratio)
    if proximity is None:
        return 0.0
    # proximity > 0 → 已穿触发线; 将其映射到 [0, 1]
    distance_score = max(0.0, min(1.0, 0.5 + proximity * 2.0))

    # 2. 到期紧迫性
    if days_to_maturity is not None and days_to_maturity >= 0:
        # 2 年内紧迫性线性增长
        urgency = max(0.0, min(1.0, (730.0 - days_to_maturity) / 730.0))
    else:
        urgency = 0.1  # 缺失数据，保守默认

    # 3. 价格弹性空间（越低于面值 → 下修后弹性越大）
    if cb_price is not None and cb_price > 0:
        space_score = max(0.0, min(1.0, (110.0 - cb_price) / 30.0))
    else:
        space_score = 0.3  # 缺失数据，中性默认

    # 4. 发行人动机
    motive = 0.0
    if has_put_clause:
        motive += 0.5  # 有回售条款 → 不下修就面临大额回售
    if remain_size > large_size_threshold:
        motive += 0.4  # 余额大 → 偿债压力大
    elif remain_size > large_size_threshold * 0.5:
        motive += 0.2
    motive = min(1.0, motive + 0.1)  # 基线 0.1

    # 加权综合
    prob = (
        0.35 * distance_score
        + 0.25 * urgency
        + 0.20 * space_score
        + 0.20 * motive
    )
    return round(max(0.0, min(1.0, prob)), 4)


def compute_clause_play_score(
    cb_value: float | None,
    days_to_maturity: int | None,
    cb_price: float | None = None,
    trigger_ratio: float = 0.70,
    *,
    has_put_clause: bool = True,
    putback_weight: float = 0.5,
    maturity_weight: float = 0.5,
) -> float:
    """条款博弈复合分数。

    将回售接近度和到期催化效应加权组合。

    Args:
        cb_value: 转股价值
        days_to_maturity: 距到期天数
        cb_price: 转债当前价格
        trigger_ratio: 回售触发比例
        has_put_clause: 是否有回售条款
        putback_weight: 回售接近度权重
        maturity_weight: 到期催化权重

    Returns:
        复合分数 [0.0, 1.0]
    """
    from aipha.factors.maturity_catalyst import compute_maturity_catalyst

    # 回售接近度分数
    proximity = compute_putback_from_cb(cb_value, trigger_ratio=trigger_ratio)
    if proximity is None:
        putback_score = 0.0
    else:
        # 将 proximity（可能为负数）映射到 [0, 1]
        # proximity > 0 → 已低于触发线 → 高分
        # proximity ≈ 0 → 在触发线附近 → 中等分
        # proximity < -0.3 → 距触发线很远 → 低分
        putback_score = max(0.0, min(1.0, 0.5 + proximity * 1.5))

    # 无回售条款 → 回售驱动力大打折扣
    if not has_put_clause:
        putback_score *= 0.3

    # 到期催化分数
    if days_to_maturity is not None and days_to_maturity >= 0:
        catalyst_score = compute_maturity_catalyst(days_to_maturity)
    else:
        catalyst_score = 0.1

    # 交叉增强：当两个因素都高时，额外加分
    cross_bonus = 0.0
    if putback_score > 0.6 and catalyst_score > 0.6:
        cross_bonus = 0.1 * min(putback_score, catalyst_score)

    # 加权合成
    total_w = putback_weight + maturity_weight
    if total_w <= 0:
        return 0.0

    score = (
        putback_weight / total_w * putback_score
        + maturity_weight / total_w * catalyst_score
        + cross_bonus
    )
    return round(max(0.0, min(1.0, score)), 4)
