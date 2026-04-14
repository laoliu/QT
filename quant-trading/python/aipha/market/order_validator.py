"""
订单校验器 - 统一拒单校验与原因日志

整合多种校验规则：
- 交易时段校验（基于交易日历）
- 涨跌停/停牌校验（基于HaltChecker）
- T+1可卖数量校验（基于Position）
- 委托数量校验（最小单位100股）
- 资金校验

每次校验失败都记录详细拒单原因，用于审计与调试。
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import List, Optional

from aipha.data.calendar import CNStockCalendar, TradingSession
from aipha.market.halt_checker import HaltChecker, HaltStatus
from aipha.portfolio.position import Position


class OrderSide(Enum):
    """订单方向"""

    BUY = "buy"
    SELL = "sell"


class RejectReason(Enum):
    """拒单原因分类"""

    # 时段相关
    NON_TRADING_DAY = "non_trading_day"  # 非交易日
    MARKET_CLOSED = "market_closed"  # 非交易时段
    CALL_AUCTION_ONLY = "call_auction_only"  # 仅集合竞价

    # 市场规则
    STOCK_HALTED = "stock_halted"  # 停牌
    LIMIT_UP_NO_BUY = "limit_up_no_buy"  # 涨停无法买入
    LIMIT_DOWN_NO_SELL = "limit_down_no_sell"  # 跌停无法卖出
    PRICE_EXCEEDS_LIMIT = "price_exceeds_limit"  # 价格超限

    # 持仓相关
    INSUFFICIENT_POSITION = "insufficient_position"  # 可卖不足
    T_PLUS_1_RESTRICTION = "t_plus_1_restriction"  # T+1限制

    # 数量相关
    INVALID_QUANTITY = "invalid_quantity"  # 无效数量
    NOT_ROUND_LOT = "not_round_lot"  # 非整手（非 lot_size 整数倍）
    EXCEEDS_MAX_QTY = "exceeds_max_qty"  # 超过单笔上限

    # 资金相关
    INSUFFICIENT_FUNDS = "insufficient_funds"  # 资金不足

    # 其他
    SYMBOL_NOT_TRADABLE = "symbol_not_tradable"  # 标的不可交易
    UNKNOWN = "unknown"  # 未知原因


@dataclass
class RejectLog:
    """拒单日志"""

    timestamp: datetime
    order_id: Optional[str]
    symbol: str
    side: OrderSide
    price: Decimal
    quantity: int
    reason: RejectReason
    reason_detail: str
    context: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        """导出为字典"""
        return {
            "timestamp": self.timestamp.isoformat(),
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "price": str(self.price),
            "quantity": self.quantity,
            "reason": self.reason.value,
            "reason_detail": self.reason_detail,
            "context": self.context,
        }


@dataclass
class ValidationResult:
    """校验结果"""

    is_valid: bool
    reject_reason: Optional[RejectReason] = None
    reject_detail: str = ""
    warnings: List[str] = field(default_factory=list)


class OrderValidator:
    """
    订单校验器

    整合交易日历、市场规则、持仓状态进行统一校验，
    并记录所有拒单原因用于审计。
    """

    # 默认最小交易单位（A股 = 100股，可转债 = 10张）
    DEFAULT_LOT_SIZE = 100

    # 单笔最大委托数量
    MAX_ORDER_QTY = 1_000_000

    def __init__(
        self,
        calendar: Optional[CNStockCalendar] = None,
        halt_checker: Optional[HaltChecker] = None,
        lot_size: int | None = None,
    ):
        """
        初始化订单校验器

        Args:
            calendar: 交易日历（可选，默认创建新实例）
            halt_checker: 涨跌停检查器（可选，默认创建新实例）
            lot_size: 最小交易单位（默认 100，可转债传 10）
        """
        self.calendar = calendar or CNStockCalendar()
        self.halt_checker = halt_checker or HaltChecker()
        self._lot_size = lot_size if lot_size is not None else self.DEFAULT_LOT_SIZE
        self._reject_logs: List[RejectLog] = []

    def _log_reject(
        self,
        order_id: Optional[str],
        symbol: str,
        side: OrderSide,
        price: Decimal,
        quantity: int,
        reason: RejectReason,
        reason_detail: str,
        context: Optional[dict] = None,
    ) -> None:
        """记录拒单日志"""
        log = RejectLog(
            timestamp=datetime.now(),
            order_id=order_id,
            symbol=symbol,
            side=side,
            price=price,
            quantity=quantity,
            reason=reason,
            reason_detail=reason_detail,
            context=context or {},
        )
        self._reject_logs.append(log)

    def validate_trading_session(
        self, check_time: datetime
    ) -> ValidationResult:
        """
        校验交易时段

        Args:
            check_time: 校验时间

        Returns:
            校验结果
        """
        check_date = check_time.date()

        # 检查是否交易日
        if not self.calendar.is_trading_day(check_date):
            return ValidationResult(
                is_valid=False,
                reject_reason=RejectReason.NON_TRADING_DAY,
                reject_detail=f"{check_date}不是交易日",
            )

        # 检查交易时段
        session = self.calendar.get_session(check_time)

        if session == TradingSession.CLOSED:
            return ValidationResult(
                is_valid=False,
                reject_reason=RejectReason.MARKET_CLOSED,
                reject_detail="当前时段市场已关闭",
            )

        if session == TradingSession.LUNCH_BREAK:
            return ValidationResult(
                is_valid=False,
                reject_reason=RejectReason.MARKET_CLOSED,
                reject_detail="午间休市",
            )

        warnings = []
        if session in (
            TradingSession.MORNING_CALL_AUCTION,
            TradingSession.CLOSING_CALL_AUCTION,
        ):
            warnings.append(f"当前为集合竞价时段: {session.value}")

        return ValidationResult(is_valid=True, warnings=warnings)

    def validate_quantity(self, quantity: int) -> ValidationResult:
        """
        校验委托数量

        Args:
            quantity: 委托数量

        Returns:
            校验结果
        """
        if quantity <= 0:
            return ValidationResult(
                is_valid=False,
                reject_reason=RejectReason.INVALID_QUANTITY,
                reject_detail=f"委托数量必须为正: {quantity}",
            )

        if quantity % self._lot_size != 0:
            return ValidationResult(
                is_valid=False,
                reject_reason=RejectReason.NOT_ROUND_LOT,
                reject_detail=f"委托数量{quantity}不是{self._lot_size}的整数倍",
            )

        if quantity > self.MAX_ORDER_QTY:
            return ValidationResult(
                is_valid=False,
                reject_reason=RejectReason.EXCEEDS_MAX_QTY,
                reject_detail=f"委托数量{quantity}超过单笔上限{self.MAX_ORDER_QTY}",
            )

        return ValidationResult(is_valid=True)

    def validate_price(
        self,
        symbol: str,
        price: Decimal,
        prev_close: Decimal,
        is_st: bool = False,
    ) -> ValidationResult:
        """
        校验委托价格（涨跌停限制）

        Args:
            symbol: 股票代码
            price: 委托价格
            prev_close: 前收盘价
            is_st: 是否ST股票

        Returns:
            校验结果
        """
        board_type = self.halt_checker.get_board_type(symbol, is_st)
        limit_up, limit_down = self.halt_checker.calculate_limit_prices(
            prev_close, board_type
        )

        if price > limit_up:
            return ValidationResult(
                is_valid=False,
                reject_reason=RejectReason.PRICE_EXCEEDS_LIMIT,
                reject_detail=f"委托价格{price}超过涨停价{limit_up}",
            )

        if price < limit_down:
            return ValidationResult(
                is_valid=False,
                reject_reason=RejectReason.PRICE_EXCEEDS_LIMIT,
                reject_detail=f"委托价格{price}低于跌停价{limit_down}",
            )

        return ValidationResult(is_valid=True)

    def validate_halt_status(
        self,
        symbol: str,
        side: OrderSide,
        current_price: Optional[Decimal],
        prev_close: Decimal,
        is_halted: bool = False,
        is_st: bool = False,
    ) -> ValidationResult:
        """
        校验停牌/涨跌停状态

        Args:
            symbol: 股票代码
            side: 订单方向
            current_price: 当前价格
            prev_close: 前收盘价
            is_halted: 是否停牌
            is_st: 是否ST股票

        Returns:
            校验结果
        """
        market_status = self.halt_checker.get_market_status(
            symbol=symbol,
            current_price=current_price,
            prev_close=prev_close,
            is_halted=is_halted,
            is_st=is_st,
        )

        # 停牌检查
        if market_status.halt_status != HaltStatus.TRADING:
            return ValidationResult(
                is_valid=False,
                reject_reason=RejectReason.STOCK_HALTED,
                reject_detail=f"股票{symbol}当前状态: {market_status.halt_status.value}",
            )

        # 涨停买入检查
        if side == OrderSide.BUY and not market_status.can_buy:
            return ValidationResult(
                is_valid=False,
                reject_reason=RejectReason.LIMIT_UP_NO_BUY,
                reject_detail=f"股票{symbol}涨停封板，无法买入",
            )

        # 跌停卖出检查
        if side == OrderSide.SELL and not market_status.can_sell:
            return ValidationResult(
                is_valid=False,
                reject_reason=RejectReason.LIMIT_DOWN_NO_SELL,
                reject_detail=f"股票{symbol}跌停封板，无法卖出",
            )

        return ValidationResult(is_valid=True)

    def validate_sell_position(
        self,
        position: Position,
        sell_qty: int,
    ) -> ValidationResult:
        """
        校验卖出持仓（T+1规则）

        Args:
            position: 持仓对象
            sell_qty: 卖出数量

        Returns:
            校验结果
        """
        if position.available_to_sell < sell_qty:
            if position.today_bought_qty > 0:
                return ValidationResult(
                    is_valid=False,
                    reject_reason=RejectReason.T_PLUS_1_RESTRICTION,
                    reject_detail=(
                        f"可卖{position.available_to_sell}股，"
                        f"今日买入{position.today_bought_qty}股不可卖"
                    ),
                )
            else:
                return ValidationResult(
                    is_valid=False,
                    reject_reason=RejectReason.INSUFFICIENT_POSITION,
                    reject_detail=(
                        f"可卖{position.available_to_sell}股，"
                        f"不足{sell_qty}股"
                    ),
                )

        return ValidationResult(is_valid=True)

    def validate_buy_funds(
        self,
        available_cash: Decimal,
        order_price: Decimal,
        order_qty: int,
        commission_rate: Decimal = Decimal("0.0000854"),
        commission_min: Decimal = Decimal("5"),
    ) -> ValidationResult:
        """
        校验买入资金

        Args:
            available_cash: 可用资金
            order_price: 委托价格
            order_qty: 委托数量
            commission_rate: 佣金费率
            commission_min: 最低佣金（A股=5元, CB=0元）
                           §13.1: 权威值见 MarketRulesCNEquity / MarketRulesConvertibleBond

        Returns:
            校验结果
        """
        order_amount = order_price * order_qty
        commission = max(order_amount * commission_rate, commission_min)
        total_required = order_amount + commission

        if available_cash < total_required:
            return ValidationResult(
                is_valid=False,
                reject_reason=RejectReason.INSUFFICIENT_FUNDS,
                reject_detail=(
                    f"需要资金{total_required:.2f}元"
                    f"（含佣金{commission:.2f}），"
                    f"可用{available_cash:.2f}元"
                ),
            )

        return ValidationResult(is_valid=True)

    def validate_order(
        self,
        order_id: Optional[str],
        symbol: str,
        side: OrderSide,
        price: Decimal,
        quantity: int,
        check_time: datetime,
        prev_close: Decimal,
        current_price: Optional[Decimal] = None,
        position: Optional[Position] = None,
        available_cash: Optional[Decimal] = None,
        is_halted: bool = False,
        is_st: bool = False,
    ) -> ValidationResult:
        """
        综合订单校验

        Args:
            order_id: 订单ID
            symbol: 股票代码
            side: 订单方向
            price: 委托价格
            quantity: 委托数量
            check_time: 校验时间
            prev_close: 前收盘价
            current_price: 当前价格（用于涨跌停判断）
            position: 持仓（卖单需要）
            available_cash: 可用资金（买单需要）
            is_halted: 是否停牌
            is_st: 是否ST股票

        Returns:
            校验结果
        """
        # 1. 交易时段校验
        result = self.validate_trading_session(check_time)
        if not result.is_valid:
            self._log_reject(
                order_id, symbol, side, price, quantity,
                result.reject_reason, result.reject_detail,
                {"check_time": check_time.isoformat()},
            )
            return result

        # 2. 数量校验
        result = self.validate_quantity(quantity)
        if not result.is_valid:
            self._log_reject(
                order_id, symbol, side, price, quantity,
                result.reject_reason, result.reject_detail,
            )
            return result

        # 3. 价格校验
        result = self.validate_price(symbol, price, prev_close, is_st)
        if not result.is_valid:
            self._log_reject(
                order_id, symbol, side, price, quantity,
                result.reject_reason, result.reject_detail,
                {"prev_close": str(prev_close)},
            )
            return result

        # 4. 停牌/涨跌停校验
        result = self.validate_halt_status(
            symbol, side, current_price, prev_close, is_halted, is_st
        )
        if not result.is_valid:
            self._log_reject(
                order_id, symbol, side, price, quantity,
                result.reject_reason, result.reject_detail,
                {"current_price": str(current_price) if current_price else None},
            )
            return result

        # 5. 卖单持仓校验
        if side == OrderSide.SELL and position is not None:
            result = self.validate_sell_position(position, quantity)
            if not result.is_valid:
                self._log_reject(
                    order_id, symbol, side, price, quantity,
                    result.reject_reason, result.reject_detail,
                    {"position": position.to_dict()},
                )
                return result

        # 6. 买单资金校验
        if side == OrderSide.BUY and available_cash is not None:
            result = self.validate_buy_funds(available_cash, price, quantity)
            if not result.is_valid:
                self._log_reject(
                    order_id, symbol, side, price, quantity,
                    result.reject_reason, result.reject_detail,
                    {"available_cash": str(available_cash)},
                )
                return result

        return ValidationResult(is_valid=True)

    def get_reject_logs(self) -> List[RejectLog]:
        """获取所有拒单日志"""
        return list(self._reject_logs)

    def get_reject_summary(self) -> dict:
        """获取拒单统计摘要"""
        summary = {}
        for log in self._reject_logs:
            reason = log.reason.value
            summary[reason] = summary.get(reason, 0) + 1
        return summary

    def clear_logs(self) -> None:
        """清空日志"""
        self._reject_logs.clear()

    def export_logs(self) -> List[dict]:
        """导出日志为字典列表"""
        return [log.to_dict() for log in self._reject_logs]
