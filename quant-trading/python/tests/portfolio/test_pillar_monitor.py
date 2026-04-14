"""支柱降权监控器测试

覆盖：
- 配置与默认值
- 无降权（正常表现）
- 降权触发（连续 N 月 SR < 阈值）
- 降权恢复（SR 回升后恢复）
- 重置
"""

from __future__ import annotations

import numpy as np
import pytest

from aipha.portfolio.pillar_monitor import (
    DownweightRule,
    PillarDownweightMonitor,
    PillarMonitorState,
)


class TestDownweightRule:
    """配置默认值。"""

    def test_defaults(self):
        r = DownweightRule()
        assert r.sr_threshold == 0.0
        assert r.consecutive_months == 6
        assert r.weight_reduction == 0.30
        assert r.recovery_months == 3

    def test_custom(self):
        r = DownweightRule(
            sr_threshold=-0.5,
            consecutive_months=3,
            weight_reduction=0.50,
        )
        assert r.sr_threshold == -0.5
        assert r.weight_reduction == 0.50


class TestPillarMonitorState:
    def test_to_dict(self):
        s = PillarMonitorState(
            current_scale=0.7, rolling_sr=-0.3,
        )
        d = s.to_dict()
        assert d["current_scale"] == 0.7
        assert d["is_downweighted"] is False


class TestPillarDownweightMonitor:
    """核心监控逻辑。"""

    def test_initial_scale(self):
        m = PillarDownweightMonitor()
        assert m.get_scale() == 1.0

    def test_positive_returns_no_downweight(self):
        """正收益 → 不降权。"""
        m = PillarDownweightMonitor(
            rule=DownweightRule(consecutive_months=2),
            rebalance_interval=5,
        )
        for _ in range(200):
            m.update(0.001)
        assert m.get_scale() == 1.0
        assert not m.state.is_downweighted

    def test_negative_returns_trigger(self):
        """持续负收益 → 触发降权。"""
        rule = DownweightRule(
            sr_threshold=0.0,
            consecutive_months=3,
            weight_reduction=0.30,
            rolling_window_days=60,
        )
        m = PillarDownweightMonitor(
            rule=rule, rebalance_interval=21,
        )
        rng = np.random.RandomState(42)
        # Feed 200 days of negative returns with noise
        for _ in range(200):
            m.update(rng.normal(-0.005, 0.008))
        assert m.state.is_downweighted
        assert abs(m.get_scale() - 0.70) < 1e-6

    def test_recovery(self):
        """降权后正收益恢复。"""
        rule = DownweightRule(
            sr_threshold=0.0,
            consecutive_months=3,
            recovery_months=2,
            weight_reduction=0.30,
            rolling_window_days=60,
        )
        m = PillarDownweightMonitor(
            rule=rule, rebalance_interval=21,
        )
        rng = np.random.RandomState(42)
        # Phase 1: Trigger downweight
        for _ in range(200):
            m.update(rng.normal(-0.005, 0.008))
        assert m.state.is_downweighted

        # Phase 2: Positive returns to recover
        for _ in range(200):
            m.update(rng.normal(0.005, 0.008))
        assert not m.state.is_downweighted
        assert m.get_scale() == 1.0

    def test_reset(self):
        m = PillarDownweightMonitor()
        for _ in range(100):
            m.update(-0.003)
        m.reset()
        assert m.get_scale() == 1.0
        assert m.state.months_below_threshold == 0
