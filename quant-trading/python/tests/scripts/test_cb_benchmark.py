"""cb_benchmark.py 的单元测试。

覆盖范围（轻量、无真实回测 IO）：
- 配置发现 discover_configs
- 日期解析 _parse_iso_date
- Top 排名输出 _print_top（对格式做基本断言）

注意：这里不跑真实回测，避免 CI 依赖大量本地缓存数据。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.cb_benchmark import (
    BenchmarkResult,
    _parse_iso_date,
    _print_top,
    discover_configs,
)


def test_discover_configs_finds_cb_configs():
    paths = discover_configs("configs/cb_strategies/*.yml")
    assert paths
    assert all(isinstance(p, Path) for p in paths)
    assert all(p.suffix == ".yml" for p in paths)


@pytest.mark.parametrize(
    "s,expected",
    [
        (None, None),
        ("", None),
        ("2022-01-01", "2022-01-01"),
    ],
)
def test_parse_iso_date(s, expected):
    d = _parse_iso_date(s)
    assert (d.isoformat() if d else None) == expected


def test_print_top_renders_table(capsys):
    results = [
        BenchmarkResult(
            config_path="configs/cb_strategies/a.yml",
            strategy="s1",
            start_date="2022-01-01",
            end_date="2024-12-31",
            initial_capital=1_000_000.0,
            final_nav=1_300_000.0,
            total_return_pct=30.0,
            annual_return_pct=9.14,
            max_drawdown_pct=10.0,
            sharpe=1.2,
            calmar=0.9,
            total_trades=100,
            total_fees=123.0,
            cost_drag_pct=0.5,
            error=None,
        ),
        BenchmarkResult(
            config_path="configs/cb_strategies/b.yml",
            strategy="s2",
            start_date="2022-01-01",
            end_date="2024-12-31",
            initial_capital=1_000_000.0,
            final_nav=2_000_000.0,
            total_return_pct=100.0,
            annual_return_pct=26.0,
            max_drawdown_pct=20.0,
            sharpe=0.8,
            calmar=1.3,
            total_trades=200,
            total_fees=456.0,
            cost_drag_pct=1.2,
            error=None,
        ),
    ]

    _print_top(results, sort_by="annual_return", top_n=2)
    out = capsys.readouterr().out
    assert "CB 策略基准跑分" in out
    assert "s2" in out  # annual_return 更高的应该排在前
    assert "s1" in out
