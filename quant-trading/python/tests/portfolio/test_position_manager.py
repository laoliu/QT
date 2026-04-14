"""Tests for PositionManager.

Coverage targets:
- Position CRUD operations
- Fill processing (buy/sell)
- Freeze/unfreeze for orders
- Day-end T+1 unlock
- Snapshot generation

Quality gates: >90% coverage.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from aipha.domain.models import Fill, Side
from aipha.portfolio.position_manager import PositionManager, PositionSnapshot


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def manager() -> PositionManager:
    """Empty position manager."""
    return PositionManager()


@pytest.fixture
def buy_fill() -> Fill:
    """Sample BUY fill."""
    return Fill(
        order_id="ORD001",
        symbol="600000.SH",
        side=Side.BUY,
        qty=1000,
        price=10.00,
        t_fill=datetime(2025, 1, 6, 9, 31),
        model="next_bar_open",
    )


@pytest.fixture
def sell_fill() -> Fill:
    """Sample SELL fill."""
    return Fill(
        order_id="ORD002",
        symbol="600000.SH",
        side=Side.SELL,
        qty=500,
        price=10.50,
        t_fill=datetime(2025, 1, 6, 14, 30),
        model="next_bar_open",
    )


# ============================================================================
# Basic Operations Tests
# ============================================================================


class TestBasicOperations:
    """Tests for basic position manager operations."""

    def test_empty_manager(self, manager: PositionManager):
        """Empty manager has no positions."""
        assert len(manager) == 0
        assert manager.get_qty("600000.SH") == 0
        assert manager.get_all_symbols() == []

    def test_get_position_non_existing(self, manager: PositionManager):
        """get_position returns None for non-existing symbol."""
        assert manager.get_position("600000.SH") is None

    def test_contains_operator(self, manager: PositionManager, buy_fill: Fill):
        """__contains__ operator works correctly."""
        assert "600000.SH" not in manager

        manager.process_fill(buy_fill, trade_date=date(2025, 1, 6))

        assert "600000.SH" in manager


# ============================================================================
# Fill Processing Tests
# ============================================================================


class TestFillProcessing:
    """Tests for fill processing."""

    def test_process_buy_fill(self, manager: PositionManager, buy_fill: Fill):
        """Processing BUY fill creates position."""
        manager.process_fill(buy_fill, trade_date=date(2025, 1, 6))

        assert manager.get_qty("600000.SH") == 1000
        pos = manager.get_position("600000.SH")
        assert pos is not None
        assert pos.today_bought_qty == 1000  # T+1: today's buy

    def test_process_multiple_buys(self, manager: PositionManager):
        """Multiple buys accumulate position."""
        fill1 = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            price=10.00,
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="test",
        )
        fill2 = Fill(
            order_id="ORD002",
            symbol="600000.SH",
            side=Side.BUY,
            qty=500,
            price=10.10,
            t_fill=datetime(2025, 1, 6, 10, 00),
            model="test",
        )

        manager.process_fill(fill1, trade_date=date(2025, 1, 6))
        manager.process_fill(fill2, trade_date=date(2025, 1, 6))

        assert manager.get_qty("600000.SH") == 1500

    def test_process_sell_fill(self, manager: PositionManager):
        """Processing SELL fill reduces position."""
        # Setup: buy first, then unlock via day_end with NEXT trade date
        buy_fill = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            price=10.00,
            t_fill=datetime(2025, 1, 5, 9, 31),
            model="test",
        )
        manager.process_fill(buy_fill, trade_date=date(2025, 1, 5))
        # day_end_unlock requires new_trade_date > last_trade_date
        manager.day_end_process(trade_date=date(2025, 1, 6))

        # Freeze before sell (required by Position class)
        manager.freeze_for_sell("600000.SH", qty=500, order_id="ORD002")

        # Now sell on next day
        sell_fill = Fill(
            order_id="ORD002",
            symbol="600000.SH",
            side=Side.SELL,
            qty=500,
            price=10.50,
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="test",
        )
        manager.process_fill(sell_fill, trade_date=date(2025, 1, 6))

        assert manager.get_qty("600000.SH") == 500

    def test_sell_to_zero_removes_position(self, manager: PositionManager):
        """Selling all shares removes position from manager."""
        buy_fill = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            price=10.00,
            t_fill=datetime(2025, 1, 5, 9, 31),
            model="test",
        )
        manager.process_fill(buy_fill, trade_date=date(2025, 1, 5))
        # day_end_unlock requires new_trade_date > last_trade_date
        manager.day_end_process(trade_date=date(2025, 1, 6))

        # Freeze before sell (required by Position class)
        manager.freeze_for_sell("600000.SH", qty=1000, order_id="ORD002")

        sell_fill = Fill(
            order_id="ORD002",
            symbol="600000.SH",
            side=Side.SELL,
            qty=1000,
            price=10.50,
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="test",
        )
        manager.process_fill(sell_fill, trade_date=date(2025, 1, 6))

        assert manager.get_qty("600000.SH") == 0
        assert "600000.SH" not in manager

    def test_fill_history_recorded(self, manager: PositionManager, buy_fill: Fill):
        """Fill history is recorded."""
        manager.process_fill(buy_fill, trade_date=date(2025, 1, 6))

        history = manager.get_fill_history()
        assert len(history) == 1
        assert history[0]["symbol"] == "600000.SH"
        assert history[0]["qty"] == 1000


# ============================================================================
# Freeze/Unfreeze Tests
# ============================================================================


class TestFreezeUnfreeze:
    """Tests for freeze/unfreeze operations."""

    def test_freeze_for_sell(self, manager: PositionManager):
        """Freeze reduces available_to_sell."""
        # Setup position with sellable shares
        buy_fill = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            price=10.00,
            t_fill=datetime(2025, 1, 5, 9, 31),
            model="test",
        )
        manager.process_fill(buy_fill, trade_date=date(2025, 1, 5))
        # day_end_unlock requires new_trade_date > last_trade_date
        manager.day_end_process(trade_date=date(2025, 1, 6))

        # Freeze
        result = manager.freeze_for_sell("600000.SH", qty=500, order_id="ORD002")

        assert result is True
        assert manager.get_frozen("600000.SH") == 500
        assert manager.get_available_to_sell("600000.SH") == 500

    def test_freeze_non_existing_fails(self, manager: PositionManager):
        """Freeze on non-existing position fails."""
        result = manager.freeze_for_sell("600000.SH", qty=500, order_id="ORD001")
        assert result is False

    def test_freeze_exceeds_available_fails(self, manager: PositionManager):
        """Freeze exceeding available fails."""
        buy_fill = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            price=10.00,
            t_fill=datetime(2025, 1, 5, 9, 31),
            model="test",
        )
        manager.process_fill(buy_fill, trade_date=date(2025, 1, 5))
        # day_end_unlock requires new_trade_date > last_trade_date
        manager.day_end_process(trade_date=date(2025, 1, 6))

        result = manager.freeze_for_sell("600000.SH", qty=1500, order_id="ORD002")
        assert result is False

    def test_unfreeze_for_sell(self, manager: PositionManager):
        """Unfreeze restores available_to_sell."""
        buy_fill = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            price=10.00,
            t_fill=datetime(2025, 1, 5, 9, 31),
            model="test",
        )
        manager.process_fill(buy_fill, trade_date=date(2025, 1, 5))
        # day_end_unlock requires new_trade_date > last_trade_date
        manager.day_end_process(trade_date=date(2025, 1, 6))

        manager.freeze_for_sell("600000.SH", qty=500, order_id="ORD002")
        result = manager.unfreeze_for_sell("600000.SH", qty=500, order_id="ORD002")

        assert result is True
        assert manager.get_frozen("600000.SH") == 0
        assert manager.get_available_to_sell("600000.SH") == 1000

    def test_unfreeze_non_existing_fails(self, manager: PositionManager):
        """Unfreeze on non-existing position fails."""
        result = manager.unfreeze_for_sell("600000.SH", qty=500, order_id="ORD001")
        assert result is False


# ============================================================================
# Day End Processing Tests
# ============================================================================


class TestDayEndProcessing:
    """Tests for day-end T+1 unlock."""

    def test_day_end_unlocks_today_bought(self, manager: PositionManager):
        """Day end unlocks today's bought shares."""
        buy_fill = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            price=10.00,
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="test",
        )
        manager.process_fill(buy_fill, trade_date=date(2025, 1, 6))

        # Before day_end: not sellable
        assert manager.get_available_to_sell("600000.SH") == 0

        # After day_end with NEXT trade date: sellable
        manager.day_end_process(trade_date=date(2025, 1, 7))
        assert manager.get_available_to_sell("600000.SH") == 1000

    def test_day_end_multiple_positions(self, manager: PositionManager):
        """Day end processes all positions."""
        fill1 = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            price=10.00,
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="test",
        )
        fill2 = Fill(
            order_id="ORD002",
            symbol="000001.SZ",
            side=Side.BUY,
            qty=500,
            price=15.00,
            t_fill=datetime(2025, 1, 6, 10, 00),
            model="test",
        )
        manager.process_fill(fill1, trade_date=date(2025, 1, 6))
        manager.process_fill(fill2, trade_date=date(2025, 1, 6))

        # day_end_unlock requires new_trade_date > last_trade_date
        manager.day_end_process(trade_date=date(2025, 1, 7))

        assert manager.get_available_to_sell("600000.SH") == 1000
        assert manager.get_available_to_sell("000001.SZ") == 500


