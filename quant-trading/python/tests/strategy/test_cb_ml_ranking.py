"""Tests for CBMLRankingStrategy — ML 排名策略。

测试覆盖（≥10 用例）：
- Config: 默认值、fallback 模式
- Strategy: 注册表、空候选、fallback 打分、premium 过滤
- Features: 特征构建、rating 转换
"""

from __future__ import annotations

from datetime import datetime

import pytest

from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.cb_ml_ranking import (
    CBMLRankingStrategy,
    CBMLRankingConfig,
)


def _make_candidate(
    code: str, close: float, cb_over_rate: float = 10.0,
    cb_value: float = 95.0, amount: float = 200.0,
    issue_size: float = 5000.0, maturity: str = "20270615",
    rating: str = "AA",
) -> CandidateInfo:
    return CandidateInfo(
        symbol=code, close=close, metadata={},
        amount=amount, cb_over_rate=cb_over_rate,
        cb_value=cb_value, maturity_date=maturity,
        issue_size=issue_size, stk_short_name="正股",
        rating=rating,
    )


def _ctx() -> StrategyContext:
    return StrategyContext(
        current_time=datetime(2024, 6, 15, 15, 0),
        positions={}, cash=500_000.0, nav=500_000.0,
    )


class TestMLRankingConfig:
    def test_defaults(self) -> None:
        cfg = CBMLRankingConfig()
        assert cfg.top_k == 20
        assert cfg.model_path == ""
        assert cfg.use_fallback is True

    def test_fallback_flag(self) -> None:
        cfg = CBMLRankingConfig(use_fallback=False, model_path="/nonexistent")
        assert cfg.use_fallback is False


class TestMLRankingStrategy:
    @pytest.fixture
    def strategy(self):
        cfg = CBMLRankingConfig(
            top_k=2, rebalance_days=1,
            max_premium=40.0, lot_size=10,
            use_fallback=True,
        )
        s = CBMLRankingStrategy(cfg)
        s.on_start()
        return s

    def test_registry(self) -> None:
        registry = StrategyRegistry()
        assert registry.get("cb_ml_ranking") is not None

    def test_fallback_mode(self, strategy) -> None:
        """无模型路径 → fallback 模式。"""
        assert strategy._fallback_mode is True

    def test_empty_candidates(self, strategy) -> None:
        result = strategy._score_candidates([], _ctx())
        assert result == []

    def test_fallback_score(self, strategy) -> None:
        """Fallback 模式下使用因子打分。"""
        candidates = [
            _make_candidate("128001.SZ", 90.0, cb_over_rate=5.0),
            _make_candidate("128002.SZ", 130.0, cb_over_rate=25.0),
            _make_candidate("128003.SZ", 100.0, cb_over_rate=10.0),
        ]
        result = strategy._score_candidates(candidates, _ctx())
        assert len(result) == 2  # top_k=2
        assert result[0] == "128001.SZ"  # 低双低排前

    def test_premium_filter(self, strategy) -> None:
        c = _make_candidate("128001.SZ", 105.0, cb_over_rate=50.0)
        assert not strategy._extra_filter(c, _ctx())

    def test_build_features(self, strategy) -> None:
        candidates = [_make_candidate("128001.SZ", 100.0)]
        features = strategy._build_features(candidates, _ctx())
        assert len(features) == 1
        f = features[0]
        assert "close" in f
        assert "iv_bias" in f
        assert "gamma_convexity" in f

    def test_rating_to_score(self) -> None:
        assert CBMLRankingStrategy._rating_to_score("AAA") == 6.0
        assert CBMLRankingStrategy._rating_to_score("AA") == 4.0
        assert CBMLRankingStrategy._rating_to_score("X") == 3.0  # 默认

    def test_top_k_fallback(self, strategy) -> None:
        """5 个候选, top_k=2 → 取 2。"""
        candidates = [
            _make_candidate(f"128{i:03d}.SZ", 90.0 + i * 10, cb_over_rate=5.0 + i * 5)
            for i in range(5)
        ]
        result = strategy._score_candidates(candidates, _ctx())
        assert len(result) == 2
