"""追高上限校准测试。

核心约束（这些测试就是为了守住它们）:

1. 样本不足时不给新数字 —— 拿 n<30 去改 cap 与凭直觉改没区别。
2. 追高幅度与结果无统计关系时不给新数字 —— 收紧 cap 只会白掉成交。
3. 只有方向成立后才定 cap，且取「结果转负」的分箱边界，
   不在候选网格上取最优（那是过拟合）。
4. 分状态乘数需要基准状态先校准成功 —— 乘数是相对量。
"""

from __future__ import annotations

import math

import pytest

from aipha.advisory.chase_calibration import (
    CAP_LOWER_BOUND,
    CAP_UPPER_BOUND,
    DEFAULT_BIN_EDGES,
    MIN_SAMPLE_FOR_CALIBRATION,
    MIN_SAMPLE_PER_BIN,
    ChaseCalibration,
    apply_calibration,
    calibrate_chase_cap,
    calibrate_regime_multipliers,
    format_chase_calibration,
    format_regime_calibration,
    forward_total_bps,
    observed_chase_pct,
    spearman_one_tailed,
    welch_one_tailed,
)
from aipha.advisory.models import TimeHorizon
from aipha.advisory.recommendation_engine import (
    RecommendationConfig,
    RegimeGateConfig,
)
from aipha.advisory.shortfall import ExecutionRecord


def _rec(
    chase_pct: float,
    forward_pct: float,
    *,
    signal_price: float = 10.0,
    side: str = "buy",
    stop_loss: float | None = None,
    with_arrival: bool = True,
    with_forward: bool = True,
    ts_code: str = "600000.SH",
) -> ExecutionRecord:
    """构造一条「追高 chase_pct、买入后涨 forward_pct」的执行记录。"""
    arrival = signal_price * (1 + chase_pct)
    return ExecutionRecord(
        ts_code=ts_code,
        signal_price=signal_price,
        arrival_price=arrival if with_arrival else None,
        fill_price=arrival,
        qty=100,
        fees=0.0,
        side=side,
        stop_loss=stop_loss,
        forward_prices=(
            [arrival * (1 + forward_pct), arrival * (1 + forward_pct * 1.5)]
            if with_forward else []
        ),
    )


def _dataset(
    low_chase: float,
    low_forward: float,
    high_chase: float,
    high_forward: float,
    n_each: int = 15,
) -> list[ExecutionRecord]:
    """两簇样本: 低追高一簇 + 高追高一簇。加确定性微扰避免全并列。"""
    out: list[ExecutionRecord] = []
    for i in range(n_each):
        j = i / (n_each * 1000.0)
        out.append(_rec(low_chase + j, low_forward + j))
        out.append(_rec(high_chase + j, high_forward - j))
    return out


@pytest.fixture
def predictive_records() -> list[ExecutionRecord]:
    """追高 <1% 的赚钱，追高 3~5% 的亏钱 —— cap 应落在 3%。"""
    return _dataset(0.005, 0.01, 0.035, -0.02)


@pytest.fixture
def flat_records() -> list[ExecutionRecord]:
    """追高幅度铺满 0~5%，但结果与追高无关。"""
    out: list[ExecutionRecord] = []
    for i in range(40):
        chase = 0.0005 + i * 0.0012          # 0.05% ~ 4.7%
        forward = 0.01 if i % 2 == 0 else -0.01
        out.append(_rec(chase, forward))
    return out


class TestDerivedMetrics:
    """记录侧派生量"""

    def test_observed_chase_pct(self) -> None:
        rec = _rec(0.025, 0.0)

        assert observed_chase_pct(rec) == pytest.approx(0.025, abs=1e-9)

    def test_observed_chase_pct_negative_when_cheaper(self) -> None:
        """信号价之下买到 —— 追高为负，不该被 cap 挡"""
        assert observed_chase_pct(_rec(-0.01, 0.0)) == pytest.approx(-0.01, abs=1e-9)

    def test_observed_chase_pct_none_without_arrival(self) -> None:
        assert observed_chase_pct(_rec(0.02, 0.0, with_arrival=False)) is None

    def test_forward_total_uses_last_price(self) -> None:
        """整段持有取最后一个前瞻价（这里是 forward_pct 的 1.5 倍）"""
        rec = _rec(0.0, 0.02)

        assert forward_total_bps(rec) == pytest.approx(300.0, rel=1e-6)

    def test_forward_total_none_without_forward(self) -> None:
        assert forward_total_bps(_rec(0.0, 0.0, with_forward=False)) is None


