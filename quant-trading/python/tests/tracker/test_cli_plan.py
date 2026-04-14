"""Tests for tracker.cli — plan command."""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from aipha.tracker.cli import cmd_plan, _load_config
from aipha.tracker.models import (
    Confidence,
    PlanStatus,
    TradePlan,
    TradeDirection,
)
from aipha.tracker.storage import TrackerStorage


@pytest.fixture
def tmp_storage(tmp_path):
    storage = TrackerStorage(data_dir=tmp_path / "tracker")
    yield storage
    storage.close()


def _make_plan(**overrides):
    defaults = dict(
        plan_id=TradePlan.new_id(),
        symbol="600519.SH", name="贵州茅台",
        direction=TradeDirection.BUY,
        reasons=["估值合理", "行业龙头", "现金流优秀"],
        stop_loss_price=1500.0, stop_loss_pct=0.15,
        target_position_pct=0.10, confidence=Confidence.HIGH,
    )
    defaults.update(overrides)
    return TradePlan(**defaults)


class TestCmdPlanCreate:

    def test_create_plan(self, tmp_storage, tmp_path, capsys):
        args = MagicMock()
        args.plan_action = "create"
        args.symbol = "600519.SH"
        args.name = "贵州茅台"
        args.direction = "BUY"
        args.reasons = "估值合理|行业龙头|现金流优秀"
        args.stop_loss_price = 1500.0
        args.stop_loss_pct = 0.15
        args.target_pct = 0.10
        args.confidence = "高"
        args.vip_refs = "学知利行,挖地瓜"

        config = {"data_dir": str(tmp_path / "tracker")}
        asyncio.run(cmd_plan(args, config))

        out = capsys.readouterr().out
        assert "✅ 交易计划已创建" in out
        assert "BUY" in out

    def test_create_plan_less_than_3_reasons(self, tmp_path, capsys):
        args = MagicMock()
        args.plan_action = "create"
        args.symbol = "600519.SH"
        args.name = "茅台"
        args.direction = "BUY"
        args.reasons = "原因1|原因2"
        args.stop_loss_price = 1500.0
        args.stop_loss_pct = 0.15
        args.target_pct = 0.10
        args.confidence = "中"
        args.vip_refs = ""

        config = {"data_dir": str(tmp_path / "tracker")}
        asyncio.run(cmd_plan(args, config))

        out = capsys.readouterr().out
        assert "❌" in out
        assert "3 条理由" in out


class TestCmdPlanList:

    def test_list_empty(self, tmp_path, capsys):
        args = MagicMock()
        args.plan_action = "list"
        args.status = None

        config = {"data_dir": str(tmp_path / "tracker")}
        asyncio.run(cmd_plan(args, config))

        out = capsys.readouterr().out
        assert "无交易计划" in out

    def test_list_with_plans(self, tmp_path, capsys):
        # Pre-populate storage
        storage = TrackerStorage(data_dir=tmp_path / "tracker")
        plan = _make_plan()
        storage.save_trade_plan(plan)
        storage.close()

        args = MagicMock()
        args.plan_action = "list"
        args.status = None

        config = {"data_dir": str(tmp_path / "tracker")}
        asyncio.run(cmd_plan(args, config))

        out = capsys.readouterr().out
        assert "贵州茅台" in out
        assert "DRAFT" in out


class TestCmdPlanApprove:

    def test_approve_success(self, tmp_path, capsys):
        storage = TrackerStorage(data_dir=tmp_path / "tracker")
        plan = _make_plan()
        storage.save_trade_plan(plan)
        storage.close()

        args = MagicMock()
        args.plan_action = "approve"
        args.plan_id = plan.plan_id

        config = {"data_dir": str(tmp_path / "tracker")}
        asyncio.run(cmd_plan(args, config))

        out = capsys.readouterr().out
        assert "✅" in out
        assert "APPROVED" in out

    def test_approve_not_found(self, tmp_path, capsys):
        args = MagicMock()
        args.plan_action = "approve"
        args.plan_id = "TP-nonexistent"

        config = {"data_dir": str(tmp_path / "tracker")}
        asyncio.run(cmd_plan(args, config))

        out = capsys.readouterr().out
        assert "❌" in out
        assert "未找到" in out


class TestCmdPlanExecute:

    def test_execute_success(self, tmp_path, capsys):
        storage = TrackerStorage(data_dir=tmp_path / "tracker")
        plan = _make_plan()
        plan.approve()
        storage.save_trade_plan(plan)
        storage.close()

        args = MagicMock()
        args.plan_action = "execute"
        args.plan_id = plan.plan_id

        config = {"data_dir": str(tmp_path / "tracker")}
        asyncio.run(cmd_plan(args, config))

        out = capsys.readouterr().out
        assert "EXECUTED" in out

    def test_execute_non_approved_fails(self, tmp_path, capsys):
        storage = TrackerStorage(data_dir=tmp_path / "tracker")
        plan = _make_plan()  # DRAFT
        storage.save_trade_plan(plan)
        storage.close()

        args = MagicMock()
        args.plan_action = "execute"
        args.plan_id = plan.plan_id

        config = {"data_dir": str(tmp_path / "tracker")}
        asyncio.run(cmd_plan(args, config))

        out = capsys.readouterr().out
        assert "❌" in out


class TestCmdPlanCancel:

    def test_cancel_success(self, tmp_path, capsys):
        storage = TrackerStorage(data_dir=tmp_path / "tracker")
        plan = _make_plan()
        storage.save_trade_plan(plan)
        storage.close()

        args = MagicMock()
        args.plan_action = "cancel"
        args.plan_id = plan.plan_id
        args.reason = "市场环境变化"

        config = {"data_dir": str(tmp_path / "tracker")}
        asyncio.run(cmd_plan(args, config))

        out = capsys.readouterr().out
        assert "✅" in out
        assert "已取消" in out
