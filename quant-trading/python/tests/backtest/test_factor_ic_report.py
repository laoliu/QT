"""因子 IC 摘要嵌入回测报告的测试"""

from datetime import date, timedelta
import random

import pytest

from aipha.backtest.report import (
    BacktestReport,
    FactorICBrief,
    compute_factor_ic_briefs,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_dates(n: int = 60) -> list[date]:
    """生成连续交易日列表"""
    start = date(2023, 1, 3)
    return [start + timedelta(days=i) for i in range(n)]


def _make_factor_and_returns(
    dates: list[date],
    symbols: list[str],
    ic_target: float = 0.1,
    seed: int = 42,
) -> tuple[dict[date, dict[str, float]], dict[date, dict[str, float]]]:
    """生成有一定 IC 关系的因子值 + 收益率数据

    Args:
        ic_target: 近似目标 IC（大于 0 = 因子有效）
        seed: 随机种子
    """
    rng = random.Random(seed)
    factor_data: dict[date, dict[str, float]] = {}
    return_data: dict[date, dict[str, float]] = {}

    for d in dates:
        factors = {}
        returns = {}
        for s in symbols:
            f = rng.gauss(0, 1)
            # 收益 = ic_target * 因子 + 噪音
            r = ic_target * f + rng.gauss(0, 1)
            factors[s] = f
            returns[s] = r
        factor_data[d] = factors
        return_data[d] = returns

    return factor_data, return_data


# ---------------------------------------------------------------------------
# FactorICBrief 单元测试
# ---------------------------------------------------------------------------

class TestFactorICBrief:
    """FactorICBrief 数据类测试"""

    def test_creation(self):
        """测试创建"""
        brief = FactorICBrief(
            factor_name="momentum",
            ic_mean=0.05,
            ic_std=0.08,
            ir=0.625,
            ic_positive_rate=0.6,
            t_stat=2.5,
            p_value=0.01,
            sample_days=100,
        )
        assert brief.factor_name == "momentum"
        assert brief.ir == 0.625

    def test_to_dict(self):
        """测试序列化"""
        brief = FactorICBrief(
            factor_name="value",
            ic_mean=0.03,
            ic_std=0.05,
            ir=0.6,
            ic_positive_rate=0.55,
            t_stat=1.8,
            p_value=0.04,
            sample_days=80,
        )
        d = brief.to_dict()
        assert d["factor_name"] == "value"
        assert d["ic_mean"] == 0.03
        assert d["sample_days"] == 80
        assert isinstance(d["ir"], float)

    def test_to_dict_rounds_values(self):
        """测试序列化精度"""
        brief = FactorICBrief(
            factor_name="x",
            ic_mean=0.0312345678,
            ic_std=0.0812345678,
            ir=0.62345678,
            ic_positive_rate=0.55345678,
            t_stat=1.83456789,
            p_value=0.04123456,
            sample_days=50,
        )
        d = brief.to_dict()
        assert d["ic_mean"] == 0.031235  # 6 decimals
        assert d["ir"] == 0.6235  # 4 decimals


# ---------------------------------------------------------------------------
# compute_factor_ic_briefs 测试
# ---------------------------------------------------------------------------

class TestComputeFactorICBriefs:
    """compute_factor_ic_briefs 集成测试"""

    def test_single_factor(self):
        """单因子 IC 计算"""
        dates = _make_dates(60)
        symbols = [f"S{i:03d}" for i in range(20)]
        factor_data, return_data = _make_factor_and_returns(
            dates, symbols, ic_target=0.3, seed=42,
        )

        briefs = compute_factor_ic_briefs(
            factor_exposures={"momentum": factor_data},
            return_data=return_data,
        )

        assert len(briefs) == 1
        assert briefs[0].factor_name == "momentum"
        # IC 应非零（因为有信号），且有有效天数
        assert briefs[0].sample_days > 0
        assert briefs[0].ic_std > 0

    def test_multiple_factors(self):
        """多因子 IC 计算 + 排序"""
        dates = _make_dates(60)
        symbols = [f"S{i:03d}" for i in range(20)]

        # 两个因子：一个强、一个弱
        strong_factor, return_data = _make_factor_and_returns(
            dates, symbols, ic_target=0.5, seed=42,
        )
        weak_factor, _ = _make_factor_and_returns(
            dates, symbols, ic_target=0.01, seed=99,
        )

        briefs = compute_factor_ic_briefs(
            factor_exposures={
                "strong": strong_factor,
                "weak": weak_factor,
            },
            return_data=return_data,
        )

        assert len(briefs) == 2
        # 按 |IC| 降序排列
        assert abs(briefs[0].ic_mean) >= abs(briefs[1].ic_mean)

    def test_empty_factor_exposures(self):
        """空因子暴露"""
        briefs = compute_factor_ic_briefs(
            factor_exposures={},
            return_data={date(2023, 1, 1): {"A": 0.01}},
        )
        assert briefs == []

    def test_no_common_dates(self):
        """因子与收益无重叠日期"""
        factor_data = {date(2023, 1, 1): {"A": 1.0}}
        return_data = {date(2024, 1, 1): {"A": 0.01}}

        briefs = compute_factor_ic_briefs(
            factor_exposures={"factor1": factor_data},
            return_data=return_data,
        )

        assert len(briefs) == 1
        assert briefs[0].ic_mean == 0.0  # 无数据时 IC = 0

    def test_too_few_symbols(self):
        """标的不足（<5 个）应返回 IC=0"""
        dates = _make_dates(30)
        symbols = ["A", "B"]  # 只有 2 个，FactorAnalyzer 要求 >= 5
        factor_data, return_data = _make_factor_and_returns(
            dates, symbols, ic_target=0.3, seed=42,
        )

        briefs = compute_factor_ic_briefs(
            factor_exposures={"f1": factor_data},
            return_data=return_data,
        )

        assert len(briefs) == 1
        assert briefs[0].ic_mean == 0.0  # 不够样本

    def test_factor_analysis_in_report_summary(self):
        """因子 IC 出现在报告摘要中"""
        dates = _make_dates(60)
        symbols = [f"S{i:03d}" for i in range(20)]
        factor_data, return_data = _make_factor_and_returns(
            dates, symbols, ic_target=0.3, seed=42,
        )

        briefs = compute_factor_ic_briefs(
            factor_exposures={"momentum": factor_data},
            return_data=return_data,
        )

        # 构造最小 BacktestReport
        from aipha.backtest.report import (
            CostAttribution,
            DrawdownAnalysis,
            PerformanceMetrics,
            PositionStats,
            TradingStats,
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
            factor_analysis=briefs,
        )

        summary = report.summary()
        assert "因子 IC 分析" in summary
        assert "momentum" in summary
        assert "IC=" in summary

    def test_report_summary_without_factors(self):
        """无因子分析时摘要不含因子部分"""
        from aipha.backtest.report import (
            CostAttribution,
            DrawdownAnalysis,
            PerformanceMetrics,
            PositionStats,
            TradingStats,
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
        )

        summary = report.summary()
        assert "因子 IC 分析" not in summary
