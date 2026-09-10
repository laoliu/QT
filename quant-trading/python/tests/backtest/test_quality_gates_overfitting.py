"""过拟合门禁测试 — DSR / PBO / IS→OOS 衰减接入默认门禁。

样本内指标再漂亮也不能证明信号在样本外还在。本文件覆盖:
1. required=False 的「未检验」语义（不等于通过，也不等于失败）
2. DSR / PBO / 衰减门禁的判定
3. compute_overfitting_metrics 的计算与降级
4. 阈值与 governance/statistical_gate.py 保持一致
"""

from __future__ import annotations

import math
import random

import pytest

from aipha.backtest.quality_gates import (
    DEFAULT_QUALITY_GATES,
    DSR_P_MAX,
    OOS_SHARPE_DECAY_MAX,
    OVERFITTING_QUALITY_GATES,
    PBO_MAX,
    QualityChecker,
    check_overfitting_gates,
    compute_overfitting_metrics,
    format_quality_report,
)


def _in_sample_metrics(**over) -> dict[str, float]:
    """一份「样本内全部达标」的指标，方便单独测过拟合门禁。"""
    base = {
        "sharpe_ratio": 1.5,
        "max_drawdown_pct": 0.15,
        "win_rate": 0.55,
        "total_return": 0.40,
        "calmar_ratio": 1.2,
        "annualized_return": 0.20,
        "profit_factor": 1.8,
        "avg_positions": 8.0,
        "turnover_rate": 10.0,
        "max_concentration": 0.20,
    }
    base.update(over)
    return base


class TestThresholdConsistency:
    """阈值与晋级门禁同源"""

    def test_dsr_threshold_matches_governance(self) -> None:
        from aipha.governance.statistical_gate import DSR_P_SOFT

        assert DSR_P_MAX == DSR_P_SOFT

    def test_pbo_threshold_matches_governance(self) -> None:
        from aipha.governance import statistical_gate

        assert PBO_MAX == statistical_gate.PBO_MAX

    def test_decay_threshold_matches_governance(self) -> None:
        from aipha.governance.statistical_gate import WF_DECAY_MAX

        assert OOS_SHARPE_DECAY_MAX == WF_DECAY_MAX


class TestDefaultGatesInclude:
    """过拟合门禁进入默认配置"""

    def test_included_in_defaults(self) -> None:
        metrics = {g["metric"] for g in DEFAULT_QUALITY_GATES}

        assert {"dsr", "dsr_p_value", "pbo", "oos_sharpe_decay"} <= metrics

    def test_all_optional(self) -> None:
        """全部 required=False —— 否则所有现有回测立刻变红"""
        assert all(g["required"] is False for g in OVERFITTING_QUALITY_GATES)

    def test_in_sample_gates_stay_required(self) -> None:
        """样本内门禁行为不变（缺失仍算失败）"""
        overfit_metrics = {g["metric"] for g in OVERFITTING_QUALITY_GATES}
        for g in DEFAULT_QUALITY_GATES:
            if g["metric"] not in overfit_metrics:
                assert g.get("required", True) is True


class TestSkippedSemantics:
    """未检验 ≠ 通过，也 ≠ 失败"""

    def test_absent_optional_metric_is_skipped(self) -> None:
        report = QualityChecker.default().check(_in_sample_metrics())

        assert report.all_passed
        assert {gr.gate.metric for gr in report.skipped} == {
            "dsr", "dsr_p_value", "pbo", "oos_sharpe_decay",
        }
        assert report.errors == []

    def test_absent_required_metric_still_fails(self) -> None:
        checker = QualityChecker.from_config([
            {"metric": "sharpe_ratio", "operator": ">=", "threshold": 1.0},
        ])
        report = checker.check({})

        assert not report.all_passed
        assert report.skipped == []

    def test_skipped_named_in_formatted_report(self) -> None:
        """报告必须点名缺哪项检验，否则和做过检验看不出区别"""
        report = QualityChecker.default().check(_in_sample_metrics())
        text = format_quality_report(report)

        assert "未检验" in text
        assert "dsr_p_value" in text
        assert "不等于「通过」" in text

    def test_provided_optional_metric_is_not_skipped(self) -> None:
        report = QualityChecker.default().check(
            _in_sample_metrics(dsr_p_value=0.02, dsr=2.1, pbo=0.2,
                               oos_sharpe_decay=0.3),
        )

        assert report.skipped == []
        assert report.all_passed

    def test_non_numeric_optional_metric_still_fails(self) -> None:
        """给了值但不是数 → 是数据问题，不该被当成未检验放过"""
        report = QualityChecker.default().check(
            _in_sample_metrics(dsr_p_value="n/a"),
        )

        assert not report.all_passed