# ============================================================================
# Snapshot Tests
# ============================================================================


class TestSnapshots:
    """Tests for position snapshots."""

    def test_get_positions_snapshot(self, manager: PositionManager):
        """get_positions_snapshot returns correct data."""
        buy_fill = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            price=10.00,
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="test",
        )
        manager.process_fill(buy_fill, trade_date=date(2025, 1, 6))

        snapshots = manager.get_positions_snapshot(prices={"600000.SH": 10.50})

        assert "600000.SH" in snapshots
        snap = snapshots["600000.SH"]
        assert snap.total_qty == 1000
        assert snap.market_value == 10500.0
        assert snap.today_bought_qty == 1000

    def test_get_positions_dict(self, manager: PositionManager):
        """get_positions_dict returns symbol -> qty mapping."""
        fill1 = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            price=10.00,
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="test",
        )
        fill2 = Fill(
            order_id="ORD002",
            symbol="000001.SZ",
            side=Side.BUY,
            qty=500,
            price=15.00,
            t_fill=datetime(2025, 1, 6, 10, 00),
            model="test",
        )
        manager.process_fill(fill1, trade_date=date(2025, 1, 6))
        manager.process_fill(fill2, trade_date=date(2025, 1, 6))

        positions_dict = manager.get_positions_dict()

        assert positions_dict == {"600000.SH": 1000, "000001.SZ": 500}

    def test_get_total_market_value(self, manager: PositionManager):
        """get_total_market_value calculates correct total."""
        fill1 = Fill(
            order_id="ORD001",
            symbol="600000.SH",
            side=Side.BUY,
            qty=1000,
            price=10.00,
            t_fill=datetime(2025, 1, 6, 9, 31),
            model="test",
        )
        fill2 = Fill(
            order_id="ORD002",
            symbol="000001.SZ",
            side=Side.BUY,
            qty=500,
            price=15.00,
            t_fill=datetime(2025, 1, 6, 10, 00),
            model="test",
        )
        manager.process_fill(fill1, trade_date=date(2025, 1, 6))
        manager.process_fill(fill2, trade_date=date(2025, 1, 6))

        prices = {"600000.SH": 10.50, "000001.SZ": 15.50}
        total_mv = manager.get_total_market_value(prices)

        # 1000 * 10.50 + 500 * 15.50 = 10500 + 7750 = 18250
        assert total_mv == pytest.approx(18250.0, rel=1e-9)


# ============================================================================
# PositionSnapshot Tests
# ============================================================================


class TestPositionSnapshot:
    """Tests for PositionSnapshot dataclass."""

    def test_snapshot_creation(self):
        """PositionSnapshot can be created with all fields."""
        snap = PositionSnapshot(
            symbol="600000.SH",
            total_qty=1000,
            frozen_qty=100,
            available_to_sell=400,
            today_bought_qty=500,
            avg_cost=10.00,
            market_value=10500.0,
        )

        assert snap.symbol == "600000.SH"
        assert snap.total_qty == 1000
        assert snap.frozen_qty == 100
        assert snap.available_to_sell == 400
        assert snap.today_bought_qty == 500
        assert snap.avg_cost == 10.00
        assert snap.market_value == 10500.0
