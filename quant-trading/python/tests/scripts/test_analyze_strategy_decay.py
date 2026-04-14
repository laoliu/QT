"""tests/scripts/test_analyze_strategy_decay.py

analyze_strategy_decay 核心函数单元测试（≥8 条）。

通过构造真实数据结构实例测试，不 Mock 业务对象。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# 将脚本目录加进 sys.path
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from scripts.analyze_strategy_decay import (
    HARD_AR_THRESHOLD,
    HARD_SHARPE_THRESHOLD,
    MAX_SHARPE_DECAY,
    PortfolioResult,
    StrategyDecayStats,
    _parse_splits,
    analyze_portfolio_combinations,
    build_report,
    compute_decay_stats,
    rank_strategies,
)


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _make_wf_row(
    name: str,
    verdict: str = "PASS",
    score: float = 4.0,
    n_splits: int = 4,
    is_ar: float = 0.40,
    oos_ar: float = 0.30,
    is_sharpe: float = 1.5,
    oos_sharpe: float = 1.0,
) -> dict:
    """构造一条 WF DB 行（真实数据结构）。"""
    splits = []
    for i in range(n_splits):
        decay_r = (is_ar - oos_ar) / abs(is_ar) if abs(is_ar) > 1e-9 else 0.0
        decay_s = (is_sharpe - oos_sharpe) / abs(is_sharpe) if abs(is_sharpe) > 1e-9 else 0.0
        splits.append({
            "split_id": i + 1,
            "train_return": is_ar,
            "test_return": oos_ar,
            "train_sharpe": is_sharpe,
            "test_sharpe": oos_sharpe,
            "return_decay": decay_r,
            "sharpe_decay": decay_s,
        })
    return {
        "wf_id": f"{name}_wf",
        "strategy_name": name,
        "verdict": verdict,
        "score": score,
        "config_path": f"configs/cb_strategies/{name}.yml",
        "data_range": "2017-01-01 ~ 2025-01-01",
        "n_splits": n_splits,
        "splits_json": splits,
    }


def _make_stats(
    name: str,
    verdict: str = "PASS",
    oos_ar: float = 0.30,
    oos_sharpe: float = 1.0,
    sharpe_decay: float = 0.20,
    oos_positive: float = 1.0,
) -> StrategyDecayStats:
    return StrategyDecayStats(
        strategy_name=name,
        verdict=verdict,
        score=4.0,
        is_ar_mean=0.4,
        is_sharpe_mean=1.4,
        oos_ar_mean=oos_ar,
        oos_sharpe_mean=oos_sharpe,
        ar_decay=0.25,
        sharpe_decay=sharpe_decay,
        oos_positive_ratio=oos_positive,
        n_splits=4,
    )


# ── _parse_splits ─────────────────────────────────────────────────────────────

class TestParseSplits:
    def test_list_passthrough(self):
        data = [{"split_id": 1, "test_return": 0.1}]
        assert _parse_splits(data) == data

    def test_json_string(self):
        data = [{"split_id": 1, "test_return": 0.2}]
        assert _parse_splits(json.dumps(data)) == data

    def test_none_returns_empty(self):
        assert _parse_splits(None) == []

    def test_invalid_json_returns_empty(self):
        assert _parse_splits("not json {{{") == []


# ── compute_decay_stats ───────────────────────────────────────────────────────

class TestComputeDecayStats:
    def test_basic_fields(self):
        row = _make_wf_row("small_size", oos_ar=0.30, is_ar=0.40)
        stats = compute_decay_stats(row)

        assert stats.strategy_name == "small_size"
        assert stats.verdict == "PASS"
        assert abs(stats.oos_ar_mean - 0.30) < 1e-9
        assert abs(stats.is_ar_mean - 0.40) < 1e-9
        assert stats.n_splits == 4

    def test_ar_decay_direction(self):
        """IS > OOS → decay > 0（IS 端高）。"""
        row = _make_wf_row("x", is_ar=0.50, oos_ar=0.25)
        stats = compute_decay_stats(row)
        assert stats.ar_decay > 0

    def test_oos_positive_ratio_all_positive(self):
        row = _make_wf_row("y", oos_ar=0.20, n_splits=4)
        stats = compute_decay_stats(row)
        assert stats.oos_positive_ratio == 1.0

    def test_oos_positive_ratio_half_negative(self):
        # 手动构造 2 正 2 负折
        splits = [
            {"split_id": 1, "train_return": 0.3, "test_return": 0.2, "train_sharpe": 1.0, "test_sharpe": 0.8},
            {"split_id": 2, "train_return": 0.3, "test_return": 0.1, "train_sharpe": 1.0, "test_sharpe": 0.5},
            {"split_id": 3, "train_return": 0.3, "test_return": -0.1, "train_sharpe": 1.0, "test_sharpe": -0.3},
            {"split_id": 4, "train_return": 0.3, "test_return": -0.05, "train_sharpe": 1.0, "test_sharpe": -0.2},
        ]
        row = _make_wf_row("z")
        row["splits_json"] = splits
        stats = compute_decay_stats(row)
        assert abs(stats.oos_positive_ratio - 0.5) < 1e-9

    def test_empty_splits_returns_zero_stats(self):
        row = _make_wf_row("empty_strat")
        row["splits_json"] = []
        stats = compute_decay_stats(row)
        assert stats.n_splits == 0
        assert stats.oos_ar_mean == 0.0


# ── passes_hard_threshold ─────────────────────────────────────────────────────

class TestHardThreshold:
    def test_all_met_returns_true(self):
        s = _make_stats("a", oos_ar=0.25, oos_sharpe=1.0, sharpe_decay=0.30)
        assert s.passes_hard_threshold is True

    def test_low_oos_ar_fails(self):
        s = _make_stats("b", oos_ar=0.10, oos_sharpe=1.0, sharpe_decay=0.20)
        assert s.passes_hard_threshold is False

    def test_low_sharpe_fails(self):
        s = _make_stats("c", oos_ar=0.25, oos_sharpe=0.5, sharpe_decay=0.20)
        assert s.passes_hard_threshold is False

    def test_high_sharpe_decay_fails(self):
        s = _make_stats("d", oos_ar=0.25, oos_sharpe=1.0, sharpe_decay=0.70)
        assert s.passes_hard_threshold is False


# ── rank_strategies ───────────────────────────────────────────────────────────

class TestRankStrategies:
    def test_pass_before_fail(self):
        stats = [
            _make_stats("fail_one", verdict="FAIL", oos_ar=0.5),
            _make_stats("pass_low", verdict="PASS", oos_ar=0.2),
        ]
        ranked = rank_strategies(stats)
        assert ranked[0].verdict == "PASS"
        assert ranked[-1].verdict == "FAIL"

    def test_higher_oos_ar_ranked_first(self):
        stats = [
            _make_stats("b", oos_ar=0.25),
            _make_stats("a", oos_ar=0.40),
        ]
        ranked = rank_strategies(stats)
        assert ranked[0].strategy_name == "a"

    def test_high_sharpe_decay_penalizes_ranking(self):
        """高 sharpe_decay 策略即使 OOS AR 更高也可能排后。"""
        stats = [
            _make_stats("high_ar_bad_decay", oos_ar=0.45, sharpe_decay=2.0, oos_positive=0.5),
            _make_stats("lower_ar_good_decay", oos_ar=0.38, sharpe_decay=0.10, oos_positive=1.0),
        ]
        ranked = rank_strategies(stats)
        # lower_ar_good_decay quality = 0.38 * 1.0 - 0.3 * 0.10 = 0.377
        # high_ar_bad_decay quality = 0.45 * 0.5 - 0.3 * 2.0 = 0.225 - 0.60 = -0.375
        assert ranked[0].strategy_name == "lower_ar_good_decay"


# ── analyze_portfolio_combinations ────────────────────────────────────────────

class TestPortfolioCombinations:
    def _make_ranked(self, n: int = 10) -> list[StrategyDecayStats]:
        return [
            _make_stats(f"strat_{i}", oos_ar=0.30 - i * 0.01, oos_sharpe=1.0)
            for i in range(n)
        ]

    def test_returns_results_for_valid_sizes(self):
        ranked = self._make_ranked(10)
        results = analyze_portfolio_combinations(ranked, sizes=[3, 5])
        assert len(results) == 2

    def test_skips_size_exceeding_available(self):
        ranked = self._make_ranked(4)
        results = analyze_portfolio_combinations(ranked, sizes=[3, 5, 10])
        sizes = [r.size for r in results]
        assert 3 in sizes
        assert 5 not in sizes  # 超出 4 个 PASS 策略
        assert 10 not in sizes

    def test_equal_weight_ar_is_mean(self):
        stats = [
            _make_stats(f"s{i}", oos_ar=float(i + 1) * 0.10)
            for i in range(3)
        ]
        results = analyze_portfolio_combinations(stats, sizes=[3])
        expected_mean = (0.10 + 0.20 + 0.30) / 3
        assert abs(results[0].equal_weight_oos_ar - expected_mean) < 1e-9

    def test_all_pass_threshold_flag(self):
        stats = [
            _make_stats("good_a", oos_ar=0.25, oos_sharpe=1.0, sharpe_decay=0.20),
            _make_stats("good_b", oos_ar=0.22, oos_sharpe=0.9, sharpe_decay=0.30),
            _make_stats("bad_c",  oos_ar=0.10, oos_sharpe=0.5, sharpe_decay=0.20),
        ]
        results = analyze_portfolio_combinations(stats, sizes=[2, 3])
        by_size = {r.size: r for r in results}
        assert by_size[2].all_pass_threshold is True
        assert by_size[3].all_pass_threshold is False


# ── build_report ──────────────────────────────────────────────────────────────

class TestBuildReport:
    def test_counts_correct(self):
        stats = [
            _make_stats("a", verdict="PASS", oos_ar=0.25, oos_sharpe=1.0, sharpe_decay=0.20),
            _make_stats("b", verdict="PASS", oos_ar=0.10, oos_sharpe=0.6, sharpe_decay=0.20),
            _make_stats("c", verdict="FAIL", oos_ar=0.05, oos_sharpe=0.3, sharpe_decay=0.80),
        ]
        report = build_report(stats, [])
        assert report.n_strategies_analyzed == 3
        assert report.n_wf_pass == 2
        assert report.n_threshold_pass == 1  # 只有 "a" 满足全部

    def test_top5_oos_ar_mean_computed(self):
        stats = [_make_stats(f"s{i}", oos_ar=float(i + 1) * 0.10) for i in range(8)]
        report = build_report(stats, [])
        # Top-5 OOS AR：0.80, 0.70, 0.60, 0.50, 0.40 → mean = 0.60
        assert abs(report.top5_oos_ar_mean - 0.60) < 1e-6
