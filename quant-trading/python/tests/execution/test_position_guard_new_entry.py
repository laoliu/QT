"""PositionGuard 新股建仓测试 — action 门控与价位计划拦截。

覆盖 _check_new_entries 对 advisory 推荐自带价位计划的消费:
action 非买入 / 追高 / 开仓前破位 三类拦截。
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from aipha.advisory.models import ActionType as AdvisoryAction
from aipha.advisory.models import Recommendation
from aipha.execution.position_guard import ActionType, GuardConfig, PositionGuard


@pytest.fixture
def guard() -> PositionGuard:
    return PositionGuard(
        GuardConfig(
            new_entry_enabled=True,
            min_confidence=0.6,
            max_new_entries_per_day=3,
            max_single_position_pct=0.10,
        ),
        nav=1_000_000,
    )


def _rec(**kw) -> dict:
    base = {
        "ts_code": "600000.SH",
        "action": "buy",
        "confidence": 0.8,
        "price": 10.0,
    }
    base.update(kw)
    return base


def _entries(guard: PositionGuard, recs: list[dict]) -> list:
    actions = guard.check_all([], bar_index=0, recommendations=recs)
    return [a for a in actions if a.action_type == ActionType.NEW_ENTRY]


class TestNewEntryBaseline:
    """无价位计划时的原有行为不变"""

    def test_creates_entry(self, guard: PositionGuard) -> None:
        entries = _entries(guard, [_rec()])

        assert len(entries) == 1
        assert entries[0].symbol == "600000.SH"
        assert entries[0].side == "BUY"

    def test_no_price_plan_still_enters(self, guard: PositionGuard) -> None:
        """推荐不带 max_entry_price/stop_loss 时照旧建仓（向后兼容）"""
        assert len(_entries(guard, [_rec(action=None)])) == 1

    def test_low_confidence_skipped(self, guard: PositionGuard) -> None:
        assert _entries(guard, [_rec(confidence=0.3)]) == []


class TestActionGate:
    """action 门控 — revalidate() 的降级必须被尊重"""

    @pytest.mark.parametrize("action", ["hold", "reduce", "sell"])
    def test_non_buy_actions_skipped(self, guard: PositionGuard, action: str) -> None:
        assert _entries(guard, [_rec(action=action)]) == []

    @pytest.mark.parametrize("action", ["buy", "strong_buy", "STRONG_BUY"])
    def test_buy_actions_accepted(self, guard: PositionGuard, action: str) -> None:
        assert len(_entries(guard, [_rec(action=action)])) == 1


class TestPricePlanBlocks:
    """价位计划拦截"""

    def test_chase_blocked(self, guard: PositionGuard) -> None:
        """现价已高于入场上限 → 不建仓"""
        rec = _rec(price=10.9, signal_price=10.0, max_entry_price=10.3)
        assert _entries(guard, [rec]) == []

    def test_at_limit_allowed(self, guard: PositionGuard) -> None:
        """恰好等于上限仍可建仓"""
        rec = _rec(price=10.3, signal_price=10.0, max_entry_price=10.3)
        assert len(_entries(guard, [rec])) == 1

    def test_broken_setup_blocked(self, guard: PositionGuard) -> None:
        """还没买就已跌破止损 → 不建仓"""
        rec = _rec(price=9.4, signal_price=10.0, max_entry_price=10.3, stop_loss=9.5)
        assert _entries(guard, [rec]) == []

    def test_within_plan_allowed(self, guard: PositionGuard) -> None:
        rec = _rec(price=10.1, signal_price=10.0, max_entry_price=10.3, stop_loss=9.5)
        assert len(_entries(guard, [rec])) == 1

    def test_blocked_entry_does_not_consume_daily_quota(
        self, guard: PositionGuard,
    ) -> None:
        """被拦截的建仓不占用当日额度"""
        blocked = _rec(ts_code="600001.SH", price=10.9,
                       signal_price=10.0, max_entry_price=10.3)
        ok = _rec(ts_code="600002.SH", price=10.1,
                  signal_price=10.0, max_entry_price=10.3)

        entries = _entries(guard, [blocked, ok])

        assert [e.symbol for e in entries] == ["600002.SH"]
        assert guard._new_entries_today == 1


class TestPlanPropagation:
    """价位计划透传到 OrderAction"""

    def test_metadata_carries_plan(self, guard: PositionGuard) -> None:
        rec = _rec(stop_loss=9.5, target_price=11.0, holding_days=5)

        meta = _entries(guard, [rec])[0].metadata

        assert meta["stop_loss"] == 9.5
        assert meta["target_price"] == 11.0
        assert meta["holding_days"] == 5


class TestRecommendationToDictInterop:
    """Recommendation.to_dict() → PositionGuard 端到端"""

    def test_live_price_triggers_chase_block(self, guard: PositionGuard) -> None:
        """to_dict(live_price=...) 让追高拦截真正生效"""
        rec = Recommendation(
            ts_code="600000.SH",
            action=AdvisoryAction.STRONG_BUY,
            confidence=0.9,
            signal_price=10.0,
            max_entry_price=10.3,
            stop_loss=9.5,
            holding_days=5,
        )

        assert _entries(guard, [rec.to_dict(live_price=10.9)]) == []
        assert len(_entries(guard, [rec.to_dict(live_price=10.1)])) == 1

    def test_downgraded_recommendation_not_bought(self, guard: PositionGuard) -> None:
        """被 revalidate 降级为 HOLD 的推荐不会建仓"""
        rec = Recommendation(
            ts_code="600000.SH",
            action=AdvisoryAction.HOLD,
            confidence=0.9,
            signal_price=10.0,
        )

        assert _entries(guard, [rec.to_dict()]) == []


class TestEntryPriceBlockHelper:
    """_entry_price_block 直接单测"""

    def test_returns_none_without_plan(self) -> None:
        assert PositionGuard._entry_price_block("600000.SH", Decimal("10"), {}) is None

    def test_ignores_zero_limits(self) -> None:
        """上限/止损为 0 视为未设置，不拦截"""
        rec = {"max_entry_price": 0, "stop_loss": 0}
        assert PositionGuard._entry_price_block(
            "600000.SH", Decimal("10"), rec,
        ) is None

    def test_reason_mentions_chase(self) -> None:
        reason = PositionGuard._entry_price_block(
            "600000.SH", Decimal("10.9"), {"max_entry_price": 10.3},
        )
        assert reason is not None and "追高" in reason
