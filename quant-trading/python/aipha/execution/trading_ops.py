"""统一交易运算 — 费用计算 / 滑点 / NAV"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FeeParams:
    commission_rate: float = 0.0003
    stamp_tax: float = 0.001
    min_commission: float = 5.0
    transfer_fee: float = 0.00002
    is_cb: bool = False

    @property
    def buy_cost_rate(self) -> float:
        return self.commission_rate + self.transfer_fee

    @property
    def sell_cost_rate(self) -> float:
        if self.is_cb:
            return self.commission_rate + self.transfer_fee
        return self.commission_rate + self.stamp_tax + self.transfer_fee


def get_fee_params(market: str = "a_share") -> FeeParams:
    """工厂: 获取特定市场费率"""
    presets = {
        "a_share": FeeParams(commission_rate=0.0003, stamp_tax=0.001),
        "a_share_etf": FeeParams(commission_rate=0.0003, stamp_tax=0.0),
        "convertible_bond": FeeParams(commission_rate=0.00005, stamp_tax=0.0, is_cb=True),
        "hk_stock": FeeParams(commission_rate=0.0005, stamp_tax=0.001),
    }
    return presets.get(market, FeeParams())


def calc_trade_cost(
    price: float,
    quantity: int,
    side: str,
    params: Optional[FeeParams] = None,
) -> float:
    """计算单笔交易费用 (元)"""
    params = params or get_fee_params()
    amount = price * quantity
    if side == "buy":
        commission = max(amount * params.commission_rate, params.min_commission)
        transfer = amount * params.transfer_fee
        return commission + transfer
    else:
        commission = max(amount * params.commission_rate, params.min_commission)
        stamp = amount * params.stamp_tax
        transfer = amount * params.transfer_fee
        return commission + stamp + transfer


def apply_slippage(
    price: float,
    side: str,
    rate: float = 0.001,
) -> float:
    """根据方向施加滑点"""
    if side == "buy":
        return price * (1 + rate)
    return price * (1 - rate)


@dataclass
class NavResult:
    nav: float
    cash: float
    market_value: float
    pnl: float
    pnl_pct: float


def calc_nav(
    cash: float,
    positions: dict[str, int],
    prices: dict[str, float],
    initial_nav: float = 1_000_000,
) -> NavResult:
    """计算净值"""
    mv = sum(positions.get(sym, 0) * prices.get(sym, 0) for sym in positions)
    nav = cash + mv
    pnl = nav - initial_nav
    pnl_pct = pnl / initial_nav if initial_nav > 0 else 0
    return NavResult(nav=nav, cash=cash, market_value=mv, pnl=pnl, pnl_pct=pnl_pct)
