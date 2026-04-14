"""test_alpha_authenticity.py — Alpha 真实性验证模块测试。

覆盖要求（copilot-instructions §13.2）：
- 回测引擎模块 ≥ 15 测试
- 正常流、边界条件、空数据、极端值
"""
from __future__ import annotations

import pytest

from aipha.backtest.alpha_authenticity import (
    FACTOR_WEIGHT_BETA_THRESHOLD,
    START_DATE_SENSITIVITY_THRESHOLD,
    AlphaAuthenticityReport,
    FactorExposureCheck,
    SlippageRobustnessCheck,
    StartDateSensitivityCheck,
    check_factor_exposure,
    check_slippage_robustness,
    check_start_date_sensitivity,
    judge_alpha_authenticity,
)


# ============================================================================
# FactorExposureCheck
# ============================================================================


class TestFactorExposureCheck:
    """FactorExposureCheck dataclass 测试。"""

    def test_is_beta_above_threshold(self):
        c = FactorExposureCheck("size", 0.80, 0.40)
        assert c.is_beta is True

    def test_is_not_beta_below_threshold(self):
        c = FactorExposureCheck("size", 0.30, 0.40)
        assert c.is_beta is False

    def test_is_not_beta_at_threshold(self):
        c = FactorExposureCheck("size", 0.40, 0.40)
        assert c.is_beta is False

    def test_to_dict(self):
        c = FactorExposureCheck("momentum", 0.25, 0.40)
        d = c.to_dict()
        assert d["factor_name"] == "momentum"
        assert d["weight"] == 0.25
        assert d["is_beta"] is False

    def test_zero_weight(self):
        c = FactorExposureCheck("value", 0.0, 0.40)
        assert c.is_beta is False


# ============================================================================
# SlippageRobustnessCheck
# ============================================================================


class TestSlippageRobustnessCheck:
    """SlippageRobustnessCheck dataclass 测试。"""

    def test_positive_return(self):
        c = SlippageRobustnessCheck(15.0, 0.10, 0.5)
        assert c.is_positive is True

    def test_negative_return(self):
        c = SlippageRobustnessCheck(25.0, -0.05, -0.2)
        assert c.is_positive is False

    def test_zero_return_not_positive(self):
        c = SlippageRobustnessCheck(10.0, 0.0, 0.0)
        assert c.is_positive is False

    def test_to_dict(self):
        c = SlippageRobustnessCheck(10.0, 0.15, 0.8)
        d = c.to_dict()
        assert d["slippage_bps"] == 10.0
        assert d["is_positive"] is True


# ============================================================================
# StartDateSensitivityCheck
# ============================================================================


class TestStartDateSensitivityCheck:
    """StartDateSensitivityCheck dataclass 测试。"""

    def test_not_sensitive(self):
        c = StartDateSensitivityCheck(
            start_dates=("2015-01-05", "2015-01-06"),
            annualized_returns=(0.15, 0.16),
            returns_std=0.007,
            returns_range=0.01,
            threshold=0.10,
        )
        assert c.is_sensitive is False

    def test_sensitive(self):
        c = StartDateSensitivityCheck(
            start_dates=("2015-01-04", "2015-01-10"),
            annualized_returns=(0.30, 0.10),
            returns_std=0.141,
            returns_range=0.20,
            threshold=0.10,
        )
        assert c.is_sensitive is True

    def test_at_threshold_not_sensitive(self):
        c = StartDateSensitivityCheck(
            start_dates=("2015-01-05",),
            annualized_returns=(0.20,),
            returns_std=0.10,
            returns_range=0.0,
            threshold=0.10,
        )
        assert c.is_sensitive is False


# ============================================================================
# check_factor_exposure
# ============================================================================


class TestCheckFactorExposure:
    """check_factor_exposure 函数测试。"""

    def test_balanced_weights(self):
        weights = {"size": 0.20, "momentum": 0.25, "quality": 0.20, "value": 0.20, "volatility": 0.15}
        results = check_factor_exposure(weights)
        assert len(results) == 5
        assert all(not c.is_beta for c in results)

    def test_single_beta_factor(self):
        weights = {"size": 0.80, "momentum": 0.10, "quality": 0.10}
        results = check_factor_exposure(weights)
        beta_list = [c for c in results if c.is_beta]
        assert len(beta_list) == 1
        assert beta_list[0].factor_name == "size"

    def test_custom_threshold(self):
        weights = {"size": 0.30}
        results = check_factor_exposure(weights, threshold=0.25)
        assert results[0].is_beta is True

    def test_empty_weights(self):
        results = check_factor_exposure({})
        assert len(results) == 0

    def test_results_sorted_by_name(self):
        weights = {"z_factor": 0.50, "a_factor": 0.50}
        results = check_factor_exposure(weights)
        assert results[0].factor_name == "a_factor"
        assert results[1].factor_name == "z_factor"


# ============================================================================
# check_slippage_robustness
# ============================================================================


class TestCheckSlippageRobustness:
    """check_slippage_robustness 函数测试。"""

    def test_normal(self):
        data = [(3, 0.25, 1.2), (10, 0.15, 0.8), (25, -0.05, -0.2)]
        results = check_slippage_robustness(data)
        assert len(results) == 3
        assert results[0].is_positive is True
        assert results[2].is_positive is False

    def test_empty(self):
        results = check_slippage_robustness([])
        assert len(results) == 0

    def test_all_negative(self):
        data = [(3, -0.01, -0.1), (10, -0.05, -0.3)]
        results = check_slippage_robustness(data)
        assert all(not c.is_positive for c in results)


# ============================================================================
# check_start_date_sensitivity
# ============================================================================


