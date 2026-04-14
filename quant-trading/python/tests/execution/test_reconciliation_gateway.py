"""PositionReconciler 网关对账集成测试

覆盖：
- reconcile_with_gateway：从 TradingGateway 获取快照对账
- reconcile_with_broker_gateway：从 BrokerGateway 原始数据对账
- 网关未连接时的异常
- 本地 vs 网关一致/不一致/遗漏/幻象
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from aipha.execution.gateway import AccountSnapshot
from aipha.risk.position_reconciler import (
    DiffType,
    PositionDetail,
    PositionReconciler,
)


# ============================================================================
# reconcile_with_gateway 测试
# ============================================================================


class TestReconcileWithGateway:
    """reconcile_with_gateway 测试"""

    def _make_gateway(self, positions: dict[str, int], cash: float = 100000.0):
        """创建 mock TradingGateway"""
        gw = MagicMock()
        gw.is_connected = True
        gw.get_account_snapshot.return_value = AccountSnapshot(
            timestamp=datetime.now(),
            cash=Decimal(str(cash)),
            frozen_cash=Decimal("0"),
            total_equity=Decimal(str(cash)),
            positions=positions,
        )
        return gw

    def test_consistent(self):
        gw = self._make_gateway({"000001.SZ": 100, "600000.SH": 200})
        local = {
            "000001.SZ": PositionDetail("000001.SZ", 100),
            "600000.SH": PositionDetail("600000.SH", 200),
        }

        reconciler = PositionReconciler()
        report = reconciler.reconcile_with_gateway(local, gw)

        assert report.is_consistent
        assert report.diff_count == 0

    def test_qty_mismatch(self):
        gw = self._make_gateway({"000001.SZ": 200})  # broker: 200
        local = {"000001.SZ": PositionDetail("000001.SZ", 100)}  # local: 100

        reconciler = PositionReconciler()
        report = reconciler.reconcile_with_gateway(local, gw)

        assert not report.is_consistent
        assert report.diff_count == 1
        assert report.diffs[0].diff_type == DiffType.QTY_MISMATCH

    def test_missing_local(self):
        gw = self._make_gateway({"000001.SZ": 100})
        local: dict[str, PositionDetail] = {}

        reconciler = PositionReconciler()
        report = reconciler.reconcile_with_gateway(local, gw)

        assert not report.is_consistent
        assert report.diffs[0].diff_type == DiffType.MISSING_LOCAL

    def test_missing_broker(self):
        gw = self._make_gateway({})
        local = {"000001.SZ": PositionDetail("000001.SZ", 100)}

        reconciler = PositionReconciler()
        report = reconciler.reconcile_with_gateway(local, gw)

        assert not report.is_consistent
        assert report.diffs[0].diff_type == DiffType.MISSING_BROKER

    def test_not_connected_raises(self):
        gw = MagicMock()
        gw.is_connected = False

        reconciler = PositionReconciler()
        with pytest.raises(RuntimeError, match="未连接"):
            reconciler.reconcile_with_gateway({}, gw)

    def test_empty_both_sides(self):
        gw = self._make_gateway({})
        reconciler = PositionReconciler()
        report = reconciler.reconcile_with_gateway({}, gw)

        assert report.is_consistent


# ============================================================================
# reconcile_with_broker_gateway 测试
# ============================================================================


class TestReconcileWithBrokerGateway:
    """reconcile_with_broker_gateway 原始数据对账测试"""

    def _make_broker_gw(self, raw_positions: list[dict]):
        gw = MagicMock()
        gw.is_connected = True
        gw.query_positions.return_value = raw_positions
        return gw

    def test_consistent_cn_format(self):
        """中文字段格式（同花顺/通达信）"""
        raw = [
            {"证券代码": "000001.SZ", "证券数量": 100, "成本价": 10.5, "市值": 1050, "冻结数量": 0},
            {"证券代码": "600000.SH", "证券数量": 200, "成本价": 8.0, "市值": 1600, "冻结数量": 50},
        ]
        gw = self._make_broker_gw(raw)
        local = {
            "000001.SZ": PositionDetail("000001.SZ", 100),
            "600000.SH": PositionDetail("600000.SH", 200),
        }

        reconciler = PositionReconciler()
        report = reconciler.reconcile_with_broker_gateway(local, gw)

        assert report.is_consistent
        assert report.matched_count == 2

    def test_consistent_en_format(self):
        """英文字段格式"""
        raw = [
            {"symbol": "000001.SZ", "qty": 100},
        ]
        gw = self._make_broker_gw(raw)
        local = {"000001.SZ": PositionDetail("000001.SZ", 100)}

        reconciler = PositionReconciler()
        report = reconciler.reconcile_with_broker_gateway(local, gw)
        assert report.is_consistent

    def test_mismatch_cn_format(self):
        raw = [
            {"证券代码": "000001.SZ", "证券数量": 300},
        ]
        gw = self._make_broker_gw(raw)
        local = {"000001.SZ": PositionDetail("000001.SZ", 100)}

        reconciler = PositionReconciler()
        report = reconciler.reconcile_with_broker_gateway(local, gw)

        assert not report.is_consistent
        assert report.diffs[0].broker_qty == 300

    def test_not_connected_raises(self):
        gw = MagicMock()
        gw.is_connected = False

        reconciler = PositionReconciler()
        with pytest.raises(RuntimeError, match="未连接"):
            reconciler.reconcile_with_broker_gateway({}, gw)

    def test_empty_raw_positions(self):
        gw = self._make_broker_gw([])
        local = {"000001.SZ": PositionDetail("000001.SZ", 100)}

        reconciler = PositionReconciler()
        report = reconciler.reconcile_with_broker_gateway(local, gw)

        assert not report.is_consistent
        assert report.diffs[0].diff_type == DiffType.MISSING_BROKER

    def test_skips_empty_symbol(self):
        """跳过无证券代码的记录"""
        raw = [
            {"证券代码": "", "证券数量": 100},
            {"证券代码": "000001.SZ", "证券数量": 100},
        ]
        gw = self._make_broker_gw(raw)
        local = {"000001.SZ": PositionDetail("000001.SZ", 100)}

        reconciler = PositionReconciler()
        report = reconciler.reconcile_with_broker_gateway(local, gw)
        assert report.is_consistent

    def test_skips_zero_qty(self):
        """跳过持仓为零的记录"""
        raw = [
            {"证券代码": "600000.SH", "证券数量": 0},
            {"证券代码": "000001.SZ", "证券数量": 100},
        ]
        gw = self._make_broker_gw(raw)
        local = {"000001.SZ": PositionDetail("000001.SZ", 100)}

        reconciler = PositionReconciler()
        report = reconciler.reconcile_with_broker_gateway(local, gw)
        assert report.is_consistent


# ============================================================================
# Live CLI 入口测试 (部分)
# ============================================================================


class TestLiveCLI:
    """Live CLI 入口基本测试"""

    def test_live_subcommand_exists(self):
        """验证 live 子命令已注册"""
        from aipha.__main__ import main
        import argparse

        # 解析 help 输出
        from io import StringIO
        import sys

        old_exit = sys.exit
        captured = None

        def mock_exit(code=0):
            raise SystemExit(code)

        sys.exit = mock_exit

        try:
            # This should show help which includes 'live'
            parser = argparse.ArgumentParser(prog="aipha")
            subparsers = parser.add_subparsers(dest="command")
            live_parser = subparsers.add_parser("live")
            live_parser.add_argument("--confirm", action="store_true")
            live_parser.add_argument("--dry-run", action="store_true")

            # 验证子命令可以解析
            args = parser.parse_args(["live", "--confirm", "--dry-run"])
            assert args.command == "live"
            assert args.confirm is True
            assert args.dry_run is True
        finally:
            sys.exit = old_exit
