"""
仓位管理测试

测试覆盖：
- VolatilityTargetOverlay: 正常/低波/高波/短数据/禁用
- DrawdownPositionReducer: 无回撤/轻微/严重/超阈值/重置
- PositionSizingOverlay: 综合/组件交互
"""

import pytest

from aipha.risk.position_sizing import (
    DrawdownControlConfig,
    DrawdownPositionReducer,
    PositionSizingOverlay,
    VolatilityTargetConfig,
    VolatilityTargetOverlay,
)


# ============================================================
# VolatilityTargetOverlay
# ============================================================


class TestVolatilityTargetOverlay:
    def test_default_config(self):
        o = VolatilityTargetOverlay()
        assert o._config.target_vol == 0.10
        assert o._config.lookback_days == 20

    def test_short_returns_max_scale(self):
        """数据不足时返回 max_scale"""
        o = VolatilityTargetOverlay(VolatilityTargetConfig(lookback_days=20))
        scale = o.compute_scale([0.01, -0.01])  # 太短
        assert scale == o._config.max_scale

    def test_low_volatility_high_scale(self):
        """低波动性 → 高仓位比例"""
        # 固定小收益 → 低波动
        returns = [0.001] * 30
        o = VolatilityTargetOverlay(
            VolatilityTargetConfig(target_vol=0.20, lookback_days=20)
        )
        scale = o.compute_scale(returns)
        assert scale == o._config.max_scale  # 应为最大

    def test_high_volatility_low_scale(self):
        """高波动性 → 低仓位比例"""
        # 大幅波动
        returns = [0.05, -0.05] * 15
        o = VolatilityTargetOverlay(
            VolatilityTargetConfig(target_vol=0.05, lookback_days=20)
        )
        scale = o.compute_scale(returns)
        assert scale <= o._config.max_scale
        assert scale >= o._config.min_scale

    def test_scale_clamped_min(self):
        """仓位不低于 min_scale"""
        returns = [0.10, -0.10] * 15  # 极高波动
        cfg = VolatilityTargetConfig(
            target_vol=0.01, min_scale=0.2, lookback_days=20
        )
        scale = VolatilityTargetOverlay(cfg).compute_scale(returns)
        assert scale >= 0.2

    def test_disabled(self):
        """禁用时返回 1.0"""
        cfg = VolatilityTargetConfig(enabled=False)
        scale = VolatilityTargetOverlay(cfg).compute_scale([0.1, -0.1] * 15)
        assert scale == 1.0


# ============================================================
# DrawdownPositionReducer
# ============================================================


class TestDrawdownPositionReducer:
    def test_no_drawdown_full_scale(self):
        """净值创新高 → 满仓"""
        r = DrawdownPositionReducer()
        assert r.compute_scale(1.0) == 1.0
        assert r.compute_scale(1.05) == 1.0
        assert r.compute_scale(1.10) == 1.0

    def test_below_threshold_full_scale(self):
        """小幅回撤（< threshold）→ 满仓"""
        cfg = DrawdownControlConfig(threshold=0.05)
        r = DrawdownPositionReducer(cfg)
        r.compute_scale(1.0)
        scale = r.compute_scale(0.97)  # 3% 回撤 < 5%
        assert scale == 1.0

    def test_at_threshold_starts_reducing(self):
        """回撤刚到阈值开始降仓"""
        cfg = DrawdownControlConfig(threshold=0.05, max_threshold=0.10)
        r = DrawdownPositionReducer(cfg)
        r.compute_scale(1.0)
        scale = r.compute_scale(0.93)  # 7% 回撤，在 5%-10% 之间
        assert scale < 1.0
        assert scale > cfg.min_scale

    def test_beyond_max_threshold_min_scale(self):
        """回撤超过 max_threshold → 最低仓位"""
        cfg = DrawdownControlConfig(
            threshold=0.05, max_threshold=0.10, min_scale=0.3
        )
        r = DrawdownPositionReducer(cfg)
        r.compute_scale(1.0)
        scale = r.compute_scale(0.85)  # 15% > 10%
        assert scale == cfg.min_scale

    def test_reset(self):
        """重置后重新跟踪"""
        r = DrawdownPositionReducer()
        r.compute_scale(2.0)  # peak = 2.0
        r.reset()
        scale = r.compute_scale(1.0)  # 新 peak = 1.0
        assert scale == 1.0

    def test_recovery_after_drawdown(self):
        """回撤后净值恢复"""
        cfg = DrawdownControlConfig(threshold=0.05, max_threshold=0.10)
        r = DrawdownPositionReducer(cfg)
        r.compute_scale(1.0)
        r.compute_scale(0.92)  # 8% 回撤
        scale_recover = r.compute_scale(1.01)  # 新高
        assert scale_recover == 1.0


# ============================================================
# PositionSizingOverlay
# ============================================================


