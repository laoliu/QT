"""Tests for tracker.models — data model validation and serialization."""

from datetime import datetime
from decimal import Decimal

import pytest

from aipha.tracker.models import (
    ChangeType,
    Confidence,
    Holding,
    HoldingChange,
    PlanStatus,
    PortfolioDiff,
    PortfolioSnapshot,
    PortfolioSource,
    TradePlan,
    TradeDirection,
    VIPAuthor,
)


# ===================================================================
# Holding
# ===================================================================


class TestHolding:
    def test_create_basic(self):
        h = Holding(symbol="600519.SH", name="贵州茅台", weight=Decimal("0.3"))
        assert h.symbol == "600519.SH"
        assert h.name == "贵州茅台"
        assert h.weight == Decimal("0.3")
        assert h.shares is None
        assert h.cost is None

    def test_create_with_optional(self):
        h = Holding(
            symbol="000001.SZ",
            name="平安银行",
            weight=Decimal("0.1"),
            shares=1000,
            cost=Decimal("12.50"),
            note="测试",
        )
        assert h.shares == 1000
        assert h.cost == Decimal("12.50")
        assert h.note == "测试"

    def test_frozen(self):
        h = Holding(symbol="600519.SH", name="茅台", weight=Decimal("0.3"))
        with pytest.raises(AttributeError):
            h.weight = Decimal("0.5")  # type: ignore[misc]

    def test_invalid_empty_symbol(self):
        with pytest.raises(ValueError, match="symbol must not be empty"):
            Holding(symbol="", name="test", weight=Decimal("0.1"))

    def test_invalid_weight_negative(self):
        with pytest.raises(ValueError, match="weight must be 0"):
            Holding(symbol="600519.SH", name="test", weight=Decimal("-0.1"))

    def test_invalid_weight_over_1(self):
        with pytest.raises(ValueError, match="weight must be 0"):
            Holding(symbol="600519.SH", name="test", weight=Decimal("1.1"))

    def test_weight_boundary_0(self):
        h = Holding(symbol="A.SH", name="a", weight=Decimal("0"))
        assert h.weight == Decimal("0")

    def test_weight_boundary_1(self):
        h = Holding(symbol="B.SH", name="b", weight=Decimal("1"))
        assert h.weight == Decimal("1")

    def test_to_dict_from_dict_roundtrip(self):
        h = Holding(
            symbol="600519.SH",
            name="贵州茅台",
            weight=Decimal("0.3"),
            shares=500,
            cost=Decimal("1800.00"),
            note="重仓",
        )
        d = h.to_dict()
        h2 = Holding.from_dict(d)
        assert h2.symbol == h.symbol
        assert h2.name == h.name
        assert h2.weight == h.weight
        assert h2.shares == h.shares
        assert h2.cost == h.cost
        assert h2.note == h.note

    def test_from_dict_missing_optional(self):
        d = {"symbol": "X.SH", "name": "X", "weight": "0.1"}
        h = Holding.from_dict(d)
        assert h.shares is None
        assert h.cost is None
        assert h.note == ""


# ===================================================================
# PortfolioSnapshot
# ===================================================================


class TestPortfolioSnapshot:
    def _make(self, **kwargs):
        defaults = dict(
            snapshot_id=PortfolioSnapshot.new_id(),
            source=PortfolioSource.XUEQIU,
            author_id="test",
            author_name="Test Author",
            snapshot_time=datetime(2025, 1, 15, 15, 0),
            fetch_time=datetime(2025, 1, 15, 15, 1),
            holdings=(
                Holding(symbol="600519.SH", name="贵州茅台", weight=Decimal("0.3")),
                Holding(symbol="000001.SZ", name="平安银行", weight=Decimal("0.2")),
            ),
        )
        defaults.update(kwargs)
        return PortfolioSnapshot(**defaults)

    def test_create(self):
        s = self._make()
        assert s.source == PortfolioSource.XUEQIU
        assert len(s.holdings) == 2

    def test_symbols(self):
        s = self._make()
        assert s.symbols == {"600519.SH", "000001.SZ"}

    def test_weight_map(self):
        s = self._make()
        assert s.weight_map["600519.SH"] == Decimal("0.3")
        assert s.weight_map["000001.SZ"] == Decimal("0.2")

    def test_new_id_unique(self):
        ids = {PortfolioSnapshot.new_id() for _ in range(100)}
        assert len(ids) == 100

    def test_roundtrip(self):
        s = self._make(portfolio_code="ZH123456")
        d = s.to_dict()
        s2 = PortfolioSnapshot.from_dict(d)
        assert s2.snapshot_id == s.snapshot_id
        assert s2.source == s.source
        assert s2.portfolio_code == "ZH123456"
        assert len(s2.holdings) == 2
        assert s2.holdings[0].symbol == "600519.SH"

    def test_invalid_empty_author(self):
        with pytest.raises(ValueError, match="author_id"):
            self._make(author_id="")

    def test_invalid_empty_snapshot_id(self):
        with pytest.raises(ValueError, match="snapshot_id"):
            self._make(snapshot_id="")


