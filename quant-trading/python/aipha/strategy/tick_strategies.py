"""Tick 级别示例策略 — 动量 + 盘口不平衡策略。

对应 TS 侧 tick-momentum-imbalance 策略的 Python 回测版本。
可同时用于 tick 回测和实时交易。
"""
from __future__ import annotations

from aipha.domain.models import Side
from aipha.strategy.base import OrderIntent, TickContext, TickStrategy
from aipha.strategy.registry import TickStrategyRegistry

_registry = TickStrategyRegistry()


@_registry.register("tick_momentum_imbalance")
class TickMomentumImbalance(TickStrategy):
    """Tick 动量 + 盘口不平衡策略。

    买入条件:
      - 短期动量 > momentum_threshold
      - 盘口买单占优（imbalance > imbalance_threshold）
      - spread 在合理范围内

    卖出条件:
      - 短期动量 < -momentum_threshold
      - 盘口卖单占优
    """

    name = "tick_momentum_imbalance"

    def __init__(
        self,
        momentum_threshold: float = 0.05,
        imbalance_threshold: float = 0.2,
        max_spread_bps: float = 30.0,
        position_size: int = 1000,
        max_positions: int = 5,
    ):
        self._momentum_threshold = momentum_threshold
        self._imbalance_threshold = imbalance_threshold
        self._max_spread_bps = max_spread_bps
        self._position_size = position_size
        self._max_positions = max_positions

    def on_tick(self, context: TickContext) -> list[OrderIntent]:
        features = context.features
        symbol = context.tick.symbol
        intents: list[OrderIntent] = []

        # 检查 spread
        if features.spread_bps > self._max_spread_bps:
            return intents

        current_pos = context.get_position(symbol)
        total_positions = sum(1 for v in context.positions.values() if v > 0)

        # 买入信号
        if (
            features.short_momentum_pct > self._momentum_threshold
            and features.order_book_imbalance > self._imbalance_threshold
            and current_pos == 0
            and total_positions < self._max_positions
        ):
            intents.append(OrderIntent(
                symbol=symbol,
                side=Side.BUY,
                qty=self._position_size,
                reason=f"tick_mom={features.short_momentum_pct:.3f}, imb={features.order_book_imbalance:.3f}",
            ))

        # 卖出信号
        elif (
            features.short_momentum_pct < -self._momentum_threshold
            and features.order_book_imbalance < -self._imbalance_threshold
            and current_pos > 0
        ):
            intents.append(OrderIntent(
                symbol=symbol,
                side=Side.SELL,
                qty=current_pos,
                reason=f"tick_mom={features.short_momentum_pct:.3f}, imb={features.order_book_imbalance:.3f}",
            ))

        return intents


@_registry.register("tick_vwap_reversion")
class TickVWAPReversion(TickStrategy):
    """Tick VWAP 回归策略。

    当价格偏离 micro VWAP 过远时反向交易。
    """

    name = "tick_vwap_reversion"

    def __init__(
        self,
        deviation_threshold: float = 0.3,
        position_size: int = 1000,
        max_positions: int = 5,
    ):
        self._deviation_threshold = deviation_threshold
        self._position_size = position_size
        self._max_positions = max_positions

    def on_tick(self, context: TickContext) -> list[OrderIntent]:
        features = context.features
        symbol = context.tick.symbol
        intents: list[OrderIntent] = []

        if features.micro_vwap <= 0 or features.last_price <= 0:
            return intents

        deviation_pct = (features.last_price / features.micro_vwap - 1) * 100
        current_pos = context.get_position(symbol)
        total_positions = sum(1 for v in context.positions.values() if v > 0)

        # 价格低于 VWAP 较多 → 买入
        if (
            deviation_pct < -self._deviation_threshold
            and current_pos == 0
            and total_positions < self._max_positions
        ):
            intents.append(OrderIntent(
                symbol=symbol,
                side=Side.BUY,
                qty=self._position_size,
                reason=f"vwap_dev={deviation_pct:.3f}%",
            ))

        # 价格高于 VWAP 较多 → 卖出
        elif (
            deviation_pct > self._deviation_threshold
            and current_pos > 0
        ):
            intents.append(OrderIntent(
                symbol=symbol,
                side=Side.SELL,
                qty=current_pos,
                reason=f"vwap_dev={deviation_pct:.3f}%",
            ))

        return intents
