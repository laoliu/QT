"""多策略投组运行器 — 独立资金分配 & 汇总"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Optional

from aipha.domain.models import Bar
from aipha.strategy.base import Strategy, StrategyContext, OrderIntent
from aipha.execution.gateway import TradingGateway, GatewayOrder, OrderSide, OrderStatus, ExecutionReport

logger = logging.getLogger(__name__)


@dataclass
class StrategySlot:
    strategy: Strategy
    allocation: float  # 0~1 — 资金分配比例
    enabled: bool = True
    nav: float = 0.0
    pnl: float = 0.0
    order_count: int = 0
    fill_count: int = 0


@dataclass
class StrategyAllocation:
    """轻量分配记录，供 PortfolioConfig 模式使用"""
    strategy: Strategy
    weight: float


@dataclass
class PortfolioConfig:
    """Portfolio-style config for MultiStrategyRunner (no gateway required)."""
    total_capital: Decimal = Decimal("1000000")
    rebalance_interval: int = 0
    lot_size: int = 100


@dataclass
class MultiStrategyConfig:
    initial_capital: float = 1_000_000
    rebalance_interval: int = 0  # 0 = 不自动再平衡
    lot_size: int = 100


class MultiStrategyRunner:
    """多策略投组运行器

    特性:
    - 按预设比例分配资金给各策略
    - 策略之间独立跟踪 PnL
    - 汇总投组级别指标
    """

    def __init__(
        self,
        config: MultiStrategyConfig | PortfolioConfig,
        gateway: TradingGateway | None = None,
    ):
        self.config = config
        self.gateway = gateway
        self._slots: dict[str, StrategySlot] = {}
        self._allocations: list[StrategyAllocation] = []
        if isinstance(config, PortfolioConfig):
            self._total_nav: float = float(config.total_capital)
        else:
            self._total_nav = config.initial_capital
        self._bars_processed: int = 0

    def add_strategy(self, strategy: Strategy, allocation: float = 0.0, *, weight: float = 0.0) -> None:
        effective = weight if weight else allocation
        if strategy.name in self._slots:
            raise ValueError(f"策略已存在: {strategy.name}")
        total_alloc = sum(s.allocation for s in self._slots.values()) + effective
        if total_alloc > 1.0 + 1e-9:
            raise ValueError(f"总分配比例超过100%: {total_alloc:.2%}")
        self._slots[strategy.name] = StrategySlot(
            strategy=strategy,
            allocation=effective,
            nav=self._total_nav * effective,
        )
        self._allocations.append(StrategyAllocation(strategy=strategy, weight=effective))
        logger.info("添加策略 %s, 分配 %.1f%%", strategy.name, effective * 100)

    def remove_strategy(self, name: str) -> bool:
        return self._slots.pop(name, None) is not None

    def start(self) -> None:
        self.gateway.connect()
        for slot in self._slots.values():
            slot.strategy.on_start()
        logger.info("多策略运行器启动, %d 个策略", len(self._slots))

    def stop(self) -> None:
        self.gateway.disconnect()
        logger.info("多策略运行器停止")

    def on_bar(
        self,
        bars: dict[str, Bar],
        current_time: datetime,
    ) -> list[ExecutionReport]:
        """处理 bar, 分别调用各策略"""
        self._bars_processed += 1
        all_reports = []

        if hasattr(self.gateway, "set_prices"):
            self.gateway.set_prices({s: b.close for s, b in bars.items()})

        account = self.gateway.query_account()
        portfolio_nav = account.total_equity

        for name, slot in self._slots.items():
            if not slot.enabled:
                continue

            slot_cash = portfolio_nav * slot.allocation
            ctx = StrategyContext(
                current_time=current_time,
                positions=account.positions,
                cash=slot_cash,
                nav=slot_cash,
            )

            try:
                intents = slot.strategy.on_bar(bars, ctx)
            except Exception:
                logger.exception("策略 %s on_bar 异常", name)
                continue

            for intent in intents:
                rpt = self._submit_intent(intent, slot)
                if rpt:
                    all_reports.append(rpt)

        return all_reports

    def _submit_intent(self, intent: OrderIntent, slot: StrategySlot) -> Optional[ExecutionReport]:
        side = OrderSide.BUY if intent.direction > 0 else OrderSide.SELL
        qty = abs(intent.quantity)
        qty = (qty // self.config.lot_size) * self.config.lot_size
        if qty <= 0:
            return None

        order = GatewayOrder(symbol=intent.symbol, side=side, quantity=qty)
        slot.order_count += 1
        rpt = self.gateway.submit_order(order)
        if rpt.status == OrderStatus.FILLED:
            slot.fill_count += 1
        return rpt

    def status(self) -> dict[str, Any]:
        slots = {}
        for name, slot in self._slots.items():
            slots[name] = {
                "allocation": slot.allocation,
                "enabled": slot.enabled,
                "orders": slot.order_count,
                "fills": slot.fill_count,
            }
        return {
            "bars_processed": self._bars_processed,
            "strategy_count": len(self._slots),
            "slots": slots,
        }

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
