"""tests for vol_targeting module"""

from __future__ import annotations

import numpy as np
import pytest

from aipha.portfolio.vol_targeting import (
    VolatilityTargeter,
    VolTargetConfig,
    VolTargetState,
)


class TestVolTargetState:
    def test_to_dict(self):
        s = VolTargetState(current_scale=0.8, realized_vol=0.20,
                           target_vol=0.15, n_observations=30)
        d = s.to_dict()
        assert d["current_scale"] == 0.8
        assert d["realized_vol"] == 0.2
        assert d["n_observations"] == 30

    def test_defaults(self):
        s = VolTargetState()
        assert s.current_scale == 1.0
        assert s.realized_vol == 0.0


class TestVolatilityTargeter:
    def test_initial_state(self):
        vt = VolatilityTargeter()
        assert vt.get_scale() == 1.0
        assert vt.state.n_observations == 0

    def test_insufficient_data_returns_1(self):
        vt = VolatilityTargeter(VolTargetConfig(lookback=20))
        for _ in range(4):
            scale = vt.update(0.01)
        assert scale == 1.0  # < min(5, lookback//2)

    def test_low_vol_scales_up(self):
        """低波动 → 放大仓位"""
        cfg = VolTargetConfig(target_vol=0.20, lookback=20, scale_cap=1.5)
        vt = VolatilityTargeter(cfg)
        # 生成低波动序列 (年化 ~5%)
        rng = np.random.default_rng(42)
        for _ in range(30):
            ret = rng.normal(0.0, 0.003)  # daily vol ~0.3% → annual ~5%
            vt.update(ret)
        assert vt.get_scale() > 1.0
        assert vt.state.realized_vol < 0.20

    def test_high_vol_scales_down(self):
        """高波动 → 缩小仓位"""
        cfg = VolTargetConfig(target_vol=0.15, lookback=20, scale_floor=0.3)
        vt = VolatilityTargeter(cfg)
        # 生成高波动序列 (年化 ~40%)
        rng = np.random.default_rng(7)
        for _ in range(30):
            ret = rng.normal(0.0, 0.025)  # daily vol ~2.5% → annual ~40%
            vt.update(ret)
        assert vt.get_scale() < 1.0
        assert vt.state.realized_vol > 0.15

    def test_scale_floor_respected(self):
        """极高波动时scale不低于floor"""
        cfg = VolTargetConfig(target_vol=0.10, lookback=10, scale_floor=0.3)
        vt = VolatilityTargeter(cfg)
        # 极端高波
        for _ in range(15):
            vt.update(0.05 * (1 if _ % 2 == 0 else -1))
        assert vt.get_scale() >= 0.3

    def test_scale_cap_respected(self):
        """极低波动时scale不超过cap"""
        cfg = VolTargetConfig(target_vol=0.30, lookback=10, scale_cap=1.5)
        vt = VolatilityTargeter(cfg)
        for _ in range(15):
            vt.update(0.0001)  # 几乎无波动
        assert vt.get_scale() <= 1.5

    def test_ewm_mode(self):
        """指数加权模式正常工作"""
        cfg = VolTargetConfig(
            target_vol=0.15, lookback=20,
            use_ewm=True, ewm_halflife=10,
        )
        vt = VolatilityTargeter(cfg)
        rng = np.random.default_rng(99)
        for _ in range(30):
            vt.update(rng.normal(0, 0.015))
        assert 0.3 <= vt.get_scale() <= 1.5

    def test_scale_weights(self):
        """scale_weights正确缩放权重"""
        vt = VolatilityTargeter(VolTargetConfig(target_vol=0.10, lookback=10))
        # 先填充足够数据
        rng = np.random.default_rng(5)
        for _ in range(15):
            vt.update(rng.normal(0, 0.02))
        weights = {"A": 0.5, "B": 0.3, "C": 0.2}
        scaled = vt.scale_weights(weights, 0.01)
        scale = vt.get_scale()
        assert abs(scaled["A"] - 0.5 * scale) < 1e-6
        assert abs(scaled["B"] - 0.3 * scale) < 1e-6
        # 总和 < 1.0 (差额=现金)
        assert sum(scaled.values()) <= 1.0 + 1e-6

    def test_reset(self):
        vt = VolatilityTargeter()
        for _ in range(20):
            vt.update(0.01)
        vt.reset()
        assert vt.state.n_observations == 0
        assert vt.get_scale() == 1.0
