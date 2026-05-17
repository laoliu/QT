"""
Market 模块

包含市场规则、交易约束、涨跌停检测等核心组件。
"""

from aipha.market.base import MarketRules, PrecheckDecision
from aipha.market.halt_checker import (
    HaltChecker,
    HaltStatus,
    LimitStatus,
    MarketStatus,
)
from aipha.market.price_limits import (
    LIMIT_DOWN_TOLERANCE,
    LIMIT_UP_TOLERANCE,
    is_at_limit_down,
    is_at_limit_up,
)
from aipha.market.rules_cn_equity import MarketRulesCNEquity
from aipha.market.rules_convertible_bond import MarketRulesConvertibleBond
from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR

__all__ = [
    "HaltChecker",
    "HaltStatus",
    "LIMIT_DOWN_TOLERANCE",
    "LIMIT_UP_TOLERANCE",
    "LimitStatus",
    "MarketRules",
    "MarketRulesCNEquity",
    "MarketRulesConvertibleBond",
    "MarketStatus",
    "PrecheckDecision",
    "TRADING_DAYS_PER_YEAR",
    "is_at_limit_down",
    "is_at_limit_up",
]
