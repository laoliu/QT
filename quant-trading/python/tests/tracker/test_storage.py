"""Tests for tracker.storage — SQLite + JSON persistence."""

from datetime import datetime
from decimal import Decimal

import pytest

from aipha.tracker.models import (
    ChangeType,
    Confidence,
    Holding,
    HoldingChange,
    PortfolioDiff,
    PortfolioSnapshot,
    PortfolioSource,
    TradePlan,
    TradeDirection,
)
from aipha.tracker.storage import TrackerStorage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_storage(tmp_path):
    """Create a TrackerStorage backed by a temp directory."""
    storage = TrackerStorage(data_dir=tmp_path / "tracker")
    yield storage
    storage.close()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_snapshot(author_id: str = "test", holdings: list[Holding] | None = None) -> PortfolioSnapshot:
    if holdings is None:
        holdings = [
            Holding(symbol="600519.SH", name="贵州茅台", weight=Decimal("0.3")),
            Holding(symbol="000001.SZ", name="平安银行", weight=Decimal("0.2")),
        ]
    return PortfolioSnapshot(
        snapshot_id=PortfolioSnapshot.new_id(),
        source=PortfolioSource.XUEQIU,
        author_id=author_id,
        author_name="Test Author",
        snapshot_time=datetime.now(),
        fetch_time=datetime.now(),
        holdings=tuple(holdings),
    )


