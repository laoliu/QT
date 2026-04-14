"""成交模型 — 决定订单以什么价格成交。"""
from __future__ import annotations

from abc import ABC, abstractmethod

from aipha.domain.models import Bar
from dataclasses import dataclass


@dataclass(frozen=True)
class FillResult:
    """Result of a fill attempt."""
    fill: object | None
    reject_reason: str | None = None
    no_fill_reason: str | None = None
    remaining_qty: int = 0

    @property
    def is_partial(self) -> bool:
        return self.fill is not None and self.remaining_qty > 0

    @property
    def is_full(self) -> bool:
        return self.fill is not None and self.remaining_qty == 0

    @property
    def is_rejected(self) -> bool:
        return self.reject_reason is not None


class FillModel(ABC):
    """成交模型抽象基类"""

    @abstractmethod
    def get_fill_price(self, bar: Bar, side: str) -> float | None:
        """返回成交价，None 表示无法成交。"""
        raise NotImplementedError

    @property
    def name(self) -> str:
        return self.__class__.__name__


class NextBarOpenFill(FillModel):
    """下一根 bar 的开盘价成交"""

    def get_fill_price(self, bar: Bar, side: str) -> float | None:
        if bar.is_suspended:
            return None
        return bar.open

    @property
    def name(self) -> str:
        return "next_bar_open"


class NextBarCloseFill(FillModel):
    """下一根 bar 的收盘价成交"""

    def get_fill_price(self, bar: Bar, side: str) -> float | None:
        if bar.is_suspended:
            return None
        return bar.close

    @property
    def name(self) -> str:
        return "next_bar_close"


class VWAPFill(FillModel):
    """VWAP 成交"""

    def get_fill_price(self, bar: Bar, side: str) -> float | None:
        if bar.is_suspended:
            return None
        if bar.vwap and bar.vwap > 0:
            return bar.vwap
        # Fallback: 均价近似
        return (bar.open + bar.high + bar.low + bar.close) / 4

    @property
    def name(self) -> str:
        return "vwap"


def create_fill_model(name: str = "next_bar_open") -> FillModel:
    models = {
        "next_bar_open": NextBarOpenFill,
        "next_bar_close": NextBarCloseFill,
        "vwap": VWAPFill,
    }
    cls = models.get(name, NextBarOpenFill)
    return cls()
