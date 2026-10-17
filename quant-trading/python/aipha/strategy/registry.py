"""策略工厂与注册机制

提供策略的工厂模式和注册机制，支持：
- 通过名称创建策略
- 从配置创建策略
- 自动发现和注册策略

设计原则：
- 策略通过名称查找，支持配置驱动（YAML/JSON）
- 所有策略必须继承 Strategy 基类
- 支持参数验证
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Type, TypeVar

from aipha.strategy.base import Strategy, TickStrategy

logger = logging.getLogger(__name__)

# 类型变量
StrategyT = TypeVar("StrategyT", bound=Strategy)


class StrategyRegistry:
    """策略注册表

    单例模式的策略注册器，用于管理策略类型的注册与查找。

    Example:
        >>> registry = StrategyRegistry()
        >>> @registry.register("my_strategy")
        ... class MyStrategy(Strategy):
        ...     pass
        >>> strategy = registry.create("my_strategy", param1=10)
    """

    _instance: "StrategyRegistry | None" = None
    _strategies: dict[str, Type[Strategy]]

    def __new__(cls) -> "StrategyRegistry":
        """单例模式"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._strategies = {}
        return cls._instance

    def register(
        self,
        name: str | None = None,
    ) -> Callable[[Type[StrategyT]], Type[StrategyT]]:
        """注册策略装饰器

        Args:
            name: 策略名称，默认使用类名

        Returns:
            装饰器函数

        Example:
            >>> @registry.register("momentum")
            ... class MomentumStrategy(Strategy):
            ...     pass
        """

        def decorator(cls: Type[StrategyT]) -> Type[StrategyT]:
            strategy_name = name or cls.__name__
            if strategy_name in self._strategies:
                logger.warning(f"策略 '{strategy_name}' 已存在，将被覆盖")
            self._strategies[strategy_name] = cls
            logger.debug(f"注册策略: {strategy_name} -> {cls.__name__}")
            return cls

        return decorator

    def register_class(self, name: str, cls: Type[Strategy]) -> None:
        """直接注册策略类

        Args:
            name: 策略名称
            cls: 策略类
        """
        if name in self._strategies:
            logger.warning(f"策略 '{name}' 已存在，将被覆盖")
        self._strategies[name] = cls

    def get(self, name: str) -> Type[Strategy] | None:
        """获取策略类

        Args:
            name: 策略名称

        Returns:
            策略类，不存在则返回 None
        """
        return self._strategies.get(name)

    def create(self, name: str, **kwargs: Any) -> Strategy:
        """创建策略实例

        Args:
            name: 策略名称
            **kwargs: 策略参数

        Returns:
            策略实例

        Raises:
            KeyError: 策略未注册
            TypeError: 参数错误
        """
        strategy_cls = self._strategies.get(name)
        if strategy_cls is None:
            available = list(self._strategies.keys())
            raise KeyError(
                f"策略 '{name}' 未注册。可用策略: {available}"
            )

        try:
            return strategy_cls(**kwargs)
        except TypeError as e:
            raise TypeError(
                f"创建策略 '{name}' 失败：参数错误 - {e}"
            ) from e

    def list_strategies(self) -> list[str]:
        """列出所有已注册的策略（包含兼容入口）。"""
        return list(self._strategies.keys())

    def list_discoverable_strategies(self) -> list[str]:
        """List strategies suitable for new research/discovery surfaces.

        Legacy strategy classes remain constructible by explicit name for old
        configurations, but ML entries that point at ``ml_ranking`` are hidden
        from the default discovery surface.
        """
        return [
            name for name, strategy in self._strategies.items()
            if not (
                getattr(strategy, "canonical_entrypoint", None) == "ml_ranking"
                and getattr(strategy, "lifecycle", "compatibility") != "primary"
            )
        ]

    def clear(self) -> None:
        """清空注册表（主要用于测试）"""
        self._strategies.clear()


