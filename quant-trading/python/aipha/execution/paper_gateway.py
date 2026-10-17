"""Paper Trading 网关实现

模拟实盘交易环境，用于策略验证：
- 模拟订单提交与撮合
- 模拟资金与持仓管理
- 模拟成交回报
- 支持 T+1/T+0 规则

与回测的区别：
- 回测：历史数据，批量处理
- Paper Trading：模拟实时，逐笔处理
"""

from __future__ import annotations

import logging
import uuid
import warnings
from datetime import date, datetime
from decimal import Decimal
from threading import Lock
from typing import Any

from aipha.domain.models import Bar, Fill, Order, OrderType, Side
from aipha.execution.fill_models import FillModel, FillResult, NextBarOpenFillModel
from aipha.market.base import MarketRules
from aipha.portfolio.account import Account
from aipha.portfolio.position_manager import PositionManager
from aipha.execution.fees import (
    CommissionModel,
    FeeBreakdown,
    FeeModel,
    FeeModelCNEquity,
    StampTaxModel,
    TransferFeeModel,
)
from aipha.execution.slippage import (
    FixedBpsSlippageModel,
    SlippageModel,
)
from aipha.execution.gateway import (
    AccountSnapshot,
    ExecutionCallback,
    ExecutionReport,
    GatewayError,
    GatewayNotConnectedError,
    GatewayOrder,
    InsufficientFundsError,
    InsufficientPositionError,
    OrderStatus,
    OrderStatusChange,
    OrderValidationError,
    StatusCallback,
    TradingGateway,
    TradingMode,
)

logger = logging.getLogger(__name__)


