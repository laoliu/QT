"""Tests for C3: ML 模型切换器 CSI2000/CSI1000 (R89)

验证:
- MLModelSwitcherConfig 默认值
- 四状态机: NORMAL → TRANSITION → BACKUP → RECOVER → NORMAL
- 滚动 SR 计算
- PaperEngine 集成
- plan_a_5comp.yml C3 配置加载
"""

from __future__ import annotations

import math
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aipha.risk.ml_model_switcher import (
    MLModelSwitcher,
    MLModelSwitcherConfig,
    SwitcherState,
    SwitcherStatus,
)


class TestMLModelSwitcherConfig:
    """配置默认值测试。"""

    def test_defaults(self):
        cfg = MLModelSwitcherConfig()
        assert cfg.sr_threshold == 0.5
        assert cfg.consecutive_months == 2
        assert cfg.recovery_sr_threshold == 1.0
        assert cfg.transition_days == 21

    def test_custom_values(self):
        cfg = MLModelSwitcherConfig(sr_threshold=0.3, transition_days=10)
        assert cfg.sr_threshold == 0.3
        assert cfg.transition_days == 10


class TestSwitcherStatus:
    """状态序列化测试。"""

    def test_to_dict(self):
        s = SwitcherStatus(state=SwitcherState.BACKUP, rolling_sr=0.8)
        d = s.to_dict()
        assert d["state"] == "backup"
        assert d["rolling_sr"] == 0.8


class TestMLModelSwitcherInit:
    """初始化测试。"""

    def test_initial_state(self):
        sw = MLModelSwitcher()
        assert sw.status.state == SwitcherState.NORMAL

    def test_initial_weights(self):
        sw = MLModelSwitcher()
        pw, bw = sw.get_model_weights()
        assert pw == 1.0
        assert bw == 0.0


class TestRollingSR:
    """滚动 SR 计算测试。"""

    def test_insufficient_data(self):
        cfg = MLModelSwitcherConfig(rolling_window_days=60, rebalance_interval=1)
        sw = MLModelSwitcher(cfg)
        for _ in range(59):
            sw.update(0.001)
        assert math.isnan(sw.status.rolling_sr)

    def test_positive_sr(self):
        cfg = MLModelSwitcherConfig(rolling_window_days=60, rebalance_interval=1)
        sw = MLModelSwitcher(cfg)
        for _ in range(60):
            sw.update(0.005)
        assert sw.status.rolling_sr > 0


