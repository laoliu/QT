"""市场状态门控测试 — 置信度压低 / 动作降级 / 追高收紧。

同一套因子在熊市里的命中率显著低于牛市，静态权重对牛熊一视同仁，
是「买了就跌」的系统性来源之一。本文件覆盖 generate(regime=...)
的三重介入，以及不传 regime 时保持原有行为。
"""

from __future__ import annotations

import pytest

from aipha.advisory.models import ActionType, ScreenerResult, TimeHorizon
from aipha.advisory.recommendation_engine import (
    ACTION_LADDER,
    RecommendationConfig,
    RecommendationEngine,
    RegimeGateConfig,
    normalize_regime,
)


def _screener_results(score: float = 90.0) -> dict[str, list[ScreenerResult]]:
    """构造两个信号源（满足 min_source_count=2）的筛选结果。"""
    return {
        "multifactor": [ScreenerResult(ts_code="600000.SH", name="测试股", score=score)],
        "technical": [ScreenerResult(ts_code="600000.SH", name="测试股", score=score)],
    }


def _generate(score: float = 90.0, regime=None, **cfg):
    engine = RecommendationEngine(RecommendationConfig(
        horizons=[TimeHorizon.SHORT], **cfg,
    ))
    return engine.generate(
        _screener_results(score),
        current_prices={"600000.SH": 10.0},
        regime=regime,
    )[TimeHorizon.SHORT]


class TestNormalizeRegime:
    """regime 归一 — advisory 不依赖 portfolio 层"""

    def test_none_stays_none(self) -> None:
        assert normalize_regime(None) is None

    @pytest.mark.parametrize("raw", ["BEAR", "bear", " Bear "])
    def test_string_normalized_to_upper(self, raw: str) -> None:
        assert normalize_regime(raw) == "BEAR"

    def test_empty_string_treated_as_missing(self) -> None:
        assert normalize_regime("   ") is None

    def test_portfolio_market_regime_enum(self) -> None:
        """兼容 portfolio.regime_weight.MarketRegime（值为大写）"""
        from aipha.portfolio.regime_weight import MarketRegime

        assert normalize_regime(MarketRegime.BEAR) == "BEAR"

    def test_lowercase_valued_enum(self) -> None:
        """兼容 strategy.cb_regime_rotation.MarketRegime（值为小写）"""
        from aipha.strategy.cb_regime_rotation import MarketRegime as CbRegime

        assert normalize_regime(CbRegime.BEAR) == "BEAR"


class TestConfidenceMultiplier:
    """置信度按状态压低"""

    def test_bull_leaves_confidence_intact(self) -> None:
        assert _generate(regime="BULL")[0].confidence == pytest.approx(0.9)

    def test_neutral_shaves_confidence(self) -> None:
        assert _generate(regime="NEUTRAL")[0].confidence == pytest.approx(0.81)

    def test_bear_cuts_confidence_hardest(self) -> None:
        assert _generate(regime="BEAR")[0].confidence == pytest.approx(0.63)

    def test_raw_confidence_recorded(self) -> None:
        """压低前的原始置信度留在 metadata 里，便于事后归因"""
        rec = _generate(regime="BEAR")[0]

        assert rec.metadata["regime"] == "BEAR"
        assert rec.metadata["raw_confidence"] == pytest.approx(0.9)

    def test_gate_applied_before_min_confidence_filter(self) -> None:
        """压低后跌破 min_confidence 的信号被过滤掉，而非带着低分留下

        score=50 → 原始置信度 0.5 > 0.4 可通过；BEAR 下 0.35 < 0.4 出局。
        """
        assert len(_generate(score=50.0, regime=None)) == 1
        assert _generate(score=50.0, regime="BEAR") == []

    def test_unknown_regime_is_neutral_to_confidence(self) -> None:
        """未配置的状态字符串按乘数 1.0 处理，不静默丢弃推荐"""
        rec = _generate(regime="SIDEWAYS")[0]

        assert rec.confidence == pytest.approx(0.9)
        assert rec.metadata["regime"] == "SIDEWAYS"


