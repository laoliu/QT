"""Tests for DoubleLowPlusStrategy — 6因子增强双低可转债策略."""
from collections import deque
from datetime import datetime

import pytest

from aipha.domain.models import Bar
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.double_low_plus import (
    DoubleLowPlusConfig,
    DoubleLowPlusStrategy,
    _get_rating_score,
)


# ---------------------------------------------------------------------------
# Helper factories（与 test_cb_ultra.py 对齐）
# ---------------------------------------------------------------------------

def _make_bar(symbol: str, close: float, metadata: dict | None = None) -> Bar:
    dt = datetime(2024, 6, 15, 15, 0)
    return Bar(
        symbol=symbol,
        start_ts=datetime(2024, 6, 15, 9, 30),
        end_ts=dt,
        open=close, high=close * 1.02, low=close * 0.98,
        close=close, volume=1000, metadata=metadata,
    )


def _make_context(
    positions: dict[str, int] | None = None,
    cash: float = 500_000.0,
    current_time: datetime | None = None,
) -> StrategyContext:
    return StrategyContext(
        current_time=current_time or datetime(2024, 6, 15, 15, 0),
        positions=positions or {},
        cash=cash, nav=cash,
    )


def _make_candidate(
    symbol: str,
    close: float = 100.0,
    issue_size: float = 5000.0,
    cb_over_rate: float = 10.0,
    cb_value: float = 100.0,
    list_date: str = "20240101",
    amount: float = 100.0,
    rating: str = "AA",
) -> CandidateInfo:
    """构造 CandidateInfo，比 test_cb_ultra 多 rating 字段."""
    return CandidateInfo(
        symbol=symbol,
        close=close,
        metadata={
            "issue_size": issue_size,
            "cb_over_rate": cb_over_rate,
            "cb_value": cb_value,
            "list_date": list_date,
            "amount": amount,
            "rating": rating,
        },
        issue_size=issue_size,
        cb_over_rate=cb_over_rate,
        cb_value=cb_value,
        list_date=list_date,
        amount=amount,
        rating=rating,
    )


# ============================================================
# TestGetRatingScore — 信用评级数值映射
# ============================================================

class TestGetRatingScore:
    """测试 _get_rating_score 辅助函数."""

    def test_aaa_returns_100(self):
        assert _get_rating_score("AAA") == 100

    def test_aa_returns_80(self):
        assert _get_rating_score("AA") == 80

    def test_aa_minus_returns_70(self):
        assert _get_rating_score("AA-") == 70

    def test_empty_string_returns_negative(self):
        assert _get_rating_score("") == -1

    def test_unknown_rating_returns_negative(self):
        assert _get_rating_score("XYZ") == -1

    def test_case_insensitive(self):
        assert _get_rating_score("aaa") == 100
        assert _get_rating_score("Aa+") == 90

    def test_whitespace_stripped(self):
        assert _get_rating_score("  AA  ") == 80


# ============================================================
# TestDoubleLowPlusConfig — 配置默认值
# ============================================================

class TestDoubleLowPlusConfig:
    """测试配置 dataclass 默认值与覆盖."""

    def test_default_top_k(self):
        cfg = DoubleLowPlusConfig()
        assert cfg.top_k == 10

    def test_default_weights_sum_to_one(self):
        cfg = DoubleLowPlusConfig()
        total = (
            cfg.weight_price + cfg.weight_premium + cfg.weight_size
            + cfg.weight_age + cfg.weight_mean_reversion + cfg.weight_rating
        )
        assert abs(total - 1.0) < 1e-9

    def test_default_lookback(self):
        cfg = DoubleLowPlusConfig()
        assert cfg.lookback == 20
        assert cfg.bb_k == 2.0

    def test_override_weight(self):
        cfg = DoubleLowPlusConfig(weight_price=0.50, weight_premium=0.0)
        assert cfg.weight_price == 0.50
        assert cfg.weight_premium == 0.0

    def test_age_filter_defaults(self):
        cfg = DoubleLowPlusConfig()
        assert cfg.use_age_filter is True
        assert cfg.min_age_days == 3
        assert cfg.max_age_days == 180
        assert cfg.fallback_max_age_days == 730

    def test_max_premium_default(self):
        cfg = DoubleLowPlusConfig()
        assert cfg.max_premium == 70.0


