"""Tests for execution/gateway.py — 交易网关抽象层。

覆盖：OrderStatus, TradingMode, GatewayOrder (properties / state),
ExecutionReport, OrderStatusChange, AccountSnapshot,
TradingGateway Protocol (ABC), Gateway exceptions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from aipha.execution.gateway import (
    AccountSnapshot,
    ExecutionReport,
    GatewayError,
    GatewayNotConnectedError,
    GatewayOrder,
    InsufficientFundsError,
    InsufficientPositionError,
    OrderStatus,
    OrderStatusChange,
    OrderValidationError,
    TradingGateway,
    TradingMode,
)


def _now() -> datetime:
    return datetime(2024, 6, 1, 10, 0, tzinfo=timezone.utc)


# =========================================================================
# OrderStatus
# =========================================================================

class TestOrderStatus:
    def test_values(self) -> None:
        assert OrderStatus.PENDING == "pending"
        assert OrderStatus.SUBMITTED == "submitted"
        assert OrderStatus.PARTIAL_FILLED == "partial_filled"
        assert OrderStatus.FILLED == "filled"
        assert OrderStatus.CANCELED == "canceled"
        assert OrderStatus.REJECTED == "rejected"
        assert OrderStatus.EXPIRED == "expired"

    def test_string_enum(self) -> None:
        assert isinstance(OrderStatus.FILLED, str)


# =========================================================================
# TradingMode
# =========================================================================

class TestTradingMode:
    def test_values(self) -> None:
        assert TradingMode.BACKTEST == "backtest"
        assert TradingMode.PAPER == "paper"
        assert TradingMode.LIVE == "live"


# =========================================================================
# GatewayOrder
# =========================================================================

class TestGatewayOrder:
    def _default_order(self, **overrides) -> GatewayOrder:
        defaults = dict(
            order_id="O001",
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            order_type="MARKET",
        )
        defaults.update(overrides)
        return GatewayOrder(**defaults)

    def test_remaining_qty_initial(self) -> None:
        o = self._default_order()
        assert o.remaining_qty == 100

    def test_remaining_qty_partial(self) -> None:
        o = self._default_order(filled_qty=30)
        assert o.remaining_qty == 70

    def test_remaining_qty_full(self) -> None:
        o = self._default_order(qty=100, filled_qty=100)
        assert o.remaining_qty == 0

    def test_is_done_for_terminal_states(self) -> None:
        for status in (OrderStatus.FILLED, OrderStatus.CANCELED,
                       OrderStatus.REJECTED, OrderStatus.EXPIRED):
            o = self._default_order(status=status)
            assert o.is_done, f"Expected is_done for {status}"
            assert not o.is_active

    def test_is_active_for_open_states(self) -> None:
        for status in (OrderStatus.PENDING, OrderStatus.SUBMITTED,
                       OrderStatus.PARTIAL_FILLED):
            o = self._default_order(status=status)
            assert o.is_active, f"Expected is_active for {status}"
            assert not o.is_done

    def test_default_metadata(self) -> None:
        o = self._default_order()
        assert o.metadata == {}

    def test_limit_price(self) -> None:
        o = self._default_order(order_type="LIMIT", limit_price=10.5)
        assert o.limit_price == 10.5

    def test_reject_reason(self) -> None:
        o = self._default_order(status=OrderStatus.REJECTED, reject_reason="risk_limit")
        assert o.reject_reason == "risk_limit"

    def test_cancel_reason(self) -> None:
        o = self._default_order(status=OrderStatus.CANCELED, cancel_reason="user")
        assert o.cancel_reason == "user"

    def test_timestamps_default_none(self) -> None:
        o = self._default_order()
        assert o.created_at is None
        assert o.submitted_at is None
        assert o.filled_at is None


# =========================================================================
# ExecutionReport
# =========================================================================

class TestExecutionReport:
    def test_fields(self) -> None:
        er = ExecutionReport(
            order_id="O001",
            exec_id="E001",
            symbol="000001.SZ",
            side="BUY",
            fill_qty=100,
            fill_price=10.0,
            fill_time=_now(),
        )
        assert er.fill_qty == 100
        assert er.commission == Decimal("0")
        assert er.is_last is False

    def test_frozen(self) -> None:
        er = ExecutionReport(
            order_id="O1", exec_id="E1", symbol="X", side="BUY",
            fill_qty=1, fill_price=1.0, fill_time=_now(),
        )
        with pytest.raises(AttributeError):
            er.fill_qty = 200  # type: ignore[misc]


# =========================================================================
# OrderStatusChange
# =========================================================================

class TestOrderStatusChange:
    def test_fields(self) -> None:
        sc = OrderStatusChange(
            order_id="O1",
            old_status=OrderStatus.PENDING,
            new_status=OrderStatus.SUBMITTED,
            timestamp=_now(),
        )
        assert sc.reason is None

    def test_with_reason(self) -> None:
        sc = OrderStatusChange(
            order_id="O1",
            old_status=OrderStatus.SUBMITTED,
            new_status=OrderStatus.REJECTED,
            timestamp=_now(),
            reason="risk",
        )
        assert sc.reason == "risk"


# =========================================================================
# AccountSnapshot
# =========================================================================

class TestAccountSnapshot:
    def test_fields(self) -> None:
        snap = AccountSnapshot(
            timestamp=_now(),
            cash=Decimal("100000"),
            frozen_cash=Decimal("5000"),
            total_equity=Decimal("150000"),
            positions={"000001.SZ": 1000, "600000.SH": 500},
        )
        assert snap.positions["000001.SZ"] == 1000
        assert snap.positions_value == Decimal("0")

    def test_positions_value_override(self) -> None:
        snap = AccountSnapshot(
            timestamp=_now(),
            cash=Decimal("100000"),
            frozen_cash=Decimal("0"),
            total_equity=Decimal("200000"),
            positions={"X": 100},
            positions_value=Decimal("100000"),
        )
        assert snap.positions_value == Decimal("100000")


# =========================================================================
# TradingGateway (ABC — cannot instantiate directly)
# =========================================================================

class TestTradingGatewayABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError):
            TradingGateway()  # type: ignore[abstract]


# =========================================================================
# Gateway exceptions
# =========================================================================

class TestGatewayExceptions:
    def test_gateway_error_message(self) -> None:
        e = GatewayError("boom", code="E001")
        assert str(e) == "boom"
        assert e.code == "E001"

    def test_gateway_error_no_code(self) -> None:
        e = GatewayError("fail")
        assert e.code is None

    def test_subclass_hierarchy(self) -> None:
        assert issubclass(OrderValidationError, GatewayError)
        assert issubclass(InsufficientFundsError, GatewayError)
        assert issubclass(InsufficientPositionError, GatewayError)
        assert issubclass(GatewayNotConnectedError, GatewayError)

    def test_catch_by_base(self) -> None:
        with pytest.raises(GatewayError):
            raise InsufficientFundsError("no cash")

    def test_insufficient_position_error(self) -> None:
        e = InsufficientPositionError("not enough shares", code="POS_ERR")
        assert e.code == "POS_ERR"
        assert isinstance(e, GatewayError)

    def test_not_connected_error(self) -> None:
        e = GatewayNotConnectedError("disconnected")
        assert isinstance(e, GatewayError)
