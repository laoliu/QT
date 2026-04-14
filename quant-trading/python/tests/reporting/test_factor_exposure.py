"""FactorExposureAnalyzer 单元测试

覆盖：初始化、record、record_from_position_values、summary、
to_dict、format_summary、空数据、单持仓、边界条件。
"""

from __future__ import annotations

from datetime import date

import pytest

from aipha.reporting.factor_exposure import (
    FactorExposureAnalyzer,
    FactorExposureSnapshot,
    FactorExposureSummary,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def analyzer():
    return FactorExposureAnalyzer(["size", "value", "momentum"])


@pytest.fixture
def simple_scores():
    """两只股票的因子分数。"""
    return {
        "size": {"A": 0.5, "B": -0.3},
        "value": {"A": 0.8, "B": 0.2},
        "momentum": {"A": -0.1, "B": 0.6},
    }


# ---------------------------------------------------------------------------
# 初始化测试
# ---------------------------------------------------------------------------

class TestInitialization:
    def test_empty_factor_names_raises(self):
        with pytest.raises(ValueError, match="must not be empty"):
            FactorExposureAnalyzer([])

    def test_factor_names_property(self, analyzer):
        assert analyzer.factor_names == ["size", "value", "momentum"]

    def test_initial_empty(self, analyzer):
        assert analyzer.num_snapshots == 0
        assert analyzer.snapshots == []

    def test_factor_names_copy(self, analyzer):
        """factor_names 返回拷贝，不影响内部状态。"""
        names = analyzer.factor_names
        names.append("extra")
        assert "extra" not in analyzer.factor_names


# ---------------------------------------------------------------------------
# record 方法测试
# ---------------------------------------------------------------------------

class TestRecord:
    def test_basic_record(self, analyzer, simple_scores):
        weights = {"A": 0.6, "B": 0.4}
        snap = analyzer.record(date(2025, 1, 2), weights, simple_scores)

        assert isinstance(snap, FactorExposureSnapshot)
        assert snap.trade_date == date(2025, 1, 2)
        assert snap.num_holdings == 2
        # size: 0.6*0.5 + 0.4*(-0.3) = 0.30 - 0.12 = 0.18
        assert pytest.approx(snap.exposures["size"], abs=1e-9) == 0.18
        # value: 0.6*0.8 + 0.4*0.2 = 0.48 + 0.08 = 0.56
        assert pytest.approx(snap.exposures["value"], abs=1e-9) == 0.56
        # momentum: 0.6*(-0.1) + 0.4*0.6 = -0.06 + 0.24 = 0.18
        assert pytest.approx(snap.exposures["momentum"], abs=1e-9) == 0.18

    def test_missing_factor_scores_treated_as_zero(self, analyzer):
        """如果某因子没有分数数据，暴露为 0。"""
        weights = {"A": 1.0}
        scores = {"size": {"A": 0.5}}  # 缺 value 和 momentum
        snap = analyzer.record(date(2025, 1, 3), weights, scores)
        assert snap.exposures["value"] == 0.0
        assert snap.exposures["momentum"] == 0.0

    def test_missing_symbol_score_treated_as_zero(self, analyzer, simple_scores):
        """如果某标的没有某因子分数，该标的对该因子贡献为 0。"""
        weights = {"A": 0.5, "C": 0.5}  # C not in simple_scores
        snap = analyzer.record(date(2025, 1, 3), weights, simple_scores)
        # size: 0.5*0.5 + 0.5*0.0 = 0.25
        assert pytest.approx(snap.exposures["size"], abs=1e-9) == 0.25

    def test_empty_holdings(self, analyzer, simple_scores):
        """空持仓所有暴露为 0。"""
        snap = analyzer.record(date(2025, 1, 3), {}, simple_scores)
        for factor in ["size", "value", "momentum"]:
            assert snap.exposures[factor] == 0.0
        assert snap.num_holdings == 0

    def test_num_snapshots_increments(self, analyzer, simple_scores):
        weights = {"A": 1.0}
        for i in range(5):
            analyzer.record(date(2025, 1, i + 1), weights, simple_scores)
        assert analyzer.num_snapshots == 5

    def test_snapshot_frozen(self, analyzer, simple_scores):
        snap = analyzer.record(date(2025, 1, 1), {"A": 1.0}, simple_scores)
        with pytest.raises(AttributeError):
            snap.trade_date = date(2025, 2, 1)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# record_from_position_values 测试
# ---------------------------------------------------------------------------

class TestRecordFromPositionValues:
    def test_basic(self, analyzer, simple_scores):
        pos_values = {"A": 60_000, "B": 40_000}
        nav = 100_000
        snap = analyzer.record_from_position_values(
            date(2025, 1, 2), pos_values, nav, simple_scores
        )
        # weights: A=0.6, B=0.4 → same as TestRecord.test_basic_record
        assert pytest.approx(snap.exposures["size"], abs=1e-9) == 0.18
        assert pytest.approx(snap.exposures["value"], abs=1e-9) == 0.56

    def test_zero_nav(self, analyzer, simple_scores):
        """NAV=0 → 空权重 → 暴露全 0。"""
        pos_values = {"A": 60_000}
        snap = analyzer.record_from_position_values(
            date(2025, 1, 2), pos_values, 0.0, simple_scores
        )
        assert snap.exposures["size"] == 0.0
        assert snap.num_holdings == 0

    def test_negative_nav(self, analyzer, simple_scores):
        """NAV<0 → 空权重 → 暴露全 0。"""
        snap = analyzer.record_from_position_values(
            date(2025, 1, 2), {"A": 100}, -1000, simple_scores
        )
        assert snap.num_holdings == 0

    def test_zero_value_position_excluded(self, analyzer, simple_scores):
        """market_value=0 的持仓不参与计算。"""
        pos_values = {"A": 100_000, "B": 0}
        nav = 100_000
        snap = analyzer.record_from_position_values(
            date(2025, 1, 2), pos_values, nav, simple_scores
        )
        assert snap.num_holdings == 1
        # size: 1.0*0.5 = 0.5
        assert pytest.approx(snap.exposures["size"], abs=1e-9) == 0.5


# ---------------------------------------------------------------------------
# summary 测试
# ---------------------------------------------------------------------------

class TestSummary:
    def test_empty_returns_empty_list(self, analyzer):
        assert analyzer.summary() == []

    def test_single_snapshot(self, analyzer, simple_scores):
        analyzer.record(date(2025, 1, 1), {"A": 1.0}, simple_scores)
        summaries = analyzer.summary()
        assert len(summaries) == 3
        # size: only one value 0.5
        size_s = summaries[0]
        assert size_s.factor_name == "size"
        assert pytest.approx(size_s.mean_exposure) == 0.5
        assert pytest.approx(size_s.std_exposure) == 0.0
        assert pytest.approx(size_s.min_exposure) == 0.5
        assert pytest.approx(size_s.max_exposure) == 0.5
        assert pytest.approx(size_s.latest_exposure) == 0.5

    def test_multiple_snapshots(self, analyzer):
        scores1 = {"size": {"A": 0.2}, "value": {"A": 0.4}, "momentum": {"A": 0.0}}
        scores2 = {"size": {"A": 0.8}, "value": {"A": 0.6}, "momentum": {"A": 1.0}}
        analyzer.record(date(2025, 1, 1), {"A": 1.0}, scores1)
        analyzer.record(date(2025, 1, 2), {"A": 1.0}, scores2)

        summaries = analyzer.summary()
        size_s = summaries[0]
        assert pytest.approx(size_s.mean_exposure) == 0.5  # (0.2+0.8)/2
        assert pytest.approx(size_s.min_exposure) == 0.2
        assert pytest.approx(size_s.max_exposure) == 0.8
        assert pytest.approx(size_s.latest_exposure) == 0.8

    def test_summary_items_are_frozen(self, analyzer, simple_scores):
        analyzer.record(date(2025, 1, 1), {"A": 1.0}, simple_scores)
        s = analyzer.summary()[0]
        with pytest.raises(AttributeError):
            s.factor_name = "hacked"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# to_dict 测试
# ---------------------------------------------------------------------------

class TestToDict:
    def test_structure(self, analyzer, simple_scores):
        analyzer.record(date(2025, 1, 2), {"A": 0.6, "B": 0.4}, simple_scores)
        d = analyzer.to_dict()
        assert d["factor_names"] == ["size", "value", "momentum"]
        assert d["num_snapshots"] == 1
        assert len(d["snapshots"]) == 1
        assert d["snapshots"][0]["date"] == "2025-01-02"
        assert d["snapshots"][0]["num_holdings"] == 2
        assert len(d["summary"]) == 3

    def test_summary_dict_keys(self, analyzer, simple_scores):
        analyzer.record(date(2025, 1, 2), {"A": 1.0}, simple_scores)
        sd = analyzer.to_dict()["summary"][0]
        assert set(sd.keys()) == {"factor", "mean", "std", "min", "max", "latest"}

    def test_empty_to_dict(self, analyzer):
        d = analyzer.to_dict()
        assert d["num_snapshots"] == 0
        assert d["snapshots"] == []
        assert d["summary"] == []


# ---------------------------------------------------------------------------
# FactorExposureSummary.to_dict 测试
# ---------------------------------------------------------------------------

class TestFactorExposureSummaryToDict:
    def test_to_dict_rounding(self):
        s = FactorExposureSummary(
            factor_name="test",
            mean_exposure=0.123456789,
            std_exposure=0.0001234,
            min_exposure=-0.999999,
            max_exposure=1.111111,
            latest_exposure=0.5,
        )
        d = s.to_dict()
        assert d["factor"] == "test"
        assert d["mean"] == 0.123457
        assert d["std"] == 0.000123
        assert d["latest"] == 0.5


# ---------------------------------------------------------------------------
# format_summary 测试
# ---------------------------------------------------------------------------

class TestFormatSummary:
    def test_empty_message(self, analyzer):
        text = analyzer.format_summary()
        assert "No data recorded" in text

    def test_has_header_and_data(self, analyzer, simple_scores):
        analyzer.record(date(2025, 1, 1), {"A": 1.0}, simple_scores)
        text = analyzer.format_summary()
        assert "Factor Exposure Summary" in text
        assert "size" in text
        assert "value" in text
        assert "momentum" in text
        # 应该有数值行
        assert "0.5000" in text  # size exposure = 0.5


# ---------------------------------------------------------------------------
# 集成场景：多日追踪
# ---------------------------------------------------------------------------

class TestMultiDayTracking:
    def test_exposure_drift(self):
        """模拟 3 天的持仓变化追踪暴露漂移。"""
        analyzer = FactorExposureAnalyzer(["size", "value"])

        # Day 1: 全仓大市值
        analyzer.record(
            date(2025, 1, 1),
            {"BIG": 1.0},
            {"size": {"BIG": -0.8, "SMALL": 0.9}, "value": {"BIG": 0.3, "SMALL": 0.7}},
        )
        # Day 2: 一半小市值
        analyzer.record(
            date(2025, 1, 2),
            {"BIG": 0.5, "SMALL": 0.5},
            {"size": {"BIG": -0.8, "SMALL": 0.9}, "value": {"BIG": 0.3, "SMALL": 0.7}},
        )
        # Day 3: 全仓小市值
        analyzer.record(
            date(2025, 1, 3),
            {"SMALL": 1.0},
            {"size": {"BIG": -0.8, "SMALL": 0.9}, "value": {"BIG": 0.3, "SMALL": 0.7}},
        )

        summaries = analyzer.summary()
        size_s = next(s for s in summaries if s.factor_name == "size")
        # Day 1: -0.8, Day 2: 0.5*(-0.8)+0.5*0.9=0.05, Day 3: 0.9
        assert pytest.approx(size_s.min_exposure, abs=1e-6) == -0.8
        assert pytest.approx(size_s.max_exposure, abs=1e-6) == 0.9
        assert pytest.approx(size_s.latest_exposure, abs=1e-6) == 0.9

        d = analyzer.to_dict()
        assert d["num_snapshots"] == 3

    def test_single_factor(self):
        """单因子分析器也能正常工作。"""
        analyzer = FactorExposureAnalyzer(["quality"])
        analyzer.record(
            date(2025, 3, 1),
            {"X": 0.7, "Y": 0.3},
            {"quality": {"X": 0.9, "Y": 0.1}},
        )
        snap = analyzer.snapshots[0]
        # 0.7*0.9 + 0.3*0.1 = 0.63 + 0.03 = 0.66
        assert pytest.approx(snap.exposures["quality"], abs=1e-9) == 0.66