class PaperTradingGateway(TradingGateway):
    """Paper Trading 网关

    模拟真实交易环境，支持：
    - 市价单/限价单
    - T+1/T+0 规则
    - 资金冻结与结算
    - 持仓管理

    Example:
        gateway = PaperTradingGateway(
            initial_capital=1_000_000,
            commission_rate=Decimal("0.0000854"),
        )
        gateway.connect()
        gateway.submit_order(order)
        gateway.process_bar(bar)  # 触发撮合
    """

    @classmethod
    def from_cb_mode(
        cls,
        initial_capital: float = 500_000,
        commission_rate: float = 0.0000854,
        commission_min: float = 5.0,  # §13.1 MarketRulesConvertibleBond
        slippage_bps: float = 5.0,  # §13.1 MarketRulesConvertibleBond.slippage_bps_default
    ) -> "PaperTradingGateway":
        """创建可转债模式的仿真网关。

        使用可转债默认参数（§13.1 权威值来自 MarketRulesConvertibleBond）：
        - lot_size=10, t_plus_n=0, stamp_tax=0, transfer_fee=0
        - commission_rate=万0.854, commission_min=5元

        Args:
            initial_capital: 初始资金
            commission_rate: 佣金率（默认万0.854，MarketRulesConvertibleBond 权威值）
            commission_min: 最低佣金（默认5元，§13.1 MarketRulesConvertibleBond 权威值）
            slippage_bps: 滑点（默认5.0bps，§13.1 CB权威值）

        Returns:
            配置好的 PaperTradingGateway 实例
        """
        return cls(
            initial_capital=Decimal(str(initial_capital)),
            commission_rate=Decimal(str(commission_rate)),
            commission_min=Decimal(str(commission_min)),
            stamp_tax_rate=Decimal("0"),
            slippage_bps=slippage_bps,
            lot_size=10,
            t_plus_n=0,
            transfer_fee_rate=0.0,
        )

    def __init__(
        self,
        initial_capital: Decimal | float = Decimal("1000000"),
        commission_rate: Decimal = Decimal("0.0000854"),  # §13.1: MarketRulesCNEquity 万0.854
        commission_min: Decimal = Decimal("5"),
        stamp_tax_rate: Decimal = Decimal("0.0005"),  # 2023-08-28 起千分0.5
        slippage_bps: float = 10.0,  # §13.1 默认A股权威值 (与同类其他参数对齐)
        lot_size: int = 100,
        t_plus_n: int = 1,
        *,
        fill_model_name: str = "next_bar_open",
        fee_model: FeeModel | None = None,
        transfer_fee_rate: float = 0.00001,  # 过户费：默认万分之一
        slippage_model: SlippageModel | None = None,
        order_ttl_bars: int = 5,  # R92 T3-1: 订单自动取消阈值（bars）
    ):
        warnings.warn(
            "PaperTradingGateway 已废弃, 请使用 PaperGateway (§13.16)。"
            "此类将于 2026 Q3 移除。",
            DeprecationWarning,
            stacklevel=2,
        )
        self._initial_capital = Decimal(str(initial_capital))
        self._commission_rate = commission_rate
        self._commission_min = commission_min
        self._stamp_tax_rate = stamp_tax_rate
        self._slippage_bps = slippage_bps
        self._lot_size = lot_size
        self._t_plus_n = t_plus_n
        self._fill_model_name = fill_model_name.strip().lower()

        if self._fill_model_name not in {"next_bar_open", "next_bar_close", "vwap"}:
            raise ValueError(
                f"不支持的 paper fill_model: {fill_model_name!r}。"
                "支持: next_bar_open, next_bar_close, vwap"
            )

        # 滑点模型：§4.3 回测/模拟盘共享同一 SlippageModel 接口
        # 若指定 slippage_model，则优先使用；否则回退到固定 bps
        self._slippage_model: SlippageModel | None = slippage_model

        # 费用模型：可插拔，与 BacktestEngine 复用同一套
        self._fee_model = fee_model or FeeModelCNEquity(
            commission=CommissionModel(
                rate=float(commission_rate),
                min_fee=float(commission_min),
            ),
            stamp_tax=StampTaxModel(sell_rate=float(stamp_tax_rate)),
            transfer_fee=TransferFeeModel(rate=transfer_fee_rate),
        )

        # 状态
        self._connected = False
        self._cash = self._initial_capital
        self._frozen_cash = Decimal("0")

        # 持仓：symbol -> {qty, today_bought, frozen, avg_cost}
        self._positions: dict[str, dict] = {}

        # 最新行情价格（用于计算持仓市值）
        self._latest_prices: dict[str, float] = {}

        # 订单
        self._orders: dict[str, GatewayOrder] = {}
        self._pending_orders: list[str] = []  # 待撮合订单ID

        # 回调
        self._execution_callbacks: list[ExecutionCallback] = []
        self._status_callbacks: list[StatusCallback] = []

        # 线程安全
        self._lock = Lock()

        # 当前交易日
        self._current_trade_date: date | None = None

        # 执行计数器
        self._exec_counter = 0

        # R92 T3-1: 订单 TTL 自动取消
        self._order_ttl_bars = order_ttl_bars
        self._order_create_bar: dict[str, int] = {}  # order_id → 创建时的 bar 计数
        self._bar_counter = 0

    @property
    def mode(self) -> TradingMode:
        """返回交易模式（模拟盘）。"""
        return TradingMode.PAPER

    @property
    def is_connected(self) -> bool:
        """返回网关是否已连接。"""
        return self._connected

    def connect(self) -> None:
        """连接网关"""
        with self._lock:
            self._connected = True
            logger.info("Paper Trading Gateway connected")

    def disconnect(self) -> None:
        """断开连接"""
        with self._lock:
            self._connected = False
            logger.info("Paper Trading Gateway disconnected")

    def set_trade_date(self, trade_date: date) -> None:
        """设置当前交易日

        日终调用，解锁 T+N 持仓。
        """
        with self._lock:
            old_date = self._current_trade_date
            self._current_trade_date = trade_date

            # 如果日期变更，解锁持仓
            if old_date and old_date != trade_date:
                self._unlock_positions()

    def _unlock_positions(self) -> None:
        """解锁 T+N 到期的持仓"""
        for _symbol, pos in self._positions.items():
            pos["today_bought"] = 0
        logger.debug("Positions unlocked for new trade date")

    def submit_order(self, order: GatewayOrder) -> str:
        """提交订单"""
        if not self._connected:
            raise GatewayNotConnectedError("Gateway not connected")

        with self._lock:
            # 验证订单
            self._validate_order(order)

            # 设置订单状态
            order.status = OrderStatus.SUBMITTED
            order.submitted_at = datetime.now()

            # 冻结资金/持仓
            if order.side == "BUY":
                self._freeze_cash_for_buy(order)
            else:
                self._freeze_position_for_sell(order)

            # 保存订单
            self._orders[order.order_id] = order
            self._pending_orders.append(order.order_id)
            # R92 T3-1: 记录创建时的 bar 计数
            self._order_create_bar[order.order_id] = self._bar_counter

            # 发送状态变更
            self._emit_status_change(
                order.order_id,
                OrderStatus.PENDING,
                OrderStatus.SUBMITTED,
            )

            logger.info(
                f"Order submitted: {order.order_id} {order.side} "
                f"{order.symbol} x{order.qty}"
            )

            return order.order_id

    def _validate_order(self, order: GatewayOrder) -> None:
        """验证订单"""
        # 检查数量
        if order.qty <= 0:
            raise OrderValidationError("Order qty must be positive")

        if order.qty % self._lot_size != 0:
            raise OrderValidationError(
                f"Order qty must be multiple of {self._lot_size}"
            )

        # 检查价格
        if order.order_type == "LIMIT" and order.limit_price is None:
            raise OrderValidationError("Limit order requires limit_price")

    def _freeze_cash_for_buy(self, order: GatewayOrder) -> None:
        """冻结买入资金"""
        # 估算成交金额（含滑点和手续费）
        # 这里使用一个保守估算
        # R92 T3-2: 使用最新行情价格估算，而非硬编码 ¥100
        estimated_price = (
            order.limit_price
            if order.limit_price
            else self._latest_prices.get(order.symbol, 100.0)
        )
        estimated_cost = Decimal(str(estimated_price * order.qty * 1.01))

        available = self._cash - self._frozen_cash
        if estimated_cost > available:
            raise InsufficientFundsError(
                f"Insufficient funds: need {estimated_cost}, available {available}"
            )

        self._frozen_cash += estimated_cost
        order.metadata["frozen_cash"] = estimated_cost

    def _freeze_position_for_sell(self, order: GatewayOrder) -> None:
        """冻结卖出持仓"""
        pos = self._positions.get(order.symbol, {"qty": 0, "today_bought": 0, "frozen": 0})

        available = pos["qty"] - pos["frozen"]

        # T+N 规则检查
        if self._t_plus_n > 0:
            available -= pos.get("today_bought", 0)

        if order.qty > available:
            raise InsufficientPositionError(
                f"Insufficient position: need {order.qty}, available {available}"
            )

        pos["frozen"] = pos.get("frozen", 0) + order.qty
        self._positions[order.symbol] = pos
        order.metadata["frozen_qty"] = order.qty

    def cancel_order(self, order_id: str) -> bool:
        """撤销订单"""
        if not self._connected:
            raise GatewayNotConnectedError("Gateway not connected")

        with self._lock:
            order = self._orders.get(order_id)
            if order is None:
                return False

            if order.is_done:
                return False

            old_status = order.status
            order.status = OrderStatus.CANCELED
            order.cancel_reason = "User canceled"

            # 解冻资金/持仓
            self._unfreeze_order(order)

            # 从待撮合列表移除
            if order_id in self._pending_orders:
                self._pending_orders.remove(order_id)

            # 发送状态变更
            self._emit_status_change(order_id, old_status, OrderStatus.CANCELED)

            logger.info(f"Order canceled: {order_id}")
            return True

    def _unfreeze_order(self, order: GatewayOrder) -> None:
        """解冻订单占用的资金/持仓"""
        if order.side == "BUY":
            frozen_cash = order.metadata.get("frozen_cash", Decimal("0"))
            self._frozen_cash -= frozen_cash
        else:
            frozen_qty = order.metadata.get("frozen_qty", 0)
            pos = self._positions.get(order.symbol)
            if pos:
                pos["frozen"] = max(0, pos.get("frozen", 0) - frozen_qty)

    def get_order(self, order_id: str) -> GatewayOrder | None:
        """获取订单"""
        return self._orders.get(order_id)

    def get_active_orders(self) -> list[GatewayOrder]:
        """获取所有活跃订单"""
        return [o for o in self._orders.values() if o.is_active]

    def get_pending_order_codes(self) -> list[str]:
        """获取所有待撮合订单的标的代码。"""
        codes: set[str] = set()
        for o in self._orders.values():
            if o.is_active:
                codes.add(o.symbol)
        return list(codes)

    def get_account_snapshot(self) -> AccountSnapshot:
        """获取账户快照

        total_equity = cash + 持仓市值
        持仓市值 = Σ(qty × latest_price)
        """
        with self._lock:
            positions = {
                symbol: pos["qty"]
                for symbol, pos in self._positions.items()
                if pos["qty"] > 0
            }

            # 计算持仓市值
            positions_value = Decimal("0")
            for symbol, qty in positions.items():
                price = self._latest_prices.get(symbol)
                if price is not None:
                    positions_value += Decimal(str(price)) * qty

            return AccountSnapshot(
                timestamp=datetime.now(),
                cash=self._cash,
                frozen_cash=self._frozen_cash,
                total_equity=self._cash + positions_value,
                positions=positions,
                positions_value=positions_value,
            )

    def process_bar(self, bar: Bar) -> list[ExecutionReport]:
        """处理 bar 数据，触发撮合

        Paper Trading 核心方法：
        - 遍历待撮合订单
        - 尝试撮合
        - 生成成交回报

        Args:
            bar: 行情数据

        Returns:
            本次撮合的成交回报列表
        """
        if not self._connected:
            return []

        # 更新最新行情价格（用于持仓市值计算）
        self._latest_prices[bar.symbol] = bar.close
        self._bar_counter += 1

        reports = []

        with self._lock:
            # R92 T3-1: 自动取消超 TTL 的挂单
            if self._order_ttl_bars > 0:
                expired_ids = [
                    oid for oid in list(self._pending_orders)
                    if (self._bar_counter - self._order_create_bar.get(oid, 0))
                    > self._order_ttl_bars
                ]
                for oid in expired_ids:
                    order = self._orders.get(oid)
                    if order and order.is_active:
                        order.status = OrderStatus.CANCELED
                        if oid in self._pending_orders:
                            self._pending_orders.remove(oid)
                        self._order_create_bar.pop(oid, None)
                        self._unfreeze_order(order)
                        logger.info(
                            f"Order TTL expired, auto-cancelled: "
                            f"{oid} {order.symbol} {order.side}"
                        )

            orders_to_process = list(self._pending_orders)

            # 卖出优先（与 BacktestEngine 对齐：先释放资金再买入）
            sell_orders = []
            buy_orders = []
            for order_id in orders_to_process:
                order = self._orders.get(order_id)
                if order is None or order.symbol != bar.symbol:
                    continue
                if order.side == "SELL":
                    sell_orders.append(order)
                else:
                    buy_orders.append(order)

            for order in sell_orders + buy_orders:
                report = self._try_fill_order(order, bar)
                if report:
                    reports.append(report)

        return reports

    def _try_fill_order(
        self,
        order: GatewayOrder,
        bar: Bar,
    ) -> ExecutionReport | None:
        """尝试撮合订单"""
        # 检查是否可成交
        if bar.is_suspended:
            return None

        # §4.3/§13.4 涨跌停检查 — 与回测引擎 precheck_order 行为一致
        if order.side == "BUY" and bar.is_limit_up is True:
            logger.debug("涨停拒绝买入: %s", order.symbol)
            return None
        if order.side == "SELL" and bar.is_limit_down is True:
            logger.debug("跌停拒绝卖出: %s", order.symbol)
            return None

        # 计算成交价格
        fill_price = self._calculate_fill_price(order, bar)
        if fill_price is None:
            return None

        # 应用滑点 — §4.3 与回测共享 SlippageModel 接口
        side = Side.BUY if order.side == "BUY" else Side.SELL
        if self._slippage_model is not None:
            # 使用 SlippageModel Protocol（与回测引擎一致）
            raw_fill = Fill(
                order_id=order.order_id,
                symbol=order.symbol,
                side=side,
                qty=order.qty,
                price=fill_price,
                t_fill=bar.end_ts,
                model=self._fill_model_name,
            )
            slipped_fill = self._slippage_model.apply(raw_fill, bar)
            fill_price = slipped_fill.price
        else:
            # 回退：固定 bps 滑点（向后兼容）
            slippage = fill_price * self._slippage_bps / 10000
            if order.side == "BUY":
                fill_price += slippage
            else:
                fill_price -= slippage

        # 计算手续费（复用 FeeModelCNEquity）
        fill_for_fee = Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=side,
            qty=order.qty,
            price=fill_price,
            t_fill=bar.end_ts,
            model=self._fill_model_name,
        )
        fee_breakdown = self._fee_model.calc(fill_for_fee)
        commission = Decimal(str(fee_breakdown.total))

        # 更新订单状态
        old_status = order.status
        order.filled_qty = order.qty
        order.avg_fill_price = Decimal(str(fill_price))
        order.status = OrderStatus.FILLED
        order.filled_at = bar.end_ts

        # 从待撮合列表移除
        if order.order_id in self._pending_orders:
            self._pending_orders.remove(order.order_id)

        # 结算
        self._settle_order(order, fill_price, commission)

        # 生成成交回报
        self._exec_counter += 1
        report = ExecutionReport(
            order_id=order.order_id,
            exec_id=f"EXEC-{self._exec_counter:08d}",
            symbol=order.symbol,
            side=order.side,
            fill_qty=order.qty,
            fill_price=fill_price,
            fill_time=bar.end_ts,
            commission=commission,
            is_last=True,
        )

        # 发送回调
        self._emit_execution(report)
        self._emit_status_change(order.order_id, old_status, OrderStatus.FILLED)

        logger.info(
            f"Order filled: {order.order_id} {order.side} {order.symbol} "
            f"x{order.qty} @{fill_price:.4f}"
        )

        return report

    def _calculate_fill_price(
        self,
        order: GatewayOrder,
        bar: Bar,
    ) -> float | None:
        """计算成交价格"""
        base_price = self._get_market_fill_price(bar)

        if order.order_type == "MARKET":
            return base_price

        elif order.order_type == "LIMIT":
            # 限价单：检查是否触发
            limit_price = order.limit_price

            if order.side == "BUY":
                # 买入：价格 <= 限价时成交
                if bar.low <= limit_price:
                    return min(limit_price, base_price)
            else:
                # 卖出：价格 >= 限价时成交
                if bar.high >= limit_price:
                    return max(limit_price, base_price)

            return None

        return None

    def _get_market_fill_price(self, bar: Bar) -> float:
        """根据 fill_model 选择基准成交价。"""
        if self._fill_model_name == "next_bar_close":
            return bar.close
        if self._fill_model_name == "vwap":
            return bar.vwap if bar.vwap is not None else bar.close
        return bar.open

    def _calculate_commission(
        self,
        order: GatewayOrder,
        fill_price: float,
    ) -> Decimal:
        """计算手续费（委托给 FeeModelCNEquity）

        保留方法签名以兼容外部调用，内部使用统一的费用模型。
        """
        fill = Fill(
            order_id=order.order_id,
            symbol=order.symbol,
            side=Side.BUY if order.side == "BUY" else Side.SELL,
            qty=order.qty,
            price=fill_price,
            t_fill=datetime.now(),
            model="paper",
        )
        breakdown = self._fee_model.calc(fill)
        return Decimal(str(breakdown.total))

    def _settle_order(
        self,
        order: GatewayOrder,
        fill_price: float,
        commission: Decimal,
    ) -> None:
        """结算订单"""
        trade_value = Decimal(str(fill_price * order.qty))

        if order.side == "BUY":
            # 买入结算
            frozen_cash = order.metadata.get("frozen_cash", Decimal("0"))
            actual_cost = trade_value + commission

            # 解冻并扣除实际成本
            self._frozen_cash -= frozen_cash
            self._cash -= actual_cost

            # 更新持仓（含加权平均成本）
            pos = self._positions.get(
                order.symbol,
                {"qty": 0, "today_bought": 0, "frozen": 0, "avg_cost": Decimal("0")},
            )
            old_qty = pos.get("qty", 0)
            old_cost = pos.get("avg_cost", Decimal("0")) * old_qty
            new_qty = old_qty + order.qty
            if new_qty > 0:
                pos["avg_cost"] = (old_cost + trade_value) / new_qty
            pos["qty"] = new_qty
            pos["today_bought"] = pos.get("today_bought", 0) + order.qty
            self._positions[order.symbol] = pos

        else:
            # 卖出结算
            actual_income = trade_value - commission

            # 更新资金
            self._cash += actual_income

            # 更新持仓
            pos = self._positions.get(order.symbol)
            if pos:
                frozen_qty = order.metadata.get("frozen_qty", 0)
                pos["qty"] = max(0, pos.get("qty", 0) - order.qty)
                pos["frozen"] = max(0, pos.get("frozen", 0) - frozen_qty)

    def on_execution(self, callback: ExecutionCallback) -> None:
        """注册成交回调"""
        self._execution_callbacks.append(callback)

    def on_status_change(self, callback: StatusCallback) -> None:
        """注册状态变更回调"""
        self._status_callbacks.append(callback)

    def _emit_execution(self, report: ExecutionReport) -> None:
        """发送成交回报"""
        for callback in self._execution_callbacks:
            try:
                callback(report)
            except Exception as e:
                logger.error(f"Execution callback error: {e}")

    def _emit_status_change(
        self,
        order_id: str,
        old_status: OrderStatus,
        new_status: OrderStatus,
        reason: str | None = None,
    ) -> None:
        """发送状态变更"""
        change = OrderStatusChange(
            order_id=order_id,
            old_status=old_status,
            new_status=new_status,
            timestamp=datetime.now(),
            reason=reason,
        )

        for callback in self._status_callbacks:
            try:
                callback(change)
            except Exception as e:
                logger.error(f"Status callback error: {e}")

    # ==================== 辅助方法 ====================

    def get_position(self, symbol: str) -> int:
        """获取持仓数量"""
        pos = self._positions.get(symbol, {})
        return pos.get("qty", 0)

    def get_available_position(self, symbol: str) -> int:
        """获取可卖数量（考虑 T+N）"""
        pos = self._positions.get(symbol, {"qty": 0, "today_bought": 0, "frozen": 0})
        available = pos["qty"] - pos.get("frozen", 0)

        if self._t_plus_n > 0:
            available -= pos.get("today_bought", 0)

        return max(0, available)

    def get_available_cash(self) -> Decimal:
        """获取可用资金"""
        return self._cash - self._frozen_cash

    def get_position_details(self) -> dict:
        """获取持仓明细（含成本和市值）

        Returns:
            dict[str, PositionDetail] 用于对账
        """
        from aipha.risk.position_reconciler import PositionDetail

        result = {}
        for symbol, pos in self._positions.items():
            qty = pos.get("qty", 0)
            if qty <= 0:
                continue
            avg_cost = float(pos.get("avg_cost", 0))
            price = self._latest_prices.get(symbol, avg_cost)
            result[symbol] = PositionDetail(
                symbol=symbol,
                qty=qty,
                avg_cost=avg_cost,
                market_value=qty * price,
                frozen_qty=pos.get("frozen", 0),
            )
        return result

    def reset(self) -> None:
        """重置网关状态"""
        with self._lock:
            self._cash = self._initial_capital
            self._frozen_cash = Decimal("0")
            self._positions.clear()
            self._latest_prices.clear()
            self._orders.clear()
            self._pending_orders.clear()
            self._current_trade_date = None


