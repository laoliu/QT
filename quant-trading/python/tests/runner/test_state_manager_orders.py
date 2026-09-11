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


class _FakePaperGateway:
    """PaperGateway 的最小替身（§13.16）。

    刻意**不用** MagicMock：``state_manager`` 是靠 ``_is_paper_gateway``
    标记认网关的（`state_manager.py:213` / `:390`），动机就是别让裸
    MagicMock 的 auto-attribute 冒充成 PaperGateway。用 Mock 写这些测试，
    ``getattr(gw, '_is_paper_gateway', False) is True`` 会是 False，
    于是走「不支持的网关类型」分支 —— save 侧存的是**空状态**（cash=0），
    restore 侧**什么都不做**。测试看着在测持久化，其实在测降级分支。

    ``tests/runner/test_state_manager.py`` 里有一份同名替身；两个文件
    各自保留一份，是为了让单个测试文件自成一体。
    """

    _is_paper_gateway = True

    def __init__(
        self,
        cash: float = 500_000.0,
        frozen_cash: float = 10_000.0,
        positions: dict | None = None,
        active_orders: list | None = None,
        trade_date: date | None = None,
        with_orders_attr: bool = True,
        active_orders_raises: bool = False,
    ) -> None:
        self._cash = Decimal(str(cash))
        self._frozen_cash = Decimal(str(frozen_cash))
        self._positions: dict = dict(positions or {})
        self._current_trade_date = trade_date
        self._active_orders: list = list(active_orders or [])
        self._active_orders_raises = active_orders_raises
        if with_orders_attr:
            self._orders: dict = {}

    # -- §13.16 公开接口 ----------------------------------------------------
    def save_state(self) -> dict:
        """与 PaperGateway.save_state 相同的键名/口径。"""
        return {
            "cash": float(self._cash),
            "frozen_cash": float(self._frozen_cash),
            "positions": {
                symbol: {
                    "total_qty": p.get("qty", 0),
                    "avg_cost": float(p.get("avg_cost", 0)),
                    "today_bought_qty": p.get("today_bought", 0),
                    "frozen_qty": p.get("frozen", 0),
                }
                for symbol, p in self._positions.items()
            },
            "latest_prices": {},
        }

    def restore_state(self, state: dict) -> None:
        self._cash = Decimal(str(state.get("cash", 0)))
        self._frozen_cash = Decimal(str(state.get("frozen_cash", 0)))
        self._positions = {
            symbol: {
                "qty": p.get("total_qty", 0),
                "avg_cost": Decimal(str(p.get("avg_cost", 0))),
                "today_bought": p.get("today_bought_qty", 0),
                "frozen": p.get("frozen_qty", 0),
            }
            for symbol, p in state.get("positions", {}).items()
        }

    def get_active_orders(self) -> list:
        if self._active_orders_raises:
            # 模拟老网关没实现该方法
            raise AttributeError("no method")
        return list(self._active_orders)


class TestSaveFromGatewayWithOrders:
    """save_from_gateway 提取活跃订单"""

    def _make_gateway_mock(self, active_orders=None, active_orders_raises=False):
        """创建测试用 PaperGateway 替身"""
        return _FakePaperGateway(
            cash=500_000.0,
            frozen_cash=10_000.0,
            positions={
                "000001.SZ": {
                    "qty": 100,
                    "avg_cost": Decimal("10.5"),
                    "today_bought": 0,
                    "frozen": 0,
                }
            },
            active_orders=active_orders,
            trade_date=date(2024, 6, 15),
            active_orders_raises=active_orders_raises,
        )

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
        # 顺手钉住「走的确实是 PaperGateway 分支」：降级分支下 cash/持仓
        # 会是空的，只断言 pending_orders 的话这条测试用 MagicMock 也能过。
        assert loaded.account.cash == 500000.0
        assert len(loaded.account.positions) == 1

    def test_saves_empty_when_no_active_orders(self, tmp_path):
        gw = self._make_gateway_mock(active_orders=[])
        manager = StateManager(state_dir=tmp_path)

        manager.save_from_gateway(gw, strategy_name="test")

        loaded = manager.load()
        assert loaded is not None
        assert len(loaded.account.pending_orders) == 0

    def test_handles_gateway_without_active_orders(self, tmp_path):
        """get_active_orders 抛异常时不影响其他状态保存"""
        gw = self._make_gateway_mock(active_orders_raises=True)

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

        gw = _FakePaperGateway(cash=0.0, frozen_cash=0.0)

        manager = StateManager(state_dir=tmp_path)
        manager.restore_to_gateway(gw, snap)

        assert "r-001" in gw._orders
        order = gw._orders["r-001"]
        assert order.symbol == "000001.SZ"
        assert order.side == "BUY"
        # entrust_no 落在 order.metadata 里（`state_manager.py:421`），
        # 不存在单独的 _order_entrust_map —— 那个字段属于 EasyTraderGateway
        # (`easytrader_gateway.py:127`)，而 restore_to_gateway 只支持
        # PaperGateway（§13.16 明确拒绝其它网关），实盘持仓/委托是从券商侧拉的。
        assert order.metadata["entrust_no"] == "E_R001"
        # 资金也要真的恢复：走降级分支的话这里还是 0
        assert gw._cash == Decimal("100000.0")

    def test_restores_without_orders_attr(self, tmp_path):
        """Gateway 没有 _orders 属性时不崩溃（订单跳过，资金/持仓照常恢复）"""
        acct = AccountState(
            cash=50000.0,
            frozen_cash=0.0,
            pending_orders=[
                PendingOrderState("x", "SZ", "BUY", 100, "MARKET"),
            ],
        )
        snap = StateSnapshot(account=acct)

        gw = _FakePaperGateway(cash=0.0, frozen_cash=0.0, with_orders_attr=False)
        assert not hasattr(gw, "_orders")

        manager = StateManager(state_dir=tmp_path)
        # 应不崩溃
        manager.restore_to_gateway(gw, snap)

        assert not hasattr(gw, "_orders")
        # 订单恢复被跳过不应连累账户状态
        assert gw._cash == Decimal("50000.0")
