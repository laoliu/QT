"""可转债领域模型 — 基于 AlphQ 的完整可转债数据结构"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


class BondStatus(str, Enum):
    LISTED = "listed"
    DELISTED = "delisted"
    SUSPENDED = "suspended"
    REDEEMED = "redeemed"
    MATURED = "matured"


class CorporateActionType(str, Enum):
    CONVERSION_PRICE_DOWN = "conversion_price_down"
    REDEMPTION_CALL = "redemption_call"
    REDEMPTION_TRIGGER = "redemption_trigger"
    PUT_BACK = "put_back"
    COUPON_PAYMENT = "coupon_payment"
    LISTING = "listing"
    DELISTING = "delisting"


@dataclass(frozen=True, slots=True)
class ConvertibleBond:
    """可转债基础信息"""
    ts_code: str                   # 转债代码 (如 127075.SZ)
    bond_short_name: str           # 转债简称
    stk_code: str                  # 正股代码
    stk_short_name: str            # 正股简称
    conv_price: Decimal            # 当前转股价
    conv_start_date: date          # 转股起始日
    conv_end_date: date            # 转股截止日
    maturity_date: date            # 到期日
    issue_size: Decimal            # 发行规模（亿元）
    par_value: Decimal = Decimal("100")
    put_price: Optional[Decimal] = None
    call_price: Optional[Decimal] = None
    status: BondStatus = BondStatus.LISTED
    list_date: Optional[date] = None
    delist_date: Optional[date] = None
    rating: str = ""               # 信用评级

    @property
    def is_in_conversion_period(self) -> bool:
        today = date.today()
        return self.conv_start_date <= today <= self.conv_end_date

    @property
    def days_to_maturity(self) -> int:
        return (self.maturity_date - date.today()).days

    def conversion_value(self, stock_price: Decimal) -> Decimal:
        """转股价值 = 正股价 × (面值 / 转股价)"""
        if self.conv_price <= 0:
            return Decimal("0")
        return stock_price * (self.par_value / self.conv_price)

    def conversion_premium(self, bond_price: Decimal, stock_price: Decimal) -> Decimal:
        """转股溢价率 = (转债价 - 转股价值) / 转股价值 × 100%"""
        cv = self.conversion_value(stock_price)
        if cv <= 0:
            return Decimal("0")
        return (bond_price - cv) / cv * 100


@dataclass(frozen=True, slots=True)
class ConvertibleBondBar:
    """可转债行情数据"""
    symbol: str
    start_ts: datetime
    end_ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float     # 成交量（张）
    amount: float     # 成交额（元）
    vwap: float | None = None
    is_suspended: bool = False
    is_first_day: bool = False


@dataclass(frozen=True, slots=True)
class BondCorporateAction:
    """条款变更事件（强赎/下修/回售等）"""
    ts_code: str
    action_type: CorporateActionType
    announce_date: date
    effective_date: date
    old_value: Optional[Decimal] = None
    new_value: Optional[Decimal] = None
    memo: str = ""


@dataclass(frozen=True, slots=True)
class ConversionMetrics:
    """转股指标"""
    ts_code: str
    trade_date: date
    conv_value: float        # 转股价值
    conv_premium: float      # 转股溢价率 (%)
    delta_proxy: float       # Delta 近似
    double_low: float        # 双低值 = 价格 + 溢价率
    pure_bond_value: float   # 纯债价值
    ytm_estimate: float      # 近似到期收益率

    @property
    def is_balanced(self) -> bool:
        """是否在平衡区 (delta 0.3~0.7)"""
        return 0.3 <= self.delta_proxy <= 0.7
