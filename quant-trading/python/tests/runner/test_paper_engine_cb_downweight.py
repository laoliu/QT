"""Tests for C2: CB Pillar 动态降权 (R89)

验证:
- PaperEngineConfig 中 C2 参数默认值与 YAML 解析
- PillarDownweightMonitor 在 PaperEngine 中正确初始化
- _apply_cb_downweight 权重重分配正确
- _apply_cb_recovery 恢复逻辑正确
- plan_a_5comp.yml 中 C2 配置加载
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from aipha.data.akshare_realtime_provider import MarketType
from aipha.data.calendar import CNStockCalendar, TradingSession
from aipha.runner.paper_engine import PaperEngine, PaperEngineConfig


class DummyStrategy:
    """用于测试的极简策略。"""
    name = "test_strategy"

    def on_start(self):
        pass

    def on_stop(self):
        pass

    def on_bars(self, context, bars):
        return []


@pytest.fixture
def config_c2(tmp_path: Path) -> PaperEngineConfig:
    """带 C2 配置的基础 config。"""
    return PaperEngineConfig(
        strategy_name="test_strategy",
        symbols=["127045"],
        market_type=MarketType.CB,
        initial_capital=100_000.0,
        state_dir=tmp_path / "state",
        audit_log_dir=tmp_path / "audit",
        portfolio_weights={"cb": 0.30, "ml": 0.30,
                           "etf": 0.15, "bond": 0.10, "gold": 0.15},
        cb_pillar_downweight_enabled=True,
        cb_pillar_downweight_sr_threshold=0.5,
        cb_pillar_downweight_consecutive_months=4,
        cb_pillar_downweight_reduction=0.333,
        cb_pillar_downweight_recovery_months=3,
    )


@pytest.fixture
def always_closed_calendar():
    cal = MagicMock(spec=CNStockCalendar)
    cal.get_session.return_value = TradingSession.CLOSED
    cal.is_trading_day.return_value = False
    return cal


class TestCBPillarDownweightConfig:
    """C2 配置参数测试。"""

    def test_defaults_disabled(self):
        cfg = PaperEngineConfig()
        assert cfg.cb_pillar_downweight_enabled is False
        assert cfg.cb_pillar_downweight_sr_threshold == 0.5

    def test_c2_params_set(self):
        cfg = PaperEngineConfig(
            cb_pillar_downweight_enabled=True,
            cb_pillar_downweight_sr_threshold=0.3,
            cb_pillar_downweight_consecutive_months=6,
            cb_pillar_downweight_reduction=0.5,
        )
        assert cfg.cb_pillar_downweight_enabled is True
        assert cfg.cb_pillar_downweight_sr_threshold == 0.3
        assert cfg.cb_pillar_downweight_consecutive_months == 6
        assert cfg.cb_pillar_downweight_reduction == 0.5

    def test_from_yaml_c2(self, tmp_path):
        yml = tmp_path / "c2.yml"
        yml.write_text(
            "paper_engine:\n"
            "  strategy_name: test\n"
            "  symbols: ['127045']\n"
            "  cb_pillar_downweight_enabled: true\n"
            "  cb_pillar_downweight_sr_threshold: 0.4\n"
            "  cb_pillar_downweight_consecutive_months: 5\n"
            "  cb_pillar_downweight_reduction: 0.25\n"
            "  cb_pillar_downweight_recovery_months: 4\n"
            "  cb_pillar_downweight_redistribute:\n"
            "    - bond\n"
            "    - gold\n",
            encoding="utf-8",
        )
        cfg = PaperEngineConfig.from_yaml(yml)
        assert cfg.cb_pillar_downweight_enabled is True
        assert cfg.cb_pillar_downweight_sr_threshold == 0.4
        assert cfg.cb_pillar_downweight_consecutive_months == 5
        assert cfg.cb_pillar_downweight_reduction == 0.25
        assert cfg.cb_pillar_downweight_recovery_months == 4
        assert cfg.cb_pillar_downweight_redistribute == ["bond", "gold"]

    def test_redistribute_default(self):
        cfg = PaperEngineConfig()
        assert cfg.cb_pillar_downweight_redistribute == ["bond", "gold"]


class TestCBPillarDownweightInit:
    """PillarDownweightMonitor 在 PaperEngine 中初始化测试。"""

    def _make_engine(self, cfg, cal):
        return PaperEngine(cfg, DummyStrategy(), calendar=cal)

    def test_monitor_created(self, config_c2, always_closed_calendar):
        engine = self._make_engine(config_c2, always_closed_calendar)
        assert engine._cb_pillar_monitor is not None

    def test_monitor_not_created_when_disabled(self, tmp_path,
                                                always_closed_calendar):
        cfg = PaperEngineConfig(
            strategy_name="test_strategy",
            symbols=["127045"],
            state_dir=tmp_path / "state",
            audit_log_dir=tmp_path / "audit",
            cb_pillar_downweight_enabled=False,
        )
        engine = self._make_engine(cfg, always_closed_calendar)
        assert engine._cb_pillar_monitor is None

    def test_cb_nav_history_initialized(self, config_c2,
                                         always_closed_calendar):
        engine = self._make_engine(config_c2, always_closed_calendar)
        assert len(engine._cb_nav_history) == 1
        assert engine._cb_nav_history[0] == 1.0


class TestApplyCBDownweight:
    """_apply_cb_downweight 权重重分配测试。"""

    def _make_engine(self, cfg, cal):
        return PaperEngine(cfg, DummyStrategy(), calendar=cal)

    def test_cb_weight_reduced(self, config_c2, always_closed_calendar):
        engine = self._make_engine(config_c2, always_closed_calendar)
        # scale=0.667 → CB 30% * 0.667 = 20%
        engine._apply_cb_downweight(0.667)
        assert abs(engine._config.portfolio_weights["cb"] - 0.2001) < 0.01

    def test_excess_redistributed_to_bond_gold(self, config_c2,
                                                always_closed_calendar):
        engine = self._make_engine(config_c2, always_closed_calendar)
        engine._apply_cb_downweight(0.667)
        # ~10% excess split equally: bond +5%, gold +5%
        assert engine._config.portfolio_weights["bond"] > 0.10
        assert engine._config.portfolio_weights["gold"] > 0.15

    def test_total_weight_preserved(self, config_c2,
                                     always_closed_calendar):
        engine = self._make_engine(config_c2, always_closed_calendar)
        engine._apply_cb_downweight(0.667)
        total = sum(engine._config.portfolio_weights.values())
        assert abs(total - 1.0) < 0.01


class TestApplyCBRecovery:
    """_apply_cb_recovery 恢复逻辑测试。"""

    def _make_engine(self, cfg, cal):
        return PaperEngine(cfg, DummyStrategy(), calendar=cal)

    def test_cb_weight_restored(self, config_c2, always_closed_calendar):
        engine = self._make_engine(config_c2, always_closed_calendar)
        # 先降权
        engine._apply_cb_downweight(0.667)
        assert engine._config.portfolio_weights["cb"] < 0.30
        # 再恢复
        engine._apply_cb_recovery()
        assert abs(engine._config.portfolio_weights["cb"] - 0.30) < 0.01

    def test_targets_reduced_on_recovery(self, config_c2,
                                          always_closed_calendar):
        engine = self._make_engine(config_c2, always_closed_calendar)
        # 先降权 → bond/gold 升高
        engine._apply_cb_downweight(0.667)
        bond_up = engine._config.portfolio_weights["bond"]
        gold_up = engine._config.portfolio_weights["gold"]
        assert bond_up > 0.10
        assert gold_up > 0.15
        # 恢复后 → bond/gold 回到原始
        engine._apply_cb_recovery()
        assert abs(engine._config.portfolio_weights["bond"] - 0.10) < 0.01
        assert abs(engine._config.portfolio_weights["gold"] - 0.15) < 0.01


class TestPlanA5CompC2Config:
    """plan_a_5comp.yml 中 C2 配置 E2E 测试。"""

    def test_c2_enabled_in_plan_a(self):
        yml = Path("configs/paper/plan_a_5comp.yml")
        if not yml.exists():
            pytest.skip("plan_a_5comp.yml not found")
        cfg = PaperEngineConfig.from_yaml(yml)
        assert cfg.cb_pillar_downweight_enabled is True

    def test_c2_params_match_expert_consensus(self):
        """R89 专家共识参数: SR<0.5, 4月, 33.3%降权, 3月恢复。"""
        yml = Path("configs/paper/plan_a_5comp.yml")
        if not yml.exists():
            pytest.skip("plan_a_5comp.yml not found")
        cfg = PaperEngineConfig.from_yaml(yml)
        assert cfg.cb_pillar_downweight_sr_threshold == 0.5
        assert cfg.cb_pillar_downweight_consecutive_months == 4
        assert abs(cfg.cb_pillar_downweight_reduction - 0.333) < 0.01
        assert cfg.cb_pillar_downweight_recovery_months == 3
        assert "bond" in cfg.cb_pillar_downweight_redistribute
        assert "gold" in cfg.cb_pillar_downweight_redistribute
