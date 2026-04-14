"""WalkForward validation edge-case tests.

Covers untested branches:
- _compute_decay: train_value=0, negative train_value
- validate: runner returns empty/partial metrics
- TimeSeriesSplitter.split: start == end (degenerate)
- CrossValidator.split: n_folds=1, large gap_days
- compute_wf_score: all weights=0
- judge_walk_forward: boundary sharpe_decay == threshold, avg_test_sharpe == 0
- WalkForwardThresholds.from_yaml: empty YAML
- PurgedWalkForwardSplitter: purge_days == train_period
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from aipha.backtest.validation import (
    CrossValidator,
    PurgedWalkForwardSplitter,
    SplitPeriod,
    SplitResult,
    TimeSeriesSplitter,
    ValidationReport,
    WalkForwardThresholds,
    WalkForwardValidator,
    compute_wf_score,
    judge_walk_forward,
)


# ============================================================================
# _compute_decay edge cases
# ============================================================================


class TestComputeDecayEdge:

    def _make_validator(self):
        return WalkForwardValidator(
            train_period_days=30, test_period_days=10, n_splits=1
        )

    def test_train_value_zero(self):
        """train_value == 0 → 0.0 (division guard)"""
        v = self._make_validator()
        assert v._compute_decay(0.0, 0.5) == 0.0

    def test_negative_train_value(self):
        """负训练值 → abs() 处理"""
        v = self._make_validator()
        # train=-0.1, test=-0.05 → decay = (-0.1 - (-0.05)) / abs(-0.1) = -0.05/0.1 = -0.5
        decay = v._compute_decay(-0.1, -0.05)
        assert decay == pytest.approx(-0.5, abs=0.01)

    def test_both_zero(self):
        """both 0 → 0.0"""
        v = self._make_validator()
        assert v._compute_decay(0.0, 0.0) == 0.0


# ============================================================================
# validate: incomplete runner metrics
# ============================================================================


class _EmptyRunner:
    """返回空字典的 runner"""

    def run(self, start_date, end_date):
        return {}


class _PartialRunner:
    """只返回部分指标"""

    def run(self, start_date, end_date):
        return {"return": 0.05}  # no sharpe, no max_drawdown


class TestValidateRunnerEdge:

    def test_empty_metrics(self):
        """runner 返回 {} → 所有指标默认 0"""
        v = WalkForwardValidator(
            train_period_days=30, test_period_days=10, n_splits=1
        )
        report = v.validate(
            _EmptyRunner(),
            start_date=date(2023, 1, 1),
            end_date=date(2023, 6, 30),
        )
        for sr in report.split_results:
            assert sr.train_return == 0
            assert sr.train_sharpe == 0
            assert sr.test_return == 0

    def test_partial_metrics(self):
        """runner 返回部分指标 → 缺失项默认 0"""
        v = WalkForwardValidator(
            train_period_days=30, test_period_days=10, n_splits=1
        )
        report = v.validate(
            _PartialRunner(),
            start_date=date(2023, 1, 1),
            end_date=date(2023, 6, 30),
        )
        for sr in report.split_results:
            assert sr.train_sharpe == 0
            assert sr.train_max_drawdown == 0


# ============================================================================
# TimeSeriesSplitter: degenerate intervals
# ============================================================================


class TestTimeSeriesSplitterEdge:

    def test_start_equals_end(self):
        """start == end → 空分割"""
        splitter = TimeSeriesSplitter(
            train_period_days=30, test_period_days=10, n_splits=3
        )
        splits = splitter.split(date(2024, 1, 1), date(2024, 1, 1))
        assert splits == []

    def test_very_short_period(self):
        """总天数 < train_period → 空或极少分割"""
        splitter = TimeSeriesSplitter(
            train_period_days=365, test_period_days=90, n_splits=4
        )
        splits = splitter.split(date(2024, 1, 1), date(2024, 3, 1))
        assert len(splits) == 0


# ============================================================================
# CrossValidator edge cases
# ============================================================================


class TestCrossValidatorEdge:

    def test_n_folds_1(self):
        """n_folds=1 → 唯一折被跳过(i==0) → 空"""
        cv = CrossValidator(n_folds=1)
        splits = cv.split(date(2023, 1, 1), date(2023, 12, 31))
        assert splits == []

    def test_large_gap_days(self):
        """gap_days 很大 → train_end <= train_start → 跳过"""
        cv = CrossValidator(n_folds=3, gap_days=9999)
        splits = cv.split(date(2023, 1, 1), date(2023, 12, 31))
        assert splits == []

    def test_n_folds_2_normal(self):
        """n_folds=2 正常情况 → 1 个 split"""
        cv = CrossValidator(n_folds=2, gap_days=0)
        splits = cv.split(date(2023, 1, 1), date(2023, 12, 31))
        assert len(splits) == 1


# ============================================================================
# compute_wf_score edge cases
# ============================================================================


def _make_report(
    avg_sharpe_decay: float = 0.3,
    test_win_rate: float = 0.6,
    avg_test_sharpe: float = 1.0,
    decay_consistency: float = 0.1,
) -> ValidationReport:
    """Create a minimal report for scoring."""
    sp = SplitPeriod(
        train_start=date(2023, 1, 1),
        train_end=date(2023, 6, 30),
        test_start=date(2023, 7, 1),
        test_end=date(2023, 9, 30),
    )
    sr = SplitResult(
        split_id=1,
        period=sp,
        train_return=0.1,
        train_sharpe=1.5,
        train_max_drawdown=0.1,
        test_return=0.07,
        test_sharpe=avg_test_sharpe,
        test_max_drawdown=0.05,
        return_decay=0.3,
        sharpe_decay=avg_sharpe_decay,
    )
    report = ValidationReport(
        strategy_name="test",
        split_results=[sr],
        avg_train_return=0.1,
        avg_test_return=0.07,
        avg_train_sharpe=1.5,
        avg_test_sharpe=avg_test_sharpe,
        avg_return_decay=0.3,
        avg_sharpe_decay=avg_sharpe_decay,
        test_win_rate=test_win_rate,
        decay_consistency=decay_consistency,
    )
    return report


class TestComputeWfScoreEdge:

    def test_all_weights_zero(self):
        """所有权重为 0 → ZeroDivisionError 或 safe handling"""
        report = _make_report()
        # If the code doesn't guard against this, it'll raise ZeroDivisionError
        with pytest.raises(ZeroDivisionError):
            compute_wf_score(report, w_decay=0, w_win_rate=0, w_sharpe=0, w_consistency=0)

    def test_empty_results_returns_zero(self):
        """空 split_results → 0.0"""
        report = ValidationReport(
            strategy_name="test",
            split_results=[],
            avg_train_return=0.0,
            avg_test_return=0.0,
            avg_train_sharpe=0.0,
            avg_test_sharpe=0.0,
            avg_return_decay=0.0,
            avg_sharpe_decay=0.0,
            test_win_rate=0.0,
            decay_consistency=0.0,
        )
        assert compute_wf_score(report) == 0.0

    def test_perfect_score(self):
        """完美指标 → 接近 10.0"""
        report = _make_report(
            avg_sharpe_decay=-0.1,  # OOS > IS
            test_win_rate=1.0,
            avg_test_sharpe=3.0,
            decay_consistency=0.01,
        )
        score = compute_wf_score(report)
        assert score >= 9.5


# ============================================================================
# judge_walk_forward boundary cases
# ============================================================================


class TestJudgeBoundary:

    def test_decay_exactly_at_threshold(self):
        """avg_sharpe_decay == decay_threshold → < is False → checks if decay_ok"""
        # avg_sharpe_decay = 0.5, threshold = 0.5
        # decay_ok = (0.5 <= 0) or (0.5 < 0.5) → False or False → False → FAIL
        report = _make_report(avg_sharpe_decay=0.5)
        result = judge_walk_forward(report, decay_threshold=0.5, win_rate_threshold=0.0, sharpe_threshold=-1.0)
        assert result == "FAIL"

    def test_avg_test_sharpe_exactly_zero(self):
        """avg_test_sharpe == 0 → > 0 is False → FAIL"""
        report = _make_report(avg_test_sharpe=0.0, avg_sharpe_decay=-0.1)
        result = judge_walk_forward(report, sharpe_threshold=0.0)
        assert result == "FAIL"

    def test_win_rate_exactly_at_threshold(self):
        """test_win_rate == threshold → >= is True → PASS (if other checks pass)"""
        report = _make_report(avg_sharpe_decay=-0.1, avg_test_sharpe=1.0, test_win_rate=1.0)
        result = judge_walk_forward(report, win_rate_threshold=1.0, sharpe_threshold=0.0)
        # win_rate = 1.0 >= 1.0 → True
        assert result == "PASS"


# ============================================================================
# WalkForwardThresholds.from_yaml: empty YAML
# ============================================================================


class TestThresholdsFromYaml:

    def test_empty_yaml_file(self, tmp_path):
        """空 YAML → 默认阈值"""
        f = tmp_path / "empty.yml"
        f.write_text("")
        thresholds = WalkForwardThresholds.from_yaml(str(f))
        assert thresholds.decay_threshold == 0.50
        assert thresholds.win_rate_threshold == 0.50

    def test_null_yaml(self, tmp_path):
        """YAML 内容为 null → 默认"""
        f = tmp_path / "null.yml"
        f.write_text("null")
        thresholds = WalkForwardThresholds.from_yaml(str(f))
        assert thresholds.decay_threshold == 0.50


# ============================================================================
# PurgedWalkForwardSplitter: purge_days == train_period
# ============================================================================


class TestPurgedSplitterEdge:

    def test_purge_equals_train(self):
        """purge_days == train_period → effective_train_end < train_start → skip"""
        splitter = PurgedWalkForwardSplitter(
            train_period_days=30,
            test_period_days=10,
            n_splits=2,
            purge_days=30,
        )
        splits = splitter.split(date(2023, 1, 1), date(2023, 12, 31))
        assert splits == []
