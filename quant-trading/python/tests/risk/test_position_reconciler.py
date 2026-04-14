"""持仓对账测试 — Sprint 2.3

测试覆盖：
- 完全一致的对账
- 本地缺失持仓
- 券商缺失持仓
- 数量偏差
- 容差配置
- PaperGateway.get_position_details 集成
"""

import pytest
from datetime import datetime

from aipha.risk.position_reconciler import (
    DiffType,
    PositionDetail,
    PositionDiff,
    PositionReconciler,
    ReconciliationReport,
)


class TestPositionReconciler:
    """对账器核心测试"""

    def test_fully_consistent(self):
        """完全一致时 is_consistent=True"""
        reconciler = PositionReconciler()
        local = {
            "000001.SZ": PositionDetail("000001.SZ", qty=100),
            "600000.SH": PositionDetail("600000.SH", qty=200),
        }
        broker = {
            "000001.SZ": PositionDetail("000001.SZ", qty=100),
            "600000.SH": PositionDetail("600000.SH", qty=200),
        }

        report = reconciler.reconcile(local, broker)

        assert report.is_consistent
        assert report.matched_count == 2
        assert report.diff_count == 0
        assert len(report.diffs) == 0

    def test_missing_local(self):
        """券商有但本地没有"""
        reconciler = PositionReconciler()
        local = {}
        broker = {"000001.SZ": PositionDetail("000001.SZ", qty=100)}

        report = reconciler.reconcile(local, broker)

        assert not report.is_consistent
        assert report.diff_count == 1
        assert report.diffs[0].diff_type == DiffType.MISSING_LOCAL
        assert report.diffs[0].broker_qty == 100
        assert report.diffs[0].local_qty == 0

    def test_missing_broker(self):
        """本地有但券商没有"""
        reconciler = PositionReconciler()
        local = {"000001.SZ": PositionDetail("000001.SZ", qty=100)}
        broker = {}

        report = reconciler.reconcile(local, broker)

        assert not report.is_consistent
        assert report.diff_count == 1
        assert report.diffs[0].diff_type == DiffType.MISSING_BROKER
        assert report.diffs[0].local_qty == 100
        assert report.diffs[0].broker_qty == 0

    def test_qty_mismatch(self):
        """双方数量不一致"""
        reconciler = PositionReconciler()
        local = {"000001.SZ": PositionDetail("000001.SZ", qty=100)}
        broker = {"000001.SZ": PositionDetail("000001.SZ", qty=150)}

        report = reconciler.reconcile(local, broker)

        assert not report.is_consistent
        assert report.diffs[0].diff_type == DiffType.QTY_MISMATCH
        assert report.diffs[0].qty_delta == 50  # broker - local

    def test_qty_tolerance(self):
        """容差范围内视为一致"""
        reconciler = PositionReconciler(qty_tolerance=10)
        local = {"000001.SZ": PositionDetail("000001.SZ", qty=100)}
        broker = {"000001.SZ": PositionDetail("000001.SZ", qty=105)}

        report = reconciler.reconcile(local, broker)

        # 偏差=5 <= 容差=10，应一致
        assert report.is_consistent

    def test_qty_tolerance_exceeded(self):
        """超出容差视为不一致"""
        reconciler = PositionReconciler(qty_tolerance=10)
        local = {"000001.SZ": PositionDetail("000001.SZ", qty=100)}
        broker = {"000001.SZ": PositionDetail("000001.SZ", qty=120)}

        report = reconciler.reconcile(local, broker)

        # 偏差=20 > 容差=10，应不一致
        assert not report.is_consistent

    def test_multi_symbol_mixed(self):
        """多标的混合情况"""
        reconciler = PositionReconciler()
        local = {
            "000001.SZ": PositionDetail("000001.SZ", qty=100),
            "600000.SH": PositionDetail("600000.SH", qty=200),
            "000002.SZ": PositionDetail("000002.SZ", qty=300),
        }
        broker = {
            "000001.SZ": PositionDetail("000001.SZ", qty=100),  # 一致
            "600000.SH": PositionDetail("600000.SH", qty=250),  # 数量不一致
            # 000002.SZ 缺失
            "300001.SZ": PositionDetail("300001.SZ", qty=50),  # 本地缺失
        }

        report = reconciler.reconcile(local, broker)

        assert not report.is_consistent
        assert report.matched_count == 1
        assert report.diff_count == 3

    def test_empty_positions(self):
        """双方都无持仓"""
        reconciler = PositionReconciler()
        report = reconciler.reconcile({}, {})

        assert report.is_consistent
        assert report.matched_count == 0

    def test_report_summary(self):
        """摘要信息"""
        reconciler = PositionReconciler()
        local = {"000001.SZ": PositionDetail("000001.SZ", qty=100)}
        broker = {"000001.SZ": PositionDetail("000001.SZ", qty=200)}

        report = reconciler.reconcile(local, broker)

        assert "失败" in report.summary
        assert "差异" in report.summary


class TestPaperGatewayPositionDetails:
    """PaperGateway.get_position_details 集成测试"""

    def test_get_position_details_empty(self):
        """空仓时返回空"""
        from decimal import Decimal
        from aipha.execution.paper_gateway import PaperTradingGateway

        gw = PaperTradingGateway(initial_capital=Decimal("100000"))
        gw.connect()

        details = gw.get_position_details()
        assert len(details) == 0

    def test_get_position_details_with_holdings(self):
        """有持仓时返回正确的 PositionDetail"""
        from decimal import Decimal
        from datetime import datetime
        from aipha.domain.models import Bar
        from aipha.execution.paper_gateway import PaperTradingGateway
        from aipha.execution.gateway import GatewayOrder

        gw = PaperTradingGateway(
            initial_capital=Decimal("1000000"),
            commission_rate=Decimal("0.0001"),
            commission_min=Decimal("0"),
            stamp_tax_rate=Decimal("0"),
        )
        gw.connect()

        bar = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0, high=11.0, low=9.5, close=10.5,
            volume=1000000.0,
        )
        order = GatewayOrder(
            order_id="T1", symbol="000001.SZ", side="BUY",
            qty=100, order_type="MARKET",
        )
        gw.submit_order(order)
        gw.process_bar(bar)

        details = gw.get_position_details()
        assert "000001.SZ" in details
        detail = details["000001.SZ"]
        assert detail.qty == 100
        assert detail.avg_cost > 0
        assert detail.market_value > 0  # qty * latest_price

    def test_reconcile_paper_vs_paper(self):
        """Paper Gateway 与自身对账应完全一致"""
        from decimal import Decimal
        from datetime import datetime
        from aipha.domain.models import Bar
        from aipha.execution.paper_gateway import PaperTradingGateway
        from aipha.execution.gateway import GatewayOrder

        gw = PaperTradingGateway(
            initial_capital=Decimal("1000000"),
            commission_rate=Decimal("0.0001"),
            commission_min=Decimal("0"),
            stamp_tax_rate=Decimal("0"),
        )
        gw.connect()

        bar = Bar(
            symbol="000001.SZ",
            start_ts=datetime(2024, 1, 15, 9, 30),
            end_ts=datetime(2024, 1, 15, 15, 0),
            open=10.0, high=11.0, low=9.5, close=10.5,
            volume=1000000.0,
        )
        order = GatewayOrder(
            order_id="T1", symbol="000001.SZ", side="BUY",
            qty=100, order_type="MARKET",
        )
        gw.submit_order(order)
        gw.process_bar(bar)

        details = gw.get_position_details()
        reconciler = PositionReconciler()
        report = reconciler.reconcile(details, details)

        assert report.is_consistent
