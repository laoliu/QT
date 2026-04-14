"""Tests for capacity estimation module.

Coverage targets:
- estimate_capacity: normal, edge cases, bottleneck identification
- estimate_capacity_from_positions: position-to-weight conversion
- Input validation
"""
from __future__ import annotations

import pytest

from aipha.execution.capacity import (
    CapacityResult,
    estimate_capacity,
    estimate_capacity_from_positions,
)


# ============================================================================
# estimate_capacity Tests
# ============================================================================


class TestEstimateCapacity:
    """Tests for estimate_capacity"""

    def test_basic_equal_weights(self):
        """等权持仓的容量应由最小 ADV 标的决定"""
        weights = {"A": 0.5, "B": 0.5}
        adv_map = {"A": 1_000_000, "B": 500_000}

        result = estimate_capacity(weights, adv_map, max_participation_rate=0.10)

        # B: 500000 * 0.10 / 0.5 = 100000
        # A: 1000000 * 0.10 / 0.5 = 200000
        # 瓶颈 B，容量 100000
        assert result.max_aum == pytest.approx(100_000)
        assert result.bottleneck_symbol == "B"
        assert result.n_symbols == 2

    def test_single_symbol(self):
        """单标的"""
        weights = {"600000.SH": 1.0}
        adv_map = {"600000.SH": 10_000_000}

        result = estimate_capacity(weights, adv_map)

        assert result.max_aum == pytest.approx(10_000_000 * 0.10 / 1.0)
        assert result.bottleneck_symbol == "600000.SH"
        assert result.n_symbols == 1

    def test_different_weights(self):
        """不等权持仓"""
        weights = {"A": 0.8, "B": 0.2}
        adv_map = {"A": 1_000_000, "B": 500_000}

        result = estimate_capacity(weights, adv_map, max_participation_rate=0.10)

        cap_a = 1_000_000 * 0.10 / 0.8  # 125000
        cap_b = 500_000 * 0.10 / 0.2  # 250000
        assert result.max_aum == pytest.approx(cap_a)
        assert result.bottleneck_symbol == "A"

    def test_custom_participation_rate(self):
        """自定义参与率"""
        weights = {"A": 0.5}
        adv_map = {"A": 1_000_000}

        r05 = estimate_capacity(weights, adv_map, max_participation_rate=0.05)
        r20 = estimate_capacity(weights, adv_map, max_participation_rate=0.20)

        assert r05.max_aum < r20.max_aum
        assert r05.max_aum == pytest.approx(1_000_000 * 0.05 / 0.5)
        assert r20.max_aum == pytest.approx(1_000_000 * 0.20 / 0.5)

    def test_missing_adv_zero_capacity(self):
        """ADV 缺失的标的容量应为 0"""
        weights = {"A": 0.5, "B": 0.5}
        adv_map = {"A": 1_000_000}  # B missing

        result = estimate_capacity(weights, adv_map)

        assert result.max_aum == 0.0
        assert result.bottleneck_symbol == "B"

    def test_zero_adv_zero_capacity(self):
        """ADV = 0 的标的容量应为 0"""
        weights = {"A": 0.5}
        adv_map = {"A": 0.0}

        result = estimate_capacity(weights, adv_map)

        assert result.max_aum == 0.0

    def test_empty_weights(self):
        """空权重"""
        result = estimate_capacity({}, {"A": 1_000_000})

        assert result.max_aum == 0.0
        assert result.n_symbols == 0

    def test_negative_weight_skipped(self):
        """负权重标的应被跳过"""
        weights = {"A": 0.5, "B": -0.1}
        adv_map = {"A": 1_000_000, "B": 500_000}

        result = estimate_capacity(weights, adv_map)

        assert result.n_symbols == 1
        assert "B" not in result.per_symbol_capacity

    def test_zero_weight_skipped(self):
        """零权重标的应被跳过"""
        weights = {"A": 0.5, "B": 0.0}
        adv_map = {"A": 1_000_000, "B": 500_000}

        result = estimate_capacity(weights, adv_map)

        assert result.n_symbols == 1

    def test_invalid_participation_rate_zero(self):
        """参与率为 0 应报错"""
        with pytest.raises(ValueError, match="max_participation_rate"):
            estimate_capacity({"A": 0.5}, {"A": 1_000_000}, max_participation_rate=0.0)

    def test_invalid_participation_rate_negative(self):
        """参与率为负应报错"""
        with pytest.raises(ValueError, match="max_participation_rate"):
            estimate_capacity({"A": 0.5}, {"A": 1_000_000}, max_participation_rate=-0.1)

    def test_invalid_participation_rate_above_1(self):
        """参与率超过 1 应报错"""
        with pytest.raises(ValueError, match="max_participation_rate"):
            estimate_capacity({"A": 0.5}, {"A": 1_000_000}, max_participation_rate=1.5)

    def test_participation_rate_exactly_1(self):
        """参与率恰好为 1 应正常工作"""
        weights = {"A": 0.5}
        adv_map = {"A": 1_000_000}

        result = estimate_capacity(weights, adv_map, max_participation_rate=1.0)

        assert result.max_aum == pytest.approx(1_000_000 / 0.5)

    def test_per_symbol_capacity_dict(self):
        """应返回每只标的的容量明细"""
        weights = {"A": 0.3, "B": 0.7}
        adv_map = {"A": 1_000_000, "B": 2_000_000}

        result = estimate_capacity(weights, adv_map, max_participation_rate=0.10)

        assert "A" in result.per_symbol_capacity
        assert "B" in result.per_symbol_capacity
        assert result.per_symbol_capacity["A"] == pytest.approx(1_000_000 * 0.10 / 0.3)
        assert result.per_symbol_capacity["B"] == pytest.approx(2_000_000 * 0.10 / 0.7)

    def test_avg_adv(self):
        """平均 ADV 计算"""
        weights = {"A": 0.5, "B": 0.5}
        adv_map = {"A": 1_000_000, "B": 3_000_000}

        result = estimate_capacity(weights, adv_map)

        assert result.avg_adv == pytest.approx(2_000_000)

    def test_result_is_frozen(self):
        """CapacityResult 应为冻结的 dataclass"""
        result = estimate_capacity({"A": 0.5}, {"A": 1_000_000})

        with pytest.raises(AttributeError):
            result.max_aum = 0  # type: ignore


