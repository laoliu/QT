"""Phase E 测试: summary.json 指标丰富化、HTML 成本前后对比、cb_ultra 配置加载。"""
from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pytest
import yaml

from aipha.backtest.report import CostAttribution
from aipha.execution.fees import FeeBreakdown


# ============================================================================
# 1. summary.json 结构化指标测试
# ============================================================================


@dataclass
class _Snapshot:
    trade_date: date
    nav: float
    cash: float
    positions: dict = field(default_factory=dict)
    position_values: dict = field(default_factory=dict)
    daily_return: float = 0.0
    cumulative_return: float = 0.0


@dataclass
class _FakeBTResultWithSnaps:
    """模拟回测结果，含有 daily_snapshots 以便 extract_metrics_dict 可正常计算。"""
    strategy_name: str = "test_enriched"
    start_date: date = date(2024, 1, 2)
    end_date: date = date(2024, 1, 10)
    initial_capital: float = 1_000_000
    final_nav: float = 1_050_000
    total_trades: int = 10
    total_fees: FeeBreakdown = field(
        default_factory=lambda: FeeBreakdown(commission=100, stamp_tax=50, transfer_fee=20)
    )
    daily_snapshots: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    config: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.daily_snapshots:
            # 生成 7 个交易日的 snapshots (1/2 ~ 1/10)
            navs = [1_000_000, 1_010_000, 1_020_000, 1_015_000,
                    1_030_000, 1_045_000, 1_050_000]
            for i, nav_val in enumerate(navs):
                prev_nav = navs[i - 1] if i > 0 else nav_val
                dr = (nav_val / prev_nav - 1.0) if i > 0 else 0.0
                cr = nav_val / 1_000_000 - 1.0
                self.daily_snapshots.append(
                    _Snapshot(
                        trade_date=date(2024, 1, 2 + i),
                        nav=nav_val,
                        cash=nav_val * 0.1,
                        daily_return=dr,
                        cumulative_return=cr,
                    )
                )


def _make_run_config_for_enriched():
    from aipha.backtest.config_models import (
        BacktestRunConfig,
        CostConfig,
        DataConfig,
        FillModelConfig,
        RiskConfig,
        StrategyConfig,
        UniverseConfig,
    )
    return BacktestRunConfig(
        name="test_enriched",
        data=DataConfig(
            source="synthetic",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 6, 1),
        ),
        universe=UniverseConfig(),
        strategy=StrategyConfig(name="buy_and_hold"),
        cost=CostConfig(),
        fill_model=FillModelConfig(),
        risk=RiskConfig(),
    )


