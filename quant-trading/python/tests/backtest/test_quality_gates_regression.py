"""
质量门禁与报告回归测试 — 针对审计修复项

覆盖：
- #38: METRIC_ALIASES 别名映射（annual_return → annualized_return 等）
- #40: trading_days >= 60 门禁
- #32: total_trades >= 5 门禁
- report.extract_metrics_dict 包含 annual_return 别名
"""

from __future__ import annotations

import pytest

from aipha.backtest.quality_gates import QualityChecker, QualityGate


# ============================================================================
# METRIC_ALIASES 别名解析
# ============================================================================


class TestMetricAliases:
    """质量门禁检查器必须支持指标名别名。"""

    def test_annual_return_alias(self):
        """门禁配置中使用 'annual_return'，实际指标为 'annualized_return'。"""
        checker = QualityChecker.from_config([
            {"metric": "annual_return", "operator": ">=", "threshold": 0.05,
             "severity": "error", "description": "年化 >= 5%"},
        ])
        metrics = {"annualized_return": 0.10}
        report = checker.check(metrics)
        assert report.all_passed, "annual_return 应解析为 annualized_return"

    def test_annual_return_alias_fail(self):
        checker = QualityChecker.from_config([
            {"metric": "annual_return", "operator": ">=", "threshold": 0.15,
             "severity": "error", "description": "年化 >= 15%"},
        ])
        metrics = {"annualized_return": 0.10}
        report = checker.check(metrics)
        assert not report.all_passed

    def test_mdd_alias(self):
        """'mdd' 应解析为 'max_drawdown_pct'。"""
        checker = QualityChecker.from_config([
            {"metric": "mdd", "operator": "<=", "threshold": 0.30,
             "severity": "error", "description": "MDD <= 30%"},
        ])
        metrics = {"max_drawdown_pct": 0.20}
        report = checker.check(metrics)
        assert report.all_passed

    def test_reverse_alias_lookup(self):
        """当标准名作为门禁 metric，但实际指标用别名提供时也能匹配。"""
        checker = QualityChecker.from_config([
            {"metric": "annualized_return", "operator": ">=", "threshold": 0.05,
             "severity": "error", "description": "年化"},
        ])
        metrics = {"annual_return": 0.10}
        report = checker.check(metrics)
        assert report.all_passed

    def test_direct_metric_takes_precedence(self):
        """直接匹配优先于别名。"""
        checker = QualityChecker.from_config([
            {"metric": "sharpe_ratio", "operator": ">=", "threshold": 1.0,
             "severity": "error", "description": "Sharpe"},
        ])
        metrics = {"sharpe_ratio": 1.5}
        report = checker.check(metrics)
        assert report.all_passed


# ============================================================================
# trading_days >= 60 & total_trades >= 5 门禁
# ============================================================================


class TestNewQualityGates:
    """新增的 trading_days 和 total_trades 硬门禁。"""

    def test_trading_days_pass(self):
        checker = QualityChecker.from_config([
            {"metric": "trading_days", "operator": ">=", "threshold": 60,
             "severity": "error", "description": "交易日 >= 60"},
        ])
        report = checker.check({"trading_days": 120})
        assert report.all_passed

    def test_trading_days_fail(self):
        checker = QualityChecker.from_config([
            {"metric": "trading_days", "operator": ">=", "threshold": 60,
             "severity": "error", "description": "交易日 >= 60"},
        ])
        report = checker.check({"trading_days": 30})
        assert not report.all_passed
        assert len(report.errors) == 1

    def test_total_trades_pass(self):
        checker = QualityChecker.from_config([
            {"metric": "total_trades", "operator": ">=", "threshold": 5,
             "severity": "error", "description": "交易次数 >= 5"},
        ])
        report = checker.check({"total_trades": 20})
        assert report.all_passed

    def test_total_trades_fail(self):
        checker = QualityChecker.from_config([
            {"metric": "total_trades", "operator": ">=", "threshold": 5,
             "severity": "error", "description": "交易次数 >= 5"},
        ])
        report = checker.check({"total_trades": 2})
        assert not report.all_passed

    def test_combined_gates_all_pass(self):
        checker = QualityChecker.from_config([
            {"metric": "trading_days", "operator": ">=", "threshold": 60,
             "severity": "error", "description": "交易日"},
            {"metric": "total_trades", "operator": ">=", "threshold": 5,
             "severity": "error", "description": "交易次数"},
            {"metric": "sharpe_ratio", "operator": ">=", "threshold": 0.3,
             "severity": "error", "description": "Sharpe"},
        ])
        metrics = {"trading_days": 200, "total_trades": 50, "sharpe_ratio": 1.0}
        report = checker.check(metrics)
        assert report.all_passed


# ============================================================================
# extract_metrics_dict annual_return 别名
# ============================================================================


class TestExtractMetricsAnnualReturn:
    """extract_metrics_dict 输出应包含 annual_return 键。"""

    def test_annual_return_in_metrics_dict(self):
        from datetime import date
        from aipha.backtest.engine import BacktestResult, DailySnapshot
        from aipha.backtest.report import extract_metrics_dict
        from aipha.execution.fees import FeeBreakdown

        # 构造最小 BacktestResult
        navs = [100_000, 101_000, 102_000, 103_000, 104_000]
        snapshots = []
        prev = 100_000
        for i, nav in enumerate(navs):
            snapshots.append(DailySnapshot(
                trade_date=date(2024, 1, 1 + i),
                nav=float(nav),
                cash=50_000.0,
                positions={},
                position_values={},
                daily_return=(nav - prev) / prev if prev else 0,
                cumulative_return=(nav - 100_000) / 100_000,
            ))
            prev = nav

        result = BacktestResult(
            strategy_name="test",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 5),
            initial_capital=100_000.0,
            final_nav=104_000.0,
            final_cash=50_000.0,
            final_positions={},
            daily_snapshots=snapshots,
            trades=[],
            total_trades=0,
            total_fees=FeeBreakdown(commission=0, stamp_tax=0, transfer_fee=0),
            exclusion_stats={},
            config={},
        )
        metrics = extract_metrics_dict(result)
        assert "annual_return" in metrics, "extract_metrics_dict 应包含 annual_return 别名"
        assert "annualized_return" in metrics
