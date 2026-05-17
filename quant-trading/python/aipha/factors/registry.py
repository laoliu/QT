"""因子注册表

提供因子的工厂模式和注册机制，支持：
- 通过名称创建因子实例
- 从配置（dict / YAML）创建因子
- 按类别查询可用因子
- 自动发现与注册内置因子

设计参考 :class:`aipha.strategy.registry.StrategyRegistry`，保持一致的
注册/查找/创建 API 范式。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Type, TypeVar

from aipha.factors.base import Factor

logger = logging.getLogger(__name__)

FactorT = TypeVar("FactorT", bound=Factor)


# ============================================================================
# 因子元信息
# ============================================================================


class FactorMeta:
    """因子注册元信息

    Attributes:
        cls: 因子类
        category: 因子分类（value / quality / growth / momentum / size /
                  volatility / liquidity / cb / technical）
        description: 因子简要说明
    """

    __slots__ = ("cls", "category", "description")

    def __init__(
        self,
        cls: Type[Factor],
        category: str = "other",
        description: str = "",
    ) -> None:
        self.cls = cls
        self.category = category
        self.description = description

    def __repr__(self) -> str:
        return (
            f"FactorMeta(cls={self.cls.__name__!r}, "
            f"category={self.category!r})"
        )


# ============================================================================
# 注册表
# ============================================================================


class FactorRegistry:
    """因子注册表 — 单例模式"""

    _instance: FactorRegistry | None = None
    _factors: dict[str, FactorMeta]

    def __new__(cls) -> FactorRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._factors = {}
        return cls._instance

    # ------------------------------------------------------------------
    # 注册
    # ------------------------------------------------------------------

    def register(
        self,
        name: str | None = None,
        *,
        category: str = "other",
        description: str = "",
    ) -> Callable[[Type[FactorT]], Type[FactorT]]:
        """装饰器方式注册因子类

        Example::

            @factor_registry.register("pe", category="value")
            class PEFactor(Factor): ...
        """

        def decorator(cls: Type[FactorT]) -> Type[FactorT]:
            factor_name = name or cls.__name__
            if factor_name in self._factors:
                logger.warning("因子 '%s' 已存在，将被覆盖", factor_name)
            self._factors[factor_name] = FactorMeta(
                cls=cls, category=category, description=description,
            )
            logger.debug("注册因子: %s -> %s", factor_name, cls.__name__)
            return cls

        return decorator

    def register_class(
        self,
        name: str,
        cls: Type[Factor],
        *,
        category: str = "other",
        description: str = "",
    ) -> None:
        """直接注册因子类（非装饰器）"""
        if name in self._factors:
            logger.warning("因子 '%s' 已存在，将被覆盖", name)
        self._factors[name] = FactorMeta(
            cls=cls, category=category, description=description,
        )

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def get(self, name: str) -> Type[Factor] | None:
        """按名称获取因子类（不存在返回 None）"""
        meta = self._factors.get(name)
        return meta.cls if meta is not None else None

    def get_meta(self, name: str) -> FactorMeta | None:
        """按名称获取因子元信息"""
        return self._factors.get(name)

    def list_factors(self) -> list[str]:
        """列出所有已注册因子名称"""
        return list(self._factors.keys())

    def list_by_category(self, category: str) -> list[str]:
        """列出指定分类下的所有因子名称"""
        return [
            name
            for name, meta in self._factors.items()
            if meta.category == category
        ]

    def categories(self) -> list[str]:
        """列出所有已注册分类（去重排序）"""
        return sorted({meta.category for meta in self._factors.values()})

    # ------------------------------------------------------------------
    # 创建
    # ------------------------------------------------------------------

    def create(self, name: str, **kwargs: Any) -> Factor:
        """按名称创建因子实例

        Args:
            name: 已注册的因子名称
            **kwargs: 传递给因子构造函数的参数

        Raises:
            KeyError: 因子未注册
            TypeError: 构造参数错误
        """
        meta = self._factors.get(name)
        if meta is None:
            available = self.list_factors()
            raise KeyError(
                f"因子 '{name}' 未注册。可用因子: {available}"
            )
        try:
            return meta.cls(**kwargs)
        except TypeError as e:
            raise TypeError(
                f"创建因子 '{name}' 失败：参数错误 - {e}"
            ) from e

    # ------------------------------------------------------------------
    # 重置（测试用）
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """清空注册表（仅限测试使用）"""
        self._factors.clear()


# ============================================================================
# 模块级便捷函数
# ============================================================================

_registry = FactorRegistry()


def register_factor(
    name: str | None = None,
    *,
    category: str = "other",
    description: str = "",
) -> Callable[[Type[FactorT]], Type[FactorT]]:
    """装饰器方式注册因子"""
    return _registry.register(name, category=category, description=description)


def get_factor(name: str) -> Type[Factor] | None:
    """按名称获取因子类"""
    return _registry.get(name)


def get_factor_meta(name: str) -> FactorMeta | None:
    """按名称获取因子元信息"""
    return _registry.get_meta(name)


def create_factor(name: str, **kwargs: Any) -> Factor:
    """按名称创建因子实例"""
    return _registry.create(name, **kwargs)


def create_factor_from_config(config: dict[str, Any]) -> Factor:
    """从配置字典创建因子

    Args:
        config: 必须包含 ``"name"`` 键，可选 ``"params"`` 字典

    Example::

        config = {"name": "pe", "params": {"use_ttm": True, "invert": True}}
        factor = create_factor_from_config(config)
    """
    name = config.get("name")
    if not name:
        raise ValueError("配置必须包含 'name' 键")
    params = config.get("params", {})
    return create_factor(name, **params)


def list_factors() -> list[str]:
    """列出所有已注册因子名称"""
    return _registry.list_factors()


def list_factors_by_category(category: str) -> list[str]:
    """列出指定分类下的所有因子名称"""
    return _registry.list_by_category(category)


def factor_categories() -> list[str]:
    """列出所有因子分类"""
    return _registry.categories()


def _reset_factor_registry() -> None:
    """重置注册表（仅测试使用）"""
    _registry.clear()


# ============================================================================
# 自动注册内置因子
# ============================================================================


def _register_builtin_factors() -> None:
    """注册所有内置因子

    使用 register_class() 直接注册，避免在各因子模块上添加装饰器
    保持源码清洁、松耦合。
    """

    # --- 价值因子 ---
    from aipha.factors.value import PBFactor, PEFactor, PSFactor

    _registry.register_class(
        "pe", PEFactor, category="value", description="市盈率因子 (P/E)",
    )
    _registry.register_class(
        "pb", PBFactor, category="value", description="市净率因子 (P/B)",
    )
    _registry.register_class(
        "ps", PSFactor, category="value", description="市销率因子 (P/S)",
    )
    # 别名：倒数形式
    _registry.register_class(
        "ep", PEFactor, category="value", description="EP (= 1/PE)",
    )
    _registry.register_class(
        "bp", PBFactor, category="value", description="BP (= 1/PB)",
    )
    _registry.register_class(
        "sp", PSFactor, category="value", description="SP (= 1/PS)",
    )

    # --- 质量因子 ---
    from aipha.factors.quality import (
        GrossMarginFactor,
        NetMarginFactor,
        OperatingCashFlowQualityFactor,
        ROAFactor,
        ROEFactor,
    )

    _registry.register_class(
        "roe", ROEFactor, category="quality", description="净资产收益率",
    )
    _registry.register_class(
        "roa", ROAFactor, category="quality", description="总资产收益率",
    )
    _registry.register_class(
        "gross_margin", GrossMarginFactor,
        category="quality", description="毛利率",
    )
    _registry.register_class(
        "net_margin", NetMarginFactor,
        category="quality", description="净利率",
    )
    _registry.register_class(
        "cfo_quality", OperatingCashFlowQualityFactor,
        category="quality", description="经营现金流质量",
    )

    # --- 成长因子 ---
    from aipha.factors.growth import (
        EarningsGrowthFactor,
        RevenueAccelerationFactor,
        RevenueGrowthFactor,
        SUEFactor,
    )

    _registry.register_class(
        "revenue_growth", RevenueGrowthFactor,
        category="growth", description="营收同比增长率",
    )
    _registry.register_class(
        "earnings_growth", EarningsGrowthFactor,
        category="growth", description="净利润同比增长率",
    )
    _registry.register_class(
        "revenue_acceleration", RevenueAccelerationFactor,
        category="growth", description="营收增长加速度",
    )
    _registry.register_class(
        "sue", SUEFactor, category="growth", description="标准化未预期盈利",
    )

    # --- 动量因子 ---
    from aipha.factors.momentum import ReturnMomentumFactor
    from aipha.factors.residual_momentum import ResidualMomentumFactor

    _registry.register_class(
        "momentum", ReturnMomentumFactor,
        category="momentum", description="收益率动量",
    )
    _registry.register_class(
        "residual_momentum", ResidualMomentumFactor,
        category="momentum", description="残差动量 (Blitz 2011)",
    )

    # --- 规模因子 ---
    from aipha.factors.size import MarketCapFactor, TurnoverRateFactor

    _registry.register_class(
        "market_cap", MarketCapFactor,
        category="size", description="流通市值",
    )
    _registry.register_class(
        "small_cap", MarketCapFactor,
        category="size", description="小盘因子 (市值倒数)",
    )
    _registry.register_class(
        "turnover_rate", TurnoverRateFactor,
        category="size", description="换手率",
    )

    # --- 波动因子 ---
    from aipha.factors.volatility import VolatilityFactor

    _registry.register_class(
        "volatility", VolatilityFactor,
        category="volatility", description="波动率",
    )
    _registry.register_class(
        "low_vol", VolatilityFactor,
        category="volatility", description="低波因子 (波动率倒数)",
    )

    # --- 流动性因子 ---
    from aipha.factors.liquidity import (
        AmihudIlliquidityFactor,
        TurnoverMomentumFactor,
        VolumeRatioFactor,
    )

    _registry.register_class(
        "amihud", AmihudIlliquidityFactor,
        category="liquidity", description="Amihud 非流动性因子",
    )
    _registry.register_class(
        "turnover_momentum", TurnoverMomentumFactor,
        category="liquidity", description="换手率动量",
    )
    _registry.register_class(
        "volume_ratio", VolumeRatioFactor,
        category="liquidity", description="相对成交量比率",
    )

    # --- 可转债因子 ---
    from aipha.factors.putback_clause import PutbackProximityFactor

    _registry.register_class(
        "putback_proximity", PutbackProximityFactor,
        category="cb", description="下修接近度因子",
    )

    # --- 股息因子 ---
    from aipha.factors.dividend import DividendGrowthFactor, DividendYieldFactor

    _registry.register_class(
        "dividend_yield", DividendYieldFactor,
        category="dividend", description="股息率因子",
    )
    _registry.register_class(
        "dividend_yield_ttm", DividendYieldFactor,
        category="dividend", description="股息率因子 (TTM)",
    )
    _registry.register_class(
        "dividend_growth", DividendGrowthFactor,
        category="dividend", description="股息增长因子（连续增长年数）",
    )
    _registry.register_class(
        "dividend_growth_cagr", DividendGrowthFactor,
        category="dividend", description="股息增长因子 (CAGR)",
    )

    # --- 估值百分位因子 ---
    from aipha.factors.value import ValuationPercentileFactor

    _registry.register_class(
        "pe_percentile", ValuationPercentileFactor,
        category="value", description="PE 历史百分位（低百分位=便宜=高因子值）",
    )
    _registry.register_class(
        "pb_percentile", ValuationPercentileFactor,
        category="value", description="PB 历史百分位",
    )

    # --- 高阶质量因子 ---
    from aipha.factors.quality_advanced import (
        AccrualsQualityFactor,
        DebtQualityFactor,
        EarningsStabilityFactor,
        PiotroskiFScoreFactor,
    )

    _registry.register_class(
        "piotroski_f", PiotroskiFScoreFactor,
        category="quality", description="Piotroski F-Score (0-9)",
    )
    _registry.register_class(
        "accruals_quality", AccrualsQualityFactor,
        category="quality", description="应计盈余质量（低应计=高质量）",
    )
    _registry.register_class(
        "debt_quality", DebtQualityFactor,
        category="quality", description="负债质量复合（流动性+低杠杆）",
    )
    _registry.register_class(
        "earnings_stability", EarningsStabilityFactor,
        category="quality", description="盈利稳定性（低 CV = 高稳定）",
    )

    # --- 分析师因子（接口预留）---
    from aipha.factors.analyst import AnalystConsensusFactor, EarningsRevisionFactor

    _registry.register_class(
        "analyst_consensus", AnalystConsensusFactor,
        category="analyst", description="分析师一致预期（接口预留）",
    )
    _registry.register_class(
        "earnings_revision", EarningsRevisionFactor,
        category="analyst", description="盈利修正方向（接口预留）",
    )

    logger.debug(
        "已注册 %d 个内置因子，分布在 %d 个类别中",
        len(_registry.list_factors()),
        len(_registry.categories()),
    )


_register_builtin_factors()
