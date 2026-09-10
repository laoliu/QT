"""推荐实施差距度量测试 — 差距分解、买入后表现、噪声判定。"""

from __future__ import annotations

import pytest

from aipha.advisory.models import ActionType, Recommendation, TimeHorizon
from aipha.advisory.shortfall import (
    MIN_SAMPLE_FOR_VERDICT,
    ExecutionRecord,
    analyze_shortfall,
    assess_noise,
    format_shortfall_report,
)


def _rec(**kw) -> ExecutionRecord:
    base = dict(
        ts_code="600000.SH",
        signal_price=10.0,
        arrival_price=10.1,
        fill_price=10.15,
        qty=1000,
        fees=10.15,
        side="buy",
    )
    base.update(kw)
    return ExecutionRecord(**base)


class TestShortfallDecomposition:
    """Perold 差距分解"""

    def test_delay_cost_positive_when_gapped_up(self) -> None:
        """次日高开 → 延迟成本为正（不利）"""
        rec = _rec(signal_price=10.0, arrival_price=10.1)
        assert rec.delay_bps == pytest.approx(100.0)

    def test_delay_cost_negative_when_gapped_down(self) -> None:
        """次日低开 → 延迟成本为负（对买方有利）"""
        rec = _rec(signal_price=10.0, arrival_price=9.9)
        assert rec.delay_bps == pytest.approx(-100.0)

    def test_execution_cost(self) -> None:
        rec = _rec(arrival_price=10.0, fill_price=10.05)
        assert rec.execution_bps == pytest.approx(50.0)

    def test_fee_bps(self) -> None:
        rec = _rec(fill_price=10.0, qty=1000, fees=10.0)
        assert rec.fee_bps == pytest.approx(10.0)

    def test_total_is_slip_plus_fee(self) -> None:
        rec = _rec(signal_price=10.0, fill_price=10.1, qty=1000, fees=10.1)
        # 滑移 100bps + 费用 10bps
        assert rec.total_shortfall_bps == pytest.approx(110.0)

    def test_sell_side_sign_flipped(self) -> None:
        """卖出收得更少才算不利 → 符号归一"""
        rec = _rec(side="sell", signal_price=10.0, arrival_price=9.9)
        assert rec.delay_bps == pytest.approx(100.0)

    def test_missing_arrival_blocks_split_not_total(self) -> None:
        """没有 arrival_price 时无法拆分，但总差距仍可算"""
        rec = _rec(arrival_price=None)
        assert rec.delay_bps is None
        assert rec.execution_bps is None
        assert rec.total_shortfall_bps is not None

    def test_unfilled_has_no_cost_metrics(self) -> None:
        rec = _rec(fill_price=None)
        assert not rec.is_filled
        assert rec.total_shortfall_bps is None
        assert rec.execution_bps is None

    def test_zero_qty_no_fee_bps(self) -> None:
        assert _rec(qty=0).fee_bps is None


class TestPostEntryPerformance:
    """买入后表现"""

    def test_first_forward_loss(self) -> None:
        rec = _rec(fill_price=10.0, forward_prices=[9.9])
        assert rec.first_forward_bps == pytest.approx(-100.0)

    def test_mae_is_worst_excursion(self) -> None:
        """最大浮亏取路径最低点，不是终点"""
        rec = _rec(fill_price=10.0, forward_prices=[9.9, 9.5, 10.5])
        assert rec.mae_bps == pytest.approx(-500.0)

    def test_mae_positive_when_never_underwater(self) -> None:
        rec = _rec(fill_price=10.0, forward_prices=[10.2, 10.5])
        assert rec.mae_bps == pytest.approx(200.0)

    def test_stop_hit_detected(self) -> None:
        rec = _rec(fill_price=10.0, stop_loss=9.5, forward_prices=[9.8, 9.4])
        assert rec.stop_hit is True

    def test_stop_not_hit(self) -> None:
        rec = _rec(fill_price=10.0, stop_loss=9.5, forward_prices=[9.8, 9.6])
        assert rec.stop_hit is False

    def test_stop_hit_false_without_plan(self) -> None:
        assert _rec(stop_loss=None, forward_prices=[9.0]).stop_hit is False

    def test_no_forward_prices(self) -> None:
        rec = _rec(forward_prices=[])
        assert rec.first_forward_bps is None
        assert rec.mae_bps is None


class TestFromRecommendation:
    """与 Recommendation 的衔接"""

    def test_reuses_signal_price_and_stop(self) -> None:
        rec = Recommendation(
            ts_code="600000.SH",
            action=ActionType.STRONG_BUY,
            horizon=TimeHorizon.SHORT,
            signal_price=10.0,
            stop_loss=9.5,
        )
        er = ExecutionRecord.from_recommendation(
            rec, arrival_price=10.1, fill_price=10.15, qty=1000, fees=10.0,
            forward_prices=[10.0],
        )

        assert er.signal_price == 10.0
        assert er.stop_loss == 9.5
        assert er.side == "buy"
        assert er.horizon == "short"

    def test_sell_recommendation_maps_to_sell(self) -> None:
        rec = Recommendation(
            ts_code="600000.SH", action=ActionType.SELL, signal_price=10.0,
        )
        assert ExecutionRecord.from_recommendation(rec).side == "sell"

    def test_missing_signal_price_raises(self) -> None:
        """无 signal_price 的推荐无法度量 —— 明确报错而非静默产出错数据"""
        rec = Recommendation(ts_code="600000.SH", action=ActionType.BUY)
        with pytest.raises(ValueError, match="signal_price"):
            ExecutionRecord.from_recommendation(rec)


