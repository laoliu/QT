"""因子注册表 — 工厂模式 + 按名称/分类查询。"""
from __future__ import annotations

import logging
from typing import Any, Callable, Type, TypeVar

from aipha.factors.base import Factor

logger = logging.getLogger(__name__)

FactorT = TypeVar("FactorT", bound=Factor)


class FactorMeta:
    __slots__ = ("cls", "category", "description")

    def __init__(self, cls: Type[Factor], category: str = "other", description: str = "") -> None:
        self.cls = cls
        self.category = category
        self.description = description

    def __repr__(self) -> str:
        return f"FactorMeta(cls={self.cls.__name__!r}, category={self.category!r})"


class FactorRegistry:
    """因子注册表 — 单例"""

    _instance: FactorRegistry | None = None
    _factors: dict[str, FactorMeta]

    def __new__(cls) -> FactorRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._factors = {}
        return cls._instance

    def register(
        self, name: str | None = None, *, category: str = "other", description: str = "",
    ) -> Callable[[Type[FactorT]], Type[FactorT]]:
        def decorator(cls_: Type[FactorT]) -> Type[FactorT]:
            factor_name = name or cls_.__name__
            if factor_name in self._factors:
                logger.warning("因子 '%s' 已存在，将被覆盖", factor_name)
            self._factors[factor_name] = FactorMeta(cls=cls_, category=category, description=description)
            return cls_
        return decorator

    def register_class(self, name: str, cls: Type[Factor], *, category: str = "other", description: str = "") -> None:
        self._factors[name] = FactorMeta(cls=cls, category=category, description=description)

    def get(self, name: str) -> Type[Factor] | None:
        meta = self._factors.get(name)
        return meta.cls if meta else None

    def get_meta(self, name: str) -> FactorMeta | None:
        return self._factors.get(name)

    def list_factors(self) -> list[str]:
        return list(self._factors.keys())

    def list_by_category(self, category: str) -> list[str]:
        return [n for n, m in self._factors.items() if m.category == category]

    def categories(self) -> list[str]:
        return sorted({m.category for m in self._factors.values()})

    def create(self, name: str, **kwargs: Any) -> Factor:
        meta = self._factors.get(name)
        if meta is None:
            raise KeyError(f"因子 '{name}' 未注册。可用: {self.list_factors()}")
        return meta.cls(**kwargs)

    def clear(self) -> None:
        self._factors.clear()


# 模块级便捷函数
_registry = FactorRegistry()
register_factor = _registry.register
get_factor = _registry.get
create_factor = _registry.create
list_factors = _registry.list_factors
list_factors_by_category = _registry.list_by_category
factor_categories = _registry.categories
