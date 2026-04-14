"""模拟交易网关 — 内存撮合, 支持T+N / 冻结 / TTL"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

from aipha.execution.gateway import (
    TradingGateway, TradingMode, GatewayOrder, ExecutionReport,
    AccountSnapshot, OrderStatus, OrderSide, OrderType,
)
from aipha.execution.fees import FeeModel
from aipha.execution.slippage import SlippageModel

logger = logging.getLogger(__name__)


class PaperTradingGateway(TradingGateway):
    """模拟交易网关

    特性:
    - T+N 交收 (A股 T+1)
    - 卖出冻结检查
    - 订单 TTL / 自动过期
    - 线程安全
    """

    def __init__(
        self,
        initial_cash: float = 1_000_000,
        fee_model: FeeModel | None = None,
        slippage_model: SlippageModel | None = None,
        settlement_days: int = 1,
        order_ttl_seconds: int = 300,
    ):
        super().__init__(mode=TradingMode.PAPER)
        self._lock = threading.Lock()
        self._cash = initial_cash
        self._initial_cash = initial_cash
        self._positions: dict[str, int] = {}
        self._frozen: dict[str, int] = {}  # symbol → frozen qty
        self._orders: dict[str, GatewayOrder] = {}
        self._reports: dict[str, ExecutionReport] = {}
        self._settlement_days = settlement_days
        self._order_ttl = timedelta(seconds=order_ttl_seconds)
        self._fee_model = fee_model or FeeModel()
        self._slippage_model = slippage_model or SlippageModel()
        self._last_prices: dict[str, float] = {}
        self._settlement_queue: list[tuple[datetime, str, int]] = []  # (available_date, symbol, qty)

    def connect(self) -> bool:
        self._connected = True
        logger.info("模拟交易网关已连接, 初始资金=%.2f", self._initial_cash)
        return True

    def disconnect(self) -> None:
        self._connected = False

    def set_prices(self, prices: dict[str, float]) -> None:
        self._last_prices.update(prices)

    def process_settlement(self, current_date: datetime) -> None:
        """处理到期交收"""
        with self._lock:
            remaining = []
            for avail_date, symbol, qty in self._settlement_queue:
                if current_date >= avail_date:
                    self._frozen[symbol] = self._frozen.get(symbol, 0) - qty
                    if self._frozen.get(symbol, 0) <= 0:
                        self._frozen.pop(symbol, None)
                else:
                    remaining.append((avail_date, symbol, qty))
            self._settlement_queue = remaining

    def _expire_orders(self, now: datetime) -> None:
        for oid, order in list(self._orders.items()):
            if order.status == OrderStatus.PENDING and order.created_at:
                if now - order.created_at > self._order_ttl:
                    order.status = OrderStatus.EXPIRED
                    self._reports[oid] = ExecutionReport(
                        order_id=oid, symbol=order.symbol, side=order.side,
                        status=OrderStatus.EXPIRED, message="订单超时")

    def submit_order(self, order: GatewayOrder) -> ExecutionReport:
        with self._lock:
            now = datetime.now()
            self._expire_orders(now)
            self._orders[order.order_id] = order

            price = self._last_prices.get(order.symbol)
            if price is None:
                order.status = OrderStatus.REJECTED
                rpt = ExecutionReport(
                    order_id=order.order_id, symbol=order.symbol, side=order.side,
                    status=OrderStatus.REJECTED, message=f"无最新价: {order.symbol}")
                self._reports[order.order_id] = rpt
                return rpt

            # 滑点
            if order.side == OrderSide.BUY:
                exec_price = price * (1 + self._slippage_model.rate)
            else:
                exec_price = price * (1 - self._slippage_model.rate)

            total_cost = exec_price * order.quantity
            commission = self._fee_model.calculate(
                price=exec_price, quantity=order.quantity,
                side="buy" if order.side == OrderSide.BUY else "sell")

            if order.side == OrderSide.BUY:
                if self._cash < total_cost + commission:
                    order.status = OrderStatus.REJECTED
                    rpt = ExecutionReport(
                        order_id=order.order_id, symbol=order.symbol, side=order.side,
                        status=OrderStatus.REJECTED,
                        message=f"资金不足: 需{total_cost + commission:.2f}, 可用{self._cash:.2f}")
                    self._reports[order.order_id] = rpt
                    return rpt

                self._cash -= total_cost + commission
                self._positions[order.symbol] = self._positions.get(order.symbol, 0) + order.quantity

                # T+N 冻结
                if self._settlement_days > 0:
                    avail = now + timedelta(days=self._settlement_days)
                    self._settlement_queue.append((avail, order.symbol, order.quantity))
                    self._frozen[order.symbol] = self._frozen.get(order.symbol, 0) + order.quantity

            else:  # SELL
                held = self._positions.get(order.symbol, 0)
                frozen = self._frozen.get(order.symbol, 0)
                available = held - frozen
                if available < order.quantity:
                    order.status = OrderStatus.REJECTED
                    rpt = ExecutionReport(
                        order_id=order.order_id, symbol=order.symbol, side=order.side,
                        status=OrderStatus.REJECTED,
                        message=f"可用不足: 持仓{held}, 冻结{frozen}, 可卖{available}")
                    self._reports[order.order_id] = rpt
                    return rpt

                self._positions[order.symbol] -= order.quantity
                if self._positions[order.symbol] <= 0:
                    self._positions.pop(order.symbol, None)
                self._cash += total_cost - commission

            order.status = OrderStatus.FILLED
            rpt = ExecutionReport(
                order_id=order.order_id, symbol=order.symbol, side=order.side,
                status=OrderStatus.FILLED, filled_quantity=order.quantity,
                filled_price=exec_price, commission=commission,
                slippage=abs(exec_price - price) * order.quantity,
                executed_at=now)
            self._reports[order.order_id] = rpt
            return rpt

    def cancel_order(self, order_id: str) -> bool:
        with self._lock:
            order = self._orders.get(order_id)
            if order and order.status == OrderStatus.PENDING:
                order.status = OrderStatus.CANCELLED
                return True
            return False

    def query_order(self, order_id: str) -> Optional[ExecutionReport]:
        return self._reports.get(order_id)

    def query_account(self) -> AccountSnapshot:
        mv = sum(
            qty * self._last_prices.get(sym, 0)
            for sym, qty in self._positions.items()
        )
        return AccountSnapshot(
            cash=self._cash, market_value=mv,
            positions=dict(self._positions),
            frozen_cash=0, timestamp=datetime.now())

    def query_positions(self) -> dict[str, int]:
        return dict(self._positions)

    def reset(self) -> None:
        with self._lock:
            self._cash = self._initial_cash
            self._positions.clear()
            self._frozen.clear()
            self._orders.clear()
            self._reports.clear()
            self._settlement_queue.clear()


def create_paper_gateway(
    initial_cash: float = 1_000_000,
    settlement: int = 1,
    ttl: int = 300,
) -> PaperTradingGateway:
    return PaperTradingGateway(
        initial_cash=initial_cash, settlement_days=settlement,
        order_ttl_seconds=ttl)
