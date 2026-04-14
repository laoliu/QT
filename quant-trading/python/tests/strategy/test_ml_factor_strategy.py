"""Tests for MLFactorStrategy — ML 多因子选股策略。"""

from __future__ import annotations

import math
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from aipha.strategy.ml_factor_strategy import (
    MLFactorConfig,
    MLFactorStrategy,
)
from aipha.strategy.equity_rotation_base import EquityCandidateInfo
from aipha.strategy.base import StrategyContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_candidates(
    n: int = 30, seed: int = 42,
) -> list[EquityCandidateInfo]:
    """生成测试候选标的列表。"""
    rng = np.random.RandomState(seed)
    candidates = []
    for i in range(n):
        sym = f"SH{600000 + i:06d}"
        close = 10.0 + rng.randn() * 3
        meta: dict[str, Any] = {
            "pe_ttm": 10 + rng.randn() * 5,
            "pb": 1.0 + rng.randn() * 0.5,
            "roe": 0.08 + rng.randn() * 0.03,
            "circ_mv": 500000 + rng.randn() * 100000,
            "turnover_rate": 2.0 + rng.randn() * 1.0,
        }
        candidates.append(EquityCandidateInfo(
            symbol=sym,
            close=max(close, 1.0),
            metadata=meta,
            circ_mv=meta["circ_mv"],
            turnover_rate=meta["turnover_rate"],
            amount=rng.uniform(1000, 50000),
            list_date="20180101",
            name=f"Test{i}",
        ))
    return candidates


def _make_context(date_str: str = "2024-06-01") -> StrategyContext:
    """Minimal StrategyContext for testing."""
    ctx = MagicMock(spec=StrategyContext)
    ts = pd.Timestamp(date_str)
    ctx.current_date = ts
    ctx.current_time = ts
    ctx.bar_count = 100
    return ctx


# ---------------------------------------------------------------------------
# 1) Config 测试
# ---------------------------------------------------------------------------

class TestMLFactorConfig:
    def test_default_values(self):
        cfg = MLFactorConfig()
        assert cfg.top_k == 30  # 继承自 EquityRotationConfig
        assert cfg.model_objective == "regression"
        assert cfg.train_years == 3
        assert cfg.forward_days == 5

    def test_custom_values(self):
        cfg = MLFactorConfig(
            top_k=50, model_objective="lambdarank",
            train_years=2, rebalance_days=5,
        )
        assert cfg.top_k == 50
        assert cfg.model_objective == "lambdarank"
        assert cfg.rebalance_days == 5

    def test_inherits_rotation_config(self):
        cfg = MLFactorConfig(min_price=5.0, stop_loss_pct=0.10)
        assert cfg.min_price == 5.0
        assert cfg.stop_loss_pct == 0.10


# ---------------------------------------------------------------------------
# 2) 策略实例化
# ---------------------------------------------------------------------------

class TestStrategyInit:
    def test_create_with_config(self):
        cfg = MLFactorConfig(top_k=50)
        strat = MLFactorStrategy(config=cfg)
        assert strat.config.top_k == 50
        assert strat.name == "ml_factor"
        assert not strat._model_ready

    def test_create_with_kwargs(self):
        strat = MLFactorStrategy(
            top_k=40, rebalance_days=10, model_objective="lambdarank",
        )
        assert strat.config.top_k == 40
        assert strat.config.model_objective == "lambdarank"

    def test_unknown_kwargs_ignored(self):
        strat = MLFactorStrategy(
            top_k=50, unknown_param=True,
        )
        assert strat.config.top_k == 50


# ---------------------------------------------------------------------------
# 3) _linear_fallback 测试
# ---------------------------------------------------------------------------

class TestLinearFallback:
    def test_returns_ranked_list(self):
        strat = MLFactorStrategy(config=MLFactorConfig())
        candidates = _make_candidates(20)
        result = strat._linear_fallback(candidates)
        assert isinstance(result, list)
        assert len(result) == 20
        assert all(isinstance(s, str) for s in result)

    def test_empty_candidates(self):
        strat = MLFactorStrategy(config=MLFactorConfig())
        result = strat._linear_fallback([])
        assert result == []

    def test_single_candidate(self):
        strat = MLFactorStrategy(config=MLFactorConfig())
        candidates = _make_candidates(1)
        result = strat._linear_fallback(candidates)
        assert len(result) == 1

    def test_nan_metadata_handled(self):
        """metadata 含 NaN 不报错。"""
        strat = MLFactorStrategy(config=MLFactorConfig())
        c = EquityCandidateInfo(
            symbol="SH600000", close=10.0,
            metadata={"pe_ttm": None, "pb": float("nan"), "roe": 0.1},
        )
        result = strat._linear_fallback([c])
        assert len(result) == 1

    def test_weights_affect_ranking(self):
        """高 ROE 权重应使高 ROE 标的排名靠前。"""
        strat = MLFactorStrategy(config=MLFactorConfig(
            fallback_weights={"roe": 1.0},
        ))
        c1 = EquityCandidateInfo(
            symbol="A", close=10, metadata={"roe": 0.30},
        )
        c2 = EquityCandidateInfo(
            symbol="B", close=10, metadata={"roe": 0.05},
        )
        result = strat._linear_fallback([c1, c2])
        assert result[0] == "A"


