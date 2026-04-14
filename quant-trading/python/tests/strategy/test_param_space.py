"""Tests for parameter space declaration and analysis tools.

Coverage targets:
- ParamSpec validation (type constraints, range checks)
- ParamSpace operations (get, defaults, critical_params)
- generate_grid (all param types, max_combinations, only_critical)
- perturb_params (numeric, bool, choice, clamping, OAT)
- Strategy.param_space() integration
"""
from __future__ import annotations

import pytest

from aipha.strategy.param_space import (
    ParamSpace,
    ParamSpec,
    ParamType,
    generate_grid,
    perturb_params,
)


# ============================================================================
# ParamSpec Tests
# ============================================================================


class TestParamSpec:
    """ParamSpec 构造与验证测试"""

    def test_int_param(self):
        """整型参数正常构造"""
        spec = ParamSpec(
            name="top_k", param_type=ParamType.INT,
            default=20, low=5, high=50,
        )
        assert spec.name == "top_k"
        assert spec.param_type == ParamType.INT
        assert spec.default == 20

    def test_float_param(self):
        """浮点参数正常构造"""
        spec = ParamSpec(
            name="stop_loss_pct", param_type=ParamType.FLOAT,
            default=0.15, low=0.05, high=0.50,
        )
        assert spec.low == 0.05
        assert spec.high == 0.50

    def test_bool_param(self):
        """布尔参数无需 low/high"""
        spec = ParamSpec(
            name="exclude_st", param_type=ParamType.BOOL, default=True,
        )
        assert spec.default is True

    def test_choice_param(self):
        """枚举选项参数"""
        spec = ParamSpec(
            name="sort_by", param_type=ParamType.CHOICE,
            default="price", choices=("price", "volume", "turnover"),
        )
        assert spec.choices == ("price", "volume", "turnover")

    def test_int_missing_range_raises(self):
        """INT 类型缺少 low/high 应报错"""
        with pytest.raises(ValueError, match="必须指定 low 和 high"):
            ParamSpec(name="x", param_type=ParamType.INT, default=10)

    def test_float_missing_range_raises(self):
        """FLOAT 类型缺少 low/high 应报错"""
        with pytest.raises(ValueError, match="必须指定 low 和 high"):
            ParamSpec(name="x", param_type=ParamType.FLOAT, default=0.5, low=0.0)

    def test_low_gt_high_raises(self):
        """low > high 应报错"""
        with pytest.raises(ValueError, match="low.*>.*high"):
            ParamSpec(name="x", param_type=ParamType.INT, default=10, low=50, high=5)

    def test_choice_no_choices_raises(self):
        """CHOICE 类型无选项应报错"""
        with pytest.raises(ValueError, match="必须提供 choices"):
            ParamSpec(name="x", param_type=ParamType.CHOICE, default="a")

    def test_frozen(self):
        """ParamSpec 应为不可变"""
        spec = ParamSpec(name="x", param_type=ParamType.BOOL, default=True)
        with pytest.raises(AttributeError):
            spec.name = "y"  # type: ignore

    def test_is_critical_default_false(self):
        """is_critical 默认为 False"""
        spec = ParamSpec(name="x", param_type=ParamType.BOOL, default=True)
        assert spec.is_critical is False


# ============================================================================
# ParamSpace Tests
# ============================================================================


class TestParamSpace:
    """ParamSpace 操作测试"""

    @pytest.fixture
    def sample_space(self) -> ParamSpace:
        return ParamSpace(
            strategy_name="test_strategy",
            specs=[
                ParamSpec("top_k", ParamType.INT, 20, low=5, high=50, is_critical=True),
                ParamSpec("stop_loss", ParamType.FLOAT, 0.15, low=0.05, high=0.50),
                ParamSpec("exclude_st", ParamType.BOOL, True),
            ],
        )

    def test_get_existing(self, sample_space: ParamSpace):
        """按名称获取存在的参数"""
        spec = sample_space.get("top_k")
        assert spec is not None
        assert spec.name == "top_k"

    def test_get_missing(self, sample_space: ParamSpace):
        """获取不存在的参数返回 None"""
        assert sample_space.get("nonexistent") is None

    def test_defaults(self, sample_space: ParamSpace):
        """defaults 应返回所有默认值"""
        d = sample_space.defaults()
        assert d == {"top_k": 20, "stop_loss": 0.15, "exclude_st": True}

    def test_critical_params(self, sample_space: ParamSpace):
        """critical_params 应只返回关键参数"""
        critical = sample_space.critical_params()
        assert len(critical) == 1
        assert critical[0].name == "top_k"

    def test_names(self, sample_space: ParamSpace):
        """names 属性"""
        assert sample_space.names == ["top_k", "stop_loss", "exclude_st"]

    def test_empty_space(self):
        """空参数空间"""
        space = ParamSpace(strategy_name="empty")
        assert space.defaults() == {}
        assert space.critical_params() == []
        assert space.names == []


