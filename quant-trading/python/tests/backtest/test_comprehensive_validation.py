"""ComprehensiveValidator 综合验证测试。"""

from __future__ import annotations

import math

import pytest

from aipha.backtest.comprehensive_validation import (
    CB_NEW_RULE_DATE,
    CapacityResult,
    ComprehensiveReport,
    ComprehensiveValidator,
    SegmentMetrics,
    StressScenario,
    ValidationConfig,
)


# ------------------------------------------------------------------
# 辅助
# ------------------------------------------------------------------

def _good_returns(n: int = 500, daily_mean: float = 0.001) -> list[float]:
    """生成有正 alpha 的日收益率序列。"""
    import random
    rng = random.Random(42)
    return [daily_mean + rng.gauss(0, 0.01) for _ in range(n)]


def _dates(n: int = 500, start: str = "2020-01-02") -> list[str]:
    """生成交易日期序列（简化）。"""
    from datetime import date, timedelta
    d = date.fromisoformat(start)
    result = []
    for _ in range(n):
        result.append(d.isoformat())
        d += timedelta(days=1)
        # 跳过周末
        while d.weekday() >= 5:
            d += timedelta(days=1)
    return result


# ------------------------------------------------------------------
# 数据结构测试
# ------------------------------------------------------------------

class TestDataStructures:
    def test_segment_metrics_to_dict(self):
        sm = SegmentMetrics(label="full", start_date="2020-01-01",
                            end_date="2024-12-31", annual_return=0.25)
        d = sm.to_dict()
        assert d["label"] == "full"
        assert d["annual_return"] == 0.25

    def test_stress_scenario_to_dict(self):
        ss = StressScenario(name="covid", start_date="2020-01-20",
                            end_date="2020-03-23", drawdown=0.15)
        d = ss.to_dict()
        assert d["name"] == "covid"
        assert d["drawdown"] == 0.15

    def test_capacity_result_to_dict(self):
        cr = CapacityResult(capital_level=100, annual_return=0.2)
        d = cr.to_dict()
        assert d["capital_level"] == 100

    def test_comprehensive_report_to_dict(self):
        r = ComprehensiveReport(strategy_name="test", verdict="PASS")
        d = r.to_dict()
        assert d["strategy_name"] == "test"
        assert d["verdict"] == "PASS"


# ------------------------------------------------------------------
# ValidationConfig 测试
# ------------------------------------------------------------------

class TestValidationConfig:
    def test_defaults(self):
        cfg = ValidationConfig()
        assert cfg.n_trials == 45
        assert cfg.min_sharpe == 0.8
        assert cfg.post_rule_min_sr == 0.5

    def test_custom(self):
        cfg = ValidationConfig(n_trials=10, min_sharpe=1.0)
        assert cfg.n_trials == 10
        assert cfg.min_sharpe == 1.0


# ------------------------------------------------------------------
# ComprehensiveValidator 测试
# ------------------------------------------------------------------

class TestComprehensiveValidator:
    def test_validate_good_strategy(self):
        """好策略应通过 DSR + 排列检验。"""
        v = ComprehensiveValidator(ValidationConfig(
            n_trials=5, permutation_n=100))
        rets = _good_returns(500, daily_mean=0.002)
        report = v.validate("good_strat", rets)
        assert report.strategy_name == "good_strat"
        assert report.run_date != ""
        assert "dsr" in report.to_dict()
        assert "permutation" in report.to_dict()

    def test_validate_bad_strategy(self):
        """零收益策略不应通过。"""
        v = ComprehensiveValidator(ValidationConfig(
            n_trials=50, permutation_n=50))
        rets = [0.0] * 100
        report = v.validate("zero_strat", rets)
        assert report.verdict == "FAIL"

    def test_validate_insufficient_data(self):
        """数据不足时应有 error 标记。"""
        v = ComprehensiveValidator()
        report = v.validate("short", [0.01] * 5)
        assert report.dsr.get("error") is not None

    def test_segments_with_dates(self):
        """带日期的分段评估。"""
        v = ComprehensiveValidator(ValidationConfig(permutation_n=50))
        # 跨新规前后
        dates = _dates(800, start="2021-01-04")
        rets = _good_returns(800, daily_mean=0.001)
        report = v.validate("seg_test", rets, nav_dates=dates)
        assert len(report.segments) >= 2  # full + at least 1 segment
        labels = [s["label"] for s in report.segments]
        assert "full" in labels

    def test_stress_tests(self):
        """压力测试场景。"""
        v = ComprehensiveValidator(ValidationConfig(permutation_n=50))
        dates = _dates(1200, start="2018-01-02")
        rets = _good_returns(1200, daily_mean=0.0005)
        report = v.validate("stress_test", rets, nav_dates=dates)
        assert len(report.stress_tests) > 0

    def test_wf_gates_integration(self):
        """WF 报告集成。"""
        v = ComprehensiveValidator(ValidationConfig(permutation_n=50))
        rets = _good_returns(300)
        wf = {
            "avg_sharpe_decay": 0.3,
            "avg_return_decay": 0.4,
            "test_win_rate": 0.8,
            "avg_test_sharpe": 0.9,
            "score": 8.0,
            "verdict": "PASS",
        }
        report = v.validate("wf_test", rets, wf_report=wf)
        assert "passed" in report.wf_gates

    def test_report_serializable(self):
        """Report 可序列化为 dict。"""
        v = ComprehensiveValidator(ValidationConfig(permutation_n=50))
        rets = _good_returns(200)
        report = v.validate("serial_test", rets)
        d = report.to_dict()
        assert isinstance(d, dict)
        assert "score" in d
        assert isinstance(d["score"], float)

    def test_custom_stress_scenarios(self):
        """自定义压力场景。"""
        v = ComprehensiveValidator(ValidationConfig(permutation_n=50))
        dates = _dates(500, start="2020-01-02")
        rets = _good_returns(500)
        custom = [{"name": "test_shock", "start": "2020-03-01", "end": "2020-03-31"}]
        report = v.validate("custom_stress", rets,
                            nav_dates=dates, stress_scenarios=custom)
        assert len(report.stress_tests) == 1
        assert report.stress_tests[0]["name"] == "test_shock"
