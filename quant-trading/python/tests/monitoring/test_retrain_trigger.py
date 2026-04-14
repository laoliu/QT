"""RetrainTrigger 单元测试。"""

from __future__ import annotations

import pytest
from dataclasses import dataclass
from typing import Optional

from aipha.monitoring.retrain_trigger import (
    RetrainTrigger,
    RetrainDecision,
    RetrainReason,
)


# ── 模拟 DriftReport / HalflifeSnapshot 数据结构 ──


@dataclass
class FakeDriftReport:
    has_significant_drift: bool = False
    drifted_features: list = None
    warning_features: list = None
    overall_psi: float = 0.05
    n_features_checked: int = 10

    def __post_init__(self):
        if self.drifted_features is None:
            self.drifted_features = []
        if self.warning_features is None:
            self.warning_features = []


@dataclass
class FakeDecaySnapshot:
    status: str = "NORMAL"
    recent_icir: Optional[float] = 0.5
    full_icir: Optional[float] = 0.8


# ── 基础测试 ──


class TestRetrainTriggerInit:
    def test_default_params(self):
        t = RetrainTrigger()
        assert t.psi_threshold == 0.20
        assert t.sr_negative_months == 6

    def test_custom_params(self):
        t = RetrainTrigger(psi_threshold=0.15, sr_negative_months=3)
        assert t.psi_threshold == 0.15
        assert t.sr_negative_months == 3


class TestPSICheck:
    def test_no_drift(self):
        t = RetrainTrigger()
        report = FakeDriftReport(overall_psi=0.05)
        d = t.evaluate(drift_report=report)
        assert not d.should_retrain
        assert d.urgency == "none"

    def test_warning_psi_no_retrain(self):
        t = RetrainTrigger()
        report = FakeDriftReport(overall_psi=0.15)
        d = t.evaluate(drift_report=report)
        assert not d.should_retrain

    def test_significant_drift_triggers(self):
        t = RetrainTrigger()
        report = FakeDriftReport(
            has_significant_drift=True,
            overall_psi=0.25,
            drifted_features=["f1", "f2"],
        )
        d = t.evaluate(drift_report=report)
        assert d.should_retrain
        assert RetrainReason.PSI_DRIFT in d.reasons
        assert d.urgency == "high"


class TestICIRCheck:
    def test_normal_icir(self):
        t = RetrainTrigger()
        snap = FakeDecaySnapshot(status="NORMAL", recent_icir=0.5, full_icir=0.8)
        d = t.evaluate(decay_snapshot=snap)
        assert not d.should_retrain

    def test_degraded_status(self):
        t = RetrainTrigger()
        snap = FakeDecaySnapshot(status="DEGRADED", recent_icir=0.1, full_icir=0.8)
        d = t.evaluate(decay_snapshot=snap)
        assert d.should_retrain
        assert RetrainReason.ICIR_DEGRADED in d.reasons
        assert d.urgency == "medium"

    def test_alpha_decay_ratio(self):
        t = RetrainTrigger(icir_degraded_ratio=0.40)
        snap = FakeDecaySnapshot(status="WARNING", recent_icir=0.2, full_icir=0.8)
        d = t.evaluate(decay_snapshot=snap)
        assert d.should_retrain
        assert RetrainReason.ICIR_DEGRADED in d.reasons

    def test_none_icir_no_crash(self):
        t = RetrainTrigger()
        snap = FakeDecaySnapshot(status="NORMAL", recent_icir=None, full_icir=None)
        d = t.evaluate(decay_snapshot=snap)
        assert not d.should_retrain


class TestSRCheck:
    def test_positive_sr(self):
        t = RetrainTrigger()
        d = t.evaluate(rolling_sr_history=[0.5, 0.3, 0.8, 0.2])
        assert not d.should_retrain

    def test_short_negative_streak(self):
        t = RetrainTrigger(sr_negative_months=6)
        d = t.evaluate(rolling_sr_history=[0.5, -0.1, -0.2, -0.3])
        assert not d.should_retrain

    def test_long_negative_streak(self):
        t = RetrainTrigger(sr_negative_months=6)
        d = t.evaluate(
            rolling_sr_history=[0.5, -0.1, -0.2, -0.3, -0.1, -0.4, -0.2],
        )
        assert d.should_retrain
        assert RetrainReason.SR_NEGATIVE in d.reasons

    def test_empty_history(self):
        t = RetrainTrigger()
        d = t.evaluate(rolling_sr_history=[])
        assert not d.should_retrain


class TestMultipleReasons:
    def test_psi_plus_icir_high_urgency(self):
        t = RetrainTrigger()
        drift = FakeDriftReport(has_significant_drift=True, overall_psi=0.30)
        snap = FakeDecaySnapshot(status="DEGRADED")
        d = t.evaluate(drift_report=drift, decay_snapshot=snap)
        assert d.should_retrain
        assert len(d.reasons) == 2
        assert d.urgency == "high"

    def test_all_three_triggers(self):
        t = RetrainTrigger(sr_negative_months=3)
        drift = FakeDriftReport(has_significant_drift=True, overall_psi=0.25)
        snap = FakeDecaySnapshot(status="DEGRADED")
        sr = [-0.1, -0.2, -0.3, -0.4]
        d = t.evaluate(drift_report=drift, decay_snapshot=snap, rolling_sr_history=sr)
        assert d.should_retrain
        assert len(d.reasons) == 3
        assert d.urgency == "high"