class TestSummaryJsonEnriched:
    """测试 save_run_result 写出的 summary.json 包含完整结构化指标。"""

    def test_summary_contains_metrics_and_cost_attribution(self, tmp_path: Path):
        """验证 summary.json 包含 metrics 和 cost_attribution 字段。"""
        from aipha.backtest.config_parser import save_run_result
        from aipha.backtest.config_models import BacktestRunResult
        from aipha.utils.reproducibility import ExperimentFingerprint

        run_cfg = _make_run_config_for_enriched()
        fp = ExperimentFingerprint(
            git_commit="abc123", git_short="abc", git_dirty=False,
            git_branch="main", config_path=None, config_hash="h",
            data_fingerprint="d", random_seed=42,
            run_time=datetime(2025, 1, 1),
        )
        bt_result = _FakeBTResultWithSnaps()
        run_result = BacktestRunResult(
            config=run_cfg,
            result=bt_result,
            fingerprint=fp,
            output_dir=tmp_path / "out",
        )
        save_run_result(run_result)

        summary_path = tmp_path / "out" / "summary.json"
        assert summary_path.exists()
        summary = json.loads(summary_path.read_text("utf-8"))

        # 基础字段
        assert summary["strategy_name"] == "test_enriched"
        assert summary["final_nav"] == 1_050_000
        assert summary["total_fees"] == 170.0  # 100+50+20

        # 结构化指标
        assert "metrics" in summary
        m = summary["metrics"]
        assert "annualized_return" in m
        assert "sharpe_ratio" in m
        assert "max_drawdown_pct" in m
        assert "total_return" in m
        assert "volatility" in m
        assert "calmar_ratio" in m
        assert "win_rate" in m
        assert "total_trades" in m

        # 成本归因
        assert "cost_attribution" in summary
        ca = summary["cost_attribution"]
        assert "total_fees" in ca
        assert "commission_total" in ca
        assert "stamp_tax_total" in ca
        assert "transfer_fee_total" in ca
        assert "slippage_cost" in ca
        assert "cost_vs_nav_pct" in ca
        assert "cost_drag_annualized" in ca

    def test_summary_metrics_values_reasonable(self, tmp_path: Path):
        """验证 metrics 数值合理 (5% 收益、正 Sharpe 等)。"""
        from aipha.backtest.config_parser import save_run_result
        from aipha.backtest.config_models import BacktestRunResult
        from aipha.utils.reproducibility import ExperimentFingerprint

        run_cfg = _make_run_config_for_enriched()
        fp = ExperimentFingerprint(
            git_commit="abc123", git_short="abc", git_dirty=False,
            git_branch="main", config_path=None, config_hash="h",
            data_fingerprint="d", random_seed=42,
            run_time=datetime(2025, 1, 1),
        )
        bt_result = _FakeBTResultWithSnaps()
        run_result = BacktestRunResult(
            config=run_cfg,
            result=bt_result,
            fingerprint=fp,
            output_dir=tmp_path / "out",
        )
        save_run_result(run_result)
        summary = json.loads((tmp_path / "out" / "summary.json").read_text("utf-8"))
        m = summary["metrics"]

        # total_return = 5%
        assert abs(m["total_return"] - 0.05) < 0.001
        # annualized_return > 0
        assert m["annualized_return"] > 0
        # max_drawdown_pct >= 0
        assert m["max_drawdown_pct"] >= 0

    def test_summary_cost_attribution_values(self, tmp_path: Path):
        """验证 cost_attribution 数值与 FeeBreakdown 一致。"""
        from aipha.backtest.config_parser import save_run_result
        from aipha.backtest.config_models import BacktestRunResult
        from aipha.utils.reproducibility import ExperimentFingerprint

        run_cfg = _make_run_config_for_enriched()
        fp = ExperimentFingerprint(
            git_commit="abc123", git_short="abc", git_dirty=False,
            git_branch="main", config_path=None, config_hash="h",
            data_fingerprint="d", random_seed=42,
            run_time=datetime(2025, 1, 1),
        )
        bt_result = _FakeBTResultWithSnaps()
        run_result = BacktestRunResult(
            config=run_cfg,
            result=bt_result,
            fingerprint=fp,
            output_dir=tmp_path / "out",
        )
        save_run_result(run_result)
        summary = json.loads((tmp_path / "out" / "summary.json").read_text("utf-8"))
        ca = summary["cost_attribution"]

        assert ca["commission_total"] == 100.0
        assert ca["stamp_tax_total"] == 50.0
        assert ca["transfer_fee_total"] == 20.0
        assert ca["total_fees"] == 170.0
        assert ca["cost_vs_nav_pct"] > 0
        assert ca["cost_drag_annualized"] > 0

    def test_summary_graceful_on_empty_snapshots(self, tmp_path: Path):
        """空 daily_snapshots 时 metrics 仍能写入（值为 0）。"""
        from aipha.backtest.config_parser import save_run_result
        from aipha.backtest.config_models import BacktestRunResult
        from aipha.utils.reproducibility import ExperimentFingerprint

        run_cfg = _make_run_config_for_enriched()
        fp = ExperimentFingerprint(
            git_commit="abc123", git_short="abc", git_dirty=False,
            git_branch="main", config_path=None, config_hash="h",
            data_fingerprint="d", random_seed=42,
            run_time=datetime(2025, 1, 1),
        )

        @dataclass
        class _EmptyResult:
            strategy_name: str = "empty"
            start_date: date = date(2024, 1, 2)
            end_date: date = date(2024, 1, 10)
            initial_capital: float = 1_000_000
            final_nav: float = 1_000_000
            total_trades: int = 0
            total_fees: FeeBreakdown = field(
                default_factory=lambda: FeeBreakdown(commission=0, stamp_tax=0, transfer_fee=0)
            )
            daily_snapshots: list = field(default_factory=list)
            trades: list = field(default_factory=list)
            config: dict = field(default_factory=dict)

        bt_result = _EmptyResult()
        run_result = BacktestRunResult(
            config=run_cfg,
            result=bt_result,
            fingerprint=fp,
            output_dir=tmp_path / "out",
        )
        save_run_result(run_result)

        summary = json.loads((tmp_path / "out" / "summary.json").read_text("utf-8"))
        # metrics 应存在且值为 0（因为无 snapshots）
        assert "metrics" in summary
        assert summary["metrics"]["total_return"] == 0.0


