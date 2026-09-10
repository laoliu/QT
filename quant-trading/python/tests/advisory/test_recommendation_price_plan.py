"""推荐价位计划测试 — 止损 / 入场上限 / 持有期 / 防追高。

覆盖 RecommendationEngine 生成的价位计划，以及 revalidate() 在
「推荐生成」与「实际下单」之间的防追高降级。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from aipha.advisory.models import ActionType, Recommendation, ScreenerResult, TimeHorizon
from aipha.advisory.recommendation_engine import (
    HOLDING_DAYS,
    STOP_LOSS_PCT,
    RecommendationConfig,
    RecommendationEngine,
)


def _screener_results(score: float = 90.0) -> dict[str, list[ScreenerResult]]:
    """构造两个信号源（满足 min_source_count=2）的筛选结果。"""
    return {
        "multifactor": [ScreenerResult(ts_code="600000.SH", name="测试股", score=score)],
        "technical": [ScreenerResult(ts_code="600000.SH", name="测试股", score=score)],
    }


def _engine(**cfg) -> RecommendationEngine:
    return RecommendationEngine(RecommendationConfig(
        horizons=[TimeHorizon.SHORT], **cfg,
    ))


class TestPricePlanGeneration:
    """价位计划生成"""

    def test_populates_full_price_plan(self) -> None:
        """有信号价时输出 信号价/入场上限/止损/目标价/持有期"""
        recs = _engine(max_chase_pct=0.03).generate(
            _screener_results(),
            current_prices={"600000.SH": 10.0},
        )[TimeHorizon.SHORT]

        assert len(recs) == 1
        rec = recs[0]
        assert rec.signal_price == 10.0
        assert rec.max_entry_price == pytest.approx(10.3)
        # SHORT 止损 5%
        assert rec.stop_loss == pytest.approx(10.0 * (1 - STOP_LOSS_PCT[TimeHorizon.SHORT]))
        # 默认盈亏比 2.0 → 目标价 = 10 + 0.5*2
        assert rec.target_price == pytest.approx(11.0)
        assert rec.holding_days == HOLDING_DAYS[TimeHorizon.SHORT]
        assert rec.reward_risk_ratio == pytest.approx(2.0)

    def test_external_target_price_preferred(self) -> None:
        """外部目标价高于信号价时优先采用"""
        recs = _engine().generate(
            _screener_results(),
            target_prices={"600000.SH": 15.0},
            current_prices={"600000.SH": 10.0},
        )[TimeHorizon.SHORT]

        assert recs[0].target_price == pytest.approx(15.0)

    def test_unusable_target_price_falls_back(self) -> None:
        """外部目标价不高于信号价（无上行空间）时改用盈亏比反推"""
        recs = _engine().generate(
            _screener_results(),
            target_prices={"600000.SH": 9.0},
            current_prices={"600000.SH": 10.0},
        )[TimeHorizon.SHORT]

        assert recs[0].target_price == pytest.approx(11.0)

    def test_no_price_yields_no_plan(self) -> None:
        """无信号价时不臆造价位计划"""
        recs = _engine().generate(_screener_results())[TimeHorizon.SHORT]

        rec = recs[0]
        assert rec.signal_price is None
        assert rec.max_entry_price is None
        assert rec.stop_loss is None
        assert rec.reward_risk_ratio is None

    @pytest.mark.parametrize("bad", [0, -1.0, None, "abc", float("nan")])
    def test_invalid_price_rejected(self, bad) -> None:
        """非正/NaN/非数值价格视为缺失，不产生价位计划"""
        recs = _engine().generate(
            _screener_results(), current_prices={"600000.SH": bad},
        )[TimeHorizon.SHORT]

        assert recs[0].signal_price is None
        assert recs[0].stop_loss is None

    def test_horizon_specific_stop_and_holding(self) -> None:
        """不同时间维度使用不同止损距离与持有期"""
        engine = RecommendationEngine(RecommendationConfig(
            horizons=[TimeHorizon.SHORT, TimeHorizon.MEDIUM],
        ))
        out = engine.generate(_screener_results(), current_prices={"600000.SH": 100.0})

        short, medium = out[TimeHorizon.SHORT][0], out[TimeHorizon.MEDIUM][0]
        assert short.stop_loss == pytest.approx(95.0)
        assert medium.stop_loss == pytest.approx(90.0)
        assert short.holding_days < medium.holding_days

    def test_reward_risk_floor_downgrades(self) -> None:
        """盈亏比低于下限的买入建议降级为 HOLD"""
        recs = _engine(reward_risk_ratio=1.0, min_reward_risk=2.0).generate(
            _screener_results(), current_prices={"600000.SH": 10.0},
        )[TimeHorizon.SHORT]

        rec = recs[0]
        assert rec.action == ActionType.HOLD
        assert "entry_blocked" in rec.metadata
        assert rec.metadata["original_action"] == "strong_buy"


class TestEntryBlockReason:
    """Recommendation.entry_block_reason — 下单前三重拦截"""

    def _rec(self, **kw) -> Recommendation:
        base = dict(
            ts_code="600000.SH",
            action=ActionType.BUY,
            signal_price=10.0,
            max_entry_price=10.3,
            stop_loss=9.5,
            expires_at=datetime.now() + timedelta(days=3),
        )
        base.update(kw)
        return Recommendation(**base)

    def test_allows_entry_within_plan(self) -> None:
        assert self._rec().is_entry_allowed(10.2)

    def test_allows_entry_at_limit(self) -> None:
        """恰好等于上限仍可买入（严格大于才拦）"""
        assert self._rec().is_entry_allowed(10.3)

    def test_blocks_chase(self) -> None:
        reason = self._rec().entry_block_reason(10.9)
        assert reason is not None
        assert "追高" in reason

    def test_blocks_broken_setup(self) -> None:
        """还没买就已跌破止损 → 形态失效"""
        reason = self._rec().entry_block_reason(9.4)
        assert reason is not None
        assert "破位" in reason or "跌破止损" in reason

    def test_blocks_expired_signal(self) -> None:
        rec = self._rec(expires_at=datetime.now() - timedelta(days=1))
        reason = rec.entry_block_reason(10.0)
        assert reason is not None
        assert "过期" in reason

    def test_blocks_non_buy_action(self) -> None:
        reason = self._rec(action=ActionType.HOLD).entry_block_reason(10.0)
        assert reason is not None
        assert "非买入建议" in reason

    def test_blocks_invalid_price(self) -> None:
        assert self._rec().entry_block_reason(0) is not None

    def test_no_plan_allows_entry(self) -> None:
        """无价位计划时不拦截（保持向后兼容）"""
        rec = Recommendation(
            ts_code="600000.SH", action=ActionType.BUY, expires_at=None,
        )
        assert rec.is_entry_allowed(10.0)


class TestRevalidate:
    """下单前用最新价复核推荐"""

    def _recs(self) -> list[Recommendation]:
        return _engine(max_chase_pct=0.03).generate(
            _screener_results(), current_prices={"600000.SH": 10.0},
        )[TimeHorizon.SHORT]

    def test_keeps_recommendation_when_price_stable(self) -> None:
        engine = _engine()
        out = engine.revalidate(self._recs(), {"600000.SH": 10.1})

        assert out[0].action == ActionType.STRONG_BUY
        assert "entry_blocked" not in out[0].metadata

    def test_downgrades_when_gapped_up(self) -> None:
        """次日跳空高开 8% → 降级为 HOLD 而非照买"""
        engine = _engine()
        out = engine.revalidate(self._recs(), {"600000.SH": 10.8})

        rec = out[0]
        assert rec.action == ActionType.HOLD
        assert "追高" in rec.metadata["entry_blocked"]
        assert rec.metadata["original_action"] == "strong_buy"
        assert rec.metadata["revalidated_price"] == pytest.approx(10.8)
        assert any("entry_blocked" in r for r in rec.reasons)

    def test_does_not_mutate_input(self) -> None:
        """原推荐对象保持不变"""
        recs = self._recs()
        _engine().revalidate(recs, {"600000.SH": 10.8})

        assert recs[0].action == ActionType.STRONG_BUY
        assert recs[0].metadata == {}

    def test_missing_live_price_passes_through(self) -> None:
        """拿不到最新价时不做判断，原样返回"""
        out = _engine().revalidate(self._recs(), {})

        assert out[0].action == ActionType.STRONG_BUY


class TestToDict:
    """序列化 — position_guard 消费的键名"""

    def test_price_defaults_to_signal_price(self) -> None:
        rec = Recommendation(ts_code="600000.SH", signal_price=10.0, stop_loss=9.5)
        d = rec.to_dict()

        assert d["price"] == 10.0
        assert d["signal_price"] == 10.0
        assert d["stop_loss"] == 9.5

    def test_live_price_overrides_entry_price(self) -> None:
        """传入最新价时 price 用最新价 —— 追高拦截才能生效"""
        rec = Recommendation(
            ts_code="600000.SH", signal_price=10.0, max_entry_price=10.3,
        )
        d = rec.to_dict(live_price=10.9)

        assert d["price"] == 10.9
        assert d["signal_price"] == 10.0
        assert d["max_entry_price"] == 10.3