class TestSampleFilters:
    """可用样本筛选"""

    def test_sells_excluded(self) -> None:
        """max_chase_pct 卡的是买入价上限，卖出记录无关"""
        recs = [_rec(0.02, -0.01, side="sell") for _ in range(40)]

        cal = calibrate_chase_cap(recs)

        assert cal.n_records == 40
        assert cal.n_usable == 0
        assert "无可用样本" in cal.verdict

    def test_missing_arrival_price_excluded(self) -> None:
        """没有到达价就算不出追高幅度"""
        recs = [_rec(0.02, -0.01, with_arrival=False) for _ in range(40)]

        cal = calibrate_chase_cap(recs)

        assert cal.n_usable == 0

    def test_missing_forward_excluded(self) -> None:
        recs = [_rec(0.02, 0.0, with_forward=False) for _ in range(40)]

        assert calibrate_chase_cap(recs).n_usable == 0

    def test_mixed_records_partially_usable(self, predictive_records) -> None:
        recs = [*predictive_records, _rec(0.02, 0.0, with_arrival=False)]

        cal = calibrate_chase_cap(recs)

        assert cal.n_records == len(predictive_records) + 1
        assert cal.n_usable == len(predictive_records)

    def test_empty_input(self) -> None:
        cal = calibrate_chase_cap([])

        assert cal.recommended_max_chase_pct is None
        assert cal.n_usable == 0
        assert "arrival_price" in cal.verdict


class TestSampleSufficiency:
    """样本量纪律 —— 与 assess_noise 同一条"""

    def test_below_floor_refuses_to_calibrate(self) -> None:
        recs = _dataset(0.005, 0.01, 0.035, -0.02, n_each=5)  # 10 笔
        assert len(recs) < MIN_SAMPLE_FOR_CALIBRATION

        cal = calibrate_chase_cap(recs, current_max_chase_pct=0.03)

        assert cal.sample_sufficient is False
        assert cal.recommended_max_chase_pct is None
        assert cal.effective_cap == pytest.approx(0.03)

    def test_verdict_names_required_sample(self) -> None:
        cal = calibrate_chase_cap(_dataset(0.005, 0.01, 0.035, -0.02, n_each=5))

        assert "样本不足" in cal.verdict
        assert str(MIN_SAMPLE_FOR_CALIBRATION) in cal.verdict

    def test_distribution_still_reported_below_floor(self) -> None:
        """样本不够给结论，但分布该照样报 —— 那是无需检验的事实"""
        cal = calibrate_chase_cap(_dataset(0.005, 0.01, 0.035, -0.02, n_each=5))

        assert cal.chase_p50 is not None
        assert cal.bins, "分箱表应仍然产出"

    def test_no_hypothesis_test_below_floor(self) -> None:
        """样本不够时不做检验，避免留下一个看似有效的 p 值"""
        cal = calibrate_chase_cap(_dataset(0.005, 0.01, 0.035, -0.02, n_each=5))

        assert cal.p_value is None
        assert cal.predictive is False


class TestNoRelationship:
    """追高与结果无关时必须拒绝收紧"""

    def test_flat_data_gives_no_recommendation(self, flat_records) -> None:
        cal = calibrate_chase_cap(flat_records, current_max_chase_pct=0.03)

        assert cal.sample_sufficient is True
        assert cal.predictive is False
        assert cal.recommended_max_chase_pct is None

    def test_verdict_points_elsewhere(self, flat_records) -> None:
        """结论要说清「问题不在这儿」，而不是含糊地不给数"""
        cal = calibrate_chase_cap(flat_records)

        assert "无证据支持收紧" in cal.verdict
        assert "减少成交" in cal.verdict

    def test_reverse_relationship_not_significant(self) -> None:
        """追高反而更赚 —— 单尾检验不该判为「该收紧」"""
        recs = _dataset(0.005, -0.02, 0.035, 0.01)

        cal = calibrate_chase_cap(recs)

        assert cal.spearman_rho is not None and cal.spearman_rho > 0
        assert cal.predictive is False
        assert cal.recommended_max_chase_pct is None


