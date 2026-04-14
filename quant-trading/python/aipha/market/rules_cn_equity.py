from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any

from aipha.domain.models import Bar, Order, OrderType, Side
from aipha.market.base import PrecheckDecision  # noqa: F401 — re-export for backward compat
from aipha.market.halt_checker import BoardType, HaltChecker

logger = logging.getLogger(__name__)


class RejectReason(str, Enum):
    """订单拒绝原因枚举"""

    OUT_OF_SESSION = "OUT_OF_SESSION"
    SUSPENDED = "SUSPENDED"
    LIMIT_UP = "LIMIT_UP"
    LIMIT_DOWN = "LIMIT_DOWN"
    LOT_SIZE_ROUNDING = "LOT_SIZE_ROUNDING"
    PRICE_OUT_OF_RANGE = "PRICE_OUT_OF_RANGE"
    EXCEEDS_MAX_ORDER_QTY = "EXCEEDS_MAX_ORDER_QTY"
    T_PLUS_1_VIOLATION = "T_PLUS_1_VIOLATION"


# 缓存 HaltChecker 实例（无状态，可复用）
_halt_checker = HaltChecker()


@dataclass(frozen=True, slots=True)
class MarketRulesCNEquity:
    """A-share market rules (V3 — 完整接口对齐 CB 规则).

    V3 增强:
    - 新增 t_plus / commission_rate / commission_min / stamp_tax_rate 属性
    - 新增 calculate_fee() / get_price_limits() 方法
    - 科创板/创业板差异化 lot_size 校验

    Implements MarketRules Protocol (see market.base).
    """

    lot_size: int = 100
    max_order_qty: int = 1_000_000  # 单笔最大委托量（股数）
    t_plus: int = 1  # A 股 T+1

    # 费用属性 — 与 CB 规则接口对齐
    commission_rate: Decimal = Decimal("0.0000854")  # 万0.854
    commission_min: Decimal = Decimal("5")  # 最低 5 元
    stamp_tax_rate: Decimal = Decimal("0.0005")  # 卖出万5（2023年8月28日起）
    transfer_fee_rate: Decimal = Decimal("0.00001")  # 过户费万0.1

    # 滑点默认值（§13.1 权威源）
    slippage_bps_default: float = 10.0  # A 股默认滑点 10bps

    def precheck_order(self, *, order: Order, bar: Bar, **kwargs: Any) -> PrecheckDecision:
        """对订单进行 A 股市场规则预检查（手数、涨跌停、停牌、价格范围等）。"""
        # 有效 lot_size — 科创板为 200（仅当用户未自定义 lot_size 时）
        effective_lot = self._effective_lot_size(order.symbol)

        if order.qty % effective_lot != 0:
            return PrecheckDecision(allowed=False, reason=RejectReason.LOT_SIZE_ROUNDING)

        # 最大委托量检查
        if order.qty > self.max_order_qty:
            return PrecheckDecision(
                allowed=False,
                reason=RejectReason.EXCEEDS_MAX_ORDER_QTY,
            )

        # Suspension.
        if bar.is_suspended:
            return PrecheckDecision(allowed=False, reason=RejectReason.SUSPENDED)

        # Limit states — 使用 HaltChecker 进行板块差异化涨跌幅检查
        if order.side == Side.BUY and bar.is_limit_up is True:
            return PrecheckDecision(allowed=False, reason=RejectReason.LIMIT_UP)
        if order.side == Side.SELL and bar.is_limit_down is True:
            return PrecheckDecision(allowed=False, reason=RejectReason.LIMIT_DOWN)

        # 限价单价格范围校验：价格不得超出涨跌停价
        if order.order_type == OrderType.LIMIT:
            if order.limit_price is None:
                raise ValueError("limit_price must be set for LIMIT orders")

            # §13.3: prev_close 缺失时记录 warning 并标记订单为未校验
            if not bar.prev_close or bar.prev_close <= 0:
                logger.warning(
                    "A股 prev_close 缺失或无效 (symbol=%s, prev_close=%s), "
                    "涨跌停检查被跳过 — §13.3 安全关键参数",
                    bar.symbol,
                    bar.prev_close,
                )
                return PrecheckDecision(
                    allowed=True,
                    warning=(
                        f"prev_close 缺失 (symbol={bar.symbol}), "
                        "涨跌停检查未执行 — 订单放行但标记为未校验"
                    ),
                )

            is_st = getattr(bar, 'is_st', False)
            board_type = _halt_checker.get_board_type(order.symbol, is_st=is_st)
            limit_up, limit_down = _halt_checker.calculate_limit_prices(
                Decimal(str(bar.prev_close)), board_type
            )
            limit_price_d = Decimal(str(order.limit_price))
            if limit_price_d > limit_up or limit_price_d < limit_down:
                return PrecheckDecision(
                    allowed=False,
                    reason=RejectReason.PRICE_OUT_OF_RANGE,
                )

        return PrecheckDecision(allowed=True)

    def get_price_limits(
        self,
        prev_close: float,
        symbol: str = "",
        is_st: bool = False,
    ) -> tuple[float | None, float | None]:
        """计算涨跌停价格

        Args:
            prev_close: 前一日收盘价
            symbol: 股票代码（用于判断板块）
            is_st: 是否 ST

        Returns:
            (涨停价, 跌停价)
        """
        if prev_close <= 0:
            return None, None
        board_type = _halt_checker.get_board_type(symbol, is_st=is_st) if symbol else BoardType.MAIN
        limit_up, limit_down = _halt_checker.calculate_limit_prices(
            Decimal(str(prev_close)), board_type
        )
        return float(limit_up), float(limit_down)

    def calculate_fee(
        self,
        *,
        price: Decimal,
        qty: int,
        side: Side,
    ) -> Decimal:
        """计算 A 股交易费用

        A 股费用结构:
        - 佣金: 双向，按成交额计算，有最低收费
        - 印花税: 仅卖出方向
        - 过户费: 双向

        Args:
            price: 成交价格
            qty: 成交数量（股）
            side: 买卖方向

        Returns:
            总费用
        """
        trade_value = price * qty

        # qty=0 无实际成交，不收费
        if qty == 0:
            return Decimal("0")

        # 佣金（双向）
        commission = max(trade_value * self.commission_rate, self.commission_min)

        # 印花税（仅卖出）
        stamp_tax = trade_value * self.stamp_tax_rate if side == Side.SELL else Decimal("0")

        # 过户费（双向）
        transfer_fee = trade_value * self.transfer_fee_rate

        return commission + stamp_tax + transfer_fee

    def _effective_lot_size(self, symbol: str) -> int:
        """根据股票代码确定有效 lot_size.

        仅当 lot_size 为默认值 100 时，科创板（688xxx）自动升级为 200。
        若用户显式设置了 lot_size（如 CB 用 10），则忽略板块差异。
        """
        if self.lot_size == 100 and symbol.startswith("688"):
            return 200
        return self.lot_size
