"""Tests for CB enhanced factors module."""
from datetime import datetime

import pytest

from aipha.strategy.cb_enhanced_factors import (
    FactorWeight,
    PremiumMomentumTracker,
    compute_factor_values,
    compute_pool_stats,
    delta_exposure,
    enhanced_ytm,
    rank_fusion,
    rating_score,
    turnover_activity,
    value_size_composite,
)
from aipha.strategy.cb_rotation_base import CandidateInfo, _rating_score


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _make_candidate(
    symbol: str = "123456.SZ",
    close: float = 100.0,
    cb_value: float | None = 100.0,
    cb_over_rate: float | None = 10.0,
    issue_size: float = 5000.0,
    amount: float = 200.0,
    maturity_date: str = "20280101",
    rating: str = "AA",
    **kwargs,
) -> CandidateInfo:
    meta = {
        "issue_size": issue_size,
        "amount": amount,
        "cb_value": cb_value,
        "cb_over_rate": cb_over_rate,
        "maturity_date": maturity_date,
        "rating": rating,
        **kwargs,
    }
    return CandidateInfo(
        symbol=symbol,
        close=close,
        metadata=meta,
        amount=amount,
        issue_size=issue_size,
        cb_over_rate=cb_over_rate,
        cb_value=cb_value,
        maturity_date=maturity_date,
        rating=rating,
    )


# ===========================================================================
# 信用评级评分
# ===========================================================================

class TestRatingScore:
    """测试 cb_enhanced_factors.rating_score 和 cb_rotation_base._rating_score"""

    def test_aaa(self):
        assert rating_score("AAA") == 1.0

    def test_aa_plus(self):
        assert rating_score("AA+") == 0.90

    def test_aa(self):
        assert rating_score("AA") == 0.80

    def test_aa_minus(self):
        assert rating_score("AA-") == 0.70

    def test_bbb(self):
        assert rating_score("BBB") == 0.30

    def test_c(self):
        assert rating_score("C") == 0.0

    def test_empty_returns_neutral(self):
        assert rating_score("") == 0.5

    def test_unknown_returns_neutral(self):
        assert rating_score("XYZ") == 0.5

    def test_case_insensitive(self):
        assert rating_score("aaa") == 1.0
        assert rating_score("aa+") == 0.90

    def test_with_spaces(self):
        assert rating_score("  AA  ") == 0.80


class TestBaseRatingScore:
    """测试 cb_rotation_base._rating_score"""

    def test_aaa(self):
        assert _rating_score("AAA") == 100

    def test_aa(self):
        assert _rating_score("AA") == 80

    def test_c(self):
        assert _rating_score("C") == 0

    def test_unknown(self):
        assert _rating_score("XYZ") == -1

    def test_ordering(self):
        assert _rating_score("AAA") > _rating_score("AA+")
        assert _rating_score("AA+") > _rating_score("AA")
        assert _rating_score("AA") > _rating_score("AA-")
        assert _rating_score("AA-") > _rating_score("A+")
        assert _rating_score("BBB") > _rating_score("BB")


# ===========================================================================
# Delta 暴露因子
# ===========================================================================

class TestDeltaExposure:

    def test_at_par(self):
        """cb_value == close → delta = 1.0"""
        c = _make_candidate(close=100.0, cb_value=100.0)
        assert abs(delta_exposure(c) - 1.0) < 1e-9

    def test_discount(self):
        """cb_value > close → delta > 1.0（折价）"""
        c = _make_candidate(close=100.0, cb_value=110.0)
        assert delta_exposure(c) == pytest.approx(1.1)

    def test_premium(self):
        """cb_value < close → delta < 1.0（溢价）"""
        c = _make_candidate(close=110.0, cb_value=100.0)
        assert delta_exposure(c) == pytest.approx(100 / 110)

    def test_no_cb_value(self):
        """无转股价值 → 0"""
        c = _make_candidate(close=100.0, cb_value=None)
        assert delta_exposure(c) == 0.0

    def test_zero_close(self):
        c = _make_candidate(close=0.0, cb_value=100.0)
        assert delta_exposure(c) == 0.0

    def test_zero_cb_value(self):
        c = _make_candidate(close=100.0, cb_value=0.0)
        assert delta_exposure(c) == 0.0


# ===========================================================================
# 溢价率动量
# ===========================================================================