class TestCapSelection:
    """cap 取值规则"""

    def test_picks_bin_lower_edge_where_outcome_turns_negative(
        self, predictive_records,
    ) -> None:
        cal = calibrate_chase_cap(predictive_records, current_max_chase_pct=0.03)

        assert cal.predictive is True
        assert cal.recommended_max_chase_pct == pytest.approx(0.03)

    def test_earlier_turn_gives_tighter_cap(self) -> None:
        """亏损从 1~2% 区间就开始 —— cap 应落在 1%，而非 3%"""
        recs = _dataset(0.005, 0.01, 0.015, -0.02)

        cal = calibrate_chase_cap(recs)

        assert cal.recommended_max_chase_pct == pytest.approx(0.01)

    def test_no_negative_bin_keeps_current(self) -> None:
        """方向成立但每档都还赚钱 —— 没有该收紧的位置"""
        recs = _dataset(0.005, 0.03, 0.035, 0.005)

        cal = calibrate_chase_cap(recs, current_max_chase_pct=0.03)

        assert cal.predictive is True
        assert cal.recommended_max_chase_pct is None
        assert "找不到该收紧的位置" in cal.verdict

    def test_thin_bin_cannot_set_cap(self) -> None:
        """转负的那档只有几笔 —— 不足以定 cap"""
        recs = [_rec(0.005 + i / 30000, 0.01) for i in range(35)]
        recs += [_rec(0.035 + i / 30000, -0.05) for i in range(MIN_SAMPLE_PER_BIN - 1)]

        cal = calibrate_chase_cap(recs)

        assert cal.recommended_max_chase_pct is None

    def test_never_caps_at_or_below_zero(self) -> None:
        """买得比信号价便宜的那批不该被挡掉"""
        recs = _dataset(-0.02, -0.01, 0.035, -0.02)

        cal = calibrate_chase_cap(recs)

        if cal.recommended_max_chase_pct is not None:
            assert cal.recommended_max_chase_pct > 0

    def test_clamped_to_lower_bound(self) -> None:
        """原始切点过窄时收敛到下限，否则正常盘中波动会被全挡"""
        recs = _dataset(0.0002, 0.01, 0.002, -0.02)

        cal = calibrate_chase_cap(recs, bin_edges=(0.0, 0.001))

        assert cal.recommended_max_chase_pct == pytest.approx(CAP_LOWER_BOUND)
        assert "已收敛到合理区间" in cal.verdict

    def test_cap_within_bounds(self, predictive_records) -> None:
        cal = calibrate_chase_cap(predictive_records)

        assert CAP_LOWER_BOUND <= cal.recommended_max_chase_pct <= CAP_UPPER_BOUND


class TestBins:
    """分箱表"""

    def test_bin_count_matches_edges(self, predictive_records) -> None:
        cal = calibrate_chase_cap(predictive_records, bin_edges=DEFAULT_BIN_EDGES)

        assert len(cal.bins) == len(DEFAULT_BIN_EDGES) + 1

    def test_bins_partition_sample(self, predictive_records) -> None:
        cal = calibrate_chase_cap(predictive_records)

        assert sum(b.n for b in cal.bins) == cal.n_usable

    def test_loss_rate_and_outcome_signs(self, predictive_records) -> None:
        cal = calibrate_chase_cap(predictive_records)
        low = next(b for b in cal.bins if b.lower_pct == 0.0)
        high = next(b for b in cal.bins if b.lower_pct == 0.03)

        assert low.mean_outcome_bps > 0 and low.loss_rate == 0.0
        assert high.mean_outcome_bps < 0 and high.loss_rate == 1.0

    def test_mae_reported_as_context(self) -> None:
        """MAE 只作描述信息 —— 恒负，不能用来判「转负」"""
        cal = calibrate_chase_cap(_dataset(0.005, 0.01, 0.035, -0.02))
        high = next(b for b in cal.bins if b.lower_pct == 0.03)

        assert high.mean_mae_bps is not None and high.mean_mae_bps < 0

    def test_stop_hit_rate_when_stops_present(self) -> None:
        recs = [
            _rec(0.035 + i / 30000, -0.05 + i / 20000, stop_loss=9.9)
            for i in range(15)
        ] + [
            _rec(0.005 + i / 30000, 0.01 + i / 20000, stop_loss=9.0)
            for i in range(15)
        ]

        cal = calibrate_chase_cap(recs)
        high = next(b for b in cal.bins if b.lower_pct == 0.03)

        assert high.stop_hit_rate == pytest.approx(1.0)

    def test_bin_label_readable(self, predictive_records) -> None:
        cal = calibrate_chase_cap(predictive_records)

        assert cal.bins[0].label.startswith("(-∞")
        assert cal.bins[-1].label.endswith("+∞]")