# ============================================================================
# generate_grid Tests
# ============================================================================


class TestGenerateGrid:
    """参数网格生成测试"""

    def test_int_grid(self):
        """INT 参数网格"""
        space = ParamSpace("test", [
            ParamSpec("k", ParamType.INT, 10, low=5, high=20, step=5),
        ])
        grid = generate_grid(space)
        values = [g["k"] for g in grid]
        assert 5 in values
        assert 10 in values
        assert 15 in values
        assert 20 in values

    def test_float_grid(self):
        """FLOAT 参数网格"""
        space = ParamSpace("test", [
            ParamSpec("r", ParamType.FLOAT, 0.1, low=0.0, high=0.4, step=0.1),
        ])
        grid = generate_grid(space)
        values = [g["r"] for g in grid]
        assert len(values) >= 4  # 0.0, 0.1, 0.2, 0.3, 0.4
        assert pytest.approx(values[0]) == 0.0

    def test_bool_grid(self):
        """BOOL 参数网格"""
        space = ParamSpace("test", [
            ParamSpec("flag", ParamType.BOOL, True),
        ])
        grid = generate_grid(space)
        values = {g["flag"] for g in grid}
        assert values == {True, False}

    def test_choice_grid(self):
        """CHOICE 参数网格"""
        space = ParamSpace("test", [
            ParamSpec("mode", ParamType.CHOICE, "a", choices=("a", "b", "c")),
        ])
        grid = generate_grid(space)
        values = {g["mode"] for g in grid}
        assert values == {"a", "b", "c"}

    def test_cartesian_product(self):
        """多参数笛卡尔积"""
        space = ParamSpace("test", [
            ParamSpec("k", ParamType.INT, 10, low=10, high=20, step=10),
            ParamSpec("flag", ParamType.BOOL, True),
        ])
        grid = generate_grid(space)
        # k in {10, 20}, flag in {True, False} → 4 combinations
        assert len(grid) == 4

    def test_max_combinations_truncation(self):
        """超出 max_combinations 应截断"""
        space = ParamSpace("test", [
            ParamSpec("k", ParamType.INT, 10, low=1, high=100, step=1),
            ParamSpec("j", ParamType.INT, 10, low=1, high=100, step=1),
        ])
        grid = generate_grid(space, max_combinations=50)
        assert len(grid) == 50

    def test_only_critical(self):
        """only_critical 应仅扫描关键参数"""
        space = ParamSpace("test", [
            ParamSpec("k", ParamType.INT, 10, low=5, high=20, step=5, is_critical=True),
            ParamSpec("r", ParamType.FLOAT, 0.1, low=0.0, high=0.5, step=0.1),
        ])
        grid = generate_grid(space, only_critical=True)
        # r 应保持默认值 0.1
        for g in grid:
            assert g["r"] == 0.1
        # k 应有多个值
        k_values = {g["k"] for g in grid}
        assert len(k_values) > 1

    def test_empty_space(self):
        """空参数空间返回默认值组合"""
        space = ParamSpace("test")
        grid = generate_grid(space)
        assert len(grid) == 1
        assert grid[0] == {}

    def test_int_auto_step(self):
        """INT 参数无 step 时自动计算步长"""
        space = ParamSpace("test", [
            ParamSpec("k", ParamType.INT, 10, low=0, high=100),
        ])
        grid = generate_grid(space)
        # 自动步长 = max(1, (100-0)//4) = 25
        values = sorted(g["k"] for g in grid)
        assert values[0] == 0
        assert values[-1] == 100

    def test_ensures_high_included(self):
        """应确保 high 值包含在网格中"""
        space = ParamSpace("test", [
            ParamSpec("k", ParamType.INT, 10, low=1, high=13, step=5),
        ])
        grid = generate_grid(space)
        values = {g["k"] for g in grid}
        assert 13 in values  # high should be included even if step doesn't land on it


# ============================================================================
# perturb_params Tests
# ============================================================================