# ============================================================
# TestRegistration — 策略注册表
# ============================================================

class TestRegistration:
    """测试策略注册与实例化."""

    def test_registered_name(self):
        s = DoubleLowPlusStrategy()
        assert s.name == "double_low_plus"

    def test_registry_roundtrip(self):
        cls = StrategyRegistry().get("double_low_plus")
        assert cls is DoubleLowPlusStrategy

    def test_instance_has_config(self):
        s = DoubleLowPlusStrategy()
        assert isinstance(s.config, DoubleLowPlusConfig)


# ============================================================
# TestExtraFilter — 溢价率/次新预过滤
# ============================================================

class TestExtraFilter:
    """测试 _extra_filter 逻辑."""

    def _make_strategy(self, **kwargs) -> DoubleLowPlusStrategy:
        cfg = DoubleLowPlusConfig(**kwargs)
        return DoubleLowPlusStrategy(config=cfg)

    def test_pass_normal_candidate(self):
        """溢价率/年龄正常 → 通过."""
        s = self._make_strategy()
        # list_date=20240101, current=20240615 → ~166天 < 180
        c = _make_candidate("CB001", close=100, cb_over_rate=20.0,
                            list_date="20240101")
        ctx = _make_context()
        assert s._extra_filter(c, ctx) is True

    def test_reject_high_premium(self):
        """溢价率 > max_premium → 拒绝."""
        s = self._make_strategy(max_premium=50.0)
        c = _make_candidate("CB002", cb_over_rate=55.0)
        ctx = _make_context()
        assert s._extra_filter(c, ctx) is False

    def test_reject_too_new(self):
        """上市不足 min_age_days → 拒绝."""
        s = self._make_strategy(min_age_days=3)
        # 上市日 = 当天前1天
        c = _make_candidate("CB003", list_date="20240614")
        ctx = _make_context(current_time=datetime(2024, 6, 15, 15, 0))
        assert s._extra_filter(c, ctx) is False

    def test_reject_too_old(self):
        """上市超过 max_age_days → 拒绝."""
        s = self._make_strategy(max_age_days=180)
        # 上市日 = 7个月前
        c = _make_candidate("CB004", list_date="20231101")
        ctx = _make_context(current_time=datetime(2024, 6, 15, 15, 0))
        assert s._extra_filter(c, ctx) is False

    def test_none_premium_passes(self):
        """溢价率 None → 不过滤（通过）."""
        s = self._make_strategy()
        c = _make_candidate("CB005", cb_over_rate=None, list_date="20240301")
        ctx = _make_context()
        assert s._extra_filter(c, ctx) is True


# ============================================================
# TestComputeBBDeviation — 布林带偏离度
# ============================================================

class TestComputeBBDeviation:
    """测试 _compute_bb_deviation 逻辑."""

    def _make_strategy_with_history(
        self, symbol: str, prices: list[float], lookback: int = 20,
    ) -> DoubleLowPlusStrategy:
        cfg = DoubleLowPlusConfig(lookback=lookback, bb_k=2.0)
        s = DoubleLowPlusStrategy(config=cfg)
        s._price_history[symbol] = deque(prices, maxlen=lookback)
        return s

    def test_insufficient_history_returns_none(self):
        """历史不足 lookback → None."""
        s = self._make_strategy_with_history("CB001", [100.0] * 5, lookback=20)
        assert s._compute_bb_deviation("CB001", 95.0) is None

    def test_sufficient_history_returns_float(self):
        """历史充足 → 返回 float."""
        prices = [100.0 + i * 0.5 for i in range(20)]
        s = self._make_strategy_with_history("CB001", prices, lookback=20)
        result = s._compute_bb_deviation("CB001", 90.0)
        assert isinstance(result, float)

    def test_oversold_positive_deviation(self):
        """价格低于下轨 → 偏离 > 0."""
        # 构造稳定价格 100，std ≈ 0; 改成有波动
        prices = [100.0 + (i % 2) * 2.0 for i in range(20)]
        s = self._make_strategy_with_history("CB001", prices, lookback=20)
        # 当前价远低于均值
        result = s._compute_bb_deviation("CB001", 80.0)
        assert result is not None
        assert result > 0  # 超卖

    def test_zero_std_returns_none(self):
        """所有价格相同 → std≈0 → None."""
        s = self._make_strategy_with_history("CB001", [100.0] * 20, lookback=20)
        assert s._compute_bb_deviation("CB001", 100.0) is None

    def test_unknown_symbol_returns_none(self):
        """未知品种 → None."""
        cfg = DoubleLowPlusConfig()
        s = DoubleLowPlusStrategy(config=cfg)
        assert s._compute_bb_deviation("UNKNOWN", 100.0) is None