class TestSplits:
    """候选切点与多重检验校正"""

    def test_current_value_always_evaluated(self, predictive_records) -> None:
        """现状是否有依据，本身就是要回答的问题"""
        cal = calibrate_chase_cap(predictive_records, current_max_chase_pct=0.025)

        assert any(math.isclose(s.cap_pct, 0.025) for s in cal.splits)

    def test_retention_is_cost_of_tightening(self, predictive_records) -> None:
        cal = calibrate_chase_cap(predictive_records)
        split = cal.recommended_split

        assert split is not None
        assert split.retention == pytest.approx(0.5, abs=0.05)

    def test_edge_positive_when_rejected_batch_worse(self, predictive_records) -> None:
        cal = calibrate_chase_cap(predictive_records)

        assert cal.recommended_split.edge_bps > 0

    def test_bonferroni_tightens_alpha(self, predictive_records) -> None:
        cal = calibrate_chase_cap(predictive_records, alpha=0.05)

        assert cal.n_candidates >= 1
        assert cal.alpha_adjusted == pytest.approx(0.05 / cal.n_candidates)

    def test_untestable_split_has_no_p_value(self, predictive_records) -> None:
        """一侧笔数不足就不给 p 值，而不是给个不可靠的"""
        cal = calibrate_chase_cap(predictive_records, bin_edges=(0.0, 0.5))
        thin = next(s for s in cal.splits if math.isclose(s.cap_pct, 0.5))

        assert thin.testable is False
        assert thin.p_value is None

    def test_untestable_splits_excluded_from_correction(self) -> None:
        """没检验的切点不该算进 Bonferroni 分母"""
        cal = calibrate_chase_cap(
            _dataset(0.005, 0.01, 0.035, -0.02), bin_edges=(0.0, 0.03, 0.5),
        )

        assert cal.n_candidates == sum(1 for s in cal.splits if s.testable)


class TestStatHelpers:
    """统计工具（含 scipy 缺失时的兜底路径）"""

    def test_spearman_detects_negative_relation(self) -> None:
        xs = [float(i) for i in range(30)]
        ys = [-float(i) for i in range(30)]

        rho, p = spearman_one_tailed(xs, ys)

        assert rho == pytest.approx(-1.0)
        assert p < 0.01

    def test_spearman_one_tailed_ignores_positive_relation(self) -> None:
        xs = [float(i) for i in range(30)]

        _, p = spearman_one_tailed(xs, xs)

        assert p > 0.5

    def test_spearman_too_few_points(self) -> None:
        assert spearman_one_tailed([1.0], [2.0]) == (None, None)

    def test_welch_detects_better_treatment(self) -> None:
        p = welch_one_tailed([5.0, 6.0, 5.5, 6.2, 5.8], [1.0, 0.5, 1.2, 0.8, 1.1])

        assert p is not None and p < 0.01

    def test_welch_needs_two_per_side(self) -> None:
        assert welch_one_tailed([1.0], [2.0, 3.0]) is None

    def test_welch_zero_variance_returns_none(self) -> None:
        """两侧都是常数 —— 检验无定义，不给假 p 值"""
        assert welch_one_tailed([1.0] * 5, [1.0] * 5) is None