class TestCheckStartDateSensitivity:
    """check_start_date_sensitivity 函数测试。"""

    def test_stable_dates(self):
        data = [
            ("2015-01-05", 0.15),
            ("2015-01-06", 0.16),
            ("2015-01-07", 0.14),
        ]
        result = check_start_date_sensitivity(data)
        assert result.is_sensitive is False
        assert result.returns_std < 0.05

    def test_sensitive_dates(self):
        data = [
            ("2015-01-04", 0.30),
            ("2015-01-10", 0.05),
        ]
        result = check_start_date_sensitivity(data)
        # std ≈ 0.177 > 0.10
        assert result.is_sensitive is True
        assert result.returns_range == pytest.approx(0.25, abs=0.01)

    def test_empty_input(self):
        result = check_start_date_sensitivity([])
        assert result.returns_std == 0.0
        assert result.is_sensitive is False
        assert len(result.start_dates) == 0

    def test_single_date(self):
        result = check_start_date_sensitivity([("2020-01-01", 0.15)])
        assert result.returns_std == 0.0
        assert result.is_sensitive is False

    def test_custom_threshold(self):
        data = [("2015-01-05", 0.10), ("2015-01-06", 0.12)]
        result = check_start_date_sensitivity(data, threshold=0.005)
        assert result.is_sensitive is True


# ============================================================================
# judge_alpha_authenticity
# ============================================================================


class TestJudgeAlphaAuthenticity:
    """judge_alpha_authenticity 综合判定测试。"""

    def test_genuine_alpha(self):
        weights = {"size": 0.20, "momentum": 0.25, "quality": 0.20, "value": 0.20, "volatility": 0.15}
        slippage = [(5, 0.20, 1.0), (10, 0.15, 0.8), (15, 0.10, 0.5)]
        dates = [("2015-01-05", 0.17), ("2015-01-06", 0.16), ("2015-01-07", 0.18)]
        report = judge_alpha_authenticity(
            "genuine_test",
            weights,
            slippage_results=slippage,
            start_date_results=dates,
        )
        assert report.verdict == "GENUINE_ALPHA"
        assert report.score >= 7.0

    def test_likely_beta(self):
        weights = {"size": 0.80, "momentum": 0.10, "quality": 0.10}
        slippage = [(3, 0.25, 1.2), (5, -0.05, -0.3), (10, -0.20, -0.8)]
        dates = [("2015-01-04", 0.30), ("2015-01-10", 0.05)]
        report = judge_alpha_authenticity(
            "beta_test",
            weights,
            slippage_results=slippage,
            start_date_results=dates,
        )
        assert report.verdict == "LIKELY_BETA"
        assert report.score < 4.0
        assert len(report.warnings) >= 2

    def test_mixed_alpha_beta(self):
        weights = {"size": 0.45, "momentum": 0.30, "quality": 0.25}
        slippage = [(5, 0.10, 0.5), (15, 0.02, 0.1)]
        report = judge_alpha_authenticity(
            "mixed_test",
            weights,
            slippage_results=slippage,
        )
        assert report.verdict == "MIXED_ALPHA_BETA"

    def test_no_slippage_no_dates(self):
        """不提供滑点和日期数据时使用中性分。"""
        weights = {"size": 0.20, "momentum": 0.40, "quality": 0.40}
        report = judge_alpha_authenticity("partial_test", weights)
        assert "未提供滑点" in report.warnings[0] or "未提供起始" in report.warnings[-1]
        assert report.score > 0

    def test_multiple_beta_factors(self):
        """多个因子超过阈值。"""
        weights = {"size": 0.50, "momentum": 0.50}
        report = judge_alpha_authenticity("multi_beta", weights)
        beta_warnings = [w for w in report.warnings if "beta" in w.lower() or "暴露" in w]
        assert len(beta_warnings) >= 2

    def test_report_to_dict(self):
        weights = {"size": 0.20, "value": 0.30}
        report = judge_alpha_authenticity("dict_test", weights)
        d = report.to_dict()
        assert "config_name" in d
        assert "score" in d
        assert "verdict" in d
        assert "factor_checks" in d

    def test_only_slippage_data(self):
        """只有滑点数据，没有日期数据。"""
        weights = {"size": 0.30, "value": 0.30, "quality": 0.40}
        slippage = [(5, 0.15, 0.7), (15, 0.08, 0.4)]
        report = judge_alpha_authenticity(
            "slippage_only",
            weights,
            slippage_results=slippage,
        )
        assert report.slippage_checks
        assert report.start_date_check is None

    def test_score_capped_at_10(self):
        """评分不超过 10。"""
        weights = {"a": 0.10, "b": 0.10, "c": 0.80}  # 这里 c 还是会扣分
        report = judge_alpha_authenticity("cap_test", weights)
        assert report.score <= 10.0


# ============================================================================
# AlphaAuthenticityReport
# ============================================================================


class TestAlphaAuthenticityReport:
    """AlphaAuthenticityReport dataclass 测试。"""

    def test_default_values(self):
        r = AlphaAuthenticityReport(config_name="test")
        assert r.score == 0.0
        assert r.verdict == ""
        assert r.factor_checks == []

    def test_to_dict_empty(self):
        r = AlphaAuthenticityReport(config_name="empty")
        d = r.to_dict()
        assert d["config_name"] == "empty"
        assert d["factor_checks"] == []
        assert "slippage_checks" not in d

    def test_to_dict_with_slippage(self):
        r = AlphaAuthenticityReport(
            config_name="full",
            slippage_checks=[SlippageRobustnessCheck(10.0, 0.15, 0.8)],
        )
        d = r.to_dict()
        assert "slippage_checks" in d
        assert len(d["slippage_checks"]) == 1
