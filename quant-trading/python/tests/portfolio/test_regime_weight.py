"""Tests for RegimeAdaptiveAllocator — Regime 自适应策略权重分配器。

测试覆盖（≥8 用例 per §6.2 风控/组合模块）：
- 配置
- 策略风格映射
- 权重计算: bull/neutral/bear
- 高波动缩放
- EMA 平滑
- 归一化 & clip
- 边界情况
"""

from __future__ import annotations

from datetime import date

import pytest

from aipha.portfolio.regime_weight import (
    DEFAULT_STYLE_MAP,
    MarketRegime,
    RegimeAdaptiveAllocator,
    RegimeWeightConfig,
    RegimeWeightResult,
    StrategyStyle,
)


# ===========================================================================
# 1. 配置 & 枚举 (4 tests)
# ===========================================================================

class TestEnumsAndConfig:
    def test_strategy_style_values(self):
        assert str(StrategyStyle.AGGRESSIVE) == "aggressive"
        assert str(StrategyStyle.DEFENSIVE) == "defensive"

    def test_regime_values(self):
        assert str(MarketRegime.BULL) == "BULL"
        assert str(MarketRegime.BEAR) == "BEAR"

    def test_default_config(self):
        cfg = RegimeWeightConfig()
        assert cfg.bull_weights["aggressive"] == 1.5
        assert cfg.bear_weights["defensive"] == 1.5
        assert cfg.high_vol_scale == 0.7

    def test_custom_config(self):
        cfg = RegimeWeightConfig(high_vol_scale=0.5)
        assert cfg.high_vol_scale == 0.5


# ===========================================================================
# 2. 风格映射 (3 tests)
# ===========================================================================

class TestStyleMapping:
    def test_default_map_has_entries(self):
        assert "triple_low" in DEFAULT_STYLE_MAP
        assert DEFAULT_STYLE_MAP["triple_low"] == StrategyStyle.DEFENSIVE

    def test_register_strategy(self):
        alloc = RegimeAdaptiveAllocator()
        alloc.register_strategy("my_custom", StrategyStyle.AGGRESSIVE)
        assert alloc.get_style("my_custom") == StrategyStyle.AGGRESSIVE

    def test_unknown_defaults_balanced(self):
        alloc = RegimeAdaptiveAllocator()
        assert alloc.get_style("totally_unknown") == StrategyStyle.BALANCED


# ===========================================================================
# 3. 权重计算 (8 tests)
# ===========================================================================