class TickStrategyRegistry(StrategyRegistry):
    """Separate namespace so tick strategies cannot enter a bar engine."""

    _instance = None

    def register(self, name=None):
        parent = super().register(name)

        def decorator(cls):
            if not issubclass(cls, TickStrategy):
                raise TypeError("Tick strategies must inherit TickStrategy")
            return parent(cls)

        return decorator


# 全局注册表实例
_registry = StrategyRegistry()


def register(name: str | None = None) -> Callable[[Type[StrategyT]], Type[StrategyT]]:
    """注册策略的装饰器（便捷函数）

    Args:
        name: 策略名称，默认使用类名

    Returns:
        装饰器

    Example:
        >>> from aipha.strategy.registry import register
        >>> @register("my_strategy")
        ... class MyStrategy(Strategy):
        ...     pass
    """
    return _registry.register(name)


def get_strategy(name: str) -> Type[Strategy] | None:
    """获取策略类

    Args:
        name: 策略名称

    Returns:
        策略类
    """
    return _registry.get(name)


def create_strategy(name: str, **kwargs: Any) -> Strategy:
    """创建策略实例

    Args:
        name: 策略名称
        **kwargs: 策略参数

    Returns:
        策略实例
    """
    return _registry.create(name, **kwargs)


def create_strategy_from_config(config: dict[str, Any]) -> Strategy:
    """从配置字典创建策略

    Args:
        config: 策略配置，必须包含 'name' 键，可包含 'params' 键

    Returns:
        策略实例

    Example:
        >>> config = {"name": "momentum", "params": {"lookback_days": 20}}
        >>> strategy = create_strategy_from_config(config)
    """
    name = config.get("name")
    if not name:
        raise ValueError("配置必须包含 'name' 键")

    params = config.get("params", {})
    return create_strategy(name, **params)


def list_strategies() -> list[str]:
    """列出所有已注册的策略（包含兼容入口）。"""
    return _registry.list_strategies()


def list_discoverable_strategies() -> list[str]:
    """List strategies exposed to new research/discovery callers."""
    return _registry.list_discoverable_strategies()


def _reset_registry() -> None:
    """重置注册表（仅用于测试）"""
    _registry.clear()


# ============================================================================
# 自动注册内置策略
# ============================================================================


