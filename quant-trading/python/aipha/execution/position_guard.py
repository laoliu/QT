"""
实时持仓监控服务 (PositionGuard)

集中管理每只持仓股票的止盈止损、补仓触发、仓位计算。
由 LiveRunner / PaperEngine 在每个 bar/tick 调用 `check_all()`，
返回需要执行的 OrderAction 列表。

核心职责:
  1. 止损监控 — 固定/追踪止损，触发后生成卖出 OrderAction
  2. 止盈监控 — 固定/追踪/分段止盈
  3. 补仓触发 — 价格回落到预设水平时触发加仓
  4. 仓位计算 — 根据信号强度和风控约束计算买卖数量
  5. 新股买入 — 根据 advisory 推荐触发建仓

设计原则:
  - PositionGuard 只输出 OrderAction（意图），不直接操作资金/持仓
  - 规则可通过 YAML 配置
  - 状态可持久化/恢复
  - §4.3 一致性: 回测与实盘共享同一 Guard 实例
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger(__name__)


# ── 数据结构 ──────────────────────────────────────────────────────

class ActionType(str, Enum):
    STOP_LOSS_EXIT = "STOP_LOSS_EXIT"
    TAKE_PROFIT_EXIT = "TAKE_PROFIT_EXIT"
    ADD_POSITION = "ADD_POSITION"       # 补仓
    NEW_ENTRY = "NEW_ENTRY"             # 新股建仓
    REDUCE_POSITION = "REDUCE_POSITION"


@dataclass
class OrderAction:
    """Guard 输出的交易动作意图。"""
    action_type: ActionType
    symbol: str
    side: str            # BUY / SELL
    qty: int             # 建议数量（已按 lot_size 取整）
    price: Decimal       # 参考价格
    reason: str = ""
    urgency: int = 1     # 1=普通, 2=紧急, 3=熔断级
    metadata: dict = field(default_factory=dict)


@dataclass
class PositionSnapshot:
    """持仓快照 — 由调用方传入。"""
    symbol: str
    qty: int
    avg_cost: Decimal
    market_value: Decimal
    current_price: Decimal
    available_to_sell: int = 0   # T+1 后可卖数量


@dataclass
class PriceLevelRule:
    """补仓价格水平规则。

    当价格下跌到 entry_price * (1 - drop_pct) 时触发补仓，
    补仓金额 = 当前持仓市值 * add_ratio。
    """
    drop_pct: float          # 跌幅触发 (如 0.05 = 下跌 5%)
    add_ratio: float         # 补仓比例 (如 0.5 = 补仓 50%)
    max_times: int = 3       # 同一水平最多触发次数
    cooldown_bars: int = 5   # 触发后冷却 N 个 bar


@dataclass
class GuardConfig:
    """PositionGuard 配置。"""
    # 止损
    stop_loss_enabled: bool = True
    stop_loss_pct: float = 0.05      # 5% 固定止损
    trailing_stop_enabled: bool = True
    trailing_stop_pct: float = 0.08  # 8% 追踪止损

    # 止盈
    take_profit_enabled: bool = True
    take_profit_pct: float = 0.15    # 15% 固定止盈
    trailing_tp_enabled: bool = False
    trailing_tp_pct: float = 0.05
    staged_tp_enabled: bool = False
    staged_tp_levels: list[tuple[float, float]] = field(
        default_factory=lambda: [(0.10, 0.3), (0.20, 0.3), (0.30, 0.4)])

    # 补仓
    add_position_enabled: bool = True
    price_levels: list[PriceLevelRule] = field(
        default_factory=lambda: [
            PriceLevelRule(drop_pct=0.05, add_ratio=0.3),
            PriceLevelRule(drop_pct=0.10, add_ratio=0.5),
        ])
    max_add_times_per_symbol: int = 3

    # 新股建仓
    new_entry_enabled: bool = True
    min_confidence: float = 0.6      # advisory 最低置信度
    max_new_entries_per_day: int = 3

    # 仓位估算
    lot_size: int = 100
    max_single_position_pct: float = 0.10  # 单票最大 10% NAV
    max_total_position_pct: float = 0.95   # 最大总仓位 95%

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GuardConfig:
        """从 YAML dict 构建。"""
        levels = []
        for lv in d.get("price_levels", []):
            levels.append(PriceLevelRule(**lv))
        kw = {k: v for k, v in d.items() if k != "price_levels"}
        cfg = cls(**kw)
        if levels:
            cfg.price_levels = levels
        return cfg


# ── 内部追踪状态 ─────────────────────────────────────────────────

@dataclass
class _SymbolTracker:
    """每只股票的内部追踪状态。"""
    symbol: str
    entry_price: Decimal
    highest_price: Decimal
    lowest_price: Decimal
    add_count: int = 0          # 已补仓次数
    add_cooldown: int = 0       # 冷却剩余 bar 数
    staged_triggered: list[int] = field(default_factory=list)
    last_check_bar: int = 0


class PositionGuard:
    """实时持仓监控服务。

    调用方在每个 bar / tick 传入当前持仓快照和市场数据，
    PositionGuard 检查所有规则并返回 OrderAction 列表。

    用法::

        guard = PositionGuard(config, nav=1_000_000)
        # 每个 bar:
        actions = guard.check_all(positions, bar_index)
        for a in actions:
            gateway.submit_order(...)
    """

    def __init__(
        self,
        config: GuardConfig,
        nav: float = 1_000_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.nav = nav
        self._clock = clock or datetime.now
        self._trackers: dict[str, _SymbolTracker] = {}
        self._new_entries_today: int = 0
        self._today_date: str = ""

    def update_nav(self, nav: float) -> None:
        self.nav = nav

    def register_entry(self, symbol: str, price: Decimal) -> None:
        """注册新建仓/加仓，更新追踪状态。"""
        if symbol in self._trackers:
            t = self._trackers[symbol]
            t.entry_price = price
            if price > t.highest_price:
                t.highest_price = price
        else:
            self._trackers[symbol] = _SymbolTracker(
                symbol=symbol, entry_price=price,
                highest_price=price, lowest_price=price,
            )

    def clear_position(self, symbol: str) -> None:
        self._trackers.pop(symbol, None)

    # ── 主入口 ───────────────────────────────────────────────

    def check_tick(
        self,
        symbol: str,
        price: float,
        qty: int,
        available_to_sell: int = 0,
    ) -> OrderAction | None:
        """Tick 级快速检查（仅止损 + 止盈）。

        只检查单一标的的止损/止盈规则，跳过补仓/新股建仓，
        用于 on_tick() 低延迟路径。

        Args:
            symbol: 标的代码
            price: 最新价
            qty: 持仓数量
            available_to_sell: 可卖数量（T+1 限制后）

        Returns:
            OrderAction 或 None
        """
        if available_to_sell <= 0:
            return None
        t = self._trackers.get(symbol)
        if t is None:
            return None

        # 更新最高价
        dec_price = Decimal(str(price))
        if dec_price > t.highest_price:
            t.highest_price = dec_price

        pos = PositionSnapshot(
            symbol=symbol, qty=qty,
            avg_cost=t.entry_price,
            market_value=dec_price * qty,
            current_price=dec_price,
            available_to_sell=available_to_sell,
        )
        # 止损（优先）
        sl = self._check_stop_loss(pos, t)
        if sl:
            sl.urgency = 3  # tick 级止损 → 熔断级紧急
            return sl
        # 止盈
        tp = self._check_take_profit(pos, t)
        if tp:
            tp.urgency = 2
            return tp
        return None

    def check_all(
        self,
        positions: list[PositionSnapshot],
        bar_index: int = 0,
        recommendations: list[dict] | None = None,
    ) -> list[OrderAction]:
        """检查所有持仓 + 潜在新标的，返回 OrderAction 列表。

        Args:
            positions: 当前持仓快照列表
            bar_index: 当前 bar 编号（用于冷却计数）
            recommendations: advisory 推荐列表（可选）
        """
        actions: list[OrderAction] = []
        today = self._clock().strftime("%Y%m%d")
        if today != self._today_date:
            self._today_date = today
            self._new_entries_today = 0

        for pos in positions:
            self._ensure_tracker(pos)
            t = self._trackers[pos.symbol]
            t.highest_price = max(t.highest_price, pos.current_price)
            t.lowest_price = min(t.lowest_price, pos.current_price)
            t.last_check_bar = bar_index

            # 1) 止损检查（优先级最高）
            sl = self._check_stop_loss(pos, t)
            if sl:
                actions.append(sl)
                continue  # 触发止损后不再检查止盈/补仓

            # 2) 止盈检查
            tp = self._check_take_profit(pos, t)
            if tp:
                actions.append(tp)
                continue

            # 3) 补仓检查
            if t.add_cooldown > 0:
                t.add_cooldown -= 1
            else:
                add = self._check_add_position(pos, t)
                if add:
                    actions.append(add)

        # 4) 新股建仓
        if recommendations and self.config.new_entry_enabled:
            new = self._check_new_entries(positions, recommendations)
            actions.extend(new)

        return actions

    # ── 止损 ─────────────────────────────────────────────────

    def _check_stop_loss(
        self, pos: PositionSnapshot, t: _SymbolTracker,
    ) -> OrderAction | None:
        if not self.config.stop_loss_enabled:
            return None
        price = pos.current_price
        sell_qty = self._round_lot(pos.available_to_sell)
        if sell_qty <= 0:
            return None

        # 追踪止损
        if self.config.trailing_stop_enabled:
            drop = float(
                (t.highest_price - price) / t.highest_price
            ) if t.highest_price > 0 else 0
            if drop >= self.config.trailing_stop_pct:
                return OrderAction(
                    action_type=ActionType.STOP_LOSS_EXIT,
                    symbol=pos.symbol, side="SELL",
                    qty=sell_qty, price=price, urgency=2,
                    reason=f"追踪止损: 从最高 {t.highest_price} 回落 {drop:.1%}",
                )

        # 固定止损
        loss = float(
            (t.entry_price - price) / t.entry_price
        ) if t.entry_price > 0 else 0
        if loss >= self.config.stop_loss_pct:
            return OrderAction(
                action_type=ActionType.STOP_LOSS_EXIT,
                symbol=pos.symbol, side="SELL",
                qty=sell_qty, price=price, urgency=2,
                reason=f"固定止损: 亏损 {loss:.1%} >= {self.config.stop_loss_pct:.1%}",
            )
        return None

    # ── 止盈 ─────────────────────────────────────────────────

    def _check_take_profit(
        self, pos: PositionSnapshot, t: _SymbolTracker,
    ) -> OrderAction | None:
        if not self.config.take_profit_enabled:
            return None
        price = pos.current_price
        profit = float(
            (price - t.entry_price) / t.entry_price
        ) if t.entry_price > 0 else 0
        if profit <= 0:
            return None

        sell_qty = self._round_lot(pos.available_to_sell)
        if sell_qty <= 0:
            return None

        # 分段止盈
        if self.config.staged_tp_enabled:
            for i, (level, ratio) in enumerate(
                    self.config.staged_tp_levels):
                if i in t.staged_triggered:
                    continue
                if profit >= level:
                    t.staged_triggered.append(i)
                    partial = self._round_lot(
                        int(pos.available_to_sell * ratio))
                    if partial > 0:
                        return OrderAction(
                            action_type=ActionType.TAKE_PROFIT_EXIT,
                            symbol=pos.symbol, side="SELL",
                            qty=partial, price=price, urgency=1,
                            reason=f"分段止盈 {i+1}: 盈利 {profit:.1%}, 卖出 {ratio:.0%}",
                        )

        # 追踪止盈
        if self.config.trailing_tp_enabled:
            activate = self.config.take_profit_pct / 2
            if profit >= activate:
                drop = float(
                    (t.highest_price - price) / t.highest_price
                ) if t.highest_price > 0 else 0
                if drop >= self.config.trailing_tp_pct:
                    return OrderAction(
                        action_type=ActionType.TAKE_PROFIT_EXIT,
                        symbol=pos.symbol, side="SELL",
                        qty=sell_qty, price=price, urgency=1,
                        reason=f"追踪止盈: 盈利 {profit:.1%}, 从峰值回落 {drop:.1%}",
                    )

        # 固定止盈
        if profit >= self.config.take_profit_pct:
            return OrderAction(
                action_type=ActionType.TAKE_PROFIT_EXIT,
                symbol=pos.symbol, side="SELL",
                qty=sell_qty, price=price, urgency=1,
                reason=f"固定止盈: 盈利 {profit:.1%} >= {self.config.take_profit_pct:.1%}",
            )
        return None

    # ── 补仓 ─────────────────────────────────────────────────

    def _check_add_position(
        self, pos: PositionSnapshot, t: _SymbolTracker,
    ) -> OrderAction | None:
        if not self.config.add_position_enabled:
            return None
        if t.add_count >= self.config.max_add_times_per_symbol:
            return None

        price = pos.current_price
        drop = float(
            (t.entry_price - price) / t.entry_price
        ) if t.entry_price > 0 else 0
        if drop <= 0:
            return None

        # 检查持仓是否已超过单票上限
        pos_pct = float(pos.market_value) / self.nav if self.nav > 0 else 1
        if pos_pct >= self.config.max_single_position_pct:
            return None

        for rule in self.config.price_levels:
            if drop >= rule.drop_pct and t.add_count < rule.max_times:
                add_value = float(pos.market_value) * rule.add_ratio
                max_add = (self.nav * self.config.max_single_position_pct
                           - float(pos.market_value))
                add_value = min(add_value, max(max_add, 0))
                if add_value <= 0:
                    return None
                add_qty = self._round_lot(int(add_value / float(price)))
                if add_qty <= 0:
                    return None
                t.add_count += 1
                t.add_cooldown = rule.cooldown_bars
                return OrderAction(
                    action_type=ActionType.ADD_POSITION,
                    symbol=pos.symbol, side="BUY",
                    qty=add_qty, price=price, urgency=1,
                    reason=f"补仓: 跌 {drop:.1%} >= {rule.drop_pct:.1%}, 加 {add_qty} 股",
                    metadata={"add_count": t.add_count},
                )
        return None

    # ── 新股建仓 ──────────────────────────────────────────────

    def _check_new_entries(
        self,
        positions: list[PositionSnapshot],
        recommendations: list[dict],
    ) -> list[OrderAction]:
        actions: list[OrderAction] = []
        held = {p.symbol for p in positions}
        remaining = (self.config.max_new_entries_per_day
                     - self._new_entries_today)
        if remaining <= 0:
            return actions

        total_val = sum(float(p.market_value) for p in positions)
        total_pct = total_val / self.nav if self.nav > 0 else 1
        if total_pct >= self.config.max_total_position_pct:
            return actions

        for rec in recommendations[:remaining * 2]:
            if len(actions) >= remaining:
                break
            symbol = rec.get("ts_code", rec.get("symbol", ""))
            if not symbol or symbol in held:
                continue
            confidence = float(rec.get("confidence", 0))
            if confidence < self.config.min_confidence:
                continue
            price = Decimal(str(rec.get("price", 0)))
            if price <= 0:
                continue
            alloc = (self.nav * self.config.max_single_position_pct
                     * min(confidence, 1.0))
            qty = self._round_lot(int(alloc / float(price)))
            if qty <= 0:
                continue
            self._new_entries_today += 1
            self.register_entry(symbol, price)
            actions.append(OrderAction(
                action_type=ActionType.NEW_ENTRY,
                symbol=symbol, side="BUY",
                qty=qty, price=price, urgency=1,
                reason=f"新股建仓: confidence={confidence:.2f}",
                metadata={"confidence": confidence,
                          "source": rec.get("source", "advisory")},
            ))
        return actions

    # ── 辅助方法 ──────────────────────────────────────────────

    def _ensure_tracker(self, pos: PositionSnapshot) -> None:
        if pos.symbol not in self._trackers:
            self._trackers[pos.symbol] = _SymbolTracker(
                symbol=pos.symbol,
                entry_price=pos.avg_cost,
                highest_price=max(pos.avg_cost, pos.current_price),
                lowest_price=min(pos.avg_cost, pos.current_price),
            )

    def _round_lot(self, qty: int) -> int:
        """按 lot_size 向下取整。"""
        ls = self.config.lot_size
        return (qty // ls) * ls if ls > 0 else qty

    def get_state(self) -> dict[str, Any]:
        """导出状态用于持久化。"""
        return {
            "trackers": {
                s: {
                    "entry_price": str(t.entry_price),
                    "highest_price": str(t.highest_price),
                    "lowest_price": str(t.lowest_price),
                    "add_count": t.add_count,
                    "staged_triggered": t.staged_triggered,
                }
                for s, t in self._trackers.items()
            },
            "new_entries_today": self._new_entries_today,
            "today_date": self._today_date,
        }

    def load_state(self, data: dict[str, Any]) -> None:
        """从持久化数据恢复状态。"""
        self._today_date = data.get("today_date", "")
        self._new_entries_today = data.get("new_entries_today", 0)
        for s, td in data.get("trackers", {}).items():
            self._trackers[s] = _SymbolTracker(
                symbol=s,
                entry_price=Decimal(td["entry_price"]),
                highest_price=Decimal(td["highest_price"]),
                lowest_price=Decimal(td["lowest_price"]),
                add_count=td.get("add_count", 0),
                staged_triggered=td.get("staged_triggered", []),
            )
