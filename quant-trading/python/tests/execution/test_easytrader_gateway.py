"""
Easytrader 网关测试

测试覆盖：
- 配置验证
- 网关创建
- Dry run 模式下单
- 订单状态查询
"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock
import uuid

import pytest

from aipha.execution.broker_gateway import BrokerType
from aipha.execution.easytrader_gateway import (
    EasytraderClientType,
    EasytraderConfig,
    EasytraderGateway,
)
from aipha.execution.gateway import GatewayOrder, OrderStatus


@pytest.fixture
def dry_run_config() -> EasytraderConfig:
    """Dry run 模式配置"""
    return EasytraderConfig(
        broker_type=BrokerType.EASYTRADER,
        name="test_gateway",
        client_type=EasytraderClientType.UNIVERSAL,
        client_path="C:\\fake\\path.exe",
        dry_run=True,
    )


@pytest.fixture
def gateway(dry_run_config: EasytraderConfig) -> EasytraderGateway:
    """创建测试网关并模拟连接状态"""
    gw = EasytraderGateway(dry_run_config)
    # 手动设置连接状态用于测试
    gw._connected = True
    gw._user = MagicMock()  # 模拟 user 对象
    return gw


def make_order(
    side: str = "BUY",
    symbol: str = "000001.SZ",
    qty: int = 100,
    price: float = 10.5,
) -> GatewayOrder:
    """创建测试订单"""
    return GatewayOrder(
        order_id=str(uuid.uuid4()),
        symbol=symbol,
        side=side,
        qty=qty,
        order_type="LIMIT",
        limit_price=price,
        created_at=datetime.now(),
    )


class TestEasytraderConfig:
    """配置测试"""

    def test_valid_config(self):
        """测试有效配置"""
        config = EasytraderConfig(
            broker_type=BrokerType.EASYTRADER,
            name="test_config",
            client_type=EasytraderClientType.TDX,
            client_path="C:\\tdx\\app.exe",
            dry_run=True,
        )

        assert config.client_type == EasytraderClientType.TDX
        assert config.dry_run is True
        assert config.broker_type == BrokerType.EASYTRADER

    def test_default_values(self):
        """测试默认值"""
        config = EasytraderConfig(
            broker_type=BrokerType.EASYTRADER,
            name="minimal",
        )

        assert config.client_type == EasytraderClientType.UNIVERSAL
        assert config.dry_run is False
        assert config.poll_interval == 1.0

    def test_client_types(self):
        """测试客户端类型枚举"""
        assert EasytraderClientType.UNIVERSAL.value == "universal"
        assert EasytraderClientType.TDX.value == "tdx"
        assert EasytraderClientType.YH.value == "yh"
        assert EasytraderClientType.GJ.value == "gj"


class TestEasytraderGateway:
    """网关测试"""

    def test_create_gateway(self, dry_run_config: EasytraderConfig):
        """测试创建网关"""
        gw = EasytraderGateway(dry_run_config)

        assert gw is not None
        assert gw._et_config.dry_run is True
        assert gw._user is None  # 未连接时为 None

    def test_disconnect(self, gateway: EasytraderGateway):
        """测试断开连接"""
        gateway.disconnect()

        assert gateway._connected is False
        assert gateway._user is None

    def test_submit_order_dry_run(self, gateway: EasytraderGateway):
        """测试 Dry run 模式下单"""
        order = make_order()

        result = gateway.submit_order(order)

        assert result == order.order_id
        assert order.status == OrderStatus.SUBMITTED
        assert order.order_id in gateway._orders

    def test_submit_order_updates_status(self, gateway: EasytraderGateway):
        """测试下单更新状态"""
        order = make_order()

        gateway.submit_order(order)

        assert order.submitted_at is not None
        assert order.status == OrderStatus.SUBMITTED

    def test_submit_order_validation_negative_qty(self, gateway: EasytraderGateway):
        """测试订单验证 - 负数量"""
        order = make_order(qty=-100)

        from aipha.execution.gateway import OrderValidationError

        with pytest.raises(OrderValidationError, match="positive"):
            gateway.submit_order(order)

    def test_submit_order_validation_non_round_lot(self, gateway: EasytraderGateway):
        """测试订单验证 - 非整手"""
        order = make_order(qty=150)

        from aipha.execution.gateway import OrderValidationError

        with pytest.raises(OrderValidationError, match="整数倍"):
            gateway.submit_order(order)

    def test_cancel_order_dry_run(self, gateway: EasytraderGateway):
        """测试 Dry run 模式撤单"""
        order = make_order()
        gateway.submit_order(order)
        gateway._order_entrust_map[order.order_id] = "FAKE_ENTRUST"

        success = gateway.cancel_order(order.order_id)

        assert success is True
        assert order.status == OrderStatus.CANCELED

    def test_cancel_nonexistent_order(self, gateway: EasytraderGateway):
        """测试撤销不存在的订单"""
        success = gateway.cancel_order("nonexistent_id")

        assert success is False

    def test_get_order(self, gateway: EasytraderGateway):
        """测试获取订单"""
        order = make_order()
        gateway.submit_order(order)

        result = gateway.get_order(order.order_id)

        assert result is order

    def test_get_active_orders(self, gateway: EasytraderGateway):
        """测试获取活跃订单"""
        order1 = make_order()
        order2 = make_order()
        gateway.submit_order(order1)
        gateway.submit_order(order2)

        active = gateway.get_active_orders()

        assert len(active) == 2


class TestAccountQueries:
    """账户查询测试"""

    def test_get_account_snapshot_not_connected(self, dry_run_config: EasytraderConfig):
        """测试未连接时获取账户快照"""
        gw = EasytraderGateway(dry_run_config)
        # 未连接

        snapshot = gw.get_account_snapshot()

        assert snapshot.cash == Decimal("0")
        assert snapshot.total_equity == Decimal("0")

    def test_get_account_snapshot_with_mock(self, gateway: EasytraderGateway):
        """测试获取账户快照（模拟数据）"""
        gateway._user.balance = {
            "可用资金": 100000,
            "冻结资金": 5000,
            "总资产": 110000,
        }
        gateway._user.position = []

        snapshot = gateway.get_account_snapshot()

        assert snapshot.cash == Decimal("100000")
        assert snapshot.frozen_cash == Decimal("5000")
        assert snapshot.total_equity == Decimal("110000")

    def test_query_account(self, gateway: EasytraderGateway):
        """测试查询账户"""
        gateway._user.balance = {"可用资金": 50000}

        result = gateway.query_account()

        assert result["可用资金"] == 50000

    def test_query_positions(self, gateway: EasytraderGateway):
        """测试查询持仓"""
        gateway._user.position = [
            {"证券代码": "000001", "证券数量": 1000},
        ]

        result = gateway.query_positions()

        assert len(result) == 1
        assert result[0]["证券代码"] == "000001"

    def test_query_orders(self, gateway: EasytraderGateway):
        """测试查询委托"""
        gateway._user.today_entrusts = []

        result = gateway.query_orders()

        assert result == []

    def test_query_trades(self, gateway: EasytraderGateway):
        """测试查询成交"""
        gateway._user.today_trades = []

        result = gateway.query_trades()

        assert result == []
