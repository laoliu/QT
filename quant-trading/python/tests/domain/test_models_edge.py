"""Edge tests for domain/models.py — 边界与极端场景。

覆盖: slots 行为、枚举校验、哈希/相等性、repr、极端数值、全可选字段组合。
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from aipha.domain.models import Bar, Fill, Order, OrderType, Side


def _ts(hh: int = 9, mm: int = 30) -> datetime:
    return datetime(2024, 1, 2, hh, mm, tzinfo=timezone.utc)


# =========================================================================
# Side — 枚举边界
# =========================================================================


class TestSideEdge:
    """Side 枚举的边界行为。"""

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            Side("INVALID")

    def test_member_count(self) -> None:
        assert len(Side) == 2
        assert set(Side) == {Side.BUY, Side.SELL}

    def test_string_comparison(self) -> None:
        """str(Enum) 可与纯字符串比较（因为继承 str）。"""
        assert Side.BUY == "BUY"
        assert "SELL" == Side.SELL  # noqa: SIM300

    def test_lookup_by_value(self) -> None:
        assert Side("BUY") is Side.BUY
        assert Side("SELL") is Side.SELL


# =========================================================================
# OrderType — 枚举边界
# =========================================================================


class TestOrderTypeEdge:
    """OrderType 枚举的边界行为。"""

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            OrderType("BOGUS")

    def test_member_count(self) -> None:
        assert len(OrderType) == 6

    def test_all_members(self) -> None:
        expected = {"MARKET", "LIMIT", "FOK", "IOC", "BEST_FIVE", "BEST_FIVE_THEN_LIMIT"}
        assert {ot.value for ot in OrderType} == expected

    def test_lookup_by_value(self) -> None:
        for ot in OrderType:
            assert OrderType(ot.value) is ot


# =========================================================================
# Bar — slots / 哈希 / 相等性 / 全可选字段
# =========================================================================


class TestBarEdge:
    """Bar 的 slots、hashable、metadata 等边界。"""

    def _bar(self, **kw) -> Bar:
        defaults = dict(
            symbol="000001.SZ", start_ts=_ts(9, 30), end_ts=_ts(9, 31),
            open=10.0, high=10.5, low=9.8, close=10.2, volume=100000.0,
        )
        defaults.update(kw)
        return Bar(**defaults)

    def test_slots_block_dynamic_attr(self) -> None:
        """frozen + slots 禁止添加新属性（frozen __setattr__ 优先 → TypeError）。"""
        b = self._bar()
        with pytest.raises(TypeError):
            b.new_field = 42  # type: ignore[attr-defined]

    def test_equality_same_fields(self) -> None:
        b1 = self._bar()
        b2 = self._bar()
        assert b1 == b2

    def test_equality_different_fields(self) -> None:
        b1 = self._bar(close=10.2)
        b2 = self._bar(close=10.3)
        assert b1 != b2

    def test_hashable_as_dict_key(self) -> None:
        b = self._bar()
        d = {b: "val"}
        assert d[b] == "val"

    def test_hashable_in_set(self) -> None:
        b1 = self._bar()
        b2 = self._bar()
        assert len({b1, b2}) == 1

    def test_all_optional_fields_set(self) -> None:
        b = self._bar(
            vwap=10.15, prev_close=9.5,
            is_suspended=True, is_st=True,
            is_limit_up=True, is_limit_down=False,
            metadata={"key": "val"},
        )
        assert b.vwap == 10.15
        assert b.prev_close == 9.5
        assert b.is_suspended is True
        assert b.is_st is True
        assert b.is_limit_up is True
        assert b.is_limit_down is False
        assert b.metadata == {"key": "val"}

    def test_metadata_empty_dict(self) -> None:
        b = self._bar(metadata={})
        assert b.metadata == {}

    def test_metadata_nested(self) -> None:
        meta = {"level1": {"level2": [1, 2, 3]}}
        b = self._bar(metadata=meta)
        assert b.metadata["level1"]["level2"] == [1, 2, 3]

    def test_repr_contains_key_fields(self) -> None:
        b = self._bar()
        r = repr(b)
        assert "000001.SZ" in r
        assert "10.2" in r  # close

    def test_is_suspended_true(self) -> None:
        b = self._bar(is_suspended=True)
        assert b.is_suspended is True


# =========================================================================
# Order — slots / 扩展类型 / 哈希
# =========================================================================


class TestOrderEdge:
    """Order 的 slots、扩展订单类型实例化、哈希。"""

    def _order(self, **kw) -> Order:
        defaults = dict(
            order_id="O001", symbol="000001.SZ", side=Side.BUY,
            qty=100, order_type=OrderType.MARKET, t_submit=_ts(),
        )
        defaults.update(kw)
        return Order(**defaults)

    def test_slots_block_dynamic_attr(self) -> None:
        o = self._order()
        with pytest.raises(TypeError):
            o.extra = 1  # type: ignore[attr-defined]

    def test_fok_order(self) -> None:
        o = self._order(order_type=OrderType.FOK)
        assert o.order_type == OrderType.FOK

    def test_ioc_order(self) -> None:
        o = self._order(order_type=OrderType.IOC)
        assert o.order_type == OrderType.IOC

    def test_best_five_order(self) -> None:
        o = self._order(order_type=OrderType.BEST_FIVE)
        assert o.order_type == OrderType.BEST_FIVE

    def test_best_five_then_limit_order(self) -> None:
        o = self._order(order_type=OrderType.BEST_FIVE_THEN_LIMIT)
        assert o.order_type == OrderType.BEST_FIVE_THEN_LIMIT

    def test_equality(self) -> None:
        o1 = self._order()
        o2 = self._order()
        assert o1 == o2

    def test_hash_in_set(self) -> None:
        o1 = self._order()
        o2 = self._order()
        assert len({o1, o2}) == 1

    def test_repr_contains_key_fields(self) -> None:
        o = self._order(order_id="ORD_XYZ")
        r = repr(o)
        assert "ORD_XYZ" in r
        assert "BUY" in r


# =========================================================================
# Fill — slots / notional 极端值 / 哈希
# =========================================================================


class TestFillEdge:
    """Fill 的 slots、notional 极端值、哈希。"""

    def _fill(self, **kw) -> Fill:
        defaults = dict(
            order_id="O001", symbol="000001.SZ", side=Side.BUY,
            qty=100, price=10.0, t_fill=_ts(), model="next_bar_open",
        )
        defaults.update(kw)
        return Fill(**defaults)

    def test_slots_block_dynamic_attr(self) -> None:
        f = self._fill()
        with pytest.raises(TypeError):
            f.extra = 1  # type: ignore[attr-defined]

    def test_notional_negative_price(self) -> None:
        """dataclass 不做运行时校验，负价不会报错。"""
        f = self._fill(price=-5.0, qty=100)
        assert f.notional == pytest.approx(-500.0)

    def test_notional_large_values(self) -> None:
        f = self._fill(qty=10_000_000, price=100_000.0)
        assert f.notional == pytest.approx(1e12)

    def test_equality(self) -> None:
        f1 = self._fill()
        f2 = self._fill()
        assert f1 == f2

    def test_hash_in_set(self) -> None:
        f1 = self._fill()
        f2 = self._fill()
        assert len({f1, f2}) == 1

    def test_different_model_not_equal(self) -> None:
        f1 = self._fill(model="next_bar_open")
        f2 = self._fill(model="vwap")
        assert f1 != f2

    def test_repr_contains_key_fields(self) -> None:
        f = self._fill(order_id="F_EDGE", model="twap")
        r = repr(f)
        assert "F_EDGE" in r
        assert "twap" in r

    def test_sell_fill(self) -> None:
        f = self._fill(side=Side.SELL, qty=50, price=12.0)
        assert f.side == Side.SELL
        assert f.notional == pytest.approx(600.0)
