"""CapacityAwareAllocator 单元测试。

测试覆盖:
  - 基本分配 (无约束)
  - CB容量截断 + proportional 重分配
  - CB容量截断 + equal 重分配  
  - CB容量截断 + priority 重分配
  - 最低权重保障
  - 多组件同时受限
  - 不同资金规模下的自动调整
  - 配置工厂函数
  - 边界条件/异常处理
"""

import math
import pytest

from aipha.portfolio.capacity_allocator import (
    AllocationResult,
    CapacityAwareAllocator,
    CapacityConstraint,
    create_allocator_from_config,
)


class TestCapacityConstraint:
    """CapacityConstraint 数据类测试。"""

    def test_valid_constraint(self):
        c = CapacityConstraint("cb", max_capital=500_000, min_weight=0.02)
        assert c.component == "cb"
        assert c.max_capital == 500_000
        assert c.min_weight == 0.02

    def test_negative_capital_raises(self):
        with pytest.raises(ValueError, match="max_capital"):
            CapacityConstraint("cb", max_capital=-1)

    def test_invalid_min_weight_raises(self):
        with pytest.raises(ValueError, match="min_weight"):
            CapacityConstraint("cb", max_capital=100, min_weight=1.5)


class TestAllocationResult:
    """AllocationResult 数据类测试。"""

    def test_utilization_full(self):
        r = AllocationResult(
            weights={"a": 0.5, "b": 0.5},
            capitals={"a": 500, "b": 500},
            total_capital=1000,
        )
        assert r.effective_utilization == 1.0

    def test_summary_format(self):
        r = AllocationResult(
            weights={"ml": 0.50, "cb": 0.05},
            capitals={"ml": 5_000_000, "cb": 500_000},
            total_capital=10_000_000,
            capped_components=["cb"],
            excess_redistributed=3_500_000,
        )
        s = r.summary()
        assert "cb" in s
        assert "Capped" in s


class TestAllocatorNoConstraints:
    """无约束时，权重应保持不变。"""

    def test_no_limits_passthrough(self):
        a = CapacityAwareAllocator(
            base_weights={"ml": 0.50, "cb": 0.30, "etf": 0.20},
        )
        r = a.compute(1_000_000)
        assert math.isclose(r.weights["ml"], 0.50)
        assert math.isclose(r.weights["cb"], 0.30)
        assert math.isclose(r.weights["etf"], 0.20)
        assert r.capped_components == []
        assert r.excess_redistributed == 0.0

    def test_small_capital_no_cap(self):
        """资金小于容量时不触发截断。"""
        a = CapacityAwareAllocator(
            base_weights={"ml": 0.50, "cb": 0.40, "etf": 0.10},
            capacity_limits={"cb": 500_000},
        )
        r = a.compute(100_000)  # 100K * 40% = 40K < 500K
        assert math.isclose(r.weights["cb"], 0.40)
        assert r.capped_components == []


class TestAllocatorProportional:
    """proportional 重分配测试。"""

    def test_cb_capped_at_50wan(self):
        """1000万 * 40% = 400万 > 50万, 应截断 CB 到 5%。"""
        a = CapacityAwareAllocator(
            base_weights={"ml2000": 0.30, "ml1000": 0.10,
                          "cb": 0.40, "etf": 0.20},
            capacity_limits={"cb": 500_000},
        )
        r = a.compute(10_000_000)
        assert math.isclose(r.weights["cb"], 0.05, abs_tol=0.001)
        assert "cb" in r.capped_components
        # 总权重仍为 1.0
        assert math.isclose(sum(r.weights.values()), 1.0, abs_tol=0.001)
        # ML2000 应获得最多增配 (原权重最大)
        assert r.weights["ml2000"] > 0.30
        assert r.weights["ml1000"] > 0.10
        assert r.weights["etf"] > 0.20

    def test_proportional_ratio_correct(self):
        """检验 proportional 分配比例."""
        a = CapacityAwareAllocator(
            base_weights={"a": 0.60, "b": 0.30, "c": 0.10},
            capacity_limits={"c": 10_000},
        )
        r = a.compute(1_000_000)
        # c: 10% * 1M = 100K > 10K, cap to 1%
        # excess = 9% → a gets 60/(60+30)*9% = 6%, b gets 3%
        assert math.isclose(r.weights["a"], 0.66, abs_tol=0.01)
        assert math.isclose(r.weights["b"], 0.33, abs_tol=0.01)
        assert math.isclose(r.weights["c"], 0.01, abs_tol=0.001)

    def test_capitals_match_weights(self):
        a = CapacityAwareAllocator(
            base_weights={"ml": 0.60, "cb": 0.40},
            capacity_limits={"cb": 200_000},
        )
        r = a.compute(5_000_000)
        for comp in r.weights:
            expected = r.weights[comp] * r.total_capital
            assert math.isclose(r.capitals[comp], expected, rel_tol=0.001)


