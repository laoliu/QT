"""Tests for CBComprehensiveStrategy — 10因子综合可转债策略."""
from collections import deque
from datetime import datetime

import pytest

from aipha.domain.models import Bar
from aipha.strategy.base import StrategyContext
from aipha.strategy.cb_rotation_base import CandidateInfo
from aipha.strategy.registry import StrategyRegistry
from aipha.strategy.cb_comprehensive import (
    CBComprehensiveConfig,
    CBComprehensiveStrategy,
)


# ---------------------------------------------------------------------------
# Helper factories
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
    maturity_date: str = "20280101",
) -> CandidateInfo:
    """构造 CandidateInfo，含 rating 和 maturity_date."""
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
            "maturity_date": maturity_date,
        },
        issue_size=issue_size,
        cb_over_rate=cb_over_rate,
        cb_value=cb_value,
        list_date=list_date,
        amount=amount,
        rating=rating,
        maturity_date=maturity_date,
    )


# ============================================================
# TestCBComprehensiveConfig
# ============================================================

class TestCBComprehensiveConfig:
    """配置默认值与覆盖."""

    def test_default_top_k(self):
        cfg = CBComprehensiveConfig()
        assert cfg.top_k == 10

    def test_10_weights_sum_to_one(self):
        cfg = CBComprehensiveConfig()
        total = (
            cfg.weight_price + cfg.weight_premium + cfg.weight_size
            + cfg.weight_age + cfg.weight_mean_reversion + cfg.weight_rating
            + cfg.weight_delta + cfg.weight_ytm + cfg.weight_turnover
            + cfg.weight_momentum
        )
        assert abs(total - 1.0) < 1e-9

    def test_new_weights_exist(self):
        cfg = CBComprehensiveConfig()
        assert cfg.weight_delta == 0.10
        assert cfg.weight_ytm == 0.10
        assert cfg.weight_turnover == 0.05
        assert cfg.weight_momentum == 0.10

    def test_momentum_lookback_default(self):
        cfg = CBComprehensiveConfig()
        assert cfg.momentum_lookback == 10

    def test_override_weight(self):
        cfg = CBComprehensiveConfig(weight_delta=0.50, weight_ytm=0.0)
        assert cfg.weight_delta == 0.50
        assert cfg.weight_ytm == 0.0


# ============================================================
# TestRegistration
# ============================================================

class TestRegistration:
    def test_registered_name(self):
        s = CBComprehensiveStrategy()
        assert s.name == "cb_comprehensive"

    def test_registry_roundtrip(self):
        cls = StrategyRegistry().get("cb_comprehensive")
        assert cls is CBComprehensiveStrategy

    def test_instance_has_config(self):
        s = CBComprehensiveStrategy()
        assert isinstance(s.config, CBComprehensiveConfig)


# ============================================================
# TestExtraFilter
# ============================================================

class TestExtraFilter:
    def _make_strategy(self, **kwargs) -> CBComprehensiveStrategy:
        cfg = CBComprehensiveConfig(**kwargs)
        return CBComprehensiveStrategy(config=cfg)

    def test_pass_normal(self):
        s = self._make_strategy()
        c = _make_candidate("CB001", cb_over_rate=20.0, list_date="20240101")
        assert s._extra_filter(c, _make_context()) is True

    def test_reject_high_premium(self):
        s = self._make_strategy(max_premium=50.0)
        c = _make_candidate("CB002", cb_over_rate=55.0)
        assert s._extra_filter(c, _make_context()) is False

    def test_reject_too_new(self):
        s = self._make_strategy(min_age_days=3)
        c = _make_candidate("CB003", list_date="20240614")
        ctx = _make_context(current_time=datetime(2024, 6, 15, 15, 0))
        assert s._extra_filter(c, ctx) is False


# ============================================================
# TestComputeMomentum
# ============================================================

