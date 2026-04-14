"""因子注册表测试

覆盖：
- FactorRegistry 单例 / 注册 / 查询 / 创建 / 分类
- 模块级便捷函数
- create_factor_from_config 配置驱动
- 内置因子自动注册
"""

from __future__ import annotations

from datetime import date

import pytest

from aipha.factors.base import Factor, FactorData, FactorResult
from aipha.factors.registry import (
    FactorMeta,
    FactorRegistry,
    _reset_factor_registry,
    create_factor,
    create_factor_from_config,
    factor_categories,
    get_factor,
    get_factor_meta,
    list_factors,
    list_factors_by_category,
    register_factor,
)


# ============================================================================
# 辅助：自定义因子用于测试
# ============================================================================


class _DummyFactor(Factor):
    """测试用 dummy 因子"""

    def __init__(self, window: int = 10) -> None:
        self.window = window

    @property
    def name(self) -> str:
        return f"DUMMY_{self.window}"

    def compute(self, data: FactorData) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=float(self.window),
        )


class _AnotherFactor(Factor):
    """第二个 dummy 因子"""

    @property
    def name(self) -> str:
        return "ANOTHER"

    def compute(self, data: FactorData) -> FactorResult:
        return FactorResult(
            symbol=data.symbol,
            trade_date=data.trade_date,
            factor_name=self.name,
            value=0.0,
        )


# ============================================================================
# 测试：FactorRegistry 核心
# ============================================================================


class TestFactorRegistry:
    """FactorRegistry 单例/注册/查询/创建"""

    def test_singleton(self) -> None:
        r1 = FactorRegistry()
        r2 = FactorRegistry()
        assert r1 is r2

    def test_register_decorator(self) -> None:
        reg = FactorRegistry()
        original_count = len(reg.list_factors())

        @reg.register("test_dummy_dec", category="test")
        class _DecFactor(_DummyFactor):
            pass

        assert "test_dummy_dec" in reg.list_factors()
        assert reg.get("test_dummy_dec") is _DecFactor
        # cleanup
        reg._factors.pop("test_dummy_dec", None)
        assert len(reg.list_factors()) == original_count

    def test_register_class(self) -> None:
        reg = FactorRegistry()
        reg.register_class(
            "test_dummy_cls", _DummyFactor,
            category="test", description="desc",
        )
        assert reg.get("test_dummy_cls") is _DummyFactor
        meta = reg.get_meta("test_dummy_cls")
        assert meta is not None
        assert meta.category == "test"
        assert meta.description == "desc"
        reg._factors.pop("test_dummy_cls", None)

    def test_get_nonexistent(self) -> None:
        reg = FactorRegistry()
        assert reg.get("nonexistent_xyz") is None

    def test_get_meta_nonexistent(self) -> None:
        reg = FactorRegistry()
        assert reg.get_meta("nonexistent_xyz") is None

    def test_create(self) -> None:
        reg = FactorRegistry()
        reg.register_class("test_create", _DummyFactor, category="test")
        factor = reg.create("test_create", window=5)
        assert isinstance(factor, _DummyFactor)
        assert factor.window == 5
        assert factor.name == "DUMMY_5"
        reg._factors.pop("test_create", None)

    def test_create_default_params(self) -> None:
        reg = FactorRegistry()
        reg.register_class("test_default", _DummyFactor, category="test")
        factor = reg.create("test_default")
        assert factor.window == 10
        reg._factors.pop("test_default", None)

    def test_create_nonexistent_raises(self) -> None:
        reg = FactorRegistry()
        with pytest.raises(KeyError, match="未注册"):
            reg.create("absolutely_not_registered_999")

    def test_create_bad_params_raises(self) -> None:
        reg = FactorRegistry()
        reg.register_class("test_bad", _DummyFactor, category="test")
        with pytest.raises(TypeError, match="参数错误"):
            reg.create("test_bad", nonexistent_param=42)
        reg._factors.pop("test_bad", None)

    def test_list_factors(self) -> None:
        reg = FactorRegistry()
        names = reg.list_factors()
        assert isinstance(names, list)
        # 至少有内置因子
        assert len(names) > 0

    def test_clear(self) -> None:
        reg = FactorRegistry()
        reg.register_class("test_clear", _DummyFactor, category="test")
        assert "test_clear" in reg.list_factors()
        saved = dict(reg._factors)
        reg.clear()
        assert len(reg.list_factors()) == 0
        # restore
        reg._factors.update(saved)


