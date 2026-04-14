"""Tests for domain/models.py — 核心领域模型。

覆盖：Side, OrderType, Bar, Order, Fill (properties, frozen, defaults).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aipha.domain.models import Bar, Fill, Order, OrderType, Side


def _ts(hh: int = 9, mm: int = 30) -> datetime:
    return datetime(2024, 1, 2, hh, mm, tzinfo=timezone.utc)


# =========================================================================
# Side
# =========================================================================

class TestSide:
    def test_values(self) -> None:
        assert Side.BUY == "BUY"
        assert Side.SELL == "SELL"

    def test_is_str(self) -> None:
        assert isinstance(Side.BUY, str)


# =========================================================================
# OrderType
# =========================================================================

class TestOrderType:
    def test_basic_types(self) -> None:
        assert OrderType.MARKET == "MARKET"
        assert OrderType.LIMIT == "LIMIT"

    def test_extended_types(self) -> None:
        assert OrderType.FOK == "FOK"
        assert OrderType.IOC == "IOC"
        assert OrderType.BEST_FIVE == "BEST_FIVE"
        assert OrderType.BEST_FIVE_THEN_LIMIT == "BEST_FIVE_THEN_LIMIT"


# =========================================================================
# Bar
# =========================================================================

class TestBar:
    def _default_bar(self, **overrides) -> Bar:
        defaults = dict(
            symbol="000001.SZ",
            start_ts=_ts(9, 30),
            end_ts=_ts(9, 31),
            open=10.0,
            high=10.5,
            low=9.8,
            close=10.2,
            volume=100000.0,
        )
        defaults.update(overrides)
        return Bar(**defaults)

    def test_required_fields(self) -> None:
        b = self._default_bar()
        assert b.symbol == "000001.SZ"
        assert b.open == 10.0
        assert b.volume == 100000.0

    def test_optional_defaults(self) -> None:
        b = self._default_bar()
        assert b.vwap is None
        assert b.prev_close is None
        assert b.is_suspended is False
        assert b.is_st is False
        assert b.is_limit_up is None
        assert b.is_limit_down is None
        assert b.metadata is None

    def test_frozen(self) -> None:
        b = self._default_bar()
        with pytest.raises(AttributeError):
            b.close = 99.0  # type: ignore[misc]

    def test_metadata(self) -> None:
        b = self._default_bar(metadata={"premium_rate": 0.05})
        assert b.metadata["premium_rate"] == 0.05

    def test_limit_flags(self) -> None:
        b = self._default_bar(is_limit_up=True, is_limit_down=False)
        assert b.is_limit_up is True
        assert b.is_limit_down is False

    def test_st_flag(self) -> None:
        b = self._default_bar(is_st=True)
        assert b.is_st is True

    def test_prev_close(self) -> None:
        b = self._default_bar(prev_close=9.5)
        assert b.prev_close == 9.5


# =========================================================================
# Order
# =========================================================================

class TestOrder:
    def _default_order(self, **overrides) -> Order:
        defaults = dict(
            order_id="O001",
            symbol="000001.SZ",
            side=Side.BUY,
            qty=100,
            order_type=OrderType.MARKET,
            t_submit=_ts(),
        )
        defaults.update(overrides)
        return Order(**defaults)

    def test_required_fields(self) -> None:
        o = self._default_order()
        assert o.order_id == "O001"
        assert o.side == Side.BUY
        assert o.qty == 100

    def test_limit_price_default_none(self) -> None:
        o = self._default_order()
        assert o.limit_price is None

    def test_limit_order(self) -> None:
        o = self._default_order(order_type=OrderType.LIMIT, limit_price=10.5)
        assert o.limit_price == 10.5

    def test_frozen(self) -> None:
        o = self._default_order()
        with pytest.raises(AttributeError):
            o.qty = 200  # type: ignore[misc]


# =========================================================================
# Fill
# =========================================================================

class TestFill:
    def _default_fill(self, **overrides) -> Fill:
        defaults = dict(
            order_id="O001",
            symbol="000001.SZ",
            side=Side.BUY,
            qty=100,
            price=10.0,
            t_fill=_ts(),
            model="next_bar_open",
        )
        defaults.update(overrides)
        return Fill(**defaults)

    def test_notional(self) -> None:
        f = self._default_fill(qty=200, price=15.0)
        assert f.notional == pytest.approx(3000.0)

    def test_notional_zero_qty(self) -> None:
        f = self._default_fill(qty=0, price=10.0)
        assert f.notional == 0.0

    def test_frozen(self) -> None:
        f = self._default_fill()
        with pytest.raises(AttributeError):
            f.price = 99.0  # type: ignore[misc]

    def test_fields(self) -> None:
        f = self._default_fill()
        assert f.model == "next_bar_open"
        assert f.side == Side.BUY