class TestComputeMomentum:
    def test_insufficient_history(self):
        cfg = CBComprehensiveConfig(momentum_lookback=10)
        s = CBComprehensiveStrategy(config=cfg)
        s._price_history["CB001"] = deque([100.0] * 5, maxlen=30)
        assert s._compute_momentum("CB001") is None

    def test_positive_momentum(self):
        cfg = CBComprehensiveConfig(momentum_lookback=5)
        s = CBComprehensiveStrategy(config=cfg)
        prices = [100.0, 101.0, 102.0, 103.0, 104.0, 110.0]
        s._price_history["CB001"] = deque(prices, maxlen=30)
        result = s._compute_momentum("CB001")
        assert result is not None
        assert result > 0  # 涨了

    def test_negative_momentum(self):
        cfg = CBComprehensiveConfig(momentum_lookback=5)
        s = CBComprehensiveStrategy(config=cfg)
        prices = [100.0, 99.0, 98.0, 97.0, 96.0, 90.0]
        s._price_history["CB001"] = deque(prices, maxlen=30)
        result = s._compute_momentum("CB001")
        assert result is not None
        assert result < 0  # 跌了

    def test_unknown_symbol(self):
        s = CBComprehensiveStrategy()
        assert s._compute_momentum("UNKNOWN") is None


# ============================================================
# TestBBDeviation
# ============================================================

class TestBBDeviation:
    def test_insufficient_returns_none(self):
        cfg = CBComprehensiveConfig(lookback=20)
        s = CBComprehensiveStrategy(config=cfg)
        s._price_history["CB001"] = deque([100.0] * 5, maxlen=30)
        assert s._compute_bb_deviation("CB001", 95.0) is None

    def test_sufficient_returns_float(self):
        cfg = CBComprehensiveConfig(lookback=20)
        s = CBComprehensiveStrategy(config=cfg)
        prices = [100.0 + i * 0.5 for i in range(20)]
        s._price_history["CB001"] = deque(prices, maxlen=30)
        result = s._compute_bb_deviation("CB001", 90.0)
        assert isinstance(result, float)

    def test_zero_std_returns_none(self):
        cfg = CBComprehensiveConfig(lookback=20)
        s = CBComprehensiveStrategy(config=cfg)
        s._price_history["CB001"] = deque([100.0] * 20, maxlen=30)
        assert s._compute_bb_deviation("CB001", 100.0) is None


# ============================================================
# TestScoreCandidates
# ============================================================

