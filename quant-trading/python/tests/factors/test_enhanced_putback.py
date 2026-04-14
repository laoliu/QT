"""回售博弈增强因子 (EnhancedPutback) 测试。

覆盖：
- compute_putback_from_cb：回售接近度从CB数据计算
- compute_downward_revision_probability：下修概率代理
- compute_clause_play_score：条款博弈复合分数
"""

from __future__ import annotations

import pytest

from aipha.factors.enhanced_putback import (
    compute_clause_play_score,
    compute_downward_revision_probability,
    compute_putback_from_cb,
)


# ── compute_putback_from_cb ───────────────────────────────


class TestComputePutbackFromCB:
    """compute_putback_from_cb 测试。"""

    def test_normal_high_parity(self):
        """parity=1.2 > trigger → 距触发线远 → 负值"""
        result = compute_putback_from_cb(120.0, trigger_ratio=0.70)
        assert result is not None
        # 1 - 1.2/0.7 = 1 - 1.714 = -0.714
        assert result < -0.5

    def test_normal_at_trigger(self):
        """parity=0.70 ≈ trigger → 接近0"""
        result = compute_putback_from_cb(70.0, trigger_ratio=0.70)
        assert result is not None
        assert abs(result) < 0.01

    def test_normal_below_trigger(self):
        """parity=0.50 < trigger → 已穿触发线 → 正值"""
        result = compute_putback_from_cb(50.0, trigger_ratio=0.70)
        assert result is not None
        assert result > 0.2

    def test_low_parity_high_proximity(self):
        """parity=0.30 远低于触发线 → 高正值"""
        result = compute_putback_from_cb(30.0, trigger_ratio=0.70)
        assert result is not None
        assert result > 0.5

    def test_cb_value_none_returns_none(self):
        assert compute_putback_from_cb(None) is None

    def test_cb_value_zero_returns_none(self):
        assert compute_putback_from_cb(0.0) is None

    def test_cb_value_negative_returns_none(self):
        assert compute_putback_from_cb(-10.0) is None

    def test_trigger_ratio_zero_returns_none(self):
        assert compute_putback_from_cb(80.0, trigger_ratio=0.0) is None

    def test_trigger_ratio_negative_returns_none(self):
        assert compute_putback_from_cb(80.0, trigger_ratio=-0.5) is None

    def test_default_trigger_0_70(self):
        """默认触发比例为 0.70"""
        r1 = compute_putback_from_cb(70.0)
        r2 = compute_putback_from_cb(70.0, trigger_ratio=0.70)
        assert r1 == r2

    def test_custom_trigger_0_85(self):
        """使用 0.85 触发比例（下修触发）"""
        result = compute_putback_from_cb(85.0, trigger_ratio=0.85)
        assert result is not None
        assert abs(result) < 0.01

    def test_conv_price_ignored_for_now(self):
        """conv_price 参数目前不改变计算（预留扩展）"""
        r1 = compute_putback_from_cb(80.0)
        r2 = compute_putback_from_cb(80.0, conv_price=12.5)
        assert r1 == r2

    def test_parity_100_far_from_trigger(self):
        """parity=1.0 常规转债 → 应为较大负值"""
        result = compute_putback_from_cb(100.0, trigger_ratio=0.70)
        assert result is not None
        assert result < -0.3


# ── compute_downward_revision_probability ─────────────────


