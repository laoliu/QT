"""交易账户 — 资金管理 / 冻结 / 结算"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any


class TransactionType(str, Enum):
    FREEZE = "freeze"
    UNFREEZE = "unfreeze"
    SETTLE_BUY = "settle_buy"
    SETTLE_SELL = "settle_sell"
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"
    SELL_CREDIT = "sell_credit"
    REVOKE_SELL_CREDIT = "revoke_sell_credit"


@dataclass
class Account:
    initial_cash: Decimal
    currency: str = "CNY"
    cash: Decimal = field(init=False)
    frozen_cash: Decimal = field(init=False)
    _frozen_by_order: dict[str, Decimal] = field(init=False, default_factory=dict)
    _sell_credit_outstanding: Decimal = field(init=False, default=Decimal(0))
    _logs: list[dict[str, Any]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        self.cash = self.initial_cash
        self.frozen_cash = Decimal(0)

    @property
    def available_cash(self) -> Decimal:
        return self.cash - self.frozen_cash

    def get_frozen_for_order(self, order_id: str) -> Decimal:
        return self._frozen_by_order.get(order_id, Decimal(0))

    def _log(self, tx_type: TransactionType, amount: Decimal, **meta: Any) -> None:
        self._logs.append({
            "type": tx_type.value, "amount": str(amount), **meta,
        })

    def freeze_cash(self, amount: Decimal, order_id: str, reason: str = "") -> bool:
        if amount <= 0 or amount > self.available_cash:
            return False
        self.frozen_cash += amount
        self._frozen_by_order[order_id] = self._frozen_by_order.get(order_id, Decimal(0)) + amount
        self._log(TransactionType.FREEZE, amount, order_id=order_id, reason=reason)
        return True

    def unfreeze_cash(self, amount: Decimal, order_id: str, reason: str = "") -> bool:
        frozen = self._frozen_by_order.get(order_id, Decimal(0))
        actual = min(amount, frozen)
        if actual <= 0:
            return False
        self.frozen_cash -= actual
        self._frozen_by_order[order_id] -= actual
        if self._frozen_by_order[order_id] <= 0:
            self._frozen_by_order.pop(order_id, None)
        self._log(TransactionType.UNFREEZE, actual, order_id=order_id, reason=reason)
        return True

    def settle_buy(self, order_id: str, cost: Decimal, fee: Decimal,
                   trade_date: date | None = None) -> None:
        total = cost + fee
        frozen = self._frozen_by_order.pop(order_id, Decimal(0))
        self.frozen_cash -= frozen
        self.cash -= total
        if frozen > total:
            pass  # excess unfrozen back
        self._log(TransactionType.SETTLE_BUY, total, order_id=order_id, fee=str(fee))

    def settle_sell(self, order_id: str, proceeds: Decimal, fee: Decimal,
                    trade_date: date | None = None, t_plus_n: int = 0) -> None:
        net = proceeds - fee
        if t_plus_n > 0:
            self._sell_credit_outstanding += net
            self._log(TransactionType.SELL_CREDIT, net, order_id=order_id)
        else:
            self.cash += net
        self._log(TransactionType.SETTLE_SELL, net, order_id=order_id, fee=str(fee))

    def deposit(self, amount: Decimal) -> None:
        self.cash += amount
        self._log(TransactionType.DEPOSIT, amount)

    def withdraw(self, amount: Decimal) -> bool:
        if amount > self.available_cash:
            return False
        self.cash -= amount
        self._log(TransactionType.WITHDRAW, amount)
        return True

    def settle_outstanding_credits(self) -> Decimal:
        """结算 T+N 卖出到期款"""
        credit = self._sell_credit_outstanding
        if credit > 0:
            self.cash += credit
            self._sell_credit_outstanding = Decimal(0)
            self._log(TransactionType.REVOKE_SELL_CREDIT, credit)
        return credit

    @property
    def total_value(self) -> Decimal:
        return self.cash

    @property
    def transaction_log(self) -> list[dict[str, Any]]:
        return list(self._logs)
