"""Tests for aipha.risk.ml_risk_controls — 公共风控模块。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aipha.risk.ml_risk_controls import (
    CombinedPositionSizer,
    IndustryConstraint,
    LinearMddController,
    MddGradientDeleverager,
    SharpeDownweightTrigger,
    VolTargetScaler,
)


# ============================================================================
# MddGradientDeleverager
# ============================================================================


class TestMddGradientDeleverager:
    """MDD 梯度减仓器测试。"""

    def test_default_levels_full_position(self):
        d = MddGradientDeleverager()
        assert d.get_scale(0.0) == 1.0
        assert d.get_scale(0.05) == 1.0
        assert d.get_scale(0.099) == 1.0

    def test_mdd_10_to_15(self):
        d = MddGradientDeleverager()
        assert d.get_scale(0.10) == 0.80
        assert d.get_scale(0.12) == 0.80
        assert d.get_scale(0.149) == 0.80

    def test_mdd_15_to_20(self):
        d = MddGradientDeleverager()
        assert d.get_scale(0.15) == 0.55
        assert d.get_scale(0.19) == 0.55

    def test_mdd_20_to_25(self):
        d = MddGradientDeleverager()
        assert d.get_scale(0.20) == 0.30
        assert d.get_scale(0.24) == 0.30

    def test_mdd_above_25(self):
        d = MddGradientDeleverager()
        assert d.get_scale(0.25) == 0.10
        assert d.get_scale(0.50) == 0.10

    def test_custom_levels(self):
        d = MddGradientDeleverager(levels=[(0.05, 0.5), (1.0, 0.0)])
        assert d.get_scale(0.01) == 0.5
        assert d.get_scale(0.05) == 0.0

    def test_compute_mdd_empty(self):
        d = MddGradientDeleverager()
        assert d.compute_mdd(pd.Series(dtype=float)) == 0.0

    def test_compute_mdd_no_drawdown(self):
        d = MddGradientDeleverager()
        nav = pd.Series([100, 101, 102, 103])
        assert d.compute_mdd(nav) == 0.0

    def test_compute_mdd_with_drawdown(self):
        d = MddGradientDeleverager()
        nav = pd.Series([100, 110, 100, 105])
        mdd = d.compute_mdd(nav)
        # peak=110, current=105, mdd = (110-105)/110 = 5/110
        assert abs(mdd - 5.0 / 110.0) < 1e-6


# ============================================================================
# VolTargetScaler
# ============================================================================


class TestVolTargetScaler:
    """波动率目标缩放器测试。"""

    def test_short_series_returns_1(self):
        v = VolTargetScaler(lookback=60)
        nav = pd.Series(range(10), dtype=float)
        assert v.get_scale(nav) == 1.0

    def test_low_vol_scales_up(self):
        np.random.seed(42)
        rets = np.random.normal(0.0005, 0.005, 100)
        nav = pd.Series(np.cumprod(1 + rets) * 100)
        v = VolTargetScaler(vol_target=0.20, lookback=60)
        scale = v.get_scale(nav)
        assert scale > 1.0  # low vol -> scale up

    def test_high_vol_scales_down(self):
        np.random.seed(42)
        rets = np.random.normal(0.0, 0.03, 100)
        nav = pd.Series(np.cumprod(1 + rets) * 100)
        v = VolTargetScaler(vol_target=0.10, lookback=60)
        scale = v.get_scale(nav)
        assert scale < 1.0  # high vol -> scale down

    def test_clip_bounds(self):
        v = VolTargetScaler(min_scale=0.2, max_scale=1.5)
        # Zero vol should return 1.0
        nav = pd.Series([100.0] * 100)
        assert v.get_scale(nav) == 1.0


# ============================================================================
# IndustryConstraint
# ============================================================================


class TestIndustryConstraint:
    """行业中性约束测试。"""

    def test_basic_constraint(self):
        ic = IndustryConstraint(max_pct=0.20)
        candidates = ["A", "B", "C", "D", "E"]
        ind_map = {"A": "银行", "B": "银行", "C": "电子",
                   "D": "银行", "E": "医药"}
        filtered, blocked = ic.apply(candidates, ind_map, top_k=5)
        # max_per_industry = max(1, int(5 * 0.20)) = 1
        # 银行: A passes, B blocked, D blocked = 2 blocked
        assert "B" not in filtered
        assert "D" not in filtered
        assert blocked == 2

    def test_unknown_industry(self):
        ic = IndustryConstraint(max_pct=0.50)
        candidates = ["A", "B"]
        ind_map = {}  # all unknown
        filtered, blocked = ic.apply(candidates, ind_map, top_k=5)
        assert len(filtered) == 2
        assert blocked == 0

    def test_top_k_limit(self):
        ic = IndustryConstraint(max_pct=1.0)
        candidates = list("ABCDEFGH")
        ind_map = {c: f"ind_{c}" for c in candidates}
        filtered, blocked = ic.apply(candidates, ind_map, top_k=3)
        assert len(filtered) == 3


# ============================================================================
# CombinedPositionSizer
# ============================================================================


class TestCombinedPositionSizer:
    """综合仓位缩放器测试。"""

    def test_no_drawdown_full_scale(self):
        nav = pd.Series([100, 101, 102, 103, 104], dtype=float)
        sizer = CombinedPositionSizer()
        result = sizer.compute_scale(nav)
        assert result["final_scale"] == 1.0
        assert result["mdd_triggered"] is False

    def test_mdd_triggered(self):
        nav = pd.Series([100, 110, 90, 85], dtype=float)
        sizer = CombinedPositionSizer()
        result = sizer.compute_scale(nav)
        assert result["mdd_triggered"] is True
        assert result["mdd_scale"] < 1.0


# ============================================================================
# SharpeDownweightTrigger (B1)
# ============================================================================


class TestSharpeDownweightTrigger:
    """Rolling SR 降权触发器测试。"""

    def test_insufficient_data_returns_nan(self):
        """数据不足时 SR = NaN，不触发降权。"""
        t = SharpeDownweightTrigger(lookback=60)
        nav = pd.Series(range(30), dtype=float) + 100
        sr = t.compute_rolling_sr(nav)
        assert np.isnan(sr)

    def test_insufficient_data_no_trigger(self):
        """数据不足时 evaluate 返回 normal_weight。"""
        t = SharpeDownweightTrigger(lookback=60, normal_weight=0.30)
        nav = pd.Series(range(30), dtype=float) + 100
        weight, info = t.evaluate(nav)
        assert weight == 0.30
        assert not info["triggered"]

    def test_positive_sr_no_trigger(self):
        """正 SR 时不触发降权。"""
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.01, 100)
        nav = pd.Series(np.cumprod(1 + rets) * 100)
        t = SharpeDownweightTrigger(lookback=60, sr_threshold=0.0)
        weight, info = t.evaluate(nav)
        assert info["rolling_sr_60d"] is not None
        assert info["rolling_sr_60d"] > 0
        assert not info["triggered"]
        assert weight == t.normal_weight

    def test_negative_sr_triggers_downweight(self):
        """负 SR 时触发降权。"""
        np.random.seed(42)
        rets = np.random.normal(-0.003, 0.01, 100)
        nav = pd.Series(np.cumprod(1 + rets) * 100)
        t = SharpeDownweightTrigger(
            lookback=60,
            sr_threshold=0.0,
            normal_weight=0.30,
            reduced_weight=0.20,
        )
        weight, info = t.evaluate(nav)
        assert info["rolling_sr_60d"] < 0
        assert info["triggered"]
        assert weight == 0.20

    def test_custom_threshold(self):
        """自定义 SR 阈值：SR < 0.5 触发。"""
        np.random.seed(42)
        # 微弱正收益，SR 接近 0
        rets = np.random.normal(0.0001, 0.01, 100)
        nav = pd.Series(np.cumprod(1 + rets) * 100)
        t = SharpeDownweightTrigger(
            lookback=60,
            sr_threshold=0.5,
            normal_weight=0.30,
            reduced_weight=0.15,
        )
        weight, info = t.evaluate(nav)
        # SR 应该接近 0，小于 0.5
        assert info["triggered"]
        assert weight == 0.15

    def test_zero_volatility_returns_zero_sr(self):
        """零波动率时 SR = 0。"""
        t = SharpeDownweightTrigger(lookback=60)
        nav = pd.Series([100.0] * 100)
        sr = t.compute_rolling_sr(nav)
        # pct_change of constant series = 0, std = 0 → return 0.0
        assert sr == 0.0

    def test_exactly_at_threshold(self):
        """SR 恰好等于阈值时不触发（严格小于）。"""
        t = SharpeDownweightTrigger(lookback=60, sr_threshold=0.0)
        # Zero SR is AT threshold, not below → should not trigger
        nav = pd.Series([100.0] * 100)
        weight, info = t.evaluate(nav)
        assert not info["triggered"]
        assert weight == t.normal_weight

    def test_diagnostic_info_structure(self):
        """诊断信息包含所有必要字段。"""
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.01, 100)
        nav = pd.Series(np.cumprod(1 + rets) * 100)
        t = SharpeDownweightTrigger(lookback=60)
        _, info = t.evaluate(nav)
        assert "rolling_sr_60d" in info
        assert "sr_threshold" in info
        assert "triggered" in info
        assert "weight" in info
        assert "normal_weight" in info
        assert "reduced_weight" in info

    def test_annualization_factor(self):
        """自定义年化因子影响 SR 计算。"""
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.01, 100)
        nav = pd.Series(np.cumprod(1 + rets) * 100)
        t1 = SharpeDownweightTrigger(lookback=60, annualization=244)
        t2 = SharpeDownweightTrigger(lookback=60, annualization=52)
        sr1 = t1.compute_rolling_sr(nav)
        sr2 = t2.compute_rolling_sr(nav)
        # 244 trading days → higher annualized SR than 52 weeks
        assert sr1 > sr2


class TestLinearMddController:
    def test_normal_full_scale(self):
        c = LinearMddController(mdd_reduce=0.12, mdd_exit=0.22)
        c.reset(1000.0)
        assert c.update(1000.0) == (1.0, "normal")

    def test_nav_increase(self):
        c = LinearMddController(mdd_reduce=0.12, mdd_exit=0.22)
        c.reset(1000.0)
        assert c.update(1050.0) == (1.0, "normal")

    def test_reducing_state(self):
        c = LinearMddController(mdd_reduce=0.10, mdd_exit=0.20)
        c.reset(1000.0)
        scale, st = c.update(850.0)
        assert st == "reducing"
        assert 0.4 < scale < 0.6

    def test_exit_state(self):
        c = LinearMddController(mdd_reduce=0.10, mdd_exit=0.20)
        c.reset(1000.0)
        scale, st = c.update(800.0)
        assert scale == 0.0 and st == "exited"

    def test_cooldown_sequence(self):
        c = LinearMddController(
            mdd_reduce=0.10, mdd_exit=0.20, cooldown_periods=3
        )
        c.reset(1000.0)
        c.update(750.0)  # exit
        assert c.update(750.0) == (0.0, "exited")
        assert c.update(760.0) == (0.0, "exited")
        s, st = c.update(770.0)
        assert st == "recovered" and s == 1.0

    def test_recovery_resets_peak(self):
        c = LinearMddController(
            mdd_reduce=0.10, mdd_exit=0.20, cooldown_periods=2
        )
        c.reset(1000.0)
        c.update(750.0)
        c.update(760.0)
        c.update(770.0)  # recovered, peak=770
        assert c.update(750.0) == (1.0, "normal")

    def test_state_property(self):
        c = LinearMddController()
        c.reset(1000.0)
        c.update(1000.0)
        assert c.state == "normal"
        c.update(750.0)
        assert c.state == "exited"

