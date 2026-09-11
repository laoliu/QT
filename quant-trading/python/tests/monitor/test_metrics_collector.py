"""MetricsCollector 测试

测试覆盖：
- 基本 NAV 更新与时间序列记录
- 收益率计算
- 回撤跟踪
- 滚动 Sharpe 计算
- 指标摘要输出
- 文件写入
- 日切逻辑
"""

from __future__ import annotations

import json
import math
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from aipha.market.trading_calendar_constants import TRADING_DAYS_PER_YEAR
from aipha.monitor.metrics_collector import MetricsCollector, MetricsConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config(tmp_path: Path) -> MetricsConfig:
    return MetricsConfig(
        output_dir=tmp_path,
        output_file="test_metrics.json",
        write_interval_sec=0,  # 每次都写
        max_nav_history=100,
        sharpe_window=50,
    )


@pytest.fixture
def collector(config: MetricsConfig) -> MetricsCollector:
    return MetricsCollector(
        config=config,
        initial_capital=100_000.0,
        strategy_name="test_strategy",
    )


# ---------------------------------------------------------------------------
# 基本功能测试
# ---------------------------------------------------------------------------


class TestMetricsCollector:
    def test_init(self, collector: MetricsCollector):
        assert collector.nav_count == 0

    def test_single_update(self, collector: MetricsCollector):
        collector.update(nav=100_500, cash=50_000, n_positions=5)
        assert collector.nav_count == 1

    def test_nav_series(self, collector: MetricsCollector):
        """NAV 时间序列记录"""
        for i in range(5):
            collector.update(nav=100_000 + i * 1000)
        series = collector.get_nav_series()
        assert len(series) == 5
        assert series[-1][1] == 104_000

    def test_nav_series_last_n(self, collector: MetricsCollector):
        """获取最近 N 条"""
        for i in range(10):
            collector.update(nav=100_000 + i * 100)
        series = collector.get_nav_series(last_n=3)
        assert len(series) == 3

    def test_drawdown_tracking(self, collector: MetricsCollector):
        """回撤跟踪"""
        collector.update(nav=110_000)  # 新高
        collector.update(nav=100_000)  # 回撤
        summary = collector.get_summary()
        # 回撤 = (110000 - 100000) / 110000 ≈ 9.09%
        assert summary["risk"]["current_drawdown_pct"] > 9.0
        assert summary["risk"]["max_drawdown_pct"] > 9.0

    def test_drawdown_recovery(self, collector: MetricsCollector):
        """回撤恢复"""
        collector.update(nav=110_000)
        collector.update(nav=100_000)  # 回撤
        collector.update(nav=115_000)  # 新高，回撤恢复
        summary = collector.get_summary()
        assert summary["risk"]["current_drawdown_pct"] == 0.0
        # 最大回撤仍保留历史记录
        assert summary["risk"]["max_drawdown_pct"] > 9.0

    def test_total_return(self, collector: MetricsCollector):
        """总收益率"""
        collector.update(nav=110_000)
        summary = collector.get_summary()
        # (110000 - 100000) / 100000 * 100 = 10%
        assert abs(summary["nav"]["total_return_pct"] - 10.0) < 0.01

    def test_rolling_sharpe_insufficient_data(self, collector: MetricsCollector):
        """数据不足时 Sharpe = 0"""
        for i in range(5):
            collector.update(nav=100_000 + i * 100)
        summary = collector.get_summary()
        assert summary["risk"]["rolling_sharpe"] == 0.0

    def test_rolling_sharpe_with_data(self, collector: MetricsCollector):
        """有足够数据时计算 Sharpe"""
        nav = 100_000.0
        for i in range(60):
            nav *= 1.001  # 小幅正收益
            collector.update(nav=nav)
        summary = collector.get_summary()
        assert summary["risk"]["rolling_sharpe"] > 0

    def test_file_write(self, collector: MetricsCollector, config: MetricsConfig):
        """写入 JSON 文件"""
        collector.update(nav=105_000)
        output_file = config.output_dir / config.output_file
        assert output_file.exists()
        data = json.loads(output_file.read_text(encoding="utf-8"))
        assert "nav" in data
        assert "risk" in data
        assert "nav_recent" in data

    def test_summary_structure(self, collector: MetricsCollector):
        """摘要结构完整性"""
        collector.update(nav=100_000)
        s = collector.get_summary()
        assert "strategy_name" in s
        assert "timestamp" in s
        assert "nav" in s
        assert "risk" in s
        assert "trading" in s
        assert s["nav"]["initial"] == 100_000.0
        assert s["trading"]["total_fills"] == 0

    def test_day_switch(self, collector: MetricsCollector):
        """日切逻辑"""
        t1 = datetime(2024, 1, 1, 10, 0)
        t2 = datetime(2024, 1, 2, 10, 0)

        collector.update(nav=100_000, timestamp=t1)
        collector.update(nav=101_000, timestamp=t2)
        summary = collector.get_summary()
        # 第二天的日收益应基于前一天收盘
        assert summary["nav"]["daily_return_pct"] != 0.0

    def test_max_history_cap(self):
        """超过最大历史长度时截断"""
        config = MetricsConfig(max_nav_history=10, write_interval_sec=9999)
        collector = MetricsCollector(config=config, initial_capital=100_000)
        for i in range(20):
            collector.update(nav=100_000 + i * 100)
        assert collector.nav_count == 10

    def test_trading_stats(self, collector: MetricsCollector):
        """交易统计"""
        collector.update(
            nav=100_000,
            n_fills=15,
            n_orders=20,
            n_bars=500,
        )
        s = collector.get_summary()
        assert s["trading"]["total_fills"] == 15
        assert s["trading"]["total_orders"] == 20
        assert s["trading"]["total_bars"] == 500


class TestMetricsConfig:
    def test_defaults(self):
        config = MetricsConfig()
        assert config.output_dir == Path("data/metrics")
        assert config.write_interval_sec == 60
        # 244 而不是美股惯例的 252：§13.8 规定 A 股年化因子的唯一标准值是
        # sqrt(244)（见 aipha/market/trading_calendar_constants.py:20，据
        # 2010-2025 沪深交易日历统计）。断言常量本身，别把 252 改回来。
        assert config.sharpe_window == TRADING_DAYS_PER_YEAR
        assert TRADING_DAYS_PER_YEAR == 244

    def test_from_dict(self):
        data = {
            "output_dir": "/tmp/metrics",
            "write_interval_sec": 30,
            "sharpe_window": 126,
        }
        config = MetricsConfig.from_dict(data)
        assert config.output_dir == Path("/tmp/metrics")
        assert config.write_interval_sec == 30
        assert config.sharpe_window == 126
