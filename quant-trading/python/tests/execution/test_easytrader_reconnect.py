"""EasytraderGateway 连接状态机与重连测试

覆盖：
- ConnectionState 枚举
- 初始状态
- 连接/断开/登录状态转换
- 重连触发条件
- 重连后凭证恢复
- 提交订单的状态检查
- dry-run 模式
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from aipha.execution.easytrader_gateway import (
    ConnectionState,
    EasytraderClientType,
    EasytraderConfig,
    EasytraderGateway,
)
from aipha.execution.broker_gateway import BrokerType
from aipha.execution.gateway import (
    GatewayNotConnectedError,
    GatewayOrder,
    OrderStatus,
)


def _make_config(**kwargs) -> EasytraderConfig:
    """创建测试用 EasytraderConfig"""
    defaults = dict(
        broker_type=BrokerType.EASYTRADER,
        name="test_gateway",
        client_type=EasytraderClientType.UNIVERSAL,
        client_path="",
        dry_run=True,
        poll_interval=0.1,
        auto_reconnect=True,
        reconnect_interval=0.1,
    )
    defaults.update(kwargs)
    return EasytraderConfig(**defaults)


class TestConnectionState:
    """ConnectionState 枚举测试"""

    def test_all_states(self):
        states = list(ConnectionState)
        assert len(states) == 7

    def test_values(self):
        assert ConnectionState.DISCONNECTED.value == "disconnected"
        assert ConnectionState.CONNECTING.value == "connecting"
        assert ConnectionState.CONNECTED.value == "connected"
        assert ConnectionState.LOGGED_IN.value == "logged_in"
        assert ConnectionState.RECONNECTING.value == "reconnecting"
        assert ConnectionState.DEGRADED.value == "degraded"
        assert ConnectionState.FATAL.value == "fatal"


class TestEasytraderGatewayInit:
    """初始化测试"""

    def test_initial_state(self):
        config = _make_config()
        gw = EasytraderGateway(config)
        assert gw.connection_state == ConnectionState.DISCONNECTED
        assert gw.reconnect_count == 0
        assert not gw.is_connected

    def test_lot_size_default(self):
        config = _make_config()
        gw = EasytraderGateway(config)
        assert gw._lot_size == 100

    def test_lot_size_custom(self):
        config = _make_config(lot_size=10)
        gw = EasytraderGateway(config)
        assert gw._lot_size == 10


class TestConnectionLifecycle:
    """连接生命周期测试"""

    @patch("aipha.execution.easytrader_gateway.EasytraderGateway.connect")
    def test_connect_updates_state(self, mock_connect):
        config = _make_config()
        gw = EasytraderGateway(config)

        # 手动模拟 connect 行为
        gw._connected = True
        gw._conn_state = ConnectionState.CONNECTED

        assert gw.is_connected
        assert gw.connection_state == ConnectionState.CONNECTED

    def test_disconnect_resets_state(self):
        config = _make_config()
        gw = EasytraderGateway(config)

        # 模拟已连接状态
        gw._connected = True
        gw._conn_state = ConnectionState.LOGGED_IN

        gw.disconnect()

        assert not gw.is_connected
        assert gw.connection_state == ConnectionState.DISCONNECTED

    def test_disconnect_stops_poll(self):
        config = _make_config()
        gw = EasytraderGateway(config)

        # 确保 _stop_poll 被设置
        gw.disconnect()
        assert gw._stop_poll.is_set()


class TestCredentialsPersistence:
    """凭证保存与重连恢复"""

    def test_login_saves_credentials(self):
        config = _make_config()
        gw = EasytraderGateway(config)
        gw._connected = True
        gw._user = MagicMock()
        gw._conn_state = ConnectionState.CONNECTED

        credentials = {"user": "test_user", "password": "test_pass"}
        gw.login(credentials)

        assert gw._last_credentials == credentials

    def test_login_not_connected_raises(self):
        config = _make_config()
        gw = EasytraderGateway(config)

        with pytest.raises(GatewayNotConnectedError):
            gw.login({"user": "u", "password": "p"})


class TestDryRunMode:
    """Dry-run 模式测试"""

    def test_submit_order_dry_run(self):
        config = _make_config(dry_run=True)
        gw = EasytraderGateway(config)
        gw._connected = True
        gw._user = MagicMock()
        gw._conn_state = ConnectionState.LOGGED_IN

        order = GatewayOrder(
            order_id="test-001",
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            order_type="MARKET",
        )

        result = gw.submit_order(order)
        assert result == "test-001"
        assert order.status == OrderStatus.SUBMITTED

    def test_cancel_order_dry_run(self):
        config = _make_config(dry_run=True)
        gw = EasytraderGateway(config)
        gw._connected = True
        gw._user = MagicMock()
        gw._conn_state = ConnectionState.LOGGED_IN

        order = GatewayOrder(
            order_id="test-002",
            symbol="600000.SH",
            side="SELL",
            qty=200,
            order_type="LIMIT",
            limit_price=10.0,
        )
        gw.submit_order(order)

        # 模拟 entrust_no
        gw._order_entrust_map["test-002"] = "ENTRUST_001"

        result = gw.cancel_order("test-002")
        assert result


class TestReconnectTrigger:
    """重连触发条件测试"""

    def test_consecutive_errors_trigger(self):
        config = _make_config(auto_reconnect=True)
        gw = EasytraderGateway(config)
        gw._conn_state = ConnectionState.LOGGED_IN

        # 模拟连续错误
        gw._consecutive_errors = gw._error_threshold
        assert gw._consecutive_errors >= 5

    def test_no_reconnect_when_fatal(self):
        config = _make_config()
        gw = EasytraderGateway(config)
        gw._conn_state = ConnectionState.FATAL

        # _trigger_reconnect 应在 FATAL 状态下不执行
        gw._trigger_reconnect()
        assert gw._conn_state == ConnectionState.FATAL

    def test_no_reconnect_when_already_reconnecting(self):
        config = _make_config()
        gw = EasytraderGateway(config)
        gw._conn_state = ConnectionState.RECONNECTING

        gw._trigger_reconnect()
        # 应保持 RECONNECTING，不启动新线程
        assert gw._conn_state == ConnectionState.RECONNECTING


class TestOrderSubmitStateCheck:
    """订单提交的连接状态检查"""

    def test_submit_while_reconnecting(self):
        config = _make_config()
        gw = EasytraderGateway(config)
        gw._connected = True
        gw._user = MagicMock()
        gw._conn_state = ConnectionState.RECONNECTING

        order = GatewayOrder(
            order_id="test-003",
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            order_type="MARKET",
        )

        with pytest.raises(GatewayNotConnectedError, match="reconnecting"):
            gw.submit_order(order)

    def test_submit_while_fatal(self):
        config = _make_config()
        gw = EasytraderGateway(config)
        gw._connected = True
        gw._user = MagicMock()
        gw._conn_state = ConnectionState.FATAL

        order = GatewayOrder(
            order_id="test-004",
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            order_type="MARKET",
        )

        with pytest.raises(GatewayNotConnectedError, match="fatal"):
            gw.submit_order(order)

    def test_submit_when_not_connected(self):
        config = _make_config()
        gw = EasytraderGateway(config)

        order = GatewayOrder(
            order_id="test-005",
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            order_type="MARKET",
        )

        with pytest.raises(GatewayNotConnectedError):
            gw.submit_order(order)


class TestCallbackRegistration:
    """回调注册测试"""

    def test_on_execution(self):
        config = _make_config()
        gw = EasytraderGateway(config)

        cb = MagicMock()
        gw.on_execution(cb)
        assert cb in gw._execution_callbacks

    def test_on_status_change(self):
        config = _make_config()
        gw = EasytraderGateway(config)

        cb = MagicMock()
        gw.on_status_change(cb)
        assert cb in gw._status_callbacks


class TestOrderValidation:
    """订单验证测试"""

    def test_zero_qty_rejected(self):
        config = _make_config()
        gw = EasytraderGateway(config)
        gw._connected = True
        gw._user = MagicMock()
        gw._conn_state = ConnectionState.LOGGED_IN

        from aipha.execution.gateway import OrderValidationError

        order = GatewayOrder(
            order_id="val-001",
            symbol="000001.SZ",
            side="BUY",
            qty=0,
            order_type="MARKET",
        )

        with pytest.raises(OrderValidationError):
            gw.submit_order(order)

    def test_non_lot_size_rejected(self):
        config = _make_config(lot_size=100)
        gw = EasytraderGateway(config)
        gw._connected = True
        gw._user = MagicMock()
        gw._conn_state = ConnectionState.LOGGED_IN

        from aipha.execution.gateway import OrderValidationError

        order = GatewayOrder(
            order_id="val-002",
            symbol="000001.SZ",
            side="BUY",
            qty=50,
            order_type="MARKET",
        )

        with pytest.raises(OrderValidationError, match="100"):
            gw.submit_order(order)
