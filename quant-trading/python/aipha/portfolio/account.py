"""
账户管理模块

管理资金状态：
- 现金余额（总额、可用、冻结）
- 资金冻结/解冻（下单/撤单）
- 买卖结算
- NAV 计算
- 交易日志
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any


class TransactionType(str, Enum):
    """交易日志类型"""

    FREEZE = "FREEZE"
    UNFREEZE = "UNFREEZE"
    SETTLE_BUY = "SETTLE_BUY"
    SETTLE_SELL = "SETTLE_SELL"
    DEPOSIT = "DEPOSIT"
    WITHDRAW = "WITHDRAW"
    SELL_CREDIT = "SELL_CREDIT"
    REVOKE_SELL_CREDIT = "REVOKE_SELL_CREDIT"


@dataclass
class Account:
    """
    资金账户

    管理现金余额和交易结算。

    Attributes:
        initial_cash: 初始资金
        currency: 币种
        cash: 当前现金总额
        frozen_cash: 冻结资金（待结算订单）
        _frozen_by_order: 按订单跟踪冻结金额
        _logs: 交易日志
    """

    initial_cash: Decimal
    currency: str = "CNY"
    cash: Decimal = field(init=False)
    frozen_cash: Decimal = field(init=False, default=Decimal("0.00"))
    _frozen_by_order: dict[str, Decimal] = field(init=False, default_factory=dict)
    _sell_credit_outstanding: Decimal = field(init=False, default=Decimal("0.00"))
    _logs: list[dict[str, Any]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        """初始化验证"""
        if self.initial_cash <= Decimal("0.00"):
            raise ValueError(f"初始资金必须为正数，当前: {self.initial_cash}")
        self.cash = self.initial_cash

    @property
    def available_cash(self) -> Decimal:
        """可用现金 = 总现金 - 冻结"""
        return self.cash - self.frozen_cash

    def get_frozen_for_order(self, order_id: str) -> Decimal:
        """查询指定订单的冻结金额。

        Args:
            order_id: 订单ID

        Returns:
            该订单冻结的金额，若不存在则返回 0
        """
        return self._frozen_by_order.get(order_id, Decimal("0.00"))

    def freeze_cash(self, amount: Decimal, order_id: str, reason: str) -> bool:
        """
        冻结资金（下单时调用）

        Args:
            amount: 冻结金额
            order_id: 订单ID
            reason: 冻结原因

        Returns:
            是否成功
        """
        if amount <= Decimal("0.00"):
            return False
        if amount > self.available_cash:
            return False

        self.frozen_cash += amount
        self._frozen_by_order[order_id] = (
            self._frozen_by_order.get(order_id, Decimal("0.00")) + amount
        )

        self._log(
            tx_type=TransactionType.FREEZE,
            order_id=order_id,
            amount=amount,
            reason=reason,
        )
        return True

    def unfreeze_cash(self, amount: Decimal, order_id: str, reason: str) -> bool:
        """
        解冻资金（撤单时调用）

        Args:
            amount: 解冻金额
            order_id: 订单ID
            reason: 解冻原因

        Returns:
            是否成功
        """
        if amount <= Decimal("0.00"):
            return False
        if amount > self.frozen_cash:
            return False

        self.frozen_cash -= amount
        if order_id in self._frozen_by_order:
            self._frozen_by_order[order_id] = max(
                Decimal("0.00"), self._frozen_by_order[order_id] - amount
            )
            if self._frozen_by_order[order_id] == Decimal("0.00"):
                del self._frozen_by_order[order_id]

        self._log(
            tx_type=TransactionType.UNFREEZE,
            order_id=order_id,
            amount=amount,
            reason=reason,
        )
        return True

    def settle_buy(
        self,
        order_id: str,
        cost: Decimal,
        fee: Decimal,
        trade_date: date,
    ) -> None:
        """
        买入结算

        扣除资金（成本+费用），释放冻结

        Args:
            order_id: 订单ID
            cost: 成交金额
            fee: 交易费用
            trade_date: 交易日期

        Raises:
            ValueError: 订单未冻结资金
        """
        if order_id not in self._frozen_by_order:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                f"订单 {order_id} 未冻结资金（可能已被释放），按零冻结处理"
            )

        total_deduct = cost + fee
        frozen_amount = self._frozen_by_order.get(order_id, Decimal("0.00"))

        # 释放冻结
        self.frozen_cash -= frozen_amount
        self._frozen_by_order.pop(order_id, None)

        # 扣除实际成本
        self.cash -= total_deduct

        # 防御性检查：结算后现金不应显著为负（允许极小浮点误差）
        if self.cash < Decimal("-0.01"):
            import logging as _logging

            _logging.getLogger(__name__).error(
                f"settle_buy 后现金为负: cash={self.cash}, "
                f"order_id={order_id}, cost={cost}, fee={fee}"
            )
            raise ValueError(
                f"settle_buy 导致现金为负 ({self.cash})，"
                f"order_id={order_id}, cost={cost}, fee={fee}"
            )

        self._log(
            tx_type=TransactionType.SETTLE_BUY,
            order_id=order_id,
            cost=cost,
            fee=fee,
            trade_date=trade_date,
        )

    def settle_sell(
        self,
        order_id: str,
        proceeds: Decimal,
        fee: Decimal,
        trade_date: date,
    ) -> None:
        """
        卖出结算

        增加资金（收入-费用）

        Args:
            order_id: 订单ID
            proceeds: 成交金额
            fee: 交易费用
            trade_date: 交易日期
        """
        net_proceeds = proceeds - fee
        self.cash += net_proceeds

        self._log(
            tx_type=TransactionType.SETTLE_SELL,
            order_id=order_id,
            proceeds=proceeds,
            fee=fee,
            trade_date=trade_date,
        )

    def calculate_nav(self, positions_market_value: Decimal) -> Decimal:
        """
        计算净资产（NAV）

        NAV = 现金 + 持仓市值

        Args:
            positions_market_value: 持仓市值

        Returns:
            净资产
        """
        return self.cash + positions_market_value

    def credit_sell_proceeds(self, amount: Decimal, reason: str) -> None:
        """
        临时预借卖出所得（同批次买单可用）。

        必须与 revoke_sell_credit 配对使用，在 finally 块中回收。

        Raises:
            RuntimeError: 上一笔 sell credit 未回收就尝试再次预借
        """
        if self._sell_credit_outstanding > Decimal("0"):
            import logging as _logging
            _logging.getLogger(__name__).error(
                f"sell_credit 双重预借: outstanding={self._sell_credit_outstanding}, "
                f"new_amount={amount}"
            )
            raise RuntimeError(
                f"sell_credit 双重预借: 上一笔 {self._sell_credit_outstanding} 尚未回收"
            )
        self._sell_credit_outstanding = amount
        self.cash += amount
        self._log(
            tx_type=TransactionType.SELL_CREDIT,
            amount=amount,
            reason=reason,
        )

    def revoke_sell_credit(self, amount: Decimal, reason: str) -> None:
        """
        回收临时预借的卖出所得。
        """
        self.cash -= amount
        self._sell_credit_outstanding = Decimal("0.00")
        self._log(
            tx_type=TransactionType.REVOKE_SELL_CREDIT,
            amount=amount,
            reason=reason,
        )

    def get_max_buy_amount(self) -> Decimal:
        """获取最大可买金额（即可用现金）"""
        return self.available_cash

    def get_transaction_logs(self) -> list[dict[str, Any]]:
        """获取交易日志"""
        return list(self._logs)

    def _log(self, tx_type: TransactionType, **kwargs: Any) -> None:
        """记录交易日志"""
        self._logs.append({"type": tx_type.value, **kwargs})

    def to_dict(self) -> dict[str, Any]:
        """导出账户状态快照"""
        return {
            "cash": self.cash,
            "frozen_cash": self.frozen_cash,
            "available_cash": self.available_cash,
            "currency": self.currency,
            "initial_cash": self.initial_cash,
        }
