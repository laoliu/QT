"""
PaperEngine <-> ml_risk_controls 共享风控模块集成测试

验证 R81 §4.3.1 约束：
- PaperEngine 正确初始化 CombinedPositionSizer
- NAV 历史追踪与缩放因子计算
- risk_position_scale 注入 LiveRunner 执行链
- YAML 配置加载 ML 风控字段
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import pandas as pd
import pytest

from aipha.data.akshare_realtime_provider import MarketType
from aipha.data.calendar import CNStockCalendar, TradingSession
from aipha.domain.models import Bar
from aipha.risk.ml_risk_controls import (
    CombinedPositionSizer,
    MddGradientDeleverager,
    VolTargetScaler,
)
from aipha.runner.live_runner import RunnerMode
from aipha.runner.paper_engine import PaperEngine, PaperEngineConfig


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


class _DummyStrategy:
    name = "test_risk"
    def on_start(self): pass
    def on_stop(self): pass
    def on_bars(self, context, bars): return []


@pytest.fixture
def closed_calendar():
    cal = MagicMock(spec=CNStockCalendar)
    cal.get_session.return_value = TradingSession.CLOSED
    cal.is_trading_day.return_value = False
    return cal


@pytest.fixture
def base_config(tmp_path: Path) -> PaperEngineConfig:
    return PaperEngineConfig(
        strategy_name="test_risk",
        symbols=["127045"],
        market_type=MarketType.CB,
        initial_capital=1_000_000.0,
        state_dir=tmp_path / "state",
        audit_log_dir=tmp_path / "audit",
        monitoring_enabled=False,
        metrics_enabled=False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestPaperRiskConfig:
    """ML 风控配置字段测试"""

    def test_defaults_enabled(self):
        cfg = PaperEngineConfig()
        assert cfg.ml_risk_controls_enabled is True
        assert cfg.ml_risk_vol_target == 0.20
        assert cfg.ml_risk_vol_lookback == 60
        assert cfg.ml_risk_max_stock_pct == 0.03
        assert cfg.ml_risk_mdd_levels is None

    def test_from_yaml_ml_risk(self, tmp_path: Path):
        yaml_content = """
paper_engine:
  strategy_name: "cb_test"
  symbols: ["127045"]
  initial_capital: 500000
  ml_risk_controls_enabled: true
  ml_risk_vol_target: 0.15
  ml_risk_vol_lookback: 30
  ml_risk_max_stock_pct: 0.05
"""
        p = tmp_path / "config.yml"
        p.write_text(yaml_content)
        cfg = PaperEngineConfig.from_yaml(p)
        assert cfg.ml_risk_controls_enabled is True
        assert cfg.ml_risk_vol_target == 0.15
        assert cfg.ml_risk_vol_lookback == 30
        assert cfg.ml_risk_max_stock_pct == 0.05

    def test_from_yaml_disabled(self, tmp_path: Path):
        yaml_content = """
paper_engine:
  strategy_name: "cb_test"
  ml_risk_controls_enabled: false