def _register_builtin_strategies() -> None:
    """注册内置策略

    在模块加载时调用，注册所有内置策略。
    各模块内部通过 @StrategyRegistry().register() 装饰器自注册，
    此处只需 import 触发装饰器执行，并补充别名。
    """
    # 延迟导入——触发各模块的装饰器注册（按字母序）
    import aipha.strategy.cb_mean_reversion  # noqa: F401
    import aipha.strategy.cb_momentum  # noqa: F401
    import aipha.strategy.cb_multi_alpha  # noqa: F401
    import aipha.strategy.cb_multi_factor  # noqa: F401
    import aipha.strategy.cb_regime_rotation  # noqa: F401
    import aipha.strategy.cb_reversal_small  # noqa: F401
    import aipha.strategy.cb_small_size_gated  # noqa: F401
    import aipha.strategy.cb_value_momentum  # noqa: F401
    import aipha.strategy.defensive_value  # noqa: F401
    import aipha.strategy.dividend_defensive  # noqa: F401
    import aipha.strategy.double_low  # noqa: F401
    import aipha.strategy.dual_ma  # noqa: F401
    import aipha.strategy.factor_strategy  # noqa: F401
    import aipha.strategy.forced_redeem_play  # noqa: F401
    import aipha.strategy.low_premium  # noqa: F401
    import aipha.strategy.low_price  # noqa: F401
    import aipha.strategy.mean_reversion  # noqa: F401
    import aipha.strategy.momentum  # noqa: F401
    import aipha.strategy.multifactor_enhance  # noqa: F401
    import aipha.strategy.negative_premium  # noqa: F401
    import aipha.strategy.new_bond  # noqa: F401
    import aipha.strategy.new_small  # noqa: F401
    import aipha.strategy.pead_event  # noqa: F401
    import aipha.strategy.pseudo_ytm  # noqa: F401
    import aipha.strategy.small_size  # noqa: F401
    import aipha.strategy.small_size_enhanced  # noqa: F401
    import aipha.strategy.smallcap_rotation  # noqa: F401
    import aipha.strategy.triple_low  # noqa: F401
    import aipha.strategy.turnover_surge  # noqa: F401
    import aipha.strategy.vol_breakout  # noqa: F401
    import aipha.strategy.ytm_plus  # noqa: F401
    import aipha.strategy.sector_momentum  # noqa: F401
    import aipha.strategy.earnings_momentum  # noqa: F401
    import aipha.strategy.value_quality  # noqa: F401
    import aipha.strategy.ml_predict  # noqa: F401
    import aipha.strategy.cb_ultra  # noqa: F401
    import aipha.strategy.quality_growth  # noqa: F401
    import aipha.strategy.cb_stock_quality  # noqa: F401
    # RETIRED (R56 A2, 2026-03-14): Alpha衰减FAIL, 近3年SR为负/极低
    # import aipha.strategy.cb_gamma_convexity  # noqa: F401  # retired
    import aipha.strategy.cb_clause_play  # noqa: F401
    import aipha.strategy.cb_multi_layer  # noqa: F401
    import aipha.strategy.cb_low_vol_reversion  # noqa: F401
    import aipha.strategy.cb_vol_squeeze  # noqa: F401
    import aipha.strategy.cb_dl_enhanced_5f  # noqa: F401
    import aipha.strategy.cb_heat_momentum  # noqa: F401
    import aipha.strategy.cb_iv_strategy  # noqa: F401
    import aipha.strategy.cb_ivol_value  # noqa: F401
    import aipha.strategy.cb_iv_event_satellite  # noqa: F401
    import aipha.strategy.cb_credit_spread  # noqa: F401
    import aipha.strategy.cb_pricing_bias  # noqa: F401
    import aipha.strategy.cb_stock_quality_v2  # noqa: F401
    # import aipha.strategy.cb_four_factor  # noqa: F401  # retired (R56 A2)
    import aipha.strategy.cb_enhanced_mf_v2  # noqa: F401
    import aipha.strategy.cb_ml_ranking  # noqa: F401
    import aipha.strategy.cb_adaptive_factor  # noqa: F401
    import aipha.strategy.etf_rotation  # noqa: F401
    import aipha.strategy.ml_factor_strategy  # noqa: F401
    import aipha.strategy.balanced_multifactor  # noqa: F401
    import aipha.strategy.behavioral_sleeve  # noqa: F401
    import aipha.strategy.bucket_multifactor  # noqa: F401
    import aipha.strategy.csi500_quality_sleeve  # noqa: F401
    import aipha.strategy.reference_five_factor  # noqa: F401
    import aipha.strategy.dividend_quality  # noqa: F401
    import aipha.strategy.v19_hgro  # noqa: F401
    import aipha.strategy.v23_sbal  # noqa: F401
    import aipha.strategy.pair_trading_industry_neutral  # noqa: F401
    import aipha.strategy.overreaction  # noqa: F401
    import aipha.strategy.quality_lowvol  # noqa: F401
    # 补充别名（不重复主名，因已由装饰器注册）
    from aipha.strategy.dual_ma import DualMAStrategy
    from aipha.strategy.factor_strategy import FactorBasedStrategy
    from aipha.strategy.mean_reversion import MeanReversionStrategy
    from aipha.strategy.momentum import MomentumStrategy

    _registry.register_class("momentum_factor", MomentumStrategy)
    _registry.register_class("factor", FactorBasedStrategy)
    _registry.register_class("reversion", MeanReversionStrategy)
    _registry.register_class("moving_average", DualMAStrategy)


# 在模块加载时注册内置策略
_register_builtin_strategies()