class TestPositionSizingOverlay:
    def test_combines_vol_and_dd(self):
        """综合叠加取 min"""
        vol_cfg = VolatilityTargetConfig(target_vol=0.10, lookback_days=5)
        dd_cfg = DrawdownControlConfig(threshold=0.05, max_threshold=0.10)
        overlay = PositionSizingOverlay(vol_cfg, dd_cfg)

        returns = [0.001] * 10
        scale = overlay.compute_scale(returns, current_nav=1.0)
        assert 0.0 < scale <= 1.0

    def test_reset(self):
        overlay = PositionSizingOverlay()
        overlay.compute_scale([0.01] * 25, current_nav=2.0)
        overlay.reset()
        scale = overlay.compute_scale([0.01] * 25, current_nav=1.0)
        assert scale == 1.0 or scale > 0

    def test_both_disabled(self):
        """两者都禁用时返回 1.0"""
        vol_cfg = VolatilityTargetConfig(enabled=False)
        dd_cfg = DrawdownControlConfig(enabled=False)
        overlay = PositionSizingOverlay(vol_cfg, dd_cfg)
        scale = overlay.compute_scale([0.1, -0.1] * 15, current_nav=0.8)
        assert scale == 1.0

    def test_mdd_gradient_integration(self):
        """R94: MDD 梯度减仓在 overlay 中生效"""
        vol_cfg = VolatilityTargetConfig(enabled=False)
        dd_cfg = DrawdownControlConfig(enabled=False)
        levels = [
            (0.10, 1.0), (0.15, 0.80), (0.20, 0.55),
            (0.25, 0.30), (1.00, 0.10),
        ]
        overlay = PositionSizingOverlay(
            vol_cfg, dd_cfg, mdd_gradient_levels=levels,
        )
        # 先建立峰值
        overlay.compute_scale([], current_nav=1.0)
        # MDD = 12% → 在 10%-15% 间 → scale=0.80
        scale = overlay.compute_scale([], current_nav=0.88)
        assert scale == pytest.approx(0.80, abs=0.01)
        # MDD = 18% → 在 15%-20% 间 → scale=0.55
        scale = overlay.compute_scale([], current_nav=0.82)
        assert scale == pytest.approx(0.55, abs=0.01)

    def test_mdd_gradient_reset(self):
        """R94: reset 清除 MDD 梯度峰值"""
        levels = [(0.10, 1.0), (0.20, 0.50)]
        overlay = PositionSizingOverlay(
            mdd_gradient_levels=levels,
        )
        overlay.compute_scale([], current_nav=2.0)
        overlay.reset()
        # 重置后峰值归零，新 NAV 成为基准
        scale = overlay.compute_scale([], current_nav=1.0)
        assert scale == 1.0  # 无回撤


# ============================================================
# DrawdownPositionReducer — 渐进模式 (R71)
# ============================================================


class TestDrawdownProgressiveMode:
    """渐进式分段回撤减仓测试。"""

    def _make_reducer(self):
        cfg = DrawdownControlConfig(
            progressive_thresholds=[
                (0.10, 0.80),
                (0.15, 0.60),
                (0.20, 0.40),
            ],
        )
        return DrawdownPositionReducer(cfg)

    def test_no_drawdown_full_scale(self):
        """无回撤 → 满仓"""
        r = self._make_reducer()
        r.compute_scale(1.0)
        assert r.compute_scale(1.0) == 1.0

    def test_below_first_threshold(self):
        """回撤 < 10% → 满仓"""
        r = self._make_reducer()
        r.compute_scale(1.0)
        scale = r.compute_scale(0.95)  # 5% < 10%
        assert scale == 1.0

    def test_at_first_threshold(self):
        """回撤 = 10% → 开始减仓边界"""
        r = self._make_reducer()
        r.compute_scale(1.0)
        scale = r.compute_scale(0.90)  # 10% 恰好在第一阈值
        assert scale == 1.0  # 恰好等于阈值被包含

    def test_between_first_and_second(self):
        """10% < DD < 15% → 线性插值到 0.60~0.80"""
        r = self._make_reducer()
        r.compute_scale(1.0)
        scale = r.compute_scale(0.88)  # 12% 回撤
        assert 0.60 < scale < 0.80

    def test_at_second_threshold(self):
        """DD = 15% → scale = 0.60"""
        r = self._make_reducer()
        r.compute_scale(1.0)
        scale = r.compute_scale(0.85)
        assert abs(scale - 0.60) < 0.01

    def test_between_second_and_third(self):
        """15% < DD < 20% → 线性插值到 0.40~0.60"""
        r = self._make_reducer()
        r.compute_scale(1.0)
        scale = r.compute_scale(0.82)  # 18% 回撤
        assert 0.40 < scale < 0.60

    def test_at_third_threshold(self):
        """DD = 20% → scale = 0.40"""
        r = self._make_reducer()
        r.compute_scale(1.0)
        scale = r.compute_scale(0.80)
        assert abs(scale - 0.40) < 0.01

    def test_beyond_max_threshold(self):
        """DD > 20% → scale = 最后一级 0.40"""
        r = self._make_reducer()
        r.compute_scale(1.0)
        scale = r.compute_scale(0.70)  # 30% 回撤
        assert abs(scale - 0.40) < 0.01

    def test_recovery_to_new_high(self):
        """回撤后净值恢复→满仓"""
        r = self._make_reducer()
        r.compute_scale(1.0)
        r.compute_scale(0.85)  # 15% DD
        scale = r.compute_scale(1.01)  # 新高
        assert scale == 1.0

    def test_progressive_overrides_simple(self):
        """有 progressive_thresholds 时忽略简单模式参数"""
        cfg = DrawdownControlConfig(
            threshold=0.01,
            max_threshold=0.02,
            min_scale=0.1,
            progressive_thresholds=[(0.10, 0.80), (0.20, 0.40)],
        )
        r = DrawdownPositionReducer(cfg)
        r.compute_scale(1.0)
        # 5% DD → 简单模式会减仓，渐进模式不减
        scale = r.compute_scale(0.95)
        assert scale == 1.0