class TestScoreCandidates:
    def _make_strategy(self, **kwargs) -> CBComprehensiveStrategy:
        cfg = CBComprehensiveConfig(**kwargs)
        return CBComprehensiveStrategy(config=cfg)

    def test_empty_candidates(self):
        s = self._make_strategy()
        assert s._score_candidates([], _make_context()) == []

    def test_single_candidate(self):
        s = self._make_strategy()
        c = _make_candidate("CB001")
        result = s._score_candidates([c], _make_context())
        assert result == ["CB001"]

    def test_lower_price_ranks_better(self):
        s = self._make_strategy(
            weight_price=1.0, weight_premium=0.0, weight_size=0.0,
            weight_age=0.0, weight_mean_reversion=0.0, weight_rating=0.0,
            weight_delta=0.0, weight_ytm=0.0, weight_turnover=0.0,
            weight_momentum=0.0,
        )
        c_low = _make_candidate("LOW", close=80.0, list_date="20240101")
        c_high = _make_candidate("HIGH", close=130.0, list_date="20240101")
        result = s._score_candidates([c_high, c_low], _make_context())
        assert result[0] == "LOW"

    def test_higher_delta_ranks_better(self):
        """Delta因子: cb_value/close 越高越好."""
        s = self._make_strategy(
            weight_price=0.0, weight_premium=0.0, weight_size=0.0,
            weight_age=0.0, weight_mean_reversion=0.0, weight_rating=0.0,
            weight_delta=1.0, weight_ytm=0.0, weight_turnover=0.0,
            weight_momentum=0.0,
        )
        c_high_delta = _make_candidate("HD", close=90.0, cb_value=110.0,
                                       list_date="20240101")
        c_low_delta = _make_candidate("LD", close=110.0, cb_value=90.0,
                                      list_date="20240101")
        result = s._score_candidates([c_low_delta, c_high_delta], _make_context())
        assert result[0] == "HD"

    def test_higher_ytm_ranks_better(self):
        """YTM因子: 到期收益率越高越好."""
        s = self._make_strategy(
            weight_price=0.0, weight_premium=0.0, weight_size=0.0,
            weight_age=0.0, weight_mean_reversion=0.0, weight_rating=0.0,
            weight_delta=0.0, weight_ytm=1.0, weight_turnover=0.0,
            weight_momentum=0.0,
        )
        # 低价 → 高YTM
        c_high_ytm = _make_candidate("HY", close=85.0, maturity_date="20280101",
                                     list_date="20240101")
        c_low_ytm = _make_candidate("LY", close=120.0, maturity_date="20280101",
                                    list_date="20240101")
        result = s._score_candidates([c_low_ytm, c_high_ytm], _make_context())
        assert result[0] == "HY"

    def test_higher_turnover_ranks_better(self):
        """流动性因子: amount/issue_size 越高越好."""
        s = self._make_strategy(
            weight_price=0.0, weight_premium=0.0, weight_size=0.0,
            weight_age=0.0, weight_mean_reversion=0.0, weight_rating=0.0,
            weight_delta=0.0, weight_ytm=0.0, weight_turnover=1.0,
            weight_momentum=0.0,
        )
        c_active = _make_candidate("ACT", amount=500.0, issue_size=1000.0,
                                   list_date="20240101")
        c_quiet = _make_candidate("QUI", amount=10.0, issue_size=10000.0,
                                  list_date="20240101")
        result = s._score_candidates([c_quiet, c_active], _make_context())
        assert result[0] == "ACT"

    def test_top_k_limits_output(self):
        s = self._make_strategy(top_k=3)
        candidates = [
            _make_candidate(f"CB{i:03d}", close=100.0 + i, list_date="20240101")
            for i in range(10)
        ]
        result = s._score_candidates(candidates, _make_context())
        assert len(result) <= 3


# ============================================================
# TestAdaptiveAgeDegradation
# ============================================================

class TestAdaptiveAgeDegradation:
    """测试基类自适应降级/恢复机制（通过子类实例验证）。"""

    def test_degrade_when_few(self):
        """_relax_filters_one_step 应放宽 max_age。"""
        cfg = CBComprehensiveConfig(
            min_candidates=5, max_age_days=180, fallback_max_age_days=730,
        )
        s = CBComprehensiveStrategy(config=cfg)
        assert s._effective_max_age == 180
        changed = s._relax_filters_one_step()
        assert changed is True
        assert s._effective_max_age > 180  # 已放宽

    def test_restore_when_enough(self):
        """候选充足时 _maybe_tighten_filters 应逐步恢复 max_age。"""
        cfg = CBComprehensiveConfig(
            top_k=3, min_candidates=5, max_age_days=180, fallback_max_age_days=730,
        )
        s = CBComprehensiveStrategy(config=cfg)
        s._effective_max_age = 270  # 模拟已降级状态
        s._maybe_tighten_filters()
        assert s._effective_max_age < 270  # 已收紧


# ============================================================
# TestEdgeCases
# ============================================================

class TestEdgeCases:
    def test_on_start_extra_clears(self):
        s = CBComprehensiveStrategy()
        s._price_history["CB001"] = deque([100.0] * 5)
        s._effective_max_age = 500
        s._on_start_extra()
        assert len(s._price_history) == 0
        assert s._effective_max_age == s.config.max_age_days

    def test_on_bar_update_appends(self):
        s = CBComprehensiveStrategy()
        bar = _make_bar("CB001", close=105.0)
        s._on_bar_update_state({"CB001": bar}, _make_context())
        assert len(s._price_history["CB001"]) == 1

    def test_all_same_close_no_crash(self):
        s = CBComprehensiveStrategy()
        candidates = [
            _make_candidate(f"CB{i}", close=100.0, list_date="20240101")
            for i in range(5)
        ]
        result = s._score_candidates(candidates, _make_context())
        assert len(result) == 5

    def test_rebalance_first_bar_false(self):
        s = CBComprehensiveStrategy()
        s._bar_count = 1
        assert s._should_rebalance() is False

    def test_rebalance_periodic(self):
        cfg = CBComprehensiveConfig(rebalance_days=3)
        s = CBComprehensiveStrategy(config=cfg)
        s._bar_count = 4
        assert s._should_rebalance() is True
        s._bar_count = 5
        assert s._should_rebalance() is False