class TestPerturbParams:
    """邻域扰动测试"""

    @pytest.fixture
    def space(self) -> ParamSpace:
        return ParamSpace("test", [
            ParamSpec("top_k", ParamType.INT, 20, low=5, high=50, is_critical=True),
            ParamSpec("stop_loss", ParamType.FLOAT, 0.15, low=0.05, high=0.50),
            ParamSpec("exclude_st", ParamType.BOOL, True),
        ])

    def test_int_perturbation(self, space: ParamSpace):
        """INT 参数扰动"""
        base = {"top_k": 20, "stop_loss": 0.15, "exclude_st": True}
        results = perturb_params(base, space, perturbation_pcts=(-0.10, 0.10))

        top_k_results = [r for r in results if r.get("_perturbed_param") == "top_k"]
        assert len(top_k_results) == 2
        values = {r["top_k"] for r in top_k_results}
        assert 18 in values  # int(round(20 * 0.9))
        assert 22 in values  # int(round(20 * 1.1))

    def test_float_perturbation(self, space: ParamSpace):
        """FLOAT 参数扰动"""
        base = {"top_k": 20, "stop_loss": 0.15, "exclude_st": True}
        results = perturb_params(base, space, perturbation_pcts=(-0.20,))

        sl_results = [r for r in results if r.get("_perturbed_param") == "stop_loss"]
        assert len(sl_results) == 1
        assert sl_results[0]["stop_loss"] == pytest.approx(0.15 * 0.80)

    def test_bool_perturbation(self, space: ParamSpace):
        """BOOL 参数扰动为反值"""
        base = {"top_k": 20, "stop_loss": 0.15, "exclude_st": True}
        results = perturb_params(base, space)

        bool_results = [r for r in results if r.get("_perturbed_param") == "exclude_st"]
        assert len(bool_results) == 1
        assert bool_results[0]["exclude_st"] is False

    def test_choice_perturbation(self):
        """CHOICE 参数扰动为其他选项"""
        space = ParamSpace("test", [
            ParamSpec("mode", ParamType.CHOICE, "a", choices=("a", "b", "c")),
        ])
        base = {"mode": "a"}
        results = perturb_params(base, space)

        assert len(results) == 2  # b and c
        values = {r["mode"] for r in results}
        assert values == {"b", "c"}

    def test_clamping_to_range(self, space: ParamSpace):
        """扰动值应钳位到合法范围"""
        base = {"top_k": 5, "stop_loss": 0.05, "exclude_st": True}
        results = perturb_params(base, space, perturbation_pcts=(-0.50,))

        top_k_results = [r for r in results if r.get("_perturbed_param") == "top_k"]
        # 5 * 0.5 = 2.5 → clamped to low=5 → equals base → skipped
        # Or 5 * 0.5 = 2.5 → round to 3 → clamped to 5 → equals base → skipped
        for r in top_k_results:
            assert r["top_k"] >= 5  # must be >= low

    def test_oat_one_at_a_time(self, space: ParamSpace):
        """每次只扰动一个参数"""
        base = {"top_k": 20, "stop_loss": 0.15, "exclude_st": True}
        results = perturb_params(base, space)

        for r in results:
            param_name = r["_perturbed_param"]
            for spec in space.specs:
                if spec.name != param_name:
                    assert r[spec.name] == base[spec.name], (
                        f"非扰动参数 {spec.name} 不应改变"
                    )

    def test_only_critical(self, space: ParamSpace):
        """only_critical 仅扰动关键参数"""
        base = {"top_k": 20, "stop_loss": 0.15, "exclude_st": True}
        results = perturb_params(base, space, only_critical=True)

        perturbed_names = {r["_perturbed_param"] for r in results}
        assert perturbed_names == {"top_k"}

    def test_skip_no_change(self):
        """扰动后值不变应被跳过"""
        space = ParamSpace("test", [
            ParamSpec("k", ParamType.INT, 1, low=1, high=100),
        ])
        base = {"k": 1}
        # k=1, -20% → 0.8 → int(round(0.8))=1 → clamped to 1 → skip
        results = perturb_params(base, space, perturbation_pcts=(-0.10,))
        k_results = [r for r in results if r["_perturbed_param"] == "k"]
        for r in k_results:
            assert r["k"] != 1  # should be skipped if equals base

    def test_metadata_fields(self, space: ParamSpace):
        """扰动结果应包含元数据"""
        base = {"top_k": 20, "stop_loss": 0.15, "exclude_st": True}
        results = perturb_params(base, space)

        for r in results:
            assert "_perturbed_param" in r
            assert "_perturbation_pct" in r


# ============================================================================
# Strategy.param_space() Integration Tests
# ============================================================================


class TestStrategyParamSpaceIntegration:
    """Strategy 基类与 param_space 集成测试"""

    def test_base_strategy_returns_none(self):
        """Strategy 基类 param_space() 默认返回 None"""
        from aipha.strategy.base import Strategy

        assert Strategy.param_space() is None

    def test_subclass_can_declare_param_space(self):
        """子类可以声明参数空间"""
        from aipha.strategy.base import OrderIntent, Strategy, StrategyContext

        class TestStrategy(Strategy):
            name = "test_with_params"

            @classmethod
            def param_space(cls) -> ParamSpace:
                return ParamSpace("test_with_params", [
                    ParamSpec("k", ParamType.INT, 10, low=5, high=50),
                ])

            def on_bar(self, bars, context):
                return []

        space = TestStrategy.param_space()
        assert space is not None
        assert space.strategy_name == "test_with_params"
        assert len(space.specs) == 1

    def test_subclass_without_param_space(self):
        """未声明 param_space 的子类返回 None"""
        from aipha.strategy.base import Strategy

        class SimpleStrategy(Strategy):
            name = "simple"

            def on_bar(self, bars, context):
                return []

        assert SimpleStrategy.param_space() is None
