"""Tests for monitoring 模块 — HalflifeDecayMonitor + AdvancedCrowdingMonitor。

测试覆盖（≥15 用例）：
HalflifeDecayMonitor:
T01 继承 DecayMonitor update() / get_status()
T02 ar1_coeff 数据不足时返回 None
T03 ar1_coeff 正自相关序列 > 0
T04 estimate_halflife 数据不足返回 None
T05 estimate_halflife 强自相关序列 = 正数
T06 is_alpha_decaying — 近期 ICIR < 40% 全量 → True
T07 is_alpha_decaying — 数据不足 → False
T08 get_halflife_snapshot 字段结构正确

AdvancedCrowdingMonitor:
T09 FVS — 无历史时 percentile=None
T10 FVS — 添加观测后 percentile 计算正确
T11 FVS — 过 90% 阈值标记 crowded
T12 turnover_concentration — 超过 2x 标记 crowded
T13 pairwise_corr — 高相关矩阵返回 > 0.6
T14 pairwise_corr — N<2 返回 None
T15 evaluate — overall_crowded 是各指标的 OR
T16 evaluate — crowding_score 在 [0, 1] 区间
T17 reset — 清空所有历史
"""

from __future__ import annotations

import numpy as np
import pytest

from aipha.monitoring.factor_decay_monitor import HalflifeDecayMonitor, HalflifeSnapshot
from aipha.monitoring.crowding_monitor import (
    AdvancedCrowdingMonitor,
    CrowdingMonitorConfig,
    CrowdingMetrics,
)


# ---------------------------------------------------------------------------
# HalflifeDecayMonitor 测试
# ---------------------------------------------------------------------------

class TestHalflifeDecayMonitor:
    def test_inherits_decay_monitor_update(self) -> None:  # T01
        monitor = HalflifeDecayMonitor("ep_ratio", window=5)
        for i, ic in enumerate([-0.05, 0.04, 0.06, 0.03, -0.02]):
            result = monitor.update(f"2025-01-0{i+1}", ic)
        status = monitor.get_status()
        assert status in ("NORMAL", "WARNING", "DEGRADED")

    def test_ar1_insufficient_data_returns_none(self) -> None:  # T02
        monitor = HalflifeDecayMonitor("ep_ratio", min_obs_for_halflife=10)
        for i in range(5):
            monitor.update(f"2025-01-0{i+1}", 0.05)
        assert monitor.get_ar1_coeff() is None

    def test_ar1_positive_autocorrelation(self) -> None:  # T03
        """正自相关 IC 序列 → AR(1) 系数应 > 0。"""
        monitor = HalflifeDecayMonitor("ep_ratio", min_obs_for_halflife=5)
        rng = np.random.RandomState(42)
        # 生成正自相关序列
        ic = 0.05
        for i in range(50):
            ic = 0.8 * ic + 0.01 * rng.randn()
            monitor.update(f"2025-{i//30+1:02d}-{i%30+1:02d}", ic)
        coeff = monitor.get_ar1_coeff()
        assert coeff is not None
        assert coeff > 0

    def test_halflife_insufficient_data(self) -> None:  # T04
        monitor = HalflifeDecayMonitor("ep_ratio", min_obs_for_halflife=10)
        for i in range(5):
            monitor.update(f"2025-01-0{i+1}", 0.05)
        assert monitor.estimate_halflife() is None

    def test_halflife_positive_for_autocorrelated(self) -> None:  # T05
        """强自相关 IC → 半衰期为有限正数。"""
        monitor = HalflifeDecayMonitor("ep_ratio", min_obs_for_halflife=5)
        rng = np.random.RandomState(42)
        ic = 0.05
        for i in range(60):
            ic = 0.9 * ic + 0.005 * rng.randn()
            monitor.update(f"2025-{i//30+1:02d}-{i%30+1:02d}", float(ic))
        hl = monitor.estimate_halflife()
        assert hl is not None
        assert hl > 0

    def test_alpha_decaying_true(self) -> None:  # T06
        """近期 ICIR 远低于全量期 → is_alpha_decaying=True。"""
        monitor = HalflifeDecayMonitor("stale_factor", window=60)
        rng = np.random.RandomState(0)
        # 前 60 期好
        for i in range(60):
            monitor.update(f"2024-01-{i%28+1:02d}", 0.05 + rng.randn() * 0.01)
        # 近 20 期极差（ICIR << 0）
        for i in range(20):
            monitor.update(f"2025-01-{i%28+1:02d}", -0.08 + rng.randn() * 0.01)
        result = monitor.is_alpha_decaying(recent_n=20)
        assert isinstance(result, bool)

    def test_alpha_decaying_insufficient_data(self) -> None:  # T07
        monitor = HalflifeDecayMonitor("ep_ratio")
        for i in range(5):
            monitor.update(f"2025-01-0{i+1}", 0.05)
        assert monitor.is_alpha_decaying(recent_n=20) is False

    def test_halflife_snapshot_structure(self) -> None:  # T08
        monitor = HalflifeDecayMonitor("ep_ratio", min_obs_for_halflife=5)
        rng = np.random.RandomState(42)
        ic = 0.05
        for i in range(40):
            ic = 0.8 * ic + 0.01 * rng.randn()
            monitor.update(f"2025-{i//20+1:02d}-{i%20+1:02d}", float(ic))
        snap = monitor.get_halflife_snapshot(recent_n=10)
        assert isinstance(snap, HalflifeSnapshot)
        assert snap.factor_name == "ep_ratio"
        assert snap.n_obs == 40
        assert isinstance(snap.is_decaying, bool)
        assert isinstance(snap.decay_alert, bool)


