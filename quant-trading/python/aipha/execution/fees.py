"""费用模型 — 佣金 + 印花税 + 过户费。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from aipha.domain.models import Side

CN_EQUITY_STAMP_TAX_SCHEDULE: tuple[tuple[date, float], ...] = (
    (date(2008, 9, 19), 0.001),
    (date(2023, 8, 28), 0.0005),
)


@dataclass
class TradeFees:
    """单笔交易费用明细"""
    commission: float = 0.0
    stamp_tax: float = 0.0
    transfer_fee: float = 0.0

    @property
    def total(self) -> float:
        return self.commission + self.stamp_tax + self.transfer_fee


# Backward-compatible aliases used by some migrated modules
FeeBreakdown = TradeFees


@dataclass(frozen=True)
class CommissionModel:
    """佣金模型：按成交额比例计算，不低于最低收费。"""
    rate: float = 8.54e-05
    min_fee: float = 5.0

    def calc(self, notional: float) -> float:
        fee = notional * self.rate
        return self.min_fee if fee < self.min_fee else fee


@dataclass(frozen=True)
class StampTaxModel:
    """印花税模型（卖出时收取）。"""
    rate: float = 0.0005
    sell_only: bool = True
    default_rate: float = 0.0005
    schedule: tuple = ()

    def calc_amount(self, notional: float, side: str) -> float:
        if self.sell_only and side != "sell":
            return 0.0
        return notional * (self.default_rate if self.default_rate != 0.0005 else self.rate)


@dataclass(frozen=True)
class TransferFeeModel:
    """过户费模型。"""
    rate: float = 0.00001

    def calc_amount(self, notional: float) -> float:
        return notional * self.rate


TimeVaryingStampTaxModel = StampTaxModel  # alias


@dataclass(frozen=True)
class FeeModelCNEquity:
    """中国 A 股/可转债综合费用模型。"""
    commission: CommissionModel = None  # type: ignore[assignment]
    stamp_tax: StampTaxModel = None  # type: ignore[assignment]
    transfer_fee: TransferFeeModel = None  # type: ignore[assignment]

    def calc(self, notional: float, side: str = "buy") -> TradeFees:
        comm = self.commission.calc(notional) if self.commission else 0.0
        st = self.stamp_tax.calc_amount(notional, side) if self.stamp_tax else 0.0
        tf = self.transfer_fee.calc_amount(notional) if self.transfer_fee else 0.0
        return TradeFees(commission=comm, stamp_tax=st, transfer_fee=tf)


class FeeModel:
    """A 股费用模型

    - 佣金: 双向收取，万 0.854，最低 5 元
    - 印花税: 仅卖出，千分之 0.5（2023-08 起）
    - 过户费: 双向，十万分之一
    """

    def __init__(
        self,
        commission_rate: float = 8.54e-05,
        commission_min: float = 5.0,
        stamp_tax_rate: float = 0.0005,
        transfer_fee_rate: float = 0.00001,
        time_varying_tax: bool = True,
    ):
        self._comm_rate = commission_rate
        self._comm_min = commission_min
        self._stamp_tax_rate = stamp_tax_rate
        self._transfer_fee_rate = transfer_fee_rate
        self._time_varying = time_varying_tax

    def compute(self, notional: float, side: Side, trade_date: int = 0) -> TradeFees:
        """计算交易费用。

        Args:
            notional: 成交金额
            side: 买/卖方向
            trade_date: 交易日（YYYYMMDD, 用于历史费率）
        """
        commission = max(self._comm_min, notional * self._comm_rate)

        stamp_tax = 0.0
        if side == Side.SELL:
            rate = self._get_stamp_tax_rate(trade_date) if self._time_varying else self._stamp_tax_rate
            stamp_tax = notional * rate

        transfer_fee = notional * self._transfer_fee_rate

        return TradeFees(
            commission=round(commission, 4),
            stamp_tax=round(stamp_tax, 4),
            transfer_fee=round(transfer_fee, 4),
        )

    def _get_stamp_tax_rate(self, trade_date: int) -> float:
        """历史印花税率切换表"""
        if trade_date == 0:
            return self._stamp_tax_rate
        if trade_date >= 20230828:
            return 0.0005   # 2023-08-28 起降至千0.5
        if trade_date >= 20080919:
            return 0.001    # 2008-09-19 起千分之一（单边）
        return 0.001        # 默认使用降低后费率