class TestStateMachine:
    """四状态机转换测试。"""

    def test_normal_no_trigger(self):
        """喂正收益，保持 NORMAL。"""
        cfg = MLModelSwitcherConfig(
            rolling_window_days=20, rebalance_interval=21,
            consecutive_months=2, sr_threshold=0.5,
        )
        sw = MLModelSwitcher(cfg)
        for _ in range(100):
            sw.update(0.005)
        assert sw.status.state == SwitcherState.NORMAL
        assert sw.get_model_weights() == (1.0, 0.0)

    def test_normal_to_transition(self):
        """连续 consecutive_months 月 SR < threshold → TRANSITION。"""
        cfg = MLModelSwitcherConfig(
            rolling_window_days=20, rebalance_interval=21,
            consecutive_months=2, sr_threshold=0.5,
            transition_days=200,  # 足够长，防止快速跳到 BACKUP
        )
        sw = MLModelSwitcher(cfg)
        # 喂带噪声的负收益（避免 std=0 导致 SR=0 的边界问题）
        import random
        random.seed(42)
        for _ in range(21 * 3):
            sw.update(-0.01 + random.gauss(0, 0.002))
        assert sw.status.state == SwitcherState.TRANSITION
        assert sw.status.primary_weight == 0.5

    def test_transition_to_backup(self):
        """过渡期 transition_days 后 → BACKUP。"""
        import random
        random.seed(42)
        cfg = MLModelSwitcherConfig(
            rolling_window_days=20, rebalance_interval=21,
            consecutive_months=2, sr_threshold=0.5,
            transition_days=5,
        )
        sw = MLModelSwitcher(cfg)
        for _ in range(21 * 3):
            sw.update(-0.01 + random.gauss(0, 0.002))
        # 可能已过渡完成，检查 TRANSITION 或 BACKUP
        assert sw.status.state in (SwitcherState.TRANSITION, SwitcherState.BACKUP)
        # 再喂几天确保到达 BACKUP
        for _ in range(10):
            sw.update(-0.01 + random.gauss(0, 0.002))
        assert sw.status.state == SwitcherState.BACKUP
        assert sw.get_model_weights() == (0.0, 1.0)

    def test_backup_to_recover(self):
        """BACKUP 下 SR 恢复 → RECOVER。"""
        import random
        random.seed(42)
        cfg = MLModelSwitcherConfig(
            rolling_window_days=20, rebalance_interval=21,
            consecutive_months=2, sr_threshold=0.5,
            transition_days=200, recovery_sr_threshold=0.3,
            recovery_months=2,
        )
        # 手动构建到 BACKUP 状态
        sw = MLModelSwitcher(cfg)
        sw._status.state = SwitcherState.BACKUP
        sw._status.primary_weight = 0.0
        sw._status.backup_weight = 1.0
        # 预填充窗口数据（负收益历史）
        sw._returns = [-0.01 + random.gauss(0, 0.002) for _ in range(20)]
        sw._status.day_counter = 20
        # 喂正收益触发恢复
        for _ in range(21 * 3):
            sw.update(0.02 + random.gauss(0, 0.002))
        assert sw.status.state == SwitcherState.RECOVER

    def test_recover_to_normal(self):
        """RECOVER 过渡完成 → NORMAL。"""
        cfg = MLModelSwitcherConfig(
            rolling_window_days=20, rebalance_interval=21,
            consecutive_months=2, sr_threshold=0.5,
            transition_days=5, recovery_sr_threshold=0.3,
            recovery_months=2,
        )
        # 手动构建到 RECOVER 状态
        sw = MLModelSwitcher(cfg)
        sw._status.state = SwitcherState.RECOVER
        sw._status.primary_weight = 0.5
        sw._status.backup_weight = 0.5
        sw._status.transition_days_elapsed = 0
        sw._returns = [0.02] * 20
        sw._status.day_counter = 20
        # 喂足够天数完成过渡
        for _ in range(10):
            sw.update(0.02)
        assert sw.status.state == SwitcherState.NORMAL
        assert sw.get_model_weights() == (1.0, 0.0)

    def test_reset(self):
        sw = MLModelSwitcher()
        sw._status.state = SwitcherState.BACKUP
        sw._returns = [0.01] * 100
        sw.reset()
        assert sw.status.state == SwitcherState.NORMAL
        assert len(sw._returns) == 0


class TestPaperEngineC3Integration:
    """Paper Engine C3 集成测试。"""

    def test_config_defaults_disabled(self):
        from aipha.runner.paper_engine import PaperEngineConfig
        cfg = PaperEngineConfig()
        assert cfg.ml_model_switcher_enabled is False

    def test_from_yaml_c3(self, tmp_path):
        from aipha.runner.paper_engine import PaperEngineConfig
        yml = tmp_path / "c3.yml"
        yml.write_text(
            "paper_engine:\n"
            "  ml_model_switcher_enabled: true\n"
            "  ml_model_switcher_sr_threshold: 0.4\n"
            "  ml_model_switcher_transition_days: 14\n"
        )
        cfg = PaperEngineConfig.from_yaml(yml)
        assert cfg.ml_model_switcher_enabled is True
        assert cfg.ml_model_switcher_sr_threshold == 0.4
        assert cfg.ml_model_switcher_transition_days == 14

    def test_plan_a_c3_enabled(self):
        from aipha.runner.paper_engine import PaperEngineConfig
        p = Path("configs/paper/plan_a_5comp.yml")
        if not p.exists():
            pytest.skip("plan_a_5comp.yml not found")
        cfg = PaperEngineConfig.from_yaml(p)
        assert cfg.ml_model_switcher_enabled is True
        assert cfg.ml_model_switcher_sr_threshold == 0.5