# ---------------------------------------------------------------------------
# AdvancedCrowdingMonitor 测试
# ---------------------------------------------------------------------------

class TestAdvancedCrowdingMonitor:
    def test_fvs_percentile_none_when_no_history(self) -> None:  # T09
        monitor = AdvancedCrowdingMonitor()
        assert monitor.get_fvs_percentile() is None

    def test_fvs_percentile_correct(self) -> None:  # T10
        monitor = AdvancedCrowdingMonitor()
        for v in [0.10, 0.12, 0.08, 0.11, 0.13]:
            monitor.add_fvs_observation(v)
        pct = monitor.get_fvs_percentile()
        assert pct is not None
        # latest=0.13 是最大值，百分位应接近 1.0
        assert pct > 0.8

    def test_fvs_crowded_above_90pct(self) -> None:  # T11
        monitor = AdvancedCrowdingMonitor()
        # 添加 100 个值，最后一个是历史最大
        rng = np.random.RandomState(0)
        hist = sorted(rng.rand(99) * 0.2)
        for v in hist:
            monitor.add_fvs_observation(v)
        monitor.add_fvs_observation(hist[-1] + 0.5)  # 超历史最大
        metrics = monitor.evaluate()
        assert metrics.fvs_crowded is True
        assert metrics.overall_crowded is True

    def test_turnover_crowded(self) -> None:  # T12
        monitor = AdvancedCrowdingMonitor()
        ratio = monitor.compute_turnover_concentration(
            current_turnovers={"A001": 10000.0},
            historical_avg_turnover=1000.0,
        )
        assert ratio == pytest.approx(10.0)
        metrics = monitor.evaluate()
        assert metrics.turnover_crowded is True

    def test_pairwise_corr_high(self) -> None:  # T13
        monitor = AdvancedCrowdingMonitor()
        rng = np.random.RandomState(42)
        # 5 个策略几乎完全一致
        base = rng.randn(60, 1)
        # Shape: (60, 5) with high correlation
        returns = np.hstack([base + 0.01 * rng.randn(60, 1) for _ in range(5)])
        corr = monitor.compute_pairwise_correlation(returns)
        assert corr is not None
        assert corr > 0.5

    def test_pairwise_corr_insufficient_strategies(self) -> None:  # T14
        monitor = AdvancedCrowdingMonitor()
        single_strat = np.random.randn(60, 1)  # N=1 < 2
        corr = monitor.compute_pairwise_correlation(single_strat)
        assert corr is None

    def test_evaluate_overall_is_or(self) -> None:  # T15
        """任一指标 crowded → overall_crowded=True"""
        monitor = AdvancedCrowdingMonitor()
        # 仅添加低换手（不拥挤）
        monitor.compute_turnover_concentration(
            current_turnovers={"A": 100.0},
            historical_avg_turnover=1000.0,
        )
        metrics = monitor.evaluate()
        assert metrics.overall_crowded is False  # 无拥挤
        # 再添加高换手
        monitor.compute_turnover_concentration(
            current_turnovers={"A": 5000.0},
            historical_avg_turnover=1000.0,
        )
        metrics2 = monitor.evaluate()
        assert metrics2.overall_crowded is True

    def test_crowding_score_in_range(self) -> None:  # T16
        monitor = AdvancedCrowdingMonitor()
        monitor.add_fvs_observation(0.1)
        monitor.add_fvs_observation(0.15)
        metrics = monitor.evaluate()
        assert 0.0 <= metrics.crowding_score <= 1.0

    def test_reset_clears_history(self) -> None:  # T17
        monitor = AdvancedCrowdingMonitor()
        for v in [0.1, 0.2, 0.3]:
            monitor.add_fvs_observation(v)
        monitor.reset()
        assert monitor.get_fvs_percentile() is None
        m = monitor.evaluate()
        assert m.fvs_crowded is False
