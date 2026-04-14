"""Tests for tracker.diff — portfolio diff engine."""

from datetime import datetime
from decimal import Decimal

import pytest

from aipha.tracker.diff import compute_diff, DEFAULT_WEIGHT_THRESHOLD
from aipha.tracker.models import (
    ChangeType,
    Holding,
    PortfolioSnapshot,
    PortfolioSource,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _snap(author_id: str, holdings: list[Holding], time: datetime | None = None) -> PortfolioSnapshot:
    return PortfolioSnapshot(
        snapshot_id=PortfolioSnapshot.new_id(),
        source=PortfolioSource.XUEQIU,
        author_id=author_id,
        author_name="Test",
        snapshot_time=time or datetime(2025, 1, 15),
        fetch_time=datetime.now(),
        holdings=tuple(holdings),
    )


def _h(symbol: str, name: str, weight: float) -> Holding:
    return Holding(symbol=symbol, name=name, weight=Decimal(str(weight)))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestComputeDiff:
    def test_no_changes(self):
        old = _snap("a", [_h("600519.SH", "茅台", 0.3), _h("000001.SZ", "平安", 0.2)])
        new = _snap("a", [_h("600519.SH", "茅台", 0.3), _h("000001.SZ", "平安", 0.2)])
        diff = compute_diff(old, new)
        assert not diff.has_changes
        assert len(diff.changes) == 2
        assert all(c.change_type == ChangeType.UNCHANGED for c in diff.changes)

    def test_added_stock(self):
        old = _snap("a", [_h("600519.SH", "茅台", 0.3)])
        new = _snap("a", [_h("600519.SH", "茅台", 0.3), _h("000001.SZ", "平安", 0.2)])
        diff = compute_diff(old, new)
        assert diff.has_changes
        assert len(diff.added) == 1
        assert diff.added[0].symbol == "000001.SZ"
        assert diff.added[0].new_weight == Decimal("0.2")

    def test_removed_stock(self):
        old = _snap("a", [_h("600519.SH", "茅台", 0.3), _h("000001.SZ", "平安", 0.2)])
        new = _snap("a", [_h("600519.SH", "茅台", 0.3)])
        diff = compute_diff(old, new)
        assert diff.has_changes
        assert len(diff.removed) == 1
        assert diff.removed[0].symbol == "000001.SZ"

    def test_increased_weight(self):
        old = _snap("a", [_h("600519.SH", "茅台", 0.3)])
        new = _snap("a", [_h("600519.SH", "茅台", 0.5)])
        diff = compute_diff(old, new)
        assert diff.has_changes
        assert len(diff.increased) == 1
        assert diff.increased[0].weight_delta == Decimal("0.2")

    def test_decreased_weight(self):
        old = _snap("a", [_h("600519.SH", "茅台", 0.5)])
        new = _snap("a", [_h("600519.SH", "茅台", 0.2)])
        diff = compute_diff(old, new)
        assert diff.has_changes
        assert len(diff.decreased) == 1
        assert diff.decreased[0].weight_delta == Decimal("-0.3")

    def test_below_threshold_unchanged(self):
        old = _snap("a", [_h("600519.SH", "茅台", 0.300)])
        new = _snap("a", [_h("600519.SH", "茅台", 0.304)])  # delta=0.004 < 0.005
        diff = compute_diff(old, new)
        assert not diff.has_changes

    def test_at_threshold_is_change(self):
        old = _snap("a", [_h("600519.SH", "茅台", 0.300)])
        new = _snap("a", [_h("600519.SH", "茅台", 0.305)])  # delta=0.005 == threshold
        diff = compute_diff(old, new)
        assert diff.has_changes
        assert len(diff.increased) == 1

    def test_custom_threshold(self):
        old = _snap("a", [_h("600519.SH", "茅台", 0.300)])
        new = _snap("a", [_h("600519.SH", "茅台", 0.304)])
        diff = compute_diff(old, new, weight_threshold=Decimal("0.003"))
        assert diff.has_changes
        assert len(diff.increased) == 1

    def test_complex_changes(self):
        old = _snap("a", [
            _h("600519.SH", "茅台", 0.3),
            _h("000001.SZ", "平安", 0.2),
            _h("601318.SH", "中国平安", 0.15),
        ])
        new = _snap("a", [
            _h("600519.SH", "茅台", 0.4),   # increased
            _h("000001.SZ", "平安", 0.1),   # decreased
            _h("300750.SZ", "宁德", 0.25),  # added
            # 601318 removed
        ])
        diff = compute_diff(old, new)
        assert diff.has_changes
        assert len(diff.added) == 1
        assert len(diff.removed) == 1
        assert len(diff.increased) == 1
        assert len(diff.decreased) == 1

    def test_different_authors_raises(self):
        old = _snap("a", [_h("600519.SH", "茅台", 0.3)])
        new = _snap("b", [_h("600519.SH", "茅台", 0.3)])
        with pytest.raises(ValueError, match="different authors"):
            compute_diff(old, new)

    def test_empty_portfolios(self):
        old = _snap("a", [])
        new = _snap("a", [])
        diff = compute_diff(old, new)
        assert not diff.has_changes
        assert len(diff.changes) == 0

    def test_summary_output(self):
        old = _snap("a", [_h("600519.SH", "茅台", 0.3)])
        new = _snap("a", [_h("600519.SH", "茅台", 0.4), _h("000001.SZ", "平安", 0.2)])
        diff = compute_diff(old, new)
        summary = diff.summary()
        assert "Test" in summary
        assert "新增" in summary
        assert "加仓" in summary

    def test_summary_no_changes(self):
        old = _snap("a", [_h("600519.SH", "茅台", 0.3)])
        new = _snap("a", [_h("600519.SH", "茅台", 0.3)])
        diff = compute_diff(old, new)
        summary = diff.summary()
        assert "无变化" in summary