class TestAssessNoise:
    """二项检验判定"""

    def test_small_sample_refuses_verdict(self) -> None:
        """n=1 全亏也不给失效结论 —— 正是用户遇到的场景"""
        v = assess_noise(n_losses=1, n_total=1, baseline_loss_rate=0.55)

        assert v.sample_sufficient is False
        assert v.is_significant is False
        assert "样本不足" in v.verdict
        assert "不应修改策略参数" in v.verdict

    def test_reports_required_sample_size(self) -> None:
        """样本不足时给出具体需要多少笔"""
        v = assess_noise(n_losses=3, n_total=3, baseline_loss_rate=0.55)
        assert v.min_n_for_detection is not None
        assert v.min_n_for_detection >= MIN_SAMPLE_FOR_VERDICT

    def test_normal_variation_not_flagged(self) -> None:
        """浮亏率贴合基线 → 判为正常波动"""
        v = assess_noise(n_losses=28, n_total=50, baseline_loss_rate=0.55)

        assert v.sample_sufficient is True
        assert v.is_significant is False
        assert "正常波动" in v.verdict

    def test_significant_deviation_flagged(self) -> None:
        """浮亏率远高于基线且样本足够 → 判为显著异常"""
        v = assess_noise(n_losses=95, n_total=100, baseline_loss_rate=0.55)

        assert v.is_significant is True
        assert "显著异常" in v.verdict
        assert v.p_value is not None and v.p_value < 0.05

    def test_observed_below_baseline_no_min_n(self) -> None:
        v = assess_noise(n_losses=10, n_total=50, baseline_loss_rate=0.55)
        assert v.min_n_for_detection is None
        assert v.is_significant is False

    def test_empty_sample(self) -> None:
        v = assess_noise(n_losses=0, n_total=0, baseline_loss_rate=0.5)
        assert "无法判定" in v.verdict

    @pytest.mark.parametrize("baseline", [0.0, 1.0, -0.5, 1.5])
    def test_baseline_clamped(self, baseline: float) -> None:
        """极端基线被夹紧，不抛异常"""
        v = assess_noise(n_losses=5, n_total=10, baseline_loss_rate=baseline)
        assert 0.0 < v.baseline_loss_rate < 1.0


class TestAnalyzeShortfall:
    """汇总报告"""

    def test_empty_records(self) -> None:
        report = analyze_shortfall([])
        assert report.total == 0
        assert report.fill_rate == 0.0
        assert report.mean_total_bps is None

    def test_fill_rate_counts_unfilled(self) -> None:
        report = analyze_shortfall([_rec(), _rec(fill_price=None)])
        assert report.total == 2
        assert report.filled == 1
        assert report.fill_rate == pytest.approx(0.5)

    def test_aggregates_decomposition(self) -> None:
        recs = [
            _rec(signal_price=10.0, arrival_price=10.1, fill_price=10.1,
                 qty=1000, fees=0.0),
            _rec(signal_price=10.0, arrival_price=10.3, fill_price=10.3,
                 qty=1000, fees=0.0),
        ]
        report = analyze_shortfall(recs)

        assert report.mean_delay_bps == pytest.approx(200.0)
        assert report.mean_execution_bps == pytest.approx(0.0)
        assert report.mean_total_bps == pytest.approx(200.0)
        assert report.worst_total_bps == pytest.approx(300.0)

    def test_immediate_loss_rate(self) -> None:
        recs = [
            _rec(fill_price=10.0, forward_prices=[9.9]),   # 亏
            _rec(fill_price=10.0, forward_prices=[10.1]),  # 盈
            _rec(fill_price=10.0, forward_prices=[9.8]),   # 亏
        ]
        report = analyze_shortfall(recs)
        assert report.immediate_loss_rate == pytest.approx(2 / 3)

    def test_stop_hit_rate_only_counts_records_with_plan(self) -> None:
        recs = [
            _rec(fill_price=10.0, stop_loss=9.5, forward_prices=[9.4]),
            _rec(fill_price=10.0, stop_loss=9.5, forward_prices=[9.9]),
            _rec(fill_price=10.0, stop_loss=None, forward_prices=[9.0]),
        ]
        report = analyze_shortfall(recs)
        assert report.stop_hit_rate == pytest.approx(0.5)

    def test_verdict_skipped_without_baseline(self) -> None:
        """不提供胜率就不猜基线"""
        report = analyze_shortfall([_rec(forward_prices=[9.9])])
        assert report.noise_verdict is None

    def test_verdict_present_with_baseline(self) -> None:
        report = analyze_shortfall(
            [_rec(forward_prices=[9.9])], baseline_win_rate=0.45,
        )
        assert report.noise_verdict is not None
        assert report.noise_verdict.baseline_loss_rate == pytest.approx(0.55)

    def test_per_record_detail(self) -> None:
        report = analyze_shortfall([_rec()])
        assert len(report.per_record) == 1
        assert report.per_record[0]["ts_code"] == "600000.SH"

    def test_to_dict_json_safe(self) -> None:
        import json

        report = analyze_shortfall(
            [_rec(forward_prices=[9.9])], baseline_win_rate=0.45,
        )
        json.dumps(report.to_dict())  # 不抛异常即通过


class TestFormatReport:
    """报告格式化"""

    def test_renders_without_error(self) -> None:
        report = analyze_shortfall(
            [_rec(forward_prices=[9.9, 9.5])], baseline_win_rate=0.45,
        )
        text = format_shortfall_report(report)

        assert "推荐实施差距报告" in text
        assert "延迟成本" in text
        assert "样本不足" in text

    def test_handles_all_none(self) -> None:
        text = format_shortfall_report(analyze_shortfall([]))
        assert "n/a" in text
