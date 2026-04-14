"""容量估算 — 基于 ADV 估计策略可承载资金量"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class CapacityResult:
    estimated_capacity: float
    bottleneck_symbol: Optional[str] = None
    bottleneck_adv: float = 0.0
    details: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        cap_str = f"{self.estimated_capacity / 1e4:.1f}万" if self.estimated_capacity < 1e8 else f"{self.estimated_capacity / 1e8:.2f}亿"
        lines = [f"策略容量估算: {cap_str}"]
        if self.bottleneck_symbol:
            lines.append(f"瓶颈: {self.bottleneck_symbol} (ADV={self.bottleneck_adv / 1e4:.0f}万)")
        for w in self.warnings:
            lines.append(f"⚠ {w}")
        return "\n".join(lines)


def estimate_capacity(
    positions: dict[str, float],
    adv_data: dict[str, float],
    max_adv_pct: float = 0.05,
    min_adv: float = 1e6,
    price_data: Optional[dict[str, float]] = None,
) -> CapacityResult:
    """估计策略容量

    Parameters
    ----------
    positions : dict
        symbol → 权重 (0~1之和=1)
    adv_data : dict
        symbol → 平均日成交额 (元)
    max_adv_pct : float
        最大可吃掉的 ADV 比例
    min_adv : float
        最低 ADV 门槛
    price_data : dict, optional
        symbol → 最新价 (用于细化)
    """
    if not positions or not adv_data:
        return CapacityResult(estimated_capacity=0, warnings=["无持仓或成交额数据"])

    warnings = []
    details = {}
    symbol_caps = {}

    for sym, weight in positions.items():
        if weight <= 0:
            continue
        adv = adv_data.get(sym, 0)
        if adv < min_adv:
            warnings.append(f"{sym}: ADV={adv / 1e4:.0f}万 低于门槛{min_adv / 1e4:.0f}万")
            adv = min_adv

        tradable_amount = adv * max_adv_pct
        if weight > 0:
            cap = tradable_amount / weight
        else:
            cap = float("inf")

        symbol_caps[sym] = cap
        details[sym] = tradable_amount

    if not symbol_caps:
        return CapacityResult(estimated_capacity=0, warnings=["无有效持仓"])

    bottleneck = min(symbol_caps, key=symbol_caps.get)  # type: ignore
    estimated = symbol_caps[bottleneck]

    return CapacityResult(
        estimated_capacity=estimated,
        bottleneck_symbol=bottleneck,
        bottleneck_adv=adv_data.get(bottleneck, 0),
        details=details,
        warnings=warnings,
    )


def estimate_portfolio_capacity(
    weights: dict[str, float],
    volume_data: dict[str, list[float]],
    price_data: dict[str, list[float]],
    max_participation: float = 0.05,
    lookback: int = 20,
) -> CapacityResult:
    """基于历史成交量估算投组容量

    Parameters
    ----------
    weights : symbol → 权重
    volume_data : symbol → 日成交量列表 (最近N日)
    price_data : symbol → 日收盘价列表 (最近N日)
    max_participation : 最大参与率
    lookback : 回看天数
    """
    adv = {}
    for sym in weights:
        if sym in volume_data and sym in price_data:
            vols = volume_data[sym][-lookback:]
            prices = price_data[sym][-lookback:]
            if vols and prices:
                amounts = [v * p for v, p in zip(vols, prices)]
                adv[sym] = float(np.mean(amounts))

    return estimate_capacity(weights, adv, max_adv_pct=max_participation)
