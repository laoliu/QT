"""时序基础模型评估框架单元测试。"""

from __future__ import annotations

import pytest
import numpy as np
import pandas as pd

from aipha.models.ts_foundation_eval import (
    TSFoundationEvaluator,
    TSModelAdapter,
    NaiveAdapter,
    LastValueAdapter,
    PredictionScore,
    EvaluationReport,
)


# ── 自定义测试适配器 ──


class PerfectAdapter(TSModelAdapter):
    """完美预测（测试用）"""

    @property
    def name(self) -> str:
        return "perfect"

    def predict(self, history: np.ndarray, horizon: int) -> np.ndarray:
        # 实际中不可能，仅用于测试
        return np.zeros(horizon)


class ConstantAdapter(TSModelAdapter):
    """常数预测"""

    def __init__(self, value: float = 0.01):
        self.value = value

    @property
    def name(self) -> str:
        return f"constant_{self.value}"

    def predict(self, history: np.ndarray, horizon: int) -> np.ndarray:
        return np.full(horizon, self.value)


# ── PredictionScore 测试 ──


class TestPredictionScore:
    def test_to_dict(self):
        s = PredictionScore(
            model_name="test", ic=0.05, direction_accuracy=0.55,
        )
        d = s.to_dict()
        assert d["model_name"] == "test"
        assert d["ic"] == 0.05

    def test_default_values(self):
        s = PredictionScore(model_name="x")
        assert s.ic == 0.0
        assert s.mse == 0.0


# ── EvaluationReport 测试 ──


class TestEvaluationReport:
    def test_empty_report(self):
        r = EvaluationReport()
        assert r.n_samples == 0
        assert len(r.scores) == 0

    def test_summary_output(self):
        r = EvaluationReport(scores=[
            PredictionScore("naive", ic=0.02, icir=0.5, direction_accuracy=0.51),
        ])
        s = r.summary()
        assert "naive" in s
        assert "IC" in s


# ── 适配器测试 ──


class TestNaiveAdapter:
    def test_predict_shape(self):
        adapter = NaiveAdapter()
        h = np.random.randn(100)
        pred = adapter.predict(h, horizon=5)
        assert pred.shape == (5,)

    def test_name(self):
        assert NaiveAdapter().name == "naive_mean"


class TestLastValueAdapter:
    def test_predict_last(self):
        adapter = LastValueAdapter()
        h = np.array([1.0, 2.0, 3.0])
        pred = adapter.predict(h, horizon=3)
        np.testing.assert_array_equal(pred, [3.0, 3.0, 3.0])


# ── 评估器测试 ──


class TestTSFoundationEvaluator:
    def test_default_models_registered(self):
        ev = TSFoundationEvaluator()
        assert "naive_mean" in ev._models
        assert "last_value" in ev._models

    def test_register_custom_model(self):
        ev = TSFoundationEvaluator()
        ev.register_model("constant", ConstantAdapter(0.01))
        assert "constant" in ev._models

    def test_evaluate_basic(self):
        rng = np.random.RandomState(42)
        returns = pd.Series(rng.randn(200) * 0.02)
        ev = TSFoundationEvaluator(lookback=30)
        report = ev.evaluate(returns, horizon=5, step=5)
        assert report.n_samples > 0
        assert len(report.scores) >= 2  # naive + last_value

    def test_evaluate_with_custom(self):
        rng = np.random.RandomState(0)
        returns = pd.Series(rng.randn(150) * 0.01)
        ev = TSFoundationEvaluator(lookback=20)
        ev.register_model("const", ConstantAdapter(0.005))
        report = ev.evaluate(returns, horizon=3, step=3)
        names = [s.model_name for s in report.scores]
        assert "const" in names

    def test_evaluate_too_short(self):
        returns = pd.Series([0.01, 0.02])
        ev = TSFoundationEvaluator(lookback=60)
        report = ev.evaluate(returns, horizon=5)
        assert report.n_samples == 0

    def test_direction_accuracy_range(self):
        rng = np.random.RandomState(1)
        returns = pd.Series(rng.randn(300) * 0.02)
        ev = TSFoundationEvaluator(lookback=30)
        report = ev.evaluate(returns, horizon=5, step=10)
        for s in report.scores:
            assert 0.0 <= s.direction_accuracy <= 1.0

    def test_report_to_dict(self):
        rng = np.random.RandomState(2)
        returns = pd.Series(rng.randn(100) * 0.01)
        ev = TSFoundationEvaluator(lookback=20)
        report = ev.evaluate(returns, horizon=3, step=5)
        d = report.to_dict()
        assert "horizon" in d
        assert "scores" in d
