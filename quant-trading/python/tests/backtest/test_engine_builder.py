"""engine_builder 策略注册完整性测试。

确保所有带 @StrategyRegistry().register() 的策略模块被纳入
_STRATEGY_MODULES 列表，以及 auto_n_splits 使用交易日而非日历天。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# 策略注册完整性
# ---------------------------------------------------------------------------


class TestStrategyModulesCompleteness:
    """验证 _STRATEGY_MODULES 列表包含所有策略模块。"""

    @staticmethod
    def _find_registered_modules() -> dict[str, list[str]]:
        """扫描 aipha/strategy/ 下所有 @register 装饰器。

        原先拼的是 ``parents[2] / "src" / "aipha" / "strategy"`` —— src/
        拿掉后这是个不存在的目录，glob 空转，于是下面
        test_all_registered_modules_in_list 一直在「零注册模块 vs
        _STRATEGY_MODULES」之间比对，永远通过。真有人新写一个策略却忘了
        登记进 _STRATEGY_MODULES，回测引擎发现不了它，而这条护栏不会响。
        """
        strategy_dir = (
            Path(__file__).resolve().parents[2] / "aipha" / "strategy"
        )
        assert strategy_dir.is_dir(), (
            f"策略目录不存在: {strategy_dir} —— 扫描根指错会让本护栏空转通过"
        )
        registered: dict[str, list[str]] = {}
        for f in strategy_dir.glob("*.py"):
            if f.name == "__init__.py":
                continue
            content = f.read_text(encoding="utf-8")
            matches = re.findall(
                r'@StrategyRegistry\(\)\.register\(["\']([^"\']+)["\']\)',
                content,
            )
            if matches:
                module_name = f"aipha.strategy.{f.stem}"
                registered[module_name] = matches
        return registered

    @staticmethod
    def _read_strategy_modules_list() -> set[str]:
        """从 engine_builder.py 读取 _STRATEGY_MODULES。"""
        from aipha.backtest.engine_builder import _STRATEGY_MODULES

        return set(_STRATEGY_MODULES)

    def test_all_registered_modules_in_list(self):
        """所有带 @register 的模块必须在 _STRATEGY_MODULES 中。"""
        registered = self._find_registered_modules()
        listed = self._read_strategy_modules_list()

        missing = {m: names for m, names in registered.items() if m not in listed}
        assert not missing, (
            f"以下策略模块有 @register 但未在 _STRATEGY_MODULES 中注册: "
            f"{missing}"
        )

    def test_no_phantom_modules_in_list(self):
        """_STRATEGY_MODULES 中的模块必须可以导入。"""
        from aipha.backtest.engine_builder import _STRATEGY_MODULES
        import importlib

        failures = []
        for module_name in _STRATEGY_MODULES:
            try:
                importlib.import_module(module_name)
            except ImportError as e:
                failures.append(f"{module_name}: {e}")

        assert not failures, (
            f"以下 _STRATEGY_MODULES 模块无法导入: {failures}"
        )

    def test_ensure_strategies_registered_populates_registry(self):
        """_ensure_strategies_registered 后，注册表应非空。"""
        from aipha.backtest.engine_builder import _ensure_strategies_registered
        from aipha.strategy.registry import StrategyRegistry

        _ensure_strategies_registered()
        registry = StrategyRegistry()
        available = registry.list_strategies()
        # 至少 30 个策略
        assert len(available) >= 30, (
            f"注册表仅有 {len(available)} 个策略，预期 ≥30"
        )

    def test_modules_count_at_least_40(self):
        """_STRATEGY_MODULES 至少包含 40 个模块。"""
        from aipha.backtest.engine_builder import _STRATEGY_MODULES

        assert len(_STRATEGY_MODULES) >= 40, (
            f"_STRATEGY_MODULES 仅有 {len(_STRATEGY_MODULES)} 个，预期 ≥40"
        )


# ---------------------------------------------------------------------------
# auto_n_splits 应使用交易日
# ---------------------------------------------------------------------------


class TestAutoNSplitsUsesCalendarCorrectly:
    """确保 WF 分折数计算基于合理输入。"""

    def test_auto_n_splits_trading_days_vs_calendar(self):
        """7y CB: 1699 交易日 vs 2555 日历天应产生不同分折数。

        train=504, test=252 →
          交易日: (1699-504)/252 = 4
          日历天: (2555-504)/252 = 8  ← 过多，导致后续 folds OOS=0
        """
        from aipha.backtest.validation import auto_n_splits

        n_trading = auto_n_splits(1699, 504, 252)
        n_calendar = auto_n_splits(2555, 504, 252)

        assert n_trading == 4, f"交易日 1699 应产生 4 折，实际 {n_trading}"
        assert n_calendar == 8, f"日历天 2555 应产生 8 折，实际 {n_calendar}"
        # 用交易日数更合理
        assert n_trading < n_calendar

    def test_auto_n_splits_10y_cb(self):
        """10y CB: 2431 交易日, train=756, test=252"""
        from aipha.backtest.validation import auto_n_splits

        n = auto_n_splits(2431, 756, 252)
        # (2431-756)/252 = 6.6 → 6
        assert n >= 6
        assert n <= 7
