"""ExperimentComparator edge-case tests.

Covers untested branches:
- _build_nav_overlay: first NAV=0, negative NAV
- _determine_best: unknown metric→max, all None, total_fees lower-is-better
- _build_metrics_table: custom metric fallback, calmar/total_fees formatting
- compare: nav_csv missing but meta exists, empty tag fallback
- generate_text_report: yearly comparison section, yearly with None
- _load_nav_csv: empty CSV (header only)
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from aipha.reporting.comparator import ExperimentComparator


# ============================================================================
# _build_nav_overlay edge cases
# ============================================================================


class TestNavOverlayEdge:

    def _make(self):
        return ExperimentComparator(base_dir="results")

    def test_first_nav_zero(self):
        """first NAV=0 → all normalized to 0"""
        c = self._make()
        nav_data = [
            {
                "label": "exp1",
                "series": [
                    {"date": "2024-01-01", "nav": "0"},
                    {"date": "2024-01-02", "nav": "100"},
                ],
            }
        ]
        overlay = c._build_nav_overlay(nav_data)
        assert "exp1" in overlay
        for point in overlay["exp1"]:
            assert point["nav_normalized"] == 0

    def test_negative_nav_treated_as_zero(self):
        """负 NAV → first_nav < 0 → all normalized to 0"""
        c = self._make()
        nav_data = [
            {
                "label": "neg",
                "series": [
                    {"date": "2024-01-01", "nav": "-50"},
                    {"date": "2024-01-02", "nav": "-25"},
                ],
            }
        ]
        overlay = c._build_nav_overlay(nav_data)
        # first_nav = -50 → not > 0 → all normalized = 0
        for point in overlay["neg"]:
            assert point["nav_normalized"] == 0

    def test_empty_series_skipped(self):
        """空序列 → 不出现在 overlay"""
        c = self._make()
        nav_data = [{"label": "empty", "series": []}]
        overlay = c._build_nav_overlay(nav_data)
        assert "empty" not in overlay


# ============================================================================
# _determine_best edge cases
# ============================================================================


class TestDetermineBestEdge:

    def _make(self):
        return ExperimentComparator()

    def test_unknown_metric_uses_max(self):
        """未知指标 → fallback to max"""
        c = self._make()
        exps = [
            {"tag": "A", "custom_metric": 10},
            {"tag": "B", "custom_metric": 20},
        ]
        best = c._determine_best(exps, ["custom_metric"])
        assert best["custom_metric"] == "B"

    def test_all_none_metric_skipped(self):
        """所有实验某指标都为 None → 不出现在 best"""
        c = self._make()
        exps = [
            {"tag": "A", "sharpe_ratio": None},
            {"tag": "B", "sharpe_ratio": None},
        ]
        best = c._determine_best(exps, ["sharpe_ratio"])
        assert "sharpe_ratio" not in best

    def test_total_fees_lower_is_better(self):
        """total_fees: 低值胜出"""
        c = self._make()
        exps = [
            {"tag": "X", "total_fees": 500},
            {"tag": "Y", "total_fees": 200},
        ]
        best = c._determine_best(exps, ["total_fees"])
        assert best["total_fees"] == "Y"

    def test_tag_empty_uses_run_id(self):
        """tag 为空 → 使用 run_id"""
        c = self._make()
        exps = [
            {"tag": "", "run_id": "run1", "annual_return": 0.1},
            {"tag": "", "run_id": "run2", "annual_return": 0.2},
        ]
        best = c._determine_best(exps, ["annual_return"])
        assert best["annual_return"] == "run2"


# ============================================================================
# _build_metrics_table edge cases
# ============================================================================


class TestMetricsTableEdge:

    def _make(self):
        return ExperimentComparator()

    def test_custom_metric_fallback_format(self):
        """未知指标 → 原始 str(v)"""
        c = self._make()
        exps = [{"tag": "A", "my_ratio": 1.234}]
        table = c._build_metrics_table(exps, ["my_ratio"])
        # Label should be the metric name itself
        assert table["rows"][0][0] == "my_ratio"
        # Value formatted as str
        assert table["rows"][0][1] == "1.234"

    def test_calmar_ratio_format(self):
        """calmar_ratio → 4 位小数"""
        c = self._make()
        exps = [{"tag": "A", "calmar_ratio": 1.23456}]
        table = c._build_metrics_table(exps, ["calmar_ratio"])
        assert table["rows"][0][1] == "1.2346"

    def test_total_fees_format(self):
        """total_fees → 千分位逗号"""
        c = self._make()
        exps = [{"tag": "A", "total_fees": 123456.78}]
        table = c._build_metrics_table(exps, ["total_fees"])
        assert table["rows"][0][1] == "123,456.78"

    def test_none_value_shows_na(self):
        """None → N/A 对所有指标"""
        c = self._make()
        exps = [{"tag": "A"}]
        table = c._build_metrics_table(exps, ["total_return", "calmar_ratio", "total_fees"])
        for row in table["rows"]:
            assert row[1] == "N/A"


# ============================================================================
# compare E2E edge cases
# ============================================================================


class TestCompareEdge:

    def test_nav_csv_missing_but_meta_exists(self, tmp_path):
        """meta.json 存在但 nav_series.csv 不存在 → nav_overlay 缺少该实验"""
        # Setup directory
        run_dir = tmp_path / "strat1" / "run1"
        run_dir.mkdir(parents=True)
        meta = {
            "tag": "exp1",
            "metrics": {"total_return": 0.5, "sharpe_ratio": 1.2},
        }
        (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

        c = ExperimentComparator(base_dir=str(tmp_path))
        result = c.compare([("strat1", "run1")])

        assert result["num_experiments"] == 1
        assert result["nav_overlay"] == {}

    def test_empty_tag_uses_strategy_run_id_label(self, tmp_path):
        """tag 为空字符串 → NAV label 使用 strategy/run_id"""
        run_dir = tmp_path / "s1" / "r1"
        run_dir.mkdir(parents=True)
        meta = {"tag": "", "metrics": {}}
        (run_dir / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
        # Create a simple NAV CSV
        nav_csv = run_dir / "nav_series.csv"
        with open(nav_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "nav"])
            writer.writeheader()
            writer.writerow({"date": "2024-01-01", "nav": "1000"})

        c = ExperimentComparator(base_dir=str(tmp_path))
        result = c.compare([("s1", "r1")])
        assert "s1/r1" in result["nav_overlay"]

    def test_missing_experiment_skipped(self, tmp_path):
        """实验目录不存在 → 跳过"""
        c = ExperimentComparator(base_dir=str(tmp_path))
        result = c.compare([("nonexistent", "run1")])
        assert result["num_experiments"] == 0


# ============================================================================
# generate_text_report edge cases
# ============================================================================


class TestTextReportEdge:

    def test_yearly_comparison_section(self):
        """含 yearly_comparison 的文本报告"""
        comparison = {
            "generated_at": "2024-01-01T00:00:00",
            "num_experiments": 2,
            "experiments": [
                {"tag": "A", "run_id": "r1", "strategy": "s1",
                 "fill_model": "nbo", "git_commit": "abc", "archived_at": "t1"},
                {"tag": "B", "run_id": "r2", "strategy": "s2",
                 "fill_model": "sbc", "git_commit": "def", "archived_at": "t2"},
            ],
            "metrics_table": {"header": ["指标", "A", "B"], "rows": []},
            "nav_overlay": {},
            "yearly_comparison": {
                "2023": [0.10, 0.20],
                "2024": [0.05, None],
            },
            "best_by": {},
        }
        c = ExperimentComparator()
        text = c.generate_text_report(comparison)
        assert "分年度收益对比" in text
        assert "2023" in text
        assert "2024" in text

    def test_yearly_none_shows_na(self):
        """分年度收益中 None → N/A"""
        comparison = {
            "generated_at": "now",
            "num_experiments": 1,
            "experiments": [
                {"tag": "X", "run_id": "r1", "strategy": "s",
                 "fill_model": "", "git_commit": "", "archived_at": ""},
            ],
            "metrics_table": {},
            "nav_overlay": {},
            "yearly_comparison": {"2024": [None]},
            "best_by": {},
        }
        c = ExperimentComparator()
        text = c.generate_text_report(comparison)
        assert "N/A" in text


# ============================================================================
# _load_nav_csv edge cases
# ============================================================================


class TestLoadNavCsvEdge:

    def test_empty_csv_header_only(self, tmp_path):
        """仅含表头的 CSV → 返回空列表"""
        csv_path = tmp_path / "nav.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "nav"])
            writer.writeheader()

        rows = ExperimentComparator._load_nav_csv(csv_path)
        assert rows == []

    def test_csv_with_extra_columns(self, tmp_path):
        """CSV 含额外列 → 正常加载"""
        csv_path = tmp_path / "nav.csv"
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["date", "nav", "extra"])
            writer.writeheader()
            writer.writerow({"date": "2024-01-01", "nav": "100", "extra": "x"})

        rows = ExperimentComparator._load_nav_csv(csv_path)
        assert len(rows) == 1
        assert "extra" in rows[0]


# ============================================================================
# _build_yearly_comparison edge cases
# ============================================================================


class TestYearlyComparisonEdge:

    def test_no_yearly_data(self):
        """所有实验无分年度数据 → 空结果"""
        c = ExperimentComparator()
        yearly_data = [
            {"label": "A", "yearly_returns": {}},
            {"label": "B", "yearly_returns": {}},
        ]
        result = c._build_yearly_comparison(yearly_data)
        assert result == {}

    def test_asymmetric_years(self):
        """不同实验覆盖不同年份"""
        c = ExperimentComparator()
        yearly_data = [
            {"label": "A", "yearly_returns": {"2023": 0.1}},
            {"label": "B", "yearly_returns": {"2024": 0.2}},
        ]
        result = c._build_yearly_comparison(yearly_data)
        assert "2023" in result
        assert "2024" in result
        # A has 2023 but B doesn't
        assert result["2023"] == [0.1, None]
        assert result["2024"] == [None, 0.2]
