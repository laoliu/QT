"""可转债市场规则

实现可转债特有的交易规则：
- T+0 交易：当日买入可当日卖出
- 无涨跌幅限制（上市首日除外）
- 最小交易单位：10张（1000元面值）
- 强赎/回售条件监控
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Any, ClassVar, Optional

from aipha.domain.models import Bar, Order, Side
from aipha.market.base import PrecheckDecision
from aipha.market.price_limits import is_at_limit_down, is_at_limit_up

logger = logging.getLogger(__name__)


class CBRejectReason(str, Enum):
    """可转债订单拒绝原因"""

    OUT_OF_SESSION = "OUT_OF_SESSION"  # 非交易时段
    SUSPENDED = "SUSPENDED"  # 停牌
    DELISTED = "DELISTED"  # 已退市
    NOT_IN_CONVERSION = "NOT_IN_CONVERSION"  # 不在转股期
    LOT_SIZE_ROUNDING = "LOT_SIZE_ROUNDING"  # 手数不正确
    REDEMPTION_ANNOUNCED = "REDEMPTION_ANNOUNCED"  # 已公告强赎
    FIRST_DAY_LIMIT = "FIRST_DAY_LIMIT"  # 首日涨跌停
    PRICE_LIMIT = "PRICE_LIMIT"  # 涨跌停限制（2022新规）
    TEMPORARY_HALT = "TEMPORARY_HALT"  # 临时停牌（2022新规）
    DATA_MISSING = "DATA_MISSING"  # 关键数据缺失（§13.3）


# 向后兼容别名 —— 已有测试导入 CBPrecheckDecision
CBPrecheckDecision = PrecheckDecision


@dataclass(frozen=True, slots=True)
class RedemptionStatus:
    """强赎状态"""

    ts_code: str
    is_announced: bool = False  # 是否已公告强赎
    is_triggered: bool = False  # 是否已触发条件
    announce_date: Optional[date] = None  # 公告日期
    redemption_date: Optional[date] = None  # 赎回登记日
    last_trade_date: Optional[date] = None  # 最后交易日


@dataclass(frozen=True, slots=True)
class MarketRulesConvertibleBond:
    """可转债市场规则

    与 A 股的关键差异：
    - T+0：当日买入可当日卖出
    - 2022-08-01 前：首日外无涨跌幅限制
    - 2022-08-01 起：非首日 ±20% 涨跌停、±30% 临时停牌
    - lot_size = 10（张）
    - 无过户费，佣金更低

    Attributes:
        lot_size: 最小交易单位（10张）
        t_plus: T+N 规则（0=当日可卖）
        commission_rate: 佣金费率（通常万1-万2）
        commission_min: 最低佣金（5元）
        stamp_tax_rate: 印花税（可转债免征）
        conversion_fee_rate: 转股费率（万5，仅转股时收取）
        new_rule_date: 2022 新规生效日期
        price_limit_pct: 非首日涨跌停幅度（2022新规后 ±20%）
        temp_halt_pct: 非首日临时停牌幅度（2022新规后 ±30%）
    """

    lot_size: int = 10
    t_plus: int = 0
    commission_rate: Decimal = Decimal("0.0000854")  # 万0.854
    commission_min: Decimal = Decimal("5")  # 最低佣金5元
    stamp_tax_rate: Decimal = Decimal("0")  # 免印花税
    conversion_fee_rate: Decimal = Decimal("0.0005")  # 转股费万5（仅转股时收取，不影响普通交易）

    # 滑点默认值（§13.1 权威源）
    slippage_bps_default: float = 5.0  # CB 默认滑点 5bps

    # 成交量约束（§P0 容量修正）
    max_participation_rate: float = 0.05  # CB 最大参与率 5%
    opening_volume_fraction: float = 0.15  # 开盘集合竞价占全天量约 15%

    # 首日涨跌停/临停参数
    first_day_up_halt_pct: Decimal = Decimal("0.20")   # 首日涨20%临停
    first_day_down_halt_pct: Decimal = Decimal("0.30")  # 首日跌30%临停

    # 2022 新规参数（2022-08-01 起生效）
    new_rule_date: date = date(2022, 8, 1)
    price_limit_pct: Decimal = Decimal("0.20")  # ±20% 涨跌停
    temp_halt_pct: Decimal = Decimal("0.30")  # ±30% 临时停牌

    # -- Layer 2: .env → 环境变量覆盖映射（§13.1 三层配置机制） --
    _ENV_MAP: ClassVar[dict[str, tuple[str, type]]] = {
        "commission_rate": ("CB_COMMISSION_RATE", Decimal),
        "commission_min": ("CB_COMMISSION_MIN", Decimal),
        "stamp_tax_rate": ("CB_STAMP_TAX_SELL", Decimal),
        "slippage_bps_default": ("CB_SLIPPAGE_BPS", float),
        "lot_size": ("CB_LOT_SIZE", int),
    }

    def __post_init__(self) -> None:
        """Layer 2 覆盖：若 .env 设置了对应环境变量且字段值 == Layer 1 默认值，则用 .env 值替换。"""
        import dataclasses as _dc
        _defaults = {
            f.name: f.default for f in _dc.fields(self)
            if f.default is not _dc.MISSING
        }
        for attr, (env_key, conv) in self._ENV_MAP.items():
            env_val = os.environ.get(env_key)
            if env_val is None:
                continue
            # 仅当字段值 == Layer 1 代码默认值时才覆盖（显式传入的 Layer 3 优先）
            if attr in _defaults and getattr(self, attr) != _defaults[attr]:
                continue
            object.__setattr__(self, attr, conv(env_val))

    def precheck_order(
        self,
        *,
        order: Order,
        bar: Bar,
        is_first_day: bool = False,
        redemption_status: RedemptionStatus | None = None,
        trade_date: date | None = None,
        prev_close: float | None = None,
        **kwargs: Any,
    ) -> PrecheckDecision:
        """预检可转债订单

        Args:
            order: 订单
            bar: 当前行情
            is_first_day: 是否上市首日
            redemption_status: 强赎状态
            trade_date: 交易日期（用于判断是否适用 2022 新规）
            prev_close: 前一日收盘价（2022 新规涨跌停基准）

        Returns:
            预检结果
        """
        # 1. 手数检查
        if order.qty % self.lot_size != 0:
            return PrecheckDecision(
                allowed=False, reason=CBRejectReason.LOT_SIZE_ROUNDING
            )

        # 2. 停牌检查
        if bar.is_suspended:
            return PrecheckDecision(allowed=False, reason=CBRejectReason.SUSPENDED)

        # 3. 首日涨跌停（上市首日有临时停牌机制）
        if is_first_day:
            # 交易所规则：首日盘中涨跌幅达到 ±20% / ±30% 时临时停牌
            # 如果当前 bar 的 high 已触及涨停线或 low 已触及跌停线，
            # 认为当日已无法以合理价格成交
            if bar.open > 0:
                intraday_up = (bar.high - bar.open) / bar.open
                intraday_down = (bar.open - bar.low) / bar.open

                if intraday_up >= float(self.first_day_up_halt_pct):
                    return PrecheckDecision(
                        allowed=False,
                        reason=CBRejectReason.FIRST_DAY_LIMIT,
                        warning=f"首日涨幅 {intraday_up:.1%} 已触发临停线 {self.first_day_up_halt_pct}",
                    )
                if intraday_down >= float(self.first_day_down_halt_pct):
                    return PrecheckDecision(
                        allowed=False,
                        reason=CBRejectReason.FIRST_DAY_LIMIT,
                        warning=f"首日跌幅 {intraday_down:.1%} 已触发临停线 {self.first_day_down_halt_pct}",
                    )

        # 3.5 2022 新规非首日涨跌停检查
        # §13.3: prev_close=None 时不得静默跳过，必须记录 warning
        if (
            not is_first_day
            and trade_date is not None
            and trade_date >= self.new_rule_date
        ):
            if prev_close is None or prev_close <= 0:
                logger.warning(
                    "CB prev_close 缺失或无效 (symbol=%s, date=%s), "
                    "涨跌停检查无法执行 — 订单被拒绝 §13.3",
                    bar.symbol, trade_date,
                )
                # §13.3: prev_close 缺失禁止静默放行
                return PrecheckDecision(
                    allowed=False,
                    reason=CBRejectReason.DATA_MISSING,
                    warning=(
                        f"prev_close 缺失 (symbol={bar.symbol}, "
                        f"date={trade_date}), 涨跌停检查无法执行 — "
                        "订单被拒绝（§13.3 安全关键参数）"
                    ),
                )
            else:
                limit_up = prev_close * (1 + float(self.price_limit_pct))
                limit_down = prev_close * (1 - float(self.price_limit_pct))
                halt_up = prev_close * (1 + float(self.temp_halt_pct))
                halt_down = prev_close * (1 - float(self.temp_halt_pct))

                # 涨停价检查（close 已达涨停，买单被拒）
                if order.side == Side.BUY and is_at_limit_up(bar.close, limit_up):
                    return PrecheckDecision(
                        allowed=False,
                        reason=CBRejectReason.PRICE_LIMIT,
                        warning=f"涨停 {bar.close:.2f} ≥ {limit_up:.2f} (前收{prev_close:.2f}×{1+float(self.price_limit_pct):.0%})",
                    )
                # 跌停价检查（close 已达跌停，卖单被拒）
                if order.side == Side.SELL and is_at_limit_down(bar.close, limit_down):
                    return PrecheckDecision(
                        allowed=False,
                        reason=CBRejectReason.PRICE_LIMIT,
                        warning=f"跌停 {bar.close:.2f} ≤ {limit_down:.2f} (前收{prev_close:.2f}×{1-float(self.price_limit_pct):.0%})",
                    )

        # 4. 强赎检查
        warning = None
        if redemption_status:
            if redemption_status.is_announced:
                # 已公告强赎，警告但允许交易（投资者可能需要卖出）
                warning = (
                    f"强赎已公告，赎回日: {redemption_status.redemption_date}"
                )
            elif redemption_status.is_triggered:
                # 已触发条件但未公告
                warning = "强赎条件已触发，请关注公告"

        return PrecheckDecision(allowed=True, warning=warning)

    def get_price_limits(
        self,
        prev_close: float,
        trade_date: date | None = None,
        is_first_day: bool = False,
    ) -> tuple[float | None, float | None]:
        """计算涨跌停价格

        Args:
            prev_close: 前一日收盘价
            trade_date: 交易日期
            is_first_day: 是否上市首日

        Returns:
            (涨停价, 跌停价)，如无限制则返回 (None, None)
        """
        if is_first_day:
            # 首日无涨跌停价，只有临时停牌
            return None, None

        if trade_date is not None and trade_date >= self.new_rule_date:
            limit_up = prev_close * (1 + float(self.price_limit_pct))
            limit_down = prev_close * (1 - float(self.price_limit_pct))
            return limit_up, limit_down

        # 2022 新规前，非首日无涨跌停
        return None, None

    def calculate_fee(
        self,
        *,
        price: Decimal,
        qty: int,
        side: Side,
    ) -> Decimal:
        """计算交易费用

        可转债交易费用：
        - 佣金：按成交额计算，有最低限制
        - 印花税：免征
        - 过户费：免征

        Args:
            price: 成交价格
            qty: 成交数量（张）
            side: 买卖方向

        Returns:
            总费用
        """
        trade_value = price * qty

        # qty=0 无实际成交，不收费
        if qty == 0:
            return Decimal("0")

        # 佣金
        commission = max(trade_value * self.commission_rate, self.commission_min)

        # 可转债免印花税和过户费

        return commission


@dataclass
class RedemptionChecker:
    """强赎条件监控器

    检查可转债是否触发强赎条件：
    - 正股价格 >= 转股价 × 130%
    - 连续 N 个交易日（通常15-20日）
    """

    trigger_ratio: Decimal = Decimal("1.3")
    trigger_days: int = 15

    def check(
        self,
        *,
        conv_price: Decimal,
        stock_prices: list[Decimal],
    ) -> tuple[bool, int]:
        """检查强赎条件

        Args:
            conv_price: 转股价
            stock_prices: 近期正股价格序列（按时间正序）

        Returns:
            (是否触发, 连续天数)
        """
        if conv_price <= 0:
            return False, 0

        trigger_price = conv_price * self.trigger_ratio

        # 从最新往前数连续天数
        consecutive = 0
        for price in reversed(stock_prices):
            if price >= trigger_price:
                consecutive += 1
            else:
                break

        return consecutive >= self.trigger_days, consecutive


@dataclass
class PutBackChecker:
    """回售条件监控器

    检查可转债是否触发回售条件：
    - 正股价格 <= 转股价 × 70%（或其他约定比例）
    - 连续 N 个交易日
    """

    trigger_ratio: Decimal = Decimal("0.7")
    trigger_days: int = 30

    def check(
        self,
        *,
        conv_price: Decimal,
        stock_prices: list[Decimal],
    ) -> tuple[bool, int]:
        """检查回售条件

        Args:
            conv_price: 转股价
            stock_prices: 近期正股价格序列（按时间正序）

        Returns:
            (是否触发, 连续天数)
        """
        if conv_price <= 0:
            return False, 0

        trigger_price = conv_price * self.trigger_ratio

        consecutive = 0
        for price in reversed(stock_prices):
            if price <= trigger_price:
                consecutive += 1
            else:
                break

        return consecutive >= self.trigger_days, consecutive
