"""ETF 市场规则（§13.1 — 资产类型唯一权威源）。

沪深上市 ETF 的交易规则：
- T+1 交易（部分跨境 ETF / 货基 ETF 为 T+0，此处取保守值）
- 免征印花税
- 无过户费
- 最小交易单位：100 份
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from aipha.domain.models import Bar, Order, Side
from aipha.market.base import PrecheckDecision


@dataclass(frozen=True, slots=True)
class MarketRulesETF:
    """ETF 市场规则。

    Attributes:
        lot_size: 最小交易单位（100 份）
        t_plus: T+N 规则（1 = 次日可卖）
        commission_rate: 佣金费率（同 A 股万 0.854）
        commission_min: 最低佣金（5 元）
        stamp_tax_rate: 印花税（ETF 免征）
        transfer_fee_rate: 过户费（ETF 无）
        slippage_bps_default: 默认滑点（5bps）
    """

    lot_size: int = 100
    t_plus: int = 1
    commission_rate: Decimal = Decimal("0.0000854")
    commission_min: Decimal = Decimal("5")
    stamp_tax_rate: Decimal = Decimal("0")
    transfer_fee_rate: Decimal = Decimal("0")
    slippage_bps_default: float = 5.0

    def precheck_order(self, *, order: Order, bar: Bar, **kwargs: Any) -> PrecheckDecision:
        """ETF 订单预检查：手数、停牌。

        ETF 无涨跌停限制（除极端情况），预检查较宽松。
        """
        if order.qty % self.lot_size != 0:
            return PrecheckDecision(allowed=False, reason="LOT_SIZE_ROUNDING")

        if bar.is_suspended:
            return PrecheckDecision(allowed=False, reason="SUSPENDED")

        # ETF 一般无涨跌停，但极端情况下可能临停
        if order.side == Side.BUY and bar.is_limit_up is True:
            return PrecheckDecision(allowed=False, reason="LIMIT_UP")
        if order.side == Side.SELL and bar.is_limit_down is True:
            return PrecheckDecision(allowed=False, reason="LIMIT_DOWN")

        return PrecheckDecision(allowed=True)