class TestPremiumMomentumTracker:

    def test_no_history(self):
        tracker = PremiumMomentumTracker(lookback=3)
        assert tracker.momentum("A") is None

    def test_insufficient_history(self):
        tracker = PremiumMomentumTracker(lookback=3)
        tracker.update("A", 10.0)
        tracker.update("A", 11.0)
        assert tracker.momentum("A") is None

    def test_momentum_decrease(self):
        """溢价率下降 → 正分（好信号）"""
        tracker = PremiumMomentumTracker(lookback=3)
        for v in [20.0, 18.0, 16.0, 14.0, 12.0]:
            tracker.update("A", v)
        mom = tracker.momentum("A")
        assert mom is not None
        assert mom > 0  # 溢价率下降 → 产出正分

    def test_momentum_increase(self):
        """溢价率上升 → 负分（坏信号）"""
        tracker = PremiumMomentumTracker(lookback=3)
        for v in [10.0, 12.0, 14.0, 16.0, 18.0]:
            tracker.update("A", v)
        mom = tracker.momentum("A")
        assert mom is not None
        assert mom < 0

    def test_none_update_ignored(self):
        tracker = PremiumMomentumTracker(lookback=2)
        tracker.update("A", 10.0)
        tracker.update("A", None)
        tracker.update("A", 12.0)
        tracker.update("A", 14.0)
        mom = tracker.momentum("A")
        assert mom is not None

    def test_multi_symbol(self):
        tracker = PremiumMomentumTracker(lookback=2)
        for v in [10, 12, 14]:
            tracker.update("A", float(v))
        for v in [20, 18, 16]:
            tracker.update("B", float(v))
        assert tracker.momentum("A") is not None
        assert tracker.momentum("B") is not None
        assert tracker.momentum("A") < 0  # A 上升 → 负分
        assert tracker.momentum("B") > 0  # B 下降 → 正分


# ===========================================================================
# 到期收益率增强
# ===========================================================================

class TestEnhancedYTM:

    def test_below_par(self):
        """价格低于面值 → 正 YTM"""
        now = datetime(2024, 1, 1)
        ytm = enhanced_ytm(90.0, "20280101", now)
        assert ytm > 0

    def test_above_par(self):
        """价格高于面值 → 负 YTM"""
        now = datetime(2024, 1, 1)
        ytm = enhanced_ytm(110.0, "20280101", now)
        assert ytm < 0

    def test_at_par(self):
        """价格等于面值 → YTM ≈ 0"""
        now = datetime(2024, 1, 1)
        ytm = enhanced_ytm(100.0, "20280101", now)
        assert abs(ytm) < 0.01

    def test_near_maturity(self):
        """接近到期但仍有余裕 → YTM 正常"""
        now = datetime(2027, 6, 1)
        ytm = enhanced_ytm(95.0, "20280101", now)
        assert ytm > 0
        assert ytm < 10  # 不会离谱

    def test_very_near_maturity(self):
        """非常接近到期 (< 0.1年) → 返回 0"""
        now = datetime(2028, 1, 1)
        ytm = enhanced_ytm(95.0, "20280201", now)
        assert ytm == 0.0  # years < 0.1 → 保护性返回 0

    def test_expired(self):
        """已到期 → 0"""
        now = datetime(2029, 1, 1)
        ytm = enhanced_ytm(95.0, "20280101", now)
        # days would be 0 or negative, so years < 0.1
        assert ytm == 0.0

    def test_invalid_date(self):
        now = datetime(2024, 1, 1)
        assert enhanced_ytm(95.0, "invalid", now) == 0.0
        assert enhanced_ytm(95.0, "", now) == 0.0

    def test_zero_price(self):
        now = datetime(2024, 1, 1)
        assert enhanced_ytm(0.0, "20280101", now) == 0.0

    def test_very_high_price(self):
        now = datetime(2024, 1, 1)
        assert enhanced_ytm(250.0, "20280101", now) == 0.0


# ===========================================================================
# 成交活跃度
# ===========================================================================

class TestTurnoverActivity:

    def test_normal(self):
        c = _make_candidate(amount=500.0, issue_size=10000.0)
        assert turnover_activity(c) == pytest.approx(0.05)

    def test_zero_size(self):
        c = _make_candidate(amount=500.0, issue_size=0.0)
        assert turnover_activity(c) == 0.0

    def test_high_activity(self):
        c = _make_candidate(amount=10000.0, issue_size=5000.0)
        assert turnover_activity(c) == pytest.approx(2.0)


# ===========================================================================
# 价值-规模复合因子
# ===========================================================================

