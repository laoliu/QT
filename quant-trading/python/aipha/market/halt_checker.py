"""停牌检查器 — 从行情/日历判断标的是否可交易"""
from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any


class HaltStatus(Enum):
    """停牌状态"""
    TRADING = "trading"
    HALTED = "halted"
    SUSPENDED = "suspended"
    DELISTING = "delisting"


class BoardType(Enum):
    """板块类型（决定涨跌幅限制）"""
    MAIN = "main"
    SME = "sme"
    GEM = "gem"
    STAR = "star"
    BSE = "bse"
    ST = "st"
    NEW_LISTING = "new_listing"


class HaltChecker:
    """检查标的是否处于停牌或涨跌停状态。"""

    def __init__(self) -> None:
        self._halt_set: set[str] = set()

    def update_halts(self, halted_symbols: set[str]) -> None:
        self._halt_set = halted_symbols

    def is_halted(self, symbol: str) -> bool:
        return symbol in self._halt_set

    def is_tradable(self, symbol: str, bar: dict[str, Any] | None = None) -> bool:
        """综合判断: 非停牌 + 非一字涨跌停。"""
        if self.is_halted(symbol):
            return False
        if bar is None:
            return True
        # 一字涨停: open == high == low == close 且涨停
        o, h, lo, c = bar.get("open", 0), bar.get("high", 0), bar.get("low", 0), bar.get("close", 0)
        if o == h == lo == c and c > 0:
            pct = bar.get("pct_chg", 0) or 0
            if abs(pct) >= 9.5:  # 涨跌停板
                return False
        return True

    def filter_tradable(self, bars: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {sym: bar for sym, bar in bars.items() if self.is_tradable(sym, bar)}


class OrderValidator:
    """委托前置校验 — 检查委托合法性。"""

    def __init__(
        self,
        lot_size: int = 100,
        price_tick: float = 0.01,
        max_single_order: int = 1_000_000,
    ) -> None:
        self.lot_size = lot_size
        self.price_tick = price_tick
        self.max_single_order = max_single_order

    def validate(
        self,
        symbol: str,
        side: str,
        qty: int,
        price: float,
        position: int = 0,
    ) -> tuple[bool, str]:
        """返回 (valid, reason)"""
        if qty <= 0:
            return False, "qty must be positive"
        if qty % self.lot_size != 0:
            return False, f"qty must be multiple of {self.lot_size}"
        if qty > self.max_single_order:
            return False, f"qty {qty} exceeds max single order {self.max_single_order}"
        if price <= 0:
            return False, "price must be positive"
        # 卖出不能超过持仓
        if side.lower() == "sell" and qty > position:
            return False, f"sell qty {qty} > position {position}"
        return True, "ok"

    def round_qty(self, qty: float) -> int:
        """向下取整到手数。"""
        return int(qty / self.lot_size) * self.lot_size

    def round_price(self, price: float) -> float:
        return round(round(price / self.price_tick) * self.price_tick, 4)
