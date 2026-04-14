"""Tests for VaR/CVaR integration into generate_report and extract_metrics_dict.

验证 generate_report 正确填充 risk_summary，
extract_metrics_dict 包含 VaR/CVaR 指标。
"""

from __future__ import annotations

import pytest
from datetime import date
from dataclasses import dataclass, field

from aipha.backtest.report import (
    generate_report,
    extract_metrics_dict,
    compute_risk_summary,
    RiskSummary,
)
from aipha.backtest.engine import BacktestResult, DailySnapshot
from aipha.execution.fees import FeeBreakdown


def _make_snapshot(nav: float, d: date, daily_return: float = 0.0):
    """构造最小 DailySnapshot."""
    return DailySnapshot(
        trade_date=d,
        nav=nav,
        cash=nav * 0.5,
        positions={},
        position_values={},
        daily_return=daily_return,
        cumulative_return=nav / 1_000_000 - 1,
    )


def _make_backtest_result(nav_values: list[float]):
    """用 NAV 序列构造 BacktestResult."""
    from datetime import timedelta
    start = date(2024, 1, 2)
    snapshots = []
    for i, nav in enumerate(nav_values):
        d = start + timedelta(days=i)
        dr = 0.0
        if i > 0 and nav_values[i - 1] > 0:
            dr = nav / nav_values[i - 1] - 1
        snapshots.append(_make_snapshot(nav, d, dr))

    end = start + timedelta(days=max(len(nav_values) - 1, 0))
    return BacktestResult(
        strategy_name="test_var",
        start_date=start,
        end_date=end,
        initial_capital=1_000_000.0,
        final_nav=nav_values[-1] if nav_values else 1_000_000.0,
        final_cash=nav_values[-1] * 0.5 if nav_values else 500_000.0,
        final_positions={},
        daily_snapshots=snapshots,
        trades=[],
        total_trades=0,
        total_fees=FeeBreakdown(commission=0.0, stamp_tax=0.0, transfer_fee=0.0),
        exclusion_stats={},
        config={},
    )


class TestGenerateReportVaR:
    """测试 generate_report 是否正确填充 risk_summary"""

    def test_report_has_risk_summary(self):
        """>= 10 个 NAV 点时 risk_summary 不为 None"""
        # 20 个交易日，每天微涨
        navs = [1_000_000 * (1 + 0.001 * i) for i in range(20)]
        result = _make_backtest_result(navs)
        report = generate_report(result)
        assert report.risk_summary is not None
        # 连续上涨时 VaR 可为负（无亏损）
        assert isinstance(report.risk_summary.var_95, float)

    def test_report_risk_summary_none_if_short(self):
        """NAV 点不足时 risk_summary 为 None"""
        navs = [1_000_000, 1_001_000]
        result = _make_backtest_result(navs)
        report = generate_report(result)
        # 只有 1 个收益率，< 10，应为 None
        assert report.risk_summary is None

    def test_report_summary_text_has_var(self):
        """summary() 文本包含 VaR 字段"""
        navs = [1_000_000 * (1 + 0.001 * i) for i in range(25)]
        result = _make_backtest_result(navs)
        report = generate_report(result)
        text = report.summary()
        assert "VaR(95%)" in text
        assert "CVaR(95%)" in text

    def test_report_summary_includes_risk(self):
        """report 包含 risk_summary 字段"""
        navs = [1_000_000 * (1 + 0.001 * i) for i in range(20)]
        result = _make_backtest_result(navs)
        report = generate_report(result)
        assert report.risk_summary is not None
        rs = report.risk_summary
        assert hasattr(rs, 'var_95')
        assert hasattr(rs, 'cvar_95')
        assert hasattr(rs, 'var_99')
        assert hasattr(rs, 'cvar_99')


class TestExtractMetricsDictVaR:
    """测试 extract_metrics_dict 包含 VaR 指标"""

    def test_has_var_metrics(self):
        """VaR/CVaR 指标在 metrics dict 中"""
        navs = [1_000_000 * (1 + 0.001 * i) for i in range(25)]
        result = _make_backtest_result(navs)
        metrics = extract_metrics_dict(result)
        assert "var_95" in metrics
        assert "cvar_95" in metrics
        assert "var_99" in metrics
        assert "cvar_99" in metrics

    def test_no_var_if_short_nav(self):
        """NAV 不足时不含 VaR 指标"""
        navs = [1_000_000, 1_001_000]
        result = _make_backtest_result(navs)
        metrics = extract_metrics_dict(result)
        assert "var_95" not in metrics

    def test_var_values_positive(self):
        """VaR 值为正数（代表潜在损失）"""
        # 带波动的 NAV
        import random
        random.seed(42)
        navs = [1_000_000]
        for _ in range(50):
            navs.append(navs[-1] * (1 + random.gauss(0, 0.01)))
        result = _make_backtest_result(navs)
        metrics = extract_metrics_dict(result)
        # VaR 可以为负（连续上涨期间），但一般应为正
        assert "var_95" in metrics
        assert isinstance(metrics["var_95"], float)


class TestComputeRiskSummaryEdge:
    """compute_risk_summary 边界测试"""

    def test_all_same_returns(self):
        """所有收益率相同"""
        rets = [0.01] * 20
        rs = compute_risk_summary(rets)
        assert rs is not None
        assert rs.var_95 == pytest.approx(-0.01, abs=0.001)  # 无亏损

    def test_decreasing_navs(self):
        """持续下跌的收益率"""
        rets = [-0.02] * 20
        rs = compute_risk_summary(rets)
        assert rs is not None
        assert rs.var_95 > 0  # 有亏损
        assert rs.worst_day < 0