def _make_plan() -> TradePlan:
    return TradePlan(
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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTrackerStorage:
    # -- Initialization ------------------------------------------------------

    def test_init_creates_dirs(self, tmp_path):
        data_dir = tmp_path / "tracker"
        storage = TrackerStorage(data_dir=data_dir)
        assert (data_dir / "snapshots").exists()
        assert (data_dir / "diffs").exists()
        assert (data_dir / "plans").exists()
        assert (data_dir / "tracker.db").exists()
        storage.close()

    # -- Snapshots -----------------------------------------------------------

    def test_save_and_load_snapshot(self, tmp_storage):
        snap = _make_snapshot()
        tmp_storage.save_snapshot(snap)

        loaded = tmp_storage.get_snapshot(snap.snapshot_id)
        assert loaded is not None
        assert loaded.snapshot_id == snap.snapshot_id
        assert len(loaded.holdings) == 2
        assert loaded.holdings[0].symbol == "600519.SH"

    def test_get_latest_snapshot(self, tmp_storage):
        snap1 = _make_snapshot()
        snap2 = _make_snapshot()
        tmp_storage.save_snapshot(snap1)
        tmp_storage.save_snapshot(snap2)

        latest = tmp_storage.get_latest_snapshot("test")
        assert latest is not None
        assert latest.snapshot_id in {snap1.snapshot_id, snap2.snapshot_id}

    def test_get_snapshot_not_found(self, tmp_storage):
        result = tmp_storage.get_snapshot("nonexistent")
        assert result is None

    def test_get_latest_snapshot_not_found(self, tmp_storage):
        result = tmp_storage.get_latest_snapshot("nonexistent")
        assert result is None

    def test_list_snapshots_single_author(self, tmp_storage):
        for _ in range(3):
            tmp_storage.save_snapshot(_make_snapshot())
        meta = tmp_storage.list_snapshot_meta("test")
        assert len(meta) == 3

    def test_list_snapshots_all(self, tmp_storage):
        tmp_storage.save_snapshot(_make_snapshot("author_a"))
        tmp_storage.save_snapshot(_make_snapshot("author_b"))
        meta = tmp_storage.list_snapshot_meta()
        assert len(meta) == 2

    def test_get_snapshots_with_limit(self, tmp_storage):
        for _ in range(5):
            tmp_storage.save_snapshot(_make_snapshot())
        snaps = tmp_storage.get_snapshots("test", limit=3)
        assert len(snaps) == 3

    # -- Diffs ---------------------------------------------------------------

    def test_save_and_query_diff(self, tmp_storage):
        # FK constraint: must save snapshots first
        snap1 = PortfolioSnapshot(
            snapshot_id="snap1", source=PortfolioSource.XUEQIU,
            author_id="test", author_name="Test",
            snapshot_time=datetime(2025, 1, 1), fetch_time=datetime(2025, 1, 1),
            holdings=(Holding(symbol="600519.SH", name="茅台", weight=Decimal("0.2")),),
        )
        snap2 = PortfolioSnapshot(
            snapshot_id="snap2", source=PortfolioSource.XUEQIU,
            author_id="test", author_name="Test",
            snapshot_time=datetime(2025, 1, 2), fetch_time=datetime(2025, 1, 2),
            holdings=(Holding(symbol="600519.SH", name="茅台", weight=Decimal("0.3")),),
        )
        tmp_storage.save_snapshot(snap1)
        tmp_storage.save_snapshot(snap2)

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
        tmp_storage.save_diff(diff)

        diffs = tmp_storage.get_recent_diffs("test")
        assert len(diffs) == 1
        assert diffs[0]["has_changes"] == 1
        assert diffs[0]["change_count"] == 1

    def test_query_diffs_all(self, tmp_storage):
        for i, aid in enumerate(["a", "b"]):
            # FK constraint: must save snapshots first
            snap_from = PortfolioSnapshot(
                snapshot_id=f"s{i}a", source=PortfolioSource.XUEQIU,
                author_id=aid, author_name=f"Author {aid}",
                snapshot_time=datetime(2025, 1, 1), fetch_time=datetime(2025, 1, 1),
                holdings=(),
            )
            snap_to = PortfolioSnapshot(
                snapshot_id=f"s{i}b", source=PortfolioSource.XUEQIU,
                author_id=aid, author_name=f"Author {aid}",
                snapshot_time=datetime(2025, 1, 2), fetch_time=datetime(2025, 1, 2),
                holdings=(Holding(symbol="600519.SH", name="茅台", weight=Decimal("0.3")),),
            )
            tmp_storage.save_snapshot(snap_from)
            tmp_storage.save_snapshot(snap_to)

            diff = PortfolioDiff(
                author_id=aid,
                author_name=f"Author {aid}",
                from_snapshot_id=f"s{i}a",
                to_snapshot_id=f"s{i}b",
                from_time=datetime(2025, 1, 1),
                to_time=datetime(2025, 1, 2),
                changes=(
                    HoldingChange(
                        symbol="600519.SH", name="茅台",
                        change_type=ChangeType.ADDED, new_weight=Decimal("0.3"),
                    ),
                ),
            )
            tmp_storage.save_diff(diff)

        all_diffs = tmp_storage.get_recent_diffs()
        assert len(all_diffs) == 2

    # -- Trade Plans ---------------------------------------------------------

    def test_save_and_load_trade_plan(self, tmp_storage):
        plan = _make_plan()
        tmp_storage.save_trade_plan(plan)

        loaded = tmp_storage.get_trade_plan(plan.plan_id)
        assert loaded is not None
        assert loaded.plan_id == plan.plan_id
        assert loaded.symbol == "600519.SH"
        assert loaded.reasons == ["估值合理", "行业龙头", "自由现金流优秀"]

    def test_get_plan_not_found(self, tmp_storage):
        assert tmp_storage.get_trade_plan("nonexistent") is None

    def test_query_plans_by_status(self, tmp_storage):
        plan1 = _make_plan()
        plan2 = _make_plan()
        plan2.approve()
        tmp_storage.save_trade_plan(plan1)
        tmp_storage.save_trade_plan(plan2)

        drafts = tmp_storage.get_trade_plans(status="DRAFT")
        assert len(drafts) == 1
        assert drafts[0].status.value == "DRAFT"

        approved = tmp_storage.get_trade_plans(status="APPROVED")
        assert len(approved) == 1
        assert approved[0].status.value == "APPROVED"

    def test_query_plans_by_symbol(self, tmp_storage):
        plan = _make_plan()
        tmp_storage.save_trade_plan(plan)

        result = tmp_storage.get_trade_plans(symbol="600519.SH")
        assert len(result) == 1

        empty = tmp_storage.get_trade_plans(symbol="NOSUCH.XX")
        assert len(empty) == 0

    # -- Stats ---------------------------------------------------------------

    def test_stats(self, tmp_storage):
        tmp_storage.save_snapshot(_make_snapshot())
        tmp_storage.save_trade_plan(_make_plan())

        stats = tmp_storage.get_stats()
        assert stats["snapshot_count"] == 1
        assert stats["plan_count"] == 1
        assert stats["diff_count"] == 0
        assert len(stats["tracked_authors"]) == 1
        assert "data_dir" in stats

    def test_context_manager(self, tmp_path):
        """TrackerStorage works as context manager."""
        data_dir = tmp_path / "ctx_test"
        with TrackerStorage(data_dir=data_dir) as storage:
            snap = _make_snapshot()
            storage.save_snapshot(snap)
            loaded = storage.get_snapshot(snap.snapshot_id)
            assert loaded is not None
        # After exiting, storage should be closed (conn is None)
        assert storage._conn is None

    def test_close_idempotent(self, tmp_storage):
        """Calling close() twice should not raise."""
        tmp_storage.close()
        tmp_storage.close()  # Should not raise