class TestOverfittingVerdicts:
    """过拟合门禁判定"""

    def test_high_dsr_p_value_fails(self) -> None:
        """样本内 Sharpe 1.5 但多重检验后不显著 → 硬门禁失败"""
        report = QualityChecker.default().check(
            _in_sample_metrics(dsr_p_value=0.42),
        )

        assert not report.all_passed
        assert any(gr.gate.metric == "dsr_p_value" for gr in report.errors)

    def test_dsr_at_threshold_passes(self) -> None:
        report = QualityChecker.default().check(
            _in_sample_metrics(dsr_p_value=DSR_P_MAX),
        )

        assert not any(gr.gate.metric == "dsr_p_value" for gr in report.errors)

    def test_negative_dsr_warns_only(self) -> None:
        """DSR 为负是软告警 —— p 值才是硬判定"""
        report = QualityChecker.default().check(
            _in_sample_metrics(dsr=-0.5, dsr_p_value=0.01),
        )

        assert report.all_passed
        assert any(gr.gate.metric == "dsr" for gr in report.warnings)

    def test_high_pbo_fails(self) -> None:
        report = QualityChecker.default().check(_in_sample_metrics(pbo=0.72))

        assert not report.all_passed
        assert any(gr.gate.metric == "pbo" for gr in report.errors)

    def test_severe_decay_fails(self) -> None:
        """IS 1.5 → OOS 0.4 衰减 73%，样本外几乎没剩"""
        report = QualityChecker.default().check(
            _in_sample_metrics(oos_sharpe_decay=0.73),
        )

        assert not report.all_passed
        assert any(gr.gate.metric == "oos_sharpe_decay" for gr in report.errors)


class TestCheckOverfittingGates:
    """只跑过拟合门禁 — 接 WF/CPCV 已算好的 DSR/PBO"""

    def test_wf_summary_shape_accepted(self) -> None:
        """summarize_wf_statistical_validation 的输出可直接喂进来"""
        report = check_overfitting_gates({
            "dsr": 1.8, "dsr_p_value": 0.03, "pbo": 0.22,
            "hlz_adjusted_t": 2.4, "n_obs": 240, "pbo_method": "fold_matrix",
        }, strategy_name="wf")

        assert report.all_passed
        assert report.skipped == [] or all(
            gr.gate.metric == "oos_sharpe_decay" for gr in report.skipped
        )

    def test_none_values_count_as_missing(self) -> None:
        """上游用 None 表示算不出来 → 记未检验，不是「值为 0 通过」"""
        report = check_overfitting_gates({
            "dsr": None, "dsr_p_value": None, "pbo": None,
        })

        assert report.all_passed
        assert {gr.gate.metric for gr in report.skipped} >= {"dsr", "pbo"}

    def test_overfit_result_fails(self) -> None:
        report = check_overfitting_gates({"dsr_p_value": 0.6, "pbo": 0.8})

        assert not report.all_passed
        assert len(report.errors) == 2

    def test_does_not_check_in_sample_metrics(self) -> None:
        """样本内指标缺失不影响这份报告"""
        report = check_overfitting_gates({"dsr_p_value": 0.01, "pbo": 0.1})

        assert not any(
            gr.gate.metric in ("sharpe_ratio", "total_return")
            for gr in report.results
        )

    def test_empty_input_passes_with_all_skipped(self) -> None:
        report = check_overfitting_gates({})

        assert report.all_passed
        assert len(report.skipped) == len(OVERFITTING_QUALITY_GATES)