class TestDownwardRevisionProbability:
    """compute_downward_revision_probability 测试。"""

    def test_high_probability_scenario(self):
        """低 cb_value + 近到期 + 低转债价 + 有回售 + 大余额"""
        prob = compute_downward_revision_probability(
            cb_value=50.0,          # 极低 parity
            days_to_maturity=200,    # 约 7 个月
            cb_price=85.0,           # 低于面值
            has_put_clause=True,
            remain_size=80000.0,     # 大余额
        )
        assert prob > 0.65

    def test_low_probability_scenario(self):
        """高 cb_value + 远到期 + 高转债价 + 无回售"""
        prob = compute_downward_revision_probability(
            cb_value=130.0,         # 高 parity
            days_to_maturity=2000,  # 约 5.5 年
            cb_price=142.0,         # 高于面值
            has_put_clause=False,
            remain_size=5000.0,     # 小余额
        )
        assert prob < 0.35

    def test_cb_value_none_returns_zero(self):
        assert compute_downward_revision_probability(None, 365) == 0.0

    def test_cb_value_zero_returns_zero(self):
        assert compute_downward_revision_probability(0.0, 365) == 0.0

    def test_missing_days_uses_default(self):
        """缺失到期天数 → 紧迫性使用保守默认"""
        prob = compute_downward_revision_probability(60.0, None, cb_price=90.0)
        assert 0.0 < prob < 1.0

    def test_missing_cb_price_neutral(self):
        """缺失转债价格 → 空间分使用中性默认"""
        prob = compute_downward_revision_probability(60.0, 365)
        assert 0.0 < prob < 1.0

    def test_has_put_clause_increases_prob(self):
        """有回售条款 → 概率更高"""
        p_with = compute_downward_revision_probability(
            70.0, 500, cb_price=95.0, has_put_clause=True,
        )
        p_without = compute_downward_revision_probability(
            70.0, 500, cb_price=95.0, has_put_clause=False,
        )
        assert p_with > p_without

    def test_large_size_increases_prob(self):
        """大余额 → 概率更高"""
        p_large = compute_downward_revision_probability(
            70.0, 500, remain_size=80000.0,
        )
        p_small = compute_downward_revision_probability(
            70.0, 500, remain_size=5000.0,
        )
        assert p_large > p_small

    def test_values_in_range(self):
        """概率在 [0, 1] 范围内"""
        for cv in [30.0, 60.0, 80.0, 100.0, 130.0]:
            for dtm in [100, 500, 1000, 2000]:
                prob = compute_downward_revision_probability(cv, dtm)
                assert 0.0 <= prob <= 1.0, f"cv={cv}, dtm={dtm}: prob={prob}"

    def test_closer_maturity_higher_prob(self):
        """更近到期 → 更高紧迫性 → 更高概率"""
        p_near = compute_downward_revision_probability(60.0, 200)
        p_far = compute_downward_revision_probability(60.0, 1500)
        assert p_near > p_far


# ── compute_clause_play_score ─────────────────────────────


class TestClausePlayScore:
    """compute_clause_play_score 复合分数测试。"""

    def test_high_score_both_factors_high(self):
        """低 cb_value + 催化高峰期 → 高分"""
        score = compute_clause_play_score(
            cb_value=55.0,           # 接近触发线
            days_to_maturity=730,    # 2 年 = PEAK 催化
            has_put_clause=True,
        )
        assert score > 0.5

    def test_low_score_both_factors_low(self):
        """高 cb_value + 远期 → 低分"""
        score = compute_clause_play_score(
            cb_value=130.0,          # 远离触发线
            days_to_maturity=3000,   # 约 8 年 = DISTANT
            has_put_clause=False,
        )
        assert score < 0.35

    def test_cb_value_none_zero_putback(self):
        """cb_value=None → 回售分为 0，只看催化"""
        score = compute_clause_play_score(
            cb_value=None,
            days_to_maturity=730,
        )
        assert 0.0 <= score <= 1.0

    def test_days_none_low_catalyst(self):
        """days=None → 催化分使用保守默认"""
        score = compute_clause_play_score(
            cb_value=60.0,
            days_to_maturity=None,
        )
        assert 0.0 <= score <= 1.0

    def test_no_put_clause_reduces_putback(self):
        """无回售条款 → 回售驱动力打折"""
        s_with = compute_clause_play_score(
            cb_value=60.0, days_to_maturity=500,
            has_put_clause=True,
        )
        s_without = compute_clause_play_score(
            cb_value=60.0, days_to_maturity=500,
            has_put_clause=False,
        )
        assert s_with > s_without

    def test_custom_weights_putback_only(self):
        """只看回售（maturity_weight=0）"""
        score = compute_clause_play_score(
            cb_value=55.0, days_to_maturity=730,
            putback_weight=1.0, maturity_weight=0.0,
        )
        assert 0.0 <= score <= 1.0

    def test_custom_weights_maturity_only(self):
        """只看催化（putback_weight=0）"""
        score = compute_clause_play_score(
            cb_value=55.0, days_to_maturity=730,
            putback_weight=0.0, maturity_weight=1.0,
        )
        assert 0.0 <= score <= 1.0

    def test_zero_weights_returns_zero(self):
        """两权重都为0 → 返回0"""
        score = compute_clause_play_score(
            cb_value=60.0, days_to_maturity=500,
            putback_weight=0.0, maturity_weight=0.0,
        )
        assert score == 0.0

    def test_cross_bonus_when_both_high(self):
        """回售+催化均高时有交叉加分"""
        # 设置两个分数都高的场景
        score = compute_clause_play_score(
            cb_value=45.0,           # 很低 → 高回售分
            days_to_maturity=730,    # PEAK → 高催化分
            has_put_clause=True,
        )
        assert score > 0.6

    def test_values_bounded(self):
        """输出始终在 [0, 1]"""
        for cv in [30.0, 60.0, 90.0, 120.0, None]:
            for dtm in [50, 300, 700, 1500, 3000, None]:
                score = compute_clause_play_score(cv, dtm)
                assert 0.0 <= score <= 1.0, f"cv={cv}, dtm={dtm}: {score}"
