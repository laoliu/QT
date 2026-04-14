"""测试策略 param_space() 实现。

验证 5 个核心策略正确声明了参数空间：
- MomentumStrategy
- MeanReversionStrategy
- DualMAStrategy
- FactorBasedStrategy (factor_based)
- EarningsMomentumStrategy
"""

import pytest

from aipha.strategy.param_space import ParamSpace, ParamSpec, ParamType, generate_grid


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _assert_valid_param_space(ps: ParamSpace, expected_name: str, min_specs: int):
    """通用断言：ParamSpace 基本有效性。"""
    assert isinstance(ps, ParamSpace)
    assert ps.strategy_name == expected_name
    assert len(ps.specs) >= min_specs
    # 所有 spec 是 ParamSpec
    for s in ps.specs:
        assert isinstance(s, ParamSpec)
    # names 唯一
    names = ps.names
    assert len(names) == len(set(names)), f"参数名重复: {names}"
    # defaults 返回字典且长度匹配
    d = ps.defaults()
    assert len(d) == len(ps.specs)
    # 至少有一个关键参数
    assert len(ps.critical_params()) >= 1


# ===========================================================================
# MomentumStrategy
# ===========================================================================


class TestMomentumParamSpace:
    @pytest.fixture()
    def ps(self):
        from aipha.strategy.momentum import MomentumStrategy
        return MomentumStrategy.param_space()

    def test_basic(self, ps):
        _assert_valid_param_space(ps, "momentum", 3)

    def test_defaults_match(self, ps):
        d = ps.defaults()
        assert d["lookback_days"] == 20
        assert d["top_k"] == 5
        assert d["rebalance_days"] == 5

    def test_critical_params(self, ps):
        crit = {s.name for s in ps.critical_params()}
        assert "lookback_days" in crit
        assert "top_k" in crit

    def test_generate_grid(self, ps):
        grid = generate_grid(ps)
        assert len(grid) > 1
        # 每个组合都是字典
        for combo in grid:
            assert isinstance(combo, dict)
            assert set(combo.keys()) == set(ps.names)

    def test_get_spec(self, ps):
        spec = ps.get("lookback_days")
        assert spec is not None
        assert spec.param_type == ParamType.INT
        assert spec.low == 5
        assert spec.high == 60


# ===========================================================================
# MeanReversionStrategy
# ===========================================================================


class TestMeanReversionParamSpace:
    @pytest.fixture()
    def ps(self):
        from aipha.strategy.mean_reversion import MeanReversionStrategy
        return MeanReversionStrategy.param_space()

    def test_basic(self, ps):
        _assert_valid_param_space(ps, "mean_reversion", 5)

    def test_defaults_match(self, ps):
        d = ps.defaults()
        assert d["lookback_days"] == 20
        assert d["entry_std"] == 2.0
        assert d["exit_std"] == 0.5
        assert d["max_positions"] == 10
        assert d["position_pct"] == 0.10

    def test_critical_params(self, ps):
        crit = {s.name for s in ps.critical_params()}
        assert "entry_std" in crit
        assert "exit_std" in crit

    def test_float_spec_range(self, ps):
        spec = ps.get("entry_std")
        assert spec.param_type == ParamType.FLOAT
        assert spec.low == 1.0
        assert spec.high == 3.5


# ===========================================================================
# DualMAStrategy
# ===========================================================================


class TestDualMAParamSpace:
    @pytest.fixture()
    def ps(self):
        from aipha.strategy.dual_ma import DualMAStrategy
        return DualMAStrategy.param_space()

    def test_basic(self, ps):
        _assert_valid_param_space(ps, "dual_ma", 4)

    def test_defaults_match(self, ps):
        d = ps.defaults()
        assert d["short_window"] == 5
        assert d["long_window"] == 20
        assert d["position_pct"] == 0.10
        assert d["max_positions"] == 10

    def test_critical_params(self, ps):
        crit = {s.name for s in ps.critical_params()}
        assert "short_window" in crit
        assert "long_window" in crit

    def test_generate_grid_size(self, ps):
        grid = generate_grid(ps)
        # short_window: 2-20 step 1 = 19 values
        # long_window: 10-120 step 5 = 23 values
        # grid should be large
        assert len(grid) > 100


