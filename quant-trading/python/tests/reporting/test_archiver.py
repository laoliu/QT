"""归档系统与对比分析器测试"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pytest

from aipha.execution.fees import FeeBreakdown
from aipha.reporting.archiver import ExperimentArchiver, _hash_config
from aipha.reporting.comparator import ExperimentComparator


# ============================================================================
# 测试用的模拟数据结构（构造真实数据实例而非 mock）
# ============================================================================


@dataclass
class FakeSnapshot:
    trade_date: date
    nav: float
    cash: float
    positions: dict = field(default_factory=dict)
    position_values: dict = field(default_factory=dict)
    daily_return: float = 0.0
    cumulative_return: float = 0.0


@dataclass
class FakeTradeRecord:
    trade_id: str
    order_id: str
    symbol: str
    side: str
    qty: int
    price: float
    notional: float
    fees: FeeBreakdown
    trade_time: datetime = field(default_factory=datetime.now)
    trade_date: date = field(default_factory=date.today)


@dataclass
class FakeBacktestResult:
    strategy_name: str
    start_date: date
    end_date: date
    initial_capital: float
    final_nav: float
    final_cash: float
    final_positions: dict = field(default_factory=dict)
    daily_snapshots: list = field(default_factory=list)
    trades: list = field(default_factory=list)
    total_trades: int = 0
    total_fees: FeeBreakdown = field(
        default_factory=lambda: FeeBreakdown(commission=0, stamp_tax=0, transfer_fee=0)
    )
    exclusion_stats: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)


def make_fake_result(
    strategy_name: str = "test_strategy",
    initial_capital: float = 500000.0,
    final_nav: float = 550000.0,
    n_days: int = 50,
) -> FakeBacktestResult:
    """构造一个有意义的 fake 回测结果"""
    start = date(2024, 1, 2)
    snapshots = []
    nav = initial_capital
    daily_ret = (final_nav / initial_capital) ** (1 / n_days) - 1

    from datetime import timedelta as _td

    for i in range(n_days):
        if i > 0:
            nav = nav * (1 + daily_ret)
        d = start + _td(days=i)
        snapshots.append(FakeSnapshot(
            trade_date=d,
            nav=nav,
            cash=nav * 0.1,
            daily_return=daily_ret if i > 0 else 0,
            cumulative_return=(nav - initial_capital) / initial_capital,
        ))

    trades = [
        FakeTradeRecord(
            trade_id="T001",
            order_id="O001",
            symbol="128085.SZ",
            side="BUY",
            qty=100,
            price=100.0,
            notional=10000.0,
            fees=FeeBreakdown(commission=0.5, stamp_tax=0, transfer_fee=0),
            trade_time=datetime(2024, 1, 3, 10, 0),
            trade_date=date(2024, 1, 3),
        ),
    ]

    return FakeBacktestResult(
        strategy_name=strategy_name,
        start_date=start,
        end_date=snapshots[-1].trade_date,
        initial_capital=initial_capital,
        final_nav=final_nav,
        final_cash=final_nav * 0.1,
        daily_snapshots=snapshots,
        trades=trades,
        total_trades=1,
        total_fees=FeeBreakdown(commission=0.5, stamp_tax=0, transfer_fee=0),
        config={"fill_model_name": "same_bar_close"},
    )


# ============================================================================
# Archiver 测试
# ============================================================================


class TestExperimentArchiver:
    """测试归档器"""

    def test_archive_creates_all_files(self, tmp_path: Path) -> None:
        """归档应创建 meta.json, config.yml, nav_series.csv, trades.csv, summary.json"""
        archiver = ExperimentArchiver(base_dir=str(tmp_path))
        result = make_fake_result()
        config = {"name": "test_strategy", "cost": {"commission_rate": 0.0003}}

        run_id = archiver.archive(result, config, tag="test_v1")

        run_dir = tmp_path / "test_strategy" / run_id
        assert run_dir.exists()
        assert (run_dir / "meta.json").exists()
        assert (run_dir / "config.yml").exists()
        assert (run_dir / "nav_series.csv").exists()
        assert (run_dir / "trades.csv").exists()
        assert (run_dir / "summary.json").exists()

    def test_archive_updates_registry(self, tmp_path: Path) -> None:
        """归档应更新全局注册表"""
        archiver = ExperimentArchiver(base_dir=str(tmp_path))
        result = make_fake_result()
        config = {"name": "test_strategy"}

        archiver.archive(result, config, tag="run1")
        archiver.archive(result, config, tag="run2")

        registry_path = tmp_path / "registry.json"
        assert registry_path.exists()

        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        assert registry["total_experiments"] == 2
        assert len(registry["experiments"]) == 2

    def test_meta_contains_metrics(self, tmp_path: Path) -> None:
        """元信息应包含核心指标"""
        archiver = ExperimentArchiver(base_dir=str(tmp_path))
        result = make_fake_result(final_nav=600000.0)
        config = {"name": "test_strategy"}

        run_id = archiver.archive(result, config, tag="high_return")

        meta_path = tmp_path / "test_strategy" / run_id / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))

        assert "metrics" in meta
        metrics = meta["metrics"]
        assert "total_return" in metrics
        assert "annual_return" in metrics
        assert "max_drawdown" in metrics
        assert "sharpe_ratio" in metrics
        assert metrics["total_return"] > 0  # 600k > 500k

    def test_nav_series_csv_content(self, tmp_path: Path) -> None:
        """NAV CSV 应有正确的列和数据"""
        archiver = ExperimentArchiver(base_dir=str(tmp_path))
        result = make_fake_result(n_days=10)
        config = {"name": "test_strategy"}

        run_id = archiver.archive(result, config)

        nav_path = tmp_path / "test_strategy" / run_id / "nav_series.csv"
        import csv
        with open(nav_path, "r") as f:
            reader = csv.DictReader(f)
            rows = list(reader)

        assert len(rows) == 10
        assert "date" in rows[0]
        assert "nav" in rows[0]
        assert "daily_return" in rows[0]

    def test_list_experiments(self, tmp_path: Path) -> None:
        """list_experiments 应返回归档列表"""
        archiver = ExperimentArchiver(base_dir=str(tmp_path))
        result = make_fake_result()

        archiver.archive(result, {"name": "s1"}, tag="run_a")
        archiver.archive(result, {"name": "s1"}, tag="run_b")

        experiments = archiver.list_experiments()
        assert len(experiments) == 2

    def test_list_experiments_filter_strategy(self, tmp_path: Path) -> None:
        """按策略名筛选"""
        archiver = ExperimentArchiver(base_dir=str(tmp_path))

        r1 = make_fake_result(strategy_name="alpha")
        r2 = make_fake_result(strategy_name="beta")

        archiver.archive(r1, {"name": "alpha"}, tag="a1")
        archiver.archive(r2, {"name": "beta"}, tag="b1")

        results = archiver.list_experiments(strategy="alpha")
        assert len(results) == 1
        assert results[0]["strategy_name"] == "alpha"

    def test_load_experiment(self, tmp_path: Path) -> None:
        """加载实验应返回完整数据"""
        archiver = ExperimentArchiver(base_dir=str(tmp_path))
        result = make_fake_result()
        config = {"name": "test_strategy", "params": {"top_k": 10}}

        run_id = archiver.archive(result, config, tag="load_test")

        data = archiver.load_experiment("test_strategy", run_id)
        assert "meta" in data
        assert "config" in data
        assert "nav_series" in data
        assert data["config"]["params"]["top_k"] == 10

    def test_hash_config_deterministic(self) -> None:
        """配置 hash 应确定性"""
        config = {"a": 1, "b": {"c": 3}}
        h1 = _hash_config(config)
        h2 = _hash_config(config)
        assert h1 == h2
        assert len(h1) == 12


# ============================================================================
# Comparator 测试
# ============================================================================


class TestExperimentComparator:
    """测试对比分析器"""

    def _setup_two_experiments(
        self, tmp_path: Path
    ) -> tuple[ExperimentArchiver, str, str]:
        """辅助：创建两个归档实验"""
        archiver = ExperimentArchiver(base_dir=str(tmp_path))

        r1 = make_fake_result(final_nav=550000.0)
        r2 = make_fake_result(final_nav=600000.0)

        run_id1 = archiver.archive(r1, {"name": "test"}, tag="conservative")
        run_id2 = archiver.archive(r2, {"name": "test"}, tag="aggressive")

        return archiver, run_id1, run_id2

    def test_compare_returns_valid_structure(self, tmp_path: Path) -> None:
        """对比结果应有完整结构"""
        _, run_id1, run_id2 = self._setup_two_experiments(tmp_path)

        comparator = ExperimentComparator(base_dir=str(tmp_path))
        result = comparator.compare([
            ("test_strategy", run_id1),
            ("test_strategy", run_id2),
        ])

        assert result["num_experiments"] == 2
        assert "experiments" in result
        assert "metrics_table" in result
        assert "nav_overlay" in result
        assert "yearly_comparison" in result
        assert "best_by" in result

    def test_compare_metrics_table(self, tmp_path: Path) -> None:
        """对比表应包含正确的指标"""
        _, run_id1, run_id2 = self._setup_two_experiments(tmp_path)

        comparator = ExperimentComparator(base_dir=str(tmp_path))
        result = comparator.compare(
            [("test_strategy", run_id1), ("test_strategy", run_id2)],
            metrics=["annual_return", "max_drawdown"],
        )

        table = result["metrics_table"]
        assert len(table["header"]) == 3  # 指标 + 2 实验
        assert len(table["rows"]) == 2  # 2 个指标

    def test_compare_best_by(self, tmp_path: Path) -> None:
        """优胜者判断应正确"""
        _, run_id1, run_id2 = self._setup_two_experiments(tmp_path)

        comparator = ExperimentComparator(base_dir=str(tmp_path))
        result = comparator.compare([
            ("test_strategy", run_id1),
            ("test_strategy", run_id2),
        ])

        best = result["best_by"]
        # aggressive (600k) 应在年化收益上胜出
        assert "annual_return" in best
        assert best["annual_return"] == "aggressive"

    def test_nav_overlay_normalized(self, tmp_path: Path) -> None:
        """NAV 叠加数据应归一化到 1.0"""
        _, run_id1, run_id2 = self._setup_two_experiments(tmp_path)

        comparator = ExperimentComparator(base_dir=str(tmp_path))
        result = comparator.compare([
            ("test_strategy", run_id1),
            ("test_strategy", run_id2),
        ])

        overlay = result["nav_overlay"]
        for label, series in overlay.items():
            if series:
                # 第一个点应接近 1.0
                assert abs(series[0]["nav_normalized"] - 1.0) < 0.001

    def test_generate_text_report(self, tmp_path: Path) -> None:
        """文本报告应非空并包含关键词"""
        _, run_id1, run_id2 = self._setup_two_experiments(tmp_path)

        comparator = ExperimentComparator(base_dir=str(tmp_path))
        comparison = comparator.compare([
            ("test_strategy", run_id1),
            ("test_strategy", run_id2),
        ])

        report = comparator.generate_text_report(comparison)
        assert len(report) > 100
        assert "实验对比报告" in report
        assert "conservative" in report or "aggressive" in report

    def test_compare_skips_missing(self, tmp_path: Path) -> None:
        """对比时应跳过不存在的实验"""
        archiver = ExperimentArchiver(base_dir=str(tmp_path))
        r1 = make_fake_result()
        run_id1 = archiver.archive(r1, {"name": "test"}, tag="exists")

        comparator = ExperimentComparator(base_dir=str(tmp_path))
        result = comparator.compare([
            ("test_strategy", run_id1),
            ("test_strategy", "nonexistent_run"),
        ])

        # 只找到 1 个有效实验
        assert result["num_experiments"] == 1