# ============================================================================
# 测试：分类查询
# ============================================================================


class TestFactorCategories:
    """按类别查询因子"""

    def test_list_by_category_value(self) -> None:
        value_factors = list_factors_by_category("value")
        assert "pe" in value_factors
        assert "pb" in value_factors
        assert "ps" in value_factors

    def test_list_by_category_quality(self) -> None:
        quality = list_factors_by_category("quality")
        assert "roe" in quality
        assert "roa" in quality
        assert "gross_margin" in quality

    def test_list_by_category_growth(self) -> None:
        growth = list_factors_by_category("growth")
        assert "revenue_growth" in growth
        assert "earnings_growth" in growth
        assert "sue" in growth

    def test_list_by_category_momentum(self) -> None:
        mom = list_factors_by_category("momentum")
        assert "momentum" in mom
        assert "residual_momentum" in mom

    def test_list_by_category_empty(self) -> None:
        result = list_factors_by_category("nonexistent_category")
        assert result == []

    def test_categories(self) -> None:
        cats = factor_categories()
        assert isinstance(cats, list)
        assert "value" in cats
        assert "quality" in cats
        assert "growth" in cats
        assert "momentum" in cats
        assert "size" in cats
        assert "volatility" in cats
        assert "liquidity" in cats
        assert "cb" in cats

    def test_categories_sorted(self) -> None:
        cats = factor_categories()
        assert cats == sorted(cats)


# ============================================================================
# 测试：模块级便捷函数
# ============================================================================


class TestModuleFunctions:
    """模块级便捷函数"""

    def test_get_factor(self) -> None:
        cls = get_factor("pe")
        assert cls is not None
        from aipha.factors.value import PEFactor

        assert cls is PEFactor

    def test_get_factor_meta(self) -> None:
        meta = get_factor_meta("momentum")
        assert meta is not None
        assert meta.category == "momentum"

    def test_create_factor(self) -> None:
        factor = create_factor("momentum", lookback_days=30)
        assert factor.name == "MOM_30"

    def test_list_factors_returns_list(self) -> None:
        names = list_factors()
        assert isinstance(names, list)
        assert "pe" in names

    def test_register_factor_decorator(self) -> None:
        @register_factor("test_mod_func", category="test")
        class _TestFac(_DummyFactor):
            pass

        assert get_factor("test_mod_func") is _TestFac
        # cleanup
        FactorRegistry()._factors.pop("test_mod_func", None)


# ============================================================================
# 测试：create_factor_from_config
# ============================================================================


class TestCreateFactorFromConfig:
    """配置驱动创建因子"""

    def test_basic(self) -> None:
        config = {"name": "pe", "params": {"use_ttm": True, "invert": True}}
        factor = create_factor_from_config(config)
        assert factor.name == "EP"

    def test_no_params(self) -> None:
        config = {"name": "roa"}
        factor = create_factor_from_config(config)
        assert factor.name == "ROA"

    def test_with_params(self) -> None:
        config = {
            "name": "volatility",
            "params": {"lookback_days": 60, "invert": True},
        }
        factor = create_factor_from_config(config)
        assert factor.name == "LOW_VOL_60"

    def test_missing_name_raises(self) -> None:
        with pytest.raises(ValueError, match="name"):
            create_factor_from_config({"params": {}})

    def test_empty_name_raises(self) -> None:
        with pytest.raises(ValueError, match="name"):
            create_factor_from_config({"name": ""})

    def test_unknown_factor_raises(self) -> None:
        with pytest.raises(KeyError, match="未注册"):
            create_factor_from_config({"name": "unknown_xyz"})


# ============================================================================
# 测试：内置因子自动注册
# ============================================================================