class TestAllocatorEqual:
    """equal 重分配测试。"""

    def test_equal_redistribution(self):
        a = CapacityAwareAllocator(
            base_weights={"a": 0.50, "b": 0.30, "c": 0.20},
            capacity_limits={"c": 10_000},
            redistribution="equal",
        )
        r = a.compute(1_000_000)
        # c: 20% * 1M = 200K > 10K, cap to 1%
        # excess = 19% → a gets +9.5%, b gets +9.5%
        assert math.isclose(r.weights["a"], 0.595, abs_tol=0.01)
        assert math.isclose(r.weights["b"], 0.395, abs_tol=0.01)


class TestAllocatorPriority:
    """priority 重分配测试。"""

    def test_priority_all_to_first(self):
        a = CapacityAwareAllocator(
            base_weights={"a": 0.30, "b": 0.30, "c": 0.40},
            capacity_limits={"c": 10_000},
            redistribution="priority",
            priority_order=["a", "b"],
        )
        r = a.compute(1_000_000)
        # c capped, all excess goes to a (first priority)
        assert r.weights["a"] > 0.60
        assert math.isclose(r.weights["b"], 0.30, abs_tol=0.001)


class TestAllocatorMinWeight:
    """最低权重保障测试。"""

    def test_min_weight_respected(self):
        """min_weight 能放进容量上限时才生效。"""
        a = CapacityAwareAllocator(
            base_weights={"ml": 0.90, "cb": 0.10},
            capacity_limits={"cb": 500_000},  # 5% of 1000万，容得下 2%
            min_weights={"cb": 0.02},
        )
        r = a.compute(10_000_000)
        assert r.weights["cb"] >= 0.02

    def test_capacity_ceiling_beats_min_weight(self):
        """容量硬上限压过 min_weight 软下限。

        cb 容量只有 0.1 万，min_weight=2% 要 20 万 —— 真按 min_weight 配，
        就是往一个只装得下 0.1 万的策略里塞 200 倍的钱，冲击成本会把
        回测里那点超额收益全吃掉。容量是硬约束，软下限必须让路。
        """
        a = CapacityAwareAllocator(
            base_weights={"ml": 0.60, "cb": 0.40},
            capacity_limits={"cb": 1_000},  # 极小容量
            min_weights={"cb": 0.02},
        )
        r = a.compute(10_000_000)
        assert r.weights["cb"] <= 1_000 / 10_000_000 + 1e-12


class TestAllocatorMultiCapped:
    """多组件同时受限。"""

    def test_two_components_capped(self):
        a = CapacityAwareAllocator(
            base_weights={"a": 0.25, "b": 0.25, "c": 0.25, "d": 0.25},
            capacity_limits={"c": 50_000, "d": 30_000},
        )
        r = a.compute(1_000_000)
        assert "c" in r.capped_components
        assert "d" in r.capped_components
        assert math.isclose(sum(r.weights.values()), 1.0, abs_tol=0.001)


