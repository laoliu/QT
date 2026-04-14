"""ExperimentComparator 单元测试

覆盖：
- _build_metrics_table 格式化
- _build_nav_overlay NAV 归一化
- _build_yearly_comparison 分年度对比
- _determine_best 优胜者判定
- compare 端到端（基于临时目录）
- generate_text_report 文本报告
- _load_nav_csv CSV 解析
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from aipha.reporting.comparator import ExperimentComparator


# ============================================================================
# _build_metrics_table
# ============================================================================


class TestBuildMetricsTable:
    """metrics table 构建逻辑。"""

    def _make_comparator(self):
        return ExperimentComparator(base_dir="results")

    def test_basic_table_structure(self):
        comp = self._make_comparator()
        exps = [
            {"tag": "exp_A", "run_id": "r1", "annual_return": 0.12, "max_drawdown": 0.05},
            {"tag": "exp_B", "run_id": "r2", "annual_return": 0.08, "max_drawdown": 0.10},
        ]
        table = comp._build_metrics_table(exps, ["annual_return", "max_drawdown"])
        assert table["header"] == ["指标", "exp_A", "exp_B"]
        assert len(table["rows"]) == 2

    def test_none_metric_shows_na(self):
        comp = self._make_comparator()
        exps = [{"tag": "A", "run_id": "r1", "sharpe_ratio": None}]
        table = comp._build_metrics_table(exps, ["sharpe_ratio"])
        assert "N/A" in table["rows"][0][1]

    def test_formatting_percentages(self):
        comp = self._make_comparator()
        exps = [{"tag": "A", "run_id": "r1", "total_return": 0.2345}]
        table = comp._build_metrics_table(exps, ["total_return"])
        assert "23.45%" in table["rows"][0][1]

    def test_fallback_to_run_id(self):
        """无 tag 时使用 run_id 作为列头。"""
        comp = self._make_comparator()
        exps = [{"tag": "", "run_id": "run_001", "sharpe_ratio": 1.5}]
        table = comp._build_metrics_table(exps, ["sharpe_ratio"])
        assert table["header"][1] == "run_001"


# ============================================================================
# _build_nav_overlay
# ============================================================================


class TestBuildNavOverlay:

    def _make_comparator(self):
        return ExperimentComparator(base_dir="results")

    def test_normalizes_to_one(self):
        comp = self._make_comparator()
        nav_data = [{
            "label": "test",
            "series": [
                {"date": "2024-01-01", "nav": "100"},
                {"date": "2024-01-02", "nav": "110"},
                {"date": "2024-01-03", "nav": "105"},
            ],
        }]
        overlay = comp._build_nav_overlay(nav_data)
        assert "test" in overlay
        assert overlay["test"][0]["nav_normalized"] == 1.0
        assert overlay["test"][1]["nav_normalized"] == pytest.approx(1.1, abs=0.001)

    def test_empty_series_skipped(self):
        comp = self._make_comparator()
        overlay = comp._build_nav_overlay([{"label": "empty", "series": []}])
        assert "empty" not in overlay

    def test_multiple_series(self):
        comp = self._make_comparator()
        nav_data = [
            {"label": "A", "series": [{"date": "2024-01-01", "nav": "100"}]},
            {"label": "B", "series": [{"date": "2024-01-01", "nav": "200"}]},
        ]
        overlay = comp._build_nav_overlay(nav_data)
        assert len(overlay) == 2
        assert overlay["A"][0]["nav_normalized"] == 1.0
        assert overlay["B"][0]["nav_normalized"] == 1.0


# ============================================================================
# _build_yearly_comparison
# ============================================================================


class TestBuildYearlyComparison:

    def _make_comparator(self):
        return ExperimentComparator(base_dir="results")

    def test_basic_comparison(self):
        comp = self._make_comparator()
        data = [
            {"label": "A", "yearly_returns": {"2023": 0.1, "2024": 0.05}},
            {"label": "B", "yearly_returns": {"2023": 0.08, "2024": 0.12}},
        ]
        result = comp._build_yearly_comparison(data)
        assert "2023" in result
        assert "2024" in result
        assert result["2023"] == [0.1, 0.08]
        assert result["2024"] == [0.05, 0.12]

    def test_missing_year_gives_none(self):
        comp = self._make_comparator()
        data = [
            {"label": "A", "yearly_returns": {"2023": 0.1}},
            {"label": "B", "yearly_returns": {"2024": 0.2}},
        ]
        result = comp._build_yearly_comparison(data)
        assert result["2023"] == [0.1, None]
        assert result["2024"] == [None, 0.2]


# ============================================================================
# _determine_best
# ============================================================================


class TestDetermineBest:

    def _make_comparator(self):
        return ExperimentComparator(base_dir="results")

    def test_higher_better(self):
        comp = self._make_comparator()
        exps = [
            {"tag": "A", "run_id": "r1", "sharpe_ratio": 1.2},
            {"tag": "B", "run_id": "r2", "sharpe_ratio": 1.8},
        ]
        best = comp._determine_best(exps, ["sharpe_ratio"])
        assert best["sharpe_ratio"] == "B"

    def test_lower_better_drawdown(self):
        comp = self._make_comparator()
        exps = [
            {"tag": "A", "run_id": "r1", "max_drawdown": 0.15},
            {"tag": "B", "run_id": "r2", "max_drawdown": 0.08},
        ]
        best = comp._determine_best(exps, ["max_drawdown"])
        assert best["max_drawdown"] == "B"

    def test_none_values_skipped(self):
        comp = self._make_comparator()
        exps = [
            {"tag": "A", "run_id": "r1", "total_return": None},
            {"tag": "B", "run_id": "r2", "total_return": 0.1},
        ]
        best = comp._determine_best(exps, ["total_return"])
        assert best["total_return"] == "B"


# ============================================================================
# compare (端到端，用临时目录)
# ============================================================================


class TestCompareE2E:

    def _setup_experiment(self, base_dir: Path, strategy: str, run_id: str, meta: dict, nav_csv: str = ""):
        run_dir = base_dir / strategy / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        if nav_csv:
            (run_dir / "nav_series.csv").write_text(nav_csv, encoding="utf-8")

    def test_compare_two_experiments(self, tmp_path):
        meta1 = {
            "tag": "v1",
            "fill_model": "next_bar_open",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "initial_capital": 100000,
            "final_nav": 112000,
            "total_trades": 50,
            "git_commit": "abc123",
            "metrics": {
                "total_return": 0.12,
                "annual_return": 0.12,
                "max_drawdown": 0.05,
                "sharpe_ratio": 1.5,
                "calmar_ratio": 2.4,
                "total_fees": 500,
            },
        }
        meta2 = {
            "tag": "v2",
            "fill_model": "next_bar_open",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "initial_capital": 100000,
            "final_nav": 108000,
            "total_trades": 30,
            "git_commit": "def456",
            "metrics": {
                "total_return": 0.08,
                "annual_return": 0.08,
                "max_drawdown": 0.03,
                "sharpe_ratio": 1.8,
                "calmar_ratio": 2.0,
                "total_fees": 300,
            },
        }
        nav1 = "date,nav\n2024-01-01,100000\n2024-06-01,106000\n2024-12-31,112000"
        nav2 = "date,nav\n2024-01-01,100000\n2024-06-01,104000\n2024-12-31,108000"

        self._setup_experiment(tmp_path, "test_strat", "run_v1", meta1, nav1)
        self._setup_experiment(tmp_path, "test_strat", "run_v2", meta2, nav2)

        comp = ExperimentComparator(base_dir=str(tmp_path))
        result = comp.compare([
            ("test_strat", "run_v1"),
            ("test_strat", "run_v2"),
        ])
        assert result["num_experiments"] == 2
        assert len(result["experiments"]) == 2
        assert "metrics_table" in result
        assert "nav_overlay" in result
        assert "best_by" in result

    def test_missing_experiment_skipped(self, tmp_path):
        comp = ExperimentComparator(base_dir=str(tmp_path))
        result = comp.compare([("nonexistent", "run_x")])
        assert result["num_experiments"] == 0

    def test_missing_meta_skipped(self, tmp_path):
        (tmp_path / "strat" / "run1").mkdir(parents=True)
        comp = ExperimentComparator(base_dir=str(tmp_path))
        result = comp.compare([("strat", "run1")])
        assert result["num_experiments"] == 0


# ============================================================================
# generate_text_report
# ============================================================================


class TestGenerateTextReport:

    def test_report_contains_header(self, tmp_path):
        meta = {
            "tag": "test",
            "fill_model": "nbo",
            "metrics": {"annual_return": 0.15, "sharpe_ratio": 1.2},
        }
        run_dir = tmp_path / "strat" / "run1"
        run_dir.mkdir(parents=True)
        (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

        comp = ExperimentComparator(base_dir=str(tmp_path))
        comparison = comp.compare([("strat", "run1")], metrics=["annual_return", "sharpe_ratio"])
        report = comp.generate_text_report(comparison)

        assert "实验对比报告" in report
        assert "test" in report
        assert "nbo" in report

    def test_empty_comparison_report(self):
        comp = ExperimentComparator(base_dir="nonexistent")
        comparison = comp.compare([("no", "no")])
        report = comp.generate_text_report(comparison)
        assert "对比实验数: 0" in report


# ============================================================================
# _load_nav_csv
# ============================================================================


class TestLoadNavCsv:

    def test_loads_csv(self, tmp_path):
        csv_path = tmp_path / "nav.csv"
        csv_path.write_text("date,nav\n2024-01-01,100000\n2024-01-02,101000", encoding="utf-8")
        rows = ExperimentComparator._load_nav_csv(csv_path)
        assert len(rows) == 2
        assert rows[0]["date"] == "2024-01-01"
        assert rows[0]["nav"] == "100000"