# ============================================================================
# 2. HTML 报告 - 成本前后对比测试
# ============================================================================


class TestHtmlReportCostComparison:
    """测试 HTML 报告中新增的成本前后对比行。"""

    def _make_report(
        self,
        annualized_return: float = 0.15,
        cost_drag: float = 0.005,
    ):
        from aipha.backtest.report import (
            BacktestReport,
            DrawdownAnalysis,
            PerformanceMetrics,
            PositionStats,
            TradingStats,
        )
        return BacktestReport(
            strategy_name="CostComparisonTest",
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
            initial_capital=1_000_000.0,
            final_nav=1_150_000.0,
            performance=PerformanceMetrics(
                total_return=0.15,
                annualized_return=annualized_return,
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
            ),
            costs=CostAttribution(
                total_fees=5000.0,
                commission_total=3000.0,
                stamp_tax_total=1500.0,
                transfer_fee_total=500.0,
                slippage_cost=2000.0,
                cost_vs_nav_pct=0.005,
                cost_drag_annualized=cost_drag,
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

    def test_html_contains_cost_before_after(self):
        """HTML 报告包含 '成本前年化收益' 和 '成本后年化收益' 字段。"""
        from aipha.backtest.html_report import generate_html_report

        report = self._make_report(annualized_return=0.15, cost_drag=0.005)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "report.html"
            generate_html_report(report, out)
            content = out.read_text(encoding="utf-8")

            assert "成本前年化收益" in content
            assert "成本后年化收益" in content

    def test_gross_return_equals_net_plus_drag(self):
        """成本前收益 = 成本后收益 + 年化费率拖累。"""
        from aipha.backtest.html_report import generate_html_report

        ann_ret = 0.15
        cost_drag = 0.02
        report = self._make_report(annualized_return=ann_ret, cost_drag=cost_drag)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "report.html"
            generate_html_report(report, out)
            content = out.read_text(encoding="utf-8")

            gross_ret = ann_ret + cost_drag  # 0.17
            # 检查 17.00% 出现在 HTML 中
            assert f"{gross_ret:.2%}" in content
            assert f"{ann_ret:.2%}" in content

    def test_zero_cost_drag(self):
        """当 cost_drag = 0 时，成本前后收益相同。"""
        from aipha.backtest.html_report import generate_html_report

        report = self._make_report(annualized_return=0.10, cost_drag=0.0)
        with tempfile.TemporaryDirectory() as tmpdir:
            out = Path(tmpdir) / "report.html"
            generate_html_report(report, out)
            content = out.read_text(encoding="utf-8")

            assert "成本前年化收益" in content
            assert "成本后年化收益" in content
            # 两者都应显示 10.00%
            assert content.count("10.00%") >= 2


# ============================================================================
# 3. cb_ultra / cb_ultra_noage YAML 配置测试
# ============================================================================


class TestCBUltraConfigs:
    """测试 cb_ultra 和 cb_ultra_noage 的 YAML 配置正确性。"""

    @pytest.fixture
    def config_dir(self) -> Path:
        return Path(__file__).resolve().parents[2] / "configs" / "cb_strategies" / "archived"

    def test_cb_ultra_yml_exists(self, config_dir: Path):
        assert (config_dir / "cb_ultra.yml").exists()

    def test_cb_ultra_noage_yml_exists(self, config_dir: Path):
        assert (config_dir / "cb_ultra_noage.yml").exists()

    def test_cb_ultra_yml_loadable(self, config_dir: Path):
        with open(config_dir / "cb_ultra.yml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert cfg["name"] == "cb_ultra"
        assert cfg["strategy"]["name"] == "cb_ultra"

    def test_cb_ultra_noage_yml_loadable(self, config_dir: Path):
        with open(config_dir / "cb_ultra_noage.yml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert cfg["name"] == "cb_ultra_noage"
        assert cfg["strategy"]["name"] == "cb_ultra_noage"

    def test_cb_ultra_params_match_defaults(self, config_dir: Path):
        """YAML 参数与 CBUltraConfig 默认值一致。"""
        from aipha.strategy.cb_ultra import CBUltraConfig

        with open(config_dir / "cb_ultra.yml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        params = cfg["strategy"]["params"]
        defaults = CBUltraConfig()

        assert params["top_k"] == defaults.top_k
        assert params["rebalance_days"] == defaults.rebalance_days
        assert params["max_price"] == defaults.max_price
        assert params["weight_size"] == defaults.weight_size
        assert params["weight_premium"] == defaults.weight_premium
        assert params["weight_price"] == defaults.weight_price
        assert params["weight_age"] == defaults.weight_age
        assert params["use_age_filter"] == defaults.use_age_filter
        assert params["min_age_days"] == defaults.min_age_days
        assert params["max_age_days"] == defaults.max_age_days
        assert params["max_premium"] == defaults.max_premium
        assert params["lot_size"] == defaults.lot_size

    def test_cb_ultra_noage_params_match_defaults(self, config_dir: Path):
        """YAML 参数与 CBUltraNoAgeConfig 默认值一致。"""
        from aipha.strategy.cb_ultra import CBUltraNoAgeConfig

        with open(config_dir / "cb_ultra_noage.yml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        params = cfg["strategy"]["params"]
        defaults = CBUltraNoAgeConfig()

        assert params["top_k"] == defaults.top_k
        assert params["rebalance_days"] == defaults.rebalance_days
        assert params["max_price"] == defaults.max_price
        assert params["weight_size"] == defaults.weight_size
        assert params["weight_premium"] == defaults.weight_premium
        assert params["weight_price"] == defaults.weight_price
        assert params["max_premium"] == defaults.max_premium
        assert params["lot_size"] == defaults.lot_size

    def test_cb_ultra_registered_in_registry(self):
        """cb_ultra 和 cb_ultra_noage 在策略注册表中。"""
        from aipha.strategy.registry import StrategyRegistry

        registry = StrategyRegistry()
        names = registry.list_strategies()
        assert "cb_ultra" in names
        assert "cb_ultra_noage" in names

    def test_cb_ultra_cost_section(self, config_dir: Path):
        """可转债成本配置：零印花税、零过户费。"""
        with open(config_dir / "cb_ultra.yml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cost = cfg["cost"]
        assert cost["stamp_tax_rate"] == 0.0
        assert cost["transfer_fee_rate"] == 0.0
        assert cost["commission_rate"] == 0.0000854  # §13.1 MarketRulesConvertibleBond 万0.854

    def test_cb_ultra_noage_cost_section(self, config_dir: Path):
        """可转债成本配置：零印花税、零过户费。"""
        with open(config_dir / "cb_ultra_noage.yml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        cost = cfg["cost"]
        assert cost["stamp_tax_rate"] == 0.0
        assert cost["transfer_fee_rate"] == 0.0

    def test_cb_ultra_fill_model_is_next_bar(self, config_dir: Path):
        """成交模型为 next_bar_open。"""
        with open(config_dir / "cb_ultra.yml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        assert cfg["fill_model"]["name"] == "next_bar_open"