# ---------------------------------------------------------------------------
# 4) _score_candidates 测试
# ---------------------------------------------------------------------------

class TestScoreCandidates:
    def test_fallback_when_no_model(self):
        """无模型时应回退到线性打分。"""
        strat = MLFactorStrategy(config=MLFactorConfig())
        candidates = _make_candidates(10)
        ctx = _make_context()
        result = strat._score_candidates(candidates, ctx)
        assert isinstance(result, list)
        assert len(result) == 10

    def test_empty_candidates(self):
        strat = MLFactorStrategy(config=MLFactorConfig())
        ctx = _make_context()
        result = strat._score_candidates([], ctx)
        assert result == []

    def test_returns_symbols(self):
        strat = MLFactorStrategy(config=MLFactorConfig())
        candidates = _make_candidates(15)
        ctx = _make_context()
        result = strat._score_candidates(candidates, ctx)
        syms = {c.symbol for c in candidates}
        assert set(result) == syms


# ---------------------------------------------------------------------------
# 5) _ml_score 测试
# ---------------------------------------------------------------------------

class TestMLScore:
    def test_ml_score_with_alpha158_meta(self):
        """带 alpha158 特征的 metadata 能正确提取。"""
        strat = MLFactorStrategy(config=MLFactorConfig())
        # 模拟已训练模型
        mock_selector = MagicMock()
        mock_selector.predict.return_value = pd.Series(
            [0.8, 0.3], index=["A", "B"], name="pred_score",
        )
        strat._selector = mock_selector
        strat._model_ready = True

        c1 = EquityCandidateInfo(
            symbol="A", close=10,
            metadata={"roc_5": 0.02, "ma_10": 0.01, "pe_ttm": 12},
        )
        c2 = EquityCandidateInfo(
            symbol="B", close=15,
            metadata={"roc_5": -0.01, "ma_10": -0.02, "pe_ttm": 25},
        )
        ctx = _make_context()
        result = strat._score_candidates([c1, c2], ctx)
        assert result[0] == "A"  # 高分排前
        assert result[1] == "B"

    def test_ml_score_fallback_on_error(self):
        """ML 预测异常时回退到线性打分。"""
        strat = MLFactorStrategy(config=MLFactorConfig())
        mock_selector = MagicMock()
        mock_selector.predict.side_effect = RuntimeError("boom")
        strat._selector = mock_selector
        strat._model_ready = True

        candidates = _make_candidates(5)
        ctx = _make_context()
        result = strat._score_candidates(candidates, ctx)
        assert len(result) == 5  # 应正常回退


# ---------------------------------------------------------------------------
# 6) 注册表测试
# ---------------------------------------------------------------------------

class TestRegistry:
    def test_ml_factor_registered(self):
        from aipha.strategy.registry import create_strategy
        strat = create_strategy("ml_factor", top_k=25)
        assert strat.name == "ml_factor"
        assert strat.config.top_k == 25


# ---------------------------------------------------------------------------
# 7) 多模型 OOS 集成测试
# ---------------------------------------------------------------------------

def _make_multi_oos_scores(
    symbols: list[str],
    dates: list[str],
    n_models: int = 3,
    seed: int = 99,
) -> dict[str, dict[str, dict[str, float]]]:
    """构建多模型 OOS 分数字典。"""
    rng = np.random.RandomState(seed)
    result: dict[str, dict[str, dict[str, float]]] = {}
    for m in range(n_models):
        mname = f"model_{m}"
        model_scores: dict[str, dict[str, float]] = {}
        for d in dates:
            day: dict[str, float] = {}
            for s in symbols:
                day[s] = float(rng.randn())
            model_scores[d] = day
        result[mname] = model_scores
    return result


class TestEnsembleConfig:
    def test_default_ensemble_fields(self):
        cfg = MLFactorConfig()
        assert cfg.oos_predictions_paths == []
        assert cfg.ensemble_method == "rank_average"
        assert cfg.ensemble_weights == {}

    def test_custom_ensemble_fields(self):
        cfg = MLFactorConfig(
            oos_predictions_paths=["/a.parquet", "/b.parquet"],
            ensemble_method="weighted_average",
            ensemble_weights={"m1": 0.6, "m2": 0.4},
        )
        assert len(cfg.oos_predictions_paths) == 2
        assert cfg.ensemble_method == "weighted_average"


