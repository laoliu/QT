"""A 股/可转债费用模型

提供精细化费用拆分：佣金（最低收费）+ 印花税（仅卖出）+ 过户费。
所有单体模型和组合模型均通过 ``calc(fill)`` 接口统一调用。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

from aipha.domain.models import Fill, Side


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    """费用明细：拆分佣金、印花税、过户费。"""

    commission: float
    stamp_tax: float
    transfer_fee: float

    @property
    def total(self) -> float:
        """费用合计（佣金 + 印花税 + 过户费）。"""
        return self.commission + self.stamp_tax + self.transfer_fee


@runtime_checkable
class FeeModel(Protocol):
    """费用模型 Protocol。

    任何费用模型都必须实现 ``calc(fill) -> FeeBreakdown`` 方法。
    当前实现: ``FeeModelCNEquity``。
    未来扩展: 期货、期权等资产可提供不同实现。
    """

    def calc(self, fill: Fill) -> FeeBreakdown:
        """根据成交记录计算费用明细。"""
        ...


@dataclass(frozen=True, slots=True)
class CommissionModel:
    """佣金模型：按成交额比例计算，不低于最低收费。"""

    rate: float
    min_fee: float

    def calc(self, fill: Fill) -> float:
        """计算佣金，不低于最低收费 ``min_fee``。"""
        fee = fill.notional * self.rate
        return self.min_fee if fee < self.min_fee else fee


@dataclass(frozen=True, slots=True)
class StampTaxModel:
    """印花税模型：仅卖出时按成交额比例征收（固定税率）。"""

    sell_rate: float

    def calc(self, fill: Fill) -> float:
        """计算印花税（仅卖出时按 sell_rate 征收）。"""
        return fill.notional * self.sell_rate if fill.side == Side.SELL else 0.0


# --- A 股印花税历史费率表 ---
# 格式: ((生效日期, 卖方税率), ...) 按日期升序排列
# 2008-09-19: 由双边改为卖方单边 0.1%
# 2023-08-28: 减半至 0.05%
CN_EQUITY_STAMP_TAX_SCHEDULE: tuple[tuple[date, float], ...] = (
    (date(2008, 9, 19), 0.001),
    (date(2023, 8, 28), 0.0005),
)


@dataclass(frozen=True, slots=True)
class TimeVaryingStampTaxModel:
    """时间变动印花税模型：根据成交日期自动切换税率。

    当 ``schedule`` 为空时，行为等同于 ``StampTaxModel(sell_rate=default_rate)``。

    Attributes:
        default_rate: schedule 中第一条之前使用的默认税率。
        schedule: ((生效日期, 税率), ...) 按日期升序排列。
    """

    default_rate: float = 0.001
    schedule: tuple[tuple[date, float], ...] = field(
        default_factory=lambda: CN_EQUITY_STAMP_TAX_SCHEDULE
    )

    @property
    def sell_rate(self) -> float:
        """返回 schedule 中最新生效的税率（兼容 StampTaxModel 接口）。"""
        if not self.schedule:
            return self.default_rate
        return self.schedule[-1][1]

    def rate_at(self, d: date) -> float:
        """返回指定日期适用的印花税率。"""
        rate = self.default_rate
        for eff_date, eff_rate in self.schedule:
            if d >= eff_date:
                rate = eff_rate
            else:
                break
        return rate

    def calc(self, fill: Fill) -> float:
        """计算印花税（仅卖出，根据成交日期自动选择税率）。"""
        if fill.side != Side.SELL:
            return 0.0
        fill_date = fill.t_fill.date() if hasattr(fill.t_fill, 'date') else fill.t_fill
        return fill.notional * self.rate_at(fill_date)


@dataclass(frozen=True, slots=True)
class TransferFeeModel:
    """过户费模型：按成交额比例收取。"""

    rate: float

    def calc(self, fill: Fill) -> float:
        """计算过户费（按成交额 × rate）。"""
        return fill.notional * self.rate


@dataclass(frozen=True, slots=True)
class FeeModelCNEquity:
    """中国 A 股/可转债综合费用模型：佣金 + 印花税 + 过户费。"""

    commission: CommissionModel
    stamp_tax: StampTaxModel | TimeVaryingStampTaxModel
    transfer_fee: TransferFeeModel

    def calc(self, fill: Fill) -> FeeBreakdown:
        """计算全部费用明细并返回 FeeBreakdown。"""
        return FeeBreakdown(
            commission=self.commission.calc(fill),
            stamp_tax=self.stamp_tax.calc(fill),
            transfer_fee=self.transfer_fee.calc(fill),
        )


def calc_fee_from_amount(
    amount: float,
    side: str,
    *,
    commission_rate: float,
    commission_min: float,
    stamp_tax_rate: float,
    transfer_fee_rate: float,
    trade_date: date | None = None,
    use_time_varying_stamp_tax: bool = False,
) -> FeeBreakdown:
    """标量接口费用计算 — 与 FeeModelCNEquity.calc() 逻辑完全一致.

    供 Paper/Live 脚本直接调用（无需构造 Fill 对象）。
    回测引擎仍通过 FeeModelCNEquity.calc(fill) 调用。

    §13.2: 单一实现原则 — 所有费用计算最终归结到此处。
    """
    comm = max(amount * commission_rate, commission_min)

    stamp = 0.0
    if side.upper() == "SELL":
        if use_time_varying_stamp_tax and trade_date is not None:
            stamp_rate = TimeVaryingStampTaxModel().rate_at(trade_date)
        else:
            stamp_rate = stamp_tax_rate
        stamp = amount * stamp_rate

    transfer = amount * transfer_fee_rate

    return FeeBreakdown(
        commission=comm,
        stamp_tax=stamp,
        transfer_fee=transfer,
    )