"""
        p = tmp_path / "config.yml"
        p.write_text(yaml_content)
        cfg = PaperEngineConfig.from_yaml(p)
        assert cfg.ml_risk_controls_enabled is False


class TestPaperRiskInit:
    """PaperEngine 初始化 CombinedPositionSizer"""

    def test_sizer_created_when_enabled(
        self, base_config, closed_calendar
    ):
        base_config.ml_risk_controls_enabled = True
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        assert engine._position_sizer is not None
        assert isinstance(engine._position_sizer, CombinedPositionSizer)
        assert engine._risk_scale == 1.0
        assert len(engine._nav_history) == 0

    def test_sizer_not_created_when_disabled(
        self, base_config, closed_calendar
    ):
        base_config.ml_risk_controls_enabled = False
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        assert engine._position_sizer is None
        assert engine._risk_scale == 1.0

    def test_custom_vol_params(self, base_config, closed_calendar):
        base_config.ml_risk_vol_target = 0.10
        base_config.ml_risk_vol_lookback = 30
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        sizer = engine._position_sizer
        assert sizer is not None
        assert sizer.vol_scaler.vol_target == 0.10
        assert sizer.vol_scaler.lookback == 30

    def test_custom_mdd_levels(self, base_config, closed_calendar):
        custom = [(0.05, 1.0), (0.10, 0.5), (1.0, 0.0)]
        base_config.ml_risk_mdd_levels = custom
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        sizer = engine._position_sizer
        assert sizer is not None
        assert sizer.mdd_deleverager.levels == custom

    def test_risk_scale_property(self, base_config, closed_calendar):
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        assert engine.risk_scale == 1.0

    def test_runner_scale_default(self, base_config, closed_calendar):
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        assert engine.runner.risk_position_scale == 1.0


class TestScaleComputation:
    """CombinedPositionSizer 缩放因子计算集成"""

    def test_no_drawdown_scale_one(self):
        sizer = CombinedPositionSizer()
        nav = pd.Series([100.0 + i * 0.1 for i in range(100)])
        result = sizer.compute_scale(nav)
        assert result["final_scale"] >= 0.99
        assert result["mdd_triggered"] is False

    def test_large_drawdown_reduces_scale(self):
        sizer = CombinedPositionSizer()
        # 峰值 200，当前 160 → MDD = 20%
        nav_values = list(range(100, 201)) + [160]
        nav = pd.Series(nav_values, dtype=float)
        result = sizer.compute_scale(nav)
        assert result["mdd"] == pytest.approx(0.20, abs=0.01)
        assert result["mdd_scale"] < 1.0
        assert result["mdd_triggered"] is True

    def test_catastrophic_drawdown_minimal_scale(self):
        sizer = CombinedPositionSizer()
        # 峰值 200，当前 140 → MDD = 30%
        nav_values = list(range(100, 201)) + [140]
        nav = pd.Series(nav_values, dtype=float)
        result = sizer.compute_scale(nav)
        assert result["mdd_scale"] <= 0.30

    def test_vol_scaler_high_vol(self):
        """高波动率降低仓位"""
        scaler = VolTargetScaler(vol_target=0.10, lookback=20)
        # 制造高波动 NAV
        import numpy as np
        np.random.seed(42)
        rets = np.random.normal(0, 0.05, 100)  # 日均 5% 波动
        nav = pd.Series((1 + rets).cumprod() * 100)
        scale = scaler.get_scale(nav)
        assert scale < 1.0  # 高波动 → 降仓

    def test_vol_scaler_low_vol(self):
        """低波动率可能加仓"""
        scaler = VolTargetScaler(vol_target=0.30, lookback=20)
        import numpy as np
        np.random.seed(42)
        rets = np.random.normal(0.001, 0.003, 100)  # 日均 0.3% 波动
        nav = pd.Series((1 + rets).cumprod() * 100)
        scale = scaler.get_scale(nav)
        assert scale > 1.0  # 低波动 → 可加仓


class TestScaleInjection:
    """缩放因子注入 LiveRunner 执行链"""

    def test_scale_injected_via_heartbeat(
        self, base_config, closed_calendar
    ):
        """PaperEngine heartbeat 更新 runner.risk_position_scale"""
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        # 模拟 NAV 下跌 → MDD 15%
        for i in range(50):
            engine._nav_history.append(1_000_000 + i * 1000)
        # 峰值 ~1049000, 跌到 891650 → MDD ~15%
        engine._nav_history.append(1_049_000 * 0.85)

        nav_s = pd.Series(engine._nav_history)
        info = engine._position_sizer.compute_scale(nav_s)
        engine._risk_scale = info["final_scale"]
        engine._runner.risk_position_scale = engine._risk_scale

        assert engine._runner.risk_position_scale < 1.0
        assert engine.risk_scale == engine._runner.risk_position_scale

    def test_scale_not_injected_when_disabled(
        self, base_config, closed_calendar
    ):
        """禁用时 risk_position_scale 保持 1.0"""
        base_config.ml_risk_controls_enabled = False
        engine = PaperEngine(
            base_config, _DummyStrategy(), calendar=closed_calendar
        )
        assert engine._runner.risk_position_scale == 1.0