# ============================================================================
# PaperGateway — 统一执行架构 Paper 网关
# ============================================================================


class PaperGateway(TradingGateway):
    """统一执行架构的仿真交易网关。

    与 BacktestEngine 共享相同的 Protocol 组件：
    - Account（Decimal 精度资金管理）
    - PositionManager（T+N 状态机）
    - FillModel / FeeModel / SlippageModel / MarketRules

    与 PaperTradingGateway 的区别：
    - 使用 Account / PositionManager 域模型（非 dict）
    - FillModel.try_fill() 撮合（非字符串选择器）
    - MarketRules precheck（涨跌停/停牌/T+N）
    """

    def __init__(
        self,
        *,
        account: Account,
        position_mgr: PositionManager,
        fill_model: FillModel,
        fee_model: FeeModel,
        slippage_model: SlippageModel,
        rules: MarketRules,
        order_ttl_bars: int = 0,
    ) -> None:
        self._account = account
        self._position_mgr = position_mgr
        self._fill_model = fill_model
        self._fee_model = fee_model
        self._slippage_model = slippage_model
        self._rules = rules
        self._order_ttl_bars = order_ttl_bars

        self._orders: dict[str, GatewayOrder] = {}
        self._domain_orders: dict[str, Order] = {}
        self._connected = False
        self._exec_callbacks: list[ExecutionCallback] = []
        self._status_callbacks: list[StatusCallback] = []
        self._exec_counter = 0
        self._bar_counter = 0
        self._order_submit_bar: dict[str, int] = {}
        self._latest_prices: dict[str, float] = {}
        self._is_paper_gateway = True  # §13.16 marker for state_manager

    # -- TradingGateway ABC -------------------------------------------------

    @property
    def mode(self) -> TradingMode:
        return TradingMode.PAPER

    @property
    def is_connected(self) -> bool:
        return self._connected

    def connect(self) -> None:
        self._connected = True
        logger.info("PaperGateway connected")

    def disconnect(self) -> None:
        self._connected = False
        logger.info("PaperGateway disconnected")

    def submit_order(self, order: GatewayOrder) -> str:
        """提交订单 — 冻结资金/持仓，加入待撮合队列。"""
        if not self._connected:
            raise GatewayError("PaperGateway not connected")

        # 基础验证
        if order.qty <= 0:
            raise OrderValidationError("qty must be positive")
        if order.order_type == "LIMIT" and order.limit_price is None:
            raise OrderValidationError("LIMIT order requires limit_price")
        # §13.2: lot_size 整手检查（与 PaperTradingGateway / BacktestEngine 对齐）
        lot = self._rules.lot_size
        if lot > 1 and order.qty % lot != 0:
            raise OrderValidationError(
                f"qty {order.qty} not a multiple of lot_size {lot}"
            )

        side = Side.BUY if order.side == "BUY" else Side.SELL

        if side == Side.BUY:
            # 估算买入成本并冻结资金
            est_cost = self._estimate_buy_cost(order)
            ok = self._account.freeze_cash(est_cost, order.order_id, "buy_freeze")
            if not ok:
                raise InsufficientFundsError(
                    f"Insufficient funds: need ~{est_cost}, "
                    f"available {self._account.available_cash}"
                )
            order.metadata["frozen_cash"] = est_cost
        else:
            # 冻结持仓
            ok = self._position_mgr.freeze_for_sell(
                order.symbol, order.qty, order.order_id,
            )
            if not ok:
                avail = self._position_mgr.get_available_to_sell(order.symbol)
                raise InsufficientPositionError(
                    f"Insufficient position: need {order.qty}, available {avail}"
                )

        # 构造域模型 Order（供 FillModel.try_fill 使用）
        t_submit = order.submitted_at or datetime.now()
        domain_order = Order(
            order_id=order.order_id,
            symbol=order.symbol,
            side=side,
            qty=order.qty,
            order_type=OrderType.MARKET if order.order_type == "MARKET" else OrderType.LIMIT,
            t_submit=t_submit,
            limit_price=order.limit_price,
        )

        order.status = OrderStatus.SUBMITTED
        order.submitted_at = t_submit
        self._orders[order.order_id] = order
        self._domain_orders[order.order_id] = domain_order
        # Track submission bar for order TTL
        if self._order_ttl_bars > 0:
            self._order_submit_bar[order.order_id] = self._bar_counter

        logger.info(
            "PaperGateway order submitted: %s %s %s x%d",
            order.order_id, order.side, order.symbol, order.qty,
        )
        return order.order_id

    def submit_target_plan(
        self,
        plan: Any,
        *,
        prices: dict[str, float],
        account_value: float,
        current_positions: dict[str, int] | None = None,
        max_position_weight: float = 1.0,
        max_total_weight: float = 1.0,
    ) -> list[str]:
        """Consume a validated paper execution plan.

        Research exports targets, while this adapter is the only standard
        bridge that turns them into paper orders.  The plan remains the
        source of provenance; callers cannot submit a live plan through the
        paper gateway.
        """
        from aipha.research.execution_plan import execute_paper_target_plan
        # Target-plan sizing is based on the caller's as-of prices.  Seed the
        # gateway quote cache before submit_order estimates frozen cash; the
        # fallback price must never silently turn a valid plan into an
        # insufficient-funds rejection.
        self._latest_prices.update({str(symbol): float(price) for symbol, price in prices.items()})
        if current_positions is None:
            current_positions = {
                symbol: int(self._position_mgr.get_qty(symbol))
                for symbol in self._position_mgr.get_all_symbols()
            }
        # ``submit_order`` freezes a 5% safety buffer above notional.  Size a
        # fully invested target against the corresponding investable NAV so a
        # valid 100% plan remains executable after the buffer is reserved.
        from aipha.execution.trading_ops import BUY_FREEZE_BUFFER
        investable_account_value = account_value / float(BUY_FREEZE_BUFFER)
        return execute_paper_target_plan(
            self,
            plan,
            prices=prices,
            account_value=investable_account_value,
            current_positions=current_positions,
            max_position_weight=max_position_weight,
            max_total_weight=max_total_weight,
        )

    def cancel_order(self, order_id: str) -> bool:
        """撤单 — 解冻资金/持仓。"""
        gw_order = self._orders.get(order_id)
        if gw_order is None or gw_order.is_done:
            return False

        side = Side.BUY if gw_order.side == "BUY" else Side.SELL
        if side == Side.BUY:
            frozen = gw_order.metadata.get("frozen_cash", Decimal("0"))
            if frozen > Decimal("0"):
                self._account.unfreeze_cash(frozen, order_id, "cancel_unfreeze")
        else:
            self._position_mgr.unfreeze_for_sell(
                gw_order.symbol, gw_order.qty, order_id,
            )

        gw_order.status = OrderStatus.CANCELED
        gw_order.cancel_reason = "User canceled"
        self._domain_orders.pop(order_id, None)
        logger.info("PaperGateway order canceled: %s", order_id)
        return True

    def get_order(self, order_id: str) -> GatewayOrder | None:
        return self._orders.get(order_id)

    def get_active_orders(self) -> list[GatewayOrder]:
        return [o for o in self._orders.values() if o.is_active]

    def get_pending_order_codes(self) -> list[str]:
        """获取所有待撮合订单的标的代码。"""
        codes: set[str] = set()
        for o in self._orders.values():
            if o.is_active:
                codes.add(o.symbol)
        return list(codes)

    def get_account_snapshot(self) -> AccountSnapshot:
        """返回账户快照（资金 + 持仓市值）。"""
        positions = self._position_mgr.get_positions_dict()
        pos_value = Decimal(str(
            self._position_mgr.get_total_market_value(self._latest_prices)
        ))
        return AccountSnapshot(
            timestamp=datetime.now(),
            cash=self._account.cash,
            frozen_cash=self._account.frozen_cash,
            total_equity=self._account.cash + pos_value,
            positions=positions,
            positions_value=pos_value,
        )

    def on_execution(self, callback: ExecutionCallback) -> None:
        self._exec_callbacks.append(callback)

    def on_status_change(self, callback: StatusCallback) -> None:
        self._status_callbacks.append(callback)

    # -- Paper-specific API -------------------------------------------------

    def fill_pending(self, bars: dict[str, Bar]) -> list[FillResult]:
        """用实际行情撮合所有待处理订单。

        卖出订单优先撮合（先释放资金，再买入）。

        Args:
            bars: symbol → 当日 Bar 映射

        Returns:
            每个待处理订单的 FillResult 列表
        """
        # 更新所有标的的最新价格（用于 NAV 计算）
        for sym, bar in bars.items():
            self._latest_prices[sym] = bar.close

        results: list[FillResult] = []
        active = [o for o in self._orders.values() if o.is_active]

        # 卖出优先（与 BacktestEngine 行为一致）
        sells = [o for o in active if o.side == "SELL"]
        buys = [o for o in active if o.side == "BUY"]

        for gw_order in sells + buys:
            bar = bars.get(gw_order.symbol)
            if bar is None:
                continue

            domain_order = self._domain_orders.get(gw_order.order_id)
            if domain_order is None:
                continue

            # 使用 FillModel.try_fill（与回测引擎相同路径）
            fr: FillResult = self._fill_model.try_fill(
                order=domain_order, next_bar=bar, rules=self._rules,
            )

            if fr.fill is not None:
                # 限价单价格检查：确保成交价符合限价约束
                if gw_order.order_type == "LIMIT" and gw_order.limit_price is not None:
                    if gw_order.side == "BUY" and fr.fill.price > gw_order.limit_price:
                        # 买入限价单：成交价不能超过限价
                        results.append(FillResult(fill=None, no_fill_reason="LIMIT_NOT_TOUCHED"))
                        continue
                    elif gw_order.side == "SELL" and fr.fill.price < gw_order.limit_price:
                        # 卖出限价单：成交价不能低于限价
                        results.append(FillResult(fill=None, no_fill_reason="LIMIT_NOT_TOUCHED"))
                        continue

                # 应用滑点
                fill = self._slippage_model.apply(fr.fill, bar)
                # 计算费用
                fee_bk = self._fee_model.calc(fill)
                trade_date = bar.start_ts.date() if bar.start_ts else date.today()
                # 结算
                self._settle_fill(gw_order, fill, fee_bk, trade_date)
                # 更新 GatewayOrder 状态
                gw_order.filled_qty = fill.qty
                gw_order.avg_fill_price = Decimal(str(fill.price))
                gw_order.status = OrderStatus.FILLED
                gw_order.filled_at = fill.t_fill
                self._domain_orders.pop(gw_order.order_id, None)
                # 发送执行回报
                self._exec_counter += 1
                report = ExecutionReport(
                    order_id=fill.order_id,
                    exec_id=f"PG-{self._exec_counter:08d}",
                    symbol=fill.symbol,
                    side=gw_order.side,
                    fill_qty=fill.qty,
                    fill_price=fill.price,
                    fill_time=fill.t_fill,
                    commission=Decimal(str(fee_bk.total)),
                    is_last=True,
                )
                self._emit_execution(report)
            elif fr.is_rejected:
                gw_order.status = OrderStatus.REJECTED
                gw_order.reject_reason = fr.reject_reason
                self._cancel_frozen(gw_order)
                self._domain_orders.pop(gw_order.order_id, None)

            results.append(fr)

        return results

    def day_end(self, trade_date: date) -> None:
        """日终处理：T+1 解锁。

        注意：不自动过期挂单 — 挂单保留到下一日 fill_pending() 撮合。
        """
        self._position_mgr.day_end_process(trade_date)

    def process_bar(self, bar: Bar) -> list[ExecutionReport]:
        """LiveRunner 兼容接口：单 bar 撮合。

        委托给 fill_pending({symbol: bar})。收集执行回报通过
        临时回调捕获（fill_pending 内部已生成 ExecutionReport）。
        """
        self._bar_counter += 1
        # Auto-expire orders exceeding TTL
        if self._order_ttl_bars > 0:
            self._expire_ttl_orders()

        captured: list[ExecutionReport] = []
        self._exec_callbacks.append(captured.append)
        try:
            self.fill_pending({bar.symbol: bar})
        finally:
            self._exec_callbacks.pop()
        return captured

    def set_trade_date(self, trade_date: date) -> None:
        """LiveRunner 兼容接口：设置交易日期（触发 T+N 解锁）。"""
        self._position_mgr.day_end_process(trade_date)

    def expire_active_orders(self, before: datetime | None = None) -> int:
        """显式过期活跃订单（需要时手动调用）。

        Args:
            before: 若指定，仅过期 submitted_at < before 的订单。
                    用于 next_bar_open 语义：当日提交的订单保留到次日撮合。
                    若为 None，过期所有活跃订单（向后兼容）。

        Returns:
            过期订单数
        """
        count = 0
        for oid, gw_order in list(self._orders.items()):
            if not gw_order.is_active:
                continue
            if before is not None and gw_order.submitted_at is not None:
                if gw_order.submitted_at >= before:
                    continue  # 当日订单保留
            gw_order.status = OrderStatus.EXPIRED
            self._cancel_frozen(gw_order)
            self._domain_orders.pop(oid, None)
            count += 1
        return count

    # -- Internal helpers ---------------------------------------------------

    def _estimate_buy_cost(self, order: GatewayOrder) -> Decimal:
        """估算买入冻结金额（含缓冲用于滑点+手续费）。"""
        from aipha.execution.trading_ops import BUY_FREEZE_BUFFER
        price = order.limit_price or self._latest_prices.get(order.symbol, 100.0)
        notional = Decimal(str(price)) * Decimal(str(order.qty))
        return notional * Decimal(str(BUY_FREEZE_BUFFER))

    def _settle_fill(
        self, gw_order: GatewayOrder, fill: Fill, fee_bk: FeeBreakdown,
        trade_date: date,
    ) -> None:
        """结算成交：更新 Account + PositionManager。"""
        fee_dec = Decimal(str(fee_bk.total))
        cost_dec = Decimal(str(fill.notional))

        if fill.side == Side.BUY:
            self._account.settle_buy(
                order_id=fill.order_id,
                cost=cost_dec,
                fee=fee_dec,
                trade_date=trade_date,
            )
        else:
            self._account.settle_sell(
                order_id=fill.order_id,
                proceeds=cost_dec,
                fee=fee_dec,
                trade_date=trade_date,
            )

        self._position_mgr.process_fill(fill, trade_date)

    def _cancel_frozen(self, gw_order: GatewayOrder) -> None:
        """释放被拒绝/过期订单的冻结资金/持仓。"""
        if gw_order.side == "BUY":
            frozen = gw_order.metadata.get("frozen_cash", Decimal("0"))
            if frozen > Decimal("0"):
                self._account.unfreeze_cash(
                    frozen, gw_order.order_id, "rejected_unfreeze",
                )
        else:
            self._position_mgr.unfreeze_for_sell(
                gw_order.symbol, gw_order.qty, gw_order.order_id,
            )

    def _expire_ttl_orders(self) -> None:
        """过期超过 order_ttl_bars 的活跃订单。"""
        for oid, gw_order in list(self._orders.items()):
            if not gw_order.is_active:
                continue
            submit_bar = self._order_submit_bar.get(oid, 0)
            if self._bar_counter - submit_bar > self._order_ttl_bars:
                gw_order.status = OrderStatus.EXPIRED
                gw_order.cancel_reason = f"TTL expired ({self._order_ttl_bars} bars)"
                self._cancel_frozen(gw_order)
                self._domain_orders.pop(oid, None)
                self._order_submit_bar.pop(oid, None)

    def _emit_execution(self, report: ExecutionReport) -> None:
        for cb in self._exec_callbacks:
            try:
                cb(report)
            except Exception:
                logger.exception("PaperGateway execution callback error")

    # -- §13.16 公开 API (getters) ------------------------------------------

    def get_cash(self) -> Decimal:
        """返回当前可用现金。"""
        return self._account.cash

    def get_frozen_cash(self) -> Decimal:
        """返回冻结资金。"""
        return self._account.frozen_cash

    def get_positions_dict(self) -> dict[str, int]:
        """返回 {symbol: total_qty} 映射。"""
        return self._position_mgr.get_positions_dict()

    def get_position(self, symbol: str) -> int:
        """返回指定标的持仓数量。"""
        pos = self._position_mgr.positions.get(symbol)
        return pos.total_qty if pos else 0

    def get_available_position(self, symbol: str) -> int:
        """返回可卖数量（扣除冻结/T+N）。"""
        return self._position_mgr.get_available_to_sell(symbol)

    def get_position_details(self) -> dict:
        """获取持仓明细（含成本和市值），用于对账。

        Returns:
            dict[str, PositionDetail]
        """
        from aipha.risk.position_reconciler import PositionDetail

        result = {}
        for symbol, pos in self._position_mgr.positions.items():
            qty = pos.total_qty
            if qty <= 0:
                continue
            avg_cost = float(pos.avg_cost)
            price = self._latest_prices.get(symbol, avg_cost)
            result[symbol] = PositionDetail(
                symbol=symbol,
                qty=qty,
                avg_cost=avg_cost,
                market_value=qty * price,
                frozen_qty=pos.frozen_qty,
            )
        return result

    @property
    def initial_capital(self) -> Decimal:
        """返回初始资金。"""
        return self._account.initial_cash

    def reset(self) -> None:
        """重置网关状态到初始（用于测试）。"""
        self._account.cash = self._account.initial_cash
        self._account.frozen_cash = Decimal("0")
        self._position_mgr.positions.clear()
        self._latest_prices.clear()
        self._orders.clear()
        self._domain_orders.clear()

    # -- §13.16 序列化 / 反序列化 -------------------------------------------

    def save_state(self) -> dict[str, Any]:
        """序列化网关全部状态为可 JSON 化字典。"""
        positions = {}
        for sym, pos in self._position_mgr.positions.items():
            positions[sym] = {
                "total_qty": pos.total_qty,
                "avg_cost": float(pos.avg_cost),
                "today_bought_qty": pos.today_bought_qty,
                "frozen_qty": pos.frozen_qty,
                "t_plus_n": pos.t_plus_n,
            }
        return {
            "cash": float(self._account.cash),
            "frozen_cash": float(self._account.frozen_cash),
            "positions": positions,
            "latest_prices": dict(self._latest_prices),
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        """从字典恢复全部内部状态。"""
        from aipha.portfolio.position import Position

        self._account.cash = Decimal(str(state.get("cash", 0)))
        self._account.frozen_cash = Decimal(str(state.get("frozen_cash", 0)))

        self._position_mgr.positions.clear()
        for sym, pdata in state.get("positions", {}).items():
            pos = Position(
                symbol=sym,
                total_qty=pdata["total_qty"],
                avg_cost=Decimal(str(pdata.get("avg_cost", 0))),
                today_bought_qty=pdata.get("today_bought_qty", 0),
                frozen_qty=pdata.get("frozen_qty", 0),
                t_plus_n=pdata.get("t_plus_n",
                                   self._position_mgr.default_t_plus_n),
            )
            self._position_mgr.positions[sym] = pos

        self._latest_prices = state.get("latest_prices", {})

    # -- §13.16 工厂方法 ----------------------------------------------------

    @classmethod
    def from_cb_mode(
        cls, initial_capital: float = 500_000, **kwargs: Any,
    ) -> "PaperGateway":
        """创建 CB 默认参数的 PaperGateway。"""
        from aipha.market.rules_convertible_bond import (
            MarketRulesConvertibleBond,
        )

        rules = MarketRulesConvertibleBond()
        return cls._from_rules(rules, initial_capital, t_plus_n=0, **kwargs)

    @classmethod
    def from_cn_equity(
        cls, initial_capital: float = 1_000_000, **kwargs: Any,
    ) -> "PaperGateway":
        """创建 A 股默认参数的 PaperGateway。"""
        from aipha.market.rules_cn_equity import MarketRulesCNEquity

        rules = MarketRulesCNEquity()
        return cls._from_rules(rules, initial_capital, t_plus_n=1, **kwargs)

    @classmethod
    def _from_rules(
        cls,
        rules: MarketRules,
        initial_capital: float,
        t_plus_n: int,
        **kwargs: Any,
    ) -> "PaperGateway":
        """内部工厂：从 MarketRules 构建完整组件。"""
        account = Account(initial_cash=Decimal(str(initial_capital)))
        pm = PositionManager(default_t_plus_n=t_plus_n)
        fill_model = NextBarOpenFillModel()

        commission = CommissionModel(
            rate=float(rules.commission_rate),
            min_fee=float(rules.commission_min),
        )
        stamp_rate = float(getattr(rules, "stamp_tax_rate", 0))
        transfer_rate = float(getattr(rules, "transfer_fee_rate", 0))
        stamp_tax = StampTaxModel(sell_rate=stamp_rate)
        transfer_fee = TransferFeeModel(rate=transfer_rate)
        fee_model = FeeModelCNEquity(
            commission=commission,
            stamp_tax=stamp_tax,
            transfer_fee=transfer_fee,
        )
        slippage_model = FixedBpsSlippageModel(
            bps=float(rules.slippage_bps_default),
        )
        gw = cls(
            account=account,
            position_mgr=pm,
            fill_model=fill_model,
            fee_model=fee_model,
            slippage_model=slippage_model,
            rules=rules,
            **kwargs,
        )
        gw.connect()
        return gw

    @classmethod
    def from_config(
        cls,
        initial_capital: float = 1_000_000,
        commission_rate: float = 0.0000854,
        commission_min: float = 5.0,
        stamp_tax_rate: float = 0.0005,
        slippage_bps: float = 10.0,
        lot_size: int = 100,
        t_plus_n: int = 1,
        *,
        fill_model_name: str = "next_bar_open",
        slippage_model: "SlippageModel | None" = None,
        transfer_fee_rate: float = 0.00001,
        asset_type: str = "cn_equity",
        **kwargs: Any,
    ) -> "PaperGateway":
        """§13.16 兼容工厂：接受原始参数构建 PaperGateway。

        参数列表与旧 PaperTradingGateway.__init__ 对齐，
        便于 live_runner / engine_manager 迁移。
        """
        from aipha.execution.fill_models import get_fill_model

        if asset_type == "convertible_bond":
            from aipha.market.rules_convertible_bond import (
                MarketRulesConvertibleBond,
            )
            rules: MarketRules = MarketRulesConvertibleBond(
                lot_size=lot_size, t_plus=t_plus_n,
            )
        elif asset_type == "etf":
            from aipha.market.rules_etf import MarketRulesETF
            rules = MarketRulesETF(lot_size=lot_size)
        else:
            from aipha.market.rules_cn_equity import MarketRulesCNEquity
            rules = MarketRulesCNEquity(lot_size=lot_size)

        account = Account(initial_cash=Decimal(str(initial_capital)))
        pm = PositionManager(default_t_plus_n=t_plus_n)
        fm = get_fill_model(fill_model_name)
        commission = CommissionModel(
            rate=float(commission_rate), min_fee=float(commission_min),
        )
        stamp_tax = StampTaxModel(sell_rate=float(stamp_tax_rate))
        transfer_fee = TransferFeeModel(rate=float(transfer_fee_rate))
        fee = FeeModelCNEquity(
            commission=commission,
            stamp_tax=stamp_tax,
            transfer_fee=transfer_fee,
        )
        slip = slippage_model or FixedBpsSlippageModel(bps=slippage_bps)
        gw = cls(
            account=account,
            position_mgr=pm,
            fill_model=fm,
            fee_model=fee,
            slippage_model=slip,
            rules=rules,
            **kwargs,
        )
        gw.connect()
        return gw
