"""
持仓管理器 - 管理多标的持仓

PositionManager 负责：
- 维护所有标的的持仓状态
- 处理 Fill 并更新持仓
- 提供持仓快照供策略读取
- 日终批量解锁 T+1

设计原则（per copilot-instructions.md）：
- 持仓更新只能通过 Fill 触发
- 对外提供只读视图
- 所有变更可审计
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING

from aipha.portfolio.position import Position

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from aipha.domain.models import Fill


@dataclass
class PositionSnapshot:
    """持仓快照（只读视图）"""

    symbol: str
    total_qty: int
    frozen_qty: int
    available_to_sell: int
    today_bought_qty: int
    avg_cost: float
    market_value: float  # 市值 = total_qty * current_price


@dataclass
class PositionManager:
    """持仓管理器

    管理多个标的的持仓状态，处理成交并更新持仓。

    Attributes:
        positions: symbol -> Position 映射
        default_t_plus_n: 新建持仓的默认 T+N 规则（0=T+0 可转债，1=T+1 A股）
        _fill_history: 成交历史记录
    """

    positions: dict[str, Position] = field(default_factory=dict)
    default_t_plus_n: int = 1
    _fill_history: list = field(default_factory=list, repr=False)

    def get_position(self, symbol: str) -> Position | None:
        """获取指定标的的持仓对象"""
        return self.positions.get(symbol)

    def get_qty(self, symbol: str) -> int:
        """获取指定标的的持仓数量（0 if not held）"""
        pos = self.positions.get(symbol)
        return pos.total_qty if pos else 0

    def get_available_to_sell(self, symbol: str) -> int:
        """获取指定标的的可卖数量"""
        pos = self.positions.get(symbol)
        return pos.available_to_sell if pos else 0

    def get_frozen(self, symbol: str) -> int:
        """获取指定标的的冻结数量"""
        pos = self.positions.get(symbol)
        return pos.frozen_qty if pos else 0

    def get_all_symbols(self) -> list[str]:
        """获取所有持仓标的代码"""
        return list(self.positions.keys())

    def get_positions_snapshot(
        self, prices: dict[str, float] | None = None
    ) -> dict[str, PositionSnapshot]:
        """获取所有持仓的只读快照

        Args:
            prices: 当前价格映射（用于计算市值）

        Returns:
            symbol -> PositionSnapshot 映射
        """
        prices = prices or {}
        snapshots = {}
        for symbol, pos in self.positions.items():
            price = prices.get(symbol, 0.0)
            snapshots[symbol] = PositionSnapshot(
                symbol=symbol,
                total_qty=pos.total_qty,
                frozen_qty=pos.frozen_qty,
                available_to_sell=pos.available_to_sell,
                today_bought_qty=pos.today_bought_qty,
                avg_cost=float(pos.avg_cost),
                market_value=pos.total_qty * price,
            )
        return snapshots

    def get_positions_dict(self) -> dict[str, int]:
        """获取持仓字典（供策略 Context 使用）

        Returns:
            symbol -> total_qty 映射
        """
        return {symbol: pos.total_qty for symbol, pos in self.positions.items()}

    def process_fill(self, fill: "Fill", trade_date: date) -> None:
        """处理成交并更新持仓

        Args:
            fill: 成交对象
            trade_date: 交易日期（用于 T+1 判断）
        """
        from aipha.domain.models import Side

        symbol = fill.symbol

        # 确保持仓对象存在
        if symbol not in self.positions:
            self.positions[symbol] = Position(symbol=symbol, t_plus_n=self.default_t_plus_n)

        pos = self.positions[symbol]

        if fill.side == Side.BUY:
            # 买入：增加持仓，记录今日买入
            pos.buy_filled(
                qty=fill.qty,
                price=Decimal(str(fill.price)),
                trade_date=trade_date,
                order_id=fill.order_id,
            )
        elif fill.side == Side.SELL:
            # 卖出：减少持仓（假设已冻结）
            pos.sell_filled(
                qty=fill.qty,
                price=Decimal(str(fill.price)),
                order_id=fill.order_id,
            )

        # 清理空仓位
        if pos.total_qty == 0:
            del self.positions[symbol]

        # 记录成交历史
        self._fill_history.append(
            {
                "fill": fill,
                "trade_date": trade_date,
                "symbol": symbol,
                "side": fill.side,
                "qty": fill.qty,
                "price": fill.price,
            }
        )

    def freeze_for_sell(self, symbol: str, qty: int, order_id: str) -> bool:
        """为卖出订单冻结持仓

        Args:
            symbol: 标的代码
            qty: 冻结数量
            order_id: 订单ID

        Returns:
            是否冻结成功
        """
        pos = self.positions.get(symbol)
        if pos is None:
            return False

        try:
            pos.freeze_for_sell(qty=qty, order_id=order_id)
            return True
        except ValueError:
            return False

    def unfreeze_for_sell(self, symbol: str, qty: int, order_id: str) -> bool:
        """解冻卖出订单的持仓（撤单时）

        Args:
            symbol: 标的代码
            qty: 解冻数量
            order_id: 订单ID

        Returns:
            是否解冻成功
        """
        pos = self.positions.get(symbol)
        if pos is None:
            return False

        try:
            pos.unfreeze(qty=qty, order_id=order_id)
            return True
        except ValueError:
            return False

    def day_end_process(self, trade_date: date) -> None:
        """日终处理：解锁所有 T+1 持仓

        在每个交易日结束时调用，将今日买入的股票
        转为可卖状态（用于下一交易日）

        Args:
            trade_date: 当前交易日
        """
        for pos in self.positions.values():
            pos.day_end_unlock(new_trade_date=trade_date)

    def get_total_market_value(self, prices: dict[str, float]) -> float:
        """计算总市值

        缺价标的用 avg_cost 回退（§13.11 统一缺价策略）。

        Args:
            prices: 当前价格映射

        Returns:
            总市值
        """
        total = 0.0
        for symbol, pos in self.positions.items():
            price = prices.get(symbol, 0.0)
            if price > 0:
                total += pos.total_qty * price
            elif pos.avg_cost > 0:
                total += pos.total_qty * float(pos.avg_cost)
                logger.warning(
                    "PositionManager: %s 缺行情, 用均价 %.4f 估值",
                    symbol, float(pos.avg_cost),
                )
            else:
                logger.warning(
                    "PositionManager: %s 缺行情且无均价, 估值为 0",
                    symbol,
                )
        return total

    def get_fill_history(self) -> list[dict]:
        """获取成交历史"""
        return list(self._fill_history)

    def __len__(self) -> int:
        """持仓标的数量"""
        return len(self.positions)

    def __contains__(self, symbol: str) -> bool:
        """检查是否持有某标的"""
        return symbol in self.positions
