"""滑点模型 — 模拟市场冲击。"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod

from aipha.domain.models import Side


class SlippageModel(ABC):
    @abstractmethod
    def apply(self, price: float, qty: int, side: Side, volume: float = 0) -> float:
        """返回滑点调整后的成交价。"""
        raise NotImplementedError


class FixedBpsSlippage(SlippageModel):
    """固定基点滑点"""

    def __init__(self, bps: float = 10.0):
        self._bps = bps

    def apply(self, price: float, qty: int, side: Side, volume: float = 0) -> float:
        slip = price * self._bps / 10000.0
        return price + slip if side == Side.BUY else price - slip


class LinearImpactSlippage(SlippageModel):
    """线性冲击滑点: base_bps + impact_coeff * (qty/volume)"""

    def __init__(self, base_bps: float = 3.0, impact_coeff: float = 30.0):
        self._base_bps = base_bps
        self._impact_coeff = impact_coeff

    def apply(self, price: float, qty: int, side: Side, volume: float = 0) -> float:
        participation = qty / volume if volume > 0 else 0.01
        total_bps = self._base_bps + self._impact_coeff * participation
        slip = price * total_bps / 10000.0
        return price + slip if side == Side.BUY else price - slip


class SqrtImpactSlippage(SlippageModel):
    """平方根冲击模型: base_bps + coeff * sqrt(qty/volume)"""

    def __init__(self, base_bps: float = 3.0, coeff: float = 50.0):
        self._base_bps = base_bps
        self._coeff = coeff

    def apply(self, price: float, qty: int, side: Side, volume: float = 0) -> float:
        participation = qty / volume if volume > 0 else 0.01
        total_bps = self._base_bps + self._coeff * math.sqrt(participation)
        slip = price * total_bps / 10000.0
        return price + slip if side == Side.BUY else price - slip


def create_slippage_model(name: str = "fixed", **kwargs) -> SlippageModel:
    models = {
        "fixed": FixedBpsSlippage,
        "fixed_bps": FixedBpsSlippage,
        "linear_impact": LinearImpactSlippage,
        "square_root_impact": SqrtImpactSlippage,
    }
    cls = models.get(name, FixedBpsSlippage)
    return cls(**kwargs)


# Backward-compatible alias
FixedBpsSlippageModel = FixedBpsSlippage