# ============================================================
# TestScoreCandidates — 6因子评分排序
# ============================================================

class TestScoreCandidates:
    """测试 _score_candidates 评分与排序逻辑."""

    def _make_strategy(self, **kwargs) -> DoubleLowPlusStrategy:
        cfg = DoubleLowPlusConfig(**kwargs)
        return DoubleLowPlusStrategy(config=cfg)

    def test_empty_candidates(self):
        """空列表 → 返回空."""
        s = self._make_strategy()
        ctx = _make_context()
        assert s._score_candidates([], ctx) == []

    def test_single_candidate(self):
        """单个候选 → 直接返回."""
        s = self._make_strategy()
        c = _make_candidate("CB001", close=90.0)
        ctx = _make_context()
        result = s._score_candidates([c], ctx)
        assert result == ["CB001"]

    def test_lower_price_ranks_better(self):
        """价格因子权重放大：低价排前面."""
        s = self._make_strategy(
            weight_price=1.0, weight_premium=0.0, weight_size=0.0,
            weight_age=0.0, weight_mean_reversion=0.0, weight_rating=0.0,
        )
        c_low = _make_candidate("LOW", close=80.0, list_date="20240101")
        c_high = _make_candidate("HIGH", close=130.0, list_date="20240101")
        ctx = _make_context()
        result = s._score_candidates([c_high, c_low], ctx)
        assert result[0] == "LOW"

    def test_lower_premium_ranks_better(self):
        """溢价率因子权重放大：低溢价排前面."""
        s = self._make_strategy(
            weight_price=0.0, weight_premium=1.0, weight_size=0.0,
            weight_age=0.0, weight_mean_reversion=0.0, weight_rating=0.0,
        )
        c_low = _make_candidate("LOWP", cb_over_rate=5.0, list_date="20240101")
        c_high = _make_candidate("HIGHP", cb_over_rate=50.0, list_date="20240101")
        ctx = _make_context()
        result = s._score_candidates([c_high, c_low], ctx)
        assert result[0] == "LOWP"

    def test_smaller_size_ranks_better(self):
        """规模因子权重放大：小规模排前面."""
        s = self._make_strategy(
            weight_price=0.0, weight_premium=0.0, weight_size=1.0,
            weight_age=0.0, weight_mean_reversion=0.0, weight_rating=0.0,
        )
        c_small = _make_candidate("SMALL", issue_size=2000.0, list_date="20240101")
        c_big = _make_candidate("BIG", issue_size=20000.0, list_date="20240101")
        ctx = _make_context()
        result = s._score_candidates([c_big, c_small], ctx)
        assert result[0] == "SMALL"

    def test_higher_rating_ranks_better(self):
        """评级因子权重放大：高评级排前面."""
        s = self._make_strategy(
            weight_price=0.0, weight_premium=0.0, weight_size=0.0,
            weight_age=0.0, weight_mean_reversion=0.0, weight_rating=1.0,
        )
        c_aaa = _make_candidate("AAA_CB", rating="AAA", list_date="20240101")
        c_bb = _make_candidate("BB_CB", rating="BB", list_date="20240101")
        ctx = _make_context()
        result = s._score_candidates([c_bb, c_aaa], ctx)
        assert result[0] == "AAA_CB"

    def test_top_k_limits_output(self):
        """输出数量不超过 top_k."""
        s = self._make_strategy(top_k=3)
        candidates = [
            _make_candidate(f"CB{i:03d}", close=100.0 + i, list_date="20240101")
            for i in range(10)
        ]
        ctx = _make_context()
        result = s._score_candidates(candidates, ctx)
        assert len(result) <= 3


# ============================================================
# TestAdaptiveAgeDegradation — 候选不足时放宽次新约束
# ============================================================