class TestDecisionSerialization:
    def test_to_dict(self):
        d = RetrainDecision(
            should_retrain=True,
            reasons=[RetrainReason.PSI_DRIFT],
            urgency="high",
            details={"psi": {"overall_psi": 0.25}},
        )
        result = d.to_dict()
        assert result["should_retrain"] is True
        assert result["reasons"] == ["psi_drift"]
        assert result["urgency"] == "high"
        assert "timestamp" in result

    def test_no_retrain_dict(self):
        d = RetrainDecision(should_retrain=False)
        result = d.to_dict()
        assert result["should_retrain"] is False
        assert result["reasons"] == []


# ── R56 A9: 市场急变压力测试 ──


class TestMarketStressScenarios:
    """模拟真实市场急变情景，验证触发器响应。"""

    def test_covid_crash_2020(self):
        """2020 疫情冲击: PSI 急升 + SR 急降。"""
        t = RetrainTrigger()
        drift = FakeDriftReport(
            has_significant_drift=True,
            overall_psi=0.45,
            drifted_features=["vol_20d", "turnover", "momentum_20d",
                              "amihud", "beta", "ivol"],
        )
        # SR 从正转负 (6+月连续为负才触发)
        sr = [1.2, 0.8, 0.5, -0.3, -0.8, -1.5, -2.0, -1.0, -0.5]
        d = t.evaluate(drift_report=drift, rolling_sr_history=sr)
        assert d.should_retrain
        assert RetrainReason.PSI_DRIFT in d.reasons
        assert RetrainReason.SR_NEGATIVE in d.reasons
        assert d.urgency == "high"
        assert d.details["psi"]["drifted_count"] == 6

    def test_cb_new_regulation_2022(self):
        """2022-08 CB 新规(±20%涨跌停): 市场结构突变。"""
        t = RetrainTrigger()
        drift = FakeDriftReport(
            has_significant_drift=True,
            overall_psi=0.35,
            drifted_features=["daily_return_std", "high_low_range",
                              "turnover_rate"],
        )
        snap = FakeDecaySnapshot(
            status="DEGRADED",
            recent_icir=0.08,
            full_icir=0.25,
        )
        d = t.evaluate(drift_report=drift, decay_snapshot=snap)
        assert d.should_retrain
        assert len(d.reasons) >= 2
        assert d.urgency == "high"

    def test_2015_stock_crash(self):
        """2015 A股股灾: 连续暴跌导致 SR 持续为负。"""
        t = RetrainTrigger(sr_negative_months=6)
        # 2015-06 ~ 2016-02: 连续 9 月 SR < 0
        sr = [1.5, 1.0, 0.8, -0.5, -1.2, -2.0, -1.8, -1.5, -0.8,
              -0.3, -0.1, 0.2, 0.5]
        d = t.evaluate(rolling_sr_history=sr)
        # 虽然最后恢复了，但未到尾部连续6月为负
        assert not d.should_retrain

    def test_2015_crash_during_crisis(self):
        """2015 股灾期间（截至2015-12月，9月连续负SR）。"""
        t = RetrainTrigger(sr_negative_months=6)
        sr = [1.5, 1.0, 0.8, -0.5, -1.2, -2.0, -1.8, -1.5, -0.8, -0.3]
        d = t.evaluate(rolling_sr_history=sr)
        assert d.should_retrain
        assert RetrainReason.SR_NEGATIVE in d.reasons

    def test_gradual_alpha_decay(self):
        """缓慢 alpha 衰减（因子拥挤）: ICIR 逐渐退化。"""
        t = RetrainTrigger(icir_degraded_ratio=0.30)
        snap = FakeDecaySnapshot(
            status="WARNING",
            recent_icir=0.05,
            full_icir=0.20,
        )
        d = t.evaluate(decay_snapshot=snap)
        assert d.should_retrain
        assert RetrainReason.ICIR_DEGRADED in d.reasons
        assert d.urgency == "medium"
        ratio = d.details["icir"]["alpha_decay_ratio"]
        assert ratio == 0.25  # 0.05/0.20

    def test_flash_crash_no_trigger(self):
        """闪崩（1-2日急跌后恢复）: PSI低，SR仍正, 不应触发。"""
        t = RetrainTrigger()
        drift = FakeDriftReport(overall_psi=0.12)
        snap = FakeDecaySnapshot(status="NORMAL", recent_icir=0.4, full_icir=0.5)
        sr = [0.8, 0.5, 0.3, -0.1, 0.6, 0.4]
        d = t.evaluate(drift_report=drift, decay_snapshot=snap,
                        rolling_sr_history=sr)
        assert not d.should_retrain

    def test_triple_crisis_full_alarm(self):
        """三重危机同时触发: PSI+ICIR+SR 全部亮红灯。"""
        t = RetrainTrigger(sr_negative_months=3)
        drift = FakeDriftReport(
            has_significant_drift=True,
            overall_psi=0.50,
            drifted_features=["f1", "f2", "f3", "f4", "f5"],
        )
        snap = FakeDecaySnapshot(
            status="DEGRADED", recent_icir=0.02, full_icir=0.30,
        )
        sr = [-0.5, -0.8, -1.2, -0.9]
        d = t.evaluate(drift_report=drift, decay_snapshot=snap,
                        rolling_sr_history=sr)
        assert d.should_retrain
        assert len(d.reasons) == 3
        assert d.urgency == "high"