# ============================================================
# TestC1NewFactors — C1 新增因子：ValuationPercentile + PutbackProximity
# ============================================================

class TestC1NewFactors:
    """C1 增强因子：溢价率百分位 + 回售接近度."""

    # ----- Config 字段存在性 -----

    def test_c1_config_fields_exist(self):
        """C1 配置字段默认值为 0.0 (不影响现有行为)."""
        cfg = CBComprehensiveConfig()
        assert hasattr(cfg, "weight_premium_percentile")
        assert hasattr(cfg, "weight_putback")
        assert cfg.weight_premium_percentile == 0.0
        assert cfg.weight_putback == 0.0

    def test_c1_config_lookback_default(self):
        """溢价率百分位窗口默认 40 天."""
        cfg = CBComprehensiveConfig()
        assert cfg.premium_pct_lookback == 40

    def test_c1_config_putback_trigger_default(self):
        """回售触发比率默认 0.85."""
        cfg = CBComprehensiveConfig()
        assert cfg.putback_trigger_ratio == 0.85

    # ----- PremiumPercentile 因子 -----

    def test_premium_percentile_insufficient_history_returns_neutral(self):
        """历史数据不足时返回 0.5（中性）."""
        s = CBComprehensiveStrategy()
        # 只有 3 条历史，min_hist = max(10, 40//2) = 20，不足
        s._premium_history["CB001"] = deque([5.0, 6.0, 7.0], maxlen=40)
        result = s._compute_premium_percentile("CB001", current_rate=5.0)
        assert result == 0.5

    def test_premium_percentile_no_history_returns_neutral(self):
        """symbol 完全没有历史记录时返回 0.5."""
        s = CBComprehensiveStrategy()
        result = s._compute_premium_percentile("UNKNOWN", current_rate=10.0)
        assert result == 0.5

    def test_premium_percentile_low_rate_returns_high_score(self):
        """当前溢价率低于历史所有值 → 得分接近 1.0（便宜）."""
        s = CBComprehensiveStrategy()
        hist = deque([20.0] * 25, maxlen=40)  # 历史溢价率均为 20%
        s._premium_history["CB001"] = hist
        # current_rate=5.0 < 所有历史值 → pct_rank=0% → score=1.0
        result = s._compute_premium_percentile("CB001", current_rate=5.0)
        assert abs(result - 1.0) < 1e-9

    def test_premium_percentile_high_rate_returns_low_score(self):
        """当前溢价率高于历史所有值 → 得分接近 0.0（昂贵）."""
        s = CBComprehensiveStrategy()
        hist = deque([10.0] * 25, maxlen=40)  # 历史均为 10%
        s._premium_history["CB001"] = hist
        # current_rate=50.0 > 所有历史值 → pct_rank=100% → score=0.0
        result = s._compute_premium_percentile("CB001", current_rate=50.0)
        assert abs(result - 0.0) < 1e-9

    def test_premium_percentile_at_median_returns_half(self):
        """当前溢价率处于历史中位数时，得分约为 0.5."""
        s = CBComprehensiveStrategy()
        # 25 个值：0~24；中位数附近的值(≈12)应给出≈0.5
        hist = deque(range(25), maxlen=40)
        s._premium_history["CB001"] = hist
        result = s._compute_premium_percentile("CB001", current_rate=12.0)
        # rank = sum(x <= 12 for x in range(25)) / 25 = 13/25 = 0.52
        # score = 1 - 0.52 = 0.48
        assert 0.40 <= result <= 0.60

    # ----- PutbackProximity 因子 -----

    def test_putback_proximity_none_returns_zero(self):
        """cb_value=None 时返回 0.0（防御处理）."""
        s = CBComprehensiveStrategy()
        assert s._compute_putback_proximity(None) == 0.0

    def test_putback_proximity_zero_returns_zero(self):
        """cb_value=0 时返回 0.0（无效值防御）."""
        s = CBComprehensiveStrategy()
        assert s._compute_putback_proximity(0.0) == 0.0

    def test_putback_proximity_at_trigger_returns_near_zero(self):
        """cb_value=85（parity=0.85=触发线）→ proximity ≈ 0."""
        s = CBComprehensiveStrategy()
        # trigger_ratio=0.85, cb_value=85 → parity=0.85/trigger=1.0 → 1-1=0
        result = s._compute_putback_proximity(cb_value=85.0)
        assert abs(result) < 1e-9

    def test_putback_proximity_below_trigger_is_positive(self):
        """cb_value=70（低于触发线）→ proximity > 0（回售保护激活）."""
        s = CBComprehensiveStrategy()
        result = s._compute_putback_proximity(cb_value=70.0)
        assert result > 0.0

    def test_putback_proximity_above_trigger_is_negative(self):
        """cb_value=110（高于触发线）→ proximity < 0（正常转债，无回售保护）."""
        s = CBComprehensiveStrategy()
        result = s._compute_putback_proximity(cb_value=110.0)
        assert result < 0.0

    # ----- 集成测试：C1 因子进入 _score_candidates -----

    def test_c1_factors_active_no_crash(self):
        """启用 C1 权重后，_score_candidates 正常运行不崩溃."""
        cfg = CBComprehensiveConfig(weight_premium_percentile=0.08, weight_putback=0.05)
        s = CBComprehensiveStrategy(config=cfg)
        candidates = [
            _make_candidate(f"CB{i}", close=100.0 + i, cb_over_rate=10.0 - i,
                            cb_value=80.0 + i * 2)
            for i in range(10)
        ]
        # 注入足够历史
        for i in range(10):
            sym = f"CB{i}"
            s._premium_history[sym] = deque([10.0] * 25, maxlen=40)
        result = s._score_candidates(candidates, _make_context())
        assert len(result) == cfg.top_k

    def test_c1_zero_weights_not_change_scores(self):
        """C1 权重=0 时，评分结果与无 C1 版本一致（向后兼容性）."""
        s_base = CBComprehensiveStrategy()
        s_c1 = CBComprehensiveStrategy(
            config=CBComprehensiveConfig(
                weight_premium_percentile=0.0, weight_putback=0.0
            )
        )
        candidates = [
            _make_candidate(f"CB{i}", close=100.0 + i, cb_over_rate=float(i),
                            cb_value=float(80 + i))
            for i in range(10)
        ]
        base_result = s_base._score_candidates(candidates, _make_context())
        c1_result = s_c1._score_candidates(candidates, _make_context())
        # 结果类型一致（均为 list[str] 或 list[CandidateInfo]，取决于实现）
        assert len(base_result) == len(c1_result)
        # 验证每个元素字符串化后相同
        def _sym(x):
            return x.symbol if hasattr(x, "symbol") else str(x)
        assert [_sym(x) for x in base_result] == [_sym(x) for x in c1_result]

    def test_premium_history_tracked_on_bar_update(self):
        """启用 PremiumPercentile 权重时，history 在 _on_bar_update_state 中被追踪."""
        cfg = CBComprehensiveConfig(weight_premium_percentile=0.05)
        s = CBComprehensiveStrategy(config=cfg)
        bar = _make_bar("CB001", close=105.0, metadata={"cb_over_rate": 15.5})
        s._on_bar_update_state({"CB001": bar}, _make_context())
        assert "CB001" in s._premium_history
        assert len(s._premium_history["CB001"]) == 1
        assert s._premium_history["CB001"][0] == 15.5
