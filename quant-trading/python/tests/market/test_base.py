"""Tests for market/base.py — 统一市场规则接口。

覆盖：PrecheckDecision (fields, defaults), MarketRules Protocol.
"""

from __future__ import annotations

from typing import Any

import pytest

from aipha.domain.models import Bar, Order, OrderType, Side
from aipha.market.base import MarketRules, PrecheckDecision


# =========================================================================
# PrecheckDecision
# =========================================================================

class TestPrecheckDecision:
    def test_allowed(self) -> None:
        d = PrecheckDecision(allowed=True)
        assert d.allowed is True
        assert d.reason is None
        assert d.warning is None

    def test_rejected_with_reason(self) -> None:
        d = PrecheckDecision(allowed=False, reason="SUSPENDED")
        assert not d.allowed
        assert d.reason == "SUSPENDED"

    def test_allowed_with_warning(self) -> None:
        d = PrecheckDecision(allowed=True, warning="强赎预警")
        assert d.allowed
        assert d.warning == "强赎预警"

    def test_frozen(self) -> None:
        d = PrecheckDecision(allowed=True)
        with pytest.raises(AttributeError):
            d.allowed = False  # type: ignore[misc]


# =========================================================================
# MarketRules Protocol
# =========================================================================

class TestMarketRulesProtocol:
    def test_runtime_checkable(self) -> None:
        """实现了 lot_size 属性和 precheck_order 方法的类满足 Protocol。"""

        class _MockRules:
            @property
            def lot_size(self) -> int:
                return 100

            def precheck_order(self, *, order: Order, bar: Bar, **kwargs: Any) -> PrecheckDecision:
                return PrecheckDecision(allowed=True)

        assert isinstance(_MockRules(), MarketRules)

    def test_cn_equity_satisfies_protocol(self) -> None:
        from aipha.market.rules_cn_equity import MarketRulesCNEquity
        assert isinstance(MarketRulesCNEquity(), MarketRules)
