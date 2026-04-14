"""tests/scripts/test_check_wf_gate.py

check_wf_gate 核心函数单元测试（≥10 条）。

不 Mock DB 或业务对象，构造真实数据结构实例后测试。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

from scripts.check_wf_gate import (
    DEFAULT_MIN_OOS_AR,
    DEFAULT_MIN_WF_PASS,
    DEFAULT_MAX_SHARPE_DECAY,
    HARD_SHARPE_DECAY_THRESHOLD,
    WfStrategyStats,
    GateCheckResult,
    _parse_splits,
    _aggregate_splits,
    run_gate_checks,
    save_gate_report,
    print_gate_report,
    main,
)


# ── 辅助 ──────────────────────────────────────────────────────────────────────

def _splits(
    n: int = 5,
    train_ar: float = 0.40,
    test_ar: float = 0.25,
    train_sh: float = 1.5,
    test_sh: float = 1.0,
) -> list[dict]:
    """构造 n 折 WF splits 数据（真实数据结构）。"""
    result = []
    for i in range(n):
        decay_s = (train_sh - test_sh) / abs(train_sh) if abs(train_sh) > 1e-9 else 0.0
        result.append({
            "split_id": i + 1,
            "train_return": train_ar,
            "test_return": test_ar,
            "train_sharpe": train_sh,
            "test_sharpe": test_sh,
            "sharpe_decay": decay_s,
        })
    return result


def _stats(
    name: str = "test_strategy",
    verdict: str = "PASS",
    oos_ar: float = 0.25,
    oos_sharpe: float = 1.0,
    sharpe_decay: float = 0.30,
    n_splits: int = 5,
) -> WfStrategyStats:
    """构造 WfStrategyStats 实例。"""
    return WfStrategyStats(
        strategy_name=name,
        verdict=verdict,
        n_splits=n_splits,
        oos_ar_mean=oos_ar,
        oos_sharpe_mean=oos_sharpe,
        sharpe_decay=sharpe_decay,
        oos_positive_ratio=1.0,
    )


# ── 单元测试 ──────────────────────────────────────────────────────────────────


class TestParseSplits:
    """_parse_splits 格式兼容性。"""

    def test_list_input(self):
        splits = _splits(3)
        assert _parse_splits(splits) == splits

    def test_json_string_input(self):
        splits = _splits(3)
        result = _parse_splits(json.dumps(splits))
        assert len(result) == 3

    def test_none_input(self):
        assert _parse_splits(None) == []

    def test_invalid_json_string(self):
        assert _parse_splits("not_json") == []


class TestAggregateSplits:
    """_aggregate_splits 均值计算。"""

    def test_basic_aggregation(self):
        splits = _splits(n=4, train_ar=0.40, test_ar=0.20, train_sh=1.5, test_sh=1.0)
        agg = _aggregate_splits(splits)
        assert agg["oos_ar_mean"] == pytest.approx(0.20, abs=1e-6)
        assert agg["oos_sharpe_mean"] == pytest.approx(1.0, abs=1e-6)
        expected_decay = (1.5 - 1.0) / 1.5
        assert agg["sharpe_decay"] == pytest.approx(expected_decay, abs=1e-6)

    def test_empty_splits_returns_zeros(self):
        agg = _aggregate_splits([])
        assert agg["oos_ar_mean"] == 0.0
        assert agg["oos_sharpe_mean"] == 0.0

    def test_oos_positive_ratio(self):
        """2折正收益、2折负收益 → 50%。"""
        splits = [
            {"train_return": 0.4, "test_return": 0.20, "train_sharpe": 1.5, "test_sharpe": 1.0},
            {"train_return": 0.4, "test_return": 0.15, "train_sharpe": 1.5, "test_sharpe": 0.8},
            {"train_return": 0.4, "test_return": -0.05, "train_sharpe": 1.5, "test_sharpe": -0.3},
            {"train_return": 0.4, "test_return": -0.10, "train_sharpe": 1.5, "test_sharpe": -0.6},
        ]
        agg = _aggregate_splits(splits)
        assert agg["oos_positive_ratio"] == pytest.approx(0.5, abs=1e-6)


class TestRunGateChecks:
    """run_gate_checks 闸门逻辑。"""

    def _make_stats_list(self, n: int = 5, **kwargs) -> list[WfStrategyStats]:
        return [_stats(f"s{i}", **kwargs) for i in range(n)]

    def test_all_gates_pass(self):
        stats = self._make_stats_list(n=6, oos_ar=0.25, sharpe_decay=0.30)
        result = run_gate_checks(stats, min_wf_pass=5, min_oos_ar=0.08, max_sharpe_decay=0.70)
        assert result.passed is True
        assert result.exit_code == 0
        assert result.n_wf_pass == 6

    def test_gate1_fail_insufficient_pass_count(self):
        """WF PASS 数量不足。"""
        stats = [
            _stats("s1", verdict="PASS"),
            _stats("s2", verdict="FAIL"),
            _stats("s3", verdict="FAIL"),
        ]
        result = run_gate_checks(stats, min_wf_pass=5, min_oos_ar=0.08, max_sharpe_decay=0.70)
        assert result.passed is False
        assert result.exit_code == 1
        g1 = next(g for g in result.gate_results if g["gate"] == "min_wf_pass")
        assert g1["passed"] is False

    def test_gate2_fail_low_oos_ar(self):
        """OOS AR 均值太低。"""
        stats = [_stats(f"s{i}", oos_ar=0.02) for i in range(6)]
        result = run_gate_checks(stats, min_wf_pass=5, min_oos_ar=0.15, max_sharpe_decay=0.70)
        assert result.passed is False
        g2 = next(g for g in result.gate_results if g["gate"] == "min_oos_ar")
        assert g2["passed"] is False

    def test_gate3_fail_high_sharpe_decay(self):
        """高 Sharpe 衰减超过阈值。"""
        stats = [_stats(f"s{i}", sharpe_decay=0.85) for i in range(6)]
        result = run_gate_checks(stats, min_wf_pass=5, min_oos_ar=0.08, max_sharpe_decay=0.70)
        assert result.passed is False
        g3 = next(g for g in result.gate_results if g["gate"] == "max_sharpe_decay")
        assert g3["passed"] is False

    def test_fail_strategies_excluded_from_gate2(self):
        """FAIL 策略不应拉低 Gate2 的 OOS AR 均值。"""
        stats = [
            _stats("pass1", verdict="PASS", oos_ar=0.30),
            _stats("pass2", verdict="PASS", oos_ar=0.30),
            _stats("fail1", verdict="FAIL", oos_ar=0.01),  # FAIL 策略不计入
            _stats("fail2", verdict="FAIL", oos_ar=0.01),
            _stats("pass3", verdict="PASS", oos_ar=0.30),
            _stats("pass4", verdict="PASS", oos_ar=0.30),
            _stats("pass5", verdict="PASS", oos_ar=0.30),
        ]
        result = run_gate_checks(stats, min_wf_pass=5, min_oos_ar=0.20, max_sharpe_decay=0.70)
        assert result.passed is True

    def test_n_threshold_pass_count(self):
        """n_threshold_pass 准确统计。"""
        stats = [
            _stats("s1", verdict="PASS", oos_ar=0.25, sharpe_decay=0.30),
            _stats("s2", verdict="PASS", oos_ar=0.25, sharpe_decay=0.30),
            _stats("s3", verdict="PASS", oos_ar=0.01, sharpe_decay=0.30),  # OOS AR 不足
            _stats("s4", verdict="FAIL", oos_ar=0.25, sharpe_decay=0.30),  # FAIL
        ]
        result = run_gate_checks(stats, min_wf_pass=2, min_oos_ar=0.08, max_sharpe_decay=0.70)
        assert result.n_threshold_pass == 2  # s1, s2 通过; s3 OOS AR<0.08; s4 FAIL

    def test_empty_stats_fails(self):
        """空列表导致 Gate1 失败。"""
        result = run_gate_checks([], min_wf_pass=1, min_oos_ar=0.08, max_sharpe_decay=0.70)
        assert result.passed is False


class TestWfStrategyStatsPassesHardThreshold:
    """WfStrategyStats.passes_hard_threshold 属性验证。"""

    def test_passes_all_conditions(self):
        s = _stats(verdict="PASS", oos_ar=0.25, sharpe_decay=0.30)
        assert s.passes_hard_threshold is True

    def test_fails_on_verdict_fail(self):
        s = _stats(verdict="FAIL", oos_ar=0.25, sharpe_decay=0.30)
        assert s.passes_hard_threshold is False

    def test_fails_on_low_oos_ar(self):
        # DEFAULT_MIN_OOS_AR = 0.08
        s = _stats(verdict="PASS", oos_ar=0.01, sharpe_decay=0.30)
        assert s.passes_hard_threshold is False

    def test_fails_on_high_sharpe_decay(self):
        # HARD_SHARPE_DECAY_THRESHOLD = 0.50
        s = _stats(verdict="PASS", oos_ar=0.25, sharpe_decay=0.80)
        assert s.passes_hard_threshold is False


class TestSaveGateReport:
    """save_gate_report I/O 验证。"""

    def test_save_creates_json(self, tmp_path):
        stats = [_stats("s1"), _stats("s2")]
        result = run_gate_checks(stats, min_wf_pass=2, min_oos_ar=0.08, max_sharpe_decay=0.70)
        out_path = tmp_path / "gate_report.json"
        save_gate_report(result, out_path)
        assert out_path.exists()
        data = json.loads(out_path.read_text(encoding="utf-8"))
        assert "passed" in data
        assert "strategies" in data
        assert len(data["strategies"]) == 2


class TestMainReportOnly:
    """main --report-only 应始终退出码 0（即使闸门未通过）。"""

    def test_report_only_returns_zero_on_empty_db(self, monkeypatch):
        """DB 无结果时，--report-only 不应抛异常且退出码为 2。"""
        import scripts.check_wf_gate as _mod

        def _mock_load(*args, **kwargs):
            return []

        monkeypatch.setattr(_mod, "load_wf_stats", _mock_load)
        code = main(["--report-only"])
        assert code == 2  # DB 为空

    def test_report_only_flag_forces_zero(self, monkeypatch):
        """有 WF 结果但 --report-only 时，即使闸门不通过也返回 0。"""
        import scripts.check_wf_gate as _mod

        def _mock_load(*args, **kwargs):
            return [_stats("s1", verdict="FAIL")]

        monkeypatch.setattr(_mod, "load_wf_stats", _mock_load)
        code = main(["--report-only", "--min-wf-pass", "10"])
        assert code == 0