class TestBuiltinRegistration:
    """内置因子自动注册验证"""

    EXPECTED_FACTORS = [
        # value
        "pe", "pb", "ps", "ep", "bp", "sp",
        # quality
        "roe", "roa", "gross_margin", "net_margin", "cfo_quality",
        # growth
        "revenue_growth", "earnings_growth", "revenue_acceleration", "sue",
        # momentum
        "momentum", "residual_momentum",
        # size
        "market_cap", "small_cap", "turnover_rate",
        # volatility
        "volatility", "low_vol",
        # liquidity
        "amihud", "turnover_momentum", "volume_ratio",
        # cb
        "putback_proximity",
    ]

    def test_all_expected_factors_registered(self) -> None:
        registered = list_factors()
        for name in self.EXPECTED_FACTORS:
            assert name in registered, f"因子 '{name}' 未注册"

    def test_total_count(self) -> None:
        """至少 27 个内置注册（含别名）"""
        assert len(list_factors()) >= 27

    def test_each_can_be_created(self) -> None:
        """每个已注册因子都可以用默认参数实例化"""
        for name in self.EXPECTED_FACTORS:
            factor = create_factor(name)
            assert isinstance(factor, Factor)
            assert factor.name  # 非空

    def test_value_factors_correct_type(self) -> None:
        from aipha.factors.value import PBFactor, PEFactor, PSFactor

        assert isinstance(create_factor("pe"), PEFactor)
        assert isinstance(create_factor("pb"), PBFactor)
        assert isinstance(create_factor("ps"), PSFactor)

    def test_quality_factors_correct_type(self) -> None:
        from aipha.factors.quality import ROAFactor, ROEFactor

        assert isinstance(create_factor("roe"), ROEFactor)
        assert isinstance(create_factor("roa"), ROAFactor)

    def test_momentum_factors_correct_type(self) -> None:
        from aipha.factors.momentum import ReturnMomentumFactor
        from aipha.factors.residual_momentum import ResidualMomentumFactor

        assert isinstance(create_factor("momentum"), ReturnMomentumFactor)
        assert isinstance(
            create_factor("residual_momentum"), ResidualMomentumFactor,
        )

    def test_liquidity_factors_correct_type(self) -> None:
        from aipha.factors.liquidity import (
            AmihudIlliquidityFactor,
            TurnoverMomentumFactor,
            VolumeRatioFactor,
        )

        assert isinstance(create_factor("amihud"), AmihudIlliquidityFactor)
        assert isinstance(
            create_factor("turnover_momentum"), TurnoverMomentumFactor,
        )
        assert isinstance(create_factor("volume_ratio"), VolumeRatioFactor)

    def test_cb_factor_correct_type(self) -> None:
        from aipha.factors.putback_clause import PutbackProximityFactor

        assert isinstance(
            create_factor("putback_proximity"), PutbackProximityFactor,
        )

    def test_aliases_resolve_to_same_class(self) -> None:
        """别名应指向相同的因子类"""
        assert get_factor("pe") is get_factor("ep")
        assert get_factor("pb") is get_factor("bp")
        assert get_factor("ps") is get_factor("sp")
        assert get_factor("market_cap") is get_factor("small_cap")
        assert get_factor("volatility") is get_factor("low_vol")


# ============================================================================
# 测试：FactorMeta
# ============================================================================


class TestFactorMeta:
    """FactorMeta 数据结构"""

    def test_repr(self) -> None:
        meta = FactorMeta(cls=_DummyFactor, category="test", description="d")
        r = repr(meta)
        assert "_DummyFactor" in r
        assert "test" in r

    def test_attrs(self) -> None:
        meta = FactorMeta(cls=_DummyFactor, category="cat", description="desc")
        assert meta.cls is _DummyFactor
        assert meta.category == "cat"
        assert meta.description == "desc"


# ============================================================================
# 测试：因子计算端到端
# ============================================================================


class TestFactorComputeE2E:
    """通过注册表创建因子并执行计算"""

    @pytest.fixture()
    def sample_data(self) -> FactorData:
        return FactorData(
            symbol="000001.SZ",
            trade_date=date(2024, 1, 15),
            close=10.5,
            volume=1_000_000.0,
            returns=[0.01, -0.02, 0.015, 0.005, -0.01] * 4,
            historical_close={
                date(2024, 1, i): 10.0 + i * 0.1 for i in range(1, 16)
            },
        )

    def test_momentum_compute(self, sample_data: FactorData) -> None:
        factor = create_factor("momentum", lookback_days=5)
        result = factor.compute(sample_data)
        assert result.symbol == "000001.SZ"
        assert result.factor_name == "MOM_5"
        assert isinstance(result.value, float)

    def test_volatility_compute(self, sample_data: FactorData) -> None:
        factor = create_factor("volatility", lookback_days=10)
        result = factor.compute(sample_data)
        assert result.factor_name == "VOL_10"
        assert result.value > 0

    def test_config_driven_compute(self, sample_data: FactorData) -> None:
        config = {"name": "momentum", "params": {"lookback_days": 10}}
        factor = create_factor_from_config(config)
        result = factor.compute(sample_data)
        assert result.factor_name == "MOM_10"
