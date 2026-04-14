"""Tests for execution/broker_gateway.py — 券商网关工厂。

覆盖：BrokerType, BrokerConfig, BrokerGateway (ABC),
BrokerGatewayNotImplemented, register_broker, create_broker_gateway,
list_available_brokers, list_planned_brokers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import pytest

from aipha.execution.broker_gateway import (
    BrokerConfig,
    BrokerGateway,
    BrokerGatewayNotImplemented,
    BrokerType,
    _BROKER_REGISTRY,
    create_broker_gateway,
    list_available_brokers,
    list_planned_brokers,
    register_broker,
)
from aipha.execution.gateway import (
    AccountSnapshot,
    GatewayOrder,
    OrderStatus,
    TradingMode,
)


# =========================================================================
# BrokerType
# =========================================================================

class TestBrokerType:
    def test_all_known_types(self) -> None:
        names = {bt.name for bt in BrokerType}
        assert "CTP" in names
        assert "EASYTRADER" in names
        assert "SIMULATED" in names

    def test_count_at_least_9(self) -> None:
        assert len(BrokerType) >= 9


# =========================================================================
# BrokerConfig
# =========================================================================

class TestBrokerConfig:
    def test_defaults(self) -> None:
        cfg = BrokerConfig(broker_type=BrokerType.SIMULATED, name="test")
        assert cfg.is_sandbox is True
        assert cfg.host == ""
        assert cfg.port == 0
        assert cfg.account_id == ""
        assert cfg.extra == {}

    def test_custom_fields(self) -> None:
        cfg = BrokerConfig(
            broker_type=BrokerType.XTP,
            name="xtp_prod",
            is_sandbox=False,
            host="10.0.0.1",
            port=6001,
            account_id="ACC123",
            extra={"timeout": 30},
        )
        assert cfg.broker_type == BrokerType.XTP
        assert not cfg.is_sandbox
        assert cfg.extra["timeout"] == 30


# =========================================================================
# BrokerGatewayNotImplemented
# =========================================================================

class TestBrokerGatewayNotImplemented:
    def _make(self, bt: BrokerType = BrokerType.CTP) -> BrokerGatewayNotImplemented:
        return BrokerGatewayNotImplemented(
            BrokerConfig(broker_type=bt, name="stub"),
        )

    def test_connect_raises(self) -> None:
        gw = self._make()
        with pytest.raises(NotImplementedError, match="CTP"):
            gw.connect()

    def test_disconnect_raises(self) -> None:
        gw = self._make()
        with pytest.raises(NotImplementedError, match="CTP"):
            gw.disconnect()

    def test_login_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            self._make().login({})

    def test_query_account_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            self._make().query_account()

    def test_query_positions_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            self._make().query_positions()

    def test_query_orders_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            self._make().query_orders()

    def test_query_trades_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            self._make().query_trades()

    def test_submit_order_raises(self) -> None:
        order = GatewayOrder(
            order_id="O1", symbol="X", side="BUY", qty=100, order_type="MARKET",
        )
        with pytest.raises(NotImplementedError):
            self._make().submit_order(order)

    def test_cancel_order_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            self._make().cancel_order("O1")

    def test_get_order_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            self._make().get_order("O1")

    def test_get_account_snapshot_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            self._make().get_account_snapshot()

    def test_is_connected_false(self) -> None:
        assert not self._make().is_connected

    def test_config_property(self) -> None:
        gw = self._make(BrokerType.VNPY)
        assert gw.config.broker_type == BrokerType.VNPY

    def test_mode_raises(self) -> None:
        with pytest.raises(NotImplementedError, match="CTP"):
            _ = self._make().mode

    def test_get_active_orders_raises(self) -> None:
        with pytest.raises(NotImplementedError, match="CTP"):
            self._make().get_active_orders()


# =========================================================================
# Factory: create_broker_gateway / register_broker
# =========================================================================

class TestBrokerFactory:
    def setup_method(self) -> None:
        """Clean registry before each test."""
        self._saved = dict(_BROKER_REGISTRY)

    def teardown_method(self) -> None:
        _BROKER_REGISTRY.clear()
        _BROKER_REGISTRY.update(self._saved)

    def test_unregistered_returns_not_implemented(self) -> None:
        # SIMULATED is unlikely to be registered in production
        cfg = BrokerConfig(broker_type=BrokerType.SIMULATED, name="sim")
        gw = create_broker_gateway(cfg)
        assert isinstance(gw, BrokerGatewayNotImplemented)

    def test_register_and_create(self) -> None:
        """注册一个具体实现后可以正确创建。"""

        @register_broker(BrokerType.SIMULATED)
        class _SimGateway(BrokerGateway):
            @property
            def mode(self) -> TradingMode:
                return TradingMode.PAPER

            def connect(self) -> None:
                self._connected = True

            def disconnect(self) -> None:
                self._connected = False

            def login(self, credentials: dict[str, str]) -> bool:
                return True

            def query_account(self) -> dict[str, Any]:
                return {}

            def query_positions(self) -> list[dict[str, Any]]:
                return []

            def query_orders(self) -> list[dict[str, Any]]:
                return []

            def query_trades(self) -> list[dict[str, Any]]:
                return []

            def submit_order(self, order: Any) -> str:
                return order.order_id

            def cancel_order(self, order_id: str) -> bool:
                return True

            def get_order(self, order_id: str) -> GatewayOrder | None:
                return None

            def get_active_orders(self) -> list[GatewayOrder]:
                return []

            def get_account_snapshot(self) -> AccountSnapshot:
                return AccountSnapshot(
                    timestamp=datetime.now(tz=timezone.utc),
                    cash=Decimal("0"),
                    frozen_cash=Decimal("0"),
                    total_equity=Decimal("0"),
                    positions={},
                )

        cfg = BrokerConfig(broker_type=BrokerType.SIMULATED, name="sim_test")
        gw = create_broker_gateway(cfg)
        assert isinstance(gw, _SimGateway)
        assert BrokerType.SIMULATED in list_available_brokers()


# =========================================================================
# list_planned_brokers
# =========================================================================

class TestListFunctions:
    def test_planned_includes_all(self) -> None:
        planned = list_planned_brokers()
        assert set(planned) == set(BrokerType)

    def test_available_subset_of_planned(self) -> None:
        available = set(list_available_brokers())
        planned = set(list_planned_brokers())
        assert available <= planned
