from __future__ import annotations

from datetime import datetime, timezone

from aipha.domain.models import Bar, Order, OrderType, Side
from aipha.execution.fill_models import (
    BarLimitTouchFillModel,
    NextBarCloseFillModel,
    NextBarOpenFillModel,
    SameBarCloseFillModel,
    get_fill_model,
)
from aipha.market.rules_cn_equity import MarketRulesCNEquity, RejectReason


def _ts(y: int, m: int, d: int, hh: int, mm: int, ss: int = 0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc)


def test_next_bar_open_fill() -> None:
    rules = MarketRulesCNEquity(lot_size=100)
    fill_model = NextBarOpenFillModel()

    order = Order(
        order_id="o1",
        symbol="000001.SZ",
        side=Side.BUY,
        qty=100,
        order_type=OrderType.MARKET,
        t_submit=_ts(2020, 1, 2, 9, 31),
    )
    bar = Bar(
        symbol="000001.SZ",
        start_ts=_ts(2020, 1, 2, 9, 32),
        end_ts=_ts(2020, 1, 2, 9, 33),
        open=10.0,
        high=10.2,
        low=9.9,
        close=10.1,
        volume=10000,
    )

    result = fill_model.try_fill(order=order, next_bar=bar, rules=rules)
    assert result.fill is not None
    assert result.fill.price == 10.0


def test_next_bar_close_fill() -> None:
    rules = MarketRulesCNEquity(lot_size=100)
    fill_model = NextBarCloseFillModel()

    order = Order(
        order_id="o1",
        symbol="000001.SZ",
        side=Side.BUY,
        qty=100,
        order_type=OrderType.MARKET,
        t_submit=_ts(2020, 1, 2, 9, 31),
    )
    bar = Bar(
        symbol="000001.SZ",
        start_ts=_ts(2020, 1, 2, 9, 32),
        end_ts=_ts(2020, 1, 2, 9, 33),
        open=10.0,
        high=10.2,
        low=9.9,
        close=10.1,
        volume=10000,
    )

    result = fill_model.try_fill(order=order, next_bar=bar, rules=rules)
    assert result.fill is not None
    assert result.fill.price == 10.1


def test_limit_touch_no_fill_if_not_touched() -> None:
    rules = MarketRulesCNEquity(lot_size=100)
    fill_model = BarLimitTouchFillModel()

    order = Order(
        order_id="o1",
        symbol="000001.SZ",
        side=Side.BUY,
        qty=100,
        order_type=OrderType.LIMIT,
        limit_price=9.0,
        t_submit=_ts(2020, 1, 2, 9, 31),
    )
    bar = Bar(
        symbol="000001.SZ",
        start_ts=_ts(2020, 1, 2, 9, 32),
        end_ts=_ts(2020, 1, 2, 9, 33),
        open=10.0,
        high=10.2,
        low=9.9,
        close=10.1,
        volume=10000,
    )

    result = fill_model.try_fill(order=order, next_bar=bar, rules=rules)
    assert result.fill is None
    assert result.no_fill_reason is not None


def test_rules_reject_lot_size() -> None:
    rules = MarketRulesCNEquity(lot_size=100)
    fill_model = NextBarOpenFillModel()

    order = Order(
        order_id="o1",
        symbol="000001.SZ",
        side=Side.BUY,
        qty=50,
        order_type=OrderType.MARKET,
        t_submit=_ts(2020, 1, 2, 9, 31),
    )
    bar = Bar(
        symbol="000001.SZ",
        start_ts=_ts(2020, 1, 2, 9, 32),
        end_ts=_ts(2020, 1, 2, 9, 33),
        open=10.0,
        high=10.2,
        low=9.9,
        close=10.1,
        volume=10000,
    )

    result = fill_model.try_fill(order=order, next_bar=bar, rules=rules)
    assert result.fill is None
    assert result.reject_reason == RejectReason.LOT_SIZE_ROUNDING


def test_same_bar_close_fill() -> None:
    """SameBarCloseFillModel 以当 bar close 即时成交"""
    rules = MarketRulesCNEquity(lot_size=100)
    fill_model = SameBarCloseFillModel()

    order = Order(
        order_id="o1",
        symbol="000001.SZ",
        side=Side.BUY,
        qty=100,
        order_type=OrderType.MARKET,
        t_submit=_ts(2020, 1, 2, 9, 31),
    )
    bar = Bar(
        symbol="000001.SZ",
        start_ts=_ts(2020, 1, 2, 9, 32),
        end_ts=_ts(2020, 1, 2, 9, 33),
        open=10.0,
        high=10.2,
        low=9.9,
        close=10.1,
        volume=10000,
    )

    result = fill_model.try_fill(order=order, next_bar=bar, rules=rules)
    assert result.fill is not None
    assert result.fill.price == 10.1  # close 价
    assert result.fill.model == "same_bar_close"


def test_same_bar_close_is_same_bar_property() -> None:
    """SameBarCloseFillModel.is_same_bar 为 True，其它为 False"""
    assert SameBarCloseFillModel().is_same_bar is True
    assert NextBarOpenFillModel().is_same_bar is False
    assert NextBarCloseFillModel().is_same_bar is False


def test_get_fill_model_factory() -> None:
    """get_fill_model 工厂函数正确分发"""
    m1 = get_fill_model("next_bar_open")
    assert isinstance(m1, NextBarOpenFillModel)

    m2 = get_fill_model("same_bar_close")
    assert isinstance(m2, SameBarCloseFillModel)
