"""多周期 ML 预测集成策略测试

覆盖：
- 配置解析与默认值
- 单周期回退模式
- 多周期预测加载
- IC 权重计算（equal / fixed / ic_weighted）
- 分数融合与 z-score 标准化
- 候选排名
- 注册表集成
"""

from __future__ import annotations

import math
from datetime import datetime
from unittest.mock import patch

import numpy as np
import pytest

from aipha.strategy.base import StrategyContext
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.multi_period_ensemble import (
    MultiPeriodEnsembleConfig,
    MultiPeriodEnsembleStrategy,
    PeriodConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_context(date_str: str = "2023-06-01") -> StrategyContext:
    """构造最小 StrategyContext。"""
    return StrategyContext(
        current_time=datetime.strptime(date_str, "%Y-%m-%d"),
        nav=1_000_000.0,
        cash=500_000.0,
        positions={},
    )


def _make_candidates(symbols: list[str]) -> list[EquityCandidateInfo]:
    return [
        EquityCandidateInfo(symbol=s, close=10.0, metadata={})
        for s in symbols
    ]


# ---------------------------------------------------------------------------
# 配置测试
# ---------------------------------------------------------------------------

class TestMultiPeriodEnsembleConfig:
    """配置创建与默认值。"""

    def test_default_config(self):
        cfg = MultiPeriodEnsembleConfig()
        assert cfg.blend_method == "ic_weighted"
        assert cfg.score_normalize is True
        assert cfg.ic_min_weight == 0.05

    def test_periods_from_dict(self):
        cfg = MultiPeriodEnsembleConfig(
            periods=[
                {"horizon_days": 5, "predictions_path": "a.parquet"},
                {"horizon_days": 10, "predictions_path": "b.parquet"},
            ],
        )
        assert len(cfg.periods) == 2

    def test_fallback_weights(self):
        cfg = MultiPeriodEnsembleConfig()
        assert "pe_ttm" in cfg.fallback_weights

    def test_single_period_via_oos_path(self):
        cfg = MultiPeriodEnsembleConfig(
            oos_predictions_path="test.parquet",
            forward_days=10,
        )
        assert cfg.oos_predictions_path == "test.parquet"
        assert cfg.forward_days == 10


# ---------------------------------------------------------------------------
# 策略创建测试
# ---------------------------------------------------------------------------

class TestMultiPeriodEnsembleCreation:
    """策略实例化。"""

    def test_create_no_files(self):
        """无预测文件 → model_ready=False。"""
        cfg = MultiPeriodEnsembleConfig(
            periods=[
                {"horizon_days": 5, "predictions_path": "nonexistent.pq"},
            ],
        )
        s = MultiPeriodEnsembleStrategy(config=cfg)
        assert not s._model_ready

    def test_create_from_kwargs(self):
        """kwargs 模式创建。"""
        s = MultiPeriodEnsembleStrategy(
            top_k=30, rebalance_days=5, blend_method="equal",
        )
        assert s.config.top_k == 30
        assert s.config.blend_method == "equal"

    def test_name(self):
        s = MultiPeriodEnsembleStrategy()
        assert s.name == "ml_factor_ensemble"


# ---------------------------------------------------------------------------
# IC 权重计算测试
# ---------------------------------------------------------------------------

class TestICWeights:
    """IC 权重计算逻辑。"""

    def _make_strategy_with_scores(self) -> MultiPeriodEnsembleStrategy:
        """构造含手动注入分数的策略。"""
        cfg = MultiPeriodEnsembleConfig(
            periods=[
                {"horizon_days": 5, "predictions_path": "dummy"},
                {"horizon_days": 10, "predictions_path": "dummy"},
            ],
            blend_method="ic_weighted",
            ic_min_weight=0.01,
        )
        s = MultiPeriodEnsembleStrategy.__new__(
            MultiPeriodEnsembleStrategy,
        )
        s._config = cfg
        s._period_configs = [
            PeriodConfig(horizon_days=5, predictions_path="dummy"),
            PeriodConfig(horizon_days=10, predictions_path="dummy"),
        ]
        s._model_ready = True
        s._ic_cache = {5: {}, 10: {}}
        # 注入 mock scores
        s._period_scores = {5: {}, 10: {}}
        s._period_returns = {5: {}, 10: {}}
        return s

    def test_equal_weights(self):
        s = self._make_strategy_with_scores()
        s._config.blend_method = "equal"
        w = s._compute_ic_weights("2023-06-01")
        assert len(w) == 2
        assert abs(w[5] - 0.5) < 1e-6
        assert abs(w[10] - 0.5) < 1e-6

    def test_single_horizon(self):
        s = self._make_strategy_with_scores()
        s._period_scores = {5: {"2023-06-01": {"A": 0.5}}}
        s._period_returns = {5: {}}
        s._ic_cache = {5: {}}
        w = s._compute_ic_weights("2023-06-01")
        assert w[5] == 1.0

    def test_fixed_weights(self):
        s = self._make_strategy_with_scores()
        s._config.blend_method = "fixed"
        s._period_configs[0].weight = 0.6
        s._period_configs[1].weight = 0.4
        w = s._compute_ic_weights("2023-06-01")
        assert abs(w[5] - 0.6) < 1e-6
        assert abs(w[10] - 0.4) < 1e-6


# ---------------------------------------------------------------------------
# 分数融合测试
# ---------------------------------------------------------------------------

class TestBlendScores:
    """分数融合逻辑。"""

    def _make_strategy(
        self, blend_method: str = "equal",
    ) -> MultiPeriodEnsembleStrategy:
        cfg = MultiPeriodEnsembleConfig(
            periods=[
                {"horizon_days": 5, "predictions_path": "d"},
                {"horizon_days": 10, "predictions_path": "d"},
            ],
            blend_method=blend_method,
            score_normalize=False,
        )
        s = MultiPeriodEnsembleStrategy.__new__(
            MultiPeriodEnsembleStrategy,
        )
        s._config = cfg
        s._period_configs = [
            PeriodConfig(horizon_days=5, predictions_path="d"),
            PeriodConfig(horizon_days=10, predictions_path="d"),
        ]
        s._model_ready = True
        s._ic_cache = {5: {}, 10: {}}
        s._period_scores = {
            5: {"2023-06-01": {"A": 0.9, "B": 0.3, "C": 0.6}},
            10: {"2023-06-01": {"A": 0.7, "B": 0.8, "C": 0.5}},
        }
        s._period_returns = {5: {}, 10: {}}
        return s

    def test_equal_blend_no_normalize(self):
        s = self._make_strategy("equal")
        scores = s._blend_scores("2023-06-01", ["A", "B", "C"])
        # A: 0.5*0.9 + 0.5*0.7 = 0.80
        # B: 0.5*0.3 + 0.5*0.8 = 0.55
        # C: 0.5*0.6 + 0.5*0.5 = 0.55
        assert abs(scores["A"] - 0.80) < 1e-6
        assert abs(scores["B"] - 0.55) < 1e-6

    def test_blend_with_missing_symbol(self):
        """标的 D 不在任何周期中 → 分数 0。"""
        s = self._make_strategy("equal")
        scores = s._blend_scores(
            "2023-06-01", ["A", "D"],
        )
        assert abs(scores["A"] - 0.80) < 1e-6
        assert scores["D"] == 0.0

    def test_blend_missing_date(self):
        """日期无数据 → 全零。"""
        s = self._make_strategy("equal")
        scores = s._blend_scores("2099-01-01", ["A", "B"])
        assert scores["A"] == 0.0
        assert scores["B"] == 0.0

    def test_blend_z_score_normalize(self):
        """z-score 标准化后均值应接近 0。"""
        s = self._make_strategy("equal")
        s._config.score_normalize = True
        scores = s._blend_scores("2023-06-01", ["A", "B", "C"])
        mean_score = np.mean(list(scores.values()))
        assert abs(mean_score) < 0.1  # 近似零


# ---------------------------------------------------------------------------
# 候选排名测试
# ---------------------------------------------------------------------------

class TestScoreCandidates:
    """_score_candidates 集成测试。"""

    def test_empty_candidates(self):
        s = MultiPeriodEnsembleStrategy()
        result = s._score_candidates([], _make_context())
        assert result == []

    def test_no_model_uses_fallback(self):
        s = MultiPeriodEnsembleStrategy()
        cands = _make_candidates(["A", "B"])
        result = s._score_candidates(cands, _make_context())
        assert isinstance(result, list)

    def test_ranking_order(self):
        """验证排名顺序正确。"""
        cfg = MultiPeriodEnsembleConfig(
            blend_method="equal",
            score_normalize=False,
        )
        s = MultiPeriodEnsembleStrategy.__new__(
            MultiPeriodEnsembleStrategy,
        )
        s._config = cfg
        s._period_configs = []
        s._model_ready = True
        s._ic_cache = {5: {}}
        s._period_scores = {
            5: {"2023-06-01": {"A": 0.1, "B": 0.9, "C": 0.5}},
        }
        s._period_returns = {5: {}}

        cands = _make_candidates(["A", "B", "C"])
        ctx = _make_context("2023-06-01")
        result = s._score_candidates(cands, ctx)
        assert result[0] == "B"
        assert result[-1] == "A"


# ---------------------------------------------------------------------------
# 注册表集成
# ---------------------------------------------------------------------------

class TestRegistryIntegration:
    """策略注册表。"""

    def test_registered(self):
        from aipha.strategy.registry import StrategyRegistry
        reg = StrategyRegistry()
        cls = reg.get("ml_factor_ensemble")
        assert cls is not None
        assert cls is MultiPeriodEnsembleStrategy

    def test_create_via_registry(self):
        from aipha.strategy.registry import create_strategy
        s = create_strategy("ml_factor_ensemble", top_k=50)
        assert isinstance(s, MultiPeriodEnsembleStrategy)
        assert s.config.top_k == 50