class TestEnsembleOOSScore:
    """多模型 OOS 集成打分测试。"""

    def _make_strat_with_multi_oos(
        self, symbols, dates, n_models=3, weights=None,
    ):
        cfg = MLFactorConfig(
            ensemble_weights=weights or {},
        )
        strat = MLFactorStrategy(config=cfg)
        strat._multi_oos_scores = _make_multi_oos_scores(
            symbols, dates, n_models,
        )
        return strat

    def test_ensemble_returns_all_candidates(self):
        syms = ["A", "B", "C", "D", "E"]
        dates = ["2024-06-01"]
        strat = self._make_strat_with_multi_oos(syms, dates)
        cands = [EquityCandidateInfo(symbol=s, close=10, metadata={}) for s in syms]
        ctx = _make_context()
        result = strat._score_candidates(cands, ctx)
        assert set(result) == set(syms)
        assert len(result) == 5

    def test_ensemble_prefers_multi_over_single(self):
        """有多模型分数时应优先使用集成，而非单模型。"""
        strat = MLFactorStrategy(config=MLFactorConfig())
        syms = ["A", "B"]
        # 设置单模型 OOS（给 A 最高分）
        strat._oos_scores = {"2024-06-01": {"A": 10.0, "B": 1.0}}
        # 设置多模型 OOS（给 B 最高分）
        strat._multi_oos_scores = {
            "m1": {"2024-06-01": {"A": -1.0, "B": 5.0}},
            "m2": {"2024-06-01": {"A": -2.0, "B": 6.0}},
        }
        cands = [EquityCandidateInfo(symbol=s, close=10, metadata={}) for s in syms]
        ctx = _make_context()
        result = strat._score_candidates(cands, ctx)
        # 多模型集成中 B 排名更高
        assert result[0] == "B"

    def test_ensemble_fallback_when_no_date(self):
        """当日无多模型分数时回退到单模型。"""
        strat = MLFactorStrategy(config=MLFactorConfig())
        strat._multi_oos_scores = {
            "m1": {"2024-07-01": {"A": 1.0}},  # 不同日期
        }
        strat._oos_scores = {"2024-06-01": {"A": 5.0, "B": 1.0}}
        cands = [
            EquityCandidateInfo(symbol="A", close=10, metadata={}),
            EquityCandidateInfo(symbol="B", close=10, metadata={}),
        ]
        ctx = _make_context()
        result = strat._score_candidates(cands, ctx)
        assert result[0] == "A"  # 回退到单模型

    def test_ensemble_fallback_to_linear(self):
        """多模型为空 + 无单模型时回退到线性。"""
        strat = MLFactorStrategy(config=MLFactorConfig())
        strat._multi_oos_scores = {"m1": {}}  # 空
        cands = _make_candidates(5)
        ctx = _make_context()
        result = strat._score_candidates(cands, ctx)
        assert len(result) == 5

    def test_weighted_ensemble(self):
        """带权重的集成：高权重模型应更有影响力。"""
        strat = MLFactorStrategy(config=MLFactorConfig(
            ensemble_weights={"m_high": 10.0, "m_low": 0.001},
        ))
        # m_high 认为 A 好，m_low 认为 B 好
        strat._multi_oos_scores = {
            "m_high": {"2024-06-01": {"A": 10.0, "B": 1.0}},
            "m_low": {"2024-06-01": {"A": 1.0, "B": 10.0}},
        }
        cands = [
            EquityCandidateInfo(symbol="A", close=10, metadata={}),
            EquityCandidateInfo(symbol="B", close=10, metadata={}),
        ]
        ctx = _make_context()
        result = strat._score_candidates(cands, ctx)
        assert result[0] == "A"  # 高权重模型决定

    def test_partial_coverage(self):
        """模型只覆盖部分标的时不报错。"""
        strat = MLFactorStrategy(config=MLFactorConfig())
        strat._multi_oos_scores = {
            "m1": {"2024-06-01": {"A": 5.0, "B": 1.0}},
            "m2": {"2024-06-01": {"A": 3.0}},  # 不含 B
        }
        cands = [
            EquityCandidateInfo(symbol="A", close=10, metadata={}),
            EquityCandidateInfo(symbol="B", close=10, metadata={}),
        ]
        ctx = _make_context()
        result = strat._score_candidates(cands, ctx)
        assert "A" in result
        assert "B" in result