class TestAdaptiveAgeDegradation:
    """测试自适应年龄降级/恢复机制."""

    def test_degrade_when_few_candidates(self):
        """候选不足时 _relax_filters_one_step 放宽 max_age."""
        cfg = DoubleLowPlusConfig(
            min_candidates=5, max_age_days=180, fallback_max_age_days=730,
        )
        s = DoubleLowPlusStrategy(config=cfg)
        assert s._effective_max_age == 180
        changed = s._relax_filters_one_step()
        assert changed is True
        assert s._effective_max_age > 180  # 已放宽

    def test_restore_when_enough_candidates(self):
        """候选充足时 _maybe_tighten_filters 逐步恢复 max_age."""
        cfg = DoubleLowPlusConfig(
            top_k=3, min_candidates=5, max_age_days=180,
            fallback_max_age_days=730,
        )
        s = DoubleLowPlusStrategy(config=cfg)
        s._effective_max_age = 270  # 模拟已降级状态
        s._maybe_tighten_filters()
        assert s._effective_max_age < 270  # 已收紧

    def test_no_degrade_when_fallback_zero(self):
        """fallback_max_age_days=0 → 不降级."""
        cfg = DoubleLowPlusConfig(
            min_candidates=5, max_age_days=180, fallback_max_age_days=0,
        )
        s = DoubleLowPlusStrategy(config=cfg)
        original = s._effective_max_age
        changed = s._relax_filters_one_step()
        assert changed is False
        assert s._effective_max_age == original


# ============================================================
# TestEdgeCases — 边界与特殊场景
# ============================================================

class TestEdgeCases:
    """边界条件和状态重置等场景."""

    def test_on_start_extra_clears_history(self):
        """_on_start_extra 清空价格历史."""
        s = DoubleLowPlusStrategy()
        s._price_history["CB001"] = deque([100.0] * 5)
        s._effective_max_age = 500
        s._on_start_extra()
        assert len(s._price_history) == 0
        assert s._effective_max_age == s.config.max_age_days

    def test_on_bar_update_state_appends(self):
        """_on_bar_update_state 正确追加价格."""
        s = DoubleLowPlusStrategy()
        bar = _make_bar("CB001", close=105.0)
        ctx = _make_context()
        s._on_bar_update_state({"CB001": bar}, ctx)
        assert len(s._price_history["CB001"]) == 1
        assert s._price_history["CB001"][0] == 105.0

    def test_should_rebalance_first_bar_false(self):
        """第一根 bar 不触发再平衡."""
        s = DoubleLowPlusStrategy()
        s._bar_count = 1
        assert s._should_rebalance() is False

    def test_should_rebalance_periodic(self):
        """按 rebalance_days 周期触发."""
        cfg = DoubleLowPlusConfig(rebalance_days=3)
        s = DoubleLowPlusStrategy(config=cfg)
        s._bar_count = 4  # (4-1)%3 == 0 → True
        assert s._should_rebalance() is True
        s._bar_count = 5  # (5-1)%3 == 1 → False
        assert s._should_rebalance() is False

    def test_all_same_close_no_crash(self):
        """所有候选价格相同 → 不崩溃，有结果."""
        s = DoubleLowPlusStrategy()
        candidates = [
            _make_candidate(f"CB{i}", close=100.0, list_date="20240101")
            for i in range(5)
        ]
        ctx = _make_context()
        result = s._score_candidates(candidates, ctx)
        assert len(result) == 5

    def test_missing_list_date_uses_fallback_age(self):
        """list_date 为空 → age 默认 365."""
        s = DoubleLowPlusStrategy(
            config=DoubleLowPlusConfig(
                weight_age=1.0, weight_price=0.0, weight_premium=0.0,
                weight_size=0.0, weight_mean_reversion=0.0, weight_rating=0.0,
            ),
        )
        c_new = _make_candidate("NEW", list_date="20240501")
        c_nodate = _make_candidate("NODATE", list_date="")
        ctx = _make_context(current_time=datetime(2024, 6, 15, 15, 0))
        result = s._score_candidates([c_nodate, c_new], ctx)
        # NEW 上市45天, NODATE 默认365天 → NEW 更新排名更优
        assert result[0] == "NEW"