class TestComputeOverfittingMetrics:
    """指标计算"""

    @staticmethod
    def _returns(n: int = 250, mu: float = 0.0008, sd: float = 0.012) -> list[float]:
        rng = random.Random(42)
        return [rng.gauss(mu, sd) for _ in range(n)]

    def test_computes_dsr_from_returns(self) -> None:
        m = compute_overfitting_metrics(
            daily_returns=self._returns(), sharpe_ratio=1.5, n_trials=50,
        )

        assert "dsr" in m
        assert 0.0 <= m["dsr_p_value"] <= 1.0
        assert m["n_trials"] == 50.0

    def test_more_trials_weakens_significance(self) -> None:
        """试的组合越多，同一个样本内 Sharpe 越不可信"""
        rets = self._returns()
        few = compute_overfitting_metrics(rets, sharpe_ratio=1.5, n_trials=3)
        many = compute_overfitting_metrics(rets, sharpe_ratio=1.5, n_trials=500)

        assert many["dsr_p_value"] > few["dsr_p_value"]
        assert many["dsr"] < few["dsr"]

    def test_single_trial_yields_no_dsr(self) -> None:
        """n_trials<2 时 DSR 无意义 —— 不产出而非产出假值"""
        m = compute_overfitting_metrics(self._returns(), 1.5, n_trials=1)

        assert "dsr" not in m
        assert "dsr_p_value" not in m

    def test_short_series_yields_no_dsr(self) -> None:
        m = compute_overfitting_metrics([0.01] * 5, 1.5, n_trials=50)

        assert "dsr" not in m

    def test_pbo_passed_through(self) -> None:
        m = compute_overfitting_metrics([], 1.5, n_trials=0, pbo=0.31)

        assert m["pbo"] == pytest.approx(0.31)

    def test_decay_computed_from_oos_sharpe(self) -> None:
        m = compute_overfitting_metrics([], 1.0, n_trials=0, oos_sharpe=0.4)

        assert m["oos_sharpe_decay"] == pytest.approx(0.6)

    def test_decay_floored_at_zero(self) -> None:
        """样本外反而更好时衰减记 0，不记负数"""
        m = compute_overfitting_metrics([], 1.0, n_trials=0, oos_sharpe=1.5)

        assert m["oos_sharpe_decay"] == 0.0

    def test_non_positive_is_sharpe_yields_no_decay(self) -> None:
        """样本内夏普 ≤0 时衰减比例无意义"""
        m = compute_overfitting_metrics([], 0.0, n_trials=0, oos_sharpe=0.5)

        assert "oos_sharpe_decay" not in m

    def test_empty_inputs_yield_empty_dict(self) -> None:
        assert compute_overfitting_metrics([], 1.5, n_trials=0) == {}

    def test_all_values_finite(self) -> None:
        m = compute_overfitting_metrics(
            self._returns(), sharpe_ratio=1.5, n_trials=50, pbo=0.3,
            oos_sharpe=1.0,
        )

        assert all(math.isfinite(v) for v in m.values())


class TestExtractMetricsWiring:
    """extract_metrics_dict → 门禁指标"""

    def test_absent_by_default(self) -> None:
        """不传 n_trials 时不产出过拟合指标（保持原有行为）"""
        from aipha.backtest.report import extract_metrics_dict

        result = _make_result()
        metrics = extract_metrics_dict(result)

        assert "dsr" not in metrics
        assert "pbo" not in metrics

    def test_populated_with_n_trials(self) -> None:
        from aipha.backtest.report import extract_metrics_dict

        metrics = extract_metrics_dict(_make_result(), n_trials=40)

        assert "dsr_p_value" in metrics
        assert metrics["n_trials"] == 40.0

    def test_pbo_and_oos_passed_through(self) -> None:
        from aipha.backtest.report import extract_metrics_dict

        metrics = extract_metrics_dict(
            _make_result(), pbo=0.28, oos_sharpe=0.5,
        )

        assert metrics["pbo"] == pytest.approx(0.28)
        assert "oos_sharpe_decay" in metrics

    def test_in_sample_metrics_unchanged(self) -> None:
        from aipha.backtest.report import extract_metrics_dict

        result = _make_result()
        without = extract_metrics_dict(result)
        with_dsr = extract_metrics_dict(result, n_trials=40)

        for k, v in without.items():
            assert with_dsr[k] == v


def _make_result():
    """构造一份有 250 个交易日的最小回测结果。"""
    from datetime import date, timedelta

    from aipha.backtest.engine import BacktestResult, DailySnapshot
    from aipha.execution.fees import FeeBreakdown

    rng = random.Random(7)
    nav = 1_000_000.0
    start = date(2025, 1, 6)
    snapshots = []
    for i in range(250):
        ret = rng.gauss(0.0008, 0.012)
        prev = nav
        nav *= (1 + ret)
        snapshots.append(DailySnapshot(
            trade_date=start + timedelta(days=i),
            nav=nav,
            cash=nav * 0.2,
            positions={"600000.SH": 1000},
            position_values={"600000.SH": nav * 0.8},
            daily_return=nav / prev - 1,
            cumulative_return=nav / 1_000_000.0 - 1,
        ))

    return BacktestResult(
        strategy_name="OverfitTest",
        start_date=snapshots[0].trade_date,
        end_date=snapshots[-1].trade_date,
        initial_capital=1_000_000.0,
        final_nav=nav,
        final_cash=nav * 0.2,
        final_positions={"600000.SH": 1000},
        daily_snapshots=snapshots,
        trades=[],
        total_trades=0,
        total_fees=FeeBreakdown(commission=0.0, stamp_tax=0.0, transfer_fee=0.0),
        exclusion_stats={},
    )