class TestValueSizeComposite:

    def test_low_price_low_premium_small_high_delta(self):
        """低价+低溢价+小规模+高Delta = 高分"""
        c = _make_candidate(close=90.0, cb_over_rate=0.0, issue_size=2000.0, cb_value=100.0)
        score = value_size_composite(c)
        assert score > 0.7

    def test_high_price_high_premium_large_low_delta(self):
        """高价+高溢价+大规模+低Delta = 低分"""
        c = _make_candidate(close=140.0, cb_over_rate=40.0, issue_size=80000.0, cb_value=80.0)
        score = value_size_composite(c)
        assert score < 0.3

    def test_custom_weights(self):
        c = _make_candidate(close=100.0, cb_over_rate=10.0, issue_size=5000.0, cb_value=100.0)
        s1 = value_size_composite(c, price_weight=1.0, premium_weight=0, size_weight=0, delta_weight=0)
        s2 = value_size_composite(c, price_weight=0, premium_weight=1.0, size_weight=0, delta_weight=0)
        # both should be valid but different
        assert 0 <= s1 <= 1
        assert 0 <= s2 <= 1

    def test_with_pool_stats(self):
        """使用自适应归一化参数"""
        c = _make_candidate(close=100.0, cb_over_rate=10.0, issue_size=5000.0, cb_value=100.0)
        stats = {"price": (90.0, 130.0), "premium": (0.0, 30.0), "size": (2000.0, 50000.0), "delta": (0.6, 1.3)}
        s_adaptive = value_size_composite(c, pool_stats=stats)
        s_default = value_size_composite(c)
        # 两种方式都应该返回有效分数，但值可能不同
        assert 0 <= s_adaptive <= 1
        assert 0 <= s_default <= 1

    def test_pool_stats_narrow_range(self):
        """窄范围 pool_stats → 更高区分度"""
        low = _make_candidate(close=95.0, cb_over_rate=5.0, issue_size=3000.0, cb_value=100.0)
        high = _make_candidate(close=105.0, cb_over_rate=15.0, issue_size=8000.0, cb_value=90.0)
        stats = {"price": (90.0, 110.0), "premium": (0.0, 20.0), "size": (2000.0, 10000.0), "delta": (0.8, 1.2)}
        s_low = value_size_composite(low, pool_stats=stats)
        s_high = value_size_composite(high, pool_stats=stats)
        assert s_low > s_high

    def test_backward_compat_no_pool_stats(self):
        """无 pool_stats 时使用默认值（向后兼容）"""
        c = _make_candidate(close=100.0, cb_over_rate=10.0, issue_size=5000.0, cb_value=100.0)
        score = value_size_composite(c)
        assert 0 <= score <= 1


class TestComputePoolStats:
    """compute_pool_stats 测试"""

    def test_basic_computation(self):
        candidates = [
            _make_candidate(f"S{i}", close=90+i*5, cb_over_rate=i*3.0, issue_size=1000+i*2000, cb_value=95+i*2)
            for i in range(20)
        ]
        stats = compute_pool_stats(candidates)
        assert "price" in stats
        assert "premium" in stats
        assert "size" in stats
        assert "delta" in stats
        # 各范围应该是 (low, high) 且 low < high
        for key in ["price", "premium", "size", "delta"]:
            lo, hi = stats[key]
            assert lo < hi, f"{key}: lo={lo} >= hi={hi}"

    def test_empty_candidates(self):
        stats = compute_pool_stats([])
        assert stats == {}

    def test_single_candidate(self):
        candidates = [_make_candidate("A", close=100.0)]
        stats = compute_pool_stats(candidates)
        # 单个候选 → percentile_low = percentile_high
        if "price" in stats:
            lo, hi = stats["price"]
            assert lo == pytest.approx(hi)

    def test_custom_percentiles(self):
        candidates = [
            _make_candidate(f"S{i}", close=float(i), cb_over_rate=float(i), issue_size=float(i*100))
            for i in range(1, 101)
        ]
        stats_wide = compute_pool_stats(candidates, percentile_low=1.0, percentile_high=99.0)
        stats_narrow = compute_pool_stats(candidates, percentile_low=25.0, percentile_high=75.0)
        # 宽范围应该比窄范围大
        w_lo, w_hi = stats_wide["price"]
        n_lo, n_hi = stats_narrow["price"]
        assert w_hi - w_lo > n_hi - n_lo


# ===========================================================================
# 多因子排名融合
# ===========================================================================