class TestActionDowngrade:
    """动作强度按状态下调"""

    def test_bear_downgrades_strong_buy_to_buy(self) -> None:
        assert _generate(score=90.0, regime="BEAR")[0].action == ActionType.BUY

    def test_bear_downgrades_buy_to_hold(self) -> None:
        """边缘买入信号在熊市里不再是买入"""
        assert _generate(score=65.0, regime="BEAR")[0].action == ActionType.HOLD

    def test_bull_does_not_downgrade(self) -> None:
        assert _generate(score=90.0, regime="BULL")[0].action == ActionType.STRONG_BUY

    def test_downgrade_stops_at_hold(self) -> None:
        """门控只减仓不反手做空 —— 最多降到 HOLD"""
        recs = _generate(score=90.0, regime="BEAR", regime_gate=RegimeGateConfig(
            downgrade_steps={"BEAR": 99},
            confidence_multiplier={"BEAR": 1.0},
        ))

        assert recs[0].action == ActionType.HOLD

    def test_never_upgrades_on_negative_steps(self) -> None:
        """负档数不被当成升级"""
        recs = _generate(score=65.0, regime="BULL", regime_gate=RegimeGateConfig(
            downgrade_steps={"BULL": -2},
            confidence_multiplier={"BULL": 1.0},
        ))

        assert recs[0].action == ActionType.BUY

    def test_action_ladder_ordered_strong_to_weak(self) -> None:
        assert ACTION_LADDER[0] == ActionType.STRONG_BUY
        assert ACTION_LADDER[-1] == ActionType.SELL
        assert ACTION_LADDER.index(ActionType.HOLD) == 2


class TestChaseCapTightening:
    """追高上限按状态收紧"""

    def test_bear_halves_chase_tolerance(self) -> None:
        """默认 3% 追高容忍在 BEAR 收紧到 1.5%"""
        rec = _generate(regime="BEAR", max_chase_pct=0.03)[0]

        assert rec.max_entry_price == pytest.approx(10.15)

    def test_bull_keeps_full_chase_tolerance(self) -> None:
        assert _generate(regime="BULL", max_chase_pct=0.03)[0].max_entry_price \
            == pytest.approx(10.3)

    def test_stop_loss_unaffected_by_regime(self) -> None:
        """止损由 horizon 决定，与大盘状态无关"""
        bull = _generate(regime="BULL")[0]
        bear = _generate(regime="BEAR")[0]

        assert bull.stop_loss == bear.stop_loss == pytest.approx(9.5)
        assert bull.target_price == bear.target_price

    def test_no_signal_price_still_no_plan(self) -> None:
        """无信号价时门控也不臆造价位"""
        engine = RecommendationEngine(RecommendationConfig(horizons=[TimeHorizon.SHORT]))
        rec = engine.generate(_screener_results(), regime="BEAR")[TimeHorizon.SHORT][0]

        assert rec.max_entry_price is None
        assert rec.stop_loss is None


class TestGateDisabled:
    """门控关闭 / 不传状态时保持原有行为"""

    def test_no_regime_leaves_metadata_empty(self) -> None:
        rec = _generate()[0]

        assert rec.metadata == {}
        assert rec.confidence == pytest.approx(0.9)
        assert rec.action == ActionType.STRONG_BUY

    def test_disabled_gate_ignores_regime(self) -> None:
        recs = _generate(regime="BEAR", regime_gate=RegimeGateConfig(enabled=False))

        assert recs[0].action == ActionType.STRONG_BUY
        assert recs[0].confidence == pytest.approx(0.9)
        assert recs[0].max_entry_price == pytest.approx(10.3)
        assert recs[0].metadata == {}


class TestGateWithRewardRiskFloor:
    """门控与盈亏比下限叠加"""

    def test_reward_risk_metadata_merges_with_regime(self) -> None:
        """两道降级同时触发时 metadata 不互相覆盖"""
        recs = _generate(
            regime="BEAR", reward_risk_ratio=1.0, min_reward_risk=2.0,
        )

        rec = recs[0]
        assert rec.action == ActionType.HOLD
        assert rec.metadata["regime"] == "BEAR"
        assert rec.metadata["raw_confidence"] == pytest.approx(0.9)
        # STRONG_BUY 先被 BEAR 降为 BUY，再被盈亏比降为 HOLD
        assert rec.metadata["original_action"] == "buy"
        assert "盈亏比" in rec.metadata["entry_blocked"]