class TestRegimeMultipliers:
    """分状态乘数"""

    def test_requires_base_regime_calibrated(self) -> None:
        """BEAR 有数据但 BULL 没有 —— 乘数是相对量，给不出来"""
        result = calibrate_regime_multipliers({
            "BEAR": _dataset(0.005, 0.01, 0.015, -0.02),
        })

        assert result.any_calibrated is False
        assert "BEAR" in result.notes and "基准" in result.notes["BEAR"]

    def test_multiplier_is_ratio_of_caps(self) -> None:
        result = calibrate_regime_multipliers({
            "BULL": _dataset(0.005, 0.01, 0.035, -0.02),   # cap 3%
            "BEAR": _dataset(0.005, 0.01, 0.015, -0.02),   # cap 1%
        })

        assert result.base_cap_pct == pytest.approx(0.03)
        assert result.recommended_multipliers["BULL"] == 1.0
        assert result.recommended_multipliers["BEAR"] == pytest.approx(1 / 3, abs=0.01)

    def test_uncalibrated_regime_keeps_default(self) -> None:
        result = calibrate_regime_multipliers(
            {
                "BULL": _dataset(0.005, 0.01, 0.035, -0.02),
                "BEAR": _dataset(0.005, 0.01, 0.015, -0.02, n_each=3),  # 太少
            },
            current_multipliers={"BULL": 1.0, "NEUTRAL": 1.0, "BEAR": 0.5},
        )

        assert "BEAR" not in result.recommended_multipliers
        assert "样本不足" in result.notes["BEAR"]

    def test_missing_regime_noted(self) -> None:
        result = calibrate_regime_multipliers(
            {"BULL": _dataset(0.005, 0.01, 0.035, -0.02)},
            current_multipliers={"BULL": 1.0, "NEUTRAL": 1.0, "BEAR": 0.5},
        )

        assert "无 BEAR 状态样本" in result.notes["BEAR"]
        assert "无 NEUTRAL 状态样本" in result.notes["NEUTRAL"]

    def test_default_bear_guess_reported_as_unverified(self) -> None:
        """全无样本时要明说 BEAR=0.5 仍是先验值"""
        result = calibrate_regime_multipliers({})

        assert result.any_calibrated is False
        assert "未经验证的先验值" in format_regime_calibration(result)


class TestApplyCalibration:
    """写回配置"""

    def test_applies_recommended_cap(self, predictive_records) -> None:
        cfg = RecommendationConfig(max_chase_pct=0.03)
        cal = calibrate_chase_cap(_dataset(0.005, 0.01, 0.015, -0.02))

        new_cfg = apply_calibration(cfg, cal)

        assert new_cfg.max_chase_pct == pytest.approx(0.01)

    def test_does_not_mutate_input(self) -> None:
        cfg = RecommendationConfig(max_chase_pct=0.03)
        cal = calibrate_chase_cap(_dataset(0.005, 0.01, 0.015, -0.02))

        apply_calibration(cfg, cal)

        assert cfg.max_chase_pct == pytest.approx(0.03)

    def test_no_recommendation_leaves_config_untouched(self, flat_records) -> None:
        cfg = RecommendationConfig(max_chase_pct=0.03)

        new_cfg = apply_calibration(cfg, calibrate_chase_cap(flat_records))

        assert new_cfg.max_chase_pct == pytest.approx(0.03)

    def test_merges_regime_multipliers(self) -> None:
        cfg = RecommendationConfig(
            regime_gate=RegimeGateConfig(
                chase_pct_multiplier={"BULL": 1.0, "NEUTRAL": 1.0, "BEAR": 0.5},
            ),
        )
        regime = calibrate_regime_multipliers({
            "BULL": _dataset(0.005, 0.01, 0.035, -0.02),
            "BEAR": _dataset(0.005, 0.01, 0.015, -0.02),
        })

        new_cfg = apply_calibration(
            cfg, calibrate_chase_cap(_dataset(0.005, 0.01, 0.035, -0.02)), regime,
        )

        mult = new_cfg.regime_gate.chase_pct_multiplier
        assert mult["BEAR"] == pytest.approx(1 / 3, abs=0.01)
        # 未校准的状态保持原值，不被抹掉
        assert mult["NEUTRAL"] == pytest.approx(1.0)

    def test_uncalibrated_regimes_keep_prior_values(self) -> None:
        cfg = RecommendationConfig()
        prior = dict(cfg.regime_gate.chase_pct_multiplier)
        regime = calibrate_regime_multipliers({})

        new_cfg = apply_calibration(cfg, calibrate_chase_cap([]), regime)

        assert new_cfg.regime_gate.chase_pct_multiplier == prior

    def test_calibrated_config_drives_engine(self) -> None:
        """校准后的 cap 要真的改变推荐的 max_entry_price"""
        from aipha.advisory.models import ScreenerResult
        from aipha.advisory.recommendation_engine import RecommendationEngine

        cal = calibrate_chase_cap(_dataset(0.005, 0.01, 0.015, -0.02))
        cfg = apply_calibration(
            RecommendationConfig(horizons=[TimeHorizon.SHORT]), cal,
        )
        screener = {
            "multifactor": [ScreenerResult(ts_code="600000.SH", score=95.0)],
            "technical": [ScreenerResult(ts_code="600000.SH", score=90.0)],
        }

        rec = RecommendationEngine(cfg).generate(
            screener, current_prices={"600000.SH": 10.0},
        )[TimeHorizon.SHORT][0]

        assert rec.max_entry_price == pytest.approx(10.0 * 1.01, abs=1e-6)


