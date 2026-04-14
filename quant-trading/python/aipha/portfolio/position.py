"""
持仓模块 - T+1可卖数量状态机

实现A股T+1交易制度下的持仓管理：
- 当日买入股票不可卖出（T日买入，T+1日起可卖）
- 可卖数量 = 昨日持仓 + 今日已解锁部分
- 冻结/解冻机制支持订单生命周期管理

核心概念：
- total_qty: 总持仓数量
- frozen_qty: 冻结数量（已下卖单但未成交）
- today_bought_qty: 今日买入数量（T日不可卖）
- available_to_sell: 可卖数量 = total_qty - frozen_qty - today_bought_qty
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum
from typing import Optional


class PositionSide(Enum):
    """持仓方向"""

    LONG = "long"
    SHORT = "short"  # A股暂不支持，预留接口


class PositionUpdateReason(Enum):
    """持仓变动原因"""

    BUY_FILLED = "buy_filled"  # 买入成交
    SELL_FILLED = "sell_filled"  # 卖出成交
    SELL_FROZEN = "sell_frozen"  # 卖出冻结
    SELL_UNFROZEN = "sell_unfrozen"  # 卖出解冻（撤单）
    DAY_END_UNLOCK = "day_end_unlock"  # 日终解锁（T+1）
    MANUAL_ADJUST = "manual_adjust"  # 手动调整


@dataclass
class PositionLog:
    """持仓变动日志"""

    timestamp: str
    reason: PositionUpdateReason
    qty_change: int
    before_total: int
    after_total: int
    before_frozen: int
    after_frozen: int
    before_today_bought: int
    after_today_bought: int
    order_id: Optional[str] = None
    memo: str = ""


@dataclass
class Position:
    """
    持仓对象 - 支持 T+N 可卖数量状态机

    支持 A 股（T+1）和可转债（T+0）两种交易制度。

    Attributes:
        symbol: 证券代码
        side: 持仓方向（目前仅支持LONG）
        total_qty: 总持仓数量
        frozen_qty: 冻结数量（等待卖出成交）
        today_bought_qty: 今日买入数量（T日不可卖，仅在 T+1 模式下生效）
        avg_cost: 平均成本价
        last_trade_date: 最后交易日期（用于T+N判断）
        t_plus_n: T+N 规则（0=T+0可转债，1=T+1 A股）
    """

    symbol: str
    side: PositionSide = PositionSide.LONG
    total_qty: int = 0
    frozen_qty: int = 0
    today_bought_qty: int = 0
    avg_cost: Decimal = Decimal("0")
    last_trade_date: Optional[date] = None
    t_plus_n: int = 1  # 默认 T+1（A股），可转债设为 0
    _logs: list = field(default_factory=list, repr=False)

    def __post_init__(self):
        """初始化后校验"""
        if self.total_qty < 0:
            raise ValueError(f"total_qty不能为负: {self.total_qty}")
        if self.frozen_qty < 0:
            raise ValueError(f"frozen_qty不能为负: {self.frozen_qty}")
        if self.today_bought_qty < 0:
            raise ValueError(f"today_bought_qty不能为负: {self.today_bought_qty}")
        if self.frozen_qty > self.total_qty:
            raise ValueError(
                f"frozen_qty({self.frozen_qty})不能大于total_qty({self.total_qty})"
            )

    @property
    def available_to_sell(self) -> int:
        """
        可卖数量

        - T+1 模式：总持仓 - 冻结 - 今日买入
        - T+0 模式：总持仓 - 冻结（今日买入也可卖）

        Returns:
            可卖出的证券数量
        """
        if self.t_plus_n == 0:
            # T+0 模式：今日买入可当日卖出
            return max(0, self.total_qty - self.frozen_qty)
        else:
            # T+N 模式：今日买入不可卖
            return max(0, self.total_qty - self.frozen_qty - self.today_bought_qty)

    @property
    def market_value(self) -> Decimal:
        """
        持仓市值（基于成本价，需外部更新为最新价）

        Returns:
            持仓市值
        """
        return self.avg_cost * self.total_qty

    def _log(
        self,
        reason: PositionUpdateReason,
        qty_change: int,
        before_total: int,
        before_frozen: int,
        before_today_bought: int,
        order_id: Optional[str] = None,
        memo: str = "",
    ):
        """记录持仓变动日志"""
        from datetime import datetime

        log = PositionLog(
            timestamp=datetime.now().isoformat(),
            reason=reason,
            qty_change=qty_change,
            before_total=before_total,
            after_total=self.total_qty,
            before_frozen=before_frozen,
            after_frozen=self.frozen_qty,
            before_today_bought=before_today_bought,
            after_today_bought=self.today_bought_qty,
            order_id=order_id,
            memo=memo,
        )
        self._logs.append(log)

    def buy_filled(
        self,
        qty: int,
        price: Decimal,
        trade_date: date,
        order_id: Optional[str] = None,
    ) -> None:
        """
        买入成交 - 增加持仓，今日买入部分不可卖

        Args:
            qty: 成交数量（必须为正）
            price: 成交价格
            trade_date: 交易日期
            order_id: 订单ID（可选）

        Raises:
            ValueError: 数量无效
        """
        if qty <= 0:
            raise ValueError(f"买入数量必须为正: {qty}")

        before_total = self.total_qty
        before_frozen = self.frozen_qty
        before_today_bought = self.today_bought_qty

        # 更新平均成本
        if self.total_qty == 0:
            self.avg_cost = price
        else:
            total_cost = self.avg_cost * self.total_qty + price * qty
            self.avg_cost = total_cost / (self.total_qty + qty)

        # 增加总持仓和今日买入
        self.total_qty += qty

        # 检查是否同一交易日
        if self.last_trade_date == trade_date:
            self.today_bought_qty += qty
        else:
            # 新交易日，重置今日买入
            self.today_bought_qty = qty
            self.last_trade_date = trade_date

        self._log(
            reason=PositionUpdateReason.BUY_FILLED,
            qty_change=qty,
            before_total=before_total,
            before_frozen=before_frozen,
            before_today_bought=before_today_bought,
            order_id=order_id,
            memo=f"买入成交@{price}",
        )

    def freeze_for_sell(self, qty: int, order_id: Optional[str] = None) -> bool:
        """
        冻结待卖出数量

        Args:
            qty: 冻结数量
            order_id: 订单ID

        Returns:
            是否冻结成功

        Raises:
            ValueError: 冻结数量超过可卖数量
        """
        if qty <= 0:
            raise ValueError(f"冻结数量必须为正: {qty}")

        if qty > self.available_to_sell:
            raise ValueError(
                f"冻结数量({qty})超过可卖数量({self.available_to_sell})"
            )

        before_total = self.total_qty
        before_frozen = self.frozen_qty
        before_today_bought = self.today_bought_qty

        self.frozen_qty += qty

        self._log(
            reason=PositionUpdateReason.SELL_FROZEN,
            qty_change=qty,
            before_total=before_total,
            before_frozen=before_frozen,
            before_today_bought=before_today_bought,
            order_id=order_id,
            memo=f"冻结{qty}股待卖出",
        )
        return True

    def unfreeze(self, qty: int, order_id: Optional[str] = None) -> None:
        """
        解冻（撤单时调用）

        Args:
            qty: 解冻数量
            order_id: 订单ID

        Raises:
            ValueError: 解冻数量超过冻结数量
        """
        if qty <= 0:
            raise ValueError(f"解冻数量必须为正: {qty}")

        if qty > self.frozen_qty:
            raise ValueError(
                f"解冻数量({qty})超过冻结数量({self.frozen_qty})"
            )

        before_total = self.total_qty
        before_frozen = self.frozen_qty
        before_today_bought = self.today_bought_qty

        self.frozen_qty -= qty

        self._log(
            reason=PositionUpdateReason.SELL_UNFROZEN,
            qty_change=-qty,
            before_total=before_total,
            before_frozen=before_frozen,
            before_today_bought=before_today_bought,
            order_id=order_id,
            memo=f"解冻{qty}股（撤单）",
        )

    def sell_filled(
        self,
        qty: int,
        price: Decimal,
        order_id: Optional[str] = None,
    ) -> None:
        """
        卖出成交 - 减少持仓和冻结

        Args:
            qty: 成交数量
            price: 成交价格
            order_id: 订单ID

        Raises:
            ValueError: 成交数量超过冻结数量
        """
        if qty <= 0:
            raise ValueError(f"卖出数量必须为正: {qty}")

        if qty > self.frozen_qty:
            raise ValueError(
                f"卖出成交数量({qty})超过冻结数量({self.frozen_qty})"
            )
        if qty > self.total_qty:
            raise ValueError(
                f"卖出成交数量({qty})超过总持仓({self.total_qty})"
            )

        before_total = self.total_qty
        before_frozen = self.frozen_qty
        before_today_bought = self.today_bought_qty

        self.total_qty -= qty
        self.frozen_qty -= qty

        self._log(
            reason=PositionUpdateReason.SELL_FILLED,
            qty_change=-qty,
            before_total=before_total,
            before_frozen=before_frozen,
            before_today_bought=before_today_bought,
            order_id=order_id,
            memo=f"卖出成交@{price}",
        )

    def day_end_unlock(self, new_trade_date: date) -> None:
        """
        日终解锁 - 将今日买入转为可卖

        应在每个交易日结束时调用，解锁T日买入的股票

        Args:
            new_trade_date: 新交易日日期
        """
        if self.last_trade_date is None or new_trade_date > self.last_trade_date:
            before_total = self.total_qty
            before_frozen = self.frozen_qty
            before_today_bought = self.today_bought_qty

            # 重置今日买入为0
            self.today_bought_qty = 0
            self.last_trade_date = new_trade_date

            self._log(
                reason=PositionUpdateReason.DAY_END_UNLOCK,
                qty_change=0,
                before_total=before_total,
                before_frozen=before_frozen,
                before_today_bought=before_today_bought,
                memo=f"日终解锁，新交易日:{new_trade_date}",
            )

    def get_logs(self) -> list:
        """获取持仓变动日志"""
        return list(self._logs)

    def clear_logs(self) -> None:
        """清空日志"""
        self._logs.clear()

    def to_dict(self) -> dict:
        """导出为字典"""
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "total_qty": self.total_qty,
            "frozen_qty": self.frozen_qty,
            "today_bought_qty": self.today_bought_qty,
            "available_to_sell": self.available_to_sell,
            "avg_cost": str(self.avg_cost),
            "last_trade_date": (
                self.last_trade_date.isoformat() if self.last_trade_date else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"Position(symbol={self.symbol}, total={self.total_qty}, "
            f"frozen={self.frozen_qty}, today_bought={self.today_bought_qty}, "
            f"available={self.available_to_sell})"
        )
