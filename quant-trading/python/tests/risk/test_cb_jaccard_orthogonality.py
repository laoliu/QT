"""CB Jaccard 正交性分析模块测试"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from aipha.risk.cb_jaccard_orthogonality import (
    CBJaccardReport,
    JaccardPairResult,
    _jaccard,
    analyze_cb_orthogonality,
    compute_pairwise_jaccard,
    extract_daily_holdings,
)


class TestJaccard:
    """Jaccard 系数计算。"""

    def test_identical_sets(self):
        assert _jaccard({"A", "B"}, {"A", "B"}) == 1.0

    def test_disjoint_sets(self):
        assert _jaccard({"A", "B"}, {"C", "D"}) == 0.0

    def test_partial_overlap(self):
        j = _jaccard({"A", "B", "C"}, {"B", "C", "D"})
        assert abs(j - 2.0 / 4.0) < 1e-9  # |{B,C}|/|{A,B,C,D}|

    def test_empty_sets(self):
        assert _jaccard(set(), set()) == 0.0

    def test_one_empty(self):
        assert _jaccard({"A"}, set()) == 0.0


class TestExtractDailyHoldings:
    """从 daily_snapshots.csv 提取持仓。"""

    def _make_csv(self, tmp_path: Path, rows: list[dict]) -> Path:
        df = pd.DataFrame(rows)
        p = tmp_path / "daily_snapshots.csv"
        df.to_csv(p, index=False)
        return p

    def test_basic_extraction(self, tmp_path):
        path = self._make_csv(tmp_path, [
            {"date": "2024-01-02", "nav": 100, "positions": "{'A': 10, 'B': 20}"},
            {"date": "2024-01-03", "nav": 101, "positions": "{'B': 20, 'C': 15}"},
        ])
        h = extract_daily_holdings(path)
        assert h["2024-01-02"] == {"A", "B"}
        assert h["2024-01-03"] == {"B", "C"}

    def test_empty_positions(self, tmp_path):
        path = self._make_csv(tmp_path, [
            {"date": "2024-01-02", "nav": 100, "positions": "{}"},
        ])
        h = extract_daily_holdings(path)
        assert h["2024-01-02"] == set()

    def test_nan_positions(self, tmp_path):
        path = self._make_csv(tmp_path, [
            {"date": "2024-01-02", "nav": 100, "positions": float("nan")},
        ])
        h = extract_daily_holdings(path)
        assert h["2024-01-02"] == set()


class TestComputePairwiseJaccard:
    """两策略 Jaccard 时序统计。"""

    def test_identical_holdings(self):
        h = {"d1": {"A", "B"}, "d2": {"A", "B"}}
        pr = compute_pairwise_jaccard(h, h, name_a="X", name_b="Y")
        assert pr.mean_jaccard == 1.0
        assert not pr.passed  # 1.0 > 0.30

    def test_disjoint_holdings(self):
        ha = {"d1": {"A"}, "d2": {"A"}}
        hb = {"d1": {"B"}, "d2": {"B"}}
        pr = compute_pairwise_jaccard(ha, hb, name_a="X", name_b="Y")
        assert pr.mean_jaccard == 0.0
        assert pr.passed

    def test_partial_overlap(self):
        ha = {"d1": {"A", "B", "C"}, "d2": {"A", "B"}}
        hb = {"d1": {"B", "C", "D"}, "d2": {"C", "D"}}
        pr = compute_pairwise_jaccard(ha, hb, name_a="X", name_b="Y")
        # d1: 2/4=0.5, d2: 0/4=0.0 → mean=0.25
        assert abs(pr.mean_jaccard - 0.25) < 0.01
        assert pr.passed  # 0.25 < 0.30

    def test_empty_common_dates(self):
        ha = {"d1": {"A"}}
        hb = {"d2": {"B"}}
        pr = compute_pairwise_jaccard(ha, hb)
        assert pr.total_days == 0
        assert pr.passed

    def test_custom_threshold(self):
        h = {"d1": {"A", "B"}, "d2": {"A", "B"}}
        pr = compute_pairwise_jaccard(h, h, threshold=0.50)
        assert not pr.passed  # 1.0 > 0.50
        pr2 = compute_pairwise_jaccard(h, h, threshold=1.01)
        assert pr2.passed  # 1.0 <= 1.01


class TestAnalyzeCBOrthogonality:
    """完整分析流程。"""

    def _make_snapshot(self, tmp_path: Path, name: str, data: list[dict]) -> Path:
        d = tmp_path / name
        d.mkdir(exist_ok=True)
        df = pd.DataFrame(data)
        p = d / "daily_snapshots.csv"
        df.to_csv(p, index=False)
        return p

    def test_two_orthogonal_strategies(self, tmp_path):
        sa = self._make_snapshot(tmp_path, "strat_a", [
            {"date": "2024-01-02", "nav": 100, "positions": "{'A': 10}"},
            {"date": "2024-01-03", "nav": 101, "positions": "{'A': 10}"},
        ])
        sb = self._make_snapshot(tmp_path, "strat_b", [
            {"date": "2024-01-02", "nav": 100, "positions": "{'Z': 10}"},
            {"date": "2024-01-03", "nav": 101, "positions": "{'Z': 10}"},
        ])
        report = analyze_cb_orthogonality({"a": sa, "b": sb})
        assert report.overall_passed
        assert len(report.pair_results) == 1
        assert report.pair_results[0].mean_jaccard == 0.0

    def test_high_overlap_fails(self, tmp_path):
        sa = self._make_snapshot(tmp_path, "strat_a", [
            {"date": "2024-01-02", "nav": 100, "positions": "{'A': 10, 'B': 20}"},
        ])
        sb = self._make_snapshot(tmp_path, "strat_b", [
            {"date": "2024-01-02", "nav": 100, "positions": "{'A': 10, 'B': 20}"},
        ])
        report = analyze_cb_orthogonality({"a": sa, "b": sb})
        assert not report.overall_passed
        assert report.pair_results[0].mean_jaccard == 1.0

    def test_three_strategies_pairs(self, tmp_path):
        sa = self._make_snapshot(tmp_path, "a", [
            {"date": "d1", "nav": 100, "positions": "{'X': 1}"},
        ])
        sb = self._make_snapshot(tmp_path, "b", [
            {"date": "d1", "nav": 100, "positions": "{'Y': 1}"},
        ])
        sc = self._make_snapshot(tmp_path, "c", [
            {"date": "d1", "nav": 100, "positions": "{'Z': 1}"},
        ])
        report = analyze_cb_orthogonality({"a": sa, "b": sb, "c": sc})
        assert len(report.pair_results) == 3  # C(3,2) = 3 pairs
        assert report.overall_passed

    def test_to_dict_serialization(self, tmp_path):
        sa = self._make_snapshot(tmp_path, "a", [
            {"date": "d1", "nav": 100, "positions": "{'X': 1}"},
        ])
        sb = self._make_snapshot(tmp_path, "b", [
            {"date": "d1", "nav": 100, "positions": "{'Y': 1}"},
        ])
        report = analyze_cb_orthogonality({"a": sa, "b": sb})
        d = report.to_dict()
        assert "threshold" in d
        assert "pair_results" in d
        assert isinstance(d["pair_results"], list)

    def test_single_strategy_insufficient(self, tmp_path):
        sa = self._make_snapshot(tmp_path, "a", [
            {"date": "d1", "nav": 100, "positions": "{'X': 1}"},
        ])
        report = analyze_cb_orthogonality({"a": sa})
        assert len(report.pair_results) == 0
