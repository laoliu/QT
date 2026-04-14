"""事件驱动实时运行器 — 策略与交易网关集成"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Callable, Optional

from aipha.strategy.base import Strategy, StrategyContext, OrderIntent
from aipha.domain.models import Bar
from aipha.execution.gateway import (
    TradingGateway, GatewayOrder, ExecutionReport, OrderStatus,
    OrderSide, OrderType, AccountSnapshot, TradingMode,
)

logger = logging.getLogger(__name__)


class RunnerMode(str, Enum):
    BACKTEST = "backtest"
    PAPER = "paper"
    LIVE = "live"


@dataclass
class LiveRunnerConfig:
    mode: RunnerMode = RunnerMode.PAPER
    initial_capital: float = 1_000_000
    lot_size: int = 100
    t_plus_n: int = 1
    commission_rate: float = 0.0003
    stamp_tax_rate: float = 0.001
    slippage_bps: float = 10.0
    max_drawdown_pct: float = 0.20
    daily_loss_limit_pct: float = 0.05
    heartbeat_interval: float = 1.0
    max_order_retry: int = 3
    stop_loss_enabled: bool = True


@dataclass
class LiveRunnerState:
    is_running: bool = False
    current_date: Optional[date] = None
    strategy_name: str = ""
    total_bars: int = 0
    total_orders: int = 0
    total_fills: int = 0
    last_heartbeat: Optional[datetime] = None
    errors: deque = field(default_factory=lambda: deque(maxlen=500))


BarCallback = Callable[[Bar], None]
FillCallback = Callable[[ExecutionReport], None]


class LiveRunner:
    """事件驱动实时运行器

    核心职责:
    1. 管理事件循环
    2. 协调策略与网关
    3. 处理订单生命周期
    4. 止损风控
    """

    def __init__(
        self,
        config: LiveRunnerConfig,
        strategy: Strategy,
        gateway: TradingGateway,
    ):
        self.config = config
        self.strategy = strategy
        self.gateway = gateway
        self._state = LiveRunnerState(strategy_name=strategy.name)
        self._peak_nav = config.initial_capital
        self._is_halted = False
        self._order_map: dict[str, OrderIntent] = {}
        self._on_bar_callbacks: list[BarCallback] = []
        self._on_fill_callbacks: list[FillCallback] = []

    @classmethod
    def from_paper_mode(cls, config: LiveRunnerConfig, strategy: Strategy) -> LiveRunner:
        from aipha.execution.paper_gateway import PaperTradingGateway
        gw = PaperTradingGateway(initial_cash=config.initial_capital)
        return cls(config, strategy, gw)

    @property
    def state(self) -> LiveRunnerState:
        return self._state

    @property
    def is_running(self) -> bool:
        return self._state.is_running

    def on_bar_callback(self, cb: BarCallback):
        self._on_bar_callbacks.append(cb)

    def on_fill_callback(self, cb: FillCallback):
        self._on_fill_callbacks.append(cb)

    def start(self) -> None:
        if self._state.is_running:
            return
        self.gateway.connect()
        self.strategy.on_start()
        self._state.is_running = True
        self._state.last_heartbeat = datetime.now()
        logger.info("LiveRunner 启动: %s [%s]", self.strategy.name, self.config.mode.value)

    def stop(self) -> None:
        if not self._state.is_running:
            return
        self.gateway.disconnect()
        self._state.is_running = False
        logger.info("LiveRunner 停止: %s", self.strategy.name)

    def on_bar(self, bars: dict[str, Bar], current_time: datetime) -> list[ExecutionReport]:
        """处理一根 bar — 策略生成信号 → 风控检查 → 提交订单"""
        self._state.total_bars += 1
        self._state.current_date = current_time.date() if isinstance(current_time, datetime) else current_time
        self._state.last_heartbeat = datetime.now()

        for cb in self._on_bar_callbacks:
            for bar in bars.values():
                cb(bar)

        # 更新网关行情
        if hasattr(self.gateway, "set_prices"):
            self.gateway.set_prices({s: b.close for s, b in bars.items()})

        # 获取账户快照
        account = self.gateway.query_account()
        nav = account.total_equity

        # 止损检查
        if self.config.stop_loss_enabled and self._check_stop_loss(nav):
            return []

        # 策略生成信号
        ctx = StrategyContext(
            current_time=current_time,
            positions=account.positions,
            cash=account.cash,
            nav=nav,
        )
        try:
            intents = self.strategy.on_bar(bars, ctx)
        except Exception as e:
            logger.exception("策略 %s on_bar 异常", self.strategy.name)
            self._state.errors.append(f"{current_time}: {e}")
            return []

        # 信号 → 订单
        reports = []
        for intent in intents:
            rpt = self._submit_intent(intent, bars)
            if rpt:
                reports.append(rpt)

        return reports

    def _submit_intent(self, intent: OrderIntent, bars: dict[str, Bar]) -> Optional[ExecutionReport]:
        side = OrderSide.BUY if intent.direction > 0 else OrderSide.SELL
        qty = abs(intent.quantity)
        qty = (qty // self.config.lot_size) * self.config.lot_size
        if qty <= 0:
            return None

        order = GatewayOrder(
            symbol=intent.symbol,
            side=side,
            quantity=qty,
        )
        self._state.total_orders += 1
        rpt = self.gateway.submit_order(order)

        if rpt.status == OrderStatus.FILLED:
            self._state.total_fills += 1
            for cb in self._on_fill_callbacks:
                cb(rpt)

        return rpt

    def _check_stop_loss(self, nav: float) -> bool:
        if nav > self._peak_nav:
            self._peak_nav = nav

        dd = (self._peak_nav - nav) / self._peak_nav if self._peak_nav > 0 else 0
        if dd >= self.config.max_drawdown_pct:
            if not self._is_halted:
                logger.warning("回撤止损触发: %.2f%% >= %.2f%%", dd * 100, self.config.max_drawdown_pct * 100)
                self._is_halted = True
            return True

        self._is_halted = False
        return False

    async def run_replay(self, bars_by_date: dict[date, dict[str, Bar]]) -> dict[str, Any]:
        """历史数据回放模式"""
        self.start()
        all_reports = []
        for dt in sorted(bars_by_date.keys()):
            bars = bars_by_date[dt]
            current_time = datetime.combine(dt, datetime.min.time().replace(hour=15))
            reports = self.on_bar(bars, current_time)
            all_reports.extend(reports)
        self.stop()

        account = self.gateway.query_account()
        return {
            "total_bars": self._state.total_bars,
            "total_orders": self._state.total_orders,
            "total_fills": self._state.total_fills,
            "final_nav": account.total_equity,
            "reports": all_reports,
        }

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.stop()
