"""策略容量估算工具。

基于 ADV（日均成交额/量）约束估算策略可承载的最大 AUM，
帮助评估策略在真实市场中的可执行性。

核心思路：
    策略持仓的每只标的，交易量不应超过 ADV 的一定比例
    （默认 max_participation_rate = 10%），否则市场冲击显著。
    最大 AUM = Σ_i (adv_i * participation_rate) / weight_i

设计原则：
    - 纯计算函数，不依赖数据源（调用方传入 ADV 数据）
    - 可插拔：通过参数控制参与率上限、权重计算方式
    - 与回测流程解耦，可在回测前/后独立调用

Example::

    from aipha.execution.capacity import estimate_capacity

    result = estimate_capacity(
        target_weights={"600000.SH": 0.1, "000001.SZ": 0.1, ...},
        adv_map={"600000.SH": 5e8, "000001.SZ": 3e8, ...},
        max_participation_rate=0.10,
    )
    print(f"预估容量: {result.max_aum:,.0f}")
    print(f"瓶颈标的: {result.bottleneck_symbol}")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CapacityResult:
    """容量估算结果。

    Attributes:
        max_aum: 估算的最大可承载 AUM（元）
        bottleneck_symbol: 约束最紧的标的代码
        bottleneck_aum: 瓶颈标的对应的最大 AUM
        per_symbol_capacity: 每只标的允许的最大 AUM 字典
        n_symbols: 参与估算的标的数
        avg_adv: 平均 ADV（元）
        max_participation_rate: 使用的最大参与率
    """

    max_aum: float
    bottleneck_symbol: str
    bottleneck_aum: float
    per_symbol_capacity: dict[str, float] = field(default_factory=dict)
    n_symbols: int = 0
    avg_adv: float = 0.0
    max_participation_rate: float = 0.10


def estimate_capacity(
    target_weights: dict[str, float],
    adv_map: dict[str, float],
    max_participation_rate: float = 0.10,
) -> CapacityResult:
    """估算策略容量。

    对于每只标的 i:
        可交易金额 = adv_i * max_participation_rate
        该标的允许的 AUM = 可交易金额 / weight_i

    最终容量 = min(各标的允许的 AUM)，即瓶颈标的决定整体容量。

    Args:
        target_weights: 目标权重字典 ``{symbol: weight}``，权重为占
            总 AUM 的比例（0~1），不要求归一化。权重 ≤ 0 的标的将被跳过。
        adv_map: 日均成交额字典 ``{symbol: adv_value}``（单位：元）。
            缺失标的视为 ADV=0（容量为 0）。
        max_participation_rate: 最大市场参与率（0~1），默认 10%。

    Returns:
        CapacityResult

    Raises:
        ValueError: max_participation_rate 不在 (0, 1] 范围内
    """
    if not (0 < max_participation_rate <= 1.0):
        raise ValueError(
            f"max_participation_rate 必须在 (0, 1] 范围内，当前: {max_participation_rate}"
        )

    per_symbol: dict[str, float] = {}
    bottleneck_sym = ""
    bottleneck_aum = float("inf")
    adv_values: list[float] = []

    for symbol, weight in target_weights.items():
        if weight <= 0:
            continue

        adv = adv_map.get(symbol, 0.0)
        adv_values.append(adv)

        if adv <= 0:
            # ADV = 0 → 容量 = 0
            per_symbol[symbol] = 0.0
            if 0.0 < bottleneck_aum:
                bottleneck_aum = 0.0
                bottleneck_sym = symbol
            continue

        tradeable = adv * max_participation_rate
        capacity = tradeable / weight
        per_symbol[symbol] = capacity

        if capacity < bottleneck_aum:
            bottleneck_aum = capacity
            bottleneck_sym = symbol

    if not per_symbol:
        return CapacityResult(
            max_aum=0.0,
            bottleneck_symbol="",
            bottleneck_aum=0.0,
            per_symbol_capacity={},
            n_symbols=0,
            avg_adv=0.0,
            max_participation_rate=max_participation_rate,
        )

    avg_adv = sum(adv_values) / len(adv_values) if adv_values else 0.0

    return CapacityResult(
        max_aum=bottleneck_aum,
        bottleneck_symbol=bottleneck_sym,
        bottleneck_aum=bottleneck_aum,
        per_symbol_capacity=per_symbol,
        n_symbols=len(per_symbol),
        avg_adv=avg_adv,
        max_participation_rate=max_participation_rate,
    )


def estimate_capacity_from_positions(
    positions: dict[str, int],
    prices: dict[str, float],
    adv_map: dict[str, float],
    total_nav: float,
    max_participation_rate: float = 0.10,
) -> CapacityResult:
    """从实际持仓快照估算容量。

    将持仓股数 * 价格转为权重后调用 :func:`estimate_capacity`。

    Args:
        positions: 持仓 ``{symbol: qty}``
        prices: 当前价格 ``{symbol: price}``
        adv_map: 日均成交额 ``{symbol: adv}``
        total_nav: 总净值
        max_participation_rate: 最大参与率

    Returns:
        CapacityResult
    """
    if total_nav <= 0:
        return CapacityResult(
            max_aum=0.0,
            bottleneck_symbol="",
            bottleneck_aum=0.0,
            per_symbol_capacity={},
            n_symbols=0,
            avg_adv=0.0,
            max_participation_rate=max_participation_rate,
        )

    weights: dict[str, float] = {}
    for symbol, qty in positions.items():
        if qty <= 0:
            continue
        price = prices.get(symbol, 0.0)
        if price <= 0:
            continue
        weights[symbol] = (qty * price) / total_nav

    return estimate_capacity(weights, adv_map, max_participation_rate)