# ============================================================================
# estimate_capacity_from_positions Tests
# ============================================================================


class TestEstimateCapacityFromPositions:
    """Tests for estimate_capacity_from_positions"""

    def test_basic_from_positions(self):
        """从持仓快照估算容量"""
        positions = {"A": 100, "B": 200}
        prices = {"A": 50.0, "B": 25.0}
        adv_map = {"A": 1_000_000, "B": 500_000}
        total_nav = 10_000.0  # A: 5000, B: 5000

        result = estimate_capacity_from_positions(
            positions, prices, adv_map, total_nav
        )

        # A weight: 5000/10000 = 0.5, B weight: 5000/10000 = 0.5
        # A capacity: 1000000 * 0.10 / 0.5 = 200000
        # B capacity: 500000 * 0.10 / 0.5 = 100000
        assert result.max_aum == pytest.approx(100_000)
        assert result.bottleneck_symbol == "B"

    def test_zero_nav(self):
        """NAV = 0 应安全返回"""
        result = estimate_capacity_from_positions(
            {"A": 100}, {"A": 50.0}, {"A": 1_000_000}, total_nav=0.0
        )
        assert result.max_aum == 0.0

    def test_negative_nav(self):
        """负 NAV 应安全返回"""
        result = estimate_capacity_from_positions(
            {"A": 100}, {"A": 50.0}, {"A": 1_000_000}, total_nav=-100.0
        )
        assert result.max_aum == 0.0

    def test_zero_qty_skipped(self):
        """零持仓应被跳过"""
        result = estimate_capacity_from_positions(
            {"A": 0, "B": 100},
            {"A": 50.0, "B": 25.0},
            {"A": 1_000_000, "B": 500_000},
            total_nav=2_500.0,
        )
        assert result.n_symbols == 1

    def test_missing_price_skipped(self):
        """价格缺失的标的应被跳过"""
        result = estimate_capacity_from_positions(
            {"A": 100, "B": 100},
            {"A": 50.0},  # B missing
            {"A": 1_000_000},
            total_nav=5_000.0,
        )
        assert result.n_symbols == 1

    def test_custom_participation_rate(self):
        """应传递自定义参与率"""
        positions = {"A": 100}
        prices = {"A": 100.0}
        adv_map = {"A": 1_000_000}
        total_nav = 10_000.0

        r05 = estimate_capacity_from_positions(
            positions, prices, adv_map, total_nav,
            max_participation_rate=0.05,
        )
        r20 = estimate_capacity_from_positions(
            positions, prices, adv_map, total_nav,
            max_participation_rate=0.20,
        )

        assert r05.max_aum < r20.max_aum
