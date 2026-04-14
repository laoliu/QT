"""风险摘要 (RiskSummary / compute_risk_summary) 测试"""

import random
from datetime import date

import pytest

from aipha.backtest.report import RiskSummary, compute_risk_summary


# ---------------------------------------------------------------------------
# RiskSummary 数据类测试
# ---------------------------------------------------------------------------


class TestRiskSummary:
    """RiskSummary 数据类测试"""

    def test_creation(self):
        rs = RiskSummary(
            var_95=0.02, cvar_95=0.03,
            var_99=0.04, cvar_99=0.05,
            worst_day=-0.08, worst_week=-0.12,
            tail_ratio=0.8,
        )
        assert rs.var_95 == 0.02
        assert rs.tail_ratio == 0.8

    def test_to_dict(self):
        rs = RiskSummary(
            var_95=0.02, cvar_95=0.03,
            var_99=0.04, cvar_99=0.05,
            worst_day=-0.08, worst_week=-0.12,
            tail_ratio=0.83456,
        )
        d = rs.to_dict()
        assert d["var_95"] == 0.02
        assert d["tail_ratio"] == 0.8346  # 4 decimals
        assert "worst_day" in d


# ---------------------------------------------------------------------------
# compute_risk_summary 测试
# ---------------------------------------------------------------------------


class TestComputeRiskSummary:
    """compute_risk_summary 函数测试"""

    @pytest.fixture
    def normal_returns(self):
        """正态分布日收益率（200 天）"""
        rng = random.Random(42)
        return [rng.gauss(0.0005, 0.015) for _ in range(200)]

    def test_basic(self, normal_returns):
        """基本计算"""
        rs = compute_risk_summary(normal_returns)

        assert rs is not None
        assert rs.var_95 > 0
        assert rs.cvar_95 >= rs.var_95  # CVaR >= VaR
        assert rs.var_99 >= rs.var_95
        assert rs.cvar_99 >= rs.var_99

    def test_worst_day(self, normal_returns):
        """最差单日 = 最小收益"""
        rs = compute_risk_summary(normal_returns)
        assert rs.worst_day == min(normal_returns)

    def test_worst_week(self, normal_returns):
        """最差周度 <= 最差单日 * 5"""
        rs = compute_risk_summary(normal_returns)
        # 滚动 5 日和的最小值
        n = len(normal_returns)
        expected = min(sum(normal_returns[i:i+5]) for i in range(n-4))
        assert rs.worst_week == pytest.approx(expected)

    def test_insufficient_data(self):
        """数据不足返回 None"""
        assert compute_risk_summary([0.01, -0.01, 0.02]) is None
        assert compute_risk_summary([]) is None

    def test_all_positive(self):
        """全正收益 → VaR 可能为负（即无风险）"""
        rets = [0.01] * 50
        rs = compute_risk_summary(rets)
        assert rs is not None
        # VaR = -sorted[idx] = -0.01 （负值表示"赚钱"）
        assert rs.var_95 == pytest.approx(-0.01)

    def test_all_negative(self):
        """全负收益 → VaR/CVaR > 0"""
        rets = [-0.02] * 50
        rs = compute_risk_summary(rets)
        assert rs.var_95 > 0
        assert rs.cvar_95 > 0

    def test_mixed_with_outlier(self):
        """含极端值的序列"""
        rng = random.Random(99)
        rets = [rng.gauss(0, 0.01) for _ in range(100)]
        rets.append(-0.10)  # 黑天鹅
        rs = compute_risk_summary(rets)

        assert rs.worst_day == -0.10
        assert rs.cvar_99 > 0  # CVaR 应反映尾部

    def test_tail_ratio(self, normal_returns):
        """尾部比率为 right_95 / |left_05|"""
        rs = compute_risk_summary(normal_returns)
        assert rs.tail_ratio >= 0  # 合理范围

    def test_exactly_10_returns(self):
        """边界：恰好 10 个收益率"""
        rets = [0.01, -0.02, 0.015, -0.01, 0.005,
                0.008, -0.003, 0.02, -0.005, 0.003]
        rs = compute_risk_summary(rets)
        assert rs is not None
        assert rs.worst_day == min(rets)

    def test_in_report_summary(self):
        """风险摘要出现在报告中"""
        from aipha.backtest.report import (
            BacktestReport,
            CostAttribution,
            DrawdownAnalysis,
            PerformanceMetrics,
            PositionStats,
            TradingStats,
        )

        rs = RiskSummary(
            var_95=0.02, cvar_95=0.03,
            var_99=0.04, cvar_99=0.05,
            worst_day=-0.08, worst_week=-0.12,
            tail_ratio=0.8,
        )

        report = BacktestReport(
            strategy_name="test",
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            initial_capital=1_000_000,
            final_nav=1.05,
            performance=PerformanceMetrics(
                total_return=0.05,
                annualized_return=0.05,
                volatility=0.1,
                sharpe_ratio=0.5,
                sortino_ratio=0.6,
                calmar_ratio=0.4,
                trading_days=245,
            ),
            drawdown=DrawdownAnalysis(
                max_drawdown=0.05,
                max_drawdown_pct=0.05,
                max_drawdown_start=date(2023, 3, 1),
                max_drawdown_end=date(2023, 4, 1),
                max_drawdown_recovery=None,
                max_drawdown_duration=22,
                avg_drawdown=0.02,
            ),
            costs=CostAttribution(
                total_fees=1800,
                commission_total=1000,
                stamp_tax_total=500,
                transfer_fee_total=100,
                slippage_cost=200,
                cost_vs_nav_pct=0.0018,
                cost_drag_annualized=0.002,
            ),
            trading=TradingStats(
                total_trades=100,
                buy_trades=50,
                sell_trades=50,
                avg_trade_size=10000,
                avg_holding_period=5.0,
                turnover_rate=1.5,
                win_trades=55,
                loss_trades=45,
                win_rate=0.55,
                avg_win=0.02,
                avg_loss=-0.015,
                profit_factor=1.2,
            ),
            positions=PositionStats(
                avg_positions=10,
                max_positions=15,
                min_positions=5,
                avg_concentration=0.12,
                max_concentration=0.2,
            ),
            risk_summary=rs,
        )

        summary = report.summary()
        assert "风险分析" in summary
        assert "VaR(95%)" in summary
        assert "CVaR(99%)" in summary
