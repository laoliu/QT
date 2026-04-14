"""持仓管理器 — 统一持仓跟踪 + 成本计算"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class Position:
    symbol: str
    quantity: int = 0
    avg_cost: float = 0.0
    frozen: int = 0
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    last_price: float = 0.0
    last_update: Optional[datetime] = None

    @property
    def available(self) -> int:
        return max(0, self.quantity - self.frozen)

    @property
    def cost_basis(self) -> float:
        return self.quantity * self.avg_cost

    def update_price(self, price: float):
        self.last_price = price
        self.market_value = self.quantity * price
        self.unrealized_pnl = self.market_value - self.cost_basis
        self.last_update = datetime.now()


class PositionManager:
    """统一持仓管理器"""

    def __init__(self):
        self._positions: dict[str, Position] = {}
        self._history: list[dict[str, Any]] = []

    def get(self, symbol: str) -> Optional[Position]:
        return self._positions.get(symbol)

    def get_or_create(self, symbol: str) -> Position:
        if symbol not in self._positions:
            self._positions[symbol] = Position(symbol=symbol)
        return self._positions[symbol]

    def buy(self, symbol: str, quantity: int, price: float) -> Position:
        pos = self.get_or_create(symbol)
        old_cost = pos.quantity * pos.avg_cost
        new_cost = quantity * price
        pos.quantity += quantity
        pos.avg_cost = (old_cost + new_cost) / pos.quantity if pos.quantity > 0 else 0
        pos.update_price(price)
        self._log("BUY", symbol, quantity, price)
        return pos

    def sell(self, symbol: str, quantity: int, price: float) -> Position:
        pos = self.get_or_create(symbol)
        if quantity > pos.available:
            raise ValueError(f"可用不足: {symbol} 持有{pos.quantity} 冻结{pos.frozen} 可卖{pos.available}")
        realized = (price - pos.avg_cost) * quantity
        pos.realized_pnl += realized
        pos.quantity -= quantity
        if pos.quantity <= 0:
            pos.quantity = 0
            pos.avg_cost = 0
        pos.update_price(price)
        self._log("SELL", symbol, quantity, price, realized)
        return pos

    def update_prices(self, prices: dict[str, float]):
        for sym, price in prices.items():
            if sym in self._positions:
                self._positions[sym].update_price(price)

    def freeze(self, symbol: str, quantity: int):
        pos = self.get_or_create(symbol)
        pos.frozen = min(pos.quantity, pos.frozen + quantity)

    def unfreeze(self, symbol: str, quantity: int):
        pos = self.get_or_create(symbol)
        pos.frozen = max(0, pos.frozen - quantity)

    @property
    def all_positions(self) -> dict[str, Position]:
        return {s: p for s, p in self._positions.items() if p.quantity > 0}

    @property
    def total_market_value(self) -> float:
        return sum(p.market_value for p in self._positions.values())

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self._positions.values())

    @property
    def total_realized_pnl(self) -> float:
        return sum(p.realized_pnl for p in self._positions.values())

    def snapshot(self) -> dict[str, Any]:
        return {
            "positions": {s: {"qty": p.quantity, "avg_cost": p.avg_cost, "mv": p.market_value,
                              "pnl": p.unrealized_pnl} for s, p in self.all_positions.items()},
            "total_mv": self.total_market_value,
            "unrealized_pnl": self.total_unrealized_pnl,
            "realized_pnl": self.total_realized_pnl,
        }

    def _log(self, action: str, symbol: str, qty: int, price: float, pnl: float = 0):
        self._history.append({
            "time": datetime.now().isoformat(), "action": action,
            "symbol": symbol, "qty": qty, "price": price, "pnl": pnl,
        })

    def clear(self):
        self._positions.clear()
        self._history.clear()