class TestAllocatorForRange:
    """不同资金量批量测试。"""

    def test_capital_range(self):
        a = CapacityAwareAllocator(
            base_weights={"ml": 0.50, "cb": 0.40, "etf": 0.10},
            capacity_limits={"cb": 500_000},
        )
        levels = [500_000, 1_000_000, 5_000_000, 10_000_000]
        results = a.compute_for_range(levels)
        assert len(results) == 4
        # 50万: cb=40% → 20万 < 50万 → 不截断
        assert results[0].capped_components == []
        # 100万: cb=40% → 40万 < 50万 → 不截断
        assert results[1].capped_components == []
        # 500万: cb=40% → 200万 > 50万 → 截断
        assert "cb" in results[2].capped_components
        # 1000万: cb=40% → 400万 > 50万 → 截断
        assert "cb" in results[3].capped_components
        # CB权重随资金增大而缩小
        assert results[3].weights["cb"] < results[2].weights["cb"]

    def test_cb_weight_at_different_scales(self):
        """验证核心场景: CB 权重随资金自动调低。"""
        a = CapacityAwareAllocator(
            base_weights={"ml2000": 0.30, "ml1000": 0.10,
                          "cb": 0.40, "etf": 0.20},
            capacity_limits={"cb": 500_000},
        )
        # 50万 → cb=40%(20万) < 50万, 维持40%
        r50 = a.compute(500_000)
        assert math.isclose(r50.weights["cb"], 0.40)

        # 200万 → cb=40%(80万) > 50万 → cap到25%
        r200 = a.compute(2_000_000)
        assert math.isclose(r200.weights["cb"], 0.25, abs_tol=0.01)

        # 1000万 → cb=40%(400万) > 50万 → cap到5%
        r1000 = a.compute(10_000_000)
        assert math.isclose(r1000.weights["cb"], 0.05, abs_tol=0.01)


class TestConfigFactory:
    """create_allocator_from_config 测试。"""

    def test_disabled_returns_none(self):
        cfg = {"capacity_aware": {"enabled": False}}
        assert create_allocator_from_config(cfg) is None

    def test_missing_section_returns_none(self):
        cfg = {"portfolio_weights": {"a": 1.0}}
        assert create_allocator_from_config(cfg) is None

    def test_simple_config(self):
        cfg = {
            "portfolio_weights": {
                "ml2000": 0.50, "ml1000": 0.20,
                "cb": 0.05, "etf": 0.25,
            },
            "capacity_aware": {
                "enabled": True,
                "cb_max_capital": 500_000,
                "cb_min_weight": 0.02,
                "redistribution_rule": "proportional",
            },
        }
        a = create_allocator_from_config(cfg)
        assert a is not None
        assert a.capacity_limits["cb"] == 500_000
        assert a.min_weights["cb"] == 0.02

    def test_detailed_limits_config(self):
        cfg = {
            "portfolio_weights": {"a": 0.60, "b": 0.40},
            "capacity_aware": {
                "enabled": True,
                "limits": {
                    "b": {"max_capital": 200_000, "min_weight": 0.05},
                },
            },
        }
        a = create_allocator_from_config(cfg)
        assert a is not None
        assert a.capacity_limits["b"] == 200_000
        assert a.min_weights["b"] == 0.05


class TestAllocatorEdgeCases:
    """边界条件测试。"""

    def test_invalid_weights_sum(self):
        with pytest.raises(ValueError, match="sum to 1.0"):
            CapacityAwareAllocator(base_weights={"a": 0.5, "b": 0.3})

    def test_zero_capital_raises(self):
        a = CapacityAwareAllocator(base_weights={"a": 1.0})
        with pytest.raises(ValueError, match="total_capital"):
            a.compute(0)

    def test_invalid_redistribution(self):
        with pytest.raises(ValueError, match="redistribution"):
            CapacityAwareAllocator(
                base_weights={"a": 1.0}, redistribution="invalid"
            )

    def test_single_component(self):
        """单组件: 即使受限也无法重分配。"""
        a = CapacityAwareAllocator(
            base_weights={"cb": 1.0},
            capacity_limits={"cb": 50_000},
        )
        r = a.compute(1_000_000)
        assert "cb" in r.capped_components
        assert math.isclose(r.weights["cb"], 0.05, abs_tol=0.001)