class TestFormatting:
    """报告文本"""

    def test_report_contains_key_sections(self, predictive_records) -> None:
        text = format_chase_calibration(calibrate_chase_cap(predictive_records))

        for section in ("追高上限校准", "实际追高幅度分布", "分箱表现",
                        "方向检验", "候选切点", "结论"):
            assert section in text

    def test_report_marks_recommended_split(self, predictive_records) -> None:
        text = format_chase_calibration(calibrate_chase_cap(predictive_records))

        assert " *" in text, "建议切点应被标出"

    def test_report_handles_empty(self) -> None:
        text = format_chase_calibration(calibrate_chase_cap([]))

        assert "不改（数据不支持）" in text

    def test_report_omits_test_when_sample_short(self) -> None:
        cal = calibrate_chase_cap(_dataset(0.005, 0.01, 0.035, -0.02, n_each=5))

        assert "方向检验" not in format_chase_calibration(cal)

    def test_regime_report_lists_all_regimes(self) -> None:
        text = format_regime_calibration(calibrate_regime_multipliers(
            {"BULL": _dataset(0.005, 0.01, 0.035, -0.02)},
        ))

        for regime in ("BULL", "NEUTRAL", "BEAR"):
            assert regime in text


class TestSerialization:
    """to_dict 可序列化"""

    def test_calibration_to_dict_json_safe(self, predictive_records) -> None:
        import json

        json.dumps(calibrate_chase_cap(predictive_records).to_dict())

    def test_infinite_bin_edges_serialized_as_none(self, predictive_records) -> None:
        """±inf 不是合法 JSON —— 必须落成 null"""
        d = calibrate_chase_cap(predictive_records).to_dict()

        assert d["bins"][0]["lower_pct"] is None
        assert d["bins"][-1]["upper_pct"] is None

    def test_regime_to_dict_json_safe(self) -> None:
        import json

        json.dumps(calibrate_regime_multipliers({
            "BULL": _dataset(0.005, 0.01, 0.035, -0.02),
            "BEAR": _dataset(0.005, 0.01, 0.015, -0.02),
        }).to_dict())


class TestGuards:
    """输入校验"""

    def test_rejects_mae_as_outcome_metric(self) -> None:
        """MAE 恒负，「结果转负」判据对它退化"""
        with pytest.raises(ValueError, match="outcome_metric"):
            calibrate_chase_cap([], outcome_metric="mae_bps")

    def test_forward_total_metric_supported(self, predictive_records) -> None:
        cal = calibrate_chase_cap(
            predictive_records, outcome_metric="forward_total_bps",
        )

        assert cal.outcome_metric == "forward_total_bps"
        assert cal.n_usable == len(predictive_records)

    def test_effective_cap_falls_back_to_current(self) -> None:
        cal = ChaseCalibration(current_max_chase_pct=0.04)

        assert cal.effective_cap == pytest.approx(0.04)
