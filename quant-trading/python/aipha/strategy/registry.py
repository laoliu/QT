"""策略工厂与注册机制"""
from __future__ import annotations

import logging
from typing import Any, Callable, Type, TypeVar

from aipha.strategy.base import Strategy, TickStrategy

logger = logging.getLogger(__name__)

StrategyT = TypeVar("StrategyT", bound=Strategy)
TickStrategyT = TypeVar("TickStrategyT", bound=TickStrategy)


class StrategyRegistry:
    """策略注册表 — 单例"""

    _instance: StrategyRegistry | None = None
    _strategies: dict[str, Type[Strategy]]

    def __new__(cls) -> StrategyRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._strategies = {}
        return cls._instance

    def register(self, name: str | None = None) -> Callable[[Type[StrategyT]], Type[StrategyT]]:
        def decorator(cls_: Type[StrategyT]) -> Type[StrategyT]:
            strategy_name = name or cls_.__name__
            if strategy_name in self._strategies:
                logger.warning("策略 '%s' 已存在，将被覆盖", strategy_name)
            self._strategies[strategy_name] = cls_
            return cls_
        return decorator

    def register_class(self, name: str, cls: Type[Strategy]) -> None:
        if name in self._strategies:
            logger.warning("策略 '%s' 已存在，将被覆盖", name)
        self._strategies[name] = cls

    def get(self, name: str) -> Type[Strategy] | None:
        return self._strategies.get(name)

    def list_strategies(self) -> list[str]:
        return list(self._strategies.keys())

    def create(self, name: str, **kwargs: Any) -> Strategy:
        cls_ = self._strategies.get(name)
        if cls_ is None:
            raise KeyError(f"策略 '{name}' 未注册。可用策略: {self.list_strategies()}")
        return cls_(**kwargs)

    def create_from_config(self, config: dict[str, Any]) -> Strategy:
        name = config.get("name")
        if not name:
            raise ValueError("配置必须包含 'name' 键")
        params = config.get("params", {})
        return self.create(name, **params)

    def clear(self) -> None:
        self._strategies.clear()


class TickStrategyRegistry:
    """Tick 策略注册表 — 单例"""

    _instance: TickStrategyRegistry | None = None
    _strategies: dict[str, Type[TickStrategy]]

    def __new__(cls) -> TickStrategyRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._strategies = {}
        return cls._instance

    def register(self, name: str | None = None) -> Callable[[Type[TickStrategyT]], Type[TickStrategyT]]:
        def decorator(cls_: Type[TickStrategyT]) -> Type[TickStrategyT]:
            strategy_name = name or cls_.__name__
            if strategy_name in self._strategies:
                logger.warning("Tick策略 '%s' 已存在，将被覆盖", strategy_name)
            self._strategies[strategy_name] = cls_
            return cls_
        return decorator

    def register_class(self, name: str, cls: Type[TickStrategy]) -> None:
        if name in self._strategies:
            logger.warning("Tick策略 '%s' 已存在，将被覆盖", name)
        self._strategies[name] = cls

    def get(self, name: str) -> Type[TickStrategy] | None:
        return self._strategies.get(name)

    def list_strategies(self) -> list[str]:
        return list(self._strategies.keys())

    def create(self, name: str, **kwargs: Any) -> TickStrategy:
        cls_ = self._strategies.get(name)
        if cls_ is None:
            raise KeyError(f"Tick策略 '{name}' 未注册。可用: {self.list_strategies()}")
        return cls_(**kwargs)


# 模块级便捷函数
_registry = StrategyRegistry()
register_strategy = _registry.register
get_strategy = _registry.get
list_strategies = _registry.list_strategies
create_strategy = _registry.create
create_strategy_from_config = _registry.create_from_config