# ===================================================================
# TradePlan
# ===================================================================


class TestTradePlan:
    def _make(self, **kwargs):
        defaults = dict(
            plan_id=TradePlan.new_id(),
            symbol="600519.SH",
            name="贵州茅台",
            direction=TradeDirection.BUY,
            reasons=["估值合理", "行业龙头", "自由现金流优秀"],
            stop_loss_price=1500.0,
            stop_loss_pct=0.1,
            target_position_pct=0.2,
            confidence=Confidence.MEDIUM,
        )
        defaults.update(kwargs)
        return TradePlan(**defaults)

    def test_create(self):
        p = self._make()
        assert p.status == PlanStatus.DRAFT
        assert len(p.reasons) == 3

    def test_minimum_3_reasons_enforced(self):
        with pytest.raises(ValueError, match="at least 3 reasons"):
            self._make(reasons=["理由1", "理由2"])

    def test_zero_reasons_fails(self):
        with pytest.raises(ValueError, match="at least 3 reasons"):
            self._make(reasons=[])

    def test_invalid_stop_loss_zero(self):
        with pytest.raises(ValueError, match="stop_loss_price must be > 0"):
            self._make(stop_loss_price=0)

    def test_invalid_stop_loss_negative(self):
        with pytest.raises(ValueError, match="stop_loss_price must be > 0"):
            self._make(stop_loss_price=-10)

    def test_invalid_stop_loss_pct_zero(self):
        with pytest.raises(ValueError, match="stop_loss_pct must be between"):
            self._make(stop_loss_pct=0)

    def test_invalid_stop_loss_pct_one(self):
        with pytest.raises(ValueError, match="stop_loss_pct must be between"):
            self._make(stop_loss_pct=1.0)

    def test_lifecycle_approve_execute(self):
        p = self._make()
        assert p.status == PlanStatus.DRAFT
        p.approve()
        assert p.status == PlanStatus.APPROVED
        p.execute()
        assert p.status == PlanStatus.EXECUTED
        assert p.executed_at is not None

    def test_cannot_approve_non_draft(self):
        p = self._make()
        p.approve()
        with pytest.raises(ValueError, match="Can only approve DRAFT"):
            p.approve()

    def test_cannot_execute_non_approved(self):
        p = self._make()
        with pytest.raises(ValueError, match="Can only execute APPROVED"):
            p.execute()

    def test_cancel_draft(self):
        p = self._make()
        p.cancel("改变想法")
        assert p.status == PlanStatus.CANCELLED
        assert p.close_reason == "改变想法"
        assert p.closed_at is not None

    def test_cancel_approved(self):
        p = self._make()
        p.approve()
        p.cancel("市场变化")
        assert p.status == PlanStatus.CANCELLED
        assert p.closed_at is not None

    def test_cannot_cancel_executed(self):
        p = self._make()
        p.approve()
        p.execute()
        with pytest.raises(ValueError, match="Cannot cancel"):
            p.cancel()

    def test_roundtrip(self):
        p = self._make(vip_references=["学知利行", "管我财"])
        d = p.to_dict()
        p2 = TradePlan.from_dict(d)
        assert p2.plan_id == p.plan_id
        assert p2.symbol == p.symbol
        assert p2.reasons == p.reasons
        assert p2.vip_references == ["学知利行", "管我财"]
        assert p2.stop_loss_price == 1500.0
        assert p2.confidence == Confidence.MEDIUM

    def test_new_id_format(self):
        pid = TradePlan.new_id()
        assert pid.startswith("TP-")
        assert len(pid) == 15  # "TP-" + 12 hex chars


# ===================================================================
# VIPAuthor
# ===================================================================