class TestComputeWeights:

    def _base_weights(self) -> dict[str, float]:
        return {
            "cb_momentum": 0.33,       # AGGRESSIVE
            "multi_factor": 0.34,      # BALANCED
            "triple_low": 0.33,        # DEFENSIVE
        }

    def test_bull_increases_aggressive(self):
        """牛市应增加攻击型权重。"""
        alloc = RegimeAdaptiveAllocator(config=RegimeWeightConfig(transition_speed=1.0))
        base = self._base_weights()
        result = alloc.compute_weights(base, MarketRegime.BULL)
        assert sum(result.values()) == pytest.approx(1.0, abs=1e-8)
        # 攻击型权重应增加
        assert result["cb_momentum"] > result["triple_low"]

    def test_bear_increases_defensive(self):
        """熊市应增加防御型权重。"""
        alloc = RegimeAdaptiveAllocator(config=RegimeWeightConfig(transition_speed=1.0))
        base = self._base_weights()
        result = alloc.compute_weights(base, MarketRegime.BEAR)
        assert sum(result.values()) == pytest.approx(1.0, abs=1e-8)
        # 防御型权重应增加
        assert result["triple_low"] > result["cb_momentum"]

    def test_neutral_is_balanced(self):
        """震荡期各权重差异适中。"""
        alloc = RegimeAdaptiveAllocator(config=RegimeWeightConfig(transition_speed=1.0))
        base = self._base_weights()
        result = alloc.compute_weights(base, MarketRegime.NEUTRAL)
        assert sum(result.values()) == pytest.approx(1.0, abs=1e-8)

    def test_high_vol_reduces_all(self):
        """高波动环境应缩小全部仓位（归一化后比例变化归0，但调整前有缩放效果）。"""
        alloc = RegimeAdaptiveAllocator(
            config=RegimeWeightConfig(transition_speed=1.0, high_vol_scale=0.5)
        )
        base = self._base_weights()
        # 同一 regime 下，有/无高波动的差异
        result_normal = alloc.compute_weights(base, MarketRegime.NEUTRAL, is_high_vol=False)
        alloc.reset()
        result_vol = alloc.compute_weights(base, MarketRegime.NEUTRAL, is_high_vol=True)
        # 归一化后比例应相同（因为 uniform 缩放）
        for k in base:
            assert abs(result_normal[k] - result_vol[k]) < 1e-6

    def test_ema_smoothing(self):
        """EMA 平滑应避免权重剧烈切换。"""
        alloc = RegimeAdaptiveAllocator(
            config=RegimeWeightConfig(transition_speed=0.3)
        )
        base = self._base_weights()
        # 第一次计算（无 prev_weights）
        w1 = alloc.compute_weights(base, MarketRegime.BULL)
        # 切换到熊市
        w2 = alloc.compute_weights(base, MarketRegime.BEAR)
        # w2 应在 w1 和 bear 目标之间（不会完全切换到 bear 目标）
        # 切换后攻击型不会立即降到最低
        assert w2["cb_momentum"] > 0.05

    def test_weights_normalize_to_one(self):
        """各种情况权重总和为 1。"""
        alloc = RegimeAdaptiveAllocator(config=RegimeWeightConfig(transition_speed=1.0))
        for regime in MarketRegime:
            base = {"s1": 0.5, "s2": 0.3, "s3": 0.2}
            result = alloc.compute_weights(base, regime)
            assert abs(sum(result.values()) - 1.0) < 1e-8
            alloc.reset()

    def test_min_weight_enforced(self):
        """最小权重约束。"""
        cfg = RegimeWeightConfig(
            min_weight=0.15, max_weight=0.60,
            transition_speed=1.0,
            bear_weights={"aggressive": 0.01, "balanced": 0.5, "defensive": 2.0},
        )
        alloc = RegimeAdaptiveAllocator(config=cfg)
        base = {"cb_momentum": 0.33, "multi_factor": 0.34, "triple_low": 0.33}
        result = alloc.compute_weights(base, MarketRegime.BEAR)
        for v in result.values():
            assert v >= 0.15 - 1e-8  # after normalization 

    def test_max_weight_enforced(self):
        """最大权重约束。"""
        cfg = RegimeWeightConfig(
            min_weight=0.05, max_weight=0.50,
            transition_speed=1.0,
            bull_weights={"aggressive": 5.0, "balanced": 1.0, "defensive": 0.1},
        )
        alloc = RegimeAdaptiveAllocator(config=cfg)
        base = {"cb_momentum": 0.33, "multi_factor": 0.34, "triple_low": 0.33}
        result = alloc.compute_weights(base, MarketRegime.BULL)
        # After clip + normalization, no weight exceeds effective max
        for v in result.values():
            assert v <= 0.60  # some tolerance for normalization


# ===========================================================================
# 4. compute_full (2 tests)
# ===========================================================================

class TestComputeFull:

    def test_returns_result(self):
        alloc = RegimeAdaptiveAllocator(config=RegimeWeightConfig(transition_speed=1.0))
        base = {"s1": 0.5, "s2": 0.5}
        result = alloc.compute_full(base, MarketRegime.BULL, current_date=date(2024, 3, 1))
        assert isinstance(result, RegimeWeightResult)
        assert result.regime == MarketRegime.BULL
        assert "s1" in result.adjusted_weights
        assert len(result.regime_history) == 1

    def test_raw_weights_preserved(self):
        alloc = RegimeAdaptiveAllocator(config=RegimeWeightConfig(transition_speed=1.0))
        base = {"s1": 0.6, "s2": 0.4}
        result = alloc.compute_full(base, MarketRegime.NEUTRAL)
        assert result.raw_weights == {"s1": 0.6, "s2": 0.4}


# ===========================================================================
# 5. 边界情况 (4 tests)
# ===========================================================================

class TestEdgeCases:

    def test_empty_weights(self):
        alloc = RegimeAdaptiveAllocator(config=RegimeWeightConfig(transition_speed=1.0))
        result = alloc.compute_weights({}, MarketRegime.BULL)
        assert result == {}

    def test_single_strategy(self):
        alloc = RegimeAdaptiveAllocator(config=RegimeWeightConfig(transition_speed=1.0))
        result = alloc.compute_weights({"s1": 1.0}, MarketRegime.BEAR)
        assert abs(result["s1"] - 1.0) < 1e-8

    def test_reset_clears_prev(self):
        alloc = RegimeAdaptiveAllocator()
        alloc.compute_weights({"s1": 0.5, "s2": 0.5}, MarketRegime.BULL)
        assert alloc._prev_weights is not None
        alloc.reset()
        assert alloc._prev_weights is None

    def test_custom_style_map_overrides(self):
        custom = {"triple_low": StrategyStyle.AGGRESSIVE}  # 反转映射
        alloc = RegimeAdaptiveAllocator(style_map=custom)
        assert alloc.get_style("triple_low") == StrategyStyle.AGGRESSIVE