# ===========================================================================
# FactorBasedStrategy
# ===========================================================================


class TestFactorBasedParamSpace:
    @pytest.fixture()
    def ps(self):
        from aipha.strategy.factor_strategy import FactorBasedStrategy
        return FactorBasedStrategy.param_space()

    def test_basic(self, ps):
        _assert_valid_param_space(ps, "factor_based", 4)

    def test_defaults_match(self, ps):
        d = ps.defaults()
        assert d["top_k"] == 10
        assert d["rebalance_days"] == 20
        assert d["lookback_days"] == 60
        assert d["weight_method"] == "equal"

    def test_choice_spec(self, ps):
        wm = ps.get("weight_method")
        assert wm is not None
        assert wm.param_type == ParamType.CHOICE
        assert "equal" in wm.choices
        assert "risk_parity" in wm.choices
        assert len(wm.choices) == 5

    def test_grid_includes_all_methods(self, ps):
        grid = generate_grid(ps)
        methods = {c["weight_method"] for c in grid}
        assert methods == {"equal", "factor_score", "min_variance",
                           "risk_parity", "max_sharpe"}


# ===========================================================================
# EarningsMomentumStrategy
# ===========================================================================


class TestEarningsMomentumParamSpace:
    @pytest.fixture()
    def ps(self):
        from aipha.strategy.earnings_momentum import EarningsMomentumStrategy
        return EarningsMomentumStrategy.param_space()

    def test_basic(self, ps):
        _assert_valid_param_space(ps, "earnings_momentum", 6)

    def test_defaults_match(self, ps):
        d = ps.defaults()
        assert d["top_k"] == 25
        assert d["rebalance_days"] == 15
        assert d["stop_loss_pct"] == 0.15
        assert d["weight_earnings_accel"] == 0.30

    def test_critical_params(self, ps):
        crit = {s.name for s in ps.critical_params()}
        assert "top_k" in crit
        assert "stop_loss_pct" in crit

    def test_float_ranges_valid(self, ps):
        for spec in ps.specs:
            if spec.param_type == ParamType.FLOAT:
                assert spec.low < spec.high
                assert spec.low <= spec.default <= spec.high


# ===========================================================================
# 跨策略集成
# ===========================================================================


class TestCrossStrategyParamSpace:
    """跨策略 param_space 集成测试。"""

    STRATEGY_CLASSES = None

    @pytest.fixture(autouse=True)
    def _load(self):
        from aipha.strategy.momentum import MomentumStrategy
        from aipha.strategy.mean_reversion import MeanReversionStrategy
        from aipha.strategy.dual_ma import DualMAStrategy
        from aipha.strategy.factor_strategy import FactorBasedStrategy
        from aipha.strategy.earnings_momentum import EarningsMomentumStrategy
        self.__class__.STRATEGY_CLASSES = [
            MomentumStrategy,
            MeanReversionStrategy,
            DualMAStrategy,
            FactorBasedStrategy,
            EarningsMomentumStrategy,
        ]

    def test_all_return_param_space(self):
        for cls in self.STRATEGY_CLASSES:
            ps = cls.param_space()
            assert isinstance(ps, ParamSpace), f"{cls.__name__} 未返回 ParamSpace"

    def test_all_have_unique_strategy_names(self):
        names = [cls.param_space().strategy_name for cls in self.STRATEGY_CLASSES]
        assert len(names) == len(set(names))

    def test_all_generate_grid(self):
        for cls in self.STRATEGY_CLASSES:
            ps = cls.param_space()
            grid = generate_grid(ps)
            assert len(grid) >= 1, f"{cls.__name__} grid 为空"

    def test_default_in_grid(self):
        """默认参数组合必须在网格中。"""
        for cls in self.STRATEGY_CLASSES:
            ps = cls.param_space()
            defaults = ps.defaults()
            grid = generate_grid(ps)
            # 默认值应该是网格中的一个点
            found = any(
                all(combo.get(k) == v for k, v in defaults.items())
                for combo in grid
            )
            assert found, (
                f"{ps.strategy_name} 默认参数 {defaults} 不在网格中"
            )