class TestVIPAuthor:
    def test_create(self):
        a = VIPAuthor(
            author_id="xzlx",
            name="学知利行",
            platform=PortfolioSource.XUEQIU,
            platform_uid="1234567890",
            portfolio_codes=("ZH123456",),
        )
        assert a.name == "学知利行"
        assert a.enabled is True
        assert a.weight == 1.0

    def test_roundtrip(self):
        a = VIPAuthor(
            author_id="gwc",
            name="管我财",
            platform=PortfolioSource.XUEQIU,
            platform_uid="9876543210",
            portfolio_codes=("ZH111111", "ZH222222"),
            focus_assets=("stock",),
        )
        d = a.to_dict()
        a2 = VIPAuthor.from_dict(d)
        assert a2.author_id == a.author_id
        assert a2.name == a.name
        assert a2.portfolio_codes == ("ZH111111", "ZH222222")

    def test_from_dict_defaults(self):
        d = {
            "author_id": "t",
            "name": "Test",
            "platform": "xueqiu",
            "platform_uid": "123",
        }
        a = VIPAuthor.from_dict(d)
        assert a.track_posts is True
        assert a.enabled is True
        assert a.check_interval == "daily"


# ===================================================================
# Enums
# ===================================================================


class TestEnums:
    def test_portfolio_source_values(self):
        assert PortfolioSource.XUEQIU.value == "xueqiu"
        assert PortfolioSource.WEIBO.value == "weibo"
        assert PortfolioSource.MANUAL.value == "manual"

    def test_trade_direction(self):
        assert TradeDirection.BUY.value == "BUY"
        assert TradeDirection.SELL.value == "SELL"

    def test_plan_status_all(self):
        assert PlanStatus.DRAFT.value == "DRAFT"
        assert PlanStatus.APPROVED.value == "APPROVED"
        assert PlanStatus.EXECUTED.value == "EXECUTED"
        assert PlanStatus.CANCELLED.value == "CANCELLED"

    def test_confidence_chinese(self):
        assert Confidence.HIGH.value == "高"
        assert Confidence.MEDIUM.value == "中"
        assert Confidence.LOW.value == "低"

    def test_change_type_all(self):
        assert ChangeType.ADDED.value == "ADDED"
        assert ChangeType.REMOVED.value == "REMOVED"
        assert ChangeType.INCREASED.value == "INCREASED"
        assert ChangeType.DECREASED.value == "DECREASED"
        assert ChangeType.UNCHANGED.value == "UNCHANGED"


# ===================================================================
# HoldingChange serialization
# ===================================================================


class TestHoldingChangeFromDict:
    def test_roundtrip(self):
        from aipha.tracker.models import HoldingChange, ChangeType
        from decimal import Decimal
        hc = HoldingChange(
            symbol="600519.SH",
            name="贵州茅台",
            change_type=ChangeType.INCREASED,
            old_weight=Decimal("0.2"),
            new_weight=Decimal("0.3"),
            weight_delta=Decimal("0.1"),
        )
        d = hc.to_dict()
        hc2 = HoldingChange.from_dict(d)
        assert hc2.symbol == hc.symbol
        assert hc2.change_type == hc.change_type
        assert hc2.old_weight == hc.old_weight
        assert hc2.new_weight == hc.new_weight
        assert hc2.weight_delta == hc.weight_delta

    def test_roundtrip_added(self):
        from aipha.tracker.models import HoldingChange, ChangeType
        from decimal import Decimal
        hc = HoldingChange(
            symbol="000001.SZ",
            name="平安银行",
            change_type=ChangeType.ADDED,
            new_weight=Decimal("0.15"),
        )
        d = hc.to_dict()
        hc2 = HoldingChange.from_dict(d)
        assert hc2.old_weight is None
        assert hc2.new_weight == Decimal("0.15")
        assert hc2.weight_delta is None


# ===================================================================
# PortfolioDiff serialization
# ===================================================================


class TestPortfolioDiffFromDict:
    def test_roundtrip(self):
        from datetime import datetime
        from decimal import Decimal
        from aipha.tracker.models import PortfolioDiff, HoldingChange, ChangeType
        diff = PortfolioDiff(
            author_id="test",
            author_name="Test",
            from_snapshot_id="snap1",
            to_snapshot_id="snap2",
            from_time=datetime(2025, 1, 1),
            to_time=datetime(2025, 1, 2),
            changes=(
                HoldingChange(
                    symbol="600519.SH",
                    name="茅台",
                    change_type=ChangeType.INCREASED,
                    old_weight=Decimal("0.2"),
                    new_weight=Decimal("0.3"),
                    weight_delta=Decimal("0.1"),
                ),
            ),
        )
        d = diff.to_dict()
        diff2 = PortfolioDiff.from_dict(d)
        assert diff2.author_id == diff.author_id
        assert diff2.from_time == diff.from_time
        assert diff2.to_time == diff.to_time
        assert len(diff2.changes) == 1
        assert diff2.changes[0].symbol == "600519.SH"
        assert diff2.changes[0].change_type == ChangeType.INCREASED
