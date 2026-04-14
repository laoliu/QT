"""capital_sweep.py 的单元测试。

测试多资金量扫描工具的核心功能：
- 参数解析
- 资金量格式化
- 配置覆盖逻辑
- 错误处理
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 添加项目根目录
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from scripts.capital_sweep import (
    DEFAULT_CAPITALS,
    _format_capital,
    _print_comparison_table,
)


class TestFormatCapital:
    """测试资金量格式化。"""

    def test_format_10wan(self):
        assert _format_capital(100_000) == "100K"

    def test_format_50wan(self):
        assert _format_capital(500_000) == "500K"

    def test_format_100wan(self):
        assert _format_capital(1_000_000) == "1M"

    def test_format_500wan(self):
        assert _format_capital(5_000_000) == "5M"

    def test_format_1000wan(self):
        assert _format_capital(10_000_000) == "10M"

    def test_format_small(self):
        assert _format_capital(500) == "500"

    def test_format_1k(self):
        assert _format_capital(1_000) == "1K"


class TestDefaultCapitals:
    """验证默认资金档位配置。"""

    def test_default_has_5_levels(self):
        assert len(DEFAULT_CAPITALS) == 5

    def test_default_ascending(self):
        for i in range(len(DEFAULT_CAPITALS) - 1):
            assert DEFAULT_CAPITALS[i] < DEFAULT_CAPITALS[i + 1]

    def test_default_min_100k(self):
        assert DEFAULT_CAPITALS[0] == 100_000

    def test_default_max_10m(self):
        assert DEFAULT_CAPITALS[-1] == 10_000_000

    def test_default_contains_1m(self):
        assert 1_000_000 in DEFAULT_CAPITALS


class TestPrintComparisonTable:
    """测试对比表格输出。"""

    def test_print_success_results(self, capsys):
        results = [
            {
                "strategy": "test_strat",
                "initial_capital": 100_000,
                "capital_label": "100K",
                "annual_return_pct": 15.5,
                "max_drawdown_pct": 10.2,
                "sharpe": 1.5,
                "calmar": 1.2,
                "total_trades": 100,
                "cost_drag_pct": 0.5,
            }
        ]
        _print_comparison_table(results)
        out = capsys.readouterr().out
        assert "100K" in out
        assert "15.50" in out
        assert "1.50" in out

    def test_print_error_results(self, capsys):
        results = [
            {
                "strategy": "test_strat",
                "initial_capital": 100_000,
                "capital_label": "100K",
                "error": "Config validation failed",
            }
        ]
        _print_comparison_table(results)
        out = capsys.readouterr().out
        assert "ERROR" in out
        assert "Config validation failed" in out

    def test_print_mixed_results(self, capsys):
        results = [
            {
                "capital_label": "100K",
                "annual_return_pct": 10.0,
                "max_drawdown_pct": 5.0,
                "sharpe": 1.0,
                "calmar": 0.8,
                "total_trades": 50,
                "cost_drag_pct": 0.3,
            },
            {
                "capital_label": "1M",
                "error": "Failed",
            },
        ]
        _print_comparison_table(results)
        out = capsys.readouterr().out
        assert "100K" in out
        assert "1M" in out
        assert "ERROR" in out

    def test_print_empty_results(self, capsys):
        _print_comparison_table([])
        out = capsys.readouterr().out
        assert "资金量扫描结果对比" in out
