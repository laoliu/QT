"""市场规则定义 — A 股 + 可转债。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MarketRules:
    """交易市场规则基类"""
    name: str
    lot_size: int
    t_plus: int
    limit_up_pct: float
    limit_down_pct: float
    commission_rate: float
    commission_min: float
    stamp_tax_rate: float
    transfer_fee_rate: float
    slippage_bps_default: float


class MarketRulesCNEquity(MarketRules):
    """A 股市场规则"""

    def __init__(self):
        super().__init__(
            name="cn_equity",
            lot_size=100,
            t_plus=1,
            limit_up_pct=0.10,
            limit_down_pct=-0.10,
            commission_rate=8.54e-05,
            commission_min=5.0,
            stamp_tax_rate=0.0005,
            transfer_fee_rate=0.00001,
            slippage_bps_default=10.0,
        )


class MarketRulesCB(MarketRules):
    """可转债市场规则 (2022 新规后)"""

    def __init__(self):
        super().__init__(
            name="convertible_bond",
            lot_size=10,
            t_plus=0,
            limit_up_pct=0.20,
            limit_down_pct=-0.20,
            commission_rate=5.0e-05,
            commission_min=1.0,
            stamp_tax_rate=0.0,
            transfer_fee_rate=0.0,
            slippage_bps_default=5.0,
        )
