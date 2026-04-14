"""
Reporting 模块单元测试

TDD: 先写测试，再实现

测试覆盖：
1. BacktestReport 数据结构
2. 核心指标计算（年化收益、最大回撤、Sharpe、Calmar）
3. 实验指纹
4. 报告导出（JSON/Dict）
"""

from datetime import date, datetime
from decimal import Decimal

import pytest

from aipha.reporting.report import (
    BacktestReport,
    PerformanceMetrics,
    ExperimentFingerprint,
    calculate_metrics,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_nav_series() -> list[tuple[date, float]]:
    """模拟NAV序列：1年期，约20%收益"""
    navs = []
    base = 1000000.0
    # 模拟252个交易日
    for i in range(252):
        # 添加一些波动
        if i < 50:
            nav = base * (1 + 0.0008 * i)  # 上涨
        elif i < 100:
            nav = base * 1.04 - base * 0.002 * (i - 50)  # 回撤
        elif i < 150:
            nav = base * 0.94 + base * 0.003 * (i - 100)  # 恢复
        else:
            nav = base * 1.09 + base * 0.001 * (i - 150)  # 继续上涨
        navs.append((date(2025, 1, 6) + __import__('datetime').timedelta(days=i), nav))
    return navs


@pytest.fixture
def simple_nav_series() -> list[tuple[date, float]]:
    """简单NAV序列：用于边界测试"""
    return [
        (date(2025, 1, 6), 1000000.0),
        (date(2025, 1, 7), 1010000.0),  # +1%
        (date(2025, 1, 8), 995000.0),   # -1.5%
        (date(2025, 1, 9), 1005000.0),  # +1%
        (date(2025, 1, 10), 1020000.0), # +1.5%
    ]


# ============================================================================
# PerformanceMetrics Tests
# ============================================================================


class TestPerformanceMetrics:
    """性能指标测试"""

    def test_metrics_creation(self):
        """创建指标对象"""
        metrics = PerformanceMetrics(
            total_return=0.20,
            annual_return=0.20,
            max_drawdown=0.10,
            sharpe_ratio=1.5,
            calmar_ratio=2.0,
            volatility=0.15,
            win_rate=0.55,
            turnover=2.5,
        )
        assert metrics.total_return == 0.20
        assert metrics.sharpe_ratio == 1.5

    def test_metrics_immutable(self):
        """指标对象不可变"""
        metrics = PerformanceMetrics(
            total_return=0.20,
            annual_return=0.20,
            max_drawdown=0.10,
            sharpe_ratio=1.5,
            calmar_ratio=2.0,
            volatility=0.15,
        )
        with pytest.raises(AttributeError):
            metrics.total_return = 0.30  # type: ignore


# ============================================================================
# calculate_metrics Tests
# ============================================================================


class TestCalculateMetrics:
    """指标计算测试"""

    def test_calculate_total_return(self, simple_nav_series):
        """计算总收益率"""
        metrics = calculate_metrics(simple_nav_series)
        expected = (1020000.0 - 1000000.0) / 1000000.0
        assert metrics.total_return == pytest.approx(expected, rel=0.01)

    def test_calculate_max_drawdown(self, simple_nav_series):
        """计算最大回撤"""
        metrics = calculate_metrics(simple_nav_series)
        # 最高点 1010000 到最低点 995000
        # 回撤 = (1010000 - 995000) / 1010000 ≈ 0.0149
        assert metrics.max_drawdown >= 0.01
        assert metrics.max_drawdown <= 0.02

    def test_calculate_sharpe_ratio(self, sample_nav_series):
        """计算Sharpe比率"""
        metrics = calculate_metrics(sample_nav_series, risk_free_rate=0.02)
        # Sharpe应该是合理的正数
        assert metrics.sharpe_ratio > 0
        assert metrics.sharpe_ratio < 10  # 合理范围

    def test_calculate_calmar_ratio(self, sample_nav_series):
        """计算Calmar比率"""
        metrics = calculate_metrics(sample_nav_series)
        # Calmar = 年化收益 / 最大回撤
        assert metrics.calmar_ratio > 0

    def test_single_day_returns_zero_volatility(self):
        """单日NAV无法计算波动率"""
        navs = [(date(2025, 1, 6), 1000000.0)]
        metrics = calculate_metrics(navs)
        assert metrics.volatility == 0.0

    def test_empty_nav_raises(self):
        """空NAV序列抛出异常"""
        with pytest.raises(ValueError, match="NAV序列不能为空"):
            calculate_metrics([])


# ============================================================================
# ExperimentFingerprint Tests
# ============================================================================


class TestExperimentFingerprint:
    """实验指纹测试"""

    def test_fingerprint_creation(self):
        """创建实验指纹"""
        fp = ExperimentFingerprint(
            git_commit="abc123",
            config_hash="def456",
            data_fingerprint="ghi789",
            random_seed=42,
            run_time=datetime(2025, 1, 6, 10, 30, 0),
        )
        assert fp.git_commit == "abc123"
        assert fp.random_seed == 42

    def test_fingerprint_to_dict(self):
        """指纹转字典"""
        fp = ExperimentFingerprint(
            git_commit="abc123",
            config_hash="def456",
            data_fingerprint="ghi789",
            random_seed=42,
            run_time=datetime(2025, 1, 6, 10, 30, 0),
        )
        d = fp.to_dict()
        assert d["git_commit"] == "abc123"
        assert d["random_seed"] == 42


# ============================================================================
# BacktestReport Tests
# ============================================================================


class TestBacktestReport:
    """回测报告测试"""

    def test_report_creation(self, simple_nav_series):
        """创建回测报告"""
        metrics = calculate_metrics(simple_nav_series)
        report = BacktestReport(
            strategy_name="TestStrategy",
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 10),
            initial_capital=Decimal("1000000.00"),
            final_capital=Decimal("1020000.00"),
            metrics=metrics,
        )
        assert report.strategy_name == "TestStrategy"
        assert report.metrics.total_return == pytest.approx(0.02, rel=0.01)

    def test_report_with_fingerprint(self, simple_nav_series):
        """带实验指纹的报告"""
        metrics = calculate_metrics(simple_nav_series)
        fp = ExperimentFingerprint(
            git_commit="abc123",
            config_hash="def456",
            data_fingerprint="ghi789",
            random_seed=42,
            run_time=datetime.now(),
        )
        report = BacktestReport(
            strategy_name="TestStrategy",
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 10),
            initial_capital=Decimal("1000000.00"),
            final_capital=Decimal("1020000.00"),
            metrics=metrics,
            fingerprint=fp,
        )
        assert report.fingerprint is not None
        assert report.fingerprint.git_commit == "abc123"

    def test_report_to_dict(self, simple_nav_series):
        """报告转字典"""
        metrics = calculate_metrics(simple_nav_series)
        report = BacktestReport(
            strategy_name="TestStrategy",
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 10),
            initial_capital=Decimal("1000000.00"),
            final_capital=Decimal("1020000.00"),
            metrics=metrics,
        )
        d = report.to_dict()
        assert d["strategy_name"] == "TestStrategy"
        assert "metrics" in d
        assert d["metrics"]["total_return"] == pytest.approx(0.02, rel=0.01)

    def test_report_with_exclusion_stats(self, simple_nav_series):
        """包含剔除统计的报告"""
        metrics = calculate_metrics(simple_nav_series)
        exclusion_stats = {"ST": 10, "DELISTED": 5, "SUSPENDED": 3}
        report = BacktestReport(
            strategy_name="TestStrategy",
            start_date=date(2025, 1, 6),
            end_date=date(2025, 1, 10),
            initial_capital=Decimal("1000000.00"),
            final_capital=Decimal("1020000.00"),
            metrics=metrics,
            exclusion_stats=exclusion_stats,
        )
        d = report.to_dict()
        assert d["exclusion_stats"]["ST"] == 10
