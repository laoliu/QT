"""StateManager Pending Orders 持久化测试

覆盖：
- PendingOrderState 数据结构
- AccountState 包含 pending_orders
- StateSnapshot to_dict / from_dict 往返
- save_from_gateway 提取活跃订单
- restore_to_gateway 恢复 pending orders
- 向后兼容（旧格式无 pending_orders 字段）
"""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import pytest

from aipha.runner.state_manager import (
    AccountState,
    PendingOrderState,
    PositionState,
    StateManager,
    StateSnapshot,
)


# ============================================================================
# PendingOrderState 测试
# ============================================================================


class TestPendingOrderState:
    """PendingOrderState 数据结构测试"""

    def test_creation(self):
        po = PendingOrderState(
            order_id="o-001",
            symbol="000001.SZ",
            side="BUY",
            qty=100,
            order_type="MARKET",
        )
        assert po.order_id == "o-001"
        assert po.filled_qty == 0
        assert po.status == "submitted"
        assert po.entrust_no is None

    def test_all_fields(self):
        po = PendingOrderState(
            order_id="o-002",
            symbol="600000.SH",
            side="SELL",
            qty=200,
            order_type="LIMIT",
            limit_price=15.5,
            filled_qty=100,
            status="partial_filled",
            created_at="2024-01-15T10:30:00",
            entrust_no="E12345",
        )
        assert po.limit_price == 15.5
        assert po.filled_qty == 100
        assert po.entrust_no == "E12345"


# ============================================================================
# AccountState with pending_orders
# ============================================================================


class TestAccountStateWithOrders:
    """AccountState 含 pending_orders 测试"""

    def test_default_empty_orders(self):
        acct = AccountState(cash=1000.0, frozen_cash=0.0)
        assert acct.pending_orders == []

    def test_with_orders(self):
        orders = [
            PendingOrderState("o1", "000001.SZ", "BUY", 100, "MARKET"),
            PendingOrderState("o2", "600000.SH", "SELL", 200, "LIMIT", limit_price=10.0),
        ]
        acct = AccountState(cash=500.0, frozen_cash=100.0, pending_orders=orders)
        assert len(acct.pending_orders) == 2


# ============================================================================
# StateSnapshot 序列化/反序列化
# ============================================================================


class TestStateSnapshotSerialization:
    """StateSnapshot to_dict / from_dict 测试"""

    def test_roundtrip_with_orders(self):
        acct = AccountState(
            cash=100000.0,
            frozen_cash=5000.0,
            positions=[PositionState("000001.SZ", 100, 10.5)],
            peak_nav=105000.0,
            current_trade_date="2024-06-15",
            pending_orders=[
                PendingOrderState(
                    order_id="po-001",
                    symbol="600000.SH",
                    side="BUY",
                    qty=300,
                    order_type="LIMIT",
                    limit_price=8.5,
                    filled_qty=100,
                    status="partial_filled",
                    created_at="2024-06-15T09:30:00",
                    entrust_no="E_999",
                ),
            ],
        )
        snap = StateSnapshot(account=acct, meta={"test": True})

        d = snap.to_dict()
        restored = StateSnapshot.from_dict(d)

        assert restored.account.cash == 100000.0
        assert len(restored.account.pending_orders) == 1
        po = restored.account.pending_orders[0]
        assert po.order_id == "po-001"
        assert po.limit_price == 8.5
        assert po.filled_qty == 100
        assert po.entrust_no == "E_999"
        assert restored.meta["test"] is True

    def test_roundtrip_no_orders(self):
        acct = AccountState(cash=50000.0, frozen_cash=0.0)
        snap = StateSnapshot(account=acct)

        d = snap.to_dict()
        restored = StateSnapshot.from_dict(d)

        assert restored.account.cash == 50000.0
        assert len(restored.account.pending_orders) == 0

    def test_backward_compat_no_pending_orders_key(self):
        """向后兼容：旧格式没有 pending_orders 字段"""
        old_format = {
            "account": {
                "cash": 80000.0,
                "frozen_cash": 1000.0,
                "positions": [
                    {"symbol": "000001.SZ", "qty": 100, "avg_cost": 10.0}
                ],
                "peak_nav": 81000.0,
                # 没有 pending_orders 字段
            },
            "meta": {},
        }
        snap = StateSnapshot.from_dict(old_format)
        assert snap.account.cash == 80000.0
        assert len(snap.account.pending_orders) == 0

    def test_json_serializable(self):
        """确保可以 JSON 序列化"""
        acct = AccountState(
            cash=10000.0,
            frozen_cash=0.0,
            pending_orders=[
                PendingOrderState("x", "000001.SZ", "BUY", 100, "MARKET")
            ],
        )
        snap = StateSnapshot(account=acct)
        d = snap.to_dict()

        # 应不抛异常
        json_str = json.dumps(d, ensure_ascii=False, indent=2)
        assert "pending_orders" in json_str
        assert "x" in json_str


# ============================================================================
# StateManager save / load with pending orders
# ============================================================================