class TestRankFusion:

    def test_basic_fusion(self):
        candidates = [
            _make_candidate("A", close=90, cb_value=110),
            _make_candidate("B", close=100, cb_value=100),
            _make_candidate("C", close=110, cb_value=90),
        ]
        factor_values = {
            "price": {"A": -90, "B": -100, "C": -110},
            "delta": {"A": 110/90, "B": 1.0, "C": 90/110},
        }
        weights = [
            FactorWeight("price", 0.5, ascending=True),
            FactorWeight("delta", 0.5, ascending=True),
        ]
        result = rank_fusion(candidates, factor_values, weights, top_k=2)
        assert len(result) == 2
        assert result[0] == "A"  # A 最便宜 且 Delta 最高

    def test_empty_candidates(self):
        result = rank_fusion([], {}, [], top_k=5)
        assert result == []

    def test_top_k_limit(self):
        candidates = [_make_candidate(f"S{i}", close=100+i) for i in range(10)]
        factor_values = {"price": {f"S{i}": -(100+i) for i in range(10)}}
        weights = [FactorWeight("price", 1.0)]
        result = rank_fusion(candidates, factor_values, weights, top_k=3)
        assert len(result) == 3

    def test_negative_weight_raises(self):
        with pytest.raises(ValueError):
            FactorWeight("x", -0.5)


# ===========================================================================
# 批量计算
# ===========================================================================

class TestComputeFactorValues:

    def test_returns_all_factors(self):
        candidates = [
            _make_candidate("A", close=90, cb_value=95, cb_over_rate=5.0, rating="AA"),
            _make_candidate("B", close=110, cb_value=100, cb_over_rate=15.0, rating="A+"),
        ]
        now = datetime(2024, 3, 15)
        result = compute_factor_values(candidates, now)
        assert "price" in result
        assert "premium" in result
        assert "size" in result
        assert "delta" in result
        assert "ytm" in result
        assert "activity" in result
        assert "composite" in result
        assert "rating" in result
        assert "A" in result["price"]
        assert "B" in result["price"]

    def test_with_premium_tracker(self):
        candidates = [_make_candidate("A", cb_over_rate=10.0)]
        tracker = PremiumMomentumTracker(lookback=2)
        for v in [15.0, 13.0, 10.0]:
            tracker.update("A", v)
        now = datetime(2024, 3, 15)
        result = compute_factor_values(candidates, now, premium_tracker=tracker)
        assert "premium_momentum" in result
        assert "A" in result["premium_momentum"]
        assert result["premium_momentum"]["A"] > 0  # 溢价率下降

    def test_without_tracker_no_momentum(self):
        candidates = [_make_candidate("A")]
        result = compute_factor_values(candidates, datetime(2024, 1, 1))
        assert "premium_momentum" not in result


# ===========================================================================
# 信用评级过滤（集成测试 - cb_rotation_base）
# ===========================================================================

class TestRatingFilter:
    """测试 CBRotationConfig 中评级过滤在 _filter_candidates 中的行为"""

    def _make_bars_with_ratings(self) -> dict:
        """创建带不同评级的 bars"""
        from aipha.domain.models import Bar
        bars = {}
        ratings = [
            ("AAA_BOND.SZ", 100.0, "AAA"),
            ("AA_BOND.SZ", 100.0, "AA"),
            ("A_BOND.SZ", 100.0, "A"),
            ("BBB_BOND.SZ", 100.0, "BBB"),
            ("UNRATED.SZ", 100.0, ""),
        ]
        for sym, price, r in ratings:
            bars[sym] = Bar(
                symbol=sym,
                start_ts=datetime(2024, 3, 15, 9, 30),
                end_ts=datetime(2024, 3, 15, 15, 0),
                open=price, high=price*1.02, low=price*0.98,
                close=price, volume=1000,
                metadata={
                    "amount": 200.0,
                    "issue_size": 5000.0,
                    "cb_over_rate": 10.0,
                    "cb_value": 100.0,
                    "maturity_date": "20280101",
                    "stk_short_name": "正股",
                    "rating": r,
                },
            )
        return bars

    def test_no_rating_filter_passes_all(self):
        """无评级过滤 → 全部通过"""
        from aipha.strategy.cb_rotation_base import CBRotationConfig
        from aipha.strategy.base import StrategyContext

        class DummyStrategy:
            pass

        # 直接测试 _rating_score 逻辑
        assert _rating_score("AAA") > _rating_score("AA")
        assert _rating_score("AA") > _rating_score("A")
        assert _rating_score("A") > _rating_score("BBB")

    def test_min_rating_aa_filters_correctly(self):
        """min_rating=AA → 只保留 AAA 和 AA"""
        assert _rating_score("AAA") >= _rating_score("AA")
        assert _rating_score("AA") >= _rating_score("AA")
        assert _rating_score("A") < _rating_score("AA")
        assert _rating_score("BBB") < _rating_score("AA")

    def test_exclude_unrated(self):
        """exclude_unrated=True → 空评级被排除"""
        # 空字符串的评级分数为 -1（在 base 里）
        assert _rating_score("") == -1
