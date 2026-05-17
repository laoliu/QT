"""可转债领域模型

定义可转债相关数据结构，包括：
- ConvertibleBond: 可转债基础信息
- ConvertibleBondBar: 可转债行情数据（扩展 Bar）
- BondCorporateAction: 条款变更事件（强赎/下修/回售）
- ConversionMetrics: 转股相关计算指标
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Optional


class BondStatus(str, Enum):
    """可转债状态"""

    LISTED = "listed"  # 正常上市
    DELISTED = "delisted"  # 已退市
    SUSPENDED = "suspended"  # 停牌
    REDEEMED = "redeemed"  # 已强赎
    MATURED = "matured"  # 已到期


class CorporateActionType(str, Enum):
    """公司行动类型"""

    CONVERSION_PRICE_DOWN = "conversion_price_down"  # 转股价下修
    REDEMPTION_CALL = "redemption_call"  # 强赎公告
    REDEMPTION_TRIGGER = "redemption_trigger"  # 触发强赎条件
    PUT_BACK = "put_back"  # 回售公告
    COUPON_PAYMENT = "coupon_payment"  # 付息
    LISTING = "listing"  # 上市
    DELISTING = "delisting"  # 退市


@dataclass(frozen=True, slots=True)
class ConvertibleBond:
    """可转债基础信息

    Attributes:
        ts_code: 可转债代码 (如 127075.SZ)
        bond_short_name: 可转债简称
        stk_code: 正股代码 (如 002937.SZ)
        stk_short_name: 正股简称
        conv_price: 当前转股价
        conv_start_date: 转股起始日
        conv_end_date: 转股截止日
        maturity_date: 到期日
        issue_size: 发行规模（亿元）
        par_value: 面值（通常100）
        put_price: 回售价格
        call_price: 赎回价格（通常100+利息补偿）
        status: 当前状态
        list_date: 上市日期
        delist_date: 退市日期（如有）
    """

    ts_code: str
    bond_short_name: str
    stk_code: str
    stk_short_name: str
    conv_price: Decimal
    conv_start_date: date
    conv_end_date: date
    maturity_date: date
    issue_size: Decimal
    par_value: Decimal = Decimal("100")
    put_price: Optional[Decimal] = None
    call_price: Optional[Decimal] = None
    status: BondStatus = BondStatus.LISTED
    list_date: Optional[date] = None
    delist_date: Optional[date] = None

    @property
    def is_in_conversion_period(self) -> bool:
        """是否处于转股期"""
        today = date.today()
        return self.conv_start_date <= today <= self.conv_end_date

    @property
    def days_to_maturity(self) -> int:
        """距到期天数"""
        return (self.maturity_date - date.today()).days

    def conversion_value(self, stock_price: Decimal) -> Decimal:
        """计算转股价值

        转股价值 = 正股价 × (面值 / 转股价)
        """
        if self.conv_price <= 0:
            return Decimal("0")
        return stock_price * (self.par_value / self.conv_price)

    def conversion_premium(
        self, bond_price: Decimal, stock_price: Decimal
    ) -> Decimal:
        """计算转股溢价率

        溢价率 = (转债价格 - 转股价值) / 转股价值 × 100%
        """
        conv_value = self.conversion_value(stock_price)
        if conv_value <= 0:
            return Decimal("0")
        return (bond_price - conv_value) / conv_value * 100


@dataclass(frozen=True, slots=True)
class ConvertibleBondBar:
    """可转债行情数据

    扩展标准 Bar，增加可转债特有字段。
    """

    symbol: str # 可转债代码
    start_ts: datetime
    end_ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float  # 成交量（张）
    amount: float  # 成交额（元）
    vwap: float | None = None
    is_suspended: bool = False
    # 可转债首日有涨跌停限制，其他时间无
    is_first_day: bool = False

    @property
    def is_limit_up(self) -> bool | None:
        """可转债无涨跌停限制（首日除外）"""
        return None if not self.is_first_day else None

    @property
    def is_limit_down(self) -> bool | None:
        """可转债无涨跌停限制（首日除外）"""
        return None if not self.is_first_day else None


@dataclass(frozen=True, slots=True)
class BondCorporateAction:
    """可转债公司行动/条款变更事件"""

    ts_code: str
    action_type: CorporateActionType
    announce_date: date  # 公告日期
    effective_date: date  # 生效日期
    old_value: Optional[Decimal] = None  # 旧值（如旧转股价）
    new_value: Optional[Decimal] = None  # 新值（如新转股价）
    memo: str = ""


@dataclass(frozen=True, slots=True)
class ConversionMetrics:
    """转股指标计算结果

    用于策略计算和展示。
    """

    ts_code: str
    trade_date: date
    bond_price: Decimal  # 转债价格
    stock_price: Decimal  # 正股价格
    conv_price: Decimal  # 转股价
    conv_value: Decimal  # 转股价值
    premium_rate: Decimal  # 转股溢价率（%）
    double_low: Decimal  # 双低指标 = 转债价格 + 溢价率

    @classmethod
    def calculate(
        cls,
        ts_code: str,
        trade_date: date,
        bond_price: Decimal,
        stock_price: Decimal,
        conv_price: Decimal,
        par_value: Decimal = Decimal("100"),
    ) -> ConversionMetrics:
        """计算转股指标"""
        if conv_price <= 0 or stock_price <= 0:
            conv_value = Decimal("0")
            premium_rate = Decimal("0")
        else:
            conv_value = stock_price * (par_value / conv_price)
            premium_rate = (bond_price - conv_value) / conv_value * 100

        double_low = bond_price + premium_rate

        return cls(
            ts_code=ts_code,
            trade_date=trade_date,
            bond_price=bond_price,
            stock_price=stock_price,
            conv_price=conv_price,
            conv_value=conv_value,
            premium_rate=premium_rate,
            double_low=double_low,
        )


@dataclass(frozen=True, slots=True)
class RedemptionCheck:
    """强赎条件检查结果"""

    ts_code: str
    check_date: date
    conv_price: Decimal
    trigger_price: Decimal  # 触发价格 = 转股价 × 130%
    consecutive_days: int  # 正股价格连续高于触发价的天数
    trigger_threshold: int = 15  # 触发阈值（通常15-20个交易日）
    is_triggered: bool = False  # 是否触发强赎条件

    @classmethod
    def check(
        cls,
        ts_code: str,
        check_date: date,
        conv_price: Decimal,
        stock_prices: list[Decimal],  # 近期正股价格序列
        trigger_ratio: Decimal = Decimal("1.3"),
        trigger_threshold: int = 15,
    ) -> RedemptionCheck:
        """检查强赎条件

        强赎条件：正股价格连续N个交易日不低于转股价的130%
        """
        trigger_price = conv_price * trigger_ratio

        # 计算连续高于触发价的天数（从最新往前数）
        consecutive_days = 0
        for price in reversed(stock_prices):
            if price >= trigger_price:
                consecutive_days += 1
            else:
                break

        is_triggered = consecutive_days >= trigger_threshold

        return cls(
            ts_code=ts_code,
            check_date=check_date,
            conv_price=conv_price,
            trigger_price=trigger_price,
            consecutive_days=consecutive_days,
            trigger_threshold=trigger_threshold,
            is_triggered=is_triggered,
        )