class TestStateManagerPendingOrders:
    """StateManager 保存/加载含 pending orders"""

    def test_save_and_load(self, tmp_path):
        manager = StateManager(state_dir=tmp_path)

        acct = AccountState(
            cash=200000.0,
            frozen_cash=10000.0,
            positions=[PositionState("000001.SZ", 500, 12.0)],
            pending_orders=[
                PendingOrderState(
                    "save-001", "600000.SH", "BUY", 200, "LIMIT",
                    limit_price=9.0, entrust_no="E_SAVE",
                ),
            ],
        )
        snap = StateSnapshot(
            account=acct,
            meta={"saved_at": "2024-01-01"},
        )

        saved = manager._save(snap)
        assert saved

        loaded = manager.load()
        assert loaded is not None
        assert len(loaded.account.pending_orders) == 1
        assert loaded.account.pending_orders[0].order_id == "save-001"
        assert loaded.account.pending_orders[0].entrust_no == "E_SAVE"


# ============================================================================
# save_from_gateway with active orders
# ============================================================================


class TestSaveFromGatewayWithOrders:
    """save_from_gateway 提取活跃订单"""

    def _make_gateway_mock(self, active_orders=None):
        """创建 mock gateway"""
        gw = MagicMock()
        gw._positions = {
            "000001.SZ": {"qty": 100, "avg_cost": Decimal("10.5"), "today_bought": 0, "frozen": 0}
        }
        gw._cash = Decimal("500000")
        gw._frozen_cash = Decimal("10000")
        gw._current_trade_date = date(2024, 6, 15)

        if active_orders is None:
            gw.get_active_orders.return_value = []
        else:
            gw.get_active_orders.return_value = active_orders

        return gw

    def test_saves_active_orders(self, tmp_path):
        from aipha.execution.gateway import GatewayOrder, OrderStatus

        active_order = GatewayOrder(
            order_id="act-001",
            symbol="600000.SH",
            side="BUY",
            qty=300,
            order_type="LIMIT",
            limit_price=10.0,
            status=OrderStatus.SUBMITTED,
            created_at=datetime(2024, 6, 15, 9, 35),
            metadata={"entrust_no": "E_ACT"},
        )

        gw = self._make_gateway_mock(active_orders=[active_order])
        manager = StateManager(state_dir=tmp_path)

        result = manager.save_from_gateway(gw, strategy_name="test")
        assert result

        loaded = manager.load()
        assert loaded is not None
        assert len(loaded.account.pending_orders) == 1
        po = loaded.account.pending_orders[0]
        assert po.order_id == "act-001"
        assert po.entrust_no == "E_ACT"

    def test_saves_empty_when_no_active_orders(self, tmp_path):
        gw = self._make_gateway_mock(active_orders=[])
        manager = StateManager(state_dir=tmp_path)

        manager.save_from_gateway(gw, strategy_name="test")

        loaded = manager.load()
        assert loaded is not None
        assert len(loaded.account.pending_orders) == 0

    def test_handles_gateway_without_active_orders(self, tmp_path):
        """get_active_orders 抛异常时不影响其他状态保存"""
        gw = self._make_gateway_mock()
        gw.get_active_orders.side_effect = AttributeError("no method")

        manager = StateManager(state_dir=tmp_path)
        result = manager.save_from_gateway(gw, strategy_name="test")
        assert result

        loaded = manager.load()
        assert loaded is not None
        assert loaded.account.cash == 500000.0
        assert len(loaded.account.pending_orders) == 0


# ============================================================================
# restore_to_gateway with pending orders
# ============================================================================


class TestRestoreToGatewayWithOrders:
    """restore_to_gateway 恢复 pending orders"""

    def test_restores_orders_to_gateway(self, tmp_path):
        acct = AccountState(
            cash=100000.0,
            frozen_cash=5000.0,
            positions=[],
            current_trade_date="2024-06-15",
            pending_orders=[
                PendingOrderState(
                    order_id="r-001",
                    symbol="000001.SZ",
                    side="BUY",
                    qty=100,
                    order_type="MARKET",
                    status="submitted",
                    entrust_no="E_R001",
                ),
            ],
        )
        snap = StateSnapshot(account=acct)

        # Mock gateway with _orders and _order_entrust_map
        gw = MagicMock()
        gw._cash = Decimal("0")
        gw._frozen_cash = Decimal("0")
        gw._positions = {}
        gw._orders = {}
        gw._order_entrust_map = {}
        gw._current_trade_date = None

        manager = StateManager(state_dir=tmp_path)
        manager.restore_to_gateway(gw, snap)

        assert "r-001" in gw._orders
        order = gw._orders["r-001"]
        assert order.symbol == "000001.SZ"
        assert order.side == "BUY"
        assert gw._order_entrust_map["r-001"] == "E_R001"

    def test_restores_without_orders_attr(self, tmp_path):
        """Gateway 没有 _orders 属性时不崩溃"""
        acct = AccountState(
            cash=50000.0,
            frozen_cash=0.0,
            pending_orders=[
                PendingOrderState("x", "SZ", "BUY", 100, "MARKET"),
            ],
        )
        snap = StateSnapshot(account=acct)

        class SimpleGateway:
            pass

        gw = SimpleGateway()
        gw._cash = Decimal("0")
        gw._frozen_cash = Decimal("0")
        gw._positions = {}
        gw._current_trade_date = None
        # 没有 _orders 属性

        manager = StateManager(state_dir=tmp_path)
        # 应不崩溃
        manager.restore_to_gateway(gw, snap)
        assert not hasattr(gw, '_orders')
