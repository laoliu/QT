"""signal_sources 类型一致性测试。

``Recommendation.signal_sources`` 声明为 ``dict[str, float]``：既要知道
哪些源给出了信号，也要知道各给了多少分。只保留键名会让事后归因
（是哪一路信号带偏了推荐）无从下手。
"""

from __future__ import annotations

import pytest

from aipha.advisory.aggregator import SignalAggregator
from aipha.advisory.models import ScreenerResult, TimeHorizon
from aipha.advisory.recommendation_engine import (
    RecommendationConfig,
    RecommendationEngine,
)


@pytest.fixture
def screener_results() -> dict[str, list[ScreenerResult]]:
    """两路筛选器命中同一只股票。

    注意键名会被 ``SignalAggregator`` 归一到信号源桶: 非技术类筛选器
    （这里的 multifactor）归入 ``quant``，``technical`` 保持原名。
    """
    return {
        "multifactor": [ScreenerResult(ts_code="600000.SH", name="测试股", score=88.0)],
        "technical": [ScreenerResult(ts_code="600000.SH", name="测试股", score=72.0)],
    }


class TestAggregatorToRecommendations:
    """SignalAggregator.to_recommendations"""

    def _rec(self, screener_results):
        agg = SignalAggregator()
        signals = agg.aggregate(screener_results)
        return agg.to_recommendations(signals)[0]

    def test_signal_sources_is_dict(self, screener_results) -> None:
        assert isinstance(self._rec(screener_results).signal_sources, dict)

    def test_scores_preserved(self, screener_results) -> None:
        """分数必须保留，不能只剩键名"""
        sources = self._rec(screener_results).signal_sources

        assert sources == {
            "quant": pytest.approx(88.0),
            "technical": pytest.approx(72.0),
        }

    def test_values_are_numeric(self, screener_results) -> None:
        sources = self._rec(screener_results).signal_sources

        assert all(isinstance(v, (int, float)) for v in sources.values())

    def test_does_not_alias_signal(self, screener_results) -> None:
        """改推荐不应回写聚合信号，反之亦然"""
        agg = SignalAggregator()
        signals = agg.aggregate(screener_results)
        rec = agg.to_recommendations(signals)[0]

        rec.signal_sources["injected"] = 1.0

        assert "injected" not in signals[0].source_scores


class TestEngineSignalSources:
    """RecommendationEngine.generate"""

    def _rec(self, screener_results):
        engine = RecommendationEngine(RecommendationConfig(
            horizons=[TimeHorizon.SHORT],
        ))
        return engine.generate(screener_results)[TimeHorizon.SHORT][0]

    def test_scores_preserved(self, screener_results) -> None:
        sources = self._rec(screener_results).signal_sources

        assert sources == {
            "quant": pytest.approx(88.0),
            "technical": pytest.approx(72.0),
        }

    def test_does_not_alias_signal(self, screener_results) -> None:
        engine = RecommendationEngine(RecommendationConfig(
            horizons=[TimeHorizon.SHORT],
        ))
        recs = engine.generate(screener_results)[TimeHorizon.SHORT]

        recs[0].signal_sources["injected"] = 1.0

        # 同一批信号再生成一次，不应带上污染
        again = engine.generate(screener_results)[TimeHorizon.SHORT]
        assert "injected" not in again[0].signal_sources

    def test_both_paths_agree_on_shape(self, screener_results) -> None:
        """两条构造路径产出同样的键与值"""
        agg = SignalAggregator()
        from_agg = agg.to_recommendations(agg.aggregate(screener_results))[0]
        from_engine = self._rec(screener_results)

        assert from_agg.signal_sources == from_engine.signal_sources


class TestSerialization:
    """to_dict 输出"""

    def test_to_dict_carries_scores(self, screener_results) -> None:
        rec = RecommendationEngine(RecommendationConfig(
            horizons=[TimeHorizon.SHORT],
        )).generate(screener_results)[TimeHorizon.SHORT][0]

        assert rec.to_dict()["signal_sources"]["quant"] == pytest.approx(88.0)

    def test_to_dict_json_safe(self, screener_results) -> None:
        import json

        rec = RecommendationEngine(RecommendationConfig(
            horizons=[TimeHorizon.SHORT],
        )).generate(screener_results)[TimeHorizon.SHORT][0]

        json.dumps(rec.to_dict()["signal_sources"])
