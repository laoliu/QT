"""HTML报告生成器测试"""

from datetime import date, datetime
from pathlib import Path
import tempfile

import pytest

from aipha.backtest.html_report import (
    extract_drawdown_series,
    extract_nav_series,
    generate_html_report,
)
from aipha.backtest.report import (
    BacktestReport,
    CostAttribution,
    DrawdownAnalysis,
    PerformanceMetrics,
    PositionStats,
    TradingStats,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def sample_report():
    """创建示例报告"""
    return BacktestReport(
        strategy_name="TestStrategy",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        initial_capital=1_000_000.0,
        final_nav=1_150_000.0,
        performance=PerformanceMetrics(
            total_return=0.15,
            annualized_return=0.15,
            volatility=0.20,
            sharpe_ratio=0.75,
            sortino_ratio=1.10,
            calmar_ratio=1.50,
            trading_days=252,
        ),
        drawdown=DrawdownAnalysis(
            max_drawdown=50000.0,
            max_drawdown_pct=0.10,
            max_drawdown_start=date(2024, 3, 1),
            max_drawdown_end=date(2024, 3, 15),
            max_drawdown_recovery=date(2024, 4, 10),
            max_drawdown_duration=40,
            avg_drawdown=0.03,
            drawdown_series=[],
        ),
        costs=CostAttribution(
            total_fees=5000.0,
            commission_total=3000.0,
            stamp_tax_total=1500.0,
            transfer_fee_total=500.0,
            slippage_cost=2000.0,
            cost_vs_nav_pct=0.005,
            cost_drag_annualized=0.005,
        ),
        trading=TradingStats(
            total_trades=100,
            buy_trades=50,
            sell_trades=50,
            avg_trade_size=20000.0,
            avg_holding_period=10.0,
            turnover_rate=2.0,
            win_trades=55,
            loss_trades=45,
            win_rate=0.55,
            avg_win=5000.0,
            avg_loss=3000.0,
            profit_factor=1.8,
        ),
        positions=PositionStats(
            avg_positions=10.0,
            max_positions=15,
            min_positions=5,
            avg_concentration=0.15,
            max_concentration=0.25,
        ),
        generated_at=datetime.now().isoformat(),
    )


@pytest.fixture
def sample_nav_series():
    """示例净值序列"""
    return [
        ("2024-01-01", 1000000),
        ("2024-01-02", 1005000),
        ("2024-01-03", 1010000),
        ("2024-01-04", 1008000),
        ("2024-01-05", 1015000),
    ]


@pytest.fixture
def sample_drawdown_series():
    """示例回撤序列"""
    return [
        ("2024-01-01", 0.0),
        ("2024-01-02", 0.0),
        ("2024-01-03", 0.0),
        ("2024-01-04", -0.002),
        ("2024-01-05", 0.0),
    ]


# ============================================================================
# 测试
# ============================================================================


class TestGenerateHtmlReport:
    """HTML报告生成测试"""

    def test_generate_basic_report(self, sample_report):
        """测试基本报告生成"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.html"
            result = generate_html_report(sample_report, output_path)

            assert result.exists()
            content = result.read_text(encoding="utf-8")

            # 检查关键内容
            assert "TestStrategy" in content
            assert "1,150,000" in content
            assert "15.00%" in content or "15%" in content

    def test_generate_with_nav_series(self, sample_report, sample_nav_series):
        """测试带净值序列的报告"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.html"
            result = generate_html_report(
                sample_report,
                output_path,
                nav_series=sample_nav_series,
            )

            content = result.read_text(encoding="utf-8")
            assert "navChart" in content
            assert "2024-01-01" in content

    def test_generate_with_both_series(
        self, sample_report, sample_nav_series, sample_drawdown_series
    ):
        """测试带完整序列的报告"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.html"
            result = generate_html_report(
                sample_report,
                output_path,
                nav_series=sample_nav_series,
                drawdown_series=sample_drawdown_series,
            )

            content = result.read_text(encoding="utf-8")
            assert "ddChart" in content

    def test_creates_parent_directory(self, sample_report):
        """测试自动创建父目录"""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "nested" / "path" / "report.html"
            result = generate_html_report(sample_report, output_path)

            assert result.exists()
            assert result.parent.exists()

    def test_status_badges(self):
        """测试不同夏普比率的状态标签"""
        base_report = BacktestReport(
            strategy_name="Test",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            initial_capital=1_000_000.0,
            final_nav=1_000_000.0,
            performance=PerformanceMetrics(
                total_return=0.0,
                annualized_return=0.0,
                volatility=0.0,
                sharpe_ratio=2.0,  # 优秀
                sortino_ratio=0.0,
                calmar_ratio=0.0,
                trading_days=0,
            ),
            drawdown=DrawdownAnalysis(
                max_drawdown=0.0,
                max_drawdown_pct=0.0,
                max_drawdown_start=None,
                max_drawdown_end=None,
                max_drawdown_recovery=None,
                max_drawdown_duration=0,
                avg_drawdown=0.0,
            ),
            costs=CostAttribution(
                total_fees=0.0,
                commission_total=0.0,
                stamp_tax_total=0.0,
                transfer_fee_total=0.0,
                slippage_cost=0.0,
                cost_vs_nav_pct=0.0,
                cost_drag_annualized=0.0,
            ),
            trading=TradingStats(
                total_trades=0,
                buy_trades=0,
                sell_trades=0,
                avg_trade_size=0.0,
                avg_holding_period=0.0,
                turnover_rate=0.0,
                win_trades=0,
                loss_trades=0,
                win_rate=0.0,
                avg_win=0.0,
                avg_loss=0.0,
                profit_factor=0.0,
            ),
            positions=PositionStats(
                avg_positions=0.0,
                max_positions=0,
                min_positions=0,
                avg_concentration=0.0,
                max_concentration=0.0,
            ),
            generated_at=datetime.now().isoformat(),
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "report.html"
            result = generate_html_report(base_report, output_path)
            content = result.read_text(encoding="utf-8")
            assert "excellent" in content


class TestExtractNavSeries:
    """净值序列提取测试"""

    def test_extract_from_snapshots(self):
        """测试从快照提取净值"""

        class MockSnapshot:
            def __init__(self, d, nav):
                self.date = d
                self.nav = nav

        snapshots = [
            MockSnapshot(date(2024, 1, 1), 1000000),
            MockSnapshot(date(2024, 1, 2), 1010000),
            MockSnapshot(date(2024, 1, 3), 1005000),
        ]

        result = extract_nav_series(snapshots)

        assert len(result) == 3
        assert result[0] == ("2024-01-01", 1000000)
        assert result[1] == ("2024-01-02", 1010000)

    def test_empty_snapshots(self):
        """测试空快照"""
        result = extract_nav_series([])
        assert result == []


class TestExtractDrawdownSeries:
    """回撤序列提取测试"""

    def test_extract_drawdown(self):
        """测试回撤计算"""

        class MockSnapshot:
            def __init__(self, d, nav):
                self.date = d
                self.nav = nav

        snapshots = [
            MockSnapshot(date(2024, 1, 1), 1000),
            MockSnapshot(date(2024, 1, 2), 1100),  # 新高
            MockSnapshot(date(2024, 1, 3), 1000),  # 回撤 -9.09%
            MockSnapshot(date(2024, 1, 4), 1050),  # 回撤 -4.55%
            MockSnapshot(date(2024, 1, 5), 1200),  # 新高
        ]

        result = extract_drawdown_series(snapshots)

        assert len(result) == 5
        assert result[0][1] == 0.0  # 第一天无回撤
        assert result[1][1] == 0.0  # 新高无回撤
        assert result[2][1] < 0  # 有回撤
        assert abs(result[2][1] - (-100 / 1100)) < 0.001  # 约 -9.09%

    def test_empty_snapshots(self):
        """测试空快照"""
        result = extract_drawdown_series([])
        assert result == []
